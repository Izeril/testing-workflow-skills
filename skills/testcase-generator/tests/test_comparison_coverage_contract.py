import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import workflow_analysis


class ComparisonCoverageContractTests(unittest.TestCase):
    def test_code_evidence_carries_partial_coverage_into_comparator(self):
        code_analysis = {
            "sql_analysis": [
                {
                    "file": "OrderMapper.xml",
                    "analysis": {
                        "tables": {"sources": ["t_order"], "targets": []},
                    },
                }
            ],
            "technology_coverage": {
                "applicable_source_files": 1,
                "fully_analyzed_files": 0,
                "complete": False,
                "files": [{"file": "OrderMapper.xml", "status": "partial"}],
                "gaps": [{"file": "OrderMapper.xml", "status": "partial"}],
                "warnings": [],
            },
        }

        evidence = workflow_analysis.build_code_evidence(code_analysis)
        result = workflow_analysis.compare_structured(
            {"tables": {"t_required": {"content": "t_required"}}},
            evidence,
        )
        required = next(item for item in result["items"] if item["key"] == "t_required")

        self.assertEqual("partial", evidence["tables"]["coverage_status"])
        self.assertEqual("identity", evidence["tables"]["comparison_policy"])
        self.assertEqual("证据不足", required["classification"])

    def test_complete_identity_evidence_matches_same_table(self):
        code_analysis = {
            "sql_analysis": [
                {
                    "file": "OrderMapper.xml",
                    "analysis": {
                        "tables": {"sources": ["t_order"], "targets": []},
                    },
                }
            ],
            "technology_coverage": {
                "applicable_source_files": 1,
                "fully_analyzed_files": 1,
                "complete": True,
                "files": [{"file": "OrderMapper.xml", "status": "analyzed"}],
                "gaps": [],
                "warnings": [],
            },
        }

        evidence = workflow_analysis.build_code_evidence(code_analysis)
        result = workflow_analysis.compare_structured(
            {"tables": {"t_order": {"content": "t_order", "source": "requirement.md"}}},
            evidence,
        )

        self.assertEqual("一致", result["items"][0]["classification"])


if __name__ == "__main__":
    unittest.main()
