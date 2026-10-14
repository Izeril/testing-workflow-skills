"""Resolve local targets and prepare zero-configuration GitNexus targets."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .requirement_ingestion import extract_branches, extract_repo_keys, extract_services, infer_language


def _target_id(value: str, fallback: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value or "").strip("-").lower() or fallback


def _as_list(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [item.strip() for item in re.split(r"[、,，/]+", value) if item.strip()]
    return [str(item).strip() for item in value if str(item).strip()]


def _registry(repo_config: Dict[str, Any], *, remote: bool = False) -> List[Dict[str, Any]]:
    result = []
    for index, raw in enumerate(repo_config.get("targets") or repo_config.get("repositories") or [], 1):
        if not isinstance(raw, dict):
            continue
        repo_key = raw.get("repo_key") or raw.get("key")
        repo_path = raw.get("path") or raw.get("repo_path")
        if remote and not repo_key:
            continue
        if not remote and not repo_path:
            continue
        result.append({
            "target_id": raw.get("target_id") or raw.get("key") or repo_key or f"registry-{index}",
            "repo_key": repo_key or "",
            "repo_path": repo_path or "",
            "baseline": raw.get("baseline") or repo_config.get("baseline") or "origin/master",
            "languages": [item.casefold() for item in _as_list(raw.get("languages") or raw.get("language"))],
            "services": _as_list(raw.get("services") or raw.get("service")),
        })
    return result


def _load_explicit(code_targets: Any) -> List[Dict[str, Any]]:
    if not code_targets:
        return []
    payload = code_targets
    if isinstance(code_targets, (str, Path)):
        payload = json.loads(Path(code_targets).read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        payload = payload.get("targets") or payload.get("code_targets") or []
    if not isinstance(payload, list):
        raise ValueError("code_targets 必须是数组或包含 targets 数组的 JSON")
    return [dict(item) for item in payload if isinstance(item, dict)]


def resolve_code_targets(
    release_docs: List[Dict[str, Any]],
    repo_config: Optional[Dict[str, Any]] = None,
    *,
    repo_path: Optional[str] = None,
    branch: Optional[str] = None,
    baseline: Optional[str] = None,
    code_targets: Any = None,
    provider_name: str = "local_git",
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Resolve local paths or leave remote repo discovery to GitNexus.

    In GitNexus mode no repository registry is required.  A target carries
    exact repository candidates from the release document when available;
    otherwise it carries service selectors for strict online discovery.
    """
    repo_config = dict(repo_config or {})
    remote = str(provider_name or "local_git").casefold() == "gitnexus"
    registry = _registry(repo_config, remote=remote)
    errors: List[str] = []
    explicit = _load_explicit(code_targets)
    if explicit:
        targets = []
        for index, raw in enumerate(explicit, 1):
            target = dict(raw)
            target["target_id"] = _target_id(str(target.get("target_id") or target.get("repo_key") or target.get("service") or target.get("branch") or ""), f"explicit-{index}")
            target["repo_path"] = target.get("repo_path") or target.get("path")
            target["repo_key"] = target.get("repo_key") or target.get("repository") or target.get("repo") or ""
            target["services"] = _as_list(target.get("services") or target.get("service"))
            target["branch"] = branch or target.get("branch")
            target["baseline"] = target.get("baseline") or baseline or repo_config.get("baseline") or "origin/master"
            target["mapping_source"] = "explicit" if target.get("repo_key") or not remote else "gitnexus_auto_discovery"
            target["branch_source"] = "explicit_override" if branch else "code_targets"
            valid = bool(target.get("branch") and (target.get("repo_key") or target.get("services"))) if remote else bool(target.get("repo_path") and target.get("branch"))
            target["status"] = "pending" if valid else "blocked"
            if not valid:
                target["block_reason"] = "显式代码目标缺少 branch 及 repo_key/services" if remote else "显式代码目标缺少 repo_path 或 branch"
            targets.append(target)
        return targets, errors

    candidates: List[Dict[str, Any]] = []
    for index, doc in enumerate(release_docs, 1):
        path = Path(doc["path"])
        content = doc["content"]
        branches = extract_branches(content)
        services = extract_services(content)
        repo_candidates = extract_repo_keys(content)
        language = infer_language(path, content)
        candidate = {
            "target_id": _target_id(f"{language}-{services[0] if services else (branches[0] if branches else '')}", f"doc-{index}"),
            "source_doc": doc.get("relative_path") or path.name,
            "source_sha256": doc.get("sha256", ""),
            "services": services,
            "repo_candidates": repo_candidates,
            "repo_key": repo_candidates[0] if len(repo_candidates) == 1 else "",
            "language": language,
            "branch_candidates": branches,
            "branch": branch or (branches[0] if len(branches) == 1 else ""),
            "branch_source": "explicit_override" if branch else "release_test_document",
            "baseline": baseline or repo_config.get("baseline") or "origin/master",
            "mapping_source": "document_repo" if len(repo_candidates) == 1 else ("gitnexus_auto_discovery" if remote else "document"),
            "status": "pending",
        }
        if not branch and len(branches) > 1:
            candidate.update(status="blocked", block_reason="ambiguous_branch")
        elif not candidate["branch"]:
            candidate.update(status="blocked", block_reason="branch_missing")
        elif remote and not candidate["repo_key"] and not candidate["services"]:
            candidate.update(status="blocked", block_reason="service_missing_for_auto_discovery")
        candidates.append(candidate)

    if not candidates and branch and not remote and (repo_path or repo_config.get("path")):
        candidates.append({
            "target_id": "explicit-single", "source_doc": "explicit arguments", "services": [], "language": "",
            "branch_candidates": [branch], "branch": branch, "branch_source": "explicit_override",
            "baseline": baseline or repo_config.get("baseline") or "origin/master",
            "repo_path": repo_path or repo_config.get("path"), "mapping_source": "explicit", "status": "pending",
        })

    allow_default = not remote and len(candidates) == 1 and bool(repo_path or repo_config.get("path"))
    seen = set()
    for candidate in candidates:
        base = candidate["target_id"]
        suffix = 2
        while candidate["target_id"] in seen:
            candidate["target_id"] = f"{base}-{suffix}"
            suffix += 1
        seen.add(candidate["target_id"])
        if candidate["status"] == "blocked":
            continue
        matched = []
        for registered in registry:
            language_ok = not registered["languages"] or not candidate["language"] or candidate["language"].casefold() in registered["languages"]
            service_ok = bool(set(candidate["services"]) & set(registered["services"])) if candidate["services"] and registered["services"] else not candidate["services"] and not registered["services"]
            if language_ok and service_ok:
                matched.append(registered)
        if len(matched) == 1:
            selected = matched[0]
            candidate.update(repo_path=selected["repo_path"], repo_key=selected["repo_key"], baseline=baseline or selected["baseline"], registry_target_id=selected["target_id"], mapping_source="registry")
        elif len(matched) > 1:
            candidate.update(status="blocked", block_reason="ambiguous_repository_mapping")
        elif allow_default:
            candidate.update(repo_path=repo_path or repo_config.get("path"), mapping_source="single_repository_default")
        elif not remote:
            candidate.update(status="blocked", block_reason="repository_mapping_missing")
        elif candidate.get("repo_key") or candidate.get("services"):
            candidate.update(status="pending", mapping_source=candidate.get("mapping_source") or "gitnexus_auto_discovery")
        else:
            candidate.update(status="blocked", block_reason="service_missing_for_auto_discovery")
        valid = bool(candidate.get("branch") and (candidate.get("repo_key") or candidate.get("services"))) if remote else bool(candidate.get("repo_path") and candidate.get("branch"))
        if not valid and candidate.get("status") != "blocked":
            candidate.update(status="blocked", block_reason="service_missing_for_auto_discovery" if remote else "repo_path_missing")
    return candidates, errors
