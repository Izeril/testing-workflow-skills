#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Business-impact-aware facade for test design and quality gates."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from decision_register import testpoint_blocking_issues
from business_impact_gate import business_impact_execution

import workflow_testpoint_legacy as _legacy

globals().update(
    {
        name: value
        for name, value in vars(_legacy).items()
        if not name.startswith("__")
    }
)


_legacy_run_testpoint_generate = _legacy.run_testpoint_generate
_legacy_require_inputs = _legacy._require_testpoint_inputs
_legacy_build_matrix = _legacy._build_coverage_matrix
_legacy_run_gates = _legacy._run_minimal_quality_gates
_legacy_generate_quality = _legacy._generate_quality_testpoints
_legacy_load_curated = _legacy._load_curated_testpoints

_ACTIVE_CONTEXT: Dict[str, Any] = {}


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _compact(value: Any, limit: int = 100) -> str:
    if isinstance(value, list):
        value = "；".join(str(item) for item in value)
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _tp_id(prefix: str, seed: str, index: int) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8].upper()
    return f"{prefix}-{index + 1:03d}-{digest}"


def _impact_testpoints(business_impact: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Derive business-language positive, condition and resilience scenarios."""
    result = []
    if business_impact.get("status") in {"skipped", "blocked"}:
        return result
    for impact in business_impact.get("items", []):
        confidence = impact.get("confidence", "unknown")
        if confidence not in {"confirmed", "high", "medium"}:
            continue
        impact_id = impact.get("impact_id", "")
        scenario = _compact(impact.get("business_scenario"), 60)
        trigger = _compact(impact.get("trigger"), 80)
        preconditions = impact.get("preconditions") or ["满足业务准入条件"]
        condition = _compact(preconditions[0], 60)
        new_behavior = _compact(impact.get("new_behavior"), 110)
        evidence = list(impact.get("evidence_ids") or [])

        result.append(
            {
                "testpoint_id": _tp_id("TP-BI", f"{impact_id}:positive", len(result)),
                "title": f"{condition}时触发{scenario}并产生需求声明的新业务结果",
                "validation_object": scenario,
                "condition": condition,
                "category": "业务影响功能测试",
                "type": "功能",
                "steps": (
                    f"1. 准备满足“{condition}”的业务对象和输入\n"
                    f"2. 通过“{trigger}”触发业务流程\n"
                    "3. 核对接口结果、业务状态、落库数据和下游副作用"
                ),
                "expected": new_behavior,
                "coverage_ids": [impact_id],
                "evidence_ids": evidence,
                "source_type": "业务影响推导",
                "confidence": confidence,
                "candidate_quality": "high" if confidence in {"confirmed", "high"} else "medium",
                "xmind_entry_status": "ready" if confidence in {"confirmed", "high"} else "review_required",
                "requires_human_confirm": confidence == "medium",
            }
        )

        if confidence in {"confirmed", "high"}:
            result.append(
                {
                    "testpoint_id": _tp_id("TP-BI", f"{impact_id}:negative", len(result)),
                    "title": f"不满足{condition}时{scenario}不产生越权状态或重复业务数据",
                    "validation_object": scenario,
                    "condition": f"不满足{condition}",
                    "category": "业务影响条件测试",
                    "type": "边界",
                    "steps": (
                        f"1. 准备不满足“{condition}”的状态、参数或配置\n"
                        f"2. 通过“{trigger}”触发同一业务流程\n"
                        "3. 核对返回提示、状态不变量、表数据和下游调用"
                    ),
                    "expected": "业务准入条件被正确执行；不产生越权状态、重复记录或非预期下游副作用",
                    "coverage_ids": [impact_id],
                    "evidence_ids": evidence,
                    "source_type": "业务影响推导",
                    "confidence": confidence,
                    "candidate_quality": "high",
                    "xmind_entry_status": "ready",
                    "requires_human_confirm": False,
                }
            )

        if impact.get("exception_impact") or impact.get("data_impact"):
            result.append(
                {
                    "testpoint_id": _tp_id("TP-BI", f"{impact_id}:resilience", len(result)),
                    "title": f"{scenario}发生失败或数据异常时保持业务状态与数据一致且可恢复",
                    "validation_object": scenario,
                    "condition": "下游失败、超时、异常数据或重复触发",
                    "category": "业务影响异常与一致性测试",
                    "type": "异常",
                    "steps": (
                        "1. 构造下游失败、超时、异常数据或重复请求\n"
                        f"2. 通过“{trigger}”触发业务流程并记录异常路径\n"
                        "3. 核对错误提示、事务回滚、重试幂等、状态和表数据"
                    ),
                    "expected": "异常有明确反馈且流程可恢复；事务、状态和业务数据一致，不产生重复副作用",
                    "coverage_ids": [impact_id],
                    "evidence_ids": evidence,
                    "source_type": "业务影响推导",
                    "confidence": confidence,
                    "candidate_quality": "high" if confidence in {"confirmed", "high"} else "medium",
                    "xmind_entry_status": "ready" if confidence in {"confirmed", "high"} else "review_required",
                    "requires_human_confirm": confidence == "medium",
                }
            )
    return result


def _comparison_testpoints(comparison: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Turn actionable comparison findings into explicit review/regression points."""
    result = []
    for index, item in enumerate(comparison.get("items", [])):
        classification = item.get("classification")
        if classification not in {"缺失", "不一致", "额外实现"}:
            continue
        coverage_id = f"RC-{index + 1:03d}"
        dimension = item.get("dimension", "")
        key = item.get("key", "")
        if classification == "额外实现":
            title = f"代码额外行为{dimension}.{key}不改变需求外业务结果并完成风险确认"
            expected = "额外实现的适用范围、兼容性和回归结果明确；未经确认不得作为需求验收结论"
        elif classification == "不一致":
            title = f"需求与代码对{dimension}.{key}定义不一致时按评审结论验证唯一业务结果"
            expected = "需求、代码和测试预期形成唯一处理决策；实际行为与确认结论一致"
        else:
            title = f"需求声明的{dimension}.{key}在目标业务场景中可验证且无实现遗漏"
            expected = "需求声明行为存在可复核实现证据；若确认缺失则阻断验收并记录缺口"
        result.append(
            {
                "testpoint_id": _tp_id("TP-RC", coverage_id, len(result)),
                "title": title,
                "validation_object": f"{dimension}.{key}",
                "condition": classification,
                "category": "需求代码一致性测试",
                "type": "确认" if classification != "缺失" else "功能",
                "steps": (
                    f"1. 复核比较结论：{item.get('reason', '')}\n"
                    "2. 准备能区分需求值和代码值的业务数据\n"
                    "3. 执行业务流程并保留接口、状态、表数据或日志证据"
                ),
                "expected": expected,
                "coverage_ids": [coverage_id],
                "evidence_ids": [coverage_id],
                "source_type": "需求代码比较推导",
                "confidence": "high",
                "candidate_quality": "high",
                "xmind_entry_status": "ready_after_review",
                "requires_human_confirm": True,
            }
        )
    return result


def _supplement_points() -> List[Dict[str, Any]]:
    return _impact_testpoints(_ACTIVE_CONTEXT.get("business_impact") or {}) + _comparison_testpoints(
        _ACTIVE_CONTEXT.get("comparison") or {}
    )


def _merge_points(base: List[Dict[str, Any]], additions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = list(base or [])
    seen = {
        re.sub(r"\s+", "", str(item.get("title") or "")).casefold()
        for item in result
    }
    for item in additions:
        key = re.sub(r"\s+", "", str(item.get("title") or "")).casefold()
        if key and key not in seen:
            result.append(item)
            seen.add(key)
    return result


def _enhanced_generate_quality(*args, **kwargs):
    points, issues = _legacy_generate_quality(*args, **kwargs)
    return _merge_points(points, _supplement_points()), issues


def _enhanced_load_curated(output_dir):
    points, path = _legacy_load_curated(output_dir)
    if points:
        covered_ids = {
            coverage_id
            for point in points
            for coverage_id in point.get("coverage_ids", [])
        }
        additions = [
            point for point in _supplement_points()
            if not set(point.get("coverage_ids", [])).intersection(covered_ids)
        ]
        points = _merge_points(points, additions)
    return points, path


def _enhanced_build_matrix(
    requirement_facts,
    risks,
    state_transitions,
    review_questions,
    testpoints,
):
    matrix = _legacy_build_matrix(
        requirement_facts,
        risks,
        state_transitions,
        review_questions,
        testpoints,
    )
    mappings = matrix.setdefault("mappings", [])
    impacts = (_ACTIVE_CONTEXT.get("business_impact") or {}).get("items", [])
    for impact in impacts:
        impact_id = impact.get("impact_id", "")
        covered_by = [
            point.get("testpoint_id")
            for point in testpoints
            if impact_id in point.get("coverage_ids", [])
        ]
        confidence = impact.get("confidence", "unknown")
        mappings.append(
            {
                "coverage_id": impact_id,
                "coverage_type": "business_impact",
                "title": impact.get("business_scenario", ""),
                "priority": "P1" if confidence in {"confirmed", "high"} else "P2",
                "confidence": confidence,
                "evidence_ids": impact.get("evidence_ids", []),
                "covered_by": covered_by,
                "status": "covered" if covered_by else "pending_confirmation" if confidence in {"potential", "unknown"} else "uncovered",
            }
        )

    comparison = _ACTIVE_CONTEXT.get("comparison") or {}
    for index, item in enumerate(comparison.get("items", [])):
        classification = item.get("classification", "证据不足")
        if classification == "一致":
            continue
        coverage_id = f"RC-{index + 1:03d}"
        covered_by = [
            point.get("testpoint_id")
            for point in testpoints
            if coverage_id in point.get("coverage_ids", [])
        ]
        mappings.append(
            {
                "coverage_id": coverage_id,
                "coverage_type": "requirement_code_comparison",
                "title": f"{classification}：{item.get('dimension', '')}.{item.get('key', '')}",
                "priority": "P1" if classification in {"缺失", "不一致"} else "P2",
                "classification": classification,
                "decision_status": item.get("decision_status") or (
                    "test_required" if classification in {"缺失", "不一致", "额外实现"} else "pending"
                ),
                "covered_by": covered_by,
                "status": "covered" if covered_by else "pending_confirmation" if classification == "证据不足" else "uncovered",
            }
        )

    stats = matrix.setdefault("statistics", {})
    confirmed = [item for item in impacts if item.get("confidence") in {"confirmed", "high"}]
    covered_confirmed = [
        mapping
        for mapping in mappings
        if mapping.get("coverage_type") == "business_impact"
        and mapping.get("confidence") in {"confirmed", "high"}
        and mapping.get("covered_by")
    ]
    stats.update(
        {
            "business_impact_count": len(impacts),
            "confirmed_business_impact_count": len(confirmed),
            "confirmed_business_impact_covered": len(covered_confirmed),
            "confirmed_business_impact_coverage_rate": (
                None if not confirmed else round(len(covered_confirmed) / len(confirmed), 4)
            ),
            "requirement_code_finding_count": sum(
                1 for item in comparison.get("items", [])
                if item.get("classification") != "一致"
            ),
        }
    )
    matrix["business_impacts"] = impacts
    matrix["requirement_code_comparison"] = comparison.get("items", [])
    no_code = (_ACTIVE_CONTEXT.get("business_impact") or {}).get("status") == "skipped"
    matrix["business_impact_applicable"] = not no_code
    matrix["comparison_status"] = comparison.get("status", "completed")
    matrix["traceability_chain"] = "RQ → TP" if no_code else "RQ → BI → RC → TP"
    matrix["chain_applicability"] = {
        "RQ": "适用：需求事实", "BI": "不适用：代码分析未执行" if no_code else "适用：代码业务影响",
        "RC": "已跳过" if comparison.get("status") == "skipped" else "适用：需求代码比较",
        "TP": "需求测试设计" if no_code else "需求与代码证据测试设计",
    }
    return matrix


def _enhanced_run_gates(coverage_matrix, testpoints):
    report = _legacy_run_gates(coverage_matrix, testpoints)
    gates = report.setdefault("gates", [])
    high_uncovered = [
        item
        for item in coverage_matrix.get("mappings", [])
        if item.get("coverage_type") == "business_impact"
        and item.get("confidence") in {"confirmed", "high"}
        and not item.get("covered_by")
    ]
    gates.append(
        {
            "gate_id": "G8",
            "name": "确定业务影响覆盖",
            "status": "skipped" if coverage_matrix.get("business_impact_applicable") is False else "passed" if not high_uncovered else "failed",
            "message": "代码业务影响不适用，未执行覆盖检查" if coverage_matrix.get("business_impact_applicable") is False else f"未覆盖确定/高置信业务影响 {len(high_uncovered)} 条",
            "blocking": bool(high_uncovered),
        }
    )
    actionable_uncovered = [
        item
        for item in coverage_matrix.get("mappings", [])
        if item.get("coverage_type") == "requirement_code_comparison"
        and item.get("classification") in {"缺失", "不一致", "额外实现"}
        and not item.get("covered_by")
    ]
    pending_evidence = [
        item
        for item in coverage_matrix.get("mappings", [])
        if item.get("coverage_type") == "requirement_code_comparison"
        and item.get("classification") == "证据不足"
        and item.get("decision_status") == "pending"
    ]
    formal = (_ACTIVE_CONTEXT.get("business_gate") or {}).get("analysis_mode") == "uat"
    comparison_failed = bool(actionable_uncovered) or bool(formal and pending_evidence)
    gates.append(
        {
            "gate_id": "G9",
            "name": "需求代码异常项处理",
            "status": "skipped" if coverage_matrix.get("comparison_status") == "skipped" else "failed" if comparison_failed else "warning" if pending_evidence else "passed",
            "message": (
                "需求代码比较已跳过，未执行异常项检查" if coverage_matrix.get("comparison_status") == "skipped" else
                f"未覆盖异常项 {len(actionable_uncovered)} 个；"
                f"证据不足待决策 {len(pending_evidence)} 个"
            ),
            "blocking": comparison_failed,
        }
    )
    blocking = [
        item for item in gates
        if item.get("blocking") and item.get("status") == "failed"
    ]
    report["summary"] = {
        "status": "blocked" if blocking else "passed",
        "blocking_count": len(blocking),
        "warning_count": sum(1 for item in gates if item.get("status") == "warning"),
    }
    return report


def _require_testpoint_inputs(output_dir, code_analysis):
    """Require the new evidence chain and block failed formal-UAT gates."""
    _legacy_require_inputs(output_dir, code_analysis)
    internal = Path(output_dir) / ".ai-internal"
    decision_path = internal / "decision_register.json"
    if not decision_path.exists():
        raise RuntimeError("评审决策门禁未通过：缺少 .ai-internal/decision_register.json")
    decision_register = _load_json(decision_path)
    decision_blockers = testpoint_blocking_issues(decision_register)
    if decision_blockers:
        raise RuntimeError(
            "评审决策门禁未通过：" + "；".join(decision_blockers[:12])
        )
    impact = _load_json(internal / "business_impact.json")
    gate = _load_json(internal / "business_impact_gate.json")
    if not code_analysis:
        targets = _load_json(internal / "code_targets.json")
        comparison = _load_json(internal / "requirement_code_comparison.json")
        if targets.get("targets") or targets.get("manifest_errors"):
            raise RuntimeError("代码目标阻断时不能按纯需求流程放行")
        if not (
            impact.get("status") == "skipped" and impact.get("analysis_mode") == "not_applicable"
            and not impact.get("items") and not impact.get("evidence_catalog")
            and gate.get("summary", {}).get("status") == "skipped"
            and gate.get("analysis_mode") == "not_applicable"
            and not any(g.get("blocking") or g.get("status") != "skipped" for g in gate.get("gates", []))
            and comparison.get("status") == "skipped" and not comparison.get("items")
        ):
            raise RuntimeError("无代码流程的业务影响状态不一致，请重新执行需求分析以记录明确的 skipped 状态")
        return
    targets = _load_json(internal / "code_targets.json")
    execution = business_impact_execution(targets.get("targets") or [], targets.get("manifest_errors"))
    if not (
        execution["status"] == "completed" and impact.get("status") == "completed"
        and impact.get("items")
        and gate.get("summary", {}).get("status") in {"passed", "passed_with_warnings"}
    ):
        raise RuntimeError("业务影响未完成或证据状态不一致，禁止进入代码测试设计")
    missing = [
        path
        for path in (
            internal / "business_impact.json",
            internal / "business_impact_gate.json",
            internal / "codegraph_query_plan.json",
            internal / "codegraph_impact.json",
            internal / "uat_merge_gate.json",
        )
        if not path.exists()
    ]
    if missing:
        raise RuntimeError(
            "测试点阶段缺少业务影响证据：" + "；".join(str(path) for path in missing)
        )
    gate = _load_json(internal / "business_impact_gate.json")
    blocking = [item for item in gate.get("gates", []) if item.get("blocking")]
    readability_blocking = [
        item for item in blocking if item.get("gate_id") in {"BI-G7", "BI-G8", "BI-G9"}
    ]
    if blocking:
        failures = [f"{item.get('gate_id')} {item.get('message')}" for item in blocking]
        raise RuntimeError("业务影响硬门禁未通过：" + "；".join(failures))
    review_gate_path = internal / "code_review_readability_gate.json"
    if code_analysis is not None and review_gate_path.exists():
        review_gate = _load_json(review_gate_path)
        review_blocking = [item for item in review_gate.get("gates", []) if item.get("blocking")]
        if review_blocking:
            failures = [f"{item.get('gate_id')} {item.get('message')}" for item in review_blocking]
            raise RuntimeError("代码评审报告可读性门禁未通过：" + "；".join(failures))


def run_testpoint_generate(
    req_dir,
    output_dir,
    project_info,
    code_analysis,
    kb_manager=None,
):
    """Generate TP candidates from RQ, BI and RC evidence under strict gates."""
    global _ACTIVE_CONTEXT
    internal = Path(output_dir) / ".ai-internal"
    _ACTIVE_CONTEXT = {
        "business_impact": _load_json(internal / "business_impact.json"),
        "business_gate": _load_json(internal / "business_impact_gate.json"),
        "comparison": _load_json(internal / "requirement_code_comparison.json"),
        "decision_register": _load_json(internal / "decision_register.json"),
    }
    originals = {
        "_require_testpoint_inputs": _legacy._require_testpoint_inputs,
        "_generate_quality_testpoints": _legacy._generate_quality_testpoints,
        "_load_curated_testpoints": _legacy._load_curated_testpoints,
        "_build_coverage_matrix": _legacy._build_coverage_matrix,
        "_run_minimal_quality_gates": _legacy._run_minimal_quality_gates,
    }
    _legacy._require_testpoint_inputs = _require_testpoint_inputs
    _legacy._generate_quality_testpoints = _enhanced_generate_quality
    _legacy._load_curated_testpoints = _enhanced_load_curated
    _legacy._build_coverage_matrix = _enhanced_build_matrix
    _legacy._run_minimal_quality_gates = _enhanced_run_gates
    try:
        return _legacy_run_testpoint_generate(
            req_dir, output_dir, project_info, code_analysis, kb_manager
        )
    finally:
        for name, value in originals.items():
            setattr(_legacy, name, value)
        _ACTIVE_CONTEXT = {}


__all__ = [
    name
    for name, value in globals().items()
    if callable(value) and getattr(value, "__module__", None) == __name__
]
