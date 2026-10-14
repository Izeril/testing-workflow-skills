import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

import workflow_common
from skill_errors import ConversionError, ExternalDependencyError


class ConversionErrorPropagationTests(unittest.TestCase):
    def make_requirement(self, temp_dir):
        source = Path(temp_dir) / "legacy.doc"
        source.write_bytes(b"MIME-Version: 1.0\r\n")
        return source

    def test_document_content_failure_becomes_conversion_exit_6(self):
        class ForeignConversionError(RuntimeError):
            error_code = "conversion_failed"

        with tempfile.TemporaryDirectory() as temp_dir:
            source = self.make_requirement(temp_dir)
            with patch.object(
                workflow_common,
                "convert_doc_to_md",
                side_effect=ForeignConversionError("bad content"),
            ):
                with self.assertRaises(ConversionError) as raised:
                    workflow_common.scan_and_convert_docs(temp_dir)
        self.assertEqual(6, raised.exception.exit_code)
        self.assertEqual("check", raised.exception.stage)
        self.assertEqual(str(source), raised.exception.context["failures"][0]["path"])

    def test_missing_converter_becomes_external_dependency_exit_4(self):
        class ForeignDependencyError(RuntimeError):
            error_code = "external_dependency_failed"

        with tempfile.TemporaryDirectory() as temp_dir:
            self.make_requirement(temp_dir)
            with patch.object(
                workflow_common,
                "convert_doc_to_md",
                side_effect=ForeignDependencyError("LibreOffice missing"),
            ):
                with self.assertRaises(ExternalDependencyError) as raised:
                    workflow_common.scan_and_convert_docs(temp_dir)
        self.assertEqual(4, raised.exception.exit_code)
        self.assertEqual("external_dependency_failed", raised.exception.context["failures"][0]["error_code"])


if __name__ == "__main__":
    unittest.main()
