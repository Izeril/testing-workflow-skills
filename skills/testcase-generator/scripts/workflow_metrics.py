"""Aggregate workflow execution and artifact metrics."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from decision_register import decision_is_complete, finalization_blocking_issues
from completion_metrics import collect_completion_metrics


WORKFLOW_VERSION = "testcase-generator-2026.07"
STAGES = (
    ("check", "前置检查"),
    ("analysis", "需求分析"),
    ("review", "需求评审"),
    ("testpoint", "测试点生成"),
    ("finalize", "评审终稿确认"),
    ("refine", "测试用例细化"),
    ("archive", "知识库归档"),
)


def _load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return default


def _rate(numerator: int | float, denominator: int | float) -> float:
    return round(float(numerator) / float(denominator), 4) if denominator else 0.0


def _latest_json(internal_dir: Path, pattern: str) -> dict[str, Any]:
    files = sorted(
        internal_dir.glob(pattern),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for path in files:
        payload = _load_json(path, {})
        if isinstance(payload, dict):
            return payload
    return {}


def _review_issue_metrics(internal_dir: Path) -> dict[str, Any]:
    payload = _latest_json(internal_dir, "*评审数据.json")
    summary = payload.get("summary", {})
    items = [
        item
        for category in payload.get("categories", [])
        for item in category.get("items", [])
        if isinstance(item, dict)
    ]
    decision_register = _load_json(internal_dir / "decision_register.json", {})
    registered_items = decision_register.get("items", []) if isinstance(decision_register, dict) else []
    if isinstance(registered_items, list) and registered_items:
        resolved = sum(
            1 for item in registered_items
            if isinstance(item, dict) and decision_is_complete(item)
        )
        total = len(registered_items)
    else:
        resolved = sum(
            1
            for item in items
            if item.get("status") not in {"", "待解决", "待确认", "pending", None}
            or bool(str(item.get("decision", "")).strip())
        )
        total = int(summary.get("total_issues", len(items)) or 0)
    return {
        "issue_count": total,
        "critical_count": int(summary.get("critical", 0) or 0),
        "major_count": int(summary.get("major", 0) or 0),
        "minor_count": int(summary.get("minor", 0) or 0),
        "resolved_count": resolved,
        "resolution_rate": _rate(resolved, total),
    }


def _review_change_metrics(internal_dir: Path, original_count: int) -> dict[str, Any]:
    preview = _load_json(internal_dir / "评审差异预览报告.json", {})
    stats = preview.get("statistics", {}) if isinstance(preview, dict) else {}
    source = "review_diff_preview"
    if not stats:
        backflow = _load_json(internal_dir / "xmind_backflow_report.json", {})
        raw = backflow.get("statistics", {}) if isinstance(backflow, dict) else {}
        added = int(raw.get("flag_green_added", 0) or 0)
        deleted = int(raw.get("flag_red_deleted", 0) or 0)
        stats = {
            "added": added,
            "deleted": deleted,
            "modified": 0,
            "normal": max(int(raw.get("testpoint_nodes", 0) or 0) - added - deleted, 0),
            "warning_count": raw.get("warning_count", 0),
        }
        source = "xmind_backflow_report" if raw else "unavailable"
    added = int(stats.get("added", 0) or 0)
    deleted = int(stats.get("deleted", 0) or 0)
    modified = int(stats.get("modified", 0) or 0)
    unchanged = int(stats.get("normal", 0) or 0)
    changed = added + deleted + modified
    denominator = original_count or changed + unchanged
    return {
        "source": source,
        "original_testpoint_count": original_count,
        "added_count": added,
        "deleted_count": deleted,
        "modified_count": modified,
        "unchanged_count": unchanged,
        "changed_count": changed,
        "modification_rate": _rate(changed, denominator),
        "warning_count": int(stats.get("warning_count", 0) or 0),
    }


def collect_artifact_metrics(output_dir: Path) -> dict[str, Any]:
    """Collect stable counts from workflow JSON artifacts."""
    internal_dir = Path(output_dir) / ".ai-internal"
    testpoint_payload = _load_json(internal_dir / "testpoints.json", {})
    testpoints = testpoint_payload.get("test_points", []) if isinstance(testpoint_payload, dict) else []
    p1_count = sum(1 for item in testpoints if item.get("priority") == "P1")

    facts = _load_json(internal_dir / "requirement_facts.json", {})
    fact_items = facts.get("items", []) if isinstance(facts, dict) else []

    coverage = _load_json(internal_dir / "coverage_matrix.json", {})
    coverage_stats = coverage.get("statistics", {}) if isinstance(coverage, dict) else {}

    gate_report = _load_json(internal_dir / "quality_gate_report.json", {})
    gates = gate_report.get("gates", []) if isinstance(gate_report, dict) else []
    passed_gates = sum(1 for gate in gates if gate.get("status") == "passed")
    applicable_gates = [g for g in gates if g.get("status") != "skipped"]

    testcase_payload = _load_json(internal_dir / "测试用例.json", {})
    testcases = testcase_payload.get("testcases", []) if isinstance(testcase_payload, dict) else []
    testcase_stats = testcase_payload.get("metadata", {}).get("statistics", {}) if isinstance(testcase_payload, dict) else {}

    code_impact = _load_json(internal_dir / "code_impact.json", {})
    code_summary = code_impact.get("summary", {}) if isinstance(code_impact, dict) else {}

    return {
        "requirement_fact_count": len(fact_items),
        "review": _review_issue_metrics(internal_dir),
        "testpoints": {
            "total_count": len(testpoints),
            "p1_count": p1_count,
            "p2_or_other_count": max(len(testpoints) - p1_count, 0),
        },
        "review_changes": _review_change_metrics(internal_dir, len(testpoints)),
        "coverage": {
            "mapping_count": len(coverage.get("mappings", [])) if isinstance(coverage, dict) else 0,
            "requirement_fact_count": int(coverage_stats.get("requirement_fact_count", 0) or 0),
            "p1_risk_coverage_rate": float(coverage_stats.get("p1_risk_coverage_rate", 0) or 0),
            "critical_state_transition_coverage_rate": float(coverage_stats.get("critical_state_transition_coverage_rate", 0) or 0),
            "important_review_question_coverage_rate": float(coverage_stats.get("important_review_question_coverage_rate", 0) or 0),
        },
        "quality_gates": {
            "status": gate_report.get("summary", {}).get("status", "unavailable"),
            "gate_count": len(gates),
            "passed_count": passed_gates,
            "failed_count": sum(1 for gate in gates if gate.get("status") == "failed"),
            "warning_count": sum(1 for gate in gates if gate.get("status") == "warning"),
            "skipped_count": len(gates) - len(applicable_gates),
            "applicable_count": len(applicable_gates),
            "pass_rate": _rate(passed_gates, len(applicable_gates)),
        },
        "testcases": {
            "total_count": len(testcases),
            "p1_count": int(testcase_stats.get("p1_count", 0) or 0),
            "p2_count": int(testcase_stats.get("p2_count", 0) or 0),
        },
        "code_analysis": {
            "status": code_impact.get("status", "unavailable"),
            "skip_reason": code_impact.get("skip_reason", ""),
            "business_impact_status": code_impact.get("business_impact_status", "unavailable"),
            "selected_file_count": int(code_summary.get("selected_count", 0) or 0),
            "coverage_gap_file_count": int(code_summary.get("coverage_gap_files", 0) or 0),
            "impact_confidence": code_impact.get("impact_confidence", "unavailable"),
        },
    }


def aggregate_execution_metrics(trace: dict[str, Any], run_id: str = "") -> dict[str, Any]:
    """Aggregate historical attempts and the current invocation."""
    events = [event for event in trace.get("events", []) if isinstance(event, dict)]
    canonical = {step for step, _ in STAGES}
    stage_rows = []
    latest_passed = 0
    for step, name in STAGES:
        attempts = [event for event in events if event.get("step") == step]
        passed = sum(1 for event in attempts if event.get("status") == "passed")
        failed = sum(1 for event in attempts if event.get("status") == "failed")
        latest = attempts[-1] if attempts else {}
        if latest.get("status") == "passed":
            latest_passed += 1
        stage_rows.append({
            "step": step,
            "name": name,
            "attempt_count": len(attempts),
            "passed_count": passed,
            "failed_count": failed,
            "pass_rate": _rate(passed, len(attempts)),
            "total_duration_seconds": round(sum(float(event.get("duration_seconds", 0) or 0) for event in attempts), 3),
            "last_duration_seconds": float(latest.get("duration_seconds", 0) or 0),
            "last_status": latest.get("status", "not_started"),
            "last_finished_at": latest.get("finished_at", ""),
        })

    canonical_events = [event for event in events if event.get("step") in canonical]
    current_events = [event for event in canonical_events if run_id and event.get("run_id") == run_id]
    passed_attempts = sum(1 for event in canonical_events if event.get("status") == "passed")
    failed_attempts = sum(1 for event in canonical_events if event.get("status") == "failed")
    run_ids = {event.get("run_id") for event in canonical_events if event.get("run_id")}
    return {
        "summary": {
            "stage_count": len(STAGES),
            "latest_passed_stage_count": latest_passed,
            "workflow_completion_rate": _rate(latest_passed, len(STAGES)),
            "attempt_count": len(canonical_events),
            "passed_attempt_count": passed_attempts,
            "failed_attempt_count": failed_attempts,
            "historical_pass_rate": _rate(passed_attempts, len(canonical_events)),
            "total_duration_seconds": round(sum(float(event.get("duration_seconds", 0) or 0) for event in canonical_events), 3),
            "run_count": len(run_ids),
        },
        "current_run": {
            "run_id": run_id,
            "attempted_stage_count": len(current_events),
            "passed_stage_count": sum(1 for event in current_events if event.get("status") == "passed"),
            "failed_stage_count": sum(1 for event in current_events if event.get("status") == "failed"),
            "duration_seconds": round(sum(float(event.get("duration_seconds", 0) or 0) for event in current_events), 3),
        },
        "stages": stage_rows,
    }


def build_metrics(output_dir: Path, trace: dict[str, Any], run_id: str = "") -> dict[str, Any]:
    execution = aggregate_execution_metrics(trace, run_id)
    completion = collect_completion_metrics(
        output_dir, trace, run_id, execution, finalization_blocking_issues
    )
    execution["summary"].update(completion["summary"])
    recent_events = [
        {
            "run_id": event.get("run_id", ""),
            "step": event.get("step", ""),
            "status": event.get("status", ""),
            "finished_at": event.get("finished_at", ""),
            "duration_seconds": float(event.get("duration_seconds", 0) or 0),
            "message": event.get("message", ""),
            "error_type": event.get("error_type", ""),
        }
        for event in trace.get("events", [])[-20:]
        if isinstance(event, dict)
    ]
    return {
        "schema_version": "1.0.0",
        "workflow_version": WORKFLOW_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "run_id": run_id,
        "summary": execution["summary"],
        "current_run": execution["current_run"],
        "human_confirmation": completion["human_confirmation"],
        "stages": execution["stages"],
        "recent_events": recent_events,
        "artifacts": collect_artifact_metrics(output_dir),
        "definitions": {
            "stage_pass_rate": "该阶段历史成功尝试数 / 历史尝试总数",
            "workflow_completion_rate": "七阶段中最近一次状态为 passed 的阶段数 / 7",
            "requested_scope_completion": "本次运行中各目标阶段最近状态为 passed 的阶段数 / 本次目标阶段数",
            "full_workflow_completion": "七阶段中最近一次状态为 passed 的阶段数 / 7",
            "human_confirmation_completion": "评审最终决策、XMind 终稿确认、最终用例归档三个人工关口的完成数 / 3",
            "review_modification_rate": "评审新增、删除、修改测试点数 / AI 原始测试点数",
            "quality_gate_pass_rate": "状态为 passed 的质量门禁数 / 质量门禁总数",
            "decision_completion_rate": "确认字段完整且通过决策契约的问题数 / 需确认问题数",
        },
    }


def _format_rate(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except (TypeError, ValueError):
        return "0.0%"


def render_markdown(metrics: dict[str, Any]) -> str:
    summary = metrics["summary"]
    current_run = metrics["current_run"]
    artifacts = metrics["artifacts"]
    review = artifacts["review"]
    changes = artifacts["review_changes"]
    gates = artifacts["quality_gates"]
    testpoints = artifacts["testpoints"]
    testcases = artifacts["testcases"]
    lines = [
        "# 流程度量报告", "",
        f"> 更新时间：{metrics['generated_at']}  ",
        f"> 当前运行：{metrics.get('run_id') or '历史汇总'}", "",
        "## 核心指标", "",
        "| 指标 | 数值 |", "| --- | ---: |",
        f"| 七阶段完成率 | {_format_rate(summary['workflow_completion_rate'])} |",
        f"| 本次请求完成率 | {_format_rate(summary['requested_scope_completion'])} |",
        f"| 全流程完成率 | {_format_rate(summary['full_workflow_completion'])} |",
        f"| 人工确认完成率 | {_format_rate(summary['human_confirmation_completion'])} |",
        f"| 当前人工关口 | {summary['human_confirmation_status']} |",
        f"| 历史阶段通过率 | {_format_rate(summary['historical_pass_rate'])} |",
        f"| 累计执行耗时 | {summary['total_duration_seconds']:.3f} 秒 |",
        f"| 本次运行耗时 | {current_run['duration_seconds']:.3f} 秒 |",
        f"| 本次通过阶段 | {current_run['passed_stage_count']} / {current_run['attempted_stage_count']} |",
        f"| 测试点数量 | {testpoints['total_count']} |",
        f"| 最终测试用例数量 | {testcases['total_count']} |",
        f"| 评审问题数量 | {review['issue_count']} |",
        f"| 评审问题处理率 | {_format_rate(review['resolution_rate'])} |",
        f"| 评审修改率 | {_format_rate(changes['modification_rate'])} |",
        f"| 质量门禁通过率 | {_format_rate(gates['pass_rate'])} |", "",
        "## 七阶段执行", "",
        "| 阶段 | 最近状态 | 尝试 | 通过 | 失败 | 通过率 | 最近耗时（秒） |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for stage in metrics["stages"]:
        lines.append(
            f"| {stage['name']} | {stage['last_status']} | {stage['attempt_count']} | "
            f"{stage['passed_count']} | {stage['failed_count']} | "
            f"{_format_rate(stage['pass_rate'])} | {stage['last_duration_seconds']:.3f} |"
        )
    lines.extend([
        "", "## 最近执行日志", "",
        "| 完成时间 | 阶段 | 状态 | 耗时（秒） | 说明 |",
        "| --- | --- | --- | ---: | --- |",
    ])
    for event in reversed(metrics.get("recent_events", [])[-10:]):
        message = str(event.get("message", "")).replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {event.get('finished_at', '')} | {event.get('step', '')} | "
            f"{event.get('status', '')} | {event.get('duration_seconds', 0):.3f} | {message} |"
        )
    lines.extend([
        "", "## 评审与质量", "",
        f"- 评审变更：新增 {changes['added_count']}、删除 {changes['deleted_count']}、修改 {changes['modified_count']}。",
        f"- 测试点优先级：P1 {testpoints['p1_count']}，其他 {testpoints['p2_or_other_count']}。",
        f"- 质量门禁：通过 {gates['passed_count']}、失败 {gates['failed_count']}、警告 {gates['warning_count']}、跳过 {gates.get('skipped_count', 0)}。",
        f"- 代码分析状态：{artifacts['code_analysis'].get('status', 'unavailable')}；{artifacts['code_analysis'].get('skip_reason', '')}。",
        f"- 代码业务影响状态：{artifacts['code_analysis'].get('business_impact_status', 'unavailable')}。",
        ("- 代码分析覆盖检查：已跳过，未核验实现。" if artifacts['code_analysis'].get('status') == "skipped" else f"- 代码分析覆盖缺口：{artifacts['code_analysis']['coverage_gap_file_count']} 个文件。"),
        "", "## 口径", "",
        "- 阶段通过率按历史尝试统计；失败后重跑会保留失败记录。",
        "- 跳过项不算通过，也不计入适用门禁通过率分母；没有代码目标不表示实现无缺口。",
        "- 评审修改率以评审差异预览为准，未生成预览时使用 XMind 回灌标记统计。",
        "- 阶段事件：.ai-internal/execution_trace.json；逐条结构化日志：.ai-internal/workflow_log.jsonl；结构化指标：.ai-internal/workflow_metrics.json。", "",
    ])
    return "\n".join(lines)


def write_workflow_metrics(
    output_dir: Path,
    trace: dict[str, Any],
    run_id: str = "",
    metrics_file: str = ".ai-internal/workflow_metrics.json",
    metrics_report: str = "流程度量报告.md",
) -> tuple[dict[str, Any], Path, Path]:
    """Write machine-readable metrics and the user-facing dashboard."""
    output_dir = Path(output_dir)
    metrics = build_metrics(output_dir, trace, run_id)
    json_path = output_dir / metrics_file
    markdown_path = output_dir / metrics_report
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_markdown(metrics), encoding="utf-8")
    return metrics, json_path, markdown_path
