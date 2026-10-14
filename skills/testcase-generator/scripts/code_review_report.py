#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a tester-facing code review report from technical analysis evidence."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from business_impact_gate import classify_coverage_gaps
from business_impact_readability import compact

RAW_MARKERS = (
    "business_rules_summary", "if_else_rules", "exception_rules",
    "value_annotations", "source_file", '"analysis": {', "{'",
)


def _list(value: Any) -> List[Any]:
    if value in (None, ""):
        return []
    return value if isinstance(value, list) else [value]


def _unique(values: Iterable[Any], limit: int = 20) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        text = compact(value, 260)
        key = re.sub(r"\s+", "", text).lower()
        if text and key not in seen:
            result.append(text)
            seen.add(key)
        if len(result) >= limit:
            break
    return result


def _internal(output_dir: Path) -> Path:
    path = Path(output_dir) / ".ai-internal"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _extract_evidence(body: str) -> List[str]:
    paths = re.findall(
        r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.(?:java|xml|py|sql)(?::\d+)?",
        body,
    )
    inline = re.findall(r"`([^`]{2,100})`", body)
    useful_inline = [
        value for value in inline
        if re.search(r"\.(?:java|xml|py|sql)(?::\d+)?$", value)
        or re.fullmatch(r"[0-9a-f]{7,40}", value, re.I)
        or (value.count(".") >= 2 and " " not in value)
    ]
    return _unique(paths + useful_inline, 8)


def _guidance(title: str, body: str, severity: str) -> Dict[str, Any]:
    title_text = title.lower()
    if "32 位" in title_text or "hash_code 定位" in title_text or "hashcode 碰撞" in title_text:
        impact = "发生 32 位哈希碰撞时，更新语句可能同时修改非目标记录，造成错误数据。"
        focus = ["构造同税号、32 位哈希相同但 64 位去重标识不同的两条记录", "执行更新后只允许目标记录发生变化", "核对更新行数和非目标记录内容"]
        gate = "更新定位改为数据库主键或 64 位去重标识，并通过真实 SQL 碰撞用例后方可放行。"
    elif "规范化" in title_text or "金额多格式" in title_text:
        impact = "同一业务数据因金额、空值或日期格式不同生成不同去重标识，历史数据回填后仍可能重复落库。"
        focus = ["分别准备千分位、前导零、小数位不同但业务含义相同的数据", "核对在线落库与历史回填生成的去重标识一致", "重复采集不得新增第二条记录"]
        gate = "在线落库与历史迁移共用同一套字段规范化和去重键构造逻辑，并通过一致性测试。"
    elif "单税号失败" in title_text or "仍标记成功" in title_text:
        impact = "部分企业处理失败时调度平台仍显示成功，测试和运维可能误判历史数据已全部完成。"
        focus = ["让一个税号处理失败、其他税号正常", "核对最终任务状态必须反映部分失败", "核对失败税号清单、后续税号处理和可重跑参数"]
        gate = "部分失败不可显示成功，且必须提供可定位、可重跑的失败信息。"
    elif "大查询" in title_text or "大 sql" in title_text or "全量兜底" in title_text:
        impact = "大企业历史数据可能触发高内存、大 SQL、长时间锁等待或业务回调超时。"
        focus = ["按生产级数据量验证分页和批量上限", "监控 SQL 长度、内存、锁等待和接口耗时", "验证超时或中断后的数据一致性与重跑"]
        gate = "明确在线兜底开关策略和分页上限，并提供大数据量压测结果。"
    elif "索引" in title_text or "约束方案" in title_text:
        impact = "缺少适配索引时，迁移和在线去重查询可能扫描大量数据，影响业务库稳定性。"
        focus = ["确认三张表实际索引结构", "对税号与去重标识查询执行 EXPLAIN", "验证迁移期间查询耗时、锁等待和数据库负载"]
        gate = "三张表索引方案评审通过，并提供生产量级执行计划或压测结果。"
    elif "无关提交" in title_text or "混入" in title_text:
        impact = "提测范围包含非本需求改动，可能扩大回归范围并把无关缺陷带入测试环境。"
        focus = ["核对需求提交清单和最终合入 SHA", "确认无关服务文件未进入本次 UAT", "若保留无关提交则补充对应回归范围"]
        gate = "明确排除无关提交，或由负责人确认扩大的提测和回归范围。"
    else:
        first = re.split(r"[。\n]", re.sub(r"[`*_>#-]", "", body).strip())[0].strip()
        impact = (first + "。") if first else "代码证据显示存在需要测试确认的实现风险。"
        focus = ["准备正常、边界、异常和重复执行数据", "核对业务结果、数据库状态和失败恢复"]
        gate = "形成明确处置结论并完成对应专项测试。"
    if severity == "P0" and "方可放行" not in gate and "不得放行" not in gate:
        gate = "该问题修复并通过专项测试前不得放行；" + gate
    return {"tester_impact": impact, "test_focus": focus, "release_gate": gate}


