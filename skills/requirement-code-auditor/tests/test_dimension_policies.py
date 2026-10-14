import sys
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

from scripts.comparator import compare_structured


class DimensionPolicyTests(unittest.TestCase):
    def test_identifier_dimension_matches_same_table_without_value(self):
        requirement = {
            "tables": {
                "items": {
                    "t_order": {"content": "t_order", "source": "requirement.md"},
                },
                "coverage_status": "complete",
                "comparison_policy": "identity",
            }
        }
        code = {
            "tables": {
                "items": [
                    {"stable_key": "t_order", "source": "OrderMapper.xml"},
                ],
                "coverage_status": "complete",
            }
        }

        result = compare_structured(requirement, code)

        self.assertEqual("1.1.0", result["schema_version"])
        self.assertEqual("一致", result["items"][0]["classification"])
        self.assertEqual("identity", result["items"][0]["comparison_policy"])
        self.assertIn("标识", result["items"][0]["reason"])

    def test_value_dimension_requires_comparable_code_value(self):
        requirement = {
            "rules": {
                "items": {
                    "submit_gate": {"content": "only READY can submit", "source": "requirement.md"},
                },
                "comparison_policy": "value",
            }
        }
        code = {
            "rules": {
                "items": [
                    {"stable_key": "submit_gate", "source": "SubmitService.java:42"},
                ],
                "coverage_status": "complete",
            }
        }

        result = compare_structured(requirement, code)

        self.assertEqual("证据不足", result["items"][0]["classification"])
        self.assertEqual("value", result["items"][0]["comparison_policy"])

    def test_partial_code_coverage_never_produces_missing(self):
        requirement = {"tables": {"t_required": {"content": "t_required"}}}
        code = {
            "tables": {
                "items": [{"stable_key": "t_other", "source": "OtherMapper.xml"}],
                "coverage_status": "partial",
                "comparison_policy": "identity",
            }
        }

        result = compare_structured(requirement, code)
        required = next(item for item in result["items"] if item["key"] == "t_required")

        self.assertEqual("证据不足", required["classification"])
        self.assertEqual(0, result["summary"]["缺失"])
        self.assertTrue(any(item["code"] == "partial_dimension_coverage" for item in result["warnings"]))

    def test_complete_code_coverage_can_produce_missing(self):
        requirement = {"tables": {"t_required": {"content": "t_required"}}}
        code = {
            "tables": {
                "items": [{"stable_key": "t_other", "source": "OtherMapper.xml"}],
                "coverage_status": "complete",
                "comparison_policy": "identity",
            }
        }

        result = compare_structured(requirement, code)
        required = next(item for item in result["items"] if item["key"] == "t_required")

        self.assertEqual("缺失", required["classification"])

    def test_filtered_keys_are_reported_as_structured_warnings(self):
        result = compare_structured(
            {"rules": {"告警编号：ALM001 问题直达：https://example.invalid": "raw"}},
            {},
        )

        self.assertEqual(0, result["summary"]["总计"])
        self.assertEqual("filtered_unstructured_key", result["warnings"][0]["code"])


if __name__ == "__main__":
    unittest.main()
