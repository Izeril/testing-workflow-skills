"""Build traceable requirement facts in three deterministic stages."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Tuple


CONFIDENCE_WEIGHTS = {
    "source_clarity": 0.30,
    "structure_completeness": 0.25,
    "traceability": 0.20,
    "cross_document_consistency": 0.15,
    "normalization_certainty": 0.10,
}
DIMENSIONS = (
    "fields", "apis", "tables", "rules", "sorting", "pagination", "errors",
    "jobs", "configs", "side_effects", "scenarios", "state_transitions",
    "calculations", "integrations",
)
CORE_TOKENS = ("必须", "应当", "不得", "权限", "准入", "状态", "金额", "税额", "错误码", "事务", "外部系统", "新增", "修改", "删除")


def _compact(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" ：:;,，；。")


def _stable(prefix: str, *parts: Any) -> str:
    values = [re.sub(r"\s+", "_", _compact(item).casefold()) for item in parts if _compact(item)]
    raw = ":".join(values)
    if raw and len(raw) <= 100 and re.fullmatch(r"[\w\-./:\u4e00-\u9fff]+", raw):
        return f"{prefix}:{raw}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _sections(text: str) -> Iterable[Tuple[str, str, int]]:
    document = "normalized_requirement.md"
    heading = "正文"
    for number, line in enumerate(text.splitlines(), 1):
        doc_match = re.match(r"^===\s+(.+?)\s+===$", line.strip())
        if doc_match:
            document = doc_match.group(1)
            heading = "正文"
            continue
        heading_match = re.match(r"^#{1,6}\s+(.+)$", line.strip())
        if heading_match:
            heading = heading_match.group(1).strip()
            continue
        value = line.strip().lstrip("-*+0123456789.、 ")
        if value and not re.fullmatch(r"[-|: ]+", value):
            yield document, f"{heading}:L{number}", value


def _candidate(
    fact_type: str,
    document: str,
    anchor: str,
    quote: str,
    *,
    subject: str = "",
    behavior: str = "",
    obj: str = "",
    value: Any = None,
    fields: Dict[str, Any] | None = None,
    ambiguity: str = "",
) -> Dict[str, Any]:
    return {
        "candidate_type": fact_type,
        "source_document": document,
        "source_anchor": anchor,
        "source_quote": quote,
        "subject": _compact(subject),
        "precondition": "",
        "trigger": "",
        "behavior": _compact(behavior),
        "object": _compact(obj),
        "value": value,
        "expected_result": _compact(behavior),
        "exception": "",
        "ambiguity": ambiguity,
        **(fields or {}),
    }


def extract_candidate_facts(normalized_text: str) -> List[Dict[str, Any]]:
    """A3.1: extract candidates without inventing values absent from source text."""
    candidates: List[Dict[str, Any]] = []
    for document, anchor, quote in _sections(normalized_text):
        sorting = re.search(r"按\s*([A-Za-z_][\w.]*|[\u4e00-\u9fff]{2,12})(?:字段)?\s*(最新优先|从新到旧|倒序|降序|升序|正序)", quote)
        if sorting:
            direction = "asc" if sorting.group(2) in {"升序", "正序"} else "desc"
            candidates.append(_candidate("sorting", document, anchor, sorting.group(0), subject="查询结果", behavior="排序", obj=sorting.group(1), value=direction, fields={"field": sorting.group(1), "direction": direction}))

        page_size = re.search(r"每页\s*(\d+)\s*条", quote)
        page_start = re.search(r"页码(?:从|起始(?:于|为)?)\s*(\d+)", quote)
        page_max = re.search(r"(?:最多|最大)\s*(\d+)\s*页", quote)
        overflow = "empty_list" if re.search(r"超过(?:最后一页|最大页数).*?(?:空列表|空集合|无数据)", quote) else ""
        if page_size or page_start or page_max or overflow:
            fields: Dict[str, Any] = {}
            if page_size:
                fields["page_size"] = int(page_size.group(1))
            if page_start:
                fields["page_start"] = int(page_start.group(1))
            if page_max:
                fields["max_pages"] = int(page_max.group(1))
            if overflow:
                fields["overflow_behavior"] = overflow
            candidates.append(_candidate("pagination", document, anchor, quote, subject="分页查询", behavior="分页", obj="查询结果", value=fields, fields=fields))

        error = re.search(r"(?:错误码|返回码|错误编号)\s*[：:]?\s*([A-Za-z0-9_-]+)", quote)
        if error:
            message_match = re.search(r"(?:提示|文案)\s*[‘'\"“]?([^’'\"”。，；]+)", quote)
            condition = _compact(re.split(r"(?:时|则)?\s*(?:返回|报错|提示|错误码)", quote, maxsplit=1)[0])
            message = _compact(message_match.group(1)) if message_match else ""
            candidates.append(_candidate("errors", document, anchor, quote, subject=condition, behavior="返回错误", obj=error.group(1), value={"error_code": error.group(1), "message": message}, fields={"condition": condition, "error_code": error.group(1), "message": message}))

        state = re.search(r"(?:状态)?\s*(?:从\s*([\u4e00-\u9fffA-Za-z0-9_-]+)\s*)?(?:改为|变为|更新为|流转到)\s*([\u4e00-\u9fffA-Za-z0-9_-]+)", quote)
        if state:
            value = {"from": state.group(1) or "", "to": state.group(2)}
            candidates.append(_candidate("state_transitions", document, anchor, quote, subject="业务对象", behavior="状态流转", obj=state.group(2), value=value, fields=value))

        calculation = re.search(r"([^，。；]{1,30})(?:等于|=)([^，。；]{1,80})", quote)
        if calculation and any(token in quote for token in ("金额", "税额", "费", "数量", "比例", "计算", "合计")):
            candidates.append(_candidate("calculations", document, anchor, quote, subject=calculation.group(1), behavior="计算", obj=calculation.group(1), value=_compact(calculation.group(2)), fields={"formula": _compact(calculation.group(2))}))

        side_effect = re.search(r"(?:新增|写入|保存|更新|删除)\s*(?:一条|一笔|对应)?\s*([\u4e00-\u9fffA-Za-z0-9_.-]{2,40})(?:记录|数据|日志|表)", quote)
        if side_effect:
            candidates.append(_candidate("side_effects", document, anchor, quote, subject="业务操作", behavior=side_effect.group(0), obj=side_effect.group(1), value=side_effect.group(0)))

        integration = re.search(r"(?:调用|请求|推送到|发送到)\s*([\u4e00-\u9fffA-Za-z0-9_.-]{2,40})(?:系统|接口|服务)", quote)
        if integration:
            candidates.append(_candidate("integrations", document, anchor, quote, subject="当前系统", behavior="外部调用", obj=integration.group(1), value=integration.group(0)))

        for field_name in re.findall(r"(?:字段|属性)\s*[`‘'\"]?([A-Za-z_][A-Za-z0-9_]*)", quote):
            candidates.append(_candidate("fields", document, anchor, quote, subject="数据对象", behavior="字段约束", obj=field_name, value=field_name, fields={"field": field_name}))
        for method, path in re.findall(r"\b(GET|POST|PUT|DELETE|PATCH)\s+(/[A-Za-z0-9_./{}\-]+)", quote, re.IGNORECASE):
            candidates.append(_candidate("apis", document, anchor, quote, subject="接口", behavior=method.upper(), obj=path, value={"method": method.upper(), "path": path}, fields={"method": method.upper(), "path": path}))

        for table in re.findall(r"(?:表|数据表)\s*[`‘'\"]?([A-Za-z_][A-Za-z0-9_.$#]*)", quote):
            candidates.append(_candidate("tables", document, anchor, quote, subject="数据", behavior="访问", obj=table, value=table, fields={"table": table}))

        config = re.search(r"(?:配置项|配置键|开关)\s*[`‘'\"]?([A-Za-z][A-Za-z0-9_.-]+)", quote)
        if config:
            candidates.append(_candidate("configs", document, anchor, quote, subject="配置", behavior="读取", obj=config.group(1), value=config.group(1), fields={"config_key": config.group(1)}))

        job = re.search(r"(?:定时任务|任务)\s*[`‘'\"]?([A-Za-z0-9_\-\u4e00-\u9fff]{2,40})", quote)
        if job and ("定时" in quote or "cron" in quote.casefold()):
            candidates.append(_candidate("jobs", document, anchor, quote, subject="任务", behavior="调度", obj=job.group(1), value=job.group(1), fields={"job_name": job.group(1)}))

        if len(_compact(quote)) >= 10 and any(token in quote for token in ("必须", "应当", "不得", "支持", "允许", "返回", "保存", "更新", "查询", "校验")) and not any(item["source_quote"] == quote for item in candidates[-8:]):
            candidates.append(_candidate("rules", document, anchor, quote, subject="业务规则", behavior=quote, obj="业务流程", value=quote))
    return candidates


def _identity(candidate: Dict[str, Any]) -> Tuple[str, str, Any]:
    kind = candidate["candidate_type"]
    if kind == "sorting":
        return _stable("sorting", candidate.get("field"), candidate.get("direction")), _stable("sorting", candidate.get("field")), candidate.get("direction")
    if kind == "pagination":
        fields = candidate.get("value") or {}
        if fields.get("page_size") is not None:
            return _stable("pagination", "page_size", fields["page_size"]), "pagination:page_size", fields["page_size"]
        return _stable("pagination", json.dumps(fields, ensure_ascii=False, sort_keys=True)), "pagination:behavior", fields
    if kind == "errors":
        return _stable("error", candidate.get("condition"), candidate.get("error_code")), _stable("error", candidate.get("condition")), candidate.get("value")
    if kind == "apis":
        return _stable("api", candidate.get("method"), candidate.get("path")), _stable("api", candidate.get("path")), candidate.get("value")
    if kind == "tables":
        return _stable("table", candidate.get("table")), _stable("table", candidate.get("table")), candidate.get("table")
    if kind == "configs":
        return _stable("config", candidate.get("config_key")), _stable("config", candidate.get("config_key")), candidate.get("value")
    if kind == "jobs":
        return _stable("job", candidate.get("job_name")), _stable("job", candidate.get("job_name")), candidate.get("value")
    return _stable(kind, candidate.get("object"), candidate.get("behavior")), _stable(kind, candidate.get("object")), candidate.get("value")


def normalize_candidates(candidates: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """A3.2: normalize, deduplicate same-source synonyms and preserve conflicts."""
    facts: List[Dict[str, Any]] = []
    seen = set()
    conflict_index: Dict[str, List[Tuple[Any, str]]] = {}
    for candidate in candidates:
        stable_key, conflict_key, comparable_value = _identity(candidate)
        dedupe_key = (stable_key, candidate["source_document"], candidate["source_anchor"])
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        fact = dict(candidate)
        fact.update({"type": fact.pop("candidate_type"), "stable_key": stable_key, "conflict_key": conflict_key})
        facts.append(fact)
        conflict_index.setdefault(conflict_key, []).append((comparable_value, candidate["source_document"]))
    conflicts = []
    for key, values in conflict_index.items():
        normalized_values = {json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) for value, _ in values}
        documents = {document for _, document in values}
        if len(normalized_values) > 1 and len(documents) > 1:
            conflicts.append({"conflict_key": key, "values": [value for value, _ in values], "source_documents": sorted(documents)})
    return facts, conflicts


def _confidence(fact: Dict[str, Any], conflicted: bool) -> Tuple[float, Dict[str, float], List[str]]:
    components = {
        "source_clarity": 1.0 if fact.get("source_quote") else 0.0,
        "structure_completeness": 1.0 if fact.get("subject") and (fact.get("behavior") or fact.get("value") not in (None, "")) else 0.5,
        "traceability": 1.0 if fact.get("source_document") and fact.get("source_anchor") else 0.0,
        "cross_document_consistency": 0.0 if conflicted else 1.0,
        "normalization_certainty": 1.0 if fact.get("type") != "candidate_unknown" else 0.5,
    }
    score = round(sum(CONFIDENCE_WEIGHTS[key] * value for key, value in components.items()), 4)
    reasons = [f"{key}={value:g}" for key, value in components.items()]
    return score, components, reasons


def validate_comparability(facts: List[Dict[str, Any]], conflicts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A3.3: assign criticality, confidence and automatic-comparison eligibility."""
    conflict_keys = {item["conflict_key"] for item in conflicts}
    for fact in facts:
        conflicted = fact["conflict_key"] in conflict_keys
        score, components, reasons = _confidence(fact, conflicted)
        level = "high" if score >= 0.8 else "medium" if score >= 0.6 else "low"
        quote = fact.get("source_quote", "")
        core = any(token in quote for token in CORE_TOKENS)
        comparable = bool(
            fact.get("source_document")
            and fact.get("source_anchor")
            and (fact.get("subject") or fact.get("object"))
            and (fact.get("behavior") or fact.get("value") not in (None, ""))
            and not conflicted
            and level != "low"
        )
        fact.update({
            "criticality": "core" if core else "normal",
            "confidence": score,
            "confidence_level": level,
            "confidence_components": components,
            "confidence_reasons": reasons,
            "comparable": comparable,
            "non_comparable_reason": "需求事实存在跨文档冲突" if conflicted else ("需求事实置信度过低" if level == "low" else ""),
            "human_review_required": conflicted or (core and score < 0.8) or level == "low",
        })
    return facts