def extract_manual_findings(output_dir: Path) -> List[Dict[str, Any]]:
    """Persist existing manual P0/P1 findings before the user report is regenerated."""
    internal = _internal(output_dir)
    saved = internal / "manual_code_review_findings.json"
    if saved.exists():
        try:
            payload = json.loads(saved.read_text(encoding="utf-8-sig"))
            if payload.get("items"):
                return payload["items"]
        except (OSError, ValueError, TypeError):
            pass
    report = Path(output_dir) / "代码评审报告.md"
    if not report.exists():
        return []
    text = report.read_text(encoding="utf-8-sig")
    marker = text.find("# 人工源码复核补充")
    if marker < 0:
        return []
    manual = text[marker:]
    matches = list(re.finditer(r"^###\s+(P[012])-?(\d+)\s+(.+?)\s*$", manual, re.M))
    items: List[Dict[str, Any]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(manual)
        body = manual[match.end():end].strip()
        severity = match.group(1)
        finding_id = f"{severity}-{match.group(2)}"
        guidance = _guidance(match.group(3), body, severity)
        items.append({
            "finding_id": finding_id,
            "severity": severity,
            "title": compact(match.group(3), 100),
            **guidance,
            "technical_evidence": _extract_evidence(body),
            "source_body": body,
        })
    if items:
        _write_json(saved, {"schema_version": "1.0.0", "items": items})
    return items


def _generic_findings(code_analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    definitions = []
    if code_analysis.get("sql_analysis"):
        definitions.append(("P1", "CR-P1-DATA", "数据写入与 SQL 一致性", "存在数据写入或 SQL 变更"))
    critical_gaps, _accepted_partial = classify_coverage_gaps({"analysis": code_analysis})
    if critical_gaps:
        definitions.append(("P1", "CR-P1-COVERAGE", "代码分析覆盖不完整", "部分关键源码未被完整解析"))
    if code_analysis.get("scheduled_tasks"):
        definitions.append(("P2", "CR-P2-JOB", "定时任务重复执行与失败恢复", "存在定时任务变更"))
    if code_analysis.get("config_values"):
        definitions.append(("P2", "CR-P2-CONFIG", "配置开关与默认值", "存在配置变更"))
    result = []
    for severity, finding_id, title, body in definitions:
        result.append({
            "finding_id": finding_id,
            "severity": severity,
            "title": title,
            **_guidance(title, body, severity),
            "technical_evidence": [],
        })
    return result


def _comparison_summary(comparison: Dict[str, Any]) -> Dict[str, int]:
    counts = {"一致": 0, "缺失": 0, "不一致": 0, "额外实现": 0, "证据不足": 0}
    for item in comparison.get("items") or []:
        label = item.get("classification") or "证据不足"
        counts[label] = counts.get(label, 0) + 1
    return counts


def _coverage(code_analysis: Dict[str, Any]) -> Dict[str, Any]:
    raw = code_analysis.get("technology_coverage") or {}
    if not isinstance(raw, dict):
        return {
            "applicable": len(code_analysis.get("files_changed") or []),
            "complete": 0,
            "gaps": [],
            "accepted_partial": [],
        }

    critical, accepted_partial = classify_coverage_gaps({"analysis": code_analysis})

    def paths(values: List[Dict[str, Any]]) -> List[str]:
        result = []
        for value in values:
            text = compact(
                value.get("file") or value.get("path") or value.get("name") or value.get("warning"),
                200,
            )
            if text:
                result.append(text)
        return _unique(result, 20)

    return {
        "applicable": raw.get("applicable_source_files", len(code_analysis.get("files_changed") or [])),
        "complete": raw.get("fully_analyzed_files", 0),
        "gaps": paths(critical),
        "accepted_partial": paths(accepted_partial),
    }


def _validation_objects(business_impact: Dict[str, Any]) -> List[Dict[str, Any]]:
    result = []
    seen = set()
    for item in business_impact.get("items") or []:
        name = compact(item.get("validation_object"), 140)
        key = re.sub(r"\W+", "", name).lower()
        if not name or key in seen:
            continue
        seen.add(key)
        result.append({
            "impact_id": compact(item.get("impact_id"), 40),
            "category": compact(item.get("impact_category"), 60),
            "name": name,
            "test_focus": _unique(_list(item.get("test_focus")), 4),
            "confidence": compact(item.get("confidence"), 20) or "unknown",
        })
    return result


def _branch_evidence(target_results: Sequence[Dict[str, Any]]) -> List[Dict[str, str]]:
    result = []
    for target in target_results:
        result.append({
            "target_id": compact(target.get("target_id"), 80),
            "baseline": compact(target.get("baseline") or target.get("baseline_ref"), 80),
            "baseline_sha": compact(target.get("baseline_sha"), 40),
            "development_branch": compact(target.get("development_branch") or target.get("branch"), 100),
            "development_sha": compact(target.get("development_sha") or target.get("branch_sha"), 40),
            "freshness": compact(((target.get("freshness") or {}).get("freshness_status") or (target.get("freshness") or {}).get("status")) if isinstance(target.get("freshness"), dict) else target.get("freshness"), 80),
        })
    return result


def build_code_review_model(
    output_dir: Path,
    project_info: Dict[str, Any],
    code_analysis: Dict[str, Any],
    code_impact: Dict[str, Any],
    comparison: Dict[str, Any],
    business_impact: Dict[str, Any] | None = None,
    target_results: Sequence[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    manual = extract_manual_findings(output_dir)
    findings = []
    for item in manual:
        refreshed = {key: value for key, value in item.items() if key not in {"source_body", "tester_impact", "test_focus", "release_gate"}}
        refreshed.update(_guidance(item.get("title", ""), item.get("source_body", ""), item.get("severity", "P2")))
        refreshed["technical_evidence"] = _extract_evidence(item.get("source_body", ""))
        findings.append(refreshed)
    if not findings:
        findings = _generic_findings(code_analysis)
    counts = {"P0": 0, "P1": 0, "P2": 0}
    for item in findings:
        severity = item.get("severity") or "P2"
        counts[severity] = counts.get(severity, 0) + 1
    if counts.get("P0"):
        decision = "阻断放行：P0 问题修复并通过专项测试前，不建议进入正式验收。"
    elif counts.get("P1"):
        decision = "有条件放行：P1 风险必须有明确处置结论和测试结果。"
    else:
        decision = "未发现阻断级问题，仍需完成报告列出的回归验证。"
    analysis_mode = (business_impact or {}).get("analysis_mode") or code_impact.get("analysis_mode") or "uat"
    validation_objects = _validation_objects(business_impact or {})
    return {
        "schema_version": "1.0.0",
        "project_name": compact(project_info.get("project_name") or project_info.get("name"), 160),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "analysis_mode": analysis_mode,
        "impact_confidence": compact(code_impact.get("impact_confidence"), 30) or "unknown",
        "review_decision": decision,
        "risk_counts": counts,
        "findings": findings,
        "validation_objects": validation_objects,
        "comparison_summary": _comparison_summary(comparison),
        "coverage": _coverage(code_analysis),
        "change_summary": {
            "files": len(code_analysis.get("files_changed") or []),
            "scheduled_tasks": sum(1 for value in validation_objects if "任务" in value.get("category", "")),
            "sql_objects": sum(1 for value in validation_objects if "数据库" in value.get("category", "")) or len(code_analysis.get("sql_analysis") or []),
        },
        "branch_evidence": _branch_evidence(target_results or []),
        "evidence_files": [
            ".ai-internal/code_targets.json",
            ".ai-internal/code_impact.json",
            ".ai-internal/requirement_code_comparison.json",
            ".ai-internal/business_impact.json",
            ".ai-internal/manual_code_review_findings.json",
        ],
    }


def assess_code_review_readability(model: Dict[str, Any]) -> Dict[str, Any]:
    findings = model.get("findings") or []
    objects = model.get("validation_objects") or []
    incomplete = [
        item.get("finding_id", "unknown") for item in findings
        if not item.get("tester_impact") or not item.get("test_focus") or not item.get("release_gate")
    ]
    raw = {}
    for item in findings:
        text = " ".join(str(item.get(key) or "") for key in ("title", "tester_impact", "test_focus", "release_gate"))
        markers = [marker for marker in RAW_MARKERS if marker in text]
        if markers or len(text) > 1600:
            raw[item.get("finding_id", "unknown")] = markers or ["文本过长"]
    seen = set()
    duplicates = []
    for item in objects:
        key = re.sub(r"\W+", "", str(item.get("name") or "")).lower()
        if key in seen:
            duplicates.append(item.get("name", "unknown"))
        seen.add(key)
    gates = []

    def add(gate_id: str, name: str, passed: bool, message: str) -> None:
        gates.append({"gate_id": gate_id, "name": name, "status": "passed" if passed else "failed", "message": message, "blocking": not passed})

    add("CR-G1", "测试结论明确", bool(model.get("review_decision")), "已形成放行建议" if model.get("review_decision") else "缺少放行建议")
    add("CR-G2", "风险可执行", not incomplete, f"缺少测试动作或放行条件 {len(incomplete)} 条")
    add("CR-G3", "测试人员可读性", not raw, f"包含原始结构或超长风险 {len(raw)} 条")
    add("CR-G4", "验证对象去重", not duplicates, f"重复验证对象 {len(duplicates)} 个")
    blocking = [item for item in gates if item["blocking"]]
    return {
        "schema_version": "1.0.0",
        "summary": {"status": "blocked" if blocking else "passed", "blocking_count": len(blocking)},
        "gates": gates,
        "incomplete_findings": incomplete,
        "readability_issues": raw,
        "duplicate_objects": duplicates,
    }


def _mode_label(value: Any) -> str:
    return {
        "development_preanalysis": "开发分支预分析（非 UAT 最终结论）",
        "uat": "正式 UAT 分析",
        "uat_confirmed": "UAT 已确认",
        "uat_source_verified": "UAT 合入已核验，关键影响已通过目标提交源码补核",
        "uat_merged_evidence_incomplete": "UAT 代码已合入，正式证据门禁未全部通过",
        "uat_blocked_with_development_preanalysis": "UAT 未满足门禁，仅保留开发预分析",
    }.get(str(value), str(value or "未知"))


def _join(values: Any, empty: str = "待确认", limit: int = 6) -> str:
    return "；".join(_unique(_list(values), limit)) or empty


def write_code_review_report(
    output_dir: Path,
    project_info: Dict[str, Any],
    code_analysis: Dict[str, Any],
    code_impact: Dict[str, Any] | None = None,
    comparison: Dict[str, Any] | None = None,
    business_impact: Dict[str, Any] | None = None,
    target_results: Sequence[Dict[str, Any]] | None = None,
) -> Tuple[Path, Dict[str, Any], Dict[str, Any]]:
    """Write the tester report plus structured model and readability gate."""
    output_dir = Path(output_dir)
    model = build_code_review_model(
        output_dir, project_info or {}, code_analysis or {}, code_impact or {},
        comparison or {}, business_impact, target_results,
    )
    gate = assess_code_review_readability(model)
    internal = _internal(output_dir)
    _write_json(internal / "code_review_summary.json", model)
    _write_json(internal / "code_review_readability_gate.json", gate)

    counts = model["risk_counts"]
    change = model["change_summary"]
    comparison_counts = model["comparison_summary"]
    coverage = model["coverage"]
    lines = [
        "# 代码评审报告（测试人员版）", "",
        "> 阅读顺序：先看放行结论和必测风险，再看验证对象；源码路径和 SHA 仅用于问题追溯。", "",
        "## 1. 一页结论", "",
        f"- 项目：{model.get('project_name') or '未命名需求'}",
        f"- 分析阶段：{_mode_label(model.get('analysis_mode'))}",
        f"- 放行建议：**{model.get('review_decision')}**",
        f"- 风险数量：P0 {counts.get('P0', 0)} 条，P1 {counts.get('P1', 0)} 条，P2 {counts.get('P2', 0)} 条",
        f"- 可读性门禁：{'通过' if gate.get('summary', {}).get('status') == 'passed' else '未通过'}", "",
        "## 2. 测试人员必看风险", "",
        "| 编号 | 级别 | 可能出现的问题 | 测试人员怎么验证 | 放行条件 |",
        "|---|---|---|---|---|",
    ]
    for item in model.get("findings") or []:
        lines.append(
            f"| {item.get('finding_id')} {item.get('title')} | {item.get('severity')} | "
            f"{compact(item.get('tester_impact'), 260)} | {_join(item.get('test_focus'), limit=5)} | "
            f"{compact(item.get('release_gate'), 260)} |"
        )
    if not model.get("findings"):
        lines.append("| - | - | 未发现明确风险 | 完成正常、边界、异常回归 | 测试结果通过 |")

    lines.extend(["", "## 3. 本次改动与验证对象", "",
                  f"本次代码范围包含 {change.get('files', 0)} 个文件、{change.get('scheduled_tasks', 0)} 个去重后的定时任务、{change.get('sql_objects', 0)} 个 SQL 分析对象。", ""])
    if model.get("validation_objects"):
        lines.extend(["| 业务影响 ID | 类型 | 验证对象 | 优先测试内容 | 置信度 |", "|---|---|---|---|---|"])
        for item in model["validation_objects"]:
            lines.append(
                f"| {item.get('impact_id')} | {item.get('category')} | {item.get('name')} | "
                f"{_join(item.get('test_focus'), limit=4)} | {item.get('confidence')} |"
            )
    else:
        lines.append("- 尚未生成归并后的业务验证对象，测试点阶段前必须补齐。")

    lines.extend([
        "", "## 4. 需求与代码核对结果", "",
        "> 自动对比只作为线索；长需求原文、告警文本和解析器键值不直接展示在测试报告中。", "",
        "| 结论 | 数量 | 测试处理 |", "|---|---:|---|",
        f"| 不一致 | {comparison_counts.get('不一致', 0)} | 逐项确认需求口径与实际实现，形成测试或评审结论 |",
        f"| 需求有、代码证据未确认 | {comparison_counts.get('缺失', 0)} | 回到目标 SHA 源码核验，不直接判定为未实现 |",
        f"| 代码额外实现 | {comparison_counts.get('额外实现', 0)} | 确认是否属于本需求，并决定是否扩大回归 |",
        f"| 证据不足 | {comparison_counts.get('证据不足', 0)} | 补充 DDL、SQL、出参映射、调用链或运行结果后再下结论 |",
        f"| 一致 | {comparison_counts.get('一致', 0)} | 纳入正常回归 |", "",
        "## 5. 代码覆盖边界", "",
        f"- 适用源码：{coverage.get('applicable', 0)} 个",
        f"- 完整分析：{coverage.get('complete', 0)} 个",
        f"- 关键覆盖缺口：{len(coverage.get('gaps') or [])} 个",
        f"- 部分覆盖提醒（非阻断）：{len(coverage.get('accepted_partial') or [])} 个",
    ])
    lines.extend(f"- 需补充分析：{value}" for value in coverage.get("gaps") or [])
    lines.extend(
        f"- 已提取 SQL，仍需集成验证：{value}"
        for value in coverage.get("accepted_partial") or []
    )

    lines.extend(["", "## 6. 技术追溯（测试执行时可忽略）", ""])
    for branch in model.get("branch_evidence") or []:
        lines.append(
            f"- {branch.get('target_id')}：基线 {branch.get('baseline')}@{branch.get('baseline_sha')}；"
            f"开发分支 {branch.get('development_branch')}@{branch.get('development_sha')}；最新性 {branch.get('freshness') or '未确认'}"
        )
    evidence = _unique((
        value for finding in model.get("findings") or []
        for value in _list(finding.get("technical_evidence"))
    ), 30)
    if evidence:
        lines.extend(f"- 风险证据：{value}" for value in evidence)
    lines.extend(f"- 内部证据：`{value}`" for value in model.get("evidence_files") or [])

    lines.extend(["", "## 7. 报告可读性门禁", "", "| 门禁 | 状态 | 说明 |", "|---|---|---|"])
    for item in gate.get("gates") or []:
        lines.append(f"| {item.get('gate_id')} {item.get('name')} | {'通过' if item.get('status') == 'passed' else '未通过'} | {item.get('message')} |")
    lines.extend(["", "---", "", f"*生成日期：{datetime.now().strftime('%Y年%m月%d日')}*", ""])
    path = output_dir / "代码评审报告.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path, model, gate
