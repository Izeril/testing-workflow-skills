import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import workflow_analysis


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        shell=False,
    )
    return result.stdout.strip()


class GitIntegrationTests(unittest.TestCase):
    def test_cached_development_snapshot_is_preanalysis_not_formal_uat(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            _git(repo, "init")
            _git(repo, "config", "user.email", "codex@example.invalid")
            _git(repo, "config", "user.name", "Codex Test")
            (repo / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
            (repo / "service.py").write_text("def apply():\n    return 'old'\n", encoding="utf-8")
            _git(repo, "add", "pyproject.toml", "service.py")
            _git(repo, "commit", "-m", "baseline")
            baseline_sha = _git(repo, "rev-parse", "HEAD")
            _git(repo, "branch", "-M", "master")
            _git(repo, "checkout", "-b", "feature")
            (repo / "service.py").write_text(
                "def apply(status):\n"
                "    # 新增准入条件，供真实 Git 差异验证。\n"
                "    if status != 'READY':\n"
                "        raise ValueError('invalid status')\n"
                "    return 'accepted'\n",
                encoding="utf-8",
            )
            _git(repo, "add", "service.py")
            _git(repo, "commit", "-m", "REQ-100 apply admission")
            feature_sha = _git(repo, "rev-parse", "HEAD")
            _git(repo, "update-ref", "refs/remotes/origin/master", baseline_sha)
            _git(repo, "update-ref", "refs/remotes/origin/feature", feature_sha)

            result = workflow_analysis.preflight_code_target(
                {
                    "target_id": "python-demo",
                    "repo_path": str(repo),
                    "language": "python",
                    "services": ["apply-service"],
                    "branch": "feature",
                    "baseline": "master",
                    "uat_branch": "uat_merge",
                    "analysis_mode": "development",
                    "refresh_remote_refs": False,
                    "status": "pending",
                }
            )

        self.assertEqual("verified_stale", result["status"])
        self.assertEqual("development", result["effective_analysis_mode"])
        self.assertEqual("cached_unverified", result["git_snapshot"]["snapshot_status"])
        self.assertEqual("indeterminate", result["uat_merge_gate"]["status"])
        self.assertEqual(["service.py"], [item["path"] for item in result["raw_changed_files"]])
        self.assertEqual(feature_sha, result["development_sha"])


if __name__ == "__main__":
    unittest.main()
