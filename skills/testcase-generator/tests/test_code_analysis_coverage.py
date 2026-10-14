import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import main


class CodeAnalysisCoverageContractTests(unittest.TestCase):
    def test_run_code_analysis_preserves_coverage_and_ast_fields(self):
        logic = {
            "file_changes": {"ui/app.ts": "M"},
            "technology_coverage": {
                "applicable_source_files": 1,
                "fully_analyzed_files": 0,
                "complete": False,
                "gaps": [{"file": "ui/app.ts", "status": "recognized_unanalyzed"}],
                "warnings": ["ui/app.ts: no syntax analyzer"],
            },
            "syntax_analysis": [{"file": "Service.java", "engine": "balanced-token-fallback"}],
            "python_analysis": [{"file": "job.py", "engine": "python-ast"}],
            "analysis_warnings": ["ui/app.ts: no syntax analyzer"],
        }
        with tempfile.TemporaryDirectory() as temp_dir, patch("builtins.print"), patch.object(
            main, "extract_business_logic", return_value=logic
        ):
            result = main.run_code_analysis(
                "repo", "feature", "main", Path(temp_dir),
                selected_files=["ui/app.ts"],
                branch_sha="a" * 40,
                baseline_sha="b" * 40,
            )
        self.assertFalse(result["technology_coverage"]["complete"])
        self.assertEqual("balanced-token-fallback", result["syntax_analysis"][0]["engine"])
        self.assertEqual("python-ast", result["python_analysis"][0]["engine"])
        self.assertTrue(result["analysis_warnings"])

    def test_coverage_gap_lowers_confidence_and_reaches_impact_json(self):
        analysis = {
            "files_changed": ["ui/app.ts"],
            "api_endpoints": ["/a", "/b", "/c"],
            "technology_coverage": {
                "applicable_source_files": 1,
                "fully_analyzed_files": 0,
                "complete": False,
                "gaps": [{"file": "ui/app.ts"}],
                "warnings": ["ui/app.ts: inventory only"],
            },
            "analysis_warnings": ["ui/app.ts: inventory only"],
        }
        self.assertEqual("medium", main._classify_impact_confidence(analysis))
        with tempfile.TemporaryDirectory() as temp_dir, patch("builtins.print"):
            path = main.generate_code_impact_json(
                Path(temp_dir), Path(temp_dir), "feature", "main", analysis
            )
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual("1.3.0", payload["schema_version"])
        self.assertEqual(1, payload["summary"]["coverage_gap_files"])
        self.assertIn("ui/app.ts: inventory only", payload["unconfirmed_evidence"])


if __name__ == "__main__":
    unittest.main()
