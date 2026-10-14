#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only, targeted Git snapshot collection for dev/master/UAT refs."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime
from typing import Any, Dict, Iterable, List, Tuple

from workflow_git_runner import run_git


def _git(repo_path: str, args: List[str], timeout: int = 90) -> Tuple[int, str, str]:
    """Run Git with an argument list and no interactive credential prompt."""
    result = run_git(
        repo_path, args, timeout=timeout,
        allow_fetch=bool(args and args[0] == "fetch"), executor=subprocess.run,
    )
    return result.returncode, result.stdout, result.stderr


def normalize_remote_ref(ref: str) -> str:
    """Normalize a branch name to the cached origin ref used for analysis."""
    value = str(ref or "").strip().replace("\\", "/")
    if not value:
        return ""
    if value.startswith("refs/remotes/"):
        return value.removeprefix("refs/remotes/")
    if value.startswith("origin/"):
        return value
    if value.startswith("refs/heads/"):
        value = value.removeprefix("refs/heads/")
    return f"origin/{value}"


def _short_branch(ref: str) -> str:
    normalized = normalize_remote_ref(ref)
    return normalized.removeprefix("origin/")


def _commit(repo_path: str, ref: str) -> str:
    if not ref:
        return ""
    code, stdout, _ = _git(repo_path, ["rev-parse", "--verify", f"{ref}^{{commit}}"], 20)
    return stdout.strip() if code == 0 else ""


def _snapshot(repo_path: str, refs: Iterable[str]) -> Dict[str, str]:
    return {ref: _commit(repo_path, ref) for ref in refs if ref}


def capture_git_snapshot(target: Dict[str, Any]) -> Dict[str, Any]:
    """Refresh only required refs and record before/after SHA evidence.

    A single fetch carries all requested refspecs.  If that fetch fails after
    changing any ref, ``partial_update`` is raised and formal analysis is
    closed rather than mixing old and new SHAs.
    """
    repo_path = str(target.get("repo_path") or "")
    analysis_mode = str(target.get("analysis_mode") or "uat").lower()
    dev_ref = normalize_remote_ref(target.get("branch", ""))
    baseline_ref = normalize_remote_ref(target.get("baseline", "origin/master"))
    uat_ref = normalize_remote_ref(target.get("uat_branch", "origin/uat_merge"))
    required_refs = [dev_ref, baseline_ref]
    if analysis_mode == "uat":
        required_refs.append(uat_ref)
    refs = list(dict.fromkeys(ref for ref in required_refs if ref))
    before = _snapshot(repo_path, refs)
    refresh = bool(target.get("refresh_remote_refs", True))

    payload = {
        "schema_version": "1.0.0",
        "target_id": target.get("target_id", "unknown"),
        "analysis_mode": analysis_mode,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "fetch_requested": refresh,
        "fetch_status": "skipped",
        "development_ref": dev_ref,
        "baseline_ref": baseline_ref,
        "uat_ref": uat_ref,
        "refs_before": before,
        "refs_after": dict(before),
        "partial_update": False,
        "remote_ref_changes": {},
    }

    if refresh:
        refspecs = [
            f"+refs/heads/{_short_branch(ref)}:refs/remotes/{ref}"
            for ref in refs
        ]
        args = ["fetch", "--no-tags", "origin", *refspecs]
        payload["fetch_command"] = "git " + " ".join(args)
        try:
            code, _, stderr = _git(repo_path, args)
        except (OSError, subprocess.TimeoutExpired) as exc:
            code, stderr = 1, str(exc)
        after = _snapshot(repo_path, refs)
        changes = {
            ref: {"before": before.get(ref, ""), "after": after.get(ref, "")}
            for ref in refs
            if before.get(ref, "") != after.get(ref, "")
        }
        payload.update(
            refs_after=after,
            remote_ref_changes=changes,
            partial_update=bool(code and changes),
        )
        if code:
            payload.update(fetch_status="failed", error=stderr.strip() or "定向刷新失败")
        else:
            payload["fetch_status"] = "succeeded"

    after = payload["refs_after"]
    payload.update(
        development_sha=after.get(dev_ref, ""),
        baseline_sha=after.get(baseline_ref, ""),
        uat_sha=after.get(uat_ref, ""),
    )
    missing = [
        label
        for label, ref in (
            ("development", dev_ref),
            ("baseline", baseline_ref),
            ("uat", uat_ref if analysis_mode == "uat" else ""),
        )
        if ref and not after.get(ref)
    ]
    payload["missing_refs"] = missing
    payload["snapshot_status"] = (
        "verified"
        if refresh and payload["fetch_status"] == "succeeded" and not missing
        else "cached_unverified"
        if not refresh and not missing
        else "blocked"
    )
    payload["formal_ready"] = (
        analysis_mode == "uat"
        and payload["snapshot_status"] == "verified"
        and not payload["partial_update"]
        and bool(payload["uat_sha"])
    )
    return payload
