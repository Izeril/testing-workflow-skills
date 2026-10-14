"""Small standard-library MCP HTTP client used by the GitNexus provider.

The auditor intentionally owns the MCP wire protocol here.  Callers only see
``list_tools``, ``call_tool`` and ``read_resource`` and cannot accidentally
invoke a mutating tool.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


AUDIT_TOOL_ALLOWLIST = frozenset(
    {
        "list_repos",
        "query",
        "context",
        "impact",
        "detect_changes",
        "check",
        "cypher",
        "explain",
        "pdg_query",
        "route_map",
        "tool_map",
        "shape_check",
        "api_impact",
        "group_list",
        "trace",
    }
)


class MCPClientError(RuntimeError):
    """A transport or JSON-RPC error with enough context for an audit report."""

    def __init__(self, code: str, message: str, *, context: Optional[Dict[str, Any]] = None, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.context = context or {}
        self.retryable = retryable


@dataclass
class _HTTPResponse:
    status: int
    headers: Mapping[str, str]
    body: bytes


def _header(headers: Mapping[str, Any], name: str) -> str:
    wanted = name.casefold()
    for key, value in headers.items():
        if str(key).casefold() == wanted:
            return str(value)
    return ""


def _parse_sse(body: str) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    data_lines: List[str] = []

    def flush() -> None:
        if not data_lines:
            return
        data = "\n".join(data_lines).strip()
        data_lines.clear()
        if not data or data == "[DONE]":
            return
        try:
            value = json.loads(data)
        except json.JSONDecodeError as exc:
            raise MCPClientError("mcp_invalid_sse", "MCP SSE data 不是合法 JSON", context={"data": data[:400]}) from exc
        if isinstance(value, dict):
            messages.append(value)

    for line in body.splitlines():
        if not line.strip():
            flush()
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    flush()
    return messages


def parse_mcp_body(body: bytes | str, content_type: str = "") -> List[Dict[str, Any]]:
    """Parse JSON or text/event-stream responses, including multi-event SSE."""
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
    if not text.strip():
        return []
    if "text/event-stream" in content_type.casefold() or text.lstrip().startswith(("event:", "data:")):
        return _parse_sse(text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MCPClientError("mcp_non_json_response", "MCP 响应不是合法 JSON 或 SSE", context={"body": text[:400]}) from exc
    return [value] if isinstance(value, dict) else []


class MCPHttpClient:
    """JSON-RPC MCP client with session recovery and an audit-only tool gate."""

    def __init__(
        self,
        url: str,
        *,
        protocol_version: str = "2025-03-26",
        connect_timeout_seconds: float = 10,
        request_timeout_seconds: float = 60,
        token: str = "",
        max_retries: int = 1,
        opener: Optional[Callable[..., Any]] = None,
    ):
        self.url = str(url or "").strip()
        self.protocol_version = protocol_version
        self.connect_timeout = max(0.1, float(connect_timeout_seconds or 10))
        self.request_timeout = max(self.connect_timeout, float(request_timeout_seconds or 60))
        self.token = token
        self.max_retries = max(0, int(max_retries or 0))
        self._opener = opener or urlopen
        self._request_id = 0
        self.session_id = ""
        self.initialized = False
        self.server_info: Dict[str, Any] = {}
        self.tools: Dict[str, Dict[str, Any]] = {}

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _http(self, payload: Dict[str, Any], *, timeout: float) -> _HTTPResponse:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = Request(self.url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers, method="POST")
        try:
            with self._opener(request, timeout=timeout) as response:
                response_headers = {str(k): str(v) for k, v in response.headers.items()}
                return _HTTPResponse(int(getattr(response, "status", 200)), response_headers, response.read())
        except HTTPError as exc:
            raw = exc.read() if hasattr(exc, "read") else b""
            retryable = exc.code in {401, 404, 408, 409, 429, 500, 502, 503, 504}
            raise MCPClientError(
                "mcp_http_error",
                f"MCP HTTP 请求失败：{exc.code}",
                context={"status": exc.code, "body": raw.decode("utf-8", errors="replace")[:400]},
                retryable=retryable,
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise MCPClientError("mcp_transport_error", f"MCP 连接失败：{exc}", context={"url": self.url}, retryable=True) from exc

    @staticmethod
    def _message(messages: Iterable[Dict[str, Any]], request_id: Any = None) -> Dict[str, Any]:
        values = list(messages)
        if request_id is not None:
            values = [item for item in values if item.get("id") == request_id] or values
        for message in reversed(values):
            if "error" in message:
                error = message.get("error") or {}
                raise MCPClientError(
                    "mcp_jsonrpc_error",
                    str(error.get("message") or "MCP JSON-RPC 调用失败"),
                    context={"code": error.get("code"), "data": error.get("data")},
                    retryable=error.get("code") in {-32001, -32002, -32003},
                )
            if "result" in message:
                return message
        if not values:
            raise MCPClientError("mcp_empty_response", "MCP 返回空响应", retryable=True)
        raise MCPClientError("mcp_invalid_response", "MCP 响应缺少 result/error", context={"response": values[-1]})

    def _request(self, method: str, params: Optional[Dict[str, Any]] = None, *, notification: bool = False) -> Any:
        request_id = None if notification else self._next_id()
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if request_id is not None:
            payload["id"] = request_id
        if params is not None:
            payload["params"] = params
        response = self._http(payload, timeout=self.connect_timeout if method == "initialize" else self.request_timeout)
        session_id = _header(response.headers, "mcp-session-id")
        if session_id:
            self.session_id = session_id
        if notification:
            return None
        messages = parse_mcp_body(response.body, _header(response.headers, "content-type"))
        message = self._message(messages, request_id)
        result = message.get("result")
        return result

    def initialize(self, *, force: bool = False) -> Dict[str, Any]:
        if self.initialized and not force:
            return self.server_info
        self.session_id = "" if force else self.session_id
        result = self._request(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "requirement-code-auditor", "version": "1.0.0"},
            },
        )
        if not isinstance(result, dict):
            raise MCPClientError("mcp_initialize_invalid", "MCP initialize 返回结构无效", context={"result": result})
        self.server_info = dict(result.get("serverInfo") or result.get("server_info") or {})
        try:
            self._request("notifications/initialized", notification=True)
        except MCPClientError:
            # Some compatible servers answer 202/no-content to notifications.
            pass
        self.initialized = True
        return self.server_info

    def list_tools(self, *, force: bool = False) -> Dict[str, Dict[str, Any]]:
        self.initialize(force=force)
        result = self._request("tools/list")
        raw_tools = result.get("tools", []) if isinstance(result, dict) else result
        if not isinstance(raw_tools, list):
            raise MCPClientError("mcp_tools_invalid", "tools/list 返回结构无效", context={"result": result})
        self.tools = {
            str(item.get("name")): dict(item)
            for item in raw_tools
            if isinstance(item, dict) and item.get("name")
        }
        return self.tools

    def _recover_session(self) -> None:
        self.initialized = False
        self.tools = {}
        self.initialize(force=True)
        self.list_tools()

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        if name not in AUDIT_TOOL_ALLOWLIST:
            raise MCPClientError("mcp_tool_forbidden", f"审计器禁止调用 MCP 工具：{name}", context={"tool": name})
        if not self.initialized:
            self.list_tools()
        if self.tools and name not in self.tools:
            raise MCPClientError("mcp_tool_missing", f"MCP 未提供必需工具：{name}", context={"tool": name})
        last_error: Optional[MCPClientError] = None
        for attempt in range(self.max_retries + 1):
            try:
                return self._request("tools/call", {"name": name, "arguments": arguments or {}})
            except MCPClientError as exc:
                last_error = exc
                if attempt >= self.max_retries or not exc.retryable:
                    raise
                time.sleep(0.05 * (attempt + 1))
                self._recover_session()
        raise last_error or MCPClientError("mcp_call_failed", "MCP 工具调用失败")

    def read_resource(self, uri: str) -> Any:
        if not self.initialized:
            self.initialize()
        return self._request("resources/read", {"uri": uri})
