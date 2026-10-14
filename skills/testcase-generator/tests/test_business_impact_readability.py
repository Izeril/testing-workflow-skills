import sys
import tempfile
import unittest
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from business_impact_gate import assess_business_impact_gate
from business_impact_readability import make_tester_readable, readability_issues
from business_impact_report import write_business_impact_report
from business_impact_translator import translate_business_impacts


class BusinessImpactReadabilityTests(unittest.TestCase):
    def _target(self):
        task = {
            "name": "dedupHashMigration4DemoJob",
            "source_file": "src/DedupHashMigration4DemoJob.java",
        }
        return {
            "target_id": "demo",
            "analysis_mode": "development",
            "analysis": {
                "scheduled_tasks": [task],
                "entry_points": [{"name": task["name"], "type": "XxlJob"}],
                "api_endpoints": [task["name"]],
                "sql_analysis": [{
                    "source_file": "mapper/DemoMapper.xml",
                    "analysis": {"tables": {"sources": ["t_demo_history"], "targets": ["id", "t_demo_history"]}},
                }],
                "business_rules": {
                    "if_else_rules": [{
                        "condition": "targetTaxIds != null && !targetTaxIds.isEmpty()",
                        "action": "taxIds = targetTaxIds",
                        "source_file": "src/AbstractMigrationJob.java",
                    }]
                },
                "files_changed": ["src/DedupHashMigration4DemoJob.java", "mapper/DemoMapper.xml"],
            },
        }

    def test_duplicate_entry_types_are_grouped_and_sql_is_grouped_by_table(self):
        old = {
            "items": [{
                "impact_id": "BI-OLD",
                "target_id": "demo",
                "trigger": "dedupHashMigration4DemoJob",
                "business_scenario": "旧场景",
                "affected_actor": ["测试人员"],
                "confidence": "medium",
                "evidence_ids": ["CE-001-DEMO"],
            }],
            "summary": {},
        }
        result = make_tester_readable(old, {}, [self._target()], {"requirement_name": "去重回填"})
        self.assertEqual(2, len(result["items"]))
        self.assertEqual(2, result["summary"]["tester_readable"])
        objects = [item["validation_object"] for item in result["items"]]
        self.assertEqual(1, sum("dedupHashMigration4DemoJob" in value for value in objects))
        self.assertTrue(any("t_demo_history" in value for value in objects))
        self.assertFalse(any(readability_issues(item) for item in result["items"]))

    def test_raw_parser_structure_fails_readability_gate(self):
        item = {
            "impact_id": "BI-RAW",
            "target_id": "demo",
            "business_domain": "去重回填",
            "affected_actor": ["测试人员"],
            "impact_category": "任务",
            "validation_object": "回填任务",
            "business_scenario": "执行回填",
            "trigger": "手工触发",
            "preconditions": ["value_annotations | [{'source_file': 'A.java'}]"],
            "new_behavior": "完成回填",
            "expected_result": ["完成回填"],
            "test_focus": ["正常场景"],
            "confidence": "medium",
            "evidence_ids": ["CE-001-DEMO"],
        }
        gate = assess_business_impact_gate({"items": [item]}, [self._target()])
        g7 = next(value for value in gate["gates"] if value["gate_id"] == "BI-G7")
        self.assertEqual("failed", g7["status"])
        self.assertTrue(g7["blocking"])
        self.assertEqual("blocked", gate["summary"]["status"])

    def test_report_uses_tester_sections_without_raw_dicts(self):
        payload = make_tester_readable(
            {"items": [{
                "impact_id": "BI-OLD", "target_id": "demo",
                "trigger": "dedupHashMigration4DemoJob", "affected_actor": ["测试人员"],
                "confidence": "medium", "evidence_ids": ["CE-001-DEMO"],
            }], "summary": {}, "analysis_mode": "development_preanalysis"},
            {}, [self._target()], {"requirement_name": "去重回填"},
        )
        gate = assess_business_impact_gate(payload, [self._target()])
        with tempfile.TemporaryDirectory() as temp:
            report = write_business_impact_report(Path(temp), payload, gate)
            text = report.read_text(encoding="utf-8")
        self.assertIn("测试关注点", text)
        self.assertIn("技术追溯（测试执行时可忽略）", text)
        self.assertNotIn("if_else_rules", text)
        self.assertNotIn("source_file", text)
        self.assertNotIn("[{'", text)


    def test_nonblocking_failed_gate_is_rendered_as_warning(self):
        payload = make_tester_readable(
            {"items": [], "summary": {}, "analysis_mode": "uat_confirmed"},
            {},
            [],
            {"requirement_name": "去重回填"},
        )
        gate = {
            "summary": {"status": "passed"},
            "gates": [{
                "gate_id": "BI-G5-W",
                "name": "MyBatis 部分覆盖提示",
                "status": "failed",
                "blocking": False,
                "message": "已提取 SQL，仍需集成验证",
            }],
        }
        with tempfile.TemporaryDirectory() as temp:
            report = write_business_impact_report(Path(temp), payload, gate)
            text = report.read_text(encoding="utf-8")
        self.assertIn("| BI-G5-W MyBatis 部分覆盖提示 | 提醒 |", text)
        self.assertNotIn("| BI-G5-W MyBatis 部分覆盖提示 | 未通过 |", text)
    def test_uat_merged_with_missing_graph_is_not_labeled_development_preanalysis(self):
        target = self._target()
        target.update({
            "analysis_mode": "uat",
            "effective_analysis_mode": "uat",
            "uat_merge_gate": {"status": "fully_merged"},
        })
        payload = translate_business_impacts(
            {},
            [target],
            {
                "targets": [{
                    "target_id": "demo",
                    "status": "index_missing",
                    "relations": [],
                    "files": [],
                }]
            },
            {"requirement_name": "去重回填"},
        )
        self.assertEqual("uat_merged_evidence_incomplete", payload["analysis_mode"])

    def test_evidenced_mybatis_partial_coverage_is_warning_not_blocker(self):
        target = self._target()
        target.update({
            "analysis_mode": "uat",
            "uat_merge_gate": {"status": "fully_merged"},
            "codegraph_status": "ready",
        })
        target["analysis"]["technology_coverage"] = {
            "complete": False,
            "gaps": [{
                "file": "mapper/DemoMapper.xml",
                "status": "partial",
                "analyzer": "mybatis-sql-when-detected",
            }],
        }
        item = {
            "impact_id": "BI-MYBATIS",
            "target_id": "demo",
            "business_domain": "去重回填",
            "affected_actor": ["测试人员"],
            "impact_category": "数据",
            "validation_object": "历史表回填",
            "business_scenario": "批量更新去重标识",
            "trigger": "执行迁移任务",
            "preconditions": ["存在待回填记录"],
            "new_behavior": "分批更新去重标识",
            "expected_result": ["目标记录全部完成回填"],
            "test_focus": ["核对更新行数和字段值"],
            "confidence": "high",
            "evidence_ids": ["CE-001-DEMO"],
        }
        gate = assess_business_impact_gate({"items": [item]}, [target])
        g5 = next(value for value in gate["gates"] if value["gate_id"] == "BI-G5")
        warning = next(value for value in gate["gates"] if value["gate_id"] == "BI-G5-W")
        self.assertEqual("passed", g5["status"])
        self.assertFalse(g5["blocking"])
        self.assertEqual("failed", warning["status"])
        self.assertFalse(warning["blocking"])
        self.assertEqual("passed", gate["summary"]["status"])
        self.assertEqual(1, gate["summary"]["warning_count"])

    def test_evidenced_mybatis_partial_with_ready_graph_is_formal_uat(self):
        target = self._target()
        target.update({
            "analysis_mode": "uat",
            "effective_analysis_mode": "uat",
            "uat_merge_gate": {"status": "fully_merged"},
            "codegraph_status": "ready",
        })
        target["analysis"]["technology_coverage"] = {
            "complete": False,
            "gaps": [{
                "file": "mapper/DemoMapper.xml",
                "status": "partial",
                "analyzer": "mybatis-sql-when-detected",
            }],
        }
        payload = translate_business_impacts(
            {},
            [target],
            {
                "targets": [{
                    "target_id": "demo",
                    "status": "ready",
                    "relations": [{"source": "job", "target": "service"}],
                    "files": ["src/DedupHashMigration4DemoJob.java"],
                }]
            },
            {"requirement_name": "去重回填"},
        )
        self.assertEqual("uat_confirmed", payload["analysis_mode"])
        self.assertTrue(payload["items"])
        self.assertTrue(all(item["confidence"] == "confirmed" for item in payload["items"]))
    def test_mybatis_partial_without_sql_evidence_remains_blocking(self):
        target = self._target()
        target.update({
            "analysis_mode": "uat",
            "uat_merge_gate": {"status": "fully_merged"},
            "codegraph_status": "ready",
        })
        target["analysis"]["technology_coverage"] = {
            "complete": False,
            "gaps": [{
                "file": "mapper/UnknownMapper.xml",
                "status": "partial",
                "analyzer": "mybatis-sql-when-detected",
            }],
        }
        gate = assess_business_impact_gate({"items": []}, [target])
        g5 = next(value for value in gate["gates"] if value["gate_id"] == "BI-G5")
        self.assertEqual("failed", g5["status"])
        self.assertTrue(g5["blocking"])
if __name__ == "__main__":
    unittest.main()
