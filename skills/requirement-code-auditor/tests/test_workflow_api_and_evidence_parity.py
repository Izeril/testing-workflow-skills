import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import workflow_api
from scripts.analysis_batching import merge_analysis_batches
from scripts.audit_runner import _aggregate
from scripts.comparator import compare_structured
from scripts.evidence_normalizer import build_code_evidence
from scripts.evidence_provider import LocalRepositoryEvidenceProvider
from scripts.git_runner import GitResult


def batch(path, endpoint, value):
    return {
        "file_changes": {path: "M"},
        "api_endpoints": [endpoint],
        "business_rules": {"if_else_rules": [{"condition": "amount > 0", "action": "accept()", "else_action": "reject()", "source_file": path, "line": 12}], "business_rules_summary": path},
        "config_values": {"properties": [{"config_key": path + ".limit", "config_value": value, "source_file": path}]},
        "call_chains": [{"file": path, "call_graph": {"run": ["accept"]}}],
        "syntax_analysis": [{"file": path, "engine": "balanced-token-fallback"}],
        "python_analysis": [{"file": path, "engine": "python-ast"}],
        "test_cases": [{"name": path + " boundary"}],
        "output_layer": {"schema_version": "1.0.0", "coverage_status": "partial", "endpoints": [{"endpoint": endpoint}], "verified_fields": [{"field_key": endpoint + ".total", "source_expression": "entity.getAmount()", "source_column": "total_amount", "file": path + ".VO", "line": 2, "mapping_file": path, "mapping_line": 22}], "declared_fields": [{"field": "unmapped", "file": path + ".VO"}], "context_files": [path + ".Controller"], "warnings": []},
        "technology_coverage": {"files": [{"path": path, "status": "partial"}], "gaps": [], "complete": False},
        "analysis_warnings": [],
    }


