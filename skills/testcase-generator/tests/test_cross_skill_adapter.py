import inspect
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import cross_skill_adapter as adapter


class CrossSkillAdapterTests(unittest.TestCase):
    def test_declared_public_entrypoints_resolve_inside_expected_skill(self):
        targets = (
            ("requirement-code-auditor", "extract_business_logic", "workflow_api.py"),
            ("requirement-code-auditor", "compare_structured", "workflow_api.py"),
            ("doc-to-md", None, "doc_to_md.py"),
            ("xmind-generator", "gen_id", "xmind_utils.py"),
            ("xmind-generator", "make_node", "xmind_utils.py"),
            ("xmind-generator", "create_xmind_file", "xmind_utils.py"),
        )
        for skill_name, symbol, expected_file in targets:
            with self.subTest(skill=skill_name, symbol=symbol):
                entrypoint = adapter.load_public_symbol(skill_name, symbol)
                module = inspect.getmodule(entrypoint)
                self.assertIsNotNone(module)
                self.assertEqual(expected_file, Path(module.__file__).name)
                self.assertIn(skill_name, Path(module.__file__).parts)

    def test_loading_does_not_leave_sibling_skill_paths_in_sys_path(self):
        before = list(sys.path)
        adapter.load_public_symbol("requirement-code-auditor", "extract_business_logic")
        adapter.load_public_symbol("requirement-code-auditor", "compare_structured")
        adapter.load_public_symbol("doc-to-md")
        adapter.load_public_symbol("xmind-generator", "gen_id")
        self.assertEqual(before, sys.path)

    def test_concurrent_loading_returns_one_isolated_package(self):
        before = list(sys.path)
        with ThreadPoolExecutor(max_workers=8) as pool:
            symbols = list(pool.map(
                lambda _: adapter.load_public_symbol("requirement-code-auditor"),
                range(16),
            ))
        self.assertTrue(all(symbol is symbols[0] for symbol in symbols))
        module = inspect.getmodule(symbols[0])
        self.assertEqual([str(Path(module.__file__).parent)], list(module.__path__))
        self.assertNotEqual("scripts", module.__name__)
        self.assertEqual(before, sys.path)

    def test_wrappers_preserve_arguments_and_auditor_ownership(self):
        selected = ["service.py"]
        extractor = Mock(return_value={"file_changes": {"service.py": "M"}})
        with patch.object(adapter, "load_public_symbol", return_value=extractor) as load:
            result = adapter.extract_business_logic("repo", "feature", "main", selected_files=selected)
        load.assert_called_once_with("requirement-code-auditor", "extract_business_logic")
        extractor.assert_called_once_with("repo", "feature", "main", selected_files=selected)
        self.assertEqual({"service.py": "M"}, result["file_changes"])
        compare = Mock(return_value={"items": []})
        facts, evidence = {"tables": ["t_order"]}, {"tables": ["t_order"]}
        with patch.object(adapter, "load_public_symbol", return_value=compare) as load:
            adapter.compare_structured(facts, evidence, dimensions=["tables"])
        load.assert_called_once_with("requirement-code-auditor", "compare_structured")
        compare.assert_called_once_with(facts, evidence, dimensions=["tables"])

    def test_failed_relative_import_cleans_children_and_can_be_retried(self):
        name = "broken-auditor"
        module_name = "_codex_sibling_broken_auditor_workflow_api"
        key = (name, "scripts/workflow_api.py")
        before = list(sys.path)
        try:
            with tempfile.TemporaryDirectory() as temp:
                skills_root = Path(temp)
                scripts = skills_root / name / "scripts"
                scripts.mkdir(parents=True)
                (scripts / "helper.py").write_text("VALUE = 7\n", encoding="utf-8")
                api = scripts / "workflow_api.py"
                api.write_text('from .helper import VALUE\nraise RuntimeError("failed import")\n', encoding="utf-8")
                contract = {"module": "scripts/workflow_api.py", "symbol": "extract_business_logic", "package": True}
                with patch.object(adapter, "SKILLS_ROOT", skills_root), patch.dict(adapter.PUBLIC_ENTRYPOINTS, {name: contract}):
                    with self.assertRaisesRegex(adapter.CrossSkillDependencyError, "failed import"):
                        adapter.load_public_symbol(name)
                    self.assertNotIn(key, adapter._MODULE_CACHE)
                    self.assertFalse(any(item == module_name or item.startswith(module_name + ".") for item in sys.modules))
                    self.assertEqual(before, sys.path)
                    api.write_text('from .helper import VALUE\ndef extract_business_logic():\n    return VALUE\n', encoding="utf-8")
                    self.assertEqual(7, adapter.load_public_symbol(name)())
                    self.assertEqual(before, sys.path)
        finally:
            adapter._MODULE_CACHE.pop(key, None)
            for item in tuple(sys.modules):
                if item == module_name or item.startswith(module_name + "."):
                    sys.modules.pop(item, None)

    def test_undeclared_symbols_and_path_traversal_are_rejected(self):
        with self.assertRaises(adapter.CrossSkillDependencyError):
            adapter.load_public_symbol("requirement-code-auditor", "run_git_command")
        for old_skill in ("code-analyzer", "req-code-comparator"):
            with self.subTest(skill=old_skill), self.assertRaises(adapter.CrossSkillDependencyError):
                adapter.load_public_symbol(old_skill)
        with self.assertRaises(adapter.CrossSkillDependencyError):
            adapter.get_skill_file("xmind-generator", "../testcase-generator/config.yaml")
        with self.assertRaises(adapter.CrossSkillDependencyError):
            adapter.get_skill_file("requirement-code-auditor", "../code-analyzer/business_extractor.py")

    def test_missing_auditor_never_falls_back_to_archived_old_skills(self):
        with tempfile.TemporaryDirectory() as temp:
            codex_root = Path(temp) / ".codex"
            skills_root = codex_root / "skills"
            skills_root.mkdir(parents=True)
            archive_root = codex_root / "skills-archive"
            for old_skill, module_file in (
                ("code-analyzer", "business_extractor.py"),
                ("req-code-comparator", "comparator.py"),
            ):
                archived = archive_root / old_skill
                archived.mkdir(parents=True)
                (archived / module_file).write_text(
                    'raise AssertionError("Archived skill must not be loaded")\n',
                    encoding="utf-8",
                )
                self.assertTrue((archived / module_file).is_file())
            with patch.object(adapter, "SKILLS_ROOT", skills_root), patch.object(adapter, "_MODULE_CACHE", {}):
                for entrypoint, args in (
                    (adapter.extract_business_logic, ("repo", "feature", "main")),
                    (adapter.compare_structured, ({}, {})),
                ):
                    with self.subTest(entrypoint=entrypoint.__name__), self.assertRaisesRegex(
                        adapter.CrossSkillDependencyError,
                        "技能目录不存在或越界: requirement-code-auditor",
                    ):
                        entrypoint(*args)

    def test_testcase_generator_has_one_cross_skill_boundary(self):
        common = (SCRIPTS_DIR / "workflow_common.py").read_text(encoding="utf-8")
        analysis = (SCRIPTS_DIR / "workflow_analysis_legacy.py").read_text(encoding="utf-8")
        refine = (SCRIPTS_DIR / "workflow_refine.py").read_text(encoding="utf-8")
        testpoint = (SCRIPTS_DIR / "workflow_testpoint_legacy.py").read_text(encoding="utf-8")

        self.assertNotIn("setup_skill_paths", common)
        self.assertNotIn("from comparator import", analysis)
        self.assertNotIn("PROJECT_SKILLS_DIR / 'xmind-generator'", refine)
        self.assertNotIn("PROJECT_SKILLS_DIR / 'xmind-generator'", testpoint)
        self.assertIn("from cross_skill_adapter import", common)
        self.assertNotIn("code-analyzer", adapter.PUBLIC_ENTRYPOINTS)
        self.assertNotIn("req-code-comparator", adapter.PUBLIC_ENTRYPOINTS)


if __name__ == "__main__":
    unittest.main()
