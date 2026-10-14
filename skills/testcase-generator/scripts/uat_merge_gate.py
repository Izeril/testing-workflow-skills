#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only UAT merge gate supporting merge, cherry-pick and squash evidence."""

from __future__ import annotations

import hashlib
import os
import subprocess
from typing import Any, Dict, Iterable, List, Tuple

from workflow_git_runner import run_git, stable_patch_id


def _git(repo_path: str, args: List[str], timeout: int = 90) -> Tuple[int, str, str]:
    result = run_git(
        repo_path, args, timeout=timeout, text=False, executor=subprocess.run
    )
    return result.returncode, result.stdout, result.stderr


def _patch_id(repo_path: str, git_args: List[str]) -> str:
    """Calculate stable patch-id through the unified argument-list runner."""
    return stable_patch_id(repo_path, git_args, executor=subprocess.run)


def _path_content_equal(repo_path: str, left: str, right: str, path: str) -> bool:
    code, _, _ = _git(repo_path, ["diff", "--quiet", left, right, "--", path], 30)
    return code == 0


def assess_uat_merge(
    repo_path: str,
    scope: Dict[str, Any],
    snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    """Classify whether the requirement scope is present in the UAT snapshot."""
    target_id = scope.get("target_id", "unknown")
    development = snapshot.get("development_sha") or snapshot.get("development_ref", "")
    baseline = snapshot.get("baseline_sha") or snapshot.get("baseline_ref", "")
    uat = snapshot.get("uat_sha") or snapshot.get("uat_ref", "")
    commits = list(scope.get("commits") or [])
    paths = [item.get("path") for item in scope.get("raw_changed_files", []) if item.get("path")]
    if not snapshot.get("uat_sha") or not uat:
        return {
            "target_id": target_id,
            "status": "indeterminate",
            "reason": "缺少可验证的 UAT SHA",
            "formal_ready": False,
        }
    if not commits:
        return {
            "target_id": target_id,
            "status": "indeterminate",
            "reason": "需求提交范围为空，无法验证 UAT 合入",
            "formal_ready": False,
        }

    direct, missing = [], []
    for commit in commits:
        code, _, _ = _git(repo_path, ["merge-base", "--is-ancestor", commit, uat], 20)
        (direct if code == 0 else missing).append(commit)

    # git cherry reports patch-equivalent commits with "-", covering normal
    # cherry-picks even when commit SHA changes.
    _, cherry_text, _ = _git(repo_path, ["cherry", uat, development], 60)
    patch_equivalent = {
        line[2:].strip()
        for line in cherry_text.splitlines()
        if line.startswith("- ")
    }
    equivalent_missing = [commit for commit in missing if commit in patch_equivalent]
    unresolved = [commit for commit in missing if commit not in patch_equivalent]

    equal_paths = [path for path in paths if _path_content_equal(repo_path, development, uat, path)]
    different_paths = [path for path in paths if path not in equal_paths]

    # Multi-commit squash detection compares the combined requirement patch
    # with recent UAT commits, then requires target file content to match.
    combined_patch = _patch_id(repo_path, ["diff", "--binary", scope.get("comparison_range", f"{baseline}...{development}")])
    _, uat_history, _ = _git(repo_path, ["rev-list", "--max-count=200", uat], 30)
    squash_commit = ""
    if combined_patch:
        for candidate in (line.strip() for line in uat_history.splitlines() if line.strip()):
            if _patch_id(repo_path, ["show", "--pretty=format:", "--binary", candidate]) == combined_patch:
                squash_commit = candidate
                break

    if not unresolved:
        status, method = "fully_merged", "history_or_patch_equivalent"
    elif squash_commit and not different_paths:
        status, method = "fully_merged", "squash_patch_equivalent_and_content_verified"
    elif direct or equivalent_missing or equal_paths:
        status, method = "partially_merged", "partial_commit_or_content_match"
    else:
        status, method = "not_merged", "no_commit_patch_or_content_evidence"

    return {
        "schema_version": "1.0.0",
        "target_id": target_id,
        "status": status,
        "method": method,
        "development_sha": snapshot.get("development_sha", ""),
        "uat_sha": snapshot.get("uat_sha", ""),
        "commit_count": len(commits),
        "directly_merged_commits": direct,
        "patch_equivalent_commits": equivalent_missing,
        "missing_commits": unresolved,
        "equal_files": equal_paths,
        "missing_or_different_files": different_paths,
        "combined_patch_fingerprint": combined_patch,
        "squash_commit": squash_commit,
        "formal_ready": status == "fully_merged",
    }
