import json
import importlib
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "golden"
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from source_inventory import build_source_inventory
from cross_skill_adapter import load_public_symbol

# Exercise the same auditor parser loaded through the production boundary.
_auditor_package = load_public_symbol("requirement-code-auditor").__module__
analyze_sql_content = importlib.import_module(
    _auditor_package + ".analyzer.sql_analyzer"
).analyze_sql_content
import workflow_analysis_legacy as workflow


class GoldenSemanticRegressionTests(unittest.TestCase):
    def fixtures(self):
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(FIXTURE_DIR.glob("*.json"))
        ]

    def test_all_golden_samples_match_semantic_expectations(self):
        fixtures = self.fixtures()
        self.assertGreaterEqual(len(fixtures), 3)
        self.assertGreaterEqual(len({item["category"] for item in fixtures}), 3)

        for fixture in fixtures:
            with self.subTest(sample=fixture["id"]):
                code_evidence = fixture.get("code_evidence", {})
                if fixture.get("mapper_sql"):
                    code_evidence = workflow.build_code_evidence({
                        "sql_analysis": [{
                            "file": "mapper/GoldenMapper.xml",
                            "analysis": analyze_sql_content(fixture["mapper_sql"]),
                        }],
                        "technology_coverage": {"summary": {"overall_status": "analyzed"}},
                    })

                result = workflow.compare_structured(
                    fixture["requirement_facts"], code_evidence
                )
                self.assertEqual(fixture["expected"]["summary"], result["summary"])

                warning_code = fixture["expected"].get("warning_code")
                if warning_code:
                    self.assertIn(warning_code, {item["code"] for item in result["warnings"]})

                markdown = fixture.get("requirement_markdown")
                if markdown:
                    with tempfile.TemporaryDirectory() as temp_dir:
                        root = Path(temp_dir)
                        normalized = root / "normalized_requirement.md"
                        normalized.write_text(markdown, encoding="utf-8")
                        inventory = build_source_inventory(markdown, root, normalized)
                    expected = fixture["expected"]["source_inventory"]
                    self.assertEqual(
                        expected["linked_missing_count"],
                        inventory["summary"]["linked_missing_count"],
                    )
                    self.assertEqual(
                        expected["decision_required_count"],
                        inventory["summary"]["decision_required_count"],
                    )


if __name__ == "__main__":
    unittest.main()