class WorkflowApiAndEvidenceParityTests(unittest.TestCase):
    def test_workflow_api_preserves_arguments_and_does_not_write_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            original = batch("Service.java", "/orders", "12")
            with patch.object(workflow_api, "_extract_business_logic", return_value=original) as extractor:
                actual = workflow_api.extract_business_logic(temp, "feature", "main", selected_files=["Service.java"])
            extractor.assert_called_once_with(temp, "feature", "main", selected_files=["Service.java"])
            self.assertIs(original, actual)
            self.assertEqual([], list(Path(temp).iterdir()))
        facts = {"tables": {"items": [{"stable_key": "orders"}], "coverage_status": "complete", "comparison_policy": "identity"}}
        evidence = copy.deepcopy(facts)
        self.assertEqual(compare_structured(facts, evidence), workflow_api.compare_structured(facts, evidence))

    def test_batches_retain_nested_rules_configs_and_all_output_assignments(self):
        merged = merge_analysis_batches([batch("A.java", "/a", "12"), batch("B.java", "/b", "24")], ["A.java", "B.java"], [])
        self.assertIsInstance(merged["business_rules"], dict)
        self.assertIsInstance(merged["config_values"], dict)
        self.assertEqual({"A.java", "B.java"}, {item["file"] for item in merged["call_chains"]})
        for field in ("syntax_analysis", "python_analysis", "test_cases"):
            self.assertEqual(2, len(merged[field]), field)
        self.assertEqual(2, len(merged["business_rules"]["if_else_rules"]))
        self.assertEqual(2, len(merged["config_values"]["properties"]))
        self.assertEqual({"/a.total", "/b.total"}, {item["field_key"] for item in merged["output_layer"]["verified_fields"]})
        self.assertEqual(["A.java.Controller", "B.java.Controller"], merged["output_layer"]["context_files"])
        evidence = build_code_evidence(merged)
        self.assertEqual({"A.java.limit": "12", "B.java.limit": "24"}, {item["stable_key"]: item["value"] for item in evidence["configs"]["items"]})
        self.assertEqual(2, len(evidence["rules"]["items"]))
        self.assertEqual({"/a.total", "/b.total"}, {item["stable_key"] for item in evidence["fields"]["items"]})
        self.assertEqual("partial", evidence["fields"]["coverage_status"])
        self.assertEqual("identity", evidence["fields"]["comparison_policy"])
        self.assertTrue(all(item["source_column"] == "total_amount" for item in evidence["fields"]["items"]))
        self.assertFalse(any(item["stable_key"] == "unmapped" for item in evidence["fields"]["items"]))

    def test_partial_output_does_not_report_untracked_declaration_as_missing(self):
        evidence = build_code_evidence(batch("A.java", "/a", "12"))
        facts = {"fields": {"items": [{"stable_key": "/a.total"}, {"stable_key": "/a.unmapped"}], "coverage_status": "complete", "comparison_policy": "identity"}}
        comparison = workflow_api.compare_structured(facts, evidence, dimensions=["fields"])
        actual = {item["key"]: item["classification"] for item in comparison["items"]}
        self.assertEqual({"/a.total": "一致", "/a.unmapped": "证据不足"}, actual)

    def test_multi_target_keeps_target_anchors_for_same_output_key(self):
        results = []
        for target_id in ("first", "second"):
            analysis = batch("Service.java", "/same", target_id)
            analysis["target_id"] = target_id
            analysis["dimension_coverage"] = {"configs": "partial" if target_id == "first" else "complete"}
            results.append({"code_analysis": analysis, "snapshot": {"repository": target_id}})
        aggregate = _aggregate(results)
        self.assertEqual(2, len(aggregate["output_layers"]))
        self.assertEqual(2, len(aggregate["call_chains"]))
        self.assertEqual("partial", aggregate["dimension_coverage"]["configs"])
        evidence = build_code_evidence(aggregate)
        self.assertEqual({"first", "second"}, {item["target_id"] for item in evidence["fields"]["items"]})
        self.assertEqual({"first", "second"}, {item["target_id"] for item in evidence["configs"]["items"]})
        self.assertEqual({"first", "second"}, {item["target_id"] for item in evidence["apis"]["items"]})

    def test_generic_dimension_uses_preferred_keys_and_keeps_source_anchor(self):
        evidence = build_code_evidence({"sorting": [{"field": "created_at", "value": "desc", "source_file": "Mapper.xml", "line": 8}], "technology_coverage": {"complete": True, "gaps": []}})
        item = evidence["sorting"]["items"][0]
        self.assertEqual(("created_at", "desc", "Mapper.xml", 8), (item["stable_key"], item["value"], item["source"], item["line"]))

    def test_single_batch_keeps_original_nested_shape_and_is_not_mutated(self):
        original = batch("A.java", "/a", "12")
        expected = copy.deepcopy(original)
        merged = merge_analysis_batches([original], ["A.java"], [])
        self.assertEqual(expected, original)
        self.assertEqual(expected["business_rules"], merged["business_rules"])
        self.assertEqual(expected["config_values"], merged["config_values"])
        self.assertEqual(expected["output_layer"], merged["output_layer"])

    def test_local_provider_reads_resolved_commits_and_honors_empty_scope(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / ".git").mkdir()
            responses = [GitResult([], 0, "a" * 40, ""), GitResult([], 0, "b" * 40, ""), GitResult([], 0, "A.java\nB.java\n", "")]
            with patch("scripts.evidence_provider.run_git", side_effect=responses) as runner, patch("scripts.evidence_provider.analyze_code", return_value=batch("A.java", "/a", "12")) as analyzer:
                LocalRepositoryEvidenceProvider().analyze({"repo_path": temp, "branch": "feature", "baseline": "main", "selected_files": []})
            analyzer.assert_called_once_with(temp, "a" * 40, "b" * 40, selected_files=[])
            self.assertEqual(["diff", "--name-only", "b" * 40 + "..." + "a" * 40], runner.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
