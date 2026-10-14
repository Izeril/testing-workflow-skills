import sys
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

from executable_xmind import (
    EXPECTED_LABELS,
    PRECONDITION_LABELS,
    STEP_LABELS,
    has_any_label,
    strip_label,
)


class ExecutableLabelTests(unittest.TestCase):
    def test_only_valid_utf8_chinese_labels_are_registered(self):
        self.assertEqual(("操作步骤",), STEP_LABELS)
        self.assertEqual(("预期结果",), EXPECTED_LABELS)
        self.assertEqual(("前置条件",), PRECONDITION_LABELS)

    def test_step_label_matching_and_stripping(self):
        self.assertTrue(has_any_label("操作步骤：1. 提交数据", STEP_LABELS))
        self.assertEqual("1. 提交数据", strip_label("操作步骤：1. 提交数据", STEP_LABELS))
        self.assertFalse(has_any_label("鎿嶄綔姝ラ：1. 提交数据", STEP_LABELS))


if __name__ == "__main__":
    unittest.main()
