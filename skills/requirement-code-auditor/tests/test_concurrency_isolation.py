import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scripts.audit_runner import run_requirement_code_audit
from scripts.evidence_provider import CodeEvidenceProvider


class Provider(CodeEvidenceProvider):
    def analyze(self, target, requirement_anchors=None):
        coverage = {"complete": True, "gaps": [], "files": []}
        return {
            "code_analysis": {"files_changed": ["A.java"], "file_changes": {"A.java": "M"}, "api_endpoints": ["POST /run"], "technology_coverage": coverage, "coverage_summary": coverage, "analysis_warnings": []},
            "snapshot": {"repository": "fake", "target_sha": "a" * 40, "baseline_sha": "b" * 40},
            "coverage": coverage,
            "analysis_capabilities": {"call_chain": {"provider": "static_text", "codegraph_status": "not_configured", "degraded": False, "limitations": []}},
        }


def prepare(root: Path, name: str):
    req = root / name
    req.mkdir()
    (req / "需求.md").write_text("# 需求描述\nPOST /run 执行。", encoding="utf-8")
    (req / f"PRJ-{name}-提测.md").write_text(f"开发分支：feature/{name}\n涉及服务：{name}\nJava", encoding="utf-8")
    return req


class ConcurrencyIsolationTests(unittest.TestCase):
    def test_two_requirement_directories_get_distinct_runs_and_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first, second = prepare(root, "first"), prepare(root, "second")
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda req: run_requirement_code_audit(req, repo_path="fake", evidence_provider=Provider()), (first, second)))
            payloads = [json.loads((req / "测试分析" / ".ai-internal" / "requirement_code_audit.json").read_text(encoding="utf-8")) for req in (first, second)]
        self.assertNotEqual(results[0]["run_id"], results[1]["run_id"])
        self.assertEqual({result["run_id"] for result in results}, {payload["run_id"] for payload in payloads})


if __name__ == "__main__":
    unittest.main()
