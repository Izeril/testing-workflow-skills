import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

import main


class ArchiveStageGateTests(unittest.TestCase):
    def test_unavailable_archive_cannot_be_recorded_as_passed_stage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            req_dir = Path(temp_dir) / "requirement"
            output_dir = req_dir / "测试分析"
            output_dir.mkdir(parents=True)
            config = {
                "repo": {"path": "read-only-repo", "baseline": "master"},
                "workflow": {},
                "output": {"dir_name": "测试分析"},
                "project": {"manager": "", "company": ""},
            }
            with patch.object(sys, "argv", ["main.py", str(req_dir), "--step", "archive"]), \
                    patch.object(main, "resolve_requirement_dir", return_value=req_dir), \
                    patch.object(main, "get_config", return_value=config), \
                    patch.object(main, "configure_structured_logging"), \
                    patch.object(main, "_install_workflow_failure_hook"), \
                    patch.object(main, "check_and_initialize", return_value=(None, output_dir)), \
                    patch.object(main, "begin_stage", return_value=0), \
                    patch.object(main, "run_archive", return_value={
                        "status": "unavailable",
                        "completed": False,
                        "reason": "knowledge_base_not_initialized",
                    }), \
                    patch.object(main, "append_execution_trace") as append_trace, \
                    patch("builtins.print"):
                with self.assertRaisesRegex(RuntimeError, "knowledge_base_not_initialized"):
                    main.main()
            append_trace.assert_not_called()


if __name__ == "__main__":
    unittest.main()
