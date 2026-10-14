import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from code_review_report import (
    assess_code_review_readability,
    extract_manual_findings,
    write_code_review_report,
)


class CodeReviewReadabilityTests(unittest.TestCase):
    def test_manual_findings_are_preserved_and_translated_to_test_actions(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            (output / "代码评审报告.md").write_text(
                "# 旧报告\n\n# 人工源码复核补充\n\n"
                "### P0-1 在线更新仍使用 32 位 hash_code 定位\n\n"
                "碰撞时可能更新错误记录。`DemoMapper.xml:80`\n\n"
                "### P1-2 单税号失败后任务仍标记成功\n\n"
                "异常只记录日志。`MigrationJob.java:90`\n",
                encoding="utf-8",
            )
            findings = extract_manual_findings(output)
            self.assertEqual(2, len(findings))
            self.assertIn("只允许目标记录", " ".join(findings[0]["test_focus"]))
            self.assertIn("最终任务状态", " ".join(findings[1]["test_focus"]))
            self.assertTrue((output / ".ai-internal" / "manual_code_review_findings.json").exists())

    def test_report_is_risk_first_deduplicated_and_has_no_raw_parser_dump(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            (output / "代码评审报告.md").write_text(
                "# 旧报告\n\n# 人工源码复核补充\n\n"
                "### P0-1 在线哈希与历史回填哈希缺少统一规范化\n\n"
                "金额格式不同可能重复落库。\n",
                encoding="utf-8",
            )
            business = {
                "analysis_mode": "development_preanalysis",
                "items": [
                    {"impact_id": "BI-1", "impact_category": "任务", "validation_object": "迁移任务", "test_focus": ["异常恢复"], "confidence": "medium"},
                    {"impact_id": "BI-2", "impact_category": "API", "validation_object": "迁移任务", "test_focus": ["重复执行"], "confidence": "medium"},
                ],
            }
            path, model, gate = write_code_review_report(
                output,
                {"project_name": "去重优化"},
                {"files_changed": ["A.java"], "scheduled_tasks": ["job", "job"], "sql_analysis": [{"analysis": {"source_file": "raw"}}]},
                {"impact_confidence": "medium"},
                {"items": [{"classification": "额外实现", "key": "business_rules_summary {'raw': 1}"}]},
                business,
                [{"target_id": "demo", "baseline": "master", "baseline_sha": "a" * 40, "development_branch": "feature", "development_sha": "b" * 40}],
            )
            text = path.read_text(encoding="utf-8")
            self.assertEqual(1, len(model["validation_objects"]))
            self.assertEqual("passed", gate["summary"]["status"])
            self.assertIn("测试人员必看风险", text)
            self.assertIn("怎么验证", text)
            self.assertNotIn("business_rules_summary", text)
            self.assertNotIn("source_file", text)
            self.assertNotIn("{'raw'", text)

    def test_evidenced_mybatis_partial_is_rendered_as_nonblocking(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)
            code_analysis = {
                "files_changed": ["mapper/DemoMapper.xml"],
                "sql_analysis": [{
                    "target_id": "demo",
                    "value": {
                        "file": "mapper/DemoMapper.xml",
                        "analysis": {"tables": {"targets": ["t_demo"]}},
                    },
                }],
                "technology_coverage": {
                    "complete": False,
                    "applicable_source_files": 1,
                    "fully_analyzed_files": 0,
                    "gaps": [{
                        "file": "mapper/DemoMapper.xml",
                        "status": "partial",
                        "analyzer": "mybatis-sql-when-detected",
                    }],
                },
            }
            path, model, _gate = write_code_review_report(
                output,
                {"project_name": "去重优化"},
                code_analysis,
                {},
                {},
                {"analysis_mode": "uat_confirmed", "items": []},
                [],
            )
            text = path.read_text(encoding="utf-8")
            self.assertEqual([], model["coverage"]["gaps"])
            self.assertEqual(["mapper/DemoMapper.xml"], model["coverage"]["accepted_partial"])
            self.assertIn("关键覆盖缺口：0 个", text)
            self.assertIn("已提取 SQL，仍需集成验证：mapper/DemoMapper.xml", text)
    def test_raw_or_inactionable_findings_fail_hard_gate(self):
        model = {
            "review_decision": "有风险",
            "findings": [{
                "finding_id": "P0-1",
                "title": "原始结构",
                "tester_impact": "business_rules_summary {'raw': 1}",
                "test_focus": [],
                "release_gate": "",
            }],
            "validation_objects": [],
        }
        gate = assess_code_review_readability(model)
        failed = {item["gate_id"] for item in gate["gates"] if item["blocking"]}
        self.assertIn("CR-G2", failed)
        self.assertIn("CR-G3", failed)
        self.assertEqual("blocked", gate["summary"]["status"])


if __name__ == "__main__":
    unittest.main()
