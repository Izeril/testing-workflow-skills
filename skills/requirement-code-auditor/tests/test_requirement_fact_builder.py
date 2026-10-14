import unittest

from scripts.requirement_fact_builder import build_requirement_facts


class RequirementFactBuilderTests(unittest.TestCase):
    def test_pagination_sorting_and_error_code_golden_sample(self):
        payload = build_requirement_facts(
            "=== 需求.md ===\n# 查询规则\n"
            "查询记录默认按申请时间最新优先展示，每页 20 条。页码超过最后一页时返回空列表。\n"
            "未登录用户调用接口时返回错误码 AUTH_001，并提示‘请先登录’。"
        )
        by_key = {item["stable_key"]: item for item in payload["items"]}
        self.assertIn("sorting:申请时间:desc", by_key)
        self.assertIn("pagination:page_size:20", by_key)
        self.assertIn("error:未登录用户调用接口:auth_001", by_key)
        for key in ("sorting:申请时间:desc", "pagination:page_size:20", "error:未登录用户调用接口:auth_001"):
            self.assertEqual("high", by_key[key]["confidence_level"])
            self.assertTrue(by_key[key]["comparable"])
            self.assertEqual(5, len(by_key[key]["confidence_components"]))
        self.assertEqual("core", by_key["error:未登录用户调用接口:auth_001"]["criticality"])

    def test_cross_document_value_conflict_requires_human_review(self):
        payload = build_requirement_facts("=== A.md ===\n每页 20 条\n=== B.md ===\n每页 50 条")
        self.assertEqual(1, len(payload["conflicts"]))
        self.assertTrue(payload["quality_summary"]["human_review_required"])
        self.assertTrue(all(not item["comparable"] for item in payload["items"]))

    def test_low_information_text_does_not_become_false_fact(self):
        payload = build_requirement_facts("=== 需求.md ===\n支持查询优化")
        self.assertEqual([], payload["items"])
        self.assertEqual("blocked", payload["quality_summary"]["status"])
