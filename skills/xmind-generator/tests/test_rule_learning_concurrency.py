import json
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

from review_xmind import auto_learn_rules, rules_config_scope


class RuleLearningConcurrencyTests(unittest.TestCase):
    def test_same_project_scope_merges_concurrent_rule_updates(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            documents = {
                "api-project": "- 1.1.1 接口正常调用\n- 1.1.2 接口正常返回",
                "concurrent-project": "- 1.1.1 并发处理能力\n- 1.1.2 并发正确处理",
            }

            def learn(item):
                project_type, markdown = item
                with rules_config_scope(output):
                    auto_learn_rules(markdown, project_type)

            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(learn, documents.items()))

            payload = json.loads(
                (output / ".ai-internal" / "xmind_rules_config.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertIn("api-project", payload["project_rules"])
        self.assertIn("concurrent-project", payload["project_rules"])


if __name__ == "__main__":
    unittest.main()
