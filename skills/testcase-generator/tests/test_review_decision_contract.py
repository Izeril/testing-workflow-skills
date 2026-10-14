import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import workflow_review


def issue(severity="critical"):
    return {
        "id": "EV-001",
        "category": "技术方案",
        "title": "需求与代码不一致",
        "location": "结构化需求—代码对比",
        "description": "同键值冲突",
        "suggestion": "确认需求口径",
        "severity": severity,
        "preconditions": ["比较证据完整"],
        "evidence": [{"source": "comparison.json"}],
    }


class ReviewDecisionContractTests(unittest.TestCase):
    def test_review_json_contains_default_confirmation_fields(self):
        payload = workflow_review._build_review_json([issue()], "需求", [], [])
        item = payload["categories"][0]["items"][0]

        self.assertEqual("pending", item["status"])
        self.assertEqual("", item["decision_type"])
        self.assertEqual("", item["confirmed_by"])
        self.assertEqual("", item["confirmed_at"])
        self.assertTrue(item["affects_test_design"])
        self.assertIn("reject", item["decision_options"])

    def test_critical_issue_produces_blocking_release_recommendation(self):
        report = workflow_review._build_review_report([issue()], "需求", [], [])

        self.assertIn("放行结论", report)
        self.assertIn("阻断：存在严重问题", report)
        self.assertNotIn("可继续开发**：✅", report)
        self.assertIn("**确认日期**", report)

    def test_minor_only_report_can_continue(self):
        report = workflow_review._build_review_report(
            [issue(severity="minor")], "需求", [], []
        )

        self.assertIn("可继续：未发现严重或重要问题", report)


if __name__ == "__main__":
    unittest.main()
