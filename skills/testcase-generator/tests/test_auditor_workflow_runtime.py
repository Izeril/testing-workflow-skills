import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SKILLS_ROOT = SKILL_DIR.parent


class AuditorWorkflowRuntimeTests(unittest.TestCase):
    def test_cold_main_analysis_and_comparison_work_without_old_skill_packages(self):
        for old_skill in ("code-analyzer", "req-code-comparator"):
            self.assertFalse((SKILLS_ROOT / old_skill).exists())
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            isolated_skills = root / "skills"
            for skill_name in ("testcase-generator", "requirement-code-auditor", "doc-to-md", "xmind-generator", "contracts"):
                shutil.copytree(
                    SKILLS_ROOT / skill_name, isolated_skills / skill_name,
                    ignore=shutil.ignore_patterns("__pycache__", "tests", ".reports"),
                )
            self.assertFalse((isolated_skills / "code-analyzer").exists())
            self.assertFalse((isolated_skills / "req-code-comparator").exists())
            program = r'''import importlib.util
import sys
from pathlib import Path

root = Path(sys.argv[1])
skills = root / "skills"
sys.path.insert(0, str(skills / "testcase-generator" / "scripts"))
import main
import cross_skill_adapter as adapter

before = list(sys.path)
logic = main.run_code_analysis(
    str(root / "repo"), "feature", "baseline", root / "out",
    selected_files=["mapper/OrderMapper.xml"],
)
assert logic is not None, "Auditor analysis failed through the main entrypoint"
assert logic["files_changed"] == ["mapper/OrderMapper.xml"], logic
assert logic["sql_analysis"], logic
assert "call_chains" in logic and "output_layer" in logic, logic
result = main.compare_structured(
    {"tables": {"t_order": {"content": "t_order", "source": "requirement.md"}}},
    main.build_code_evidence(logic),
)
items = [item for item in result["items"] if item["dimension"] == "tables"]
assert len(items) == 1 and items[0]["classification"] == "一致", result
assert "code-analyzer" not in adapter.PUBLIC_ENTRYPOINTS
assert "req-code-comparator" not in adapter.PUBLIC_ENTRYPOINTS
assert before == sys.path, "Cross-skill loading leaked an import path"
assert not any("code-analyzer" in value or "req-code-comparator" in value for value in sys.path)

spec = importlib.util.spec_from_file_location("cold_validator", skills / "contracts" / "validate_contracts.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)
manifest = validator.load_manifest()
assert set(manifest["skills"]) == {"testcase-generator", "requirement-code-auditor", "doc-to-md", "xmind-generator"}, manifest["skills"]
report = validator.validate_manifest(manifest)
assert report["status"] == "passed", report
assert report["skill_count"] == report["workflow_skill_count"] == 4, report
assert report["entrypoint_count"] == 8, report
assert report["archived_skill_count"] == 2, report
assert report["skipped_compatibility_skills"] == [], report
assert not (skills / "code-analyzer").exists()
assert not (skills / "req-code-comparator").exists()
print("COLD_RUNTIME_VERIFIED")
'''
            repo = root / "repo"
            repo.mkdir()
            def git(*args):
                subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, encoding="utf-8", timeout=15)
            git("init", "-b", "baseline")
            git("config", "user.name", "Workflow Regression")
            git("config", "user.email", "workflow-regression@example.invalid")
            mapper = repo / "mapper" / "OrderMapper.xml"
            mapper.parent.mkdir()
            mapper.write_text('<mapper namespace="OrderMapper"><select id="find">SELECT id FROM t_order</select></mapper>', encoding="utf-8")
            git("add", ".")
            git("commit", "-m", "baseline fixture")
            git("checkout", "-b", "feature")
            mapper.write_text('<mapper namespace="OrderMapper"><select id="find">SELECT id, total_amount FROM t_order WHERE id &gt; #{lastId}</select></mapper>', encoding="utf-8")
            git("add", ".")
            git("commit", "-m", "feature fixture")
            completed = subprocess.run(
                [sys.executable, "-I", "-c", program, str(root)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            self.assertIn("COLD_RUNTIME_VERIFIED", completed.stdout)


if __name__ == "__main__":
    unittest.main()
