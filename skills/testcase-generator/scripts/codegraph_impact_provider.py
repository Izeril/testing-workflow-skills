#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codegraph query-plan generation and MCP result validation.

The standalone Python workflow cannot invoke an MCP tool directly.  It writes
an explicit query plan, then validates an MCP-produced result on the next run.
No code reads Codegraph's private SQLite schema.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from source_impact_verification import verify_source_impact
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_STOP_PATTERNS = (
    "logger",
    "logging",
    "common.dto",
    "baseentity",
    "tostring",
    "equals",
    "hashcode",
)


def _public_text(value: Any, limit: int = 240) -> str:
    if isinstance(value, dict):
        preferred = [
            value.get(key)
            for key in ("name", "method", "path", "endpoint", "title", "key", "value")
            if value.get(key)
        ]
        text = " | ".join(map(str, preferred)) or json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value or "")
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [{"key": key, "value": item} for key, item in value.items()]
    return [value]


def read_indexed_sha(repo_path: str) -> Tuple[str, str]:
    """Read only stable metadata files; never inspect ``codegraph.db`` tables."""
    root = Path(repo_path) / ".codegraph"
    direct = root / "indexed_sha"
    if direct.is_file():
        return direct.read_text(encoding="utf-8", errors="replace").strip(), str(direct)
    for name in ("metadata.json", "index.json"):
        path = root / name
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for key in ("indexed_sha", "commit_sha", "sha", "head"):
            if payload.get(key):
                return str(payload[key]).strip(), str(path)
    return "", ""


