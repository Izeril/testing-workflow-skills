import importlib.util
import json
import sys
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "illegal_violation"
sys.path.insert(0, str(SKILL_DIR))

from scripts.comparator import compare_structured
from scripts.comparator.compatibility import ReqCodeComparator


class ComparatorTest(unittest.TestCase):
    def load(self, name):
        return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))

    def test_domain_fixture_is_compared_structurally(self):
        result = compare_structured(
            self.load("requirement.json"),
            self.load("code_evidence.json"),
        )
        self.assertEqual(self.load("expected.json")["summary"], result["summary"])

    def test_absent_code_dimension_is_insufficient_evidence(self):
        result = compare_structured({"tables": ["sample_table"]}, {})
        self.assertEqual("证据不足", result["items"][0]["classification"])
        self.assertEqual(0, result["summary"]["缺失"])

    def test_raw_alert_and_serialized_json_keys_are_rejected(self):
        requirement = {
            "rules": {
                "告警编号：ALM001 问题直达：https://example.invalid/" + "x" * 150: {
                    "content": "原始告警"
                },
                json.dumps({"business_rules_summary": "x" * 200}, ensure_ascii=False): {
                    "content": "原始 JSON"
                },
                "解决方案": {"content": "使用稳定去重标识"},
            }
        }
        result = compare_structured(requirement, {})
        self.assertEqual(1, result["summary"]["总计"])
        self.assertEqual("解决方案", result["items"][0]["key"])
    def test_legacy_wrapper_uses_the_same_engine(self):
        wrapper = ReqCodeComparator({"fields": {"amount": "decimal"}}, {"fields": {"amount": "string"}})
        structured = wrapper.compare_structured()
        self.assertEqual("不一致", structured["items"][0]["classification"])
        self.assertIn("[不一致]", wrapper.compare()[0])

    def test_production_comparator_contains_no_fixture_domain_terms(self):
        source = "\n".join(
            (SKILL_DIR / "scripts" / "comparator" / name).read_text(encoding="utf-8")
            for name in ("comparator.py", "compatibility.py")
        )
        for term in ("违法违章", "税局服务监控", "risk_tax_punish", "t_tax_special_legal", "凭安"):
            self.assertNotIn(term, source)


if __name__ == "__main__":
    unittest.main()
