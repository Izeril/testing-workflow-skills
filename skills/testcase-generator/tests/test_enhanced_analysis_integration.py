import json
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


class EnhancedAnalysisIntegrationTests(unittest.TestCase):
    def test_development_preanalysis_writes_complete_governance_chain(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            requirement = root / "申报准入优化"
            output = requirement / "测试分析"
            repo.mkdir()
            requirement.mkdir()
            output.mkdir()

            _git(repo, "init")
            _git(repo, "config", "user.email", "codex@example.invalid")
            _git(repo, "config", "user.name", "Codex Test")
            (repo / "pyproject.toml").write_text("[project]\nname='apply-service'\n", encoding="utf-8")
            (repo / "apply_service.py").write_text(
                "def submit(status):\n    return status\n", encoding="utf-8"
            )
            _git(repo, "add", "pyproject.toml", "apply_service.py")
            _git(repo, "commit", "-m", "baseline")
            baseline_sha = _git(repo, "rev-parse", "HEAD")
            _git(repo, "branch", "-M", "master")
            _git(repo, "checkout", "-b", "feature")
            (repo / "apply_service.py").write_text(
                "def submit(status):\n"
                "    # 业务准入：只有待申报状态可以提交。\n"
                "    if status != 'READY':\n"
                "        raise ValueError('invalid status')\n"
                "    return 'PROCESSING'\n",
                encoding="utf-8",
            )
            _git(repo, "add", "apply_service.py")
            _git(repo, "commit", "-m", "REQ-200 submit admission")
            feature_sha = _git(repo, "rev-parse", "HEAD")
            _git(repo, "update-ref", "refs/remotes/origin/master", baseline_sha)
            _git(repo, "update-ref", "refs/remotes/origin/feature", feature_sha)

            (requirement / "需求说明.md").write_text(
                "# 申报准入优化\n"
                "## 需求背景\n企业在错误状态下仍可提交申报。\n"
                "## 解决方案\n仅待申报状态允许提交，其他状态返回明确错误。\n"
                "## 涉及范围\n涉及服务：apply-service\n"
                "业务场景：提交申报\n"
                "用户：企业用户\n",
                encoding="utf-8",
            )

            project_info, code_analysis = workflow_analysis.run_requirement_analysis(
                requirement,
                output,
                str(repo),
                "feature",
                "master",
                repo_config={
                    "path": str(repo),
                    "legacy_workflow": True,
                    "baseline": "master",
                    "uat_branch": "uat_merge",
                    "analysis_mode": "development",
                    "refresh_remote_refs": False,
                    "require_codegraph_for_uat": True,
                },
                force_legacy=True,
            )

            internal = output / ".ai-internal"
            required = (
                "code_analysis_admission.json",
                "git_snapshot.json",
                "code_change_scope.json",
                "uat_merge_gate.json",
                "codegraph_query_plan.json",
                "codegraph_impact.json",
                "business_impact.json",
                "business_impact_gate.json",
            )
            missing = [name for name in required if not (internal / name).exists()]
            impact = json.loads((internal / "business_impact.json").read_text(encoding="utf-8"))
            gate = json.loads((internal / "business_impact_gate.json").read_text(encoding="utf-8"))
            comparison = json.loads((internal / "requirement_code_comparison.json").read_text(encoding="utf-8"))

        self.assertFalse(missing)
        self.assertEqual("completed", comparison["status"])
        self.assertTrue(code_analysis["files_changed"])
        self.assertEqual("申报准入优化", project_info["project_name"])
        self.assertEqual("development_preanalysis", impact["analysis_mode"])
        self.assertTrue(impact["items"])
        self.assertEqual("passed_with_warnings", gate["summary"]["status"])


if __name__ == "__main__":
    unittest.main()
