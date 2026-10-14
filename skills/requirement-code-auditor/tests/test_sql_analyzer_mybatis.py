import sys
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

from scripts.analyzer.sql_analyzer import analyze_sql_content, extract_tables_from_sql


class MyBatisSqlAnalyzerTests(unittest.TestCase):
    def test_update_tag_id_is_not_treated_as_target_table(self):
        mapper = """
        <mapper namespace="OrderMapper">
          <update id="batchUpdate">
            UPDATE t_order
            SET hash_code = #{hashCode}
            WHERE id = #{id}
          </update>
          <select id="selectPage" resultType="map">
            SELECT * FROM t_order WHERE id &gt; #{lastId} ORDER BY id
          </select>
        </mapper>
        """

        result = analyze_sql_content(mapper)

        self.assertEqual(["t_order"], sorted(result["tables"]["targets"]))
        self.assertNotIn("id", result["tables"]["targets"])
        self.assertEqual(["t_order"], sorted(result["tables"]["sources"]))

    def test_plain_update_sql_still_extracts_target(self):
        result = extract_tables_from_sql(
            "UPDATE t_order SET status = 'DONE' WHERE id = 1"
        )

        self.assertEqual(["t_order"], result["targets"])

    def test_mybatis_statements_do_not_leak_into_each_other(self):
        mapper = """
        <mapper>
          <select id="first">SELECT id FROM t_first WHERE enabled = 1</select>
          <select id="second">SELECT id FROM t_second WHERE enabled = 1</select>
        </mapper>
        """

        result = analyze_sql_content(mapper)

        self.assertCountEqual(["t_first", "t_second"], result["tables"]["sources"])
        self.assertTrue(all("</select>" not in item["condition"] for item in result["filters"]))


if __name__ == "__main__":
    unittest.main()