def extract_codegraph_seeds(target: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extract bounded symbol seeds from verified direct-change evidence."""
    analysis = target.get("analysis") or {}
    seeds, seen = [], set()
    fields = (
        ("entry_points", "entry"),
        ("api_endpoints", "api"),
        ("scheduled_tasks", "job"),
        ("business_rules", "rule"),
        ("sql_analysis", "sql"),
        ("config_values", "config"),
    )
    for field, kind in fields:
        for value in _as_list(analysis.get(field)):
            text = _public_text(value)
            key = (kind, text.casefold())
            if text and key not in seen:
                seeds.append({"kind": kind, "symbol": text, "source": f"code_analysis.{field}"})
                seen.add(key)
            if len(seeds) >= 60:
                return seeds
    for item in target.get("selected_files", []) or []:
        text = str(item.get("path") or "")
        key = ("file", text.casefold())
        if text and key not in seen:
            seeds.append({"kind": "file", "symbol": text, "source": "code_change_scope.selected_files"})
            seen.add(key)
    return seeds[:60]


def build_codegraph_query_plan(targets: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    plans = []
    for target in targets:
        requested_mode = str(target.get("analysis_mode") or "uat")
        target_sha = target.get("branch_sha") or (
            target.get("uat_sha")
            if requested_mode == "uat" and target.get("effective_analysis_mode", "uat") == "uat"
            and (target.get("uat_merge_gate") or {}).get("formal_ready")
            else target.get("development_sha")
        )
        indexed_sha, metadata_path = read_indexed_sha(str(target.get("repo_path") or ""))
        plans.append(
            {
                "target_id": target.get("target_id", "unknown"),
                "codegraph_policy": target.get("codegraph_policy", "risk_based"),
                "required_source_files": [item["path"] for item in target.get("raw_changed_files", []) if isinstance(item, dict) and item.get("path")],
                "required_critical_paths": target.get("critical_impact_paths") or [],
                "repo_path": target.get("repo_path", ""),
                "target_sha": target_sha or "",
                "indexed_sha": indexed_sha,
                "index_metadata_path": metadata_path,
                "index_aligned": bool(indexed_sha and indexed_sha == target_sha),
                "max_hops": 2,
                "max_files": 30,
                "max_relations_per_seed": 10,
                "directions": ["callers", "callees", "tests", "data_dependencies"],
                "stop_patterns": list(DEFAULT_STOP_PATTERNS),
                "seeds": extract_codegraph_seeds(target),
            }
        )
    return {
        "schema_version": "1.0.0",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "provider": "codegraph_mcp",
        "targets": plans,
    }


def _load_result(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    source = Path(path)
    if not source.is_file():
        return {}
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _result_by_target(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    items = payload.get("targets") if isinstance(payload.get("targets"), list) else [payload]
    return {
        str(item.get("target_id") or "unknown"): item
        for item in items
        if isinstance(item, dict)
    }


def validate_codegraph_results(
    plan: Dict[str, Any], result_payload: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Use stale indexes for navigation; accept aligned graph or Git-checked source evidence."""
    provided = _result_by_target(result_payload or {})
    results = []
    for target_plan in plan.get("targets", []):
        target_id = target_plan.get("target_id", "unknown")
        item = provided.get(str(target_id), {})
        policy = target_plan.get("codegraph_policy", "risk_based")
        if policy not in {"risk_based", "strict"}:
            raise ValueError("codegraph_policy 必须为 risk_based 或 strict")
        relations = item.get("relations") or item.get("edges") or []
        files = item.get("files") or item.get("affected_files") or []
        invalid_bounds = not isinstance(relations, list) or not isinstance(files, list)
        if not invalid_bounds:
            invalid_bounds = len(files) > int(target_plan.get("max_files", 30)) or len(relations) > 300
            for relation in relations:
                hop = relation.get("hop", 1) if isinstance(relation, dict) else None
                if isinstance(hop, bool) or not isinstance(hop, int) or not 1 <= hop <= 2:
                    invalid_bounds = True
        aligned = bool(target_plan.get("index_aligned"))
        supplied_sha = str(item.get("target_sha") or item.get("indexed_sha") or "")
        if invalid_bounds:
            graph_status, reason = "blocked_result_bounds", "MCP 结果超出两跳或最大文件数边界，或关系格式非法"
        elif not target_plan.get("indexed_sha"):
            graph_status, reason = "index_missing", "未发现可证明 SHA 的索引元数据；可按目标提交补核源码"
        elif not aligned:
            graph_status = "blocked_index_mismatch" if policy == "strict" else "stale_index"
            reason = "索引与冻结分析 SHA 不同，仅作候选定位；使用目标 SHA 源码补核关键影响"
        elif not item or not any(key in item for key in ("relations", "edges", "files", "affected_files")):
            graph_status, reason = "awaiting_mcp_result", "查询计划已生成，等待图查询或关键影响源码补核"
        elif supplied_sha != str(target_plan.get("target_sha") or ""):
            graph_status, reason = "blocked_result_sha_mismatch", "MCP 结果 SHA 与查询计划不一致"
        else:
            graph_status, reason = "ready", ""
        fallback = verify_source_impact(target_plan, item.get("source_verification"))
        status, accepted = graph_status, "graph" if graph_status == "ready" else "none"
        if policy == "risk_based" and graph_status in {"stale_index", "index_missing", "awaiting_mcp_result"} and fallback["status"] == "verified":
            status, accepted = "source_verified", "source"
        accepted_relations = relations if accepted == "graph" else fallback["relations"] if accepted == "source" else []
        accepted_files = files if accepted == "graph" else fallback["verified_files"] if accepted == "source" else []
        results.append({
            "target_id": target_id, "status": status, "reason": reason,
            "codegraph_policy": policy, "graph_status": graph_status,
            "target_sha": target_plan.get("target_sha", ""),
            "indexed_sha": target_plan.get("indexed_sha", ""),
            "accepted_evidence": accepted, "relations": accepted_relations, "files": accepted_files,
            "navigation_relations": relations[:300] if isinstance(relations, list) and not invalid_bounds and accepted == "none" else [],
            "navigation_files": files[:30] if isinstance(files, list) and not invalid_bounds and accepted == "none" else [],
            "source_verification": fallback,
            "uncovered_dynamic_behavior": item.get("uncovered_dynamic_behavior") or [],
        })
    return {
        "schema_version": "1.0.0", "generated_at": datetime.now().isoformat(timespec="seconds"),
        "targets": results,
        "summary": {
            "ready": sum(x["status"] == "ready" for x in results),
            "source_verified": sum(x["status"] == "source_verified" for x in results),
            "blocked": sum(x["status"].startswith("blocked") for x in results),
            "pending": sum(x["status"] in {"index_missing", "awaiting_mcp_result"} for x in results),
            "stale": sum(x["graph_status"] == "stale_index" for x in results),
        },
    }



def prepare_codegraph_artifacts(
    output_dir: Path,
    targets: List[Dict[str, Any]],
    result_path: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Write query plan and validated impact artifacts, then tag each target."""
    internal_dir = Path(output_dir) / ".ai-internal"
    internal_dir.mkdir(parents=True, exist_ok=True)
    plan = build_codegraph_query_plan(targets)
    result = validate_codegraph_results(plan, _load_result(result_path))
    (internal_dir / "codegraph_query_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (internal_dir / "codegraph_impact.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    by_target = {item["target_id"]: item for item in result.get("targets", [])}
    for target in targets:
        graph = by_target.get(target.get("target_id", "unknown"), {})
        target["codegraph_status"] = graph.get("status", "index_missing")
        target["codegraph_indexed_sha"] = graph.get("indexed_sha", "")
        target["impact_evidence_source"] = graph.get("accepted_evidence", "none")
        target["impact_source_verification"] = graph.get("source_verification", {})
    return plan, result
