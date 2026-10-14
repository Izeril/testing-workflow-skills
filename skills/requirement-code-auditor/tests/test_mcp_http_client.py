import json
import unittest

from scripts.mcp_http_client import AUDIT_TOOL_ALLOWLIST, MCPClientError, MCPHttpClient, parse_mcp_body


class _Response:
    def __init__(self, payload, *, headers=None, status=200):
        self._payload = payload
        self.headers = headers or {"content-type": "application/json"}
        self.status = status

    def read(self):
        return self._payload if isinstance(self._payload, bytes) else self._payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _Opener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append((request, timeout))
        return self.responses.pop(0)


class MCPHttpClientTests(unittest.TestCase):
    def test_sse_single_and_multiple_messages(self):
        one = parse_mcp_body("event: message\ndata: {\"jsonrpc\": \"2.0\", \"id\": 1, \"result\": {}}\n", "text/event-stream")
        many = parse_mcp_body("data: {\"id\": 1, \"result\": {\"a\": 1}}\n\ndata: {\"id\": 2, \"result\": {\"b\": 2}}\n", "text/event-stream")
        self.assertEqual({}, one[0]["result"])
        self.assertEqual([1, 2], [item["id"] for item in many])

    def test_initialize_session_tools_and_call(self):
        opener = _Opener([
            _Response(json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "gitnexus"}}}), headers={"content-type": "application/json", "mcp-session-id": "session-1"}),
            _Response(b"", status=202),
            _Response(json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"tools": [{"name": "query"}]}})),
            _Response(json.dumps({"jsonrpc": "2.0", "id": 4, "result": {"content": [{"type": "text", "text": "ok"}]}})),
        ])
        client = MCPHttpClient("http://example.invalid/mcp", opener=opener)
        tools = client.list_tools()
        result = client.call_tool("query", {"query": "orders"})
        self.assertIn("query", tools)
        self.assertEqual("ok", result["content"][0]["text"])
        for index in (2, 3):
            headers = {str(key).casefold(): value for key, value in opener.requests[index][0].headers.items()}
            self.assertEqual("session-1", headers["mcp-session-id"])
    def test_mutating_tool_is_rejected_by_allowlist(self):
        client = MCPHttpClient("http://example.invalid/mcp")
        with self.assertRaises(MCPClientError) as caught:
            client.call_tool("rename", {})
        self.assertEqual("mcp_tool_forbidden", caught.exception.code)
        self.assertNotIn("rename", AUDIT_TOOL_ALLOWLIST)


if __name__ == "__main__":
    unittest.main()
