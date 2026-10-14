import json
import tempfile
import unittest
from pathlib import Path

from scripts.comparator import compare_structured
from scripts.requirement_ingestion import ingest_requirement_directory
from scripts.target_resolver import resolve_code_targets


class GoldenAuditSemanticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads((Path(__file__).parent / "fixtures" / "golden" / "golden_audit_cases.json").read_text(encoding="utf-8"))["cases"]

    def test_five_independent_golden_cases_are_locked(self):
        self.assertEqual({"consistent-single-repo", "missing-single-repo", "inconsistent-multi-repo", "ambiguous-target", "partial-coverage"}, {item["id"] for item in self.fixture})

        consistent = compare_structured(
            {"apis": {"items": [{"stable_key": "POST /api/orders"}], "coverage_status": "complete", "comparison_policy": "identity"}},
            {"apis": {"items": [{"stable_key": "POST /api/orders"}], "coverage_status": "complete", "comparison_policy": "identity"}},
        )
        self.assertEqual("一致", consistent["items"][0]["classification"])

        missing = compare_structured(
            {"apis": {"items": [{"stable_key": "POST /api/refunds"}], "coverage_status": "complete", "comparison_policy": "identity"}},
            {"apis": {"items": [{"stable_key": "POST /api/orders"}], "coverage_status": "complete", "comparison_policy": "identity"}},
        )
        self.assertIn("缺失", {item["classification"] for item in missing["items"]})

        inconsistent = compare_structured(
            {"pagination": {"items": [{"stable_key": "pagination:page_size", "value": 20}], "coverage_status": "complete", "comparison_policy": "value"}},
            {"pagination": {"items": [{"stable_key": "pagination:page_size", "value": 50}], "coverage_status": "complete", "comparison_policy": "value"}},
        )
        self.assertEqual("不一致", inconsistent["items"][0]["classification"])

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "需求.md").write_text("# 需求描述\n必须完成审批", encoding="utf-8")
            (root / "PRJ-1-提测.md").write_text("开发分支：feature/A、hotfix/B", encoding="utf-8")
            _, _, release_docs = ingest_requirement_directory(root)
            targets, _ = resolve_code_targets(release_docs, {"path": str(root)})
            self.assertEqual("blocked", targets[0]["status"])

        partial = compare_structured(
            {"apis": {"items": [{"stable_key": "POST /api/external"}], "coverage_status": "complete", "comparison_policy": "identity"}},
            {"apis": {"items": [], "coverage_status": "partial", "comparison_policy": "identity"}},
        )
        self.assertEqual("证据不足", partial["items"][0]["classification"])


if __name__ == "__main__":
    unittest.main()
