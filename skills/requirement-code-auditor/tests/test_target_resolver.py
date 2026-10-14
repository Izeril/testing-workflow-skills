import json
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from scripts.target_resolver import resolve_code_targets


class MultiRepositoryResolutionRegressionTests(unittest.TestCase):
    def test_registry_resolution_precedes_global_repo_path_validation(self):
        fixture = json.loads(
            (Path(__file__).parent / "fixtures" / "multi_repo_registry.json").read_text(
                encoding="utf-8"
            )
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            test_doc = Path(temp_dir) / "PRJ-1001-Java提测.md"
            test_doc.write_text(
                "涉及开发分支：feature/PRJ-1001\n涉及服务：税务服务\n技术栈：Java\n",
                encoding="utf-8",
            )
            targets, errors = resolve_code_targets(
                [{"path": test_doc, "relative_path": test_doc.name, "content": test_doc.read_text(encoding="utf-8")}],
                fixture["repo"],
            )

        self.assertEqual([], errors)
        self.assertEqual(1, len(targets))
        self.assertEqual("registry", targets[0]["mapping_source"])
        self.assertEqual("C:/fixtures/java-tax-service", targets[0]["repo_path"])
        self.assertEqual("feature/PRJ-1001", targets[0]["branch"])
        self.assertNotEqual("blocked", targets[0]["status"])


if __name__ == "__main__":
    unittest.main()
