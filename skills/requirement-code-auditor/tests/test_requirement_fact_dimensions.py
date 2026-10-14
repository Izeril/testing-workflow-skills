import unittest

from scripts.requirement_fact_builder import build_requirement_facts


class RequirementFactDimensionTests(unittest.TestCase):
    def test_state_calculation_side_effect_integration_and_field_are_separate(self):
        payload = build_requirement_facts(
            "=== 需求.md ===\n"
            "审核通过后状态从待审核变为已生效。\n"
            "税额等于计税金额乘税率。\n"
            "保存后新增一条审计日志记录。\n"
            "系统调用征信服务接口获取结果。\n"
            "响应字段 creditScore 必须返回。"
        )
        types = {item["type"] for item in payload["items"]}
        self.assertTrue({"state_transitions", "calculations", "side_effects", "integrations", "fields"}.issubset(types))
        for item in payload["items"]:
            self.assertTrue(item["source_document"])
            self.assertTrue(item["source_anchor"])
            self.assertTrue(item["source_quote"])


if __name__ == "__main__":
    unittest.main()
