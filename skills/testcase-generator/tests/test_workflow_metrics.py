import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import main
from workflow_metrics import build_metrics, write_workflow_metrics


class WorkflowMetricsTests(unittest.TestCase):
    def _write_json(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_metrics_aggregate_seven_stages_and_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "测试分析"
            internal = output_dir / ".ai-internal"
            internal.mkdir(parents=True)
            self._write_json(
                internal / "testpoints.json",
                {"test_points": [
                    {"priority": "P1"}, {"priority": "P1"},
                    *[{"priority": "P2"} for _ in range(8)],
                ]},
            )
            self._write_json(
                internal / "评审差异预览报告.json",
                {"statistics": {"added": 2, "deleted": 1, "modified": 1, "normal": 6}},
            )
            self._write_json(
                internal / "quality_gate_report.json",
                {"summary": {"status": "blocked"}, "gates": [
                    {"status": "passed"}, {"status": "passed"},
                    {"status": "passed"}, {"status": "failed"},
                ]},
            )
            self._write_json(
                internal / "需求_评审数据.json",
                {"summary": {"total_issues": 2, "major": 2}, "categories": [{"items": [
                    {"status": "已解决", "decision": "接受"},
                    {"status": "待解决", "decision": ""},
                ]}]},
            )
            self._write_json(
                internal / "测试用例.json",
                {"metadata": {"statistics": {"p1_count": 3, "p2_count": 5}},
                 "testcases": [{} for _ in range(8)]},
            )
            self._write_json(
                internal / "code_impact.json",
                {"summary": {"selected_count": 4, "coverage_gap_files": 1},
                 "impact_confidence": "medium"},
            )
            events = []
            for index, step in enumerate((
                "check", "analysis", "review", "testpoint", "finalize", "refine", "archive"
            ), 1):
                events.append({"run_id": "run-1", "step": step, "status": "passed", "duration_seconds": index})
            events.insert(1, {"run_id": "run-0", "step": "analysis", "status": "failed", "duration_seconds": 2})
            metrics = build_metrics(output_dir, {"events": events}, "run-1")
            self.assertEqual(1.0, metrics["summary"]["workflow_completion_rate"])
            self.assertEqual(0.5, next(row for row in metrics["stages"] if row["step"] == "analysis")["pass_rate"])
            self.assertEqual(10, metrics["artifacts"]["testpoints"]["total_count"])
            self.assertEqual(0.4, metrics["artifacts"]["review_changes"]["modification_rate"])
            self.assertEqual(0.75, metrics["artifacts"]["quality_gates"]["pass_rate"])
            written, json_path, report_path = write_workflow_metrics(output_dir, {"events": events}, "run-1")
            self.assertTrue(json_path.exists())
            self.assertTrue(report_path.exists())
            self.assertIn("七阶段执行", report_path.read_text(encoding="utf-8"))
            self.assertIn("最近执行日志", report_path.read_text(encoding="utf-8"))
            self.assertEqual(written["summary"], metrics["summary"])

    def test_execution_trace_refreshes_metrics_and_records_run_id(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch("builtins.print"):
            req_dir = Path(temp_dir)
            output_dir = req_dir / "测试分析"
            started = main.begin_stage("check", output_dir)
            trace_path = main.append_execution_trace(
                req_dir, "check", "passed", started, output_dir, "ok"
            )
            trace = json.loads(Path(trace_path).read_text(encoding="utf-8"))
            self.assertEqual(main.WORKFLOW_RUN_ID, trace["events"][-1]["run_id"])
            self.assertEqual(1, trace["events"][-1]["attempt_number"])
            self.assertTrue((output_dir / ".ai-internal" / "workflow_metrics.json").exists())
            self.assertTrue((output_dir / "流程度量报告.md").exists())


    def test_uncaught_failure_hook_records_failed_attempt(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch("builtins.print"):
            req_dir = Path(temp_dir)
            output_dir = req_dir / "测试分析"
            original_hook = sys.excepthook
            delegated = []

            def fake_hook(exc_type, exc_value, traceback):
                delegated.append((exc_type, str(exc_value)))

            try:
                sys.excepthook = fake_hook
                main.begin_stage("analysis", output_dir)
                main._install_workflow_failure_hook(req_dir)
                sys.excepthook(RuntimeError, RuntimeError("boom"), None)
            finally:
                sys.excepthook = original_hook
                main._ACTIVE_STAGE_CONTEXT.clear()

            trace_path = output_dir / ".ai-internal" / "execution_trace.json"
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            self.assertEqual("failed", trace["events"][-1]["status"])
            self.assertEqual("RuntimeError", trace["events"][-1]["error_type"])
            structured_events = [
                json.loads(line) for line in
                (output_dir / ".ai-internal" / "workflow_log.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            failed = next(item for item in structured_events if item["event"] == "stage_completed")
            self.assertEqual("ERROR", failed["level"])
            self.assertEqual("RuntimeError", failed["error_type"])
            self.assertEqual([(RuntimeError, "boom")], delegated)


    def test_auto_mode_emits_canonical_stage_events(self):
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as temp_dir:
            req_dir = Path(temp_dir)
            output_dir = req_dir / "测试分析"
            output_dir.mkdir()
            seen = []
            args = SimpleNamespace(branch="feature", code_targets=None, repo=None)
            with patch.object(main, "check_and_initialize", return_value=(None, output_dir)), \
                    patch.object(main, "scan_and_convert_docs"), \
                    patch.object(main, "run_requirement_analysis", return_value=({}, {})), \
                    patch.object(main, "run_requirement_review"), \
                    patch.object(main, "run_testpoint_generate"), \
                    patch.object(main, "save_workflow_state"), \
                    patch.object(main, "append_execution_trace", side_effect=lambda _, step, *a, **k: seen.append(step)), \
                    patch("builtins.input", return_value="y"), \
                    patch("builtins.print"):
                main.run_auto_mode(req_dir, args, {}, "repo", "main", {})
            self.assertEqual(["check", "analysis", "review", "testpoint"], seen)


    def test_metrics_report_is_allowed_by_output_layout_gate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "测试分析"
            (output_dir / ".ai-internal").mkdir(parents=True)
            (output_dir / "流程度量报告.md").write_text("# metrics", encoding="utf-8")
            report = main._validate_output_layout(output_dir, "analysis")
            self.assertEqual("passed", report["status"])


if __name__ == "__main__":
    unittest.main()
