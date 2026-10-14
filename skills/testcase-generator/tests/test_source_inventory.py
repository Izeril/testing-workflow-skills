import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from source_inventory import build_source_inventory


class SourceInventoryTests(unittest.TestCase):
    def test_local_remote_missing_and_image_links_are_classified(self):
        with tempfile.TemporaryDirectory() as temp:
            requirement_dir = Path(temp)
            (requirement_dir / "接口说明.pdf").write_bytes(b"pdf")
            content = """
接口字段口径见[接口说明](接口说明.pdf)。
计费规则见[远程附件](https://example.invalid/billing.docx)。
页面效果见![截图](images/missing.png)。
问题直达：<https://example.invalid/alert/123>。
"""
            inventory = build_source_inventory(
                content,
                requirement_dir,
                requirement_dir / "测试分析" / ".ai-internal" / "normalized_requirement.md",
            )

        attachment_by_name = {item["name"]: item for item in inventory["attachments"]}
        self.assertEqual("local_available", attachment_by_name["接口说明.pdf"]["availability"])
        self.assertEqual("linked_missing", attachment_by_name["billing.docx"]["availability"])
        self.assertTrue(attachment_by_name["billing.docx"]["decision_required"])
        self.assertEqual("linked_missing", inventory["images"][0]["availability"])
        self.assertTrue(inventory["images"][0]["decision_required"])
        alert = next(item for item in inventory["attachments"] if "alert/123" in item["url"])
        self.assertFalse(alert["decision_required"])
        self.assertEqual(3, inventory["summary"]["linked_missing_count"])


if __name__ == "__main__":
    unittest.main()
