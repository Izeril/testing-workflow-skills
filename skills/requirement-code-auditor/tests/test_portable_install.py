import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]


def run(command, cwd, env=None):
    return subprocess.run(command, cwd=cwd, env=env, shell=False, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)


def create_synthetic_repo(root: Path):
    repo = root / "synthetic-repo"
    repo.mkdir()
    for command in (
        ["git", "init", "-b", "master"],
        ["git", "config", "user.email", "ci@example.invalid"],
        ["git", "config", "user.name", "Portable CI"],
    ):
        result = run(command, repo)
        if result.returncode:
            raise AssertionError(result.stderr)
    (repo / "README.md").write_text("baseline\n", encoding="utf-8")
    self_commands = (["git", "add", "README.md"], ["git", "commit", "-m", "baseline"], ["git", "checkout", "-b", "feature/PRJ-2001"])
    for command in self_commands:
        result = run(command, repo)
        if result.returncode:
            raise AssertionError(result.stderr)
    source = repo / "src" / "main" / "java" / "OrderController.java"
    source.parent.mkdir(parents=True)
    source.write_text(
        '@RestController\nclass OrderController {\n  @PostMapping("/orders")\n  public void create() { if (true) { return; } }\n}\n',
        encoding="utf-8",
    )
    for command in (["git", "add", "src/main/java/OrderController.java"], ["git", "commit", "-m", "implement order endpoint"]):
        result = run(command, repo)
        if result.returncode:
            raise AssertionError(result.stderr)
    return repo


def create_requirement(root: Path):
    requirement = root / "requirement"
    requirement.mkdir()
    (requirement / "需求.md").write_text("# 需求描述\nPOST /orders 创建订单。", encoding="utf-8")
    (requirement / "PRJ-2001-Java提测.md").write_text("涉及开发分支：feature/PRJ-2001\n涉及服务：订单服务\n技术栈：Java", encoding="utf-8")
    return requirement


class PortableInstallTests(unittest.TestCase):
    def test_distribution_has_no_sibling_or_workstation_dependency(self):
        inspected = [SKILL_ROOT / "SKILL.md", SKILL_ROOT / "config.yaml", *sorted((SKILL_ROOT / "scripts").rglob("*.py")), *sorted((SKILL_ROOT / "references").glob("*"))]
        text = "\n".join(path.read_text(encoding="utf-8") for path in inspected if path.is_file())
        for forbidden in ("../code-analyzer", "../req-code-comparator", "D:\\用户故事", "C:\\Users\\kingdee", "config.local.yaml\n"):
            self.assertNotIn(forbidden, text)

    def test_clean_isolated_copy_runs_local_synthetic_repository(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            copied = root / "requirement-code-auditor"
            shutil.copytree(
                SKILL_ROOT,
                copied,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "config.local.yaml", ".runs"),
            )
            repo = create_synthetic_repo(root)
            requirement = create_requirement(root)
            clean_profile = root / "profile"
            clean_profile.mkdir()
            env = os.environ.copy()
            env.update({
                "USERPROFILE": str(clean_profile),
                "HOME": str(clean_profile),
                "PYTHONNOUSERSITE": "1",
                "PYTHONUTF8": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
            })
            command = [
                sys.executable,
                "-I",
                str(copied / "scripts" / "cli.py"),
                str(requirement),
                "--repo",
                str(repo),
                "--baseline",
                "master",
            ]
            result = run(command, root, env=env)
            self.assertEqual(0, result.returncode, msg=f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
            self.assertTrue((requirement / "测试分析" / ".ai-internal" / "requirement_code_audit.json").exists())
            self.assertFalse(any(copied.rglob("config.local.yaml")))


if __name__ == "__main__":
    unittest.main()
