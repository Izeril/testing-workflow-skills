import unittest
from pathlib import Path


class PromptEntrypointTests(unittest.TestCase):
    def test_requirement_analysis_prompt_uses_current_entrypoints(self):
        skill_dir = Path(__file__).resolve().parents[1]
        prompt = (skill_dir / "prompts" / "requirement_analysis.md").read_text(encoding="utf-8")
        self.assertNotIn("requirement-analysis/main.py", prompt)
        self.assertIn("testcase-generator/scripts/main.py", prompt)
        self.assertIn("cross_skill_adapter.py", prompt)
        self.assertIn("requirement-code-auditor", prompt)
        self.assertIn("workflow_api.py", prompt)
        self.assertIn("output_layer_analyzer.py", prompt)
        self.assertIn("没有完成分析时比较环节记录 skipped", prompt)
        self.assertIn("已指定目标但分析失败时保留代码环节 blocked", prompt)
        self.assertIn("禁止进入代码测试设计", prompt)
        self.assertNotIn("sys.path.insert", prompt)
        self.assertNotIn("from business_rule_extractor import", prompt)


if __name__ == "__main__":
    unittest.main()
