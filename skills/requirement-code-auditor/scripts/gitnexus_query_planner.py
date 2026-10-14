"""Deterministic requirement-fact to GitNexus query planning."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List


DIMENSION_TERMS = {
    "apis": "API 路由 controller handler endpoint consumer",
    "fields": "字段 field response request mapping DTO",
    "tables": "表 table SQL Mapper MyBatis 查询写入",
    "rules": "业务规则 校验 条件 分支 异常 validation rule",
    "sorting": "排序 order by sort",
    "pagination": "分页 page size offset limit",
    "errors": "错误码 异常 error exception response",
    "jobs": "定时任务 scheduler cron job task",
    "configs": "配置 config property yaml switch",
    "side_effects": "写入 更新 删除 落库 side effect transaction",
    "scenarios": "场景 workflow process",
    "state_transitions": "状态 state transition",
    "calculations": "计算 公式 金额 税额 calculation formula",
    "integrations": "外部系统 接口 client consumer integration",
}


def _compact(value: Any, limit: int = 240) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _fact_text(fact: Dict[str, Any]) -> str:
    parts = [fact.get("subject"), fact.get("object"), fact.get("behavior"), fact.get("source_quote")]
    value = fact.get("value")
    if isinstance(value, dict):
        parts.extend(str(item) for item in value.values())
    else:
        parts.append(value)
    return _compact(" ".join(str(item) for item in parts if item not in (None, "")))


def build_query_plan(
    fact: Dict[str, Any],
    target: Dict[str, Any],
    *,
    max_queries: int = 3,
) -> List[Dict[str, Any]]:
    """Build bounded domain-specific queries without inventing requirement values."""
    dimension = str(fact.get("type") or "rules")
    base = _fact_text(fact)
    terms = DIMENSION_TERMS.get(dimension, "symbol method class function process")
    service = " ".join(str(item) for item in target.get("services", []) if item)
    queries = [
        _compact(f"{base} {terms}"),
        _compact(f"{fact.get('object') or base} {service} implementation source"),
        _compact(f"{fact.get('source_quote') or base} call flow downstream"),
    ]
    unique: List[str] = []
    for query in queries:
        if query and query.casefold() not in {item.casefold() for item in unique}:
            unique.append(query)
    task_context = _compact(f"审计 {dimension} 需求是否在 {target.get('repo_key') or target.get('target_id')} 的 {target.get('branch')} 分支实现")
    goal = _compact(f"查找 {dimension} 的入口、行为、异常分支、下游调用和源码锚点")
    return [
        {
            "dimension": dimension,
            "stable_key": fact.get("stable_key", ""),
            "query": query,
            "task_context": task_context,
            "goal": goal,
            "source_anchor": {
                "document": fact.get("source_document", ""),
                "anchor": fact.get("source_anchor", ""),
                "quote": fact.get("source_quote", ""),
            },
        }
        for query in unique[: max(1, int(max_queries or 3))]
    ]


def build_query_plans(facts: Iterable[Dict[str, Any]], target: Dict[str, Any], *, max_queries: int = 3) -> List[Dict[str, Any]]:
    plans: List[Dict[str, Any]] = []
    for fact in facts:
        if fact.get("comparable") is False:
            continue
        plans.extend(build_query_plan(fact, target, max_queries=max_queries))
    return plans
