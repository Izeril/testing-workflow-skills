import tempfile
import unittest
from pathlib import Path

from scripts.target_resolver import resolve_code_targets


def doc(root: Path, name: str, content: str):
    path = root / name
    path.write_text(content, encoding="utf-8")
    return {"path": path, "relative_path": name, "content": content, "sha256": name}


class TargetResolutionMatrixTests(unittest.TestCase):
    def test_remote_mode_requires_no_repo_registry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            docs = [doc(root, "PRJ-REMOTE.md", "开发分支：feature/remote\n涉及服务：jwy3\nJava")]
            targets, errors = resolve_code_targets(docs, {"baseline": "origin/master"}, provider_name="gitnexus")
        self.assertEqual([], errors)
        self.assertEqual("pending", targets[0]["status"])
        self.assertEqual("gitnexus_auto_discovery", targets[0]["mapping_source"])
        self.assertEqual("", targets[0]["repo_key"])

    def test_single_repository_default_and_explicit_branch_override(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            docs = [doc(root, "PRJ-1.md", "开发分支：feature/A\n涉及服务：A\nJava")]
            targets, errors = resolve_code_targets(docs, {"path": "repo", "baseline": "master"}, branch="hotfix/override")
        self.assertEqual([], errors)
        self.assertEqual("single_repository_default", targets[0]["mapping_source"])
        self.assertEqual("hotfix/override", targets[0]["branch"])
        self.assertEqual("explicit_override", targets[0]["branch_source"])

    def test_two_release_documents_keep_independent_branches_and_repositories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            docs = [
                doc(root, "PRJ-Java.md", "开发分支：feature/java\n涉及服务：税务服务\nJava"),
                doc(root, "PRJ-Python.md", "开发分支：feature/python\n涉及服务：采集服务\nPython"),
            ]
            config = {"targets": [
                {"target_id": "java", "path": "java-repo", "services": ["税务服务"], "languages": ["java"]},
                {"target_id": "python", "path": "python-repo", "services": ["采集服务"], "languages": ["python"]},
            ]}
            targets, _ = resolve_code_targets(docs, config)
        self.assertEqual(["feature/java", "feature/python"], [item["branch"] for item in targets])
        self.assertEqual(["java-repo", "python-repo"], [item["repo_path"] for item in targets])

    def test_missing_branch_and_ambiguous_repository_are_blocked(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            missing, _ = resolve_code_targets([doc(root, "PRJ-X.md", "涉及服务：A\nJava")], {"path": "repo"})
            ambiguous, _ = resolve_code_targets(
                [doc(root, "PRJ-Y.md", "开发分支：feature/Y\n涉及服务：A\nJava")],
                {"targets": [
                    {"path": "repo-1", "services": ["A"], "languages": ["java"]},
                    {"path": "repo-2", "services": ["A"], "languages": ["java"]},
                ]},
            )
        self.assertEqual("branch_missing", missing[0]["block_reason"])
        self.assertEqual("ambiguous_repository_mapping", ambiguous[0]["block_reason"])


if __name__ == "__main__":
    unittest.main()
