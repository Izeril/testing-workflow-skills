import json
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from workflow_metrics import build_metrics


class DualCompletionMetricsTests(unittest.TestCase):
    def test_requested_scope_can_be_complete_while_full_workflow_is_partial(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "测试分析"
            (output / ".ai-internal").mkdir(parents=True)
            trace = {"events": [
                {"run_id": "run-review", "step": "analysis", "status": "passed"},
                {"run_id": "run-review", "step": "review", "status": "passed"},
            ]}
            metrics = build_metrics(output, trace, "run-review")
            self.assertEqual(1.0, metrics["summary"]["requested_scope_completion"])
            self.assertEqual(0.2857, metrics["summary"]["full_workflow_completion"])
            self.assertEqual(0.2857, metrics["summary"]["workflow_completion_rate"])
            self.assertEqual(
                "awaiting_review_decisions",
                metrics["summary"]["human_confirmation_status"],
            )

    def test_human_confirmation_progress_requires_decisions_finalize_and_archive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "测试分析"
            internal = output / ".ai-internal"
            internal.mkdir(parents=True)
            (internal / "decision_register.json").write_text(json.dumps({
                "schema_version": "1.0.0",
                "items": [{
                    "issue_id": "ISSUE-001",
                    "severity": "major",
                    "status": "confirmed",
                    "decision_type": "accept",
                    "decision": "按约束实施并验证",
                    "confirmed_by": "张三",
                    "confirmed_at": "2026-08-03",
                    "affects_test_design": True,
                }],
            }, ensure_ascii=False), encoding="utf-8")
            trace = {"events": [
                {"run_id": "run-final", "step": "review", "status": "passed"},
                {"run_id": "run-final", "step": "finalize", "status": "passed"},
            ]}
            metrics = build_metrics(output, trace, "run-final")
            self.assertEqual(0.6667, metrics["summary"]["human_confirmation_completion"])
            self.assertEqual(
                "awaiting_archive_confirmation",
                metrics["summary"]["human_confirmation_status"],
            )

            trace["events"].append(
                {"run_id": "run-archive", "step": "archive", "status": "passed"}
            )
            metrics = build_metrics(output, trace, "run-archive")
            self.assertEqual(1.0, metrics["summary"]["human_confirmation_completion"])
            self.assertEqual("complete", metrics["summary"]["human_confirmation_status"])


if __name__ == "__main__":
    unittest.main()
