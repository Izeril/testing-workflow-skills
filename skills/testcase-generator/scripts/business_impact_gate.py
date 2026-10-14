#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evidence, readability and actionability gates for business impacts."""

from __future__ import annotations

import re
from typing import Any, Dict, List

from business_impact_readability import readability_issues


REQUIRED_FIELDS = (
    "business_domain", "affected_actor", "impact_category", "validation_object",
    "business_scenario", "trigger", "preconditions", "new_behavior",
    "expected_result", "test_focus", "confidence", "evidence_ids",
)


def _normal(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").lower())


def classify_coverage_gaps(target: Dict[str, Any]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Separate critical gaps from intentionally partial MyBatis SQL coverage."""
    analysis = target.get("analysis") or {}
    coverage = analysis.get("technology_coverage") or {}
    sql_files = set()
    for item in analysis.get("sql_analysis") or []:
        if not isinstance(item, dict):
            continue
        payload = item.get("value") if isinstance(item.get("value"), dict) else item
        if payload.get("analysis"):
            sql_files.add(str(payload.get("file") or payload.get("source_file") or ""))
    critical: List[Dict[str, Any]] = []
    accepted_partial: List[Dict[str, Any]] = []
    for gap in coverage.get("gaps") or []:
        if not isinstance(gap, dict):
            critical.append({"status": "unknown", "warning": str(gap)})
            continue
        file_path = str(gap.get("file") or "")
        is_evidenced_mybatis = (
            gap.get("status") == "partial"
            and gap.get("analyzer") == "mybatis-sql-when-detected"
            and file_path in sql_files
        )
        if is_evidenced_mybatis:
            accepted_partial.append(gap)
        else:
            critical.append(gap)
    if coverage.get("complete") is False and not (coverage.get("gaps") or []):
        critical.append({"status": "unknown", "warning": "覆盖标记不完整但未提供逐文件缺口"})
    return critical, accepted_partial
def completed_code_target(target: Dict[str, Any]) -> bool:
    """Require analyzed changed files; a configured target or empty dict is not evidence."""
    status = target.get("status")
    return (
        (status is None or status in {"completed", "completed_stale"})
        and bool((target.get("analysis") or {}).get("files_changed"))
    )


def business_impact_execution(target_results, manifest_errors=None):
    """Keep absent code, unsuccessful code and completed code distinct."""
    errors = list(manifest_errors or [])
    completed = [target for target in target_results if completed_code_target(target)]
    blocked = [target for target in target_results if not completed_code_target(target)]
    if errors:
        status, reason = "blocked", "代码目标清单无效：" + "；".join(map(str, errors))
    elif not target_results:
        status, reason = "skipped", "未提供可分析的代码目标，未执行代码业务影响分析"
    elif blocked:
        status = "blocked"
        reason = "；".join(
            str(target.get("block_reason") or
                f"{target.get('target_id', 'unknown')}：代码分析未完成或缺少已分析变更文件")
            for target in blocked
        )
    else:
        status, reason = "completed", ""
    return {
        "status": status, "reason": reason,
        "target_count": len(target_results), "analyzed_target_count": len(completed),
        "blocked_target_ids": [str(t.get("target_id") or "unknown") for t in blocked],
    }


def assess_business_impact_gate(
    business_impact: Dict[str, Any],
    target_results: List[Dict[str, Any]],
    require_codegraph_for_uat: bool = True,
) -> Dict[str, Any]:
    """Block unreadable output and formal UAT conclusions without evidence."""
    execution = business_impact_execution(
        target_results, business_impact.get("code_target_manifest_errors")
    )
    status = execution["status"]
    reason = execution["reason"]
    if status == "skipped" and business_impact.get("items"):
        status, reason = "blocked", "没有代码目标却存在业务影响结论，证据状态不一致"
    if status == "completed" and business_impact.get("status") in {"skipped", "blocked"}:
        status, reason = "blocked", business_impact.get("block_reason") or "业务影响状态与代码执行证据不一致"
    if status != "completed":
        return {
            "schema_version": "1.1.0",
            "summary": {"status": status, "blocking_count": int(status == "blocked"),
                        "warning_count": 0, "reason": reason},
            "analysis_mode": "not_applicable" if status == "skipped" else "code_analysis_blocked",
            "execution": execution,
            "gates": [{"gate_id": "BI-G0", "name": "代码分析执行证据",
                       "status": "skipped" if status == "skipped" else "failed",
                       "message": reason, "blocking": status == "blocked"}],
            "readability_issues": {}, "inactionable_impact_ids": [],
            "duplicate_scenarios": [], "nonblocking_partial_coverage": {},
            "blocked_target_ids": execution["blocked_target_ids"],
        }
    gates = []

    def add(gate_id: str, name: str, passed: bool, message: str, blocking: bool) -> None:
        gates.append({
            "gate_id": gate_id,
            "name": name,
            "status": "passed" if passed else "failed",
            "message": message,
            "blocking": blocking and not passed,
        })

    impacts = business_impact.get("items") or []
    incomplete = [
        item.get("impact_id", "unknown") for item in impacts
        if any(item.get(field) in (None, "", []) for field in REQUIRED_FIELDS)
    ]
    missing_code = [
        item.get("impact_id", "unknown") for item in impacts
        if not any(str(value).startswith("CE-") for value in item.get("evidence_ids", []))
    ]
    unreadable = {
        item.get("impact_id", "unknown"): readability_issues(item)
        for item in impacts if readability_issues(item)
    }
    inactionable = [
        item.get("impact_id", "unknown") for item in impacts
        if not item.get("validation_object") or not item.get("expected_result") or not item.get("test_focus")
    ]
    seen = {}
    duplicates = []
    for item in impacts:
        key = (
            item.get("target_id", "unknown"),
            _normal(item.get("impact_category")),
            _normal(item.get("validation_object")),
        )
        if key in seen:
            duplicates.append(f"{seen[key]} / {item.get('impact_id', 'unknown')}")
        else:
            seen[key] = item.get("impact_id", "unknown")

    formal_targets = [
        target for target in target_results
        if str(target.get("analysis_mode") or "uat") == "uat"
    ]
    merge_failures = [
        target.get("target_id", "unknown") for target in formal_targets
        if (target.get("uat_merge_gate") or {}).get("status") != "fully_merged"
    ]
    graph_failures = [
        target.get("target_id", "unknown") for target in formal_targets
        if target.get("codegraph_status") != "ready"
    ]
    strict_graph_failures = [
        target.get("target_id", "unknown") for target in formal_targets
        if target.get("codegraph_policy", "risk_based") == "strict"
        and target.get("codegraph_status") != "ready"
    ]
    source_coverage_failures = [
        target.get("target_id", "unknown") for target in formal_targets
        if target.get("codegraph_policy", "risk_based") == "risk_based"
        and target.get("codegraph_status") not in {"ready", "source_verified"}
    ]
    freshness_failures = [
        target.get("target_id", "unknown") for target in formal_targets
        if target.get("formal_admission_status") == "blocked_remote_refresh"
        or (target.get("git_snapshot") and not target["git_snapshot"].get("formal_ready"))
    ]
    critical_coverage_gaps = []
    accepted_partial_coverage: Dict[str, List[str]] = {}
    for target in formal_targets:
        critical, accepted_partial = classify_coverage_gaps(target)
        if critical:
            critical_coverage_gaps.append(target.get("target_id", "unknown"))
        if accepted_partial:
            accepted_partial_coverage[target.get("target_id", "unknown")] = [
                str(item.get("file") or "") for item in accepted_partial
            ]

    formal = bool(formal_targets)
    add("BI-G1", "业务影响字段完整", not incomplete, f"字段不完整影响 {len(incomplete)} 条", True)
    add("BI-G2", "代码证据必备", not missing_code, f"缺少 CE 代码证据 {len(missing_code)} 条", True)
    add("BI-G3", "UAT 完整合入", not merge_failures, f"未完整合入目标 {len(merge_failures)} 个", formal)
    add("BI-G3-F", "正式 UAT 引用最新性", not freshness_failures,
        f"正式引用尚未验证目标 {len(freshness_failures)} 个", formal)
    add("BI-G4", "Codegraph 版本与查询提示", not graph_failures,
        f"图索引滞后或结果未就绪目标 {len(graph_failures)} 个；默认允许源码补核",
        formal and require_codegraph_for_uat and bool(strict_graph_failures))
    add("BI-G4-S", "关键影响证据完整", not source_coverage_failures,
        f"图证据或目标 SHA 源码补核未完成目标 {len(source_coverage_failures)} 个", formal)
    add("BI-G5", "关键技术覆盖", not critical_coverage_gaps,
        f"存在关键覆盖缺口目标 {len(critical_coverage_gaps)} 个", formal)
    partial_count = sum(len(files) for files in accepted_partial_coverage.values())
    add("BI-G5-W", "MyBatis 部分覆盖提示", partial_count == 0,
        f"已提取 SQL 证据但仍需集成验证的 MyBatis 文件 {partial_count} 个", False)
    add("BI-G6", "确定影响存在", bool(impacts), f"业务影响 {len(impacts)} 条", True)
    add("BI-G7", "测试人员可读性", not unreadable,
        f"存在原始结构、超长文本或解析器字段 {len(unreadable)} 条", True)
    add("BI-G8", "测试可执行性", not inactionable,
        f"缺少验证对象、预期结果或测试关注点 {len(inactionable)} 条", True)
    add("BI-G9", "场景归并去重", not duplicates,
        f"重复验证场景 {len(duplicates)} 组", True)

    blocking = [item for item in gates if item["blocking"]]
    status = "blocked" if blocking else "passed" if formal else "passed_with_warnings"
    return {
        "schema_version": "1.1.0",
        "summary": {
            "status": status,
            "blocking_count": len(blocking),
            "warning_count": sum(1 for item in gates if item["status"] == "failed" and not item["blocking"]),
        },
        "analysis_mode": "uat" if formal else "development",
        "gates": gates,
        "readability_issues": unreadable,
        "inactionable_impact_ids": inactionable,
        "duplicate_scenarios": duplicates,
        "nonblocking_partial_coverage": accepted_partial_coverage,
        "blocked_target_ids": list(dict.fromkeys(merge_failures + strict_graph_failures + source_coverage_failures + freshness_failures + critical_coverage_gaps)),
    }
