import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import main


class StructuredLoggingTests(unittest.TestCase):
    def test_console_and_stage_duration_are_written_as_jsonl(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch("builtins.print"):
            self.addCleanup(main.shutdown_structured_logging)
            req_dir = Path(temp_dir)
            output_dir = req_dir / "测试分析"
            main.configure_structured_logging(output_dir, "run-structured")
            started = main.begin_stage("analysis", output_dir)
            main.log_console("⚠️ 代码证据不足")
            main.append_execution_trace(
                req_dir, "analysis", "passed", started, output_dir, "分析完成"
            )

            log_path = output_dir / ".ai-internal" / "workflow_log.jsonl"
            events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
            warning = next(item for item in events if item["event"] == "console")
            completed = next(item for item in events if item["event"] == "stage_completed")
            self.assertEqual("WARNING", warning["level"])
            self.assertEqual("analysis", warning["stage"])
            self.assertTrue(warning["run_id"])
            self.assertTrue(warning["timestamp"])
            self.assertEqual("passed", completed["status"])
            self.assertIn("duration_seconds", completed)
            main.shutdown_structured_logging()


if __name__ == "__main__":
    unittest.main()
