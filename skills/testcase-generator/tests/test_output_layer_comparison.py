import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import workflow_analysis_legacy as analysis


class OutputComparisonTests(unittest.TestCase):
    def test_explicit_response_fields_compare_only_confirmed_assignment(self):
        code = {
            "files_changed": ["OrderController.java"],
            "output_layers": [{
                "endpoints": [{"endpoint": "/orders/{id}"}],
                "verified_fields": [{
                    "field_key": "/orders/{id}.total",
                    "mapping_file": "OrderController.java",
                    "mapping_line": 6,
                    "file": "OrderVO.java",
                    "line": 2,
                    "source_expression": "order.getAmount()",
                    "source_column": "total_amount",
                    "mapper_file": "OrderMapper.xml",
                    "mapper_line": 4,
                }],
                "declared_fields": [{"field_key": "/orders/{id}.unused"}],
                "coverage_status": "partial",
            }],
            "technology_coverage": {"complete": True, "files": [], "gaps": []},
        }
        doc = (
            "## 接口\nGET /orders/{id}\n"
            "### 响应字段\n"
            "| 字段 | 说明 |\n|---|---|\n"
            "| total | 金额 |\n| unused | 备注 |\n| absent | 其他 |\n"
        )
        facts = analysis.build_requirement_facts({}, doc, code)
        evidence = analysis.build_code_evidence(code)
        comparison = analysis.compare_structured(facts, evidence)
        fields = {
            item["key"]: item["classification"]
            for item in comparison["items"] if item["dimension"] == "fields"
        }
        self.assertEqual("一致", fields["/orders/{id}.total"])
        self.assertEqual("证据不足", fields["/orders/{id}.unused"])
        self.assertEqual("证据不足", fields["/orders/{id}.absent"])
        self.assertEqual("partial", evidence["fields"]["coverage_status"])
        self.assertIn("OrderMapper.xml:4", evidence["fields"]["items"][0]["evidence"])
        self.assertIn("SQL 列 total_amount", analysis._output_layer_markdown(code))
        with tempfile.TemporaryDirectory() as temp:
            report = analysis.generate_requirement_analysis_md(
                Path(temp), {"project_name": "订单输出"}, code, doc,
                facts, {"summary": {"status": "passed"}}, comparison,
            )
            body = report.read_text(encoding="utf-8")
            self.assertIn("### 输出层证据", body)
            self.assertIn("SQL 列 total_amount", body)
            self.assertIn("证据不足", body)

    def test_unscoped_field_in_multi_endpoint_document_is_not_compared(self):
        code = {"output_layers": [{
            "endpoints": [{"endpoint": "/a"}, {"endpoint": "/b"}],
            "verified_fields": [],
        }]}
        facts = analysis.build_requirement_facts({}, "### 输出字段\n| 字段 |\n| name |\n", code)
        self.assertTrue(any(item["dimension"] == "fields" for item in facts["items"]))
        self.assertNotIn("fields", facts)

    def test_analysis_cache_requires_new_output_evidence_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / "old.json"
            cache.write_text(
                '{"schema_version":"2.1.0","branch_sha":"a","baseline_sha":"b",'
                '"selection_hash":"x","analysis_result":{"files_changed":["a.java"]}}',
                encoding="utf-8",
            )
            self.assertIsNone(analysis._load_code_analysis_cache(cache, "a", "b"))


if __name__ == "__main__":
    unittest.main()
