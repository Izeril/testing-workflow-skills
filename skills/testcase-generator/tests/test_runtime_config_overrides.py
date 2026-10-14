import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

import workflow_common


class RuntimeConfigOverrideTests(unittest.TestCase):
    def test_precedence_is_distribution_then_local_then_environment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            skill = Path(temp_dir)
            (skill / "config.yaml").write_text(
                "project:\n  company: distribution\n  manager: distribution-manager\n"
                "repo:\n  path: ''\n  baseline: origin/main\n",
                encoding="utf-8",
            )
            (skill / "config.local.yaml").write_text(
                "project:\n  company: local\n  manager: local-manager\n"
                "repo:\n  path: C:/local/repo\n",
                encoding="utf-8",
            )
            env = {
                "TESTCASE_COMPANY": "environment",
                "TESTCASE_MANAGER": "environment-manager",
                "TESTCASE_REPO_PATH": r"C:\repo with spaces & symbols",
                "TESTCASE_BASELINE": "origin/env-main",
            }
            with patch.object(workflow_common, "SKILL_DIR", skill), patch.dict(
                os.environ, env, clear=False
            ):
                config = workflow_common.load_config()
            self.assertEqual("environment", config["project"]["company"])
            self.assertEqual("environment-manager", config["project"]["manager"])
            self.assertEqual(r"C:\repo with spaces & symbols", config["repo"]["path"])
            self.assertEqual("origin/env-main", config["repo"]["baseline"])

    def test_local_config_preserves_machine_values_without_distribution_hardcoding(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            skill = Path(temp_dir)
            (skill / "config.yaml").write_text(
                "project:\n  company: ''\n  manager: ''\nrepo:\n  path: ''\n",
                encoding="utf-8",
            )
            (skill / "config.local.yaml").write_text(
                "project:\n  company: local-company\n  manager: local-manager\n"
                "repo:\n  path: D:/local/repo\n",
                encoding="utf-8",
            )
            env_keys = [
                "TESTCASE_COMPANY", "TESTCASE_MANAGER", "TESTCASE_REPO_PATH",
                "TESTCASE_BASELINE", "TESTCASE_UAT_BRANCH", "TESTCASE_PRODUCT",
            ]
            with patch.object(workflow_common, "SKILL_DIR", skill), patch.dict(
                os.environ, {key: "" for key in env_keys}, clear=False
            ):
                config = workflow_common.load_config()
            self.assertEqual("local-company", config["project"]["company"])
            self.assertEqual("local-manager", config["project"]["manager"])
            self.assertEqual("D:/local/repo", config["repo"]["path"])


if __name__ == "__main__":
    unittest.main()
