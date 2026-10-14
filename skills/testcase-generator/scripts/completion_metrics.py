"""Requested-scope, full-workflow, and explicit human-confirmation metrics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


def _rate(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _load_register(output_dir: Path) -> tuple[bool, dict[str, Any]]:
    path = Path(output_dir) / ".ai-internal" / "decision_register.json"
    if not path.exists():
        return False, {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return True, {}
    return True, payload if isinstance(payload, dict) else {}


def collect_completion_metrics(
    output_dir: Path,
    trace: dict[str, Any],
    run_id: str,
    execution: dict[str, Any],
    finalization_blocking_issues: Callable[[dict[str, Any]], list[str]],
) -> dict[str, Any]:
    """Separate the current request target from the seven-stage lifecycle."""
    events = [
        event for event in trace.get("events", [])
        if isinstance(event, dict) and run_id and event.get("run_id") == run_id
    ]
    latest_by_step: dict[str, dict[str, Any]] = {}
    for event in events:
        latest_by_step[str(event.get("step", ""))] = event
    requested_count = len(latest_by_step)
    requested_passed = sum(
        1 for event in latest_by_step.values() if event.get("status") == "passed"
    )

    stage_status = {
        row.get("step"): row.get("last_status", "not_started")
        for row in execution.get("stages", [])
    }
    register_exists, register = _load_register(output_dir)
    decision_complete = bool(register_exists and register) and not finalization_blocking_issues(register)
    review_reached = stage_status.get("review") == "passed"
    finalize_complete = stage_status.get("finalize") == "passed"
    archive_complete = stage_status.get("archive") == "passed"

    gates = [
        {
            "gate": "review_decisions",
            "name": "评审问题最终决策",
            "status": "completed" if decision_complete else "awaiting" if review_reached else "not_reached",
        },
        {
            "gate": "final_xmind_confirmation",
            "name": "评审 XMind 终稿确认",
            "status": "completed" if finalize_complete else "awaiting" if decision_complete else "not_reached",
        },
        {
            "gate": "archive_confirmation",
            "name": "最终用例确认并归档",
            "status": "completed" if archive_complete else "awaiting" if finalize_complete else "not_reached",
        },
    ]
    human_completed = sum(1 for gate in gates if gate["status"] == "completed")
    if human_completed == len(gates):
        human_status = "complete"
    else:
        awaiting = next((gate["gate"] for gate in gates if gate["status"] == "awaiting"), None)
        human_status = f"awaiting_{awaiting}" if awaiting else "not_reached"

    full = float(execution.get("summary", {}).get("workflow_completion_rate", 0) or 0)
    return {
        "summary": {
            "requested_scope_stage_count": requested_count,
            "requested_scope_passed_stage_count": requested_passed,
            "requested_scope_completion": _rate(requested_passed, requested_count),
            "full_workflow_completion": full,
            "human_confirmation_completion": _rate(human_completed, len(gates)),
            "human_confirmation_status": human_status,
        },
        "human_confirmation": {
            "completed_count": human_completed,
            "gate_count": len(gates),
            "completion_rate": _rate(human_completed, len(gates)),
            "status": human_status,
            "gates": gates,
        },
    }
