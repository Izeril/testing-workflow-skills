#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate a concise, tester-facing business impact report."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4
from typing import Any, Dict

from business_impact_readability import compact


def _values(values: Any, empty: str = "待确认", limit: int = 8) -> str:
    if values in (None, "", []):
        return empty
    if not isinstance(values, list):
        values = [values]
    result = [compact(value, 220) for value in values[:limit]]
    return "；".join(value for value in result if value) or empty


def _mode_label(value: Any) -> str:
    return {
        "not_applicable": "未执行：没有代码目标",
        "code_analysis_blocked": "代码分析未完成，不能确认业务影响",
        "development_preanalysis": "开发分支预分析（非 UAT 最终结论）",
        "uat_confirmed": "UAT 已确认",
        "uat_source_verified": "UAT 合入已核验，关键影响已通过目标提交源码补核",
        "uat_merged_evidence_incomplete": "UAT 代码已合入，正式证据门禁未全部通过",
        "uat_blocked_with_development_preanalysis": "UAT 未满足门禁，仅保留开发预分析",
    }.get(str(value), str(value or "未知"))


def _gate_label(value: Any) -> str:
    return {
        "skipped": "已跳过，不适用",
        "passed_with_warnings": "已通过开发预分析门禁，仍需 UAT 确认",
        "passed": "已通过",
        "blocked": "未通过",
    }.get(str(value), str(value or "未知"))

def _gate_item_label(gate: Dict[str, Any]) -> str:
    if gate.get("status") == "skipped":
        return "跳过"
    if gate.get("status") == "passed":
        return "通过"
    if not gate.get("blocking"):
        return "提醒"
    return "未通过"

def _bullets(lines, title: str, values: Any) -> None:
    lines.extend([f"### {title}", ""])
    values = values if isinstance(values, list) else [values]
    clean = [compact(value, 220) for value in values if compact(value, 220)]
    if not clean:
        clean = ["待确认"]
    lines.extend(f"- {value}" for value in clean[:8])
    lines.append("")


def write_business_impact_report(
    output_dir: Path,
    business_impact: Dict[str, Any],
    gate_report: Dict[str, Any],
) -> Path | None:
    """Write validation objects first and isolate technical trace information."""
    path = Path(output_dir) / "业务影响分析.md"
    if business_impact.get("status") == "skipped" or business_impact.get("analysis_mode") == "not_applicable":
        if path.exists():
            archive = Path(output_dir) / ".ai-internal" / "inapplicable_reports" / uuid4().hex
            archive.mkdir(parents=True, exist_ok=True)
            path.replace(archive / path.name)
        return None
    summary = business_impact.get("summary") or {}
    items = business_impact.get("items") or []
    gate_status = gate_report.get("summary", {}).get("status")
    if not items or business_impact.get("status") == "blocked":
        gate_status = "blocked"
    lines = [
        "# 业务影响分析（测试人员版）", "",
        "> 阅读方式：先按“验证对象”设计测试；技术文件和证据编号仅用于追溯，不是测试步骤。", "",
        f"- 分析模式：{_mode_label(business_impact.get('analysis_mode'))}",
        f"- 已归并验证对象：{summary.get('grouped_objects', len(items))} 个",
        f"- 可读对象：{summary.get('tester_readable', 0)} 个",
        f"- 业务影响门禁：{_gate_label(gate_status)}", "",
        "## 一览", "",
        "| ID | 影响分类 | 验证对象 | 业务场景 | 置信度 |",
        "|---|---|---|---|---|",
    ]
    for item in items:
        lines.append(
            f"| {item.get('impact_id', '')} | {_values(item.get('impact_category'))} | "
            f"{_values(item.get('validation_object'))} | {_values(item.get('business_scenario'))} | "
            f"{item.get('confidence', 'unknown')} |"
        )
    lines.append("")

    for item in items:
        lines.extend([
            f"## {item.get('impact_id')} {_values(item.get('validation_object'))}", "",
            f"- 影响分类：{_values(item.get('impact_category'))}",
            f"- 影响人员/系统：{_values(item.get('affected_actor'))}",
            f"- 业务场景：{_values(item.get('business_scenario'))}",
            f"- 触发方式：{_values(item.get('trigger'))}",
            f"- 前置条件：{_values(item.get('preconditions'))}", "",
        ])
        _bullets(lines, "预期业务结果", item.get("expected_result"))
        _bullets(lines, "关键业务规则", item.get("business_rule"))
        data_values = item.get("data_impact") or []
        state_values = item.get("state_impact") or []
        if not isinstance(data_values, list):
            data_values = [data_values]
        if not isinstance(state_values, list):
            state_values = [state_values]
        _bullets(lines, "数据与状态检查", data_values + state_values)
        _bullets(lines, "异常与恢复", item.get("exception_impact"))
        _bullets(lines, "测试关注点", item.get("test_focus"))
        _bullets(lines, "风险与待确认", item.get("risk_notes"))
        lines.extend([
            "### 技术追溯（测试执行时可忽略）", "",
            f"- 代码位置：{_values(item.get('technical_evidence'))}",
            f"- 证据编号：{_values(item.get('evidence_ids'))}", "",
        ])

    if not items:
        lines.extend(["## 暂无可确认业务影响", "", f"- {business_impact.get('block_reason') or gate_report.get('summary', {}).get('reason') or '没有有效业务影响结论，请检查代码准入、提交范围及源码证据。'}", ""])
    lines.extend(["## 门禁与待确认", "", "| 门禁 | 状态 | 说明 |", "|---|---|---|"])
    for gate in gate_report.get("gates", []):
        lines.append(f"| {gate.get('gate_id')} {gate.get('name')} | {_gate_item_label(gate)} | {gate.get('message')} |")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
