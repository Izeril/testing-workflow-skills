import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from decision_register import build_decision_register, testpoint_blocking_issues
from source_inventory import build_source_inventory
import workflow_review


class AttachmentReviewGateTests(unittest.TestCase):
    def test_missing_business_attachment_becomes_decidable_review_issue(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            content = "计费字段口径见[接口附件](https://example.invalid/billing.docx)。"
            inventory = build_source_inventory(
                content,
                root,
                root / "测试分析" / ".ai-internal" / "normalized_requirement.md",
            )
            issues = workflow_review._ai_deep_review(
                content,
                [],
                [],
                [],
                {"project_name": "附件需求"},
                {
                    "requirement_facts": {"items": []},
                    "code_impact": {},
                    "comparison": {"items": []},
                    "source_inventory": inventory,
                },
            )
            attachment_issue = next(item for item in issues if item["id"] == "ATT-001")
            review = workflow_review._build_review_json(
                issues, "附件需求", [], [], source_inventory=inventory
            )
            register = build_decision_register(review)

        self.assertEqual("major", attachment_issue["severity"])
        self.assertIn("linked_missing", attachment_issue["description"])
        self.assertEqual(1, review["document_status"]["attachments"]["total"])
        self.assertIn("ATT-001", testpoint_blocking_issues(register))


if __name__ == "__main__":
    unittest.main()
