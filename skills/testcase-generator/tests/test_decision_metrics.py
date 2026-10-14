import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from decision_register import build_decision_register
from workflow_metrics import _review_issue_metrics


class DecisionMetricsTests(unittest.TestCase):
    def test_review_resolution_rate_uses_complete_decision_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            internal = Path(temp)
            review = {
                "summary": {"total_issues": 2, "critical": 1, "major": 1},
                "categories": [
                    {
                        "items": [
                            {"id": "RQ-001", "title": "问题一", "severity": "critical"},
                            {"id": "RQ-002", "title": "问题二", "severity": "major"},
                        ]
                    }
                ],
            }
            (internal / "需求_评审数据.json").write_text(
                json.dumps(review, ensure_ascii=False), encoding="utf-8"
            )
            register = build_decision_register(review)
            register["items"][0].update(
                {
                    "status": "confirmed",
                    "decision": "接受",
                    "decision_type": "accept",
                    "confirmed_by": "张三",
                    "confirmed_at": "2026-08-03",
                }
            )
            (internal / "decision_register.json").write_text(
                json.dumps(register, ensure_ascii=False), encoding="utf-8"
            )

            metrics = _review_issue_metrics(internal)

        self.assertEqual(1, metrics["resolved_count"])
        self.assertEqual(0.5, metrics["resolution_rate"])


if __name__ == "__main__":
    unittest.main()
