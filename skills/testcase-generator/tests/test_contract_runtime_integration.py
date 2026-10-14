import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import cross_skill_adapter
import workflow_common
import workflow_testpoint_legacy


def _requirement_payload():
    return {
        "schema_version": "1.0.0",
        "workflow_version": "testcase-generator-2026.07",
        "generated_at": "2026-08-03T12:00:00",
        "items": [
            {
                "fact_id": "REQ-001",
                "dimension": "rules",
                "type": "明确需求",
                "title": "处理规则",
                "content": "满足条件时执行",
                "source": ".ai-internal/normalized_requirement.md",
                "confidence": "high",
            }
        ],
    }


class ContractRuntimeIntegrationTests(unittest.TestCase):
    def test_contract_loader_is_allowlisted_and_leaves_sys_path_unchanged(self):
        before = list(sys.path)
        validator = cross_skill_adapter.load_contract_symbol("validate_payload")
        self.assertTrue(callable(validator))
        self.assertEqual(before, sys.path)
        with self.assertRaises(cross_skill_adapter.CrossSkillDependencyError):
            cross_skill_adapter.load_contract_symbol("_validate_schema_value")

    def test_internal_compatibility_report_uses_nested_contract(self):
        valid = workflow_testpoint_legacy._assess_internal_json_payload(
            "requirement_facts.json", _requirement_payload()
        )
        self.assertEqual("ok", valid["status"])
        self.assertTrue(valid["nested_contract_checked"])
        self.assertEqual("", valid["nested_contract_error"])

        broken_payload = _requirement_payload()
        del broken_payload["items"][0]["source"]
        broken = workflow_testpoint_legacy._assess_internal_json_payload(
            "requirement_facts.json", broken_payload
        )
        self.assertEqual("incompatible", broken["status"])
        self.assertEqual("regenerate_required", broken["action"])
        self.assertIn("$.items[0] 缺少字段: source", broken["nested_contract_error"])

    def test_internal_json_writer_does_not_mutate_caller_payload(self):
        payload = _requirement_payload()
        expected = copy.deepcopy(payload)
        with tempfile.TemporaryDirectory() as tmp:
            path = workflow_common._write_internal_json(
                Path(tmp), "requirement_facts.json", payload
            )
            written = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(expected, payload)
        self.assertEqual(expected, written)


if __name__ == "__main__":
    unittest.main()
