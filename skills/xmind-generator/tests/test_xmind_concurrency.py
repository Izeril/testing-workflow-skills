import json
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

from generate_xmind import generate_xmind
from review_xmind import load_rules_config, rules_config_scope, save_rules_config
from xmind_utils import release_reserved_path, reserve_versioned_path


class XMindConcurrencyTests(unittest.TestCase):
    def test_workstation_rules_seed_project_without_changing_generic_baseline(self):
        import review_xmind
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            baseline = root / "rules_config.json"
            baseline.write_text(json.dumps({"common_templates": {"generic": {}}, "project_rules": {}}), encoding="utf-8")
            before = baseline.read_bytes()
            baseline.with_name("rules_config.local.json").write_text(json.dumps({"project_rules": {"local-project": {}}, "kym_mapping": {"demo": {"核心变更": "演示变更", "影响业务": "演示业务"}}}), encoding="utf-8")
            with patch.object(review_xmind, "RULES_CONFIG_PATH", baseline):
                with rules_config_scope(root / "output"):
                    rules = load_rules_config()
                    self.assertIn("generic", rules["common_templates"])
                    self.assertIn("local-project", rules["project_rules"])
                    self.assertEqual("演示变更", rules["kym_mapping"]["demo"]["核心变更"])
                    save_rules_config({"project_rules": {"output-project": {}}})
                    self.assertNotIn("local-project", load_rules_config()["project_rules"])
                save_rules_config(rules)
                self.assertEqual(before, baseline.read_bytes())

    def test_version_reservation_is_unique_under_threads(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)

            with ThreadPoolExecutor(max_workers=8) as pool:
                paths = list(
                    pool.map(
                        lambda _: reserve_versioned_path(output, "评审_20260803"),
                        range(8),
                    )
                )

            self.assertEqual(8, len({str(path) for path in paths}))
            self.assertEqual(
                {f"评审_20260803_v{index}.xmind" for index in range(1, 9)},
                {path.name for path in paths},
            )
            for path in paths:
                release_reserved_path(path)

    def test_template_generation_publishes_two_valid_unique_archives(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            requirement = root / "需求.md"
            requirement.write_text(
                "# 登录限制\n\n## 需求背景\n连续失败后锁定账号。",
                encoding="utf-8",
            )
            output = root / "测试分析"

            with ThreadPoolExecutor(max_workers=2) as pool:
                paths = list(
                    pool.map(
                        lambda _: Path(generate_xmind(str(requirement), output_dir=str(output))),
                        range(2),
                    )
                )

            self.assertEqual(2, len({str(path) for path in paths}))
            for path in paths:
                with zipfile.ZipFile(path, "r") as archive:
                    self.assertTrue(
                        {"content.json", "metadata.json", "manifest.json"}.issubset(
                            archive.namelist()
                        )
                    )

    def test_rule_state_is_scoped_per_output_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "project-a"
            second = root / "project-b"
            with rules_config_scope(first):
                save_rules_config({"project_rules": {"A": {}}})
                self.assertIn("A", load_rules_config()["project_rules"])
            with rules_config_scope(second):
                save_rules_config({"project_rules": {"B": {}}})
                self.assertIn("B", load_rules_config()["project_rules"])

            first_payload = json.loads(
                (first / ".ai-internal" / "xmind_rules_config.json").read_text(
                    encoding="utf-8"
                )
            )
            second_payload = json.loads(
                (second / ".ai-internal" / "xmind_rules_config.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertIn("A", first_payload["project_rules"])
            self.assertNotIn("A", second_payload["project_rules"])


if __name__ == "__main__":
    unittest.main()
