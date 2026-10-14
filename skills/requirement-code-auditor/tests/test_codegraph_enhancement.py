import json
import tempfile
import unittest
from pathlib import Path

from scripts.evidence_provider import detect_codegraph


class CodegraphEnhancementTests(unittest.TestCase):
    def test_sha_aligned_candidate_file_list_enables_enhanced_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            index = repo / ".codegraph"
            index.mkdir()
            (index / "metadata.json").write_text(json.dumps({"target_sha": "abc"}), encoding="utf-8")
            (index / "candidates.json").write_text(json.dumps({"candidate_files": ["src/OrderService.java"]}), encoding="utf-8")
            capability = detect_codegraph(str(repo), "abc", {"enabled": True})
        self.assertEqual("available", capability["codegraph_status"])
        self.assertEqual("static_with_codegraph", capability["provider"])
        self.assertEqual(["src/OrderService.java"], capability["candidate_files"])

    def test_valid_index_without_candidate_query_is_not_required(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            index = repo / ".codegraph"
            index.mkdir()
            (index / "metadata.json").write_text(json.dumps({"target_sha": "abc"}), encoding="utf-8")
            capability = detect_codegraph(str(repo), "abc", {"enabled": True})
        self.assertEqual("not_required", capability["codegraph_status"])
