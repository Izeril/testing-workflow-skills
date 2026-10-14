import subprocess
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

import scripts.analyzer as code_analyzer
from scripts.analyzer import code_context_analyzer


class GitCommandSafetyTests(unittest.TestCase):
    def test_code_analyzer_passes_repo_path_as_one_argument(self):
        repo = r"C:\work trees\tax & monitor"
        completed = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
        with patch.object(code_analyzer.subprocess, "run", return_value=completed) as run:
            status, output = code_analyzer.CodeAnalyzer(repo)._run_git(["status", "--short"])
        command = run.call_args.args[0]
        self.assertEqual(["git", "-C", repo, "status", "--short"], command)
        self.assertIs(run.call_args.kwargs["shell"], False)
        self.assertEqual((0, "ok"), (status, output))

    def test_string_shell_command_is_rejected(self):
        with self.assertRaises(TypeError):
            code_analyzer.CodeAnalyzer("repo")._run_git("status --short")
        with self.assertRaises(TypeError):
            code_context_analyzer._run_git("repo", "status --short", 10)

    def test_context_analyzer_preserves_special_paths_and_branch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo with spaces & symbols"
            source = repo / "src" / "Service.java"
            source.parent.mkdir(parents=True)
            source.write_text("class Service {}", encoding="utf-8")
            with patch.object(
                code_context_analyzer,
                "_run_git",
                return_value=SimpleNamespace(stdout=""),
            ) as run:
                code_context_analyzer.analyze_java_file_context(
                    str(source), str(repo), "feature/a&b", "origin/main"
                )
        self.assertEqual(
            ["diff", "origin/main...feature/a&b", "--", "src/Service.java"],
            run.call_args.args[1],
        )

    def test_context_git_runner_uses_shell_false(self):
        repo = r"C:\work trees\tax & monitor"
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch.object(code_context_analyzer.subprocess, "run", return_value=completed) as run:
            code_context_analyzer._run_git(repo, ["diff", "main...feature/a&b", "--"], 10)
        self.assertEqual(repo, run.call_args.args[0][2])
        self.assertIs(run.call_args.kwargs["shell"], False)

    def test_file_outside_repository_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            outside = root / "outside.java"
            repo.mkdir()
            outside.write_text("class Outside {}", encoding="utf-8")
            with self.assertRaises(ValueError):
                code_context_analyzer._relative_repo_path(str(outside), str(repo))


    @unittest.skipUnless(shutil.which("git"), "git executable is required")
    def test_real_git_repository_with_spaces_and_ampersand(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "repo with spaces & symbols"
            subprocess.run(
                ["git", "init", str(repo)],
                shell=False,
                check=True,
                capture_output=True,
                text=True,
            )
            status, _ = code_analyzer.CodeAnalyzer(str(repo))._run_git(["status", "--short"])
            context_result = code_context_analyzer._run_git(
                str(repo), ["status", "--short"], 10
            )
        self.assertEqual(0, status)
        self.assertEqual(0, context_result.returncode)


if __name__ == "__main__":
    unittest.main()
