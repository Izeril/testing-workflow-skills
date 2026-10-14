import json
import tempfile
import unittest
from pathlib import Path

from scripts.evidence_provider import detect_codegraph


class CodegraphDegradationTests(unittest.TestCase):
    def test_not_configured_is_static_not_failure(self):
        self.assertEqual("not_configured", detect_codegraph(".", "a", {"enabled": False})["codegraph_status"])

    def test_missing_and_stale_indexes_fall_back(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing = detect_codegraph(str(root), "a", {"enabled": True})
            self.assertEqual("index_missing", missing["codegraph_status"])
            index = root / ".codegraph"
            index.mkdir()
            (index / "metadata.json").write_text(json.dumps({"target_sha": "old"}), encoding="utf-8")
            stale = detect_codegraph(str(root), "new", {"enabled": True})
            self.assertEqual("index_stale", stale["codegraph_status"])
            self.assertTrue(stale["degraded"])
