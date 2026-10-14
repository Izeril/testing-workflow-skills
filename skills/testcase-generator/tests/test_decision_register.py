import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from decision_register import (
    build_decision_register,
    decision_is_complete,
    testpoint_blocking_issues,
    validate_decision_register,
)


def review_payload():
    return {
        "categories": [
            {
                "name": "技术方案",
                "items": [
                    {"id": "EV-001", "title": "表结构待确认", "severity": "critical"}
                ],
            }
        ]
    }


class DecisionRegisterTests(unittest.TestCase):
    def test_build_register_contains_fillable_confirmation_contract(self):
        register = build_decision_register(review_payload())
        item = register["items"][0]

        self.assertEqual("1.0.0", register["schema_version"])
        self.assertEqual("pending", item["status"])
        self.assertEqual("", item["confirmed_by"])
        self.assertIn("accept_with_constraint", item["decision_options"])
        self.assertEqual(["EV-001"], testpoint_blocking_issues(register))

    def test_complete_decision_passes_validation(self):
        register = build_decision_register(review_payload())
        register["items"][0].update(
            {
                "status": "confirmed",
                "decision": "按约束方案实现并验证边界",
                "decision_type": "accept_with_constraint",
                "confirmed_by": "张三",
                "confirmed_at": "2026-08-03",
            }
        )

        self.assertTrue(decision_is_complete(register["items"][0]))
        report = validate_decision_register(register)
        self.assertEqual("passed", report["summary"]["status"])
        self.assertEqual([], testpoint_blocking_issues(register))

    def test_rebuild_preserves_user_decision(self):
        existing = build_decision_register(review_payload())
        existing["items"][0].update(
            {
                "status": "confirmed",
                "decision": "接受",
                "decision_type": "accept",
                "confirmed_by": "李四",
                "confirmed_at": "2026-08-03",
            }
        )

        rebuilt = build_decision_register(review_payload(), existing)

        self.assertEqual("接受", rebuilt["items"][0]["decision"])
        self.assertEqual("李四", rebuilt["items"][0]["confirmed_by"])

    def test_invalid_date_is_not_complete(self):
        item = build_decision_register(review_payload())["items"][0]
        item.update(
            {
                "status": "confirmed",
                "decision": "接受",
                "decision_type": "accept",
                "confirmed_by": "王五",
                "confirmed_at": "2026-02-30",
            }
        )

        self.assertFalse(decision_is_complete(item))


if __name__ == "__main__":
    unittest.main()
