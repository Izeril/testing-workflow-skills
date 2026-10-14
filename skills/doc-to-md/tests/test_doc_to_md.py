import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = SKILL_DIR / "doc_to_md.py"
SPEC = importlib.util.spec_from_file_location("doc_to_md_under_test", SCRIPT_PATH)
DOC_TO_MD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DOC_TO_MD)


class DocToMarkdownTest(unittest.TestCase):
    def test_magic_bytes_detect_ole2_before_parsing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "legacy.doc"
            source.write_bytes(DOC_TO_MD.OLE2_MAGIC + b"\x00" * 32)
            self.assertEqual("ole2", DOC_TO_MD.detect_doc_format(source))

    def test_ole2_without_converter_fails_before_output(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "legacy.doc"
            source.write_bytes(DOC_TO_MD.OLE2_MAGIC + b"\x00" * 64)
            with patch.object(DOC_TO_MD, "find_libreoffice", return_value=None):
                with self.assertRaises(DOC_TO_MD.UnsupportedDocFormatError) as raised:
                    DOC_TO_MD.convert_doc_to_md(source)
            message = str(raised.exception)
            self.assertIn("LibreOffice", message)
            self.assertIn("Microsoft Word", message)
            self.assertIn("未生成 Markdown", message)
            self.assertFalse(source.with_suffix(".md").exists())

    def test_mhtml_converts_to_utf8_markdown(self):
        payload = (
            "MIME-Version: 1.0\r\n"
            "Content-Type: multipart/related; boundary=demo\r\n\r\n"
            "--demo\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            "Content-Transfer-Encoding: 8bit\r\n\r\n"
            "<html><body><h1>需求标题</h1><p>中文内容</p></body></html>\r\n"
            "--demo--\r\n"
        ).encode("utf-8")
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "mhtml.doc"
            source.write_bytes(payload)
            output = Path(DOC_TO_MD.convert_doc_to_md(source))
            self.assertTrue(output.is_file())
            content = output.read_text(encoding="utf-8")
            self.assertIn("# 需求标题", content)
            self.assertIn("中文内容", content)

    def test_cli_reports_actionable_ole2_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "legacy.doc"
            source.write_bytes(DOC_TO_MD.OLE2_MAGIC)
            stderr = io.StringIO()
            with patch.object(DOC_TO_MD, "find_libreoffice", return_value=None):
                with contextlib.redirect_stderr(stderr):
                    exit_code = DOC_TO_MD.main([str(source)])
            self.assertEqual(4, exit_code)
            message = stderr.getvalue()
            self.assertIn("soffice", message)
            self.assertIn("Microsoft Word", message)
            self.assertIn("未生成 Markdown", message)

    def test_quality_gate_rejects_garbled_or_empty_markdown(self):
        with self.assertRaises(DOC_TO_MD.UnsupportedDocFormatError):
            DOC_TO_MD.validate_markdown_content("��")
        with self.assertRaises(DOC_TO_MD.UnsupportedDocFormatError):
            DOC_TO_MD.validate_markdown_content(
                "# Title\nplain text without a table",
                "<html><body><table><tr><td>中文</td></tr></table></body></html>",
            )

    def test_quality_gate_accepts_chinese_and_table_structure(self):
        report = DOC_TO_MD.validate_markdown_content(
            "# 需求\n\n| 字段 | 说明 |\n|---|---|\n| code | 状态码 |",
            "<html><body><table><tr><td>字段</td></tr></table></body></html>",
        )
        self.assertEqual("passed", report["status"])
        self.assertTrue(report["markdown_has_chinese"])
        self.assertGreaterEqual(report["markdown_table_rows"], 2)


if __name__ == "__main__":
    unittest.main()
