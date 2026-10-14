import unittest

from scripts.contract_validator import validate_artifact, validate_artifacts


class ArtifactContractTests(unittest.TestCase):
    def test_comparison_schema_110_is_required(self):
        issues = validate_artifact("requirement_code_comparison", {"schema_version": "1.0.0", "summary": {}, "items": []})
        self.assertTrue(any("1.1.0" in item for item in issues))

    def test_recursive_non_json_type_is_rejected(self):
        report = validate_artifacts({"requirement_facts": {"schema_version": "1.0.0", "producer": "x", "items": [{"bad": object()}], "quality_summary": {}}})
        self.assertEqual("failed", report["status"])
