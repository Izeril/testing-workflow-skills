import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from workflow_git_runner import GitCommandRejected, run_git, stable_patch_id


class GitRunnerContractTests(unittest.TestCase):
    def test_targeted_fetch_requires_explicit_permission(self):
        executor = Mock(return_value=subprocess.CompletedProcess([], 0, "", ""))
        with self.assertRaises(GitCommandRejected):
            run_git("repo", ["fetch", "origin"], executor=executor)
        result = run_git(
            "repo", ["fetch", "--no-tags", "origin", "+refs/heads/main:refs/remotes/origin/main"],
            allow_fetch=True, executor=executor,
        )
        self.assertEqual(0, result.returncode)
        self.assertEqual("fetch", result.command[3])

    def test_destructive_commands_and_shell_strings_are_rejected(self):
        with self.assertRaises(TypeError):
            run_git("repo", "diff main...feature")
        with self.assertRaises(GitCommandRejected):
            run_git("repo", ["checkout", "main"])

    def test_patch_id_uses_two_argument_list_processes(self):
        executor = Mock(side_effect=[
            subprocess.CompletedProcess([], 0, b"diff --git a/a b/a\n", b""),
            subprocess.CompletedProcess([], 0, "abc123 000000\n", ""),
        ])
        value = stable_patch_id("repo & space", ["show", "--binary", "abc"], executor=executor)
        self.assertEqual("abc123", value)
        first = executor.call_args_list[0]
        second = executor.call_args_list[1]
        self.assertEqual(["git", "-C", "repo & space", "show", "--binary", "abc"], first.args[0])
        self.assertEqual(["git", "-C", "repo & space", "patch-id", "--stable"], second.args[0])
        self.assertFalse(first.kwargs["shell"])
        self.assertFalse(second.kwargs["shell"])


if __name__ == "__main__":
    unittest.main()
