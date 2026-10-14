import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

import workflow_analysis
import main
import cross_skill_adapter as adapter
from cross_skill_adapter import CrossSkillDependencyError
from skill_errors import AnalysisError, ExternalDependencyError


class AnalysisErrorContractTests(unittest.TestCase):
    def test_unknown_stage_failure_becomes_analysis_exit_5(self):
        with patch.object(
            workflow_analysis,
            "_canonical_run_requirement_analysis",
            side_effect=RuntimeError("parser failed"),
        ):
            with self.assertRaises(AnalysisError) as raised:
                workflow_analysis.run_requirement_analysis("req", "out")
        self.assertEqual(5, raised.exception.exit_code)
        self.assertEqual("analysis", raised.exception.stage)
        self.assertEqual("RuntimeError", raised.exception.context["original_type"])

    def test_cross_skill_failure_preserves_external_dependency_exit_4(self):
        with patch.object(
            workflow_analysis,
            "_canonical_run_requirement_analysis",
            side_effect=CrossSkillDependencyError("requirement-code-auditor missing"),
        ):
            with self.assertRaises(ExternalDependencyError) as raised:
                workflow_analysis.run_requirement_analysis("req", "out")
        self.assertEqual(4, raised.exception.exit_code)
        self.assertEqual("cross_skill_dependency_failed", raised.exception.context["original_error_code"])


    def test_real_missing_auditor_reaches_cli_exit_4_and_error_artifact(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "out"
            def run_analysis():
                return main.run_code_analysis(
                    "repo", "feature", "main", output,
                    selected_files=["service.py"],
                    branch_sha="a" * 40, baseline_sha="b" * 40,
                )
            with patch.object(adapter, "SKILLS_ROOT", Path(temp) / "missing-skills"), patch.dict(
                adapter._MODULE_CACHE, {}, clear=True,
            ), patch.object(main, "main", side_effect=run_analysis), patch.object(
                main, "_CLI_OUTPUT_DIR", output,
            ), patch.object(main, "record_active_stage_failure") as record:
                self.assertEqual(4, main.cli_entrypoint())
            payload = json.loads((output / ".ai-internal" / "last_error.json").read_text(encoding="utf-8"))
            self.assertEqual("external_dependency_failed", payload["error_code"])
            self.assertEqual(4, payload["exit_code"])
            self.assertEqual("cross_skill_dependency_failed", payload["context"]["original_error_code"])
            self.assertIn("requirement-code-auditor", payload["message"])
            self.assertEqual(4, record.call_args.args[0].exit_code)
            self.assertFalse(list((output / ".ai-internal").glob(".code_analysis_cache_*.json")))

    def test_ordinary_analyzer_exception_keeps_failed_analysis_behavior(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(
            main, "extract_business_logic", side_effect=ValueError("unsupported source"),
        ):
            result = main.run_code_analysis(
                "repo", "feature", "main", Path(temp),
                selected_files=["service.py"],
                branch_sha="a" * 40, baseline_sha="b" * 40,
            )
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
