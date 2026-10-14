import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from scripts.audit_runner import run_requirement_code_audit
from scripts.evidence_provider import LocalRepositoryEvidenceProvider


def git(repo: Path, *args):
    result = subprocess.run(["git", *args], cwd=repo, shell=False, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def fixture(root: Path):
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "master")
    git(repo, "config", "user.email", "ci@example.invalid")
    git(repo, "config", "user.name", "Mode CI")
    (repo / "base.txt").write_text("base", encoding="utf-8")
    git(repo, "add", "base.txt")
    git(repo, "commit", "-m", "base")
    git(repo, "checkout", "-b", "feature/modes")
    source = repo / "src" / "ModeController.java"
    source.parent.mkdir()
    source.write_text('@PostMapping("/modes")\nvoid run() {}', encoding="utf-8")
    git(repo, "add", "src/ModeController.java")
    git(repo, "commit", "-m", "mode endpoint")
    requirement = root / "requirement"
    requirement.mkdir()
    (requirement / "需求.md").write_text("# 需求描述\nPOST /modes 执行模式。", encoding="utf-8")
    (requirement / "PRJ-MODE-Java提测.md").write_text("开发分支：feature/modes\n涉及服务：模式服务\n技术栈：Java", encoding="utf-8")
    return repo, requirement


class EndToEndModeTests(unittest.TestCase):
    def test_static_fallback_and_enhanced_modes_are_traceable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repo, requirement = fixture(root)
            target_sha = git(repo, "rev-parse", "feature/modes")
            modes = {
                "static": {"enabled": False},
                "fallback": {"enabled": True},
            }
            observed = {}
            for mode, config in modes.items():
                started = time.perf_counter()
                result = run_requirement_code_audit(
                    requirement,
                    repo_path=str(repo),
                    baseline="master",
                    output_dir=str(root / f"out-{mode}"),
                    evidence_provider=LocalRepositoryEvidenceProvider(codegraph_config=config),
                )
                observed[mode] = (result["analysis_capabilities"][0]["call_chain"]["codegraph_status"], time.perf_counter() - started)
            index = repo / ".codegraph"
            index.mkdir()
            (index / "metadata.json").write_text(json.dumps({"target_sha": target_sha}), encoding="utf-8")
            (index / "candidates.json").write_text(json.dumps({"candidate_files": ["src/ModeController.java"]}), encoding="utf-8")
            started = time.perf_counter()
            result = run_requirement_code_audit(
                requirement,
                repo_path=str(repo),
                baseline="master",
                output_dir=str(root / "out-enhanced"),
                evidence_provider=LocalRepositoryEvidenceProvider(codegraph_config={"enabled": True}),
            )
            observed["enhanced"] = (result["analysis_capabilities"][0]["call_chain"]["codegraph_status"], time.perf_counter() - started)

        self.assertEqual("not_configured", observed["static"][0])
        self.assertEqual("index_missing", observed["fallback"][0])
        self.assertEqual("available", observed["enhanced"][0])
        self.assertTrue(all(duration < 900 for _, duration in observed.values()))


if __name__ == "__main__":
    unittest.main()
