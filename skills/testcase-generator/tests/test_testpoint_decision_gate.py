import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from decision_register import build_decision_register
import workflow_testpoint_facade as facade


def review_payload():
    return {
        "categories": [
            {
                "items": [
                    {"id": "EV-001", "title": "实现差异", "severity": "critical"}
                ]
            }
        ]
    }


class TestpointDecisionGateTests(unittest.TestCase):
    def write_json(self, path, payload):
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_pending_critical_issue_blocks_testpoint_stage(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            internal = output / ".ai-internal"
            internal.mkdir()
            self.write_json(
                internal / "decision_register.json",
                build_decision_register(review_payload()),
            )

            with patch.object(facade, "_legacy_require_inputs"), self.assertRaisesRegex(
                RuntimeError, "EV-001"
            ):
                facade._require_testpoint_inputs(output, {})

    def test_completed_critical_decision_passes_decision_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            internal = output / ".ai-internal"
            internal.mkdir()
            register = build_decision_register(review_payload())
            register["items"][0].update(
                {
                    "status": "confirmed",
                    "decision": "接受并补充边界验证",
                    "decision_type": "accept_with_constraint",
                    "confirmed_by": "张三",
                    "confirmed_at": "2026-08-03",
                }
            )
            self.write_json(internal / "decision_register.json", register)
            for name, payload in {
                "code_targets.json": {"targets": [], "manifest_errors": []},
                "requirement_code_comparison.json": {"status": "skipped", "items": []},
                "business_impact.json": {"status": "skipped", "analysis_mode": "not_applicable", "items": []},
                "business_impact_gate.json": {"summary": {"status": "skipped"}, "analysis_mode": "not_applicable", "gates": []},
            }.items():
                self.write_json(internal / name, payload)

            with patch.object(facade, "_legacy_require_inputs"):
                facade._require_testpoint_inputs(output, {})


if __name__ == "__main__":
    unittest.main()
