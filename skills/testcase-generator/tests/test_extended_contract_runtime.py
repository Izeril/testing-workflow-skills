import sys
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from decision_register import build_decision_register
import workflow_testpoint_legacy


class ExtendedContractRuntimeTests(unittest.TestCase):
    def test_runtime_map_covers_all_nine_nested_artifacts(self):
        expected = {
            "requirement_facts.json", "test_design_model.json",
            "coverage_matrix.json", "quality_gate_report.json",
            "business_impact.json", "testpoints.json",
            "decision_register.json", "source_inventory.json",
            "workflow_metrics.json",
        }
        self.assertEqual(expected, set(workflow_testpoint_legacy._NESTED_ARTIFACT_CONTRACTS))

    def test_decision_register_without_workflow_envelope_is_valid(self):
        payload = build_decision_register({"categories": [{"items": [{
            "id": "RQ-001", "title": "口径待确认",
            "severity": "major", "status": "pending",
        }]}]})
        report = workflow_testpoint_legacy._assess_internal_json_payload(
            "decision_register.json", payload
        )
        self.assertEqual("ok", report["status"])
        self.assertTrue(report["nested_contract_checked"])
        self.assertEqual("1.0.0", report["expected_schema_version"])
        self.assertNotIn("workflow_version", " ".join(report["reasons"]))

    def test_mixed_schema_versions_do_not_create_false_incompatibility(self):
        comparison = {
            "schema_version": "1.1.0",
            "workflow_version": "testcase-generator-2026.07",
            "generated_at": "2026-08-04T09:00:00",
            "summary": {}, "items": [],
        }
        impact = {
            "schema_version": "1.3.0",
            "workflow_version": "testcase-generator-2026.07",
            "generated_at": "2026-08-04T09:00:00",
            "status": "completed", "summary": {}, "impact_scope": {},
        }
        for filename, payload, version in (
            ("requirement_code_comparison.json", comparison, "1.1.0"),
            ("code_impact.json", impact, "1.3.0"),
        ):
            with self.subTest(filename=filename):
                report = workflow_testpoint_legacy._assess_internal_json_payload(filename, payload)
                self.assertEqual("ok", report["status"])
                self.assertEqual(version, report["expected_schema_version"])


if __name__ == "__main__":
    unittest.main()
