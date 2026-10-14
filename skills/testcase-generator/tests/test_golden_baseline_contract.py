import json
import unittest
from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent
FIXTURE_DIR = TESTS_DIR / "fixtures" / "golden"
BASELINE_PATH = TESTS_DIR / "fixtures" / "golden_baseline.json"


class GoldenBaselineContractTests(unittest.TestCase):
    def test_baseline_manifest_matches_fixture_inventory(self):
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        fixtures = [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(FIXTURE_DIR.glob("*.json"))
        ]
        self.assertEqual(baseline["sample_count"], len(fixtures))
        self.assertEqual(
            set(baseline["categories"]), {fixture["category"] for fixture in fixtures}
        )
        self.assertEqual(
            0, baseline["acceptance"]["high_risk_false_positive_count"]
        )
        self.assertIn("test_golden_semantic_regressions.py", baseline["ci_command"])


if __name__ == "__main__":
    unittest.main()
