import copy
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SKILL_DIR.parents[2]
CONTRACTS_DIR = SKILL_DIR.parent / "contracts"
VALIDATOR_PATH = CONTRACTS_DIR / "validate_contracts.py"
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "core_artifact_golden.json"

SPEC = importlib.util.spec_from_file_location("nested_contract_validator", VALIDATOR_PATH)
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def _metadata():
    return {
        "schema_version": "1.0.0",
        "workflow_version": "testcase-generator-2026.07",
        "generated_at": "2026-08-03T12:00:00",
    }


def _mode():
    return {
        "selected_mode": "standard",
        "mode_label": "标准模式",
        "mode_reason": ["存在状态流转"],
        "mode_score": 7,
        "score_items": [
            {
                "feature": "状态流转",
                "weight": 4,
                "reason": "存在状态迁移",
                "evidence": "状态",
                "source": "确定性规则",
            }
        ],
        "trigger_features": ["状态流转"],
        "code_analysis_strategy": "codegraph_impact_selection_preferred",
        "selected_by": "auto_rule_score",
        "confirmed_by_user": False,
    }


def _requirement_facts():
    return {
        **_metadata(),
        "items": [
            {
                "fact_id": "REQ-001",
                "dimension": "rules",
                "type": "明确需求",
                "title": "处理规则",
                "content": "满足条件时执行",
                "source": ".ai-internal/normalized_requirement.md",
                "confidence": "high",
            }
        ],
    }


def _test_design_model():
    return {
        **_metadata(),
        "mode": _mode(),
        "risks": [],
        "state_transitions": [],
        "review_questions": [],
        "quality_issues": [],
        "consistency_summary": {
            "status": "passed",
            "c1_count": 0,
            "c2_count": 0,
            "c3_count": 0,
            "c4_count": 0,
            "finding_count": 0,
        },
        "fix_suggestion_summary": {
            "status": "passed",
            "suggestion_count": 0,
            "p1_count": 0,
            "p2_count": 0,
            "requires_human_confirm_count": 0,
            "selected_mode": "standard",
        },
    }


def _coverage_matrix():
    return {
        **_metadata(),
        "requirement_facts": [],
        "risks": [],
        "state_transitions": [],
        "review_questions": [],
        "mappings": [
            {
                "coverage_id": "RISK-001",
                "coverage_type": "risk",
                "title": "异常恢复",
                "priority": "P1",
                "covered_by": ["TP-001"],
                "status": "covered",
            }
        ],
        "statistics": {
            "requirement_fact_count": 0,
            "risk_count": 1,
            "p1_risk_count": 1,
            "p1_risk_covered": 1,
            "p1_risk_coverage_rate": 1.0,
            "state_transition_count": 0,
            "critical_state_transition_count": 0,
            "critical_state_transition_covered": 0,
            "critical_state_transition_coverage_rate": 1.0,
            "review_question_count": 0,
            "important_review_question_count": 0,
            "important_review_question_handled": 0,
            "important_review_question_coverage_rate": 1.0,
            "testpoint_count": 1,
        },
        "mode_selection": _mode(),
    }


def _quality_gate_report():
    return {
        **_metadata(),
        "summary": {"status": "passed", "blocking_count": 0, "warning_count": 0},
        "gates": [
            {
                "gate_id": "G1",
                "name": "来源完整性",
                "status": "passed",
                "message": "无来源缺口",
                "blocking": False,
            }
        ],
        "auto_repaired_count": 0,
        "auto_repaired_by_gate": {"G2": 0, "G3": 0, "G4": 0, "G7": 0},
    }


class NestedArtifactContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = VALIDATOR.load_manifest()

    def test_manifest_registers_all_nested_schemas(self):
        report = VALIDATOR.validate_manifest(self.manifest)
        self.assertEqual(16, report["artifact_count"])
        self.assertEqual(11, report["nested_schema_count"])

    def test_requirement_fact_missing_source_reports_precise_path(self):
        payload = _requirement_facts()
        del payload["items"][0]["source"]
        with self.assertRaisesRegex(
            VALIDATOR.ContractValidationError,
            r"\$\.items\[0\] 缺少字段: source",
        ):
            VALIDATOR.validate_payload(self.manifest, "requirement_facts", payload)

    def test_test_design_model_rejects_nested_type_drift(self):
        payload = _test_design_model()
        payload["mode"]["confirmed_by_user"] = "false"
        with self.assertRaisesRegex(
            VALIDATOR.ContractValidationError,
            r"\$\.mode\.confirmed_by_user 类型错误",
        ):
            VALIDATOR.validate_payload(self.manifest, "test_design_model", payload)

    def test_coverage_matrix_rejects_duplicate_coverage_id(self):
        payload = _coverage_matrix()
        payload["mappings"].append(copy.deepcopy(payload["mappings"][0]))
        with self.assertRaisesRegex(
            VALIDATOR.ContractValidationError,
            r"\$\.mappings\[1\]\.coverage_id 重复",
        ):
            VALIDATOR.validate_payload(self.manifest, "coverage_matrix", payload)

    def test_quality_gate_rejects_unknown_status(self):
        payload = _quality_gate_report()
        payload["gates"][0]["status"] = "ignored"
        with self.assertRaisesRegex(
            VALIDATOR.ContractValidationError,
            r"\$\.gates\[0\]\.status 取值",
        ):
            VALIDATOR.validate_payload(self.manifest, "quality_gate_report", payload)

    def test_minimal_current_payloads_pass(self):
        for artifact, payload in (
            ("requirement_facts", _requirement_facts()),
            ("test_design_model", _test_design_model()),
            ("coverage_matrix", _coverage_matrix()),
            ("quality_gate_report", _quality_gate_report()),
        ):
            with self.subTest(artifact=artifact):
                self.assertTrue(VALIDATOR.validate_payload(self.manifest, artifact, payload))

    def test_real_project_golden_artifacts_remain_compatible(self):
        if not FIXTURE_PATH.is_file():
            self.skipTest("真实业务基线清单不随仓库分发；在本机工作区验证")
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        source_root = PROJECT_ROOT / Path(fixture["source_root"])
        if not source_root.is_dir():
            self.skipTest(f"真实项目金样不在当前工作区: {source_root}")
        for artifact, expected in fixture["artifacts"].items():
            path = source_root / expected["filename"]
            with self.subTest(artifact=artifact):
                data = path.read_bytes()
                self.assertEqual(expected["sha256"], hashlib.sha256(data).hexdigest())
                self.assertEqual(expected["size"], len(data))
                payload = json.loads(data.decode("utf-8"))
                self.assertTrue(VALIDATOR.validate_payload(self.manifest, artifact, payload))
                for key, count in expected["counts"].items():
                    self.assertEqual(count, len(payload[key]), key)


if __name__ == "__main__":
    unittest.main()
