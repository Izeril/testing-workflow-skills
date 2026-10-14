import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = SKILL_DIR.parents[2]
SCRIPTS_DIR = SKILL_DIR / "scripts"
CONTRACTS_DIR = SKILL_DIR.parent / "contracts"
VALIDATOR_PATH = CONTRACTS_DIR / "validate_contracts.py"
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "extended_artifact_golden.json"
sys.path.insert(0, str(SCRIPTS_DIR))

from decision_register import build_decision_register
from source_inventory import build_source_inventory
from workflow_common import _write_internal_json
from workflow_metrics import build_metrics

SPEC = importlib.util.spec_from_file_location("extended_contract_validator", VALIDATOR_PATH)
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def _business_impact():
    return {
        "schema_version": "1.1.0",
        "workflow_version": "testcase-generator-2026.07",
        "generated_at": "2026-08-04T09:00:00",
        "analysis_mode": "development_preanalysis",
        "items": [
            {
                "impact_id": "BI-001",
                "target_id": "target-1",
                "business_domain": "申报",
                "affected_actor": ["企业用户"],
                "impact_category": "业务入口",
                "validation_object": "申报提交",
                "business_scenario": "企业提交申报",
                "trigger": "提交按钮",
                "preconditions": ["企业已登录"],
                "previous_behavior": "原流程不校验状态",
                "new_behavior": "按状态执行准入校验",
                "expected_result": ["符合条件时提交成功"],
                "business_rule": ["状态必须有效"],
                "data_impact": ["保存申报记录"],
                "state_impact": ["状态更新为已提交"],
                "downstream_impact": [],
                "exception_impact": ["失败时不落库"],
                "test_focus": ["正常提交", "重复提交"],
                "regression_scope": ["申报入口"],
                "confidence": "medium",
                "evidence_ids": ["REQ-001", "CE-001"],
            }
        ],
        "evidence_catalog": [
            {
                "evidence_id": "CE-001",
                "type": "code_change",
                "target_id": "target-1",
                "source": "SubmitService.submit",
            }
        ],
        "summary": {"total": 1, "confirmed": 0, "high": 0, "medium": 1, "potential": 0},
    }


def _testpoints():
    return {
        "schema_version": "1.0.0",
        "workflow_version": "testcase-generator-2026.07",
        "generated_at": "2026-08-04T09:00:00",
        "test_points": [
            {
                "testpoint_id": "TP-001",
                "title": "有效状态提交成功",
                "category": "申报准入",
                "type": "功能",
                "priority": "P1",
                "steps": "准备有效状态并提交",
                "expected": "提交成功",
                "coverage_ids": ["BI-001"],
                "source_type": "业务影响推导",
                "confidence": "high",
                "candidate_quality": "high",
                "xmind_entry_status": "ready",
                "requires_human_confirm": False,
            }
        ],
    }


class ExtendedArtifactContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = VALIDATOR.load_manifest()

    def test_manifest_registers_nine_nested_schemas(self):
        report = VALIDATOR.validate_manifest(self.manifest)
        self.assertEqual(16, report["artifact_count"])
        self.assertEqual(11, report["nested_schema_count"])

    def test_business_impact_and_testpoints_minimal_payloads_pass(self):
        self.assertTrue(
            VALIDATOR.validate_payload(self.manifest, "business_impact", _business_impact())
        )
        self.assertTrue(VALIDATOR.validate_payload(self.manifest, "testpoints", _testpoints()))

    def test_duplicate_business_impact_and_null_testpoint_are_rejected(self):
        impacts = _business_impact()
        impacts["items"].append(copy.deepcopy(impacts["items"][0]))
        with self.assertRaisesRegex(VALIDATOR.ContractValidationError, r"impact_id 重复"):
            VALIDATOR.validate_payload(self.manifest, "business_impact", impacts)

        points = _testpoints()
        points["test_points"][0] = None
        with self.assertRaisesRegex(
            VALIDATOR.ContractValidationError, r"\$\.test_points\[0\] 类型错误"
        ):
            VALIDATOR.validate_payload(self.manifest, "testpoints", points)

    def test_decision_register_authoritative_builder_and_date_pattern(self):
        payload = build_decision_register(
            {
                "categories": [
                    {
                        "items": [
                            {
                                "id": "RQ-001",
                                "title": "异常口径待确认",
                                "severity": "major",
                                "status": "pending",
                            }
                        ]
                    }
                ]
            }
        )
        self.assertTrue(
            VALIDATOR.validate_payload(self.manifest, "decision_register", payload)
        )
        payload["items"][0]["confirmed_at"] = "2026/08/04"
        with self.assertRaisesRegex(
            VALIDATOR.ContractValidationError, r"\$\.items\[0\]\.confirmed_at 不匹配格式"
        ):
            VALIDATOR.validate_payload(self.manifest, "decision_register", payload)

    def test_source_inventory_authoritative_builder_and_availability_enum(self):
        with tempfile.TemporaryDirectory() as tmp:
            req_dir = Path(tmp) / "requirement"
            output_dir = Path(tmp) / "测试分析"
            req_dir.mkdir()
            normalized = output_dir / ".ai-internal" / "normalized_requirement.md"
            normalized.parent.mkdir(parents=True)
            normalized.write_text("# 需求", encoding="utf-8")
            payload = build_source_inventory(
                "详见[接口说明](https://example.invalid/spec.pdf)", req_dir, normalized
            )
            path = _write_internal_json(output_dir, "source_inventory.json", payload)
            wrapped = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(
            VALIDATOR.validate_payload(self.manifest, "source_inventory", wrapped)
        )
        wrapped["attachments"][0]["availability"] = "unknown"
        with self.assertRaisesRegex(
            VALIDATOR.ContractValidationError,
            r"\$\.attachments\[0\]\.availability 取值",
        ):
            VALIDATOR.validate_payload(self.manifest, "source_inventory", wrapped)

    def test_current_workflow_metrics_builder_passes_and_missing_human_gate_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = build_metrics(Path(tmp), {"events": []}, "run-1")
        self.assertTrue(
            VALIDATOR.validate_payload(self.manifest, "workflow_metrics", payload)
        )
        del payload["human_confirmation"]
        with self.assertRaisesRegex(
            VALIDATOR.ContractValidationError,
            r"workflow_metrics 缺少字段: human_confirmation",
        ):
            VALIDATOR.validate_payload(self.manifest, "workflow_metrics", payload)

    def test_real_positive_artifacts_remain_compatible(self):
        if not FIXTURE_PATH.is_file():
            self.skipTest("真实业务基线清单不随仓库分发；在本机工作区验证")
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        missing = [
            str(PROJECT_ROOT / Path(item["path"]))
            for item in fixture["positive"].values()
            if not (PROJECT_ROOT / Path(item["path"])).is_file()
        ]
        if missing:
            self.skipTest("真实正向金样不在当前工作区: " + "; ".join(missing))
        for artifact, expected in fixture["positive"].items():
            path = PROJECT_ROOT / Path(expected["path"])
            with self.subTest(artifact=artifact):
                data = path.read_bytes()
                self.assertEqual(expected["sha256"], hashlib.sha256(data).hexdigest())
                self.assertEqual(expected["size"], len(data))
                payload = json.loads(data.decode("utf-8"))
                self.assertEqual(expected["count"], len(payload[expected["count_key"]]))
                self.assertTrue(VALIDATOR.validate_payload(self.manifest, artifact, payload))

    def test_real_legacy_artifacts_are_stably_classified_for_regeneration(self):
        if not FIXTURE_PATH.is_file():
            self.skipTest("真实业务基线清单不随仓库分发；在本机工作区验证")
        fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
        missing = [
            str(PROJECT_ROOT / Path(item["path"]))
            for item in fixture["negative"].values()
            if not (PROJECT_ROOT / Path(item["path"])).is_file()
        ]
        if missing:
            self.skipTest("真实负向金样不在当前工作区: " + "; ".join(missing))
        for artifact, expected in fixture["negative"].items():
            path = PROJECT_ROOT / Path(expected["path"])
            with self.subTest(artifact=artifact):
                data = path.read_bytes()
                self.assertEqual(expected["sha256"], hashlib.sha256(data).hexdigest())
                self.assertEqual(expected["size"], len(data))
                payload = json.loads(data.decode("utf-8"))
                with self.assertRaisesRegex(
                    VALIDATOR.ContractValidationError,
                    expected["expected_error"].replace("[", r"\[").replace("]", r"\]"),
                ):
                    VALIDATOR.validate_payload(self.manifest, artifact, payload)


if __name__ == "__main__":
    unittest.main()
