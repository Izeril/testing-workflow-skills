#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Translate source evidence into traceable, tester-readable business impacts."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Tuple

from business_impact_gate import classify_coverage_gaps


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [{"key": key, "value": item} for key, item in value.items()]
    return [value]


def _text(value: Any, limit: int = 360) -> str:
    if isinstance(value, dict):
        preferred = [
            value.get(key)
            for key in ("title", "name", "path", "endpoint", "method", "key", "value", "condition")
            if value.get(key)
        ]
        raw = " | ".join(map(str, preferred)) or json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        raw = str(value or "")
    return re.sub(r"\s+", " ", raw).strip()[:limit]


def _stable_id(prefix: str, value: str, index: int) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8].upper()
    return f"{prefix}-{index + 1:03d}-{digest}"


def _coverage_complete(analysis: Dict[str, Any]) -> bool:
    coverage = analysis.get("technology_coverage") or {}
    return bool(coverage.get("complete")) and not coverage.get("gaps")


def _coverage_formally_ready(target: Dict[str, Any]) -> bool:
    """Allow evidenced MyBatis partial coverage while rejecting real gaps."""
    critical, _accepted_partial = classify_coverage_gaps(target)
    return not critical


def _graph_for_target(codegraph_impact: Dict[str, Any], target_id: str) -> Dict[str, Any]:
    return next(
        (
            item
            for item in codegraph_impact.get("targets", [])
            if str(item.get("target_id")) == str(target_id)
        ),
        {},
    )


def _matching_requirement_ids(requirement_facts: Dict[str, Any], text: str) -> List[str]:
    tokens = {token.casefold() for token in re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]{2,}", text)}
    matched = []
    for fact in requirement_facts.get("items", []):
        content = _text(fact.get("content"))
        fact_tokens = {
            token.casefold()
            for token in re.findall(r"[A-Za-z0-9_\u4e00-\u9fff]{2,}", content)
        }
        if tokens & fact_tokens:
            matched.append(str(fact.get("fact_id")))
    if matched:
        return matched[:5]
    fallback = [str(item.get("fact_id")) for item in requirement_facts.get("items", []) if item.get("fact_id")]
    return fallback[:2]


def _actors(requirement_facts: Dict[str, Any], target: Dict[str, Any]) -> List[str]:
    values = []
    for item in requirement_facts.get("items", []):
        if item.get("dimension") in {"actors", "users", "roles"}:
            values.append(_text(item.get("content")))
    if not values:
        values.extend(str(value) for value in target.get("services", []) if value)
    return list(dict.fromkeys(values)) or ["需求未明确的目标用户或调用系统"]


