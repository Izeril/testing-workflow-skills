import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import impact_selector


class GitFreshnessTests(unittest.TestCase):
    def test_explicit_no_refresh_uses_cached_snapshot(self):
        shas = {
            "origin/feature": "a" * 40,
            "origin/main": "b" * 40,
        }
        with patch.object(impact_selector, "_git", return_value=("", "")) as git_call, patch.object(
            impact_selector, "_optional_commit", side_effect=lambda _repo, ref: shas.get(ref, "")
        ):
            report = impact_selector.inspect_git_freshness("repo", "feature", "main", refresh_remote=False)

        self.assertEqual("skipped", report["fetch_status"])
        self.assertEqual("cached_remote_unverified", report["freshness_status"])
        self.assertFalse(report["fetch_requested"])
        self.assertFalse(any(call.args[1][0] == "fetch" for call in git_call.call_args_list))

    def test_failed_default_fetch_reports_partial_ref_updates_without_prune(self):
        sha_values = iter(("a" * 40, "b" * 40, "c" * 40, "b" * 40))

        def fake_git(_repo, args, timeout=60):
            if args[0] == "fetch":
                self.assertNotIn("--prune", args)
                raise RuntimeError("network interrupted")
            return "", ""

        with patch.object(impact_selector, "_git", side_effect=fake_git) as git_call, patch.object(
            impact_selector, "_optional_commit", side_effect=lambda *_args: next(sha_values)
        ):
            report = impact_selector.inspect_git_freshness(
                "repo", "feature", "main"
            )

        fetch_args = next(call.args[1] for call in git_call.call_args_list if call.args[1][0] == "fetch")
        self.assertIn("--no-tags", fetch_args)
        self.assertTrue(report["partial_remote_update_detected"])
        self.assertIn("origin/feature", report["remote_ref_changes"])
        self.assertIn("未执行自动回滚", report["fetch_warning"])


if __name__ == "__main__":
    unittest.main()
