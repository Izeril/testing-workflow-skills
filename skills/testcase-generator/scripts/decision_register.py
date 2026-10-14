#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stable review-decision contract shared by review and test-design stages."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, Iterable, List


SCHEMA_VERSION = "1.0.0"
DECISION_TYPES = (
    "accept",
    "accept_with_constraint",
    "reject",
    "defer",
    "not_applicable",
)
PENDING_STATUSES = {"", "pending", "待确认", "待解决", "open"}
REQUIRED_ITEM_FIELDS = (
    "issue_id",
    "status",
    "decision",
    "decision_type",
    "confirmed_by",
    "confirmed_at",
    "affects_test_design",
)


def _review_items(review_data: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    for category in review_data.get("categories", []):
        for item in category.get("items", []):
            if isinstance(item, dict) and item.get("id"):
                yield item


def build_decision_register(
    review_data: Dict[str, Any],
    existing: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build defaults while preserving user-edited decisions by issue id."""
    existing_items = {
        str(item.get("issue_id")): item
        for item in (existing or {}).get("items", [])
        if isinstance(item, dict) and item.get("issue_id")
    }
    items: List[Dict[str, Any]] = []
    for issue in _review_items(review_data):
        issue_id = str(issue["id"])
        previous = existing_items.get(issue_id, {})
        severity = str(issue.get("severity") or "minor").lower()
        item = {
            "issue_id": issue_id,
            "title": str(issue.get("title") or ""),
            "severity": severity,
            "status": previous.get("status", issue.get("status") or "pending"),
            "decision": previous.get("decision", issue.get("decision") or ""),
            "decision_type": previous.get(
                "decision_type", issue.get("decision_type") or ""
            ),
            "confirmed_by": previous.get(
                "confirmed_by", issue.get("confirmed_by") or ""
            ),
            "confirmed_at": previous.get(
                "confirmed_at", issue.get("confirmed_at") or ""
            ),
            "affects_test_design": bool(
                previous.get(
                    "affects_test_design",
                    issue.get("affects_test_design", severity in {"critical", "major"}),
                )
            ),
            "decision_options": list(DECISION_TYPES),
        }
        items.append(item)
    return {"schema_version": SCHEMA_VERSION, "items": items}


def decision_is_complete(item: Dict[str, Any]) -> bool:
    status = str(item.get("status") or "").strip().casefold()
    decision_type = str(item.get("decision_type") or "").strip()
    confirmed_at = str(item.get("confirmed_at") or "").strip()
    valid_date = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", confirmed_at))
    if valid_date:
        try:
            date.fromisoformat(confirmed_at)
        except ValueError:
            valid_date = False
    return (
        status not in PENDING_STATUSES
        and decision_type in DECISION_TYPES
        and bool(str(item.get("decision") or "").strip())
        and bool(str(item.get("confirmed_by") or "").strip())
        and valid_date
    )


def validate_decision_register(payload: Dict[str, Any]) -> Dict[str, Any]:
    gates: List[Dict[str, Any]] = []
    schema_ok = payload.get("schema_version") == SCHEMA_VERSION
    items = payload.get("items")
    items_ok = isinstance(items, list)
    gates.append({
        "gate_id": "R3_SCHEMA",
        "status": "passed" if schema_ok and items_ok else "failed",
        "blocking": not (schema_ok and items_ok),
        "message": "决策登记 schema 有效" if schema_ok and items_ok else "决策登记 schema 无效",
    })

    missing_fields: List[str] = []
    invalid_values: List[str] = []
    unresolved: List[str] = []
    for index, item in enumerate(items if items_ok else []):
        if not isinstance(item, dict):
            missing_fields.append(f"items[{index}]")
            continue
        issue_id = str(item.get("issue_id") or f"items[{index}]")
        missing = [field for field in REQUIRED_ITEM_FIELDS if field not in item]
        if missing:
            missing_fields.append(f"{issue_id}:{','.join(missing)}")
        decision_type = str(item.get("decision_type") or "").strip()
        if decision_type and decision_type not in DECISION_TYPES:
            invalid_values.append(f"{issue_id}:decision_type={decision_type}")
        severe = str(item.get("severity") or "").lower() in {"critical", "major"}
        if severe and not decision_is_complete(item):
            unresolved.append(issue_id)

    gates.append({
        "gate_id": "R3_FIELDS_PRESENT",
        "status": "passed" if not missing_fields else "failed",
        "blocking": bool(missing_fields),
        "message": "确认字段完整" if not missing_fields else "缺少确认字段：" + "；".join(missing_fields[:8]),
    })
    gates.append({
        "gate_id": "R3_VALUE_VALID",
        "status": "passed" if not invalid_values else "failed",
        "blocking": bool(invalid_values),
        "message": "决策字段值有效" if not invalid_values else "非法决策值：" + "；".join(invalid_values[:8]),
    })
    gates.append({
        "gate_id": "R3_DECISION_COMPLETE",
        "status": "passed" if not unresolved else "pending_confirmation",
        "blocking": False,
        "message": f"严重/重要问题待确认 {len(unresolved)} 个",
        "issue_ids": unresolved,
    })
    hard_failures = [gate for gate in gates if gate["blocking"]]
    summary_status = "blocked" if hard_failures else "pending_confirmation" if unresolved else "passed"
    return {
        "schema_version": SCHEMA_VERSION,
        "summary": {
            "status": summary_status,
            "blocking_count": len(hard_failures),
            "pending_confirmation_count": len(unresolved),
        },
        "gates": gates,
        "pending_issue_ids": unresolved,
    }


def finalization_blocking_issues(payload: Dict[str, Any]) -> List[str]:
    """Return decisions that must not enter final XMind or executable cases."""
    blockers = list(testpoint_blocking_issues(payload))
    if blockers:
        return blockers
    for item in payload.get("items", []):
        if not isinstance(item, dict) or not decision_is_complete(item):
            continue
        if item.get("decision_type") == "defer":
            blockers.append(f"{item.get('issue_id', 'unknown')}:defer")
    return blockers

def testpoint_blocking_issues(payload: Dict[str, Any]) -> List[str]:
    """Return severe/important unresolved issues that block testpoint design."""
    report = validate_decision_register(payload)
    hard = [gate["message"] for gate in report["gates"] if gate.get("blocking")]
    if hard:
        return hard
    return list(report.get("pending_issue_ids", []))
