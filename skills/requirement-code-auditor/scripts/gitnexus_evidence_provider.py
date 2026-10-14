"""GitNexus-backed code evidence provider.

The provider is deliberately conservative: graph relationships help locate
code, but a comparable code item is emitted only when a file, symbol and
source/behaviour value are available.  A missing remote result therefore
becomes ``partial``/``unknown`` evidence rather than a proven code defect.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .gitnexus_query_planner import build_query_plans
from .mcp_http_client import MCPClientError, MCPHttpClient
from .evidence_provider import CodeEvidenceProvider
from .skill_errors import AuditorError, blocked


DIMENSION_FIELDS = {
    "apis": "api_endpoints",
    "rules": "business_rules",
    "tables": "sql_analysis",
    "jobs": "scheduled_tasks",
    "configs": "config_values",
    "side_effects": "processing_rules",
    "sorting": "processing_rules",
    "pagination": "processing_rules",
    "errors": "analysis_warnings",
}
SQL_FACT_TYPES = {"tables", "sorting", "pagination", "side_effects", "calculations"}
SQL_TOKENS = ("表", "字段", "查询", "筛选", "排序", "分页", "聚合", "关联", "新增", "更新", "删除", "落库", "存储过程", "Mapper", "MyBatis")


def _unwrap(value: Any) -> Any:
    """Unwrap common MCP structuredContent/content envelopes."""
    if isinstance(value, dict):
        for key in ("structuredContent", "structured_content", "data", "payload"):
            if key in value and value[key] is not value:
                unwrapped = _unwrap(value[key])
                if unwrapped is not None:
                    return unwrapped
        content = value.get("content")
        if isinstance(content, list):
            texts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
            if texts:
                text = "\n".join(str(item) for item in texts).strip()
                try:
                    return _unwrap(json.loads(text))
                except json.JSONDecodeError:
                    return {"text": text}
    return value


def _as_list(value: Any, *keys: str) -> List[Any]:
    value = _unwrap(value)
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, list):
                return candidate
        if value:
            return [value]
    return []


def _walk_records(value: Any) -> Iterable[Dict[str, Any]]:
    value = _unwrap(value)
    if isinstance(value, dict):
        signal_keys = {"uid", "id", "name", "symbol", "file", "file_path", "path", "content", "source", "kind"}
        if signal_keys.intersection(value):
            yield value
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from _walk_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_records(child)


def _text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    return str(value).strip()


def _first(record: Dict[str, Any], *names: str) -> str:
    for name in names:
        value = record.get(name)
        if value not in (None, ""):
            return _text(value)
    return ""


def _source_content(record: Dict[str, Any]) -> str:
    value = record.get("content") or record.get("source_content") or record.get("source_code") or record.get("code") or record.get("snippet")
    if isinstance(value, dict):
        value = value.get("content") or value.get("text") or value.get("source")
    return _text(value)


def _contains_expected(expected: Any, content: str) -> bool:
    if expected in (None, "") or not content:
        return False
    values = expected.values() if isinstance(expected, dict) else [expected]
    normalized = content.casefold()
    meaningful = [str(item).strip().casefold() for item in values if str(item).strip()]
    return bool(meaningful) and all(item in normalized for item in meaningful)


def _is_sql_content(content: str) -> bool:
    return bool(re.search(r"(?is)(<\s*(select|insert|update|delete)\b|\b(select|insert|update|delete)\b.{0,120}\b(from|into|set)\b|\b(from|join|where|order\s+by|group\s+by)\b)", content or ""))


IDENTIFIER_FIELDS = ("repo_key", "key", "name", "repo", "id", "repository", "slug", "project")
SERVICE_FIELDS = ("service", "services", "service_name", "service_names", "aliases", "alias", "applications", "application", "app_names", "modules")


def _norm_selector(value: Any) -> str:
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(value or "").casefold())


def _collect_strings(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        result: List[str] = []
        for item in value:
            result.extend(_collect_strings(item))
        return result
    if isinstance(value, dict):
        result: List[str] = []
        for item in value.values():
            result.extend(_collect_strings(item))
        return result
    return []


def _field_values(record: Dict[str, Any], fields: Sequence[str]) -> List[str]:
    result: List[str] = []
    for field in fields:
        if field in record:
            result.extend(_collect_strings(record.get(field)))
    return [value for value in result if value.strip()]


def _repo_identifiers(record: Dict[str, Any]) -> List[str]:
    return _field_values(record, IDENTIFIER_FIELDS)


def _repo_service_aliases(record: Dict[str, Any]) -> List[str]:
    result = _field_values(record, SERVICE_FIELDS)
    metadata = record.get("metadata")
    if isinstance(metadata, dict):
        result.extend(_field_values(metadata, SERVICE_FIELDS))
    return result

def _repo_key(record: Dict[str, Any]) -> str:
    return _first(record, "repo_key", "key", "name", "repo", "id", "repository")


def _scope_from_payload(payload: Any, branch: str) -> str:
    payload = _unwrap(payload)
    values: List[Any] = []
    if isinstance(payload, dict):
        values.extend(payload.get(key) for key in ("branch_scope", "branch_status", "scope", "index_scope") if payload.get(key) is not None)
        values.extend(payload.get(key) for key in ("branch", "ref", "branches") if payload.get(key) is not None)
    for record in _walk_records(payload):
        values.extend(record.get(key) for key in ("branch_scope", "branch_status", "scope", "index_scope") if record.get(key) is not None)
        values.extend(record.get(key) for key in ("branch", "ref", "branches") if record.get(key) is not None)
    for value in values:
        if isinstance(value, list) and branch in [str(item) for item in value]:
            return "branch_scoped"
        text = str(value).casefold()
        if branch.casefold() in text or "branch_scoped" in text or "branch-scoped" in text:
            return "branch_scoped"
        if "workspace" in text or "default" in text:
            return "workspace_index"
        if "unavailable" in text or "not found" in text or "unsupported" in text:
            return "branch_unavailable"
    if isinstance(payload, dict):
        if payload.get("branch") == branch:
            return "branch_scoped"
        if payload.get("workspace") or payload.get("workspace_index"):
            return "workspace_index"
    return "unknown"


def _normalise_index(repo: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "repo_key": _repo_key(repo),
        "index_commit": _first(repo, "index_commit", "commit", "commit_sha", "sha", "head"),
        "index_time": _first(repo, "index_time", "indexed_at", "updated_at", "last_indexed"),
        "file_count": repo.get("file_count", repo.get("files", repo.get("fileCount"))),
        "symbol_count": repo.get("symbol_count", repo.get("symbols", repo.get("symbolCount"))),
        "process_count": repo.get("process_count", repo.get("processes", repo.get("processCount"))),
    }


class GitNexusEvidenceProvider(CodeEvidenceProvider):
    name = "gitnexus"

    def __init__(self, config: Optional[Dict[str, Any]] = None, *, client: Optional[MCPHttpClient] = None):
        self.config = dict(config or {})
        url = str(self.config.get("url") or "").strip()
        if client is None and not url:
            raise AuditorError(
                "gitnexus_url_missing", "未配置 GitNexus MCP 地址", 2,
                next_action="在 config.local.yaml 中设置 gitnexus.url，或设置 AUDITOR_GITNEXUS_URL",
            )
        self.client = client if client is not None else MCPHttpClient(
            url,
            protocol_version=self.config.get("protocol_version", "2025-03-26"),
            connect_timeout_seconds=self.config.get("connect_timeout_seconds", 10),
            request_timeout_seconds=self.config.get("request_timeout_seconds", 60),
            token=self.config.get("token") or "",
            max_retries=self.config.get("max_retries", 1),
        )
        self._prepared = False
        self._tools: Dict[str, Dict[str, Any]] = {}

    def _prepare(self) -> Dict[str, Any]:
        if self._prepared:
            return {"tools": self._tools, "server_info": self.client.server_info}
        try:
            self._tools = self.client.list_tools()
        except MCPClientError as exc:
            raise AuditorError("gitnexus_unavailable", f"GitNexus MCP 不可用：{exc}", 3, {"mcp_error": exc.code, **exc.context}, True, "确认 GitNexus 地址、网络和服务状态") from exc
        missing = [name for name in ("list_repos", "query", "context") if name not in self._tools]
        if missing:
            raise blocked("gitnexus_required_tool_missing", "GitNexus 缺少审计必需工具", missing_tools=missing)
        server_name = _first(self.client.server_info, "name", "server", "product")
        if server_name and "gitnexus" not in server_name.casefold():
            raise blocked("gitnexus_server_mismatch", "MCP 服务不是 GitNexus", server_name=server_name)
        self._prepared = True
        return {"tools": self._tools, "server_info": self.client.server_info}

    def _call(self, tool: str, arguments: Dict[str, Any], warnings: List[str], *, required: bool = False) -> Any:
        if tool not in self._tools:
            if required:
                raise blocked("gitnexus_required_tool_missing", f"GitNexus 缺少工具：{tool}", missing_tools=[tool])
            warnings.append(f"GitNexus 未提供可选工具 {tool}，相关影响分析已降级")
            return None
        try:
            return self.client.call_tool(tool, arguments)
        except MCPClientError as exc:
            if required:
                raise AuditorError("gitnexus_tool_failed", f"GitNexus 工具 {tool} 调用失败：{exc}", 3, {"tool": tool, "mcp_error": exc.code, **exc.context}, True, "检查 GitNexus 索引和 MCP 服务日志") from exc
            warnings.append(f"GitNexus 工具 {tool} 调用失败，相关证据已降级：{exc}")
            return None

    def _find_repo(self, repo_key: str, target: Dict[str, Any], warnings: List[str]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Discover a repository by exact release-document selectors."""
        payload = self._call("list_repos", {}, warnings, required=True)
        records = [record for record in _as_list(payload, "repos", "repositories", "items") if isinstance(record, dict)]
        explicit = [repo_key, *(_as_list(target.get("repo_candidates")))]
        explicit_norms = {_norm_selector(item) for item in explicit if _norm_selector(item)}
        services = {_norm_selector(item) for item in _as_list(target.get("services")) if _norm_selector(item)}
        matches: List[Tuple[Dict[str, Any], str]] = []
        for record in records:
            identifiers = {_norm_selector(item) for item in _repo_identifiers(record) if _norm_selector(item)}
            aliases = {_norm_selector(item) for item in [*_repo_identifiers(record), *_repo_service_aliases(record)] if _norm_selector(item)}
            if explicit_norms.intersection(identifiers):
                matches.append((record, "explicit_repo_key"))
            elif services.intersection(aliases):
                matches.append((record, "service_alias"))
        if explicit_norms:
            matches = [item for item in matches if item[1] == "explicit_repo_key"]
        if not matches:
            raise blocked(
                "gitnexus_repo_auto_discovery_failed",
                "GitNexus 无法根据需求文档中的仓库或服务唯一发现目标仓库",
                repo_key=repo_key,
                services=target.get("services", []),
                repo_candidates=target.get("repo_candidates", []),
                available_repo_count=len(records),
            )
        if len(matches) != 1:
            raise blocked(
                "gitnexus_repo_auto_discovery_ambiguous",
                "GitNexus 根据需求服务匹配到多个仓库，已阻断模糊猜测",
                repo_key=repo_key,
                services=target.get("services", []),
                candidates=[_repo_key(item[0]) for item in matches],
            )
        record, matched_by = matches[0]
        resolved = _normalise_index(record)
        resolved["matched_by"] = matched_by
        return resolved, {"matched_by": matched_by, "candidate_count": len(matches)}

    @staticmethod
    def _fact_list(requirement_anchors: Optional[Sequence[Any]]) -> List[Dict[str, Any]]:
        facts = []
        for item in requirement_anchors or []:
            if isinstance(item, dict):
                facts.append(item)
            elif item:
                facts.append({"stable_key": str(item), "type": "rules", "behavior": str(item), "comparable": True})
        return facts

    def _symbol(self, record: Dict[str, Any], query: str, repo_key: str, branch: str) -> Optional[Dict[str, Any]]:
        file_path = _first(record, "file_path", "file", "path", "source_file")
        symbol = _first(record, "symbol", "name", "qualified_name", "display_name")
        content = _source_content(record)
        if not file_path or not symbol:
            return None
        return {
            "uid": _first(record, "uid", "id", "symbol_uid") or f"{file_path}:{symbol}",
            "symbol": symbol,
            "kind": _first(record, "kind", "type", "symbol_kind"),
            "file": file_path,
            "line": record.get("line", record.get("line_number", record.get("start_line"))),
            "module": _first(record, "module", "package", "namespace"),
            "process": _first(record, "process", "process_name", "flow"),
            "relevance": record.get("relevance", record.get("score")),
            "content": content,
            "record": record,
            "query": query,
            "repo": repo_key,
            "branch": branch,
            "source": "gitnexus.query",
        }

    @staticmethod
    def _dimension_value(fact: Dict[str, Any], symbol: Dict[str, Any]) -> Tuple[bool, Any]:
        record = symbol["record"]
        explicit = next((record.get(key) for key in ("comparison_value", "implementation_value", "value", "behavior", "definition") if record.get(key) not in (None, "")), None)
        expected = fact.get("value")
        dimension = fact.get("type")
        if dimension in {"apis", "tables", "jobs"}:
            if dimension == "apis":
                method = _first(record, "method", "http_method", "verb")
                path = _first(record, "path", "route", "endpoint", "url")
                if method and path:
                    return True, {"method": method.upper(), "path": path}
            if dimension == "tables":
                table = _first(record, "table", "table_name", "relation")
                if table:
                    return True, table
            if dimension == "jobs":
                name = _first(record, "job_name", "task", "task_name")
                if name:
                    return True, name
            return True, explicit or symbol["symbol"]
        if explicit not in (None, ""):
            return True, explicit
        if _contains_expected(expected, symbol["content"]):
            return True, expected
        return False, None

    def analyze(self, target: Dict[str, Any], requirement_anchors=None) -> Dict[str, Any]:
        repo_key = str(target.get("repo_key") or "").strip()
        branch = str(target.get("branch") or "").strip()
        baseline = str(target.get("baseline") or "origin/master").strip()
        if not branch:
            raise blocked("gitnexus_target_invalid", "GitNexus 代码目标必须包含 branch", target_id=target.get("target_id"))
        self._prepare()
        warnings: List[str] = []
        repo, discovery = self._find_repo(repo_key, target, warnings)
        repo_key = str(repo.get("repo_key") or "").strip()
        target["repo_key"] = repo_key
        probe = self._call("query", {"query": "__AUDITOR_BRANCH_SCOPE_PROBE__", "search_query": "__AUDITOR_BRANCH_SCOPE_PROBE__", "repo": repo_key, "branch": branch, "include_content": False, "limit": 1}, warnings, required=True)
        branch_scope = _scope_from_payload(probe, branch)
        if branch_scope != "branch_scoped":
            raise blocked(
                "gitnexus_branch_scope_unverified",
                "无法证明 GitNexus 索引对应提测开发分支，已阻断该代码目标",
                repo_key=repo_key,
                branch=branch,
                branch_scope=branch_scope,
                forbid_workspace_fallback=True,
            )

        admission = {
            "repo_found": True,
            "branch_scope": branch_scope,
            "index_commit": repo.get("index_commit", ""),
            "index_time": repo.get("index_time", ""),
            "audit_eligible": True,
            "reason": "已确认仓库唯一存在且查询接受目标分支",
            "repo_key": repo_key,
            "mapping_source": discovery.get("matched_by", "gitnexus_auto_discovery"),
            "discovery": discovery,
            "branch": branch,
        }
        graph_evidence: Dict[str, List[Dict[str, Any]]] = {"processes": [], "symbols": [], "contexts": [], "impacts": [], "traces": []}
        detect_changes = self._call("detect_changes", {"scope": "compare", "base_ref": baseline, "repo": repo_key, "branch": branch}, warnings)
        if detect_changes is not None:
            graph_evidence["impacts"].append({"source": "gitnexus.detect_changes", "repo": repo_key, "branch": branch, "value": _unwrap(detect_changes)})

        facts = self._fact_list(requirement_anchors)
        dimension_status = {name: "unknown" for name in DIMENSION_FIELDS}
        dimension_items: Dict[str, List[Dict[str, Any]]] = {name: [] for name in DIMENSION_FIELDS}
        seen_symbols = set()
        plans = build_query_plans(facts, target, max_queries=self.config.get("max_queries_per_fact", 3))
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for plan in plans:
            grouped.setdefault(str(plan.get("stable_key") or ""), []).append(plan)
        for fact in facts:
            key = str(fact.get("stable_key") or "")
            dimension = str(fact.get("type") or "rules")
            if dimension not in dimension_items:
                dimension_items[dimension] = []
                dimension_status[dimension] = "unknown"
            matches: List[Dict[str, Any]] = []
            for plan in grouped.get(key, []):
                payload = self._call(
                    "query",
                    {
                        "query": plan["query"],
                        "search_query": plan["query"],
                        "task_context": plan["task_context"],
                        "goal": plan["goal"],
                        "repo": repo_key,
                        "branch": branch,
                        "include_content": bool(self.config.get("include_content", True)),
                        "limit": int(self.config.get("max_symbols_per_fact", 10) or 10),
                    },
                    warnings,
                    required=False,
                )
                for record in _walk_records(payload):
                    symbol = self._symbol(record, plan["query"], repo_key, branch)
                    if not symbol:
                        continue
                    identity = (symbol["uid"], symbol["file"], symbol["symbol"])
                    if identity not in seen_symbols:
                        seen_symbols.add(identity)
                        graph_evidence["symbols"].append({key_name: symbol[key_name] for key_name in ("uid", "symbol", "kind", "file", "line", "module", "process", "relevance", "repo", "branch", "source") if symbol.get(key_name) not in (None, "")})
                    matches.append(symbol)
            if not matches:
                dimension_status[dimension] = "unknown" if dimension_status[dimension] == "unknown" else dimension_status[dimension]
                continue
            if any(symbol["content"] for symbol in matches):
                dimension_status[dimension] = "verified" if dimension != "tables" or any(_is_sql_content(symbol["content"]) for symbol in matches) else "partial"
            else:
                dimension_status[dimension] = "partial"
            for symbol in matches[: int(self.config.get("max_symbols_per_fact", 10) or 10)]:
                anchor = {
                    "repo": repo_key,
                    "branch": branch,
                    "symbol_uid": symbol["uid"],
                    "symbol": symbol["symbol"],
                    "kind": symbol["kind"],
                    "file": symbol["file"],
                    "line": symbol["line"],
                    "module": symbol["module"],
                    "process": symbol["process"],
                    "query": symbol["query"],
                    "source": symbol["source"],
                    "content": symbol["content"],
                }
                if symbol["process"]:
                    graph_evidence["processes"].append({"process": symbol["process"], "symbol_uid": symbol["uid"], "file": symbol["file"], "repo": repo_key, "branch": branch})
                context = self._call("context", {"uid": symbol["uid"], "repo": repo_key, "branch": branch, "include_content": True}, warnings)
                if context is not None:
                    graph_evidence["contexts"].append({"symbol_uid": symbol["uid"], "repo": repo_key, "branch": branch, "value": _unwrap(context)})
                impact = self._call("impact", {"uid": symbol["uid"], "repo": repo_key, "branch": branch}, warnings)
                if impact is not None:
                    graph_evidence["impacts"].append({"symbol_uid": symbol["uid"], "repo": repo_key, "branch": branch, "value": _unwrap(impact)})
                trace = self._call("trace", {"uid": symbol["uid"], "repo": repo_key, "branch": branch}, warnings)
                if trace is not None:
                    graph_evidence["traces"].append({"symbol_uid": symbol["uid"], "repo": repo_key, "branch": branch, "value": _unwrap(trace)})
                has_value, value = self._dimension_value(fact, symbol)
                # A value dimension needs source and a comparable behaviour
                # value.  Relationship-only records remain graph evidence.
                if not symbol["content"] or not has_value or (dimension == "tables" and not _is_sql_content(symbol["content"])):
                    continue
                item = {"stable_key": key, "value": value, "evidence": anchor, "source": "gitnexus.query", "coverage_status": dimension_status[dimension]}
                dimension_items[dimension].append(item)

        if not self._tools.get("impact"):
            warnings.append("GitNexus 缺少 impact，影响范围未完整获取")
        if not self._tools.get("trace"):
            warnings.append("GitNexus 缺少 trace，调用链未完整获取")
        sql_required = any(str(fact.get("type")) in SQL_FACT_TYPES or any(token in str(fact.get("source_quote") or "") for token in SQL_TOKENS) for fact in facts)
        if sql_required and dimension_status.get("tables") != "verified":
            warnings.append("需求包含 SQL/MyBatis 语义，但未获得带源码锚点的 SQL 证据")
        files = []
        for symbol in graph_evidence["symbols"]:
            files.append({"path": symbol.get("file"), "status": "analyzed" if symbol.get("file") else "unknown", "technology": symbol.get("kind", "symbol"), "source": "gitnexus"})
        gaps = [{"dimension": name, "status": status, "reason": "远程图谱未返回足够文件/符号/源码行为证据"} for name, status in dimension_status.items() if status != "verified"]
        coverage = {
            "complete": False,
            "status": "partial" if any(status == "partial" for status in dimension_status.values()) else "unknown",
            "files": files,
            "gaps": gaps,
            "dimension_coverage": dimension_status,
        }
        analysis: Dict[str, Any] = {
            "schema_version": "1.0.0",
            "analysis_mode": "gitnexus_remote",
            "repository": repo_key,
            "repo_key": repo_key,
            "branch": branch,
            "baseline": baseline,
            "index_commit": repo.get("index_commit", ""),
            "index_time": repo.get("index_time", ""),
            "branch_scope": branch_scope,
            "files_changed": [],
            "file_changes": {},
            "api_endpoints": dimension_items.get("apis", []),
            "entry_points": [],
            "sql_analysis": dimension_items.get("tables", []),
            "business_rules": dimension_items.get("rules", []),
            "processing_rules": [*dimension_items.get("sorting", []), *dimension_items.get("pagination", []), *dimension_items.get("side_effects", [])],
            "sorting": dimension_items.get("sorting", []),
            "pagination": dimension_items.get("pagination", []),
            "side_effects": dimension_items.get("side_effects", []),
            "fields": dimension_items.get("fields", []),
            "error_handling": dimension_items.get("errors", []),
            "target_id": target.get("target_id", ""),
            "scheduled_tasks": dimension_items.get("jobs", []),
            "config_values": dimension_items.get("configs", []),
            "analysis_warnings": warnings,
            "graph_evidence": graph_evidence,
            "technology_coverage": coverage,
            "coverage_summary": coverage,
            "dimension_coverage": dimension_status,
            "sql_evidence_required": sql_required,
            "analysis_capabilities": {
                "provider": "gitnexus",
                "mcp": {"url": self.config.get("url", ""), "server_info": self.client.server_info, "tools": sorted(self._tools), "status": "available"},
                "index_admission": admission,
                "limitations": warnings,
            },
        }
        return {
            "code_analysis": analysis,
            "snapshot": {
                "repository": repo_key,
                "repo_key": repo_key,
                "repo_path": "",
                "branch": branch,
                "baseline": baseline,
                "target_sha": repo.get("index_commit", ""),
                "baseline_sha": "",
                "index_commit": repo.get("index_commit", ""),
                "index_time": repo.get("index_time", ""),
                "branch_scope": branch_scope,
                "audit_eligible": True,
            },
            "coverage": coverage,
            "analysis_capabilities": analysis["analysis_capabilities"],
        }
