import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

import review_xmind


class ReviewXmindStructureTests(unittest.TestCase):
    def test_review_point_is_leaf_and_pending_issue_is_parsed(self):
        markdown = """
# 测试点评审清单
## 待确认问题
- AM-003：最终字段类型未形成统一契约
## 一、取值优先级
### 1.1 功能场景
- 1.1.1 主表认缴为0且注册资本1000万元、比例0.5时，验证比例兜底；预期输出500万元。 [P1]
  - 笔记：覆盖依据：RQ-001
    测试数据：主表认缴为0、注册资本1000万元、比例0.5
    预期结果：输出500万元
"""
        sections = review_xmind.parse_test_points_from_md(markdown)
        point = sections[0]["categories"][0]["test_points"][0]
        node = review_xmind.create_test_point_node(
            point["title"], notes_text=point["notes"]
        )
        self.assertNotIn("children", node)
        self.assertEqual(
            "覆盖依据：RQ-001\n测试数据：主表认缴为0、注册资本1000万元、比例0.5\n预期结果：输出500万元",
            node["notes"]["plain"]["content"],
        )
        self.assertEqual(
            ["AM-003：最终字段类型未形成统一契约"],
            review_xmind.parse_pending_issues_from_md(markdown),
        )

    def test_review_generator_does_not_publish_final_before_confirmation(self):
        markdown = """
## KYM
- 项目名称：演示需求
- 核心变更：金额兜底
## 一、功能测试
### 1.1 正常场景
- 1.1.1 主表认缴为0时验证比例兜底；预期输出500万元。 [P1]
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "详细测试点.md"
            source.write_text(markdown, encoding="utf-8")
            review_path, final_path = review_xmind.generate_review_xmind(
                source, temp_dir
            )
            self.assertTrue(Path(review_path).exists())
            self.assertIsNone(final_path)
            self.assertFalse((Path(temp_dir) / "测试组织_测试负责人_评审_终稿.xmind").exists())


if __name__ == "__main__":
    unittest.main()
