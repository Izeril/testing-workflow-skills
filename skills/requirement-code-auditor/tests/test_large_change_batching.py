import unittest

from scripts.analysis_batching import batch_paths, merge_analysis_batches
from scripts.evidence_normalizer import build_code_evidence


class LargeChangeBatchingTests(unittest.TestCase):
    def test_more_than_one_thousand_files_keep_full_coverage_denominator(self):
        files = [f"src/File{index}.java" for index in range(1005)]
        batches, unanalysed = batch_paths(files, 50, max_batches=2)
        self.assertEqual(2, len(batches))
        self.assertEqual(905, len(unanalysed))
        analyzed = []
        for batch in batches:
            analyzed.append({
                "file_changes": {path: "M" for path in batch},
                "technology_coverage": {"files": [{"path": path, "status": "analyzed"} for path in batch], "gaps": [], "complete": True},
                "api_endpoints": [],
                "analysis_warnings": [],
            })
        merged = merge_analysis_batches(analyzed, files, unanalysed)
        self.assertEqual(1005, merged["technology_coverage"]["total_changed_files"])
        self.assertEqual(905, len(merged["technology_coverage"]["unanalysed_files"]))
        self.assertFalse(merged["technology_coverage"]["complete"])
        evidence = build_code_evidence(merged)
        self.assertEqual("partial", evidence["coverage_summary"]["status"])

    def test_unlimited_batches_do_not_drop_files(self):
        files = [f"F{index}.py" for index in range(121)]
        batches, unanalysed = batch_paths(files, 50)
        self.assertEqual([50, 50, 21], [len(batch) for batch in batches])
        self.assertEqual([], unanalysed)
