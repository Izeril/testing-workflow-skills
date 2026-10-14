import importlib
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from cross_skill_adapter import load_public_symbol

# Exercise the same auditor parser loaded through the production boundary.
_auditor_package = load_public_symbol("requirement-code-auditor").__module__
analyze_sql_content = importlib.import_module(
    _auditor_package + ".analyzer.sql_analyzer"
).analyze_sql_content
import workflow_analysis_legacy as workflow


class MyBatisComparisonGoldenTests(unittest.TestCase):
    def test_mapper_table_flows_through_to_consistent_comparison(self):
        mapper = """
        <mapper namespace="OrderMapper">
          <update id="batchUpdate">
            UPDATE t_order SET hash_code = #{hashCode} WHERE id = #{id}
          </update>
          <select id="selectPage" resultType="map">
            SELECT * FROM t_order WHERE id &gt; #{lastId} ORDER BY id
          </select>
        </mapper>
        """
        sql_analysis = analyze_sql_content(mapper)
        code_analysis = {
            "sql_analysis": [
                {
                    "file": "mapper/OrderMapper.xml",
                    "analysis": sql_analysis,
                }
            ],
            "technology_coverage": {
                "summary": {"overall_status": "analyzed"},
            },
        }
        requirement = {
            "tables": {
                "t_order": {
                    "content": "t_order",
                    "source": "normalized_requirement.md",
                }
            }
        }

        evidence = workflow.build_code_evidence(code_analysis)
        comparison = workflow.compare_structured(requirement, evidence)
        table_items = [
            item for item in comparison["items"] if item["dimension"] == "tables"
        ]

        self.assertEqual(["t_order"], [item["key"] for item in table_items])
        self.assertEqual("一致", table_items[0]["classification"])
        self.assertNotIn("id", [item["key"] for item in table_items])


if __name__ == "__main__":
    unittest.main()
