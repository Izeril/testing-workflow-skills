import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

from scripts.analyzer import business_extractor


class CodeAnalyzerCliContractTests(unittest.TestCase):
    def test_non_repository_is_invalid_input_exit_2(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            stdout, stderr = StringIO(), StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = business_extractor.main([temp_dir, "feature/test"])
        self.assertEqual(2, code)
        payload = json.loads(stderr.getvalue())
        self.assertEqual("invalid_repository", payload["error_code"])


if __name__ == "__main__":
    unittest.main()
