import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = SKILL_DIR.parents[2]
SCRIPT_PATH = SKILL_DIR / "scripts" / "real_project_quality_baseline.py"
MANIFEST_PATH = Path(__file__).resolve().parent / "fixtures" / "real_project_quality_baseline.json"

SPEC = importlib.util.spec_from_file_location("real_project_quality_baseline", SCRIPT_PATH)
BASELINE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BASELINE)


class FakeContractValidator:
    @staticmethod
    def load_manifest():
        return {"fake": True}

    @staticmethod
    def validate_payload(_manifest, _artifact, payload):
        if payload.get("contract_fail"):
            raise ValueError("synthetic contract failure")
        return True


def _write_json(root, name, payload):
    path = root / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _artifact(path, **extra):
    data = path.read_bytes()
    return {
        "filename": path.name,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        **extra,
    }


class RealProjectQualityBaselineTests(unittest.TestCase):
    def build_portable_fixture(self, root):
        source = root / "sample"
        source.mkdir()
        requirement = _write_json(
            source,
            "requirement.json",
            {"items": [{"fact_id": "REQ-1", "source": "requirement.md:1"}]},
        )
        coverage = _write_json(
            source,
            "coverage.json",
            {"statistics": {"p1_risk_coverage_rate": 1.0, "testpoint_count": 2}},
        )
        business = _write_json(
            source,
            "business.json",
            {"items": [{"impact_id": "BI-1", "evidence_ids": ["REQ-1", "CE-1"]}]},
        )
        evidence = _write_json(source, "evidence.json", {"rules": {}})
        expected = _write_json(
            source,
            "expected.json",
            {
                "items": [
                    {
                        "dimension": "rules",
                        "key": "rule-1",
                        "classification": "一致",
                        "requirement": {"evidence": "requirement.md:1"},
                        "code": {"evidence": "Service#method"},
                    }
                ]
            },
        )
        manifest = {
            "schema_version": "1.0.0",
            "thresholds": {
                "min_project_count": 1,
                "min_projects_present": 1,
                "min_artifact_integrity_rate": 1.0,
                "min_contract_pass_rate": 1.0,
                "min_key_field_accuracy": 1.0,
                "min_evidence_traceability_rate": 1.0,
                "min_priority_coverage_rate": 1.0,
                "min_semantic_label_accuracy": 1.0,
                "max_high_risk_false_positive_rate": 0.0,
            },
            "projects": [
                {
                    "id": "portable",
                    "source_root": "sample",
                    "artifacts": [
                        _artifact(
                            requirement,
                            contract="requirement_facts",
                            evidence_kind="requirement_facts",
                            checks=[{"path": "/items", "count": 1}],
                        ),
                        _artifact(
                            coverage,
                            coverage_metrics=["/statistics/p1_risk_coverage_rate"],
                            checks=[{"path": "/statistics/testpoint_count", "equals": 2}],
                        ),
                        _artifact(business, evidence_kind="business_impact"),
                        _artifact(expected, evidence_kind="comparison"),
                    ],
                    "semantic_replays": [
                        {
                            "id": "portable-replay",
                            "requirement_facts": requirement.name,
                            "code_evidence": evidence.name,
                            "expected_comparison": expected.name,
                        }
                    ],
                }
            ],
        }
        return manifest, requirement

    @staticmethod
    def matching_comparator(_requirement, _evidence):
        return {
            "items": [
                {"dimension": "rules", "key": "rule-1", "classification": "一致"}
            ]
        }

    def evaluate(self, manifest, root, comparator=None):
        return BASELINE.evaluate_manifest(
            manifest,
            root,
            comparator=comparator or self.matching_comparator,
            contract_validator=FakeContractValidator,
        )

    def test_portable_fixture_passes_every_threshold(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest, _ = self.build_portable_fixture(root)
            report = self.evaluate(manifest, root)
        self.assertEqual("passed", report["status"])
        self.assertTrue(all(item["passed"] for item in report["thresholds"]))
        self.assertEqual(1.0, report["metrics"]["evidence_traceability_rate"])

    def test_snapshot_drift_fails_integrity_threshold(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest, requirement = self.build_portable_fixture(root)
            requirement.write_text('{"items": []}', encoding="utf-8")
            report = self.evaluate(manifest, root)
        self.assertEqual("failed", report["status"])
        self.assertLess(report["metrics"]["artifact_integrity_rate"], 1.0)

    def test_high_risk_false_positive_is_measured_from_reviewed_label(self):
        def false_positive_comparator(_requirement, _evidence):
            return {
                "items": [
                    {"dimension": "rules", "key": "rule-1", "classification": "缺失"},
                    {"dimension": "rules", "key": "unexpected", "classification": "额外实现"},
                ]
            }

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest, _ = self.build_portable_fixture(root)
            report = self.evaluate(manifest, root, false_positive_comparator)
        self.assertEqual("failed", report["status"])
        self.assertEqual(0.0, report["metrics"]["semantic_label_accuracy"])
        self.assertEqual(1.0, report["metrics"]["high_risk_false_positive_rate"])
        self.assertEqual(2, report["counts"]["semantic_labels_total"])

    def test_missing_project_is_reported_without_mutating_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest, _ = self.build_portable_fixture(root)
            manifest = copy.deepcopy(manifest)
            manifest["projects"][0]["source_root"] = "missing"
            report = self.evaluate(manifest, root)
        self.assertEqual("failed", report["status"])
        self.assertEqual(0, report["metrics"]["projects_present"])
        self.assertFalse(report["projects"][0]["present"])

    def test_unknown_threshold_prefix_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            manifest, _ = self.build_portable_fixture(root)
            manifest["thresholds"] = {"target_accuracy": 1.0}
            with self.assertRaisesRegex(BASELINE.BaselineConfigurationError, "min_ 或 max_"):
                self.evaluate(manifest, root)

    def test_real_workspace_baseline_passes_when_sources_are_available(self):
        if not MANIFEST_PATH.is_file():
            self.skipTest("真实业务基线清单不随仓库分发；在本机工作区验证")
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        if not all(
            (WORKSPACE_ROOT / project["source_root"]).is_dir()
            for project in manifest["projects"]
        ):
            self.skipTest("真实项目金样不在当前工作区")
        report = BASELINE.evaluate_manifest(manifest, WORKSPACE_ROOT)
        self.assertEqual("passed", report["status"], report)
        self.assertEqual(5, report["metrics"]["projects_present"])
        self.assertEqual(1.0, report["metrics"]["semantic_label_accuracy"])
        self.assertEqual(0.0, report["metrics"]["high_risk_false_positive_rate"])


if __name__ == "__main__":
    unittest.main()
