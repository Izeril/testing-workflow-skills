import ast
import unittest
from pathlib import Path


class GenerateXmindStructureTests(unittest.TestCase):
    def test_gen_id_uses_the_single_shared_definition(self):
        source_path = Path(__file__).resolve().parents[1] / "generate_xmind.py"
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        definitions = [
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "gen_id"
        ]
        self.assertEqual(0, len(definitions))
        self.assertIn("from xmind_utils import gen_id", source)


if __name__ == "__main__":
    unittest.main()
