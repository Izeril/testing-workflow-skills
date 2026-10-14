"""Canonical multi-stack analyzer facade."""

from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, List, Optional, Sequence

from ..git_runner import run_git
from .business_extractor import extract_business_logic


def analyze_code(
    repo_path: str,
    branch: str,
    baseline: str = "origin/master",
    selected_files: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Analyze a branch with the migrated AST/SQL/config-aware engine."""
    return extract_business_logic(repo_path, branch, baseline, selected_files=selected_files)


class CodeAnalyzer:
    """Backward-compatible class adapter with one canonical parsing path."""

    def __init__(self, repo_dir=None):
        self.repo_dir = repo_dir or os.getcwd()

    def _run_git(self, args: Sequence[str]):
        result = run_git(self.repo_dir, args, timeout=30, executor=subprocess.run)
        return result.returncode, result.stdout if result.returncode == 0 else result.stderr

    def analyze_branch(
        self,
        branch_name: str,
        baseline: str = "origin/master",
        selected_files: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        result = analyze_code(self.repo_dir, branch_name, baseline, selected_files)
        file_changes = result.get("file_changes", {})
        files = [{"status": status, "path": path} for path, status in file_changes.items()]
        result.update(
            branch=branch_name,
            files=files,
            controllers=[item["path"] for item in files if "controller" in item["path"].casefold()],
            services=[item["path"] for item in files if "service" in item["path"].casefold()],
            daos=[item["path"] for item in files if any(token in item["path"].casefold() for token in ("mapper", "dao", "repository"))],
            entities=[item["path"] for item in files if any(token in item["path"].casefold() for token in ("entity", "dto", "vo"))],
            sqls=result.get("sql_analysis", []),
            apis=result.get("api_endpoints", []),
            risks=result.get("analysis_warnings", []),
        )
        return result


__all__ = ["analyze_code", "CodeAnalyzer", "extract_business_logic"]
