import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT_PATH = SKILL_DIR / "doc_to_md.py"
SPEC = importlib.util.spec_from_file_location("doc_to_md_cli_under_test", SCRIPT_PATH)
DOC_TO_MD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DOC_TO_MD)


class DocToMarkdownCliContractTests(unittest.TestCase):
    def run_cli(self, argv):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = DOC_TO_MD.main(argv)
        return code, json.loads(stderr.getvalue())

    def test_missing_input_is_exit_2_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing.doc"
            code, payload = self.run_cli([str(missing)])
        self.assertEqual(2, code)
        self.assertEqual("invalid_input", payload["error_code"])
        self.assertEqual(2, payload["exit_code"])

    def test_unrecognized_document_is_conversion_exit_6_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "unknown.doc"
            source.write_bytes(b"not a supported document")
            code, payload = self.run_cli([str(source)])
            self.assertFalse(source.with_suffix(".md").exists())
        self.assertEqual(6, code)
        self.assertEqual("conversion_failed", payload["error_code"])

    def test_unclassified_exception_is_exit_1_json(self):
        with patch.object(DOC_TO_MD, "convert_doc_to_md", side_effect=RuntimeError("boom")):
            code, payload = self.run_cli(["ignored.doc"])
        self.assertEqual(1, code)
        self.assertEqual("unexpected_error", payload["error_code"])
        self.assertEqual("RuntimeError", payload["context"]["original_type"])


if __name__ == "__main__":
    unittest.main()
