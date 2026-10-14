import tempfile
import unittest
from pathlib import Path

from scripts.requirement_ingestion import (
    extract_branches,
    extract_latest_requirement,
    ingest_requirement_directory,
)


class RequirementIngestionTests(unittest.TestCase):
    def test_latest_requirement_is_trimmed_once(self):
        self.assertEqual("最新内容", extract_latest_requirement("最新内容\n=== 分割线，以上为最新需求 ===\n旧内容"))

    def test_branch_prefix_slash_and_underscore_are_preserved(self):
        self.assertEqual(["origin/feature/PRJ_1001.test"], extract_branches("涉及开发分支：origin/feature/PRJ_1001.test"))

    def test_multiple_branches_remain_visible_for_ambiguity_gate(self):
        self.assertEqual(["feature/A", "hotfix/B"], extract_branches("开发分支：feature/A、hotfix/B"))

    def test_inventory_separates_requirement_and_release_documents(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "需求.md").write_text("# 需求描述\n每页 20 条", encoding="utf-8")
            (root / "PRJ-1-提测.md").write_text("涉及开发分支：feature/A", encoding="utf-8")
            inventory, normalized, release_docs = ingest_requirement_directory(root)
        self.assertEqual(2, len(inventory))
        self.assertIn("每页 20 条", normalized)
        self.assertNotIn("feature/A", normalized)
        self.assertEqual(1, len(release_docs))
