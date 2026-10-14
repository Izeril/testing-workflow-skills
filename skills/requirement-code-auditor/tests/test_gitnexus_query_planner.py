import unittest

from scripts.gitnexus_query_planner import build_query_plan


class GitNexusQueryPlannerTests(unittest.TestCase):
    def test_plan_is_bounded_and_keeps_requirement_anchor(self):
        fact = {
            "type": "rules",
            "stable_key": "rules:password:validation",
            "subject": "修改密码",
            "behavior": "校验原密码",
            "source_quote": "修改密码时必须校验原密码",
            "source_document": "需求.md",
            "source_anchor": "需求描述:L3",
            "comparable": True,
        }
        plans = build_query_plan(fact, {"repo_key": "jwy3", "branch": "feature/password", "services": ["jwy3"]}, max_queries=2)
        self.assertEqual(2, len(plans))
        self.assertTrue(all("原密码" in item["query"] or "validation" in item["query"] for item in plans))
        self.assertEqual("需求.md", plans[0]["source_anchor"]["document"])
        self.assertIn("feature/password", plans[0]["task_context"])


if __name__ == "__main__":
    unittest.main()
