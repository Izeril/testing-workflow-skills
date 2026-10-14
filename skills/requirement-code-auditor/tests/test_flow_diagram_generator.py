import sys
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

from scripts.analyzer.flow_diagram_generator import generate_complete_flow_diagram


class CompleteFlowDiagramTests(unittest.TestCase):
    def test_complete_diagram_aggregates_list_based_sql_analysis(self):
        analysis = {
            "sql_analysis": [
                {
                    "file": "mapper/OrderMapper.xml",
                    "analysis": {
                        "tables": {"sources": ["orders"], "targets": ["order_summary"]},
                        "filters": [{"condition": "status = 'PAID'"}],
                        "aggregations": [{"function": "SUM", "column": "amount"}],
                    },
                }
            ]
        }

        diagram = generate_complete_flow_diagram(analysis)

        self.assertIn("orders", diagram)
        self.assertIn("order_summary", diagram)
        self.assertIn("SUM(amount)", diagram)
        self.assertIn("status = 'PAID'", diagram)

    def test_complete_diagram_rejects_invalid_sql_analysis_type(self):
        with self.assertRaisesRegex(TypeError, "sql_analysis"):
            generate_complete_flow_diagram({"sql_analysis": "not-a-list"})


if __name__ == "__main__":
    unittest.main()
