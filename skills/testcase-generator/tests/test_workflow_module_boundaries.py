import ast
import importlib
import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


class WorkflowModuleBoundaryTests(unittest.TestCase):
    def test_main_remains_a_thin_orchestrator(self):
        source = (SCRIPTS_DIR / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertLess(len(source.splitlines()), 500)
        self.assertEqual(
            {"load_config", "run_code_analysis", "run_auto_mode", "main", "_write_cli_error", "cli_entrypoint"},
            functions,
        )

    def test_stage_entrypoints_are_owned_by_stage_modules(self):
        ownership = {
            "workflow_analysis": "run_requirement_analysis",
            "workflow_review": "run_requirement_review",
            "workflow_testpoint": "run_testpoint_generate",
            "workflow_refine": "run_testcase_refine",
            "workflow_archive": "run_archive",
        }
        for module_name, function_name in ownership.items():
            module = importlib.import_module(module_name)
            function = getattr(module, function_name)
            self.assertEqual(module_name, function.__module__)

    def test_removed_legacy_templates_are_not_part_of_runtime(self):
        self.assertFalse((SCRIPTS_DIR / "v1_legacy_templates.py").exists())
        common_source = (SCRIPTS_DIR / "workflow_common.py").read_text(encoding="utf-8")
        self.assertNotIn("v1_legacy_templates", common_source)


if __name__ == "__main__":
    unittest.main()
