import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import workflow_analysis_legacy as analysis
import workflow_testpoint_facade as testpoint


class ConditionalComparisonTests(unittest.TestCase):
    def test_no_code_analysis_skips_comparator_and_preserves_review_input(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            with patch.object(analysis, "compare_structured", side_effect=AssertionError("called")):
                result, path = analysis.generate_requirement_code_comparison(
                    output, {"items": []}, None
                )
            self.assertEqual("skipped", result["status"])
            self.assertEqual([], result["items"])
            self.assertEqual("skipped", json.loads(path.read_text(encoding="utf-8"))["status"])
            self.assertIn("未执行需求代码比较", analysis._comparison_markdown(result))

    def test_completed_code_analysis_runs_comparator_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            code = {"files_changed": ["service.py"]}
            expected = {"schema_version": "1.1.0", "summary": {"总计": 1}, "items": [{"classification": "一致"}]}
            with patch.object(analysis, "compare_structured", return_value=expected) as compare:
                result, _ = analysis.generate_requirement_code_comparison(
                    output, {"items": []}, code, lambda value: {"apis": {"items": []}}
                )
            compare.assert_called_once()
            self.assertEqual("completed", result["status"])
            self.assertEqual(1, result["summary"]["总计"])

    def test_no_code_analysis_continues_through_review_and_testpoints(self):
        from workflow_analysis_facade import run_requirement_analysis
        from workflow_review import run_requirement_review
        from workflow_testpoint_facade import run_testpoint_generate

        with tempfile.TemporaryDirectory() as temp_dir:
            requirement = Path(temp_dir) / "修改密码准入优化"
            output = requirement / "测试分析"
            output.mkdir(parents=True)
            (requirement / "需求说明.md").write_text(
                "# 修改密码准入优化\n"
                "## 需求背景\n当前弱密码可重复使用，需要加强准入。\n"
                "## 解决方案\n修改密码接口增加历史密码校验。\n"
                "## 涉及接口\nPOST /api/password/change\n",
                encoding="utf-8",
            )
            project, code = run_requirement_analysis(
                requirement, output,
                repo_config={"path": "", "analysis_mode": "development", "refresh_remote_refs": False},
            )
            run_requirement_review(requirement, output, project)
            run_testpoint_generate(requirement, output, project, code)
            comparison = json.loads(
                (output / ".ai-internal" / "requirement_code_comparison.json").read_text(encoding="utf-8")
            )
            self.assertIsNone(code)
            self.assertEqual("skipped", comparison["status"])
            self.assertTrue((output / "详细测试点.md").exists())
            internal = output / ".ai-internal"
            impact = json.loads((internal / "business_impact.json").read_text(encoding="utf-8"))
            gate = json.loads((internal / "business_impact_gate.json").read_text(encoding="utf-8"))
            matrix = json.loads((internal / "coverage_matrix.json").read_text(encoding="utf-8"))
            quality = json.loads((internal / "quality_gate_report.json").read_text(encoding="utf-8"))
            self.assertEqual("skipped", impact["status"])
            self.assertEqual("not_applicable", impact["analysis_mode"])
            self.assertEqual("skipped", gate["summary"]["status"])
            self.assertFalse((output / "业务影响分析.md").exists())
            self.assertEqual("RQ → TP", matrix["traceability_chain"])
            self.assertIsNone(matrix["statistics"]["confirmed_business_impact_coverage_rate"])
            self.assertTrue(all(g["status"] == "skipped" for g in quality["gates"] if g["gate_id"] in {"G8", "G9"}))
            self.assertTrue(list(output.glob("*.xmind")))
            from cross_skill_adapter import load_contract_symbol

            manifest = load_contract_symbol("load_manifest")()
            validate = load_contract_symbol("validate_artifact_file")
            for name in ("business_impact", "coverage_matrix", "quality_gate_report", "testpoints"):
                with self.subTest(artifact=name):
                    validate(manifest, name, internal / f"{name}.json")

    def test_no_code_testpoint_gate_accepts_explicit_skip_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir)
            internal = output / ".ai-internal"
            internal.mkdir()
            (output / "需求分析.md").write_text("需求分析", encoding="utf-8")
            (output / "需求_评审报告.md").write_text("评审", encoding="utf-8")
            (internal / "需求_评审数据.json").write_text("{}", encoding="utf-8")
            payloads = {
                "requirement_facts.json": {"items": []},
                "analysis_quality_gate.json": {"summary": {"status": "passed"}},
                "requirement_code_comparison.json": {"status": "skipped", "items": []},
                "code_impact.json": {"status": "skipped"},
                "code_targets.json": {"targets": []},
                "analysis_context.json": {"code_analysis": None},
                "decision_register.json": {"schema_version": "1.0.0", "items": []},
                "business_impact.json": {"status": "skipped", "analysis_mode": "not_applicable", "items": []},
                "business_impact_gate.json": {"summary": {"status": "skipped"}, "analysis_mode": "not_applicable", "gates": []},
                "codegraph_query_plan.json": {},
                "codegraph_impact.json": {},
                "uat_merge_gate.json": {},
            }
            for name, payload in payloads.items():
                (internal / name).write_text(json.dumps(payload), encoding="utf-8")
            testpoint._require_testpoint_inputs(output, None)
            payloads["code_impact.json"] = {"status": "blocked"}
            (internal / "code_impact.json").write_text(json.dumps(payloads["code_impact.json"]), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "代码目标阻断"):
                testpoint._require_testpoint_inputs(output, None)


if __name__ == "__main__":
    unittest.main()
