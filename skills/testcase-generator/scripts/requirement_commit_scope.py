#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve a requirement-level commit and file scope without keyword filtering."""

from __future__ import annotations

import os
import subprocess
from typing import Any, Dict, Iterable, List, Tuple

from workflow_git_runner import run_git


def _git(repo_path: str, args: List[str], timeout: int = 60) -> Tuple[int, str, str]:
    result = run_git(repo_path, args, timeout=timeout, executor=subprocess.run)
    return result.returncode, result.stdout, result.stderr


def _parse_name_status(text: str) -> List[Dict[str, str]]:
    files = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        files.append(
            {
                "status": status,
                "path": parts[-1],
                "old_path": parts[1] if status.startswith("R") and len(parts) > 2 else "",
            }
        )
    return files


def _unique_files(items: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    result, positions = [], {}
    for item in items:
        path = item.get("path", "")
        if not path:
            continue
        if path in positions:
            result[positions[path]] = item
        else:
            positions[path] = len(result)
            result.append(item)
    return result


def resolve_requirement_commit_scope(
    repo_path: str,
    target: Dict[str, Any],
    snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    """Build the exact Git scope and fail closed for an ambiguous shared branch."""
    baseline = snapshot.get("baseline_sha") or snapshot.get("baseline_ref") or target.get("baseline")
    development = snapshot.get("development_sha") or snapshot.get("development_ref") or target.get("branch")
    explicit_range = str(target.get("commit_range") or "").strip()
    requirement_id = str(target.get("requirement_id") or "").strip()
    confirmed_files = target.get("changed_files") or []

    if target.get("shared_branch") and not (
        explicit_range or requirement_id or confirmed_files or target.get("pull_request") or target.get("pr")
    ):
        return {
            "target_id": target.get("target_id", "unknown"),
            "status": "blocked_ambiguous_commit_scope",
            "reason": "共享分支缺少提交范围、稳定需求编号、PR 或确认文件清单",
        }

    strategy = "explicit_commit_range" if explicit_range else "independent_branch"
    comparison_range = explicit_range or f"{baseline}...{development}"
    commit_range = explicit_range or f"{baseline}..{development}"

    code, stdout, stderr = _git(repo_path, ["rev-list", "--reverse", commit_range])
    if code:
        return {
            "target_id": target.get("target_id", "unknown"),
            "status": "blocked_ambiguous_commit_scope",
            "reason": stderr.strip() or f"无法解析提交范围 {commit_range}",
        }
    commits = [line.strip() for line in stdout.splitlines() if line.strip()]

    # A shared branch may be scoped by a stable requirement ID in every selected
    # commit.  We select matching commits explicitly; the ID is not used to
    # delete arbitrary changed files from an otherwise accepted Git range.
    selected_commits = commits
    if target.get("shared_branch") and requirement_id and not explicit_range:
        selected_commits = []
        commit_files = []
        for commit in commits:
            _, subject, _ = _git(repo_path, ["show", "-s", "--format=%s", commit], 20)
            if requirement_id.casefold() not in subject.casefold():
                continue
            selected_commits.append(commit)
            _, changed, _ = _git(
                repo_path, ["diff-tree", "--root", "--no-commit-id", "--name-status", "-r", commit]
            )
            commit_files.extend(_parse_name_status(changed))
        if not selected_commits:
            return {
                "target_id": target.get("target_id", "unknown"),
                "status": "blocked_ambiguous_commit_scope",
                "reason": f"共享分支中没有提交信息包含稳定需求编号 {requirement_id}",
            }
        raw_files = _unique_files(commit_files)
        strategy = "requirement_id_commits"
    elif confirmed_files:
        raw_files = [
            item if isinstance(item, dict) else {"path": str(item), "status": "M", "old_path": ""}
            for item in confirmed_files
        ]
        strategy = "confirmed_file_manifest"
    else:
        code, diff, stderr = _git(repo_path, ["diff", "--name-status", comparison_range])
        if code:
            return {
                "target_id": target.get("target_id", "unknown"),
                "status": "blocked_ambiguous_commit_scope",
                "reason": stderr.strip() or f"无法读取变更范围 {comparison_range}",
            }
        raw_files = _parse_name_status(diff)

    code, merge_base, _ = _git(repo_path, ["merge-base", baseline, development], 20)
    return {
        "schema_version": "1.0.0",
        "target_id": target.get("target_id", "unknown"),
        "status": "admitted",
        "strategy": strategy,
        "requirement_id": requirement_id,
        "pull_request": target.get("pull_request") or target.get("pr") or "",
        "commit_range": commit_range,
        "comparison_range": comparison_range,
        "comparison_command": f"git diff --name-status {comparison_range}",
        "merge_base_sha": merge_base.strip() if code == 0 else "",
        "commits": selected_commits,
        "raw_changed_files": raw_files,
        "changed_file_count": len(raw_files),
    }
