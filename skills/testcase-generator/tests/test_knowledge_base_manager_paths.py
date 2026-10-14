import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import knowledge_base_manager as kb_module
from knowledge_base_manager import KnowledgeBaseManager


class KnowledgeBaseManagerPathTests(unittest.TestCase):
    def test_installed_module_resolves_current_codex_skill(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = KnowledgeBaseManager(project_root=temp_dir)
        expected = Path(kb_module.__file__).resolve().parents[1]
        self.assertEqual(expected, manager.skill_dir)
        self.assertTrue((manager.skill_dir / "SKILL.md").is_file())
        self.assertEqual("testcase-generator", manager.skill_dir.name)

    def test_project_ancestor_codex_skill_is_used_as_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project with spaces & symbols"
            nested = root / "requirements" / "feature"
            skill_dir = root / ".codex" / "skills" / "testcase-generator"
            nested.mkdir(parents=True)
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("---\nname: testcase-generator\n---\n", encoding="utf-8")
            manager = KnowledgeBaseManager.__new__(KnowledgeBaseManager)
            manager.project_root = nested
            fake_module = root / "not-installed" / "scripts" / "knowledge_base_manager.py"
            with patch.object(kb_module, "__file__", str(fake_module)):
                resolved = manager._find_skill_dir()
        self.assertEqual(skill_dir.resolve(), resolved)


if __name__ == "__main__":
    unittest.main()