def translate_business_impacts(
    requirement_facts: Dict[str, Any],
    target_results: List[Dict[str, Any]],
    codegraph_impact: Dict[str, Any],
    project_info: Dict[str, Any],
) -> Dict[str, Any]:
    """Create ``RQ → CE/CG/BR → BI`` evidence chains without invented behavior."""
    impacts, evidence_catalog = [], []
    for target in target_results:
        analysis = target.get("analysis") or {}
        if not analysis:
            continue
        target_id = str(target.get("target_id") or "unknown")
        graph = _graph_for_target(codegraph_impact, target_id)
        uat_gate = target.get("uat_merge_gate") or {}
        formal_ready = (
            str(target.get("analysis_mode") or "uat") == "uat"
            and uat_gate.get("status") == "fully_merged"
            and graph.get("status") == "ready"
        )
        direct_objects: List[Tuple[str, Any]] = []
        for field, kind in (
            ("api_endpoints", "接口入口"),
            ("scheduled_tasks", "定时任务"),
            ("entry_points", "业务入口"),
            ("business_rules", "业务规则"),
        ):
            direct_objects.extend((kind, item) for item in _as_list(analysis.get(field)))
        if not direct_objects:
            direct_objects = [
                ("代码变更", {"name": path})
                for path in _as_list(analysis.get("files_changed"))[:10]
            ]

        rules = [_text(item) for item in _as_list(analysis.get("business_rules")) if _text(item)]
        sql = [_text(item) for item in _as_list(analysis.get("sql_analysis")) if _text(item)]
        configs = [_text(item) for item in _as_list(analysis.get("config_values")) if _text(item)]
        warnings = [_text(item) for item in _as_list(analysis.get("analysis_warnings")) if _text(item)]
        graph_relations = graph.get("relations") or []

        for kind, raw in direct_objects[:30]:
            object_text = _text(raw)
            if not object_text:
                continue
            ce_id = _stable_id("CE", f"{target_id}:{kind}:{object_text}", len(evidence_catalog))
            evidence_catalog.append(
                {
                    "evidence_id": ce_id,
                    "type": "code_change",
                    "target_id": target_id,
                    "source": object_text,
                    "development_sha": target.get("development_sha", ""),
                    "uat_sha": target.get("uat_sha", ""),
                }
            )
            evidence_ids = _matching_requirement_ids(requirement_facts, object_text) + [ce_id]
            if rules:
                br_id = _stable_id("BR", f"{target_id}:{rules[0]}", len(evidence_catalog))
                evidence_catalog.append(
                    {
                        "evidence_id": br_id,
                        "type": "business_rule",
                        "target_id": target_id,
                        "source": rules[0],
                    }
                )
                evidence_ids.append(br_id)
            if graph_relations:
                cg_id = _stable_id(
                    "CG", f"{target_id}:{_text(graph_relations[0])}", len(evidence_catalog)
                )
                evidence_catalog.append(
                    {
                        "evidence_id": cg_id,
                        "type": "call_chain",
                        "target_id": target_id,
                        "source": graph_relations[0],
                    }
                )
                evidence_ids.append(cg_id)

            if formal_ready and _coverage_formally_ready(target):
                confidence = "confirmed"
            elif _coverage_complete(analysis) and kind in {"接口入口", "定时任务", "业务入口"}:
                confidence = "high"
            elif object_text:
                confidence = "medium"
            else:
                confidence = "potential"

            scenario = str(project_info.get("document_title") or project_info.get("project_name") or "目标业务流程")
            impact_id = _stable_id("BI", f"{target_id}:{kind}:{object_text}", len(impacts))
            impacts.append(
                {
                    "impact_id": impact_id,
                    "target_id": target_id,
                    "business_domain": scenario,
                    "affected_actor": _actors(requirement_facts, target),
                    "business_scenario": f"{scenario}中的{kind}场景",
                    "trigger": object_text,
                    "preconditions": configs[:3] or ["满足需求文档声明的业务状态、参数和环境条件"],
                    "previous_behavior": "基线行为未形成可比较证据，需结合基线源码或历史用例确认",
                    "new_behavior": f"{kind}按本次代码证据执行：{object_text}",
                    "business_rule": rules[:5] or ["未提取到可稳定表达的业务规则"],
                    "data_impact": sql[:5],
                    "state_impact": [
                        rule for rule in rules
                        if any(word in rule.casefold() for word in ("status", "state", "状态"))
                    ][:5],
                    "downstream_impact": [
                        _text(item) for item in graph_relations[:5] if _text(item)
                    ],
                    "exception_impact": warnings[:5] or [
                        rule for rule in rules
                        if any(word in rule.casefold() for word in ("throw", "raise", "异常", "失败"))
                    ][:5],
                    "regression_scope": list(
                        dict.fromkeys(
                            [object_text]
                            + [_text(item.get("path")) for item in target.get("selected_files", [])[:10]]
                            + [_text(item) for item in graph.get("files", [])[:10]]
                        )
                    ),
                    "confidence": confidence,
                    "evidence_ids": list(dict.fromkeys(evidence_ids)),
                }
            )

    requested_modes = {
        str(target.get("analysis_mode") or "uat") for target in target_results
    }
    analysis_mode = "uat_confirmed" if impacts and all(
        impact.get("confidence") == "confirmed" for impact in impacts
    ) else "development_preanalysis" if "development" in requested_modes or impacts else "uat_blocked"
    return {
        "schema_version": "1.0.0",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "analysis_mode": analysis_mode,
        "items": impacts,
        "evidence_catalog": evidence_catalog,
        "summary": {
            "total": len(impacts),
            "confirmed": sum(1 for item in impacts if item["confidence"] == "confirmed"),
            "high": sum(1 for item in impacts if item["confidence"] == "high"),
            "medium": sum(1 for item in impacts if item["confidence"] == "medium"),
            "potential": sum(1 for item in impacts if item["confidence"] == "potential"),
        },
    }
