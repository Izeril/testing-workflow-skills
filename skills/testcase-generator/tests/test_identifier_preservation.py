import sys
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import workflow_analysis_legacy as analysis


class IdentifierPreservationTests(unittest.TestCase):
    def test_requirement_table_name_preserves_underscores(self):
        info = analysis.extract_project_info("涉及表：t_ckts_ckmx_sbxx_ymqy")

        self.assertIn("t_ckts_ckmx_sbxx_ymqy", info["tables"])
        self.assertNotIn("tcktsckmxsbxxymqy", info["tables"])


if __name__ == "__main__":
    unittest.main()
