#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict admission checks for requirement-scoped code analysis.

The module intentionally performs no Git mutation.  It only validates whether
the target definition is complete enough to proceed to snapshot collection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict


ADMISSION_STATUSES = {
    "admitted",
    "skipped_missing_branch",
    "blocked_ambiguous_repo",
    "blocked_ambiguous_commit_scope",
    "blocked_remote_refresh",
    "blocked_unsupported_target",
}


def assess_code_analysis_admission(target: Dict[str, Any]) -> Dict[str, Any]:
    """Return an auditable admission decision for one service/repository target."""
    target_id = str(target.get("target_id") or "unknown")
    checks = []

    def check(name: str, passed: bool, detail: str) -> None:
        checks.append(
            {
                "name": name,
                "status": "passed" if passed else "failed",
                "detail": detail,
            }
        )

    if target.get("status") == "blocked":
        return {
            "target_id": target_id,
            "status": "blocked_ambiguous_repo",
            "admitted": False,
            "reason": target.get("block_reason") or "代码目标已被上游映射门禁阻断",
            "checks": checks,
        }

    repo_path = str(target.get("repo_path") or "").strip()
    branch = str(target.get("branch") or "").strip()
    baseline = str(target.get("baseline") or "").strip()
    analysis_mode = str(target.get("analysis_mode") or "uat").lower()
    uat_branch = str(target.get("uat_branch") or "").strip()

    repo_exists = bool(repo_path and Path(repo_path).is_dir())
    repo_is_git = bool(repo_exists and (Path(repo_path) / ".git").exists())
    check("repository_exists", repo_exists, repo_path or "未配置仓库路径")
    check("repository_is_git", repo_is_git, "目标必须是可读取的 Git 仓库")
    check("development_branch", bool(branch), branch or "提测材料缺少开发分支")
    check("baseline", bool(baseline), baseline or "未配置基线分支")
    check(
        "uat_branch",
        analysis_mode != "uat" or bool(uat_branch),
        uat_branch or "正式 UAT 分析必须配置 uat_branch",
    )

    if not branch:
        status, reason = "skipped_missing_branch", "缺少开发分支，跳过代码分析"
    elif not repo_path or not repo_exists:
        status, reason = "blocked_ambiguous_repo", "代码仓库不存在、未配置或无法唯一映射"
    elif not repo_is_git or not baseline or (analysis_mode == "uat" and not uat_branch):
        status, reason = "blocked_unsupported_target", "仓库、基线或 UAT 目标不满足分析条件"
    elif target.get("shared_branch") and not any(
        target.get(key)
        for key in ("commit_range", "pull_request", "pr", "requirement_id", "changed_files")
    ):
        status, reason = (
            "blocked_ambiguous_commit_scope",
            "共享开发分支未提供提交范围、PR、稳定需求编号或确认文件清单",
        )
    else:
        status, reason = "admitted", ""

    return {
        "target_id": target_id,
        "status": status,
        "admitted": status == "admitted",
        "analysis_mode": analysis_mode,
        "reason": reason,
        "checks": checks,
    }
