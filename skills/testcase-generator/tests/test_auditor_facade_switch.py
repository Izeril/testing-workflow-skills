import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import workflow_analysis_facade as facade


class AnalysisChainTests(unittest.TestCase):
    def test_established_chain_uses_requested_output_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "测试分析"
            for repo_config in ({}, {"legacy_workflow": True}):
                with self.subTest(repo_config=repo_config):
                    with patch.object(
                        facade, "_legacy_run_requirement_analysis",
                        side_effect=RuntimeError("analysis reached"),
                    ) as analysis:
                        with self.assertRaisesRegex(RuntimeError, "analysis reached"):
                            facade.run_requirement_analysis(
                                Path(temp_dir) / "req", output, repo_config=repo_config
                            )
                    self.assertEqual(output, Path(analysis.call_args.args[1]))
            self.assertFalse((output / "legacy").exists())


if __name__ == "__main__":
    unittest.main()
