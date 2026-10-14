import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

import main
from skill_errors import (
    AnalysisError, ConversionError, ExternalDependencyError, GateError, InputError, normalize_error,
)


class CliExitCodeTests(unittest.TestCase):
    def run_error(self, error):
        with tempfile.TemporaryDirectory() as temp_dir:
            main._CLI_OUTPUT_DIR = Path(temp_dir)
            with patch.object(main, "main", side_effect=error), \
                    patch.object(main, "record_active_stage_failure"), \
                    patch("builtins.print"):
                code = main.cli_entrypoint()
            payload = json.loads(
                (Path(temp_dir) / ".ai-internal" / "last_error.json").read_text(encoding="utf-8")
            )
        return code, payload

    def test_stable_error_classes_have_distinct_exit_codes(self):
        for error, expected, error_code in (
            (InputError("bad input"), 2, "invalid_input"),
            (GateError("blocked", stage="review"), 3, "gate_blocked"),
            (ExternalDependencyError("git unavailable"), 4, "external_dependency_failed"),
            (AnalysisError("analysis failed"), 5, "analysis_failed"),
            (ConversionError("conversion failed"), 6, "conversion_failed"),
            (RuntimeError("boom"), 1, "unexpected_error"),
        ):
            with self.subTest(error=type(error).__name__):
                code, payload = self.run_error(error)
                self.assertEqual(expected, code)
                self.assertEqual(error_code, payload["error_code"])

    def test_foreign_cross_skill_error_maps_to_external_dependency(self):
        class ForeignDependencyError(RuntimeError):
            error_code = "cross_skill_dependency_failed"
            context = {"skill": "doc-to-md"}

        error = normalize_error(ForeignDependencyError("missing sibling"))
        self.assertIsInstance(error, ExternalDependencyError)
        self.assertEqual(4, error.exit_code)
        self.assertEqual("doc-to-md", error.context["skill"])
        self.assertEqual("cross_skill_dependency_failed", error.context["original_error_code"])
    def test_keyboard_interrupt_returns_130(self):
        main._CLI_OUTPUT_DIR = None
        with patch.object(main, "main", side_effect=KeyboardInterrupt), patch("builtins.print"):
            self.assertEqual(130, main.cli_entrypoint())


if __name__ == "__main__":
    unittest.main()
