import hashlib
import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SKILL_DIR.parents[2]
SPEC = importlib.util.spec_from_file_location("doc_to_md_real_golden", SKILL_DIR / "doc_to_md.py")
DOC_TO_MD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DOC_TO_MD)


class RealMhtmlGoldenTests(unittest.TestCase):
    def test_real_project_image_and_content_baseline(self):
        fixture_path = Path(__file__).parent / "fixtures" / "real_mhtml_golden.json"
        if not fixture_path.is_file():
            self.skipTest("真实 MHTML 基线清单不随仓库分发；在本机工作区验证")
        fixture = json.loads(
            (Path(__file__).parent / "fixtures" / "real_mhtml_golden.json").read_text(encoding="utf-8")
        )
        source = PROJECT_ROOT / Path(fixture["source_relative"])
        if not source.is_file():
            self.skipTest("真实项目样本未随技能分发；仅在项目工作区执行")
        self.assertEqual(fixture["source_size"], source.stat().st_size)
        self.assertEqual(fixture["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())

        with tempfile.TemporaryDirectory(prefix="doc-to-md-golden-") as temp_dir:
            output = Path(DOC_TO_MD.convert_doc_to_md(source, temp_dir))
            markdown = output.read_text(encoding="utf-8")
            assets = sorted((Path(temp_dir) / f"{source.stem}_assets").glob("*.png"))
            relative_refs = len(re.findall(r"!\[[^\]]*\]\([^)]+_assets/[^)]+\)", markdown))
            table_rows = sum(1 for line in markdown.splitlines() if line.count("|") >= 2)

        expected = fixture["expected"]
        self.assertEqual(expected["unique_png_assets"], len(assets))
        self.assertEqual(expected["relative_image_refs"], relative_refs)
        self.assertGreaterEqual(len(markdown), expected["minimum_markdown_chars"])
        self.assertGreaterEqual(table_rows, expected["minimum_table_rows"])
        for text in expected["must_contain"]:
            self.assertIn(text, markdown)


if __name__ == "__main__":
    unittest.main()
