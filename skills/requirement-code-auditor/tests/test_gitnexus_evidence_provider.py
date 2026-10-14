import unittest

from scripts.comparator import compare_structured
from scripts.evidence_normalizer import build_code_evidence
from scripts.gitnexus_evidence_provider import GitNexusEvidenceProvider
from scripts.skill_errors import AuditorError


class _FakeGitNexus:
    def __init__(self, *, scoped=True, content=True):
        self.server_info = {"name": "gitnexus"}
        self.scoped = scoped
        self.content = content

    def list_tools(self):
        return {name: {"name": name} for name in ("list_repos", "query", "context", "impact", "trace", "detect_changes")}

    def call_tool(self, name, arguments):
        if name == "list_repos":
            return {"repos": [{"repo_key": "jwy3", "index_commit": "fe147a7", "index_time": "2026-08-12T03:00:14Z"}]}
        if name == "query" and arguments.get("query") == "__AUDITOR_BRANCH_SCOPE_PROBE__":
            return {"branch_scope": "branch_scoped" if self.scoped else "workspace_index", "branch": arguments.get("branch") if self.scoped else "master"}
        if name == "query":
            return {"symbols": [{
                "uid": "uid-create-order",
                "name": "createOrder",
                "kind": "Method",
                "file_path": "src/OrderController.java",
                "line": 42,
                "method": "POST",
                "path": "/orders",
                "content": '@PostMapping("/orders") public void createOrder() {}' if self.content else "",
            }]}
        return {"symbols": []}


class GitNexusEvidenceProviderTests(unittest.TestCase):
    def test_missing_connection_is_a_configuration_error(self):
        with self.assertRaises(AuditorError) as caught:
            GitNexusEvidenceProvider({})
        self.assertEqual("gitnexus_url_missing", caught.exception.error_code)
        self.assertEqual(2, caught.exception.exit_code)

    def test_explicit_connection_and_injected_client_are_supported(self):
        provider = GitNexusEvidenceProvider({"url": "https://example.invalid/mcp"})
        self.assertEqual("https://example.invalid/mcp", provider.client.url)
        client = _FakeGitNexus()
        self.assertIs(client, GitNexusEvidenceProvider({}, client=client).client)

    def _fact(self):
        return {
            "type": "apis",
            "stable_key": "api:post:/orders",
            "value": {"method": "POST", "path": "/orders"},
            "subject": "接口",
            "behavior": "POST",
            "source_document": "需求.md",
            "source_anchor": "正文:L2",
            "source_quote": "POST /orders 创建订单",
            "comparable": True,
        }

    def _target(self):
        return {"target_id": "jwy3-orders", "repo_key": "jwy3", "branch": "feature/orders", "baseline": "origin/master", "services": ["jwy3"]}

    def _auto_target(self):
        return {"target_id": "jwy3-orders", "branch": "feature/orders", "baseline": "origin/master", "services": ["jwy3"]}

    def test_source_anchor_and_index_admission_are_returned(self):
        result = GitNexusEvidenceProvider({"url": "http://example.invalid"}, client=_FakeGitNexus()).analyze(self._target(), [self._fact()])
        analysis = result["code_analysis"]
        self.assertEqual("gitnexus_remote", analysis["analysis_mode"])
        self.assertEqual("branch_scoped", analysis["branch_scope"])
        self.assertEqual("verified", analysis["dimension_coverage"]["apis"])
        evidence = build_code_evidence(analysis)
        item = evidence["apis"]["items"][0]
        self.assertEqual("src/OrderController.java", item["evidence"]["file"])
        self.assertEqual("uid-create-order", item["evidence"]["symbol_uid"])

    def test_zero_config_discovers_repo_by_service_alias(self):
        result = GitNexusEvidenceProvider({"url": "http://example.invalid"}, client=_FakeGitNexus()).analyze(self._auto_target(), [self._fact()])
        self.assertEqual("jwy3", result["snapshot"]["repo_key"])
        self.assertEqual("service_alias", result["code_analysis"]["analysis_capabilities"]["index_admission"]["mapping_source"])

    def test_zero_config_ambiguous_service_is_blocked(self):
        class Ambiguous(_FakeGitNexus):
            def call_tool(self, name, arguments):
                if name == "list_repos":
                    return {"repos": [
                        {"repo_key": "jwy3-a", "services": ["jwy3"]},
                        {"repo_key": "jwy3-b", "services": ["jwy3"]},
                    ]}
                return super().call_tool(name, arguments)

        with self.assertRaises(AuditorError) as caught:
            GitNexusEvidenceProvider({"url": "http://example.invalid"}, client=Ambiguous()).analyze(self._auto_target(), [self._fact()])
        self.assertEqual("gitnexus_repo_auto_discovery_ambiguous", caught.exception.error_code)


    def test_graph_only_match_is_insufficient(self):
        result = GitNexusEvidenceProvider({"url": "http://example.invalid"}, client=_FakeGitNexus(content=False)).analyze(self._target(), [self._fact()])
        evidence = build_code_evidence(result["code_analysis"])
        comparison = compare_structured({"apis": {"items": [{"stable_key": "api:post:/orders", "value": self._fact()["value"]}], "coverage_status": "complete", "comparison_policy": "identity"}}, evidence)
        self.assertEqual("证据不足", comparison["items"][0]["classification"])

    def test_unverified_branch_is_blocked(self):
        with self.assertRaises(AuditorError) as caught:
            GitNexusEvidenceProvider({"url": "http://example.invalid"}, client=_FakeGitNexus(scoped=False)).analyze(self._target(), [self._fact()])
        self.assertEqual("gitnexus_branch_scope_unverified", caught.exception.error_code)


if __name__ == "__main__":
    unittest.main()
