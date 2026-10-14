import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

from scripts.analyzer import business_extractor
import scripts.analyzer as code_analyzer


class UnifiedEntrypointTests(unittest.TestCase):
    def test_compatibility_class_delegates_to_canonical_engine(self):
        canonical_result = {
            "file_changes": {"src/OrderService.java": "M"},
            "sql_analysis": [{"statement": "select 1"}],
            "api_endpoints": ["/orders"],
            "analysis_warnings": ["review fallback evidence"],
            "syntax_analysis": [{"engine": "tree-sitter-java"}],
        }
        with patch.object(code_analyzer, "extract_business_logic", return_value=canonical_result) as extractor:
            result = code_analyzer.CodeAnalyzer("repo with spaces").analyze_branch(
                "feature/order", "main", ["src/OrderService.java"]
            )

        extractor.assert_called_once_with(
            "repo with spaces",
            "feature/order",
            "main",
            selected_files=["src/OrderService.java"],
        )
        self.assertEqual("tree-sitter-java", result["syntax_analysis"][0]["engine"])
        self.assertEqual([{"status": "M", "path": "src/OrderService.java"}], result["files"])
        self.assertEqual(["src/OrderService.java"], result["services"])

    def test_canonical_git_runner_never_uses_a_shell(self):
        completed = subprocess.CompletedProcess([], 0, " M src/app.py\n", "")
        with patch.object(business_extractor.subprocess, "run", return_value=completed) as run:
            output = business_extractor.run_git_command(["status", "--short"], "repo with spaces & symbols")

        self.assertEqual("M src/app.py", output)
        args, kwargs = run.call_args
        self.assertEqual(["git", "-C", "repo with spaces & symbols", "status", "--short"], args[0])
        self.assertFalse(kwargs["shell"])

    def test_canonical_git_runner_rejects_shell_command_strings(self):
        with self.assertRaises(TypeError):
            business_extractor.run_git_command("status --short")


if __name__ == "__main__":
    unittest.main()
