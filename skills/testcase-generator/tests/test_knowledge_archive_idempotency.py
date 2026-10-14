import json
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from knowledge_base_manager import KnowledgeBaseManager
from workflow_archive import run_archive


class KnowledgeArchiveIdempotencyTests(unittest.TestCase):
    def build_workspace(self, root: Path):
        output = root / "requirement" / "测试分析"
        internal = output / ".ai-internal"
        internal.mkdir(parents=True)
        (output / "需求分析.md").write_text("# 需求分析\n内容", encoding="utf-8")
        (output / "代码评审报告.md").write_text("# 代码评审\n内容", encoding="utf-8")
        (output / "样例_评审报告.md").write_text("# 需求评审\n内容", encoding="utf-8")
        (internal / "样例_测试点.json").write_text('{"test_points": []}', encoding="utf-8")
        (internal / "测试用例.json").write_text('{"testcases": []}', encoding="utf-8")

        kb = root / ".skill" / "knowledge-base"
        for name in (
            "requirements", "review-reports", "tech-solutions", "testcases",
            "patterns/精华库",
        ):
            (kb / name).mkdir(parents=True, exist_ok=True)
        manager = KnowledgeBaseManager(root, SKILL_DIR)
        manager.kb_path = kb
        return output, manager

    def test_same_content_is_archived_once_and_changed_content_gets_new_revision(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output, manager = self.build_workspace(root)
            info = {"project_name": "幂等归档样例"}

            first = run_archive(output.parent, output, info, manager, {})
            second = run_archive(output.parent, output, info, manager, {})

            self.assertEqual("archived", first["status"])
            self.assertEqual("already_archived", second["status"])
            manifest = json.loads((manager.kb_path / ".archive-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(1, len(manifest["records"]))
            self.assertEqual(1, len(list((manager.kb_path / "requirements").glob("*.md"))))
            evolution = (manager.kb_path / "EVOLUTION_LOG.md").read_text(encoding="utf-8")
            self.assertEqual(1, evolution.count(first["archive_id"]))

            (output / "需求分析.md").write_text("# 需求分析\n内容已更新", encoding="utf-8")
            third = run_archive(output.parent, output, info, manager, {})
            self.assertEqual("archived", third["status"])
            self.assertNotEqual(first["archive_id"], third["archive_id"])
            manifest = json.loads((manager.kb_path / ".archive-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(2, len(manifest["records"]))
            self.assertEqual(2, len(list((manager.kb_path / "requirements").glob("*.md"))))

    def test_unavailable_knowledge_base_emits_explicit_incomplete_receipt(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "测试分析"
            output.mkdir()
            result = run_archive(output.parent, output, {}, None, {})
            self.assertEqual("unavailable", result["status"])
            self.assertFalse(result["completed"])
            receipt = json.loads(
                (output / ".ai-internal" / "archive_receipt.json").read_text(encoding="utf-8")
            )
            self.assertEqual("unavailable", receipt["status"])


if __name__ == "__main__":
    unittest.main()
