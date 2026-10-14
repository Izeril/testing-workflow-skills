import unittest

from scripts.comparator import compare_structured
from scripts.evidence_normalizer import build_code_evidence


class EvidenceNormalizerTests(unittest.TestCase):
    def test_partial_coverage_propagates_and_prevents_false_missing(self):
        evidence = build_code_evidence({
            "files_changed": ["A.java"],
            "api_endpoints": ["GET /implemented"],
            "technology_coverage": {"complete": False, "gaps": [{"path": "B.vue", "status": "unsupported"}]},
        })
        self.assertEqual("partial", evidence["apis"]["coverage_status"])
        result = compare_structured(
            {"apis": {"items": [{"stable_key": "GET /missing"}], "coverage_status": "complete", "comparison_policy": "identity"}},
            evidence,
        )
        self.assertEqual("证据不足", result["items"][0]["classification"])

    def test_sql_tables_keep_source_anchor(self):
        evidence = build_code_evidence({
            "files_changed": ["mapper.xml"],
            "sql_analysis": [{"file": "mapper.xml", "analysis": {"tables": {"sources": ["orders"], "targets": ["audit_log"]}}}],
            "technology_coverage": {"complete": True, "gaps": []},
        })
        names = {item["stable_key"] for item in evidence["tables"]["items"]}
        self.assertEqual({"orders", "audit_log"}, names)
        self.assertTrue(all(item["source"] == "mapper.xml" for item in evidence["tables"]["items"]))
