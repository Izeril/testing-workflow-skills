import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

from scripts.git_runner import GitCommandRejected, run_git


class UnifiedGitRunnerContractTests(unittest.TestCase):
    def test_special_repo_path_is_one_argument_and_shell_is_disabled(self):
        executor = Mock(return_value=subprocess.CompletedProcess([], 0, "ok", ""))
        result = run_git(r"C:\work trees\tax & monitor", ["status", "--short"], executor=executor)
        self.assertEqual(
            ["git", "-C", r"C:\work trees\tax & monitor", "status", "--short"],
            executor.call_args.args[0],
        )
        self.assertFalse(executor.call_args.kwargs["shell"])
        self.assertEqual("ok", result.stdout)

    def test_shell_strings_and_mutating_commands_are_rejected(self):
        with self.assertRaises(TypeError):
            run_git("repo", "status --short")
        with self.assertRaises(GitCommandRejected):
            run_git("repo", ["reset", "--hard"])
        with self.assertRaises(GitCommandRejected):
            run_git("repo", ["fetch", "origin"])

    def test_timeout_has_stable_result(self):
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
        result = run_git("repo", ["status"], timeout=7, executor=timeout)
        self.assertEqual(-1, result.returncode)
        self.assertTrue(result.timed_out)
        self.assertIn("7s", result.stderr)


if __name__ == "__main__":
    unittest.main()
