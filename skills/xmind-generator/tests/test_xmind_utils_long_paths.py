import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

import xmind_utils


class XMindLongPathTests(unittest.TestCase):
    def test_windows_drive_path_uses_extended_length_prefix(self):
        with patch.object(xmind_utils.os, "name", "nt"):
            self.assertEqual(
                "\\\\?\\C:\\deep\\artifact.xmind",
                xmind_utils._filesystem_path("C:\\deep\\artifact.xmind"),
            )

    def test_windows_unc_path_uses_extended_length_prefix(self):
        with patch.object(xmind_utils.os, "name", "nt"):
            self.assertEqual(
                "\\\\?\\UNC\\server\\share\\artifact.xmind",
                xmind_utils._filesystem_path("\\\\server\\share\\artifact.xmind"),
            )

    def test_archive_is_built_in_short_temp_path_then_published(self):
        temp_dir = tempfile.mkdtemp()
        try:
            deep_dir = Path(temp_dir)
            for index in range(8):
                deep_dir /= f"very-long-requirement-directory-{index:02d}"
            output = deep_dir / "result.xmind"
            sheet = xmind_utils.make_sheet(xmind_utils.make_node("root"), "project")

            original_writer = xmind_utils._write_zip_payload
            build_paths = []

            def recording_writer(path, *args, **kwargs):
                build_paths.append(str(path))
                return original_writer(path, *args, **kwargs)

            with patch.object(xmind_utils, "_write_zip_payload", side_effect=recording_writer):
                result = xmind_utils.write_xmind_zip(output, [sheet])

            filesystem_output = xmind_utils._filesystem_path(output)
            self.assertEqual(str(output), result)
            self.assertTrue(os.path.exists(filesystem_output))
            self.assertLess(len(build_paths[0]), len(str(output)))
            with zipfile.ZipFile(filesystem_output, "r") as archive:
                self.assertIn("content.json", archive.namelist())
        finally:
            shutil.rmtree(xmind_utils._filesystem_path(temp_dir), ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
