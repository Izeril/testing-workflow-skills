import json
import tempfile
import unittest
from pathlib import Path

from scripts.artifact_writer import ArtifactSession
from scripts.audit_runner import run_requirement_code_audit
from scripts.evidence_provider import CodeEvidenceProvider
from scripts.skill_errors import AuditorError


class FakeProvider(CodeEvidenceProvider):
    def __init__(self, *, complete=True, endpoint="POST /api/orders"):
        self.complete = complete
        self.endpoint = endpoint
        self.calls = []

    def analyze(self, target, requirement_anchors=None):
        self.calls.append((dict(target), list(requirement_anchors or [])))
        coverage = {"complete": self.complete, "gaps": [] if self.complete else [{"path": "Unknown.vue", "status": "unsupported"}], "files": []}
        analysis = {"files_changed": ["OrderController.java"], "file_changes": {"OrderController.java": "M"}, "api_endpoints": [self.endpoint], "technology_coverage": coverage, "coverage_summary": coverage, "analysis_warnings": []}
        return {"code_analysis": analysis, "snapshot": {"repository": "fake", "target_sha": "a" * 40, "baseline_sha": "b" * 40}, "coverage": coverage, "analysis_capabilities": {"call_chain": {"provider": "static_text", "codegraph_status": "not_configured", "degraded": False, "limitations": []}}}


def make_requirement(root: Path, branch_text="feature/PRJ-1"):
    (root / "需求.md").write_text("# 需求描述\nPOST /api/orders 创建订单。", encoding="utf-8")
    (root / "PRJ-1-提测.md").write_text(f"涉及开发分支：{branch_text}\n涉及服务：订单服务\n技术栈：Java", encoding="utf-8")


class AuditRunnerTests(unittest.TestCase):
    def test_end_to_end_requires_only_requirement_directory_when_repo_configured(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            make_requirement(root)
            provider = FakeProvider()
            result = run_requirement_code_audit(root, repo_path=str(root / "fake-repo"), evidence_provider=provider)
            internal = root / "测试分析" / ".ai-internal"
            self.assertEqual("audit", result["mode"])
            self.assertTrue((root / "测试分析" / "需求实现审计报告.md").exists())
            self.assertTrue((internal / "requirement_code_comparison.json").exists())
            audit = json.loads((internal / "requirement_code_audit.json").read_text(encoding="utf-8"))
            self.assertEqual("requirement-code-auditor", audit["producer"])
            self.assertEqual(1, len(provider.calls))

    def test_analyze_mode_does_not_create_fake_comparison(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            make_requirement(root)
            run_requirement_code_audit(root, repo_path=str(root / "fake"), mode="analyze", evidence_provider=FakeProvider())
            self.assertFalse((root / "测试分析" / ".ai-internal" / "requirement_code_comparison.json").exists())

    def test_branch_ambiguity_blocks_and_writes_error_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            make_requirement(root, "feature/A、hotfix/B")
            with self.assertRaises(AuditorError) as caught:
                run_requirement_code_audit(root, repo_path=str(root / "fake"), evidence_provider=FakeProvider())
            self.assertEqual(3, caught.exception.exit_code)
            error_path = root / "测试分析" / ".ai-internal" / "last_error.json"
            self.assertTrue(error_path.exists())
            self.assertEqual("code_target_ambiguous", json.loads(error_path.read_text(encoding="utf-8"))["error_code"])

    def test_same_output_directory_is_locked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "out"
            with ArtifactSession(output, "first"):
                with self.assertRaises(AuditorError) as caught:
                    with ArtifactSession(output, "second"):
                        pass
            self.assertEqual("output_locked", caught.exception.error_code)