def build_requirement_facts(normalized_text: str) -> Dict[str, Any]:
    candidates = extract_candidate_facts(normalized_text)
    facts, conflicts = normalize_candidates(candidates)
    validate_comparability(facts, conflicts)
    dimensions: Dict[str, Any] = {}
    for dimension in DIMENSIONS:
        items = []
        for fact in facts:
            if fact["type"] != dimension or not fact["comparable"]:
                continue
            items.append({
                "stable_key": fact["stable_key"],
                "value": fact.get("value"),
                "source": {"document": fact["source_document"], "anchor": fact["source_anchor"], "quote": fact["source_quote"]},
                "coverage_status": "complete",
            })
        if items:
            dimensions[dimension] = {"schema_version": "1.0.0", "items": items, "coverage_status": "complete", "comparison_policy": "identity" if dimension in {"apis", "tables", "jobs"} else "value", "source": "normalized_requirement.md"}
    core = [item for item in facts if item["criticality"] == "core"]
    review_reasons = []
    if conflicts:
        review_reasons.append("存在跨文档互斥需求值")
    if any(item["human_review_required"] for item in facts):
        review_reasons.append("存在低置信度、核心中置信度或不可比较事实")
    if core and sum(not item["comparable"] for item in core) / len(core) > 0.2:
        review_reasons.append("超过 20% 的核心事实不可比较")
    quality = {
        "status": "blocked" if not facts or (core and all(not item["comparable"] for item in core)) else ("review_required" if review_reasons else "passed"),
        "fact_count": len(facts),
        "core_fact_count": len(core),
        "confidence_counts": {level: sum(item["confidence_level"] == level for item in facts) for level in ("high", "medium", "low")},
        "non_comparable_count": sum(not item["comparable"] for item in facts),
        "conflict_count": len(conflicts),
        "human_review_required": bool(review_reasons),
        "human_review_reasons": review_reasons,
    }
    return {"schema_version": "1.0.0", "producer": "requirement-code-auditor", "items": facts, "conflicts": conflicts, "quality_summary": quality, **dimensions}
