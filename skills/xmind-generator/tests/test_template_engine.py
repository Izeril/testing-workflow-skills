import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

import template_engine
from generate_xmind import generate_xmind


class TemplateEngineTest(unittest.TestCase):
    def test_bundled_templates_are_discoverable(self):
        names = {item["name"] for item in template_engine.get_available_templates()}
        self.assertEqual({"generic", "tax_monitor_recovery"}, names)

    def test_generic_requirement_does_not_select_domain_template(self):
        self.assertEqual("generic", template_engine.detect_template("新增用户登录失败次数限制"))

    def test_detection_uses_template_metadata(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            generic = {
                "name": "generic",
                "title": "通用",
                "keywords": [],
                "structure": {"kym": {"title": "KYM"}, "test_analysis": {"title": "测试分析"}},
            }
            custom = {
                "name": "custom",
                "title": "自定义",
                "keywords": ["海关报关", "舱单校验"],
                "min_keyword_matches": 1,
                "structure": {"kym": {"title": "KYM"}, "test_analysis": {"title": "测试分析"}},
            }
            (root / "generic.json").write_text(json.dumps(generic, ensure_ascii=False), encoding="utf-8")
            (root / "custom.json").write_text(json.dumps(custom, ensure_ascii=False), encoding="utf-8")
            with patch.object(template_engine, "TEMPLATE_DIR", root):
                self.assertEqual("custom", template_engine.detect_template("新增海关报关数据校验"))

    def test_render_replaces_placeholders(self):
        template = template_engine.load_template("generic")
        topics = template_engine.render_template(
            template,
            {"problem": "重复提交", "root_cause": "缺少幂等", "solution": "增加幂等键"},
            {},
        )
        text = json.dumps(topics, ensure_ascii=False)
        self.assertIn("重复提交", text)
        self.assertNotIn("{problem}", text)

    def test_generate_generic_xmind_without_legacy_support(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            requirement = root / "requirement.md"
            requirement.write_text("# 用户登录限制\n\n## 问题描述\n重复失败需要锁定", encoding="utf-8")
            output = generate_xmind(str(requirement), output_dir=str(root), versioned=False)
            self.assertTrue(Path(output).is_file())
            with zipfile.ZipFile(output, "r") as archive:
                self.assertIn("content.json", archive.namelist())
                content = archive.read("content.json").decode("utf-8")
            self.assertNotIn("税局服务监控", content)

    def test_generator_has_no_legacy_import(self):
        source = (SKILL_DIR / "generate_xmind.py").read_text(encoding="utf-8")
        for term in ("support/req-analyzer", "support', 'req-analyzer", "from ai_analyzer", "from template_loader"):
            self.assertNotIn(term, source)


if __name__ == "__main__":
    unittest.main()
