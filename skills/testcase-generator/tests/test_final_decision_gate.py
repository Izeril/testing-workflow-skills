import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from decision_register import build_decision_register, finalization_blocking_issues
from workflow_refine import require_final_decisions


def completed_register(decision_type="accept"):
    review = {
        "categories": [
            {"items": [{"id": "RQ-001", "title": "业务口径", "severity": "major"}]}
        ]
    }
    register = build_decision_register(review)
    register["items"][0].update(
        {
            "status": "confirmed",
            "decision": "暂缓到下一版本" if decision_type == "defer" else "接受当前口径",
            "decision_type": decision_type,
            "confirmed_by": "张三",
            "confirmed_at": "2026-08-03",
        }
    )
    return register


class FinalDecisionGateTests(unittest.TestCase):
    def test_defer_blocks_finalization_even_when_confirmation_is_complete(self):
        register = completed_register("defer")

        self.assertEqual(["RQ-001:defer"], finalization_blocking_issues(register))

    def test_accept_allows_finalization(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            internal = output / ".ai-internal"
            internal.mkdir()
            (internal / "decision_register.json").write_text(
                json.dumps(completed_register("accept"), ensure_ascii=False),
                encoding="utf-8",
            )

            require_final_decisions(output, "finalize")

    def test_missing_register_blocks_refine(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(RuntimeError, "decision_register.json"):
                require_final_decisions(Path(temp), "refine")


if __name__ == "__main__":
    unittest.main()
