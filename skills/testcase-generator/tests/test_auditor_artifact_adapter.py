import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from auditor_artifact_adapter import AuditorArtifactError, consume_authoritative_audit


class AuditorArtifactAdapterTests(unittest.TestCase):
    def _write_bundle(self, root: Path, run_id="run-1234567890abcdef"):
        output = root / "测试分析"
        internal = output / ".ai-internal"
        internal.mkdir(parents=True)
        targets = [{"target_id": "svc", "repo_path": "synthetic/repo", "branch": "feature/A", "baseline": "master", "target_sha": "a" * 40, "baseline_sha": "b" * 40, "services": ["订单服务"], "status": "completed"}]
        payloads = {
            "requirement_code_audit.json": {"schema_version": "1.0.0", "producer": "requirement-code-auditor", "run_id": run_id, "mode": "audit", "status": "passed", "targets": targets},
            "requirement_facts.json": {"schema_version": "1.0.0", "producer": "requirement-code-auditor", "run_id": run_id, "items": []},
            "code_analysis.json": {"schema_version": "1.0.0", "producer": "requirement-code-auditor", "run_id": run_id, "files_changed": []},
            "code_evidence.json": {"schema_version": "1.0.0", "producer": "requirement-code-auditor", "run_id": run_id, "coverage_summary": {}},
            "requirement_code_comparison.json": {"schema_version": "1.1.0", "producer": "requirement-code-auditor", "run_id": run_id, "summary": {}, "items": []},
            "code_targets.json": {"schema_version": "1.0.0", "producer": "requirement-code-auditor", "run_id": run_id, "targets": targets},
        }
        for name, payload in payloads.items():
            (internal / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return output, internal

    def test_consumes_one_authoritative_run_without_rewriting_core_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output, internal = self._write_bundle(root)
            before = {name: (internal / name).read_bytes() for name in ("requirement_facts.json", "code_evidence.json", "requirement_code_comparison.json")}
            project, analysis = consume_authoritative_audit(root, output)
            after = {name: (internal / name).read_bytes() for name in before}
        self.assertEqual(before, after)
        self.assertEqual("requirement-code-auditor", project["auditor_producer"])
        self.assertEqual([], analysis["files_changed"])

    def test_mixed_run_ids_are_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output, internal = self._write_bundle(root)
            facts = json.loads((internal / "requirement_facts.json").read_text(encoding="utf-8"))
            facts["run_id"] = "different"
            (internal / "requirement_facts.json").write_text(json.dumps(facts), encoding="utf-8")
            with self.assertRaises(AuditorArtifactError):
                consume_authoritative_audit(root, output)


if __name__ == "__main__":
    unittest.main()
