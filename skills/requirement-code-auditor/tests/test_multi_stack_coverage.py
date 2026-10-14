import sys
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

from scripts.analyzer import business_extractor


class MultiStackCoverageTests(unittest.TestCase):
    def test_every_changed_file_has_coverage_record_and_gaps_are_visible(self):
        changes = {
            "src/Service.java": "M",
            "jobs/worker.py": "A",
            "db/package.pkb": "M",
            "ui/panel.ts": "M",
            "legacy/Unknown.java": "M",
        }
        contents = {
            "src/Service.java": "class Service { void run(){ if (ready) { validate(); } } }",
            "jobs/worker.py": "def run(x):\n    if not x:\n        raise ValueError()\n",
            "db/package.pkb": "CREATE OR REPLACE PROCEDURE sync_data AS BEGIN INSERT INTO target_table(id) SELECT id FROM source_table; END;",
            "ui/panel.ts": "export function save(x: Item) { if (!x) throw new Error('missing'); }",
            "legacy/Unknown.java": "",
        }

        def read_content(repo_path, branch, file_path):
            return contents[file_path]

        with patch.object(business_extractor, "get_file_changes", return_value=changes), patch.object(
            business_extractor, "get_file_content", side_effect=read_content
        ):
            result = business_extractor.extract_business_logic("repo", "feature", "main")

        coverage = result["technology_coverage"]
        self.assertEqual(len(changes), coverage["total_changed_files"])
        by_file = {item["file"]: item for item in coverage["files"]}
        java_metadata = result["syntax_analysis"][0]
        expected_java_status = (
            "partial" if java_metadata.get("fallback_reason") else "analyzed"
        )
        self.assertEqual(expected_java_status, by_file["src/Service.java"]["status"])
        if java_metadata.get("fallback_reason"):
            self.assertEqual(
                java_metadata["fallback_reason"],
                by_file["src/Service.java"]["fallback_reason"],
            )
            self.assertIn("src/Service.java", coverage["warnings"][0])
        self.assertEqual("analyzed", by_file["jobs/worker.py"]["status"])
        self.assertEqual("partial", by_file["db/package.pkb"]["status"])
        self.assertEqual("recognized_unanalyzed", by_file["ui/panel.ts"]["status"])
        self.assertEqual("unreadable", by_file["legacy/Unknown.java"]["status"])
        self.assertFalse(coverage["complete"])
        self.assertTrue(result["analysis_warnings"])
        self.assertEqual(1, len(result["python_analysis"]))
        self.assertEqual("sql-or-procedure", result["sql_analysis"][0]["kind"])


if __name__ == "__main__":
    unittest.main()
