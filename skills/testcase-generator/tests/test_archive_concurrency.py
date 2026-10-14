import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from knowledge_base_manager import KnowledgeBaseManager
from workflow_archive import run_archive


class ArchiveConcurrencyTests(unittest.TestCase):
    def test_concurrent_same_content_creates_one_manifest_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "requirement" / "测试分析"
            internal = output / ".ai-internal"
            internal.mkdir(parents=True)
            (output / "需求分析.md").write_text("# 并发归档", encoding="utf-8")
            (internal / "测试用例.json").write_text('{"testcases": []}', encoding="utf-8")
            kb = root / ".skill" / "knowledge-base"
            for name in ("requirements", "review-reports", "tech-solutions", "testcases", "patterns/精华库"):
                (kb / name).mkdir(parents=True, exist_ok=True)
            manager = KnowledgeBaseManager(root, SKILL_DIR)
            manager.kb_path = kb

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(
                    lambda _: run_archive(
                        output.parent, output, {"project_name": "并发归档"}, manager, {}
                    ),
                    range(2),
                ))

            self.assertEqual(
                ["already_archived", "archived"], sorted(item["status"] for item in results)
            )
            manifest = json.loads((kb / ".archive-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(1, len(manifest["records"]))
            self.assertEqual(1, len(list((kb / "requirements").glob("*.md"))))


if __name__ == "__main__":
    unittest.main()
