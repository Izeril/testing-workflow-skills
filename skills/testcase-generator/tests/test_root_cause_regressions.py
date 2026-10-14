import importlib.util
import subprocess
import sys
import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch


SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = SKILL_DIR / "scripts"
MAIN_PATH = SCRIPTS_DIR / "main.py"
sys.path.insert(0, str(SCRIPTS_DIR))

SPEC = importlib.util.spec_from_file_location("root_cause_main", MAIN_PATH)
MAIN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MAIN)

import workflow_review
import workflow_common


class RootCauseRegressionTests(unittest.TestCase):
    def test_cli_help_proves_standard_entrypoint_is_executed(self):
        completed = subprocess.run(
            [sys.executable, str(MAIN_PATH), "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("测试用例生成统一入口", completed.stdout)
        self.assertIn("--step", completed.stdout)

    def test_multi_target_coverage_is_normalized_to_dictionary(self):
        raw = [
            {
                "target_id": "java",
                "value": {
                    "applicable_source_files": 2,
                    "fully_analyzed_files": 1,
                    "complete": False,
                    "files": [{"file": "A.java", "status": "partial"}],
                    "gaps": [{"file": "A.java"}],
                    "warnings": ["AST fallback"],
                },
            },
            {
                "target_id": "python",
                "value": {
                    "applicable_source_files": 1,
                    "fully_analyzed_files": 1,
                    "complete": True,
                    "files": [{"file": "job.py", "status": "analyzed"}],
                    "gaps": [],
                    "warnings": [],
                },
            },
        ]
        coverage = MAIN.normalize_technology_coverage(raw)
        self.assertIsInstance(coverage, dict)
        self.assertEqual(3, coverage["applicable_source_files"])
        self.assertEqual(2, coverage["fully_analyzed_files"])
        self.assertFalse(coverage["complete"])
        self.assertEqual("java", coverage["gaps"][0]["target_id"])

    def test_structured_requirement_facts_gate_core_fields(self):
        document = """
# 修改密码准入优化
## 需求背景
当前弱密码可重复使用，需要加强准入。
## 解决方案
修改密码接口增加历史密码校验。
## 涉及接口
POST /api/password/change
"""
        info = MAIN.extract_project_info(document)
        facts = MAIN.build_requirement_facts(info, document, {})
        gate = MAIN.assess_requirement_facts(info, facts)
        self.assertEqual("passed", gate["summary"]["status"])
        self.assertIn("/api/password/change", facts["apis"])

        info["solution"] = ""
        blocked = MAIN.assess_requirement_facts(
            info, MAIN.build_requirement_facts(info, document, {})
        )
        self.assertEqual("blocked", blocked["summary"]["status"])
        self.assertIn(
            "Q3",
            [item["gate_id"] for item in blocked["gates"] if item["blocking"]],
        )

    def test_raw_alert_and_long_json_are_evidence_not_structured_keys(self):
        alert = (
            "【系统告警通知】告警编号：ALM000000000000000001 "
            "异常信息：数据库写入失败 问题直达：https://example.invalid/alarm?"
            + "trace=" + "a" * 180
        )
        long_json = json.dumps(
            {"business_rules_summary": {"items": [{"condition": "x" * 240}]}},
            ensure_ascii=False,
        )
        document = f"""
# 去重修复
## 需求背景
{alert}
## 解决方案
{long_json}
## 涉及接口
POST /api/dedup
"""
        info = MAIN.extract_project_info(document)
        facts = MAIN.build_requirement_facts(info, document, {})
        self.assertNotIn("rules", facts)
        self.assertTrue(any("告警编号" in str(item["content"]) for item in facts["items"]))
        self.assertTrue(any("business_rules_summary" in str(item["content"]) for item in facts["items"]))
        core_facts = [item for item in facts["items"] if item["dimension"] == "rules"]
        self.assertTrue(core_facts)
        self.assertTrue(all(not item["comparable"] for item in core_facts))
        self.assertTrue(all(len(item["stable_key"]) <= 48 for item in core_facts))

    def test_markdown_lines_use_generated_compatibility_keys(self):
        document = """
# 历史数据修复
## 需求背景
需要兼容存量记录。
## 解决方案
回填历史数据标识。
| TC001 | 历史数据处理成功 | |
> migrationJob 处理历史数据
## 涉及接口
POST /api/migrate
"""
        info = MAIN.extract_project_info(document)
        facts = MAIN.build_requirement_facts(info, document, {})
        keys = list(facts["data_compatibility"])
        self.assertTrue(keys)
        self.assertTrue(all(key.startswith("历史数据兼容-") for key in keys))
        contents = [value["content"] for value in facts["data_compatibility"].values()]
        self.assertFalse(any(value.lstrip().startswith(("|", ">")) for value in contents))

    def test_comparison_markdown_hides_legacy_unsafe_key(self):
        raw_key = json.dumps({"business_rules_summary": "x" * 200}, ensure_ascii=False)
        markdown = MAIN._comparison_markdown({
            "items": [{
                "dimension": "rules",
                "key": raw_key,
                "classification": "额外实现",
                "reason": "代码额外行为",
            }]
        })
        self.assertNotIn("business_rules_summary", markdown)
        self.assertIn("未命名结构项-", markdown)

    def test_unstructured_code_rule_dictionary_is_not_a_comparison_key(self):
        evidence = MAIN.build_code_evidence({
            "business_rules": {
                "business_rules_summary": {
                    "items": [{"condition": "x" * 240}]
                }
            }
        })
        self.assertNotIn("rules", evidence)
    def test_absence_marker_is_not_a_structured_scope_fact(self):
        document = """
# 无接口需求
## 需求背景
仅调整内部任务。
## 解决方案
更新内部处理逻辑。
## 涉及接口
无
"""
        info = MAIN.extract_project_info(document)
        facts = MAIN.build_requirement_facts(info, document, {})
        self.assertNotIn("apis", facts)
        self.assertFalse(any(item["dimension"] == "apis" for item in facts["items"]))
    def test_review_rules_do_not_invent_sql_rollback_or_degradation(self):
        context = {
            "requirement_facts": {
                "items": [{"fact_id": "REQ-1", "title": "解决方案"}],
                "apis": {"/api/password/change": {"content": "/api/password/change"}},
            },
            "code_impact": {"impact_scope": {}},
            "comparison": {"items": []},
        }
        issues = workflow_review._ai_deep_review(
            "修改密码接口增加历史密码校验，验证新密码符合规则。",
            [], [], [], {"project_name": "密码准入"}, context,
        )
        titles = "\n".join(item["title"] for item in issues)
        self.assertNotIn("SQL示例", titles)
        self.assertNotIn("回滚方案", titles)
        self.assertNotIn("降级策略", titles)
        self.assertTrue(all(item.get("evidence") for item in issues))

    def test_user_code_report_does_not_dump_raw_analyzer_json(self):
        analysis = {
            "files_changed": ["src/Service.java"],
            "business_rules": {"if_else_rules": list(range(104))},
            "technology_coverage": {
                "applicable_source_files": 1,
                "fully_analyzed_files": 1,
                "complete": True,
                "files": [{"file": "src/Service.java", "status": "analyzed"}],
                "gaps": [],
                "warnings": [],
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = MAIN.generate_code_review_report(
                Path(temp_dir), {"project_name": "演示"}, analysis
            )
            content = Path(path).read_text(encoding="utf-8")
        self.assertNotIn('"if_else_rules"', content)
        self.assertNotIn("104", content)
        self.assertIn("内部证据", content)

    def test_no_code_analysis_skips_comparison(self):
        facts = {
            "items": [],
            "apis": {
                "/api/password/change": {
                    "content": "/api/password/change",
                    "source": "normalized_requirement.md",
                }
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            comparison, _ = MAIN.generate_requirement_code_comparison(
                Path(temp_dir), facts, {}
            )
        classifications = {
            item["classification"] for item in comparison.get("items", [])
        }
        self.assertEqual(set(), classifications)
        self.assertEqual("skipped", comparison["status"])

    def test_analysis_stage_writes_facts_gate_and_readable_report(self):
        document = """
# 修改密码准入优化
## 需求背景
当前弱密码可重复使用，需要加强准入。
## 解决方案
修改密码接口增加历史密码校验。
## 涉及接口
POST /api/password/change
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            req_dir = Path(temp_dir) / "修改密码准入优化"
            req_dir.mkdir()
            (req_dir / "需求说明.md").write_text(document, encoding="utf-8")
            output_dir = req_dir / "测试分析"
            output_dir.mkdir()
            project, analysis = MAIN.run_requirement_analysis(
                req_dir, output_dir, repo_config={"legacy_workflow": True}
            )
            review_path, review_json_path = MAIN.run_requirement_review(
                req_dir, output_dir, project
            )
            internal = output_dir / ".ai-internal"
            gate = json.loads(
                (internal / "analysis_quality_gate.json").read_text(encoding="utf-8")
            )
            report = (output_dir / "需求分析.md").read_text(encoding="utf-8")
            review_report = Path(review_path).read_text(encoding="utf-8")
            review_data = json.loads(
                Path(review_json_path).read_text(encoding="utf-8")
            )
        self.assertEqual("passed", gate["summary"]["status"])
        self.assertNotIn("待补充", report)
        self.assertIn("需求—代码结构化对比", report)
        self.assertEqual("修改密码准入优化", project["project_name"])
        self.assertIsNone(analysis)
        self.assertNotIn("缺少查询SQL示例", review_report)
        self.assertNotIn("缺少回滚方案和兼容性保障", review_report)
        self.assertEqual(0, review_data["summary"]["total_issues"])
        self.assertIn("需求—代码结构化对比", report)
        self.assertIn("未执行需求代码比较", report)

    def test_requirement_package_name_overrides_aggregate_document_title(self):
        document = """
# 纳税报告取数优化**
## 需求背景
股东认缴金额需要按新规则输出。
## 解决方案
主表认缴为零时按持股比例和注册资本计算。
## 涉及接口
POST /api/ns004
"""
        expected_name = "【演示产品20260101】金额字段取数优化"
        with tempfile.TemporaryDirectory() as temp_dir:
            req_dir = Path(temp_dir) / expected_name
            req_dir.mkdir()
            (req_dir / "纳税报告取数优化.md").write_text(document, encoding="utf-8")
            output_dir = req_dir / "测试分析"
            output_dir.mkdir()
            project, _ = MAIN.run_requirement_analysis(
                req_dir, output_dir, repo_config={"legacy_workflow": True}
            )
        self.assertEqual(expected_name, project["project_name"])
        self.assertEqual("纳税报告取数优化", project["document_title"])
        self.assertEqual(
            "requirement_package_directory", project["project_name_source"]
        )

    def test_analysis_stage_blocks_before_placeholder_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            req_dir = Path(temp_dir) / "信息不足需求"
            req_dir.mkdir()
            (req_dir / "需求.md").write_text("# 信息不足需求\n", encoding="utf-8")
            output_dir = req_dir / "测试分析"
            output_dir.mkdir()
            with self.assertRaisesRegex(RuntimeError, "需求事实质量门禁"):
                MAIN.run_requirement_analysis(req_dir, output_dir, repo_config={"legacy_workflow": True})
            gate_path = output_dir / ".ai-internal" / "analysis_quality_gate.json"
            gate = json.loads(gate_path.read_text(encoding="utf-8"))
            self.assertEqual("blocked", gate["summary"]["status"])
            self.assertFalse((output_dir / "需求分析.md").exists())

    def test_doc_conversion_failure_blocks_preflight(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "legacy.doc"
            source.write_bytes(b"demo")
            with patch.object(
                workflow_common,
                "convert_doc_to_md",
                side_effect=RuntimeError("转换内容质量门禁未通过"),
            ):
                with self.assertRaisesRegex(RuntimeError, "DOC 转换质量门禁"):
                    workflow_common.scan_and_convert_docs(Path(temp_dir))

    def test_windows_1385_runtime_error_is_actionable(self):
        with patch.object(
            workflow_common.subprocess,
            "run",
            side_effect=OSError(1385, "CreateProcessWithLogonW failed"),
        ):
            with self.assertRaisesRegex(
                RuntimeError, "SeInteractiveLogonRight"
            ):
                workflow_common.verify_runtime_environment()


if __name__ == "__main__":
    unittest.main()
