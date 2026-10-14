import base64
import importlib.util
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = SKILL_DIR / "doc_to_md.py"
SPEC = importlib.util.spec_from_file_location("doc_to_md_assets_under_test", SCRIPT_PATH)
DOC_TO_MD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DOC_TO_MD)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"test-png-payload"


def build_mhtml(html, attachments=()):
    boundary = "doc-to-md-assets"
    chunks = [
        "MIME-Version: 1.0\r\n",
        f"Content-Type: multipart/related; boundary={boundary}\r\n\r\n",
        f"--{boundary}\r\n",
        "Content-Type: text/html; charset=utf-8\r\n",
        "Content-Transfer-Encoding: 8bit\r\n\r\n",
        html,
        "\r\n",
    ]
    for attachment in attachments:
        chunks.extend([
            f"--{boundary}\r\n",
            f"Content-Type: {attachment.get('type', 'application/octet-stream')}\r\n",
        ])
        if attachment.get("cid"):
            chunks.append(f"Content-ID: <{attachment['cid']}>\r\n")
        if attachment.get("location"):
            chunks.append(f"Content-Location: {attachment['location']}\r\n")
        chunks.extend([
            "Content-Disposition: inline\r\n",
            "Content-Transfer-Encoding: base64\r\n\r\n",
            base64.b64encode(attachment["data"]).decode("ascii"),
            "\r\n",
        ])
    chunks.append(f"--{boundary}--\r\n")
    return "".join(chunks).encode("utf-8")


class MhtmlAssetTests(unittest.TestCase):
    def test_images_are_landed_and_referenced_with_relative_paths(self):
        html = (
            "<html><body><h1>图片需求</h1><p>中文说明</p>"
            '<img src="cid:diagram"><img src="0123456789abcdef"></body></html>'
        )
        payload = build_mhtml(html, [
            {"type": "image/png", "cid": "diagram", "data": PNG_BYTES},
            {
                "type": "application/octet-stream",
                "location": "file:///C:/0123456789abcdef",
                "data": PNG_BYTES + b"-second",
            },
        ])
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "sample.doc"
            source.write_bytes(payload)
            output = Path(DOC_TO_MD.convert_doc_to_md(source))
            markdown = output.read_text(encoding="utf-8")
            assets = sorted((Path(temp_dir) / "sample_assets").glob("*.png"))

            self.assertEqual(2, len(assets))
            self.assertTrue(all(path.read_bytes().startswith(b"\x89PNG") for path in assets))
            self.assertEqual(2, markdown.count("sample_assets/image-"))
            self.assertNotIn("cid:diagram", markdown)
            self.assertNotIn("file:///", markdown)

    def test_missing_embedded_image_blocks_all_outputs(self):
        payload = build_mhtml(
            '<html><body><h1>图片需求</h1><p>中文说明</p><img src="cid:missing"></body></html>'
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "missing.doc"
            source.write_bytes(payload)
            with self.assertRaises(DOC_TO_MD.AttachmentValidationError) as raised:
                DOC_TO_MD.convert_doc_to_md(source)
            self.assertIn("缺失", str(raised.exception))
            self.assertFalse(source.with_suffix(".md").exists())
            self.assertFalse((Path(temp_dir) / "missing_assets").exists())

    def test_duplicate_content_id_with_different_bytes_is_rejected(self):
        payload = build_mhtml(
            '<html><body><h1>图片需求</h1><p>中文说明</p><img src="cid:duplicate"></body></html>',
            [
                {"type": "image/png", "cid": "duplicate", "data": PNG_BYTES},
                {"type": "image/png", "cid": "duplicate", "data": PNG_BYTES + b"-different"},
            ],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "duplicate.doc"
            source.write_bytes(payload)
            with self.assertRaises(DOC_TO_MD.AttachmentValidationError) as raised:
                DOC_TO_MD.convert_doc_to_md(source)
            self.assertIn("重复图片标识", str(raised.exception))
            self.assertFalse(source.with_suffix(".md").exists())

    def test_invalid_mime_is_rejected_even_when_magic_looks_like_png(self):
        payload = build_mhtml(
            '<html><body><h1>图片需求</h1><p>中文说明</p><img src="cid:unsafe"></body></html>',
            [{"type": "text/plain", "cid": "unsafe", "data": PNG_BYTES}],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "unsafe.doc"
            source.write_bytes(payload)
            with self.assertRaises(DOC_TO_MD.AttachmentValidationError) as raised:
                DOC_TO_MD.convert_doc_to_md(source)
            self.assertIn("MIME", str(raised.exception))
            self.assertFalse(source.with_suffix(".md").exists())

    def test_asset_output_path_file_conflict_is_actionable(self):
        payload = build_mhtml(
            '<html><body><h1>图片需求</h1><p>中文说明</p><img src="cid:diagram"></body></html>',
            [{"type": "image/png", "cid": "diagram", "data": PNG_BYTES}],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "conflict.doc"
            source.write_bytes(payload)
            (Path(temp_dir) / "conflict_assets").write_text("occupied", encoding="utf-8")
            with self.assertRaises(DOC_TO_MD.OutputConflictError) as raised:
                DOC_TO_MD.convert_doc_to_md(source)
            self.assertIn("图片输出目录", str(raised.exception))
            self.assertFalse(source.with_suffix(".md").exists())


if __name__ == "__main__":
    unittest.main()
