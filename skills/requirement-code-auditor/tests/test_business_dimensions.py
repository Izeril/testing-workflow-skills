import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.comparator import compare_structured


class BusinessDimensionTests(unittest.TestCase):
    def test_extended_dimensions_preserve_structured_comparison_semantics(self):
        requirement = {
            "actors": {"企业用户": {"content": "企业用户"}},
            "scenarios": {"提交申报": {"content": "允许提交"}},
            "state_transitions": {"申报状态": {"content": "待申报→受理中"}},
            "calculations": {"应纳税额": {"content": "收入×税率"}},
            "integrations": {"风控接口": {"content": "超时可重试"}},
            "data_compatibility": {"历史申报": {"content": "查询不受影响"}},
        }
        code = {
            "actors": {"企业用户": {"content": "企业用户"}},
            "scenarios": {"提交申报": {"content": "禁止提交"}},
            "state_transitions": {"申报状态": {"content": "待申报→受理中"}},
            "calculations": {"应纳税额": {"content": "收入×税率"}},
            "integrations": {"风控接口": {"content": "超时可重试"}},
            "data_compatibility": {"历史申报": {"content": "查询不受影响"}},
        }

        result = compare_structured(requirement, code)

        by_dimension = {item["dimension"]: item["classification"] for item in result["items"]}
        self.assertEqual("不一致", by_dimension["scenarios"])
        self.assertEqual("一致", by_dimension["actors"])
        self.assertEqual("一致", by_dimension["state_transitions"])
        self.assertEqual("一致", by_dimension["calculations"])
        self.assertEqual("一致", by_dimension["integrations"])
        self.assertEqual("一致", by_dimension["data_compatibility"])


if __name__ == "__main__":
    unittest.main()
