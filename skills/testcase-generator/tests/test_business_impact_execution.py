import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from business_impact_gate import assess_business_impact_gate
from business_impact_translator import translate_business_impacts
from business_impact_report import write_business_impact_report
import workflow_testpoint_facade as testpoint

class BusinessImpactExecutionTests(unittest.TestCase):
    def target(self, **changes):
        result = {"target_id": "service", "status": "completed", "analysis_mode": "development",
                  "analysis": {"files_changed": ["service.py"], "entry_points": ["submit"]}}
        result.update(changes)
        return result

    def test_no_targets_are_explicitly_skipped_not_passed(self):
        impact = translate_business_impacts({"items": []}, [], {}, {})
        gate = assess_business_impact_gate(impact, [])
        self.assertEqual("skipped", impact["status"])
        self.assertEqual("not_applicable", impact["analysis_mode"])
        self.assertEqual("skipped", gate["summary"]["status"])
        self.assertEqual("not_applicable", gate["analysis_mode"])
        self.assertFalse(any(g["status"] == "passed" for g in gate["gates"]))
        self.assertTrue(impact["skip_reason"])
        self.assertEqual([], impact["items"])

    def test_skipped_report_does_not_leave_stale_user_analysis(self):
        impact = translate_business_impacts({}, [], {}, {})
        gate = assess_business_impact_gate(impact, [])
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            stale = output / "业务影响分析.md"
            stale.write_text("old implementation conclusion", encoding="utf-8")
            report = write_business_impact_report(output, impact, gate)
            self.assertIsNone(report)
            self.assertFalse(stale.exists())
            archived = list((output / ".ai-internal").rglob("业务影响分析.md"))
            self.assertEqual(1, len(archived))
            self.assertEqual("old implementation conclusion", archived[0].read_text(encoding="utf-8"))

    def test_requested_but_failed_target_cannot_become_skip(self):
        for target in [self.target(status="blocked", analysis={}, block_reason="repository missing"),
                       self.target(analysis={"entry_points": ["submit"]}),
                       self.target(status="blocked")]:
            with self.subTest(target=target):
                impact = translate_business_impacts({}, [target], {}, {})
                gate = assess_business_impact_gate(impact, [target])
                self.assertEqual("blocked", impact["status"])
                self.assertEqual("blocked", gate["summary"]["status"])
                self.assertTrue(gate["summary"]["blocking_count"])
                self.assertEqual([], impact["items"])

    def test_invalid_target_manifest_is_not_no_code_request(self):
        gate = assess_business_impact_gate(
            {"items": [], "code_target_manifest_errors": ["invalid manifest"]}, [])
        self.assertEqual("blocked", gate["summary"]["status"])

    def test_invalid_manifest_remains_blocked_through_the_analysis_facade(self):
        import workflow_analysis_facade as analysis
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            internal = output / ".ai-internal"
            internal.mkdir()
            for name, value in {
                "code_targets.json": {"targets": [], "manifest_errors": ["invalid manifest"]},
                "code_impact.json": {"status": "blocked", "summary": {}},
                "requirement_facts.json": {"items": []},
            }.items():
                (internal / name).write_text(json.dumps(value), encoding="utf-8")
            with patch.object(analysis, "_legacy_run_requirement_analysis", return_value=({"project_name": "test"}, None)):
                analysis.run_requirement_analysis(output, output)
            impact = json.loads((internal / "business_impact.json").read_text(encoding="utf-8"))
            gate = json.loads((internal / "business_impact_gate.json").read_text(encoding="utf-8"))
            self.assertEqual("blocked", impact["status"])
            self.assertEqual("blocked", impact["execution"]["status"])
            self.assertEqual("blocked", gate["summary"]["status"])
            self.assertNotIn("skip_reason", impact)

    def test_stale_impact_without_targets_is_blocked(self):
        gate = assess_business_impact_gate({"items": [{"impact_id": "BI-OLD"}]}, [])
        self.assertEqual("blocked", gate["summary"]["status"])

    def test_development_analysis_with_no_impacts_cannot_pass(self):
        gate = assess_business_impact_gate({"items": []}, [self.target()])
        self.assertEqual("blocked", gate["summary"]["status"])

    def test_partial_failure_preserves_failure_even_when_some_code_completed(self):
        targets = [self.target(), self.target(target_id="failed", status="blocked", analysis={})]
        impact = translate_business_impacts({}, targets, {}, {})
        gate = assess_business_impact_gate(impact, targets)
        self.assertEqual("blocked", impact["status"])
        self.assertEqual("blocked", gate["summary"]["status"])
        self.assertFalse(any(i["target_id"] == "failed" for i in impact["items"]))

    def test_code_design_cannot_accept_existing_but_empty_evidence_files(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            internal = output / ".ai-internal"
            internal.mkdir()
            for name, value in {
                "decision_register.json": {"schema_version": "1.0.0", "items": []},
                "code_targets.json": {"targets": []},
                "business_impact.json": {"status": "completed", "items": []},
                "business_impact_gate.json": {"summary": {"status": "passed"}, "gates": []},
                "codegraph_query_plan.json": {}, "codegraph_impact.json": {}, "uat_merge_gate.json": {},
            }.items():
                (internal / name).write_text(json.dumps(value), encoding="utf-8")
            with patch.object(testpoint, "_legacy_require_inputs"):
                with self.assertRaises(RuntimeError):
                    testpoint._require_testpoint_inputs(output, {"files_changed": ["service.py"]})

    def test_skipped_checks_are_not_counted_as_passed_or_in_the_denominator(self):
        from workflow_metrics import collect_artifact_metrics
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            internal = output / ".ai-internal"
            internal.mkdir()
            (internal / "quality_gate_report.json").write_text(json.dumps({
                "summary": {"status": "blocked"},
                "gates": [{"status": "passed"}, {"status": "failed"}, {"status": "skipped"}]
            }), encoding="utf-8")
            metrics = collect_artifact_metrics(output)["quality_gates"]
            self.assertEqual(1, metrics["passed_count"])
            self.assertEqual(1, metrics["skipped_count"])
            self.assertEqual(2, metrics["applicable_count"])
            self.assertEqual(0.5, metrics["pass_rate"])
            (internal / "code_impact.json").write_text(json.dumps({
                "status": "skipped", "business_impact_status": "skipped"
            }), encoding="utf-8")
            from workflow_metrics import build_metrics, render_markdown
            report = render_markdown(build_metrics(output, {"events": []}, ""))
            self.assertIn("跳过 1", report)
            self.assertIn("未核验实现", report)
            self.assertNotIn("覆盖缺口：0 个文件", report)

    def test_no_targets_do_not_call_the_code_impact_translation_engine(self):
        with patch("business_impact_translator._translate", side_effect=AssertionError("should not run")):
            impact = translate_business_impacts({}, [], {}, {})
        self.assertEqual("skipped", impact["status"])

    def test_requirement_only_gate_needs_no_dummy_uat_or_graph_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            internal = output / ".ai-internal"
            internal.mkdir()
            data = {"decision_register.json": {"schema_version": "1.0.0", "items": []},
                    "code_targets.json": {"targets": [], "manifest_errors": []},
                    "code_impact.json": {"status": "skipped"},
                    "requirement_code_comparison.json": {"status": "skipped", "items": []},
                    "business_impact.json": {"status": "skipped", "analysis_mode": "not_applicable", "items": []},
                    "business_impact_gate.json": {"summary": {"status": "skipped"}, "analysis_mode": "not_applicable", "gates": []}}
            for name, value in data.items():
                (internal / name).write_text(json.dumps(value), encoding="utf-8")
            with patch.object(testpoint, "_legacy_require_inputs"):
                testpoint._require_testpoint_inputs(output, None)
                data["business_impact_gate.json"]["summary"]["status"] = "passed_with_warnings"
                (internal / "business_impact_gate.json").write_text(json.dumps(data["business_impact_gate.json"]), encoding="utf-8")
                with self.assertRaises(RuntimeError):
                    testpoint._require_testpoint_inputs(output, None)

if __name__ == "__main__":
    unittest.main()
