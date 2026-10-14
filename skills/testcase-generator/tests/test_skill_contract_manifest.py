import copy
import importlib.util
import sys
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
SKILLS_ROOT = SKILL_DIR.parent
sys.path.insert(0, str(SCRIPTS_DIR))

import cross_skill_adapter
from skill_errors import ConversionError

VALIDATOR_PATH = SKILLS_ROOT / "contracts" / "validate_contracts.py"
SPEC = importlib.util.spec_from_file_location("skill_contract_validator", VALIDATOR_PATH)
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


class SkillContractManifestTests(unittest.TestCase):
    def test_manifest_resolves_only_active_workflow_skills_and_public_symbols(self):
        manifest = VALIDATOR.load_manifest()
        report = VALIDATOR.validate_manifest(manifest)
        self.assertEqual("passed", report["status"])
        active_skills = {"testcase-generator", "requirement-code-auditor", "doc-to-md", "xmind-generator"}
        self.assertEqual(active_skills, set(manifest["skills"]))
        self.assertEqual(4, report["skill_count"])
        self.assertEqual(4, report["workflow_skill_count"])
        self.assertEqual(8, report["entrypoint_count"])
        self.assertEqual(2, report["archived_skill_count"])
        self.assertEqual([], report["skipped_compatibility_skills"])
        self.assertEqual({"code-analyzer", "req-code-comparator"}, set(manifest["archived_skills"]))
        for contract in manifest["archived_skills"].values():
            self.assertNotIn("public_entrypoints", contract)
            self.assertNotIn("cli", contract)
        self.assertEqual(16, report["artifact_count"])
        self.assertEqual(11, report["nested_schema_count"])

    def test_archived_skills_cannot_be_registered_as_active_dependencies(self):
        manifest = VALIDATOR.load_manifest()
        for old_skill in ("code-analyzer", "req-code-comparator"):
            with self.subTest(skill=old_skill):
                broken = copy.deepcopy(manifest)
                broken["skills"][old_skill] = {
                    "public_entrypoints": [{"path": f"{old_skill}/entrypoint.py", "symbol": "run"}],
                    "cli": f"{old_skill}/entrypoint.py",
                    "lifecycle": "compatibility",
                    "replacement_skill": "requirement-code-auditor",
                }
                with self.assertRaises(VALIDATOR.ContractValidationError):
                    VALIDATOR.validate_manifest(broken)

    def test_archive_metadata_cannot_expose_callable_entrypoints(self):
        manifest = VALIDATOR.load_manifest()
        for old_skill in ("code-analyzer", "req-code-comparator"):
            for field, payload in (
                ("cli", f"../skills-archive/{old_skill}/entrypoint.py"),
                ("public_entrypoints", [{"path": f"../skills-archive/{old_skill}/entrypoint.py", "symbol": "run"}]),
            ):
                with self.subTest(skill=old_skill, field=field):
                    broken = copy.deepcopy(manifest)
                    broken["archived_skills"][old_skill][field] = payload
                    with self.assertRaises(VALIDATOR.ContractValidationError):
                        VALIDATOR.validate_manifest(broken)

    def test_real_comparator_output_satisfies_versioned_contract(self):
        compare = cross_skill_adapter.load_public_symbol("requirement-code-auditor", "compare_structured")
        payload = compare(
            {"tables": {"items": ["t_order"], "coverage_status": "complete"}},
            {"tables": {"items": ["t_order"], "coverage_status": "complete"}},
        )
        manifest = VALIDATOR.load_manifest()
        self.assertTrue(
            VALIDATOR.validate_payload(manifest, "requirement_code_comparison", payload)
        )

    def test_error_payload_contract_and_breaking_drift_are_detected(self):
        manifest = VALIDATOR.load_manifest()
        payload = ConversionError("bad document", stage="check").as_dict()
        self.assertTrue(VALIDATOR.validate_payload(manifest, "error_payload", payload))

        broken = copy.deepcopy(manifest)
        broken["skills"]["doc-to-md"]["public_entrypoints"][0]["symbol"] = "removed_symbol"
        with self.assertRaises(VALIDATOR.ContractValidationError):
            VALIDATOR.validate_manifest(broken)


if __name__ == "__main__":
    unittest.main()
