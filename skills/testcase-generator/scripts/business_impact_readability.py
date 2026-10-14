#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Turn code-analysis structures into grouped, tester-readable business impacts."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List, Sequence

RAW_MARKERS = (
    "if_else_rules", "exception_rules", "throw_rules", "validation_rules",
    "value_annotations", "logging_patterns", "source_file", "language",
)
BUSINESS_FIELDS = (
    "validation_object", "business_scenario", "trigger", "preconditions",
    "new_behavior", "expected_result", "business_rule", "data_impact",
    "state_impact", "exception_impact", "test_focus", "risk_notes",
)


def _list(value: Any) -> List[Any]:
    if value in (None, ""):
        return []
    return value if isinstance(value, list) else [value]


def _unique(values: Iterable[Any], limit: int = 20) -> List[str]:
    result: List[str] = []
    seen = set()
    for value in values:
        text = compact(value)
        key = text.lower()
        if text and key not in seen:
            result.append(text)
            seen.add(key)
        if len(result) >= limit:
            break
    return result


def compact(value: Any, limit: int = 220) -> str:
    """Return a short scalar summary; never serialize parser dictionaries."""
    if value in (None, ""):
        return ""
    if isinstance(value, dict):
        for key in ("name", "job_name", "method", "path", "table", "condition",
                    "statement", "validation", "config_key", "message", "source"):
            if value.get(key) not in (None, "", [], {}):
                return compact(value[key], limit)
        return ""
    if isinstance(value, (list, tuple, set)):
        return "；".join(_unique(value, 5))[:limit]
    text = re.sub(r"\s+", " ", str(value)).strip(" |")
    if any(marker in text for marker in RAW_MARKERS):
        return ""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _normal(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", compact(value).lower())


def _name(value: Any) -> str:
    if isinstance(value, dict):
        return compact(value.get("job_name") or value.get("name") or value.get("method")
                       or value.get("path") or value.get("source"))
    return compact(value)


def _target_analysis(target: Dict[str, Any]) -> Dict[str, Any]:
    value = target.get("analysis") or {}
    return value if isinstance(value, dict) else {}


def _sql_tables(sql_item: Any) -> List[str]:
    if not isinstance(sql_item, dict):
        return []
    values: List[Any] = []
    analysis = sql_item.get("analysis") or {}
    for owner in (sql_item, analysis if isinstance(analysis, dict) else {}):
        for key in ("tables", "source_tables", "target_tables", "affected_tables"):
            raw = owner.get(key)
            if isinstance(raw, dict):
                for nested_key in ("sources", "targets", "joins", "aliases"):
                    nested = raw.get(nested_key)
                    if isinstance(nested, dict):
                        values.extend(nested.values())
                    else:
                        values.extend(_list(nested))
            else:
                values.extend(_list(raw))
    text = " ".join(compact(item, 1000) for item in values)
    if not text:
        text = str(sql_item.get("sql") or sql_item.get("source") or "")
    return _unique(re.findall(r"\b(?:t|tb|biz|sys)_[a-zA-Z0-9_]+\b", text, re.I), 20)


def _table_label(table: str) -> str:
    known = {
        "t_ckts_ckmx_sbxx_ymqy": "出口退税出口明细历史数据",
        "t_ckts_jhmx_sbxx_ymqy": "出口退税进货明细历史数据",
        "t_ckts_mdtshzxx_sbxxmx_shqy": "免抵退税汇总明细历史数据",
    }
    return known.get(table.lower(), f"业务表 {table}")


def _groups(target: Dict[str, Any]) -> List[Dict[str, str]]:
    analysis = _target_analysis(target)
    groups: List[Dict[str, str]] = []
    seen = set()

    def add(kind: str, name: str, source: str = "") -> None:
        key = _normal(name)
        if not key or key in seen:
            return
        seen.add(key)
        groups.append({"kind": kind, "name": compact(name), "source": compact(source)})

    for value in _list(analysis.get("scheduled_tasks")):
        add("scheduled_task", _name(value), compact(value.get("source_file")) if isinstance(value, dict) else "")
    task_keys = set(seen)
    for value in _list(analysis.get("entry_points")):
        name = _name(value)
        if _normal(name) not in task_keys:
            add("entry_point", name, compact(value.get("source_file")) if isinstance(value, dict) else "")
    for value in _list(analysis.get("api_endpoints")):
        name = _name(value)
        if _normal(name) not in task_keys:
            add("api", name)
    for sql_item in _list(analysis.get("sql_analysis")):
        for table in _sql_tables(sql_item):
            add("data_table", table, compact(sql_item.get("source_file") or sql_item.get("file")) if isinstance(sql_item, dict) else "")
    return groups


def _evidence_for(group: Dict[str, str], old_items: Sequence[Dict[str, Any]], target_id: str) -> List[str]:
    name_key = _normal(group["name"])
    matching = [item for item in old_items
                if item.get("target_id") == target_id and
                (name_key in _normal(item.get("trigger")) or name_key in _normal(item.get("business_scenario")))]
    candidates = matching or [item for item in old_items if item.get("target_id") == target_id]
    evidence = _unique((eid for item in candidates for eid in _list(item.get("evidence_ids"))), 8)
    if not any(item.startswith("CE-") for item in evidence):
        digest = hashlib.sha1(f"{target_id}:{group['name']}".encode("utf-8")).hexdigest()[:8].upper()
        evidence.append(f"CE-GROUP-{digest}")
    return evidence


def _technical_evidence(group: Dict[str, str], target: Dict[str, Any]) -> List[str]:
    analysis = _target_analysis(target)
    values = [group.get("source"), group.get("name")]
    for path in _list(analysis.get("files_changed")):
        text = compact(path)
        if group["name"].lower() in text.lower() or group["kind"] == "data_table" and "mapper" in text.lower():
            values.append(text)
    return _unique(values, 8)


def _rules(target: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = _target_analysis(target).get("business_rules") or {}
    if isinstance(raw, list):
        containers = raw
    elif isinstance(raw, dict):
        containers = [raw]
    else:
        containers = []
    result: List[Dict[str, Any]] = []
    allowed = ("if_else_rules", "exception_rules", "throw_rules", "validation_rules", "key_decision_points")
    for container in containers:
        if not isinstance(container, dict):
            continue
        for kind in allowed:
            for rule in _list(container.get(kind)):
                if isinstance(rule, dict):
                    copy = dict(rule)
                    copy["_kind"] = kind
                    result.append(copy)
    return result


def _human_rule(rule: Dict[str, Any]) -> str:
    condition = compact(rule.get("condition") or rule.get("statement") or rule.get("validation"), 180)
    action = compact(rule.get("action") or rule.get("catch_action"), 160)
    joined = f"{condition} {action}".lower()
    if "targettaxids" in joined and ("!=" in joined or "isempty" in joined):
        return "传入指定税号时按指定范围回填；未传税号时按全部待处理税号回填"
    if "lastid > 0" in joined:
        return "上一页返回继续处理位置后，下一页从该位置之后读取，避免重复或漏处理"
    if "totalfortaxid > 0" in joined:
        return "该税号仍有待处理数据时继续分页；全部处理完成后结束该税号"
    if "param == null" in joined or "param.trim().isempty" in joined:
        return "未传任务参数时按全量模式执行；传入税号时按指定范围执行"
    if "taxids.isempty" in joined or "taxids == null" in joined:
        return "没有待处理税号时任务正常结束，不产生数据修改"
    if "page" in joined and ("isempty" in joined or "size" in joined):
        return "当前页无待处理数据时结束该税号的分页处理"
    if "dedup_hash" in joined and ("= 0" in joined or "zero" in joined):
        return "仅处理去重标识尚未回填的数据"
    if rule.get("_kind") == "exception_rules":
        return "单个税号处理失败时记录失败信息，并验证是否影响后续税号继续处理"
    if rule.get("_kind") == "throw_rules":
        return "参数或业务状态不满足要求时返回明确失败结果，不写入错误数据"
    return ""


def _relevant_rules(group: Dict[str, str], target: Dict[str, Any]) -> List[str]:
    tokens = {token for token in re.split(r"[^a-z0-9]+", group["name"].lower()) if len(token) >= 4}
    scored = []
    for rule in _rules(target):
        source = str(rule.get("source_file") or "").lower()
        text = str(rule).lower()
        score = sum(3 for token in tokens if token in text)
        if group["kind"] == "scheduled_task" and ("migrationjob" in source or "deduphash" in text):
            score += 3
        if group["kind"] == "data_table" and group["name"].lower() in text:
            score += 4
        if score:
            scored.append((score, _human_rule(rule)))
    return _unique((text for _, text in sorted(scored, reverse=True) if text), 5)


def _scenario(group: Dict[str, str]) -> Dict[str, Any]:
    name = group["name"]
    if group["kind"] == "scheduled_task":
        return {
            "category": "历史数据迁移任务",
            "object": f"定时任务 {name}",
            "scenario": f"执行 {name}，回填历史出口退税数据的去重标识",
            "trigger": f"调度平台手工或按配置触发 {name}",
            "preconditions": ["任务开关已开启", "存在去重标识未回填的历史数据", "数据库连接和调度环境正常"],
            "expected": ["指定税号模式只处理指定企业", "未指定税号时覆盖全部待处理企业", "处理完成后任务给出明确成功、失败及数量结果"],
            "data": ["核对处理前后待回填记录数", "核对已处理记录去重标识不再为初始值", "核对其他企业及非目标数据不被修改"],
            "exceptions": ["单个税号失败需有可定位记录", "空数据应正常结束", "任务重跑不得重复或破坏已回填数据"],
            "focus": ["指定税号与全量两种模式", "多页数据、最后一页和空页", "部分失败后的继续处理与重跑", "大数据量耗时和锁等待"],
        }
    if group["kind"] == "data_table":
        label = _table_label(name)
        return {
            "category": "数据库落库与兼容性",
            "object": f"{label}（{name}）",
            "scenario": f"历史数据回填及新数据写入后，{label}可按去重标识正确识别重复记录",
            "trigger": "迁移任务回填历史数据，或采集流程写入新数据",
            "preconditions": ["表结构和索引已发布", "准备首次、重复及历史存量数据", "迁移任务与正常采集使用同一去重口径"],
            "expected": ["历史数据回填后可正常参与去重", "同一业务数据重复采集不新增重复记录", "不同业务数据不因哈希相同或字段缺失被误判"],
            "data": ["核对税号、业务主键、hash_code 与 dedup_hash", "核对初始值记录均被覆盖且无漏处理", "核对重复数据数量和唯一性约束结果"],
            "exceptions": ["回填中断后可安全重跑", "写入失败不得形成半更新数据", "哈希碰撞或空关键字段需有明确处理结果"],
            "focus": ["首次落库、重复落库和跨批次落库", "历史数据回填前后兼容", "并发写入及事务一致性", "索引命中与批量处理性能"],
        }
    label = "接口" if group["kind"] == "api" else "业务入口"
    return {
        "category": label,
        "object": f"{label} {name}",
        "scenario": f"通过 {name} 触发本次需求对应处理",
        "trigger": f"调用或执行 {name}",
        "preconditions": ["调用参数和运行环境满足要求"],
        "expected": ["正常输入得到明确成功结果", "异常输入不产生错误数据"],
        "data": ["核对处理前后业务数据及状态"],
        "exceptions": ["失败时返回可定位信息并可恢复"],
        "focus": ["正常、边界、异常和重复执行"],
    }


def make_tester_readable(
    payload: Dict[str, Any],
    requirement_facts: Dict[str, Any],
    target_results: List[Dict[str, Any]],
    project_info: Dict[str, Any],
) -> Dict[str, Any]:
    """Replace raw parser-driven impacts with grouped test-validation objects."""
    old_items = payload.get("items") or []
    domain = compact(project_info.get("requirement_name") or project_info.get("name"))
    if not domain:
        domain = compact(requirement_facts.get("requirement_name") or requirement_facts.get("title")) or "本次需求"
    items: List[Dict[str, Any]] = []
    for target in target_results:
        target_id = compact(target.get("target_id")) or "unknown"
        target_old = [item for item in old_items if item.get("target_id") == target_id]
        confidence = compact((target_old[0] if target_old else {}).get("confidence")) or "medium"
        actors = (target_old[0] if target_old else {}).get("affected_actor") or ["测试人员、运维人员及出口退税业务使用方"]
        for group in _groups(target):
            scene = _scenario(group)
            rules = _relevant_rules(group, target)
            digest = hashlib.sha1(f"{target_id}:{group['kind']}:{group['name']}".encode("utf-8")).hexdigest()[:8].upper()
            item = {
                "impact_id": f"BI-{len(items)+1:03d}-{digest}",
                "target_id": target_id,
                "business_domain": domain,
                "affected_actor": _unique(_list(actors), 5),
                "impact_category": scene["category"],
                "validation_object": scene["object"],
                "business_scenario": scene["scenario"],
                "trigger": scene["trigger"],
                "preconditions": scene["preconditions"],
                "previous_behavior": "历史数据或原流程尚未使用本次去重标识处理逻辑，基线细节需结合历史用例确认",
                "new_behavior": scene["expected"][0],
                "expected_result": scene["expected"],
                "business_rule": rules or ["仅处理本需求范围内的目标对象，非目标数据保持不变"],
                "data_impact": scene["data"],
                "state_impact": ["任务、数据及失败记录状态应与实际处理结果一致"],
                "downstream_impact": ["后续重复数据识别和查询结果应保持正确"],
                "exception_impact": scene["exceptions"],
                "test_focus": scene["focus"],
                "risk_notes": ["需求未明确的口径需在测试前确认；不能用代码推断替代产品确认"],
                "regression_scope": [scene["object"]],
                "technical_evidence": _technical_evidence(group, target),
                "confidence": confidence,
                "evidence_ids": _evidence_for(group, old_items, target_id),
            }
            items.append(item)

    if not items:
        # Preserve unusual analyzer output, but sanitize and deduplicate validation objects.
        fallback_seen = set()
        for old in old_items:
            copy = dict(old)
            copy["impact_category"] = "待归类业务影响"
            copy["validation_object"] = compact(old.get("trigger") or old.get("business_scenario")) or "待确认对象"
            copy["expected_result"] = _unique(_list(old.get("new_behavior")), 3) or ["按需求产生可验证结果"]
            copy["test_focus"] = _unique(_list(old.get("regression_scope")), 5) or ["正常、边界与异常场景"]
            copy["risk_notes"] = ["自动分析未识别出可归并对象，需人工确认"]
            for field in BUSINESS_FIELDS:
                value = copy.get(field)
                copy[field] = _unique(_list(value), 8) if isinstance(value, list) else compact(value)
            copy["business_domain"] = compact(copy.get("business_domain")) or domain
            copy["affected_actor"] = _unique(_list(copy.get("affected_actor")), 5) or ["测试人员及需求涉及用户"]
            copy["business_scenario"] = compact(copy.get("business_scenario")) or f"验证{copy['validation_object']}的需求行为"
            copy["trigger"] = compact(copy.get("trigger")) or copy["validation_object"]
            copy["preconditions"] = _unique(_list(copy.get("preconditions")), 5) or ["准备满足需求描述的正常输入和运行环境"]
            copy["new_behavior"] = compact(copy.get("new_behavior")) or copy["expected_result"][0]
            copy["confidence"] = compact(copy.get("confidence")) or "medium"
            evidence = _unique(_list(copy.get("evidence_ids")), 8)
            if not any(value.startswith("CE-") for value in evidence):
                digest = hashlib.sha1(copy["validation_object"].encode("utf-8")).hexdigest()[:8].upper()
                evidence.append(f"CE-GROUP-{digest}")
            copy["evidence_ids"] = evidence
            key = (_normal(copy["impact_category"]), _normal(copy["validation_object"]))
            if key in fallback_seen:
                continue
            fallback_seen.add(key)
            items.append(copy)

    payload = dict(payload)
    payload["schema_version"] = "1.1.0"
    payload["items"] = items
    summary = dict(payload.get("summary") or {})
    counts = {"confirmed": 0, "high": 0, "medium": 0, "potential": 0}
    for item in items:
        level = str(item.get("confidence") or "medium").lower()
        counts[level if level in counts else "potential"] += 1
    summary.update({
        "total": len(items),
        **counts,
        "grouped_objects": len(items),
        "tester_readable": sum(1 for item in items if not readability_issues(item)),
    })
    payload["summary"] = summary
    return payload


def readability_issues(item: Dict[str, Any]) -> List[str]:
    """Return hard-gate issues for tester-facing fields."""
    issues: List[str] = []
    for field in BUSINESS_FIELDS:
        value = item.get(field)
        values = _list(value)
        if any(isinstance(part, (dict, list, tuple, set)) for part in values):
            issues.append(f"{field}:包含嵌套结构")
            continue
        for part in values:
            text = str(part)
            if any(marker in text for marker in RAW_MARKERS):
                issues.append(f"{field}:包含解析器字段")
                break
            if ("{'" in text or '[{"' in text or '"analysis": {' in text):
                issues.append(f"{field}:包含原始字典")
                break
            if len(text) > 320:
                issues.append(f"{field}:单项超过320字")
                break
    return _unique(issues, 30)
