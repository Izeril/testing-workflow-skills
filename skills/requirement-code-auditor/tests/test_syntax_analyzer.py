import sys
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR))

from scripts.analyzer.business_rule_extractor import extract_business_rules
from scripts.analyzer.syntax_analyzer import analyze_java_syntax, analyze_python_syntax


class SyntaxAnalyzerTests(unittest.TestCase):
    def test_java_multiline_if_throw_and_validate_survive_format_changes(self):
        variants = [
            """
            if (
                account != null
                && account.isActive()
            ) {
                validator
                    .validate(account);
            } else {
                throw new IllegalStateException("inactive");
            }
            """,
            """
            if /* formatting */ (account != null &&
                account.isActive())
            { checkAccount(
                account
            ); }
            else { throw
                new IllegalStateException("inactive"); }
            """,
        ]
        for source in variants:
            with self.subTest(source=source):
                result = analyze_java_syntax(source)
                self.assertIn(result["engine"], {"tree-sitter-java", "balanced-token-fallback"})
                self.assertTrue(result["if_else_rules"])
                self.assertTrue(result["throw_rules"])
                self.assertTrue(result["validation_rules"])
                if result["engine"] == "balanced-token-fallback":
                    self.assertFalse(result["parser_available"])
                    self.assertTrue(result["fallback_reason"])

    def test_business_rule_extractor_uses_syntax_level_rules(self):
        source = """
        if (
            value == null
            || value.isBlank()
        ) {
            throw new IllegalArgumentException("value");
        }
        validateValue(
            value
        );
        """
        rules = extract_business_rules(source)
        self.assertEqual(1, len(rules["if_else_rules"]))
        self.assertEqual(1, len(rules["throw_rules"]))
        self.assertTrue(rules["validation_rules"])
        self.assertIn("engine", rules["analysis_metadata"])

    def test_python_builtin_ast_extracts_control_and_entrypoint(self):
        source = """
@api.post("/jobs")
async def submit(payload):
    check_payload(payload)
    if not payload:
        raise ValueError("missing")
    assert payload.id
"""
        result = analyze_python_syntax(source)
        self.assertEqual([], result["parse_errors"])
        self.assertEqual(1, len(result["if_else_rules"]))
        self.assertEqual(1, len(result["throw_rules"]))
        self.assertGreaterEqual(len(result["validation_rules"]), 2)
        self.assertEqual("submit", result["entry_points"][0]["name"])


if __name__ == "__main__":
    unittest.main()
