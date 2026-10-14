#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""领域无关的需求与代码结构化证据比较器。"""

import argparse
import json
import re
from collections import Counter
from pathlib import Path


CLASSIFICATIONS = ("一致", "缺失", "不一致", "额外实现", "证据不足")
DIMENSIONS = {
    "fields": ("fields", "field_mappings", "response_fields"),
    "apis": ("apis", "api_endpoints", "interfaces"),
    "tables": ("tables", "views", "data_sources"),
    "rules": ("rules", "business_rules", "predicates", "filters"),
    "sorting": ("sorting", "sort", "order_by"),
    "pagination": ("pagination", "paging"),
    "errors": ("errors", "error_handling", "error_codes"),
    "jobs": ("jobs", "scheduled_tasks"),
    "configs": ("configs", "configuration", "switches"),
    "side_effects": ("side_effects", "effects"),
}

# 标识型维度只确认同一命名对象是否存在；其余维度默认比较行为值。
# facade 可以扩展 DIMENSIONS，未知维度默认 value，避免核心层猜测语义。
IDENTITY_DIMENSIONS = {"apis", "tables", "jobs", "actors"}
DIMENSION_POLICIES = {
    name: "identity" if name in IDENTITY_DIMENSIONS else "value"
    for name in DIMENSIONS
}
COMPLETE_COVERAGE = {"complete", "full", "analyzed", "verified"}
DIMENSION_METADATA_KEYS = {
    "items", "coverage_status", "comparison_policy", "schema_version", "source",
}


def _is_structured_key(value, limit=120):
    """Reject raw documents, Markdown rows and serialized payloads as keys."""
    raw = str(value or "")
    text = re.sub(r"\s+", " ", raw).strip()
    if not text or len(text) > limit or "\n" in raw or "\r" in raw:
        return False
    if text.startswith(("{", "[{")) or text.endswith(("}]",)):
        return False
    if re.search(r'''(?s)[{\[]\s*["'][^"']+["']\s*:''', text):
        return False
    if text.count("|") >= 2 or text.startswith((">", "```")):
        return False
    if re.fullmatch(
        r"(?i)(business_rules_summary|if_else_rules|exception_rules|validation_rules|value_annotations|source_file|analysis_warnings)",
        text,
    ):
        return False
    if re.search(r"(?i)(告警编号|问题直达|stack\s*trace|traceback|exception\s*information)", text):
        return False
    return True


def _norm(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return re.sub(r"\s+", " ", str(value).strip().strip("`\"'")).casefold()


def _item(
    key,
    value=None,
    evidence=None,
    coverage_status=None,
    comparison_policy=None,
):
    if isinstance(value, dict):
        evidence = value.get("evidence") or value.get("source") or evidence
        coverage_status = value.get("coverage_status") or coverage_status
        comparison_policy = value.get("comparison_policy") or comparison_policy
        value = next(
            (value[k] for k in ("value", "mapping", "target", "definition", "content") if k in value),
            {
                k: v for k, v in value.items()
                if k not in {
                    "evidence", "source", "stable_key", "coverage_status",
                    "comparison_policy", "schema_version",
                }
            } or None,
        )
    return {
        "key": str(key).strip(),
        "norm_key": _norm(key),
        "value": value,
        "norm_value": _norm(value),
        "evidence": evidence,
        "coverage_status": coverage_status,
        "comparison_policy": comparison_policy,
    }


def _unwrap_dimension(raw):
    if (
        isinstance(raw, dict)
        and "items" in raw
        and any(key in raw for key in DIMENSION_METADATA_KEYS - {"items"})
    ):
        return raw.get("items"), {
            "coverage_status": raw.get("coverage_status"),
            "comparison_policy": raw.get("comparison_policy"),
            "schema_version": raw.get("schema_version"),
            "source": raw.get("source"),
        }
    return raw, {}


def _items(raw, warnings=None, dimension="", side=""):
    if raw is None:
        return {}
    if isinstance(raw, dict):
        values = [_item(k, v) for k, v in raw.items()]
    elif isinstance(raw, (list, tuple, set)):
        values = []
        for value in raw:
            if not isinstance(value, dict):
                values.append(_item(value))
                continue
            key_name = next(
                (
                    k for k in ("stable_key", "id", "name", "key", "field", "path", "title")
                    if value.get(k) not in (None, "")
                ),
                None,
            )
            if key_name is None:
                values.append(_item(json.dumps(value, ensure_ascii=False, sort_keys=True)))
                continue
            detail = next(
                (value[k] for k in ("value", "mapping", "target", "definition", "content") if k in value),
                {
                    k: v for k, v in value.items()
                    if k not in {
                        key_name, "evidence", "source", "coverage_status",
                        "comparison_policy", "schema_version",
                    }
                } or None,
            )
            values.append(_item(
                value[key_name],
                detail,
                value.get("evidence") or value.get("source"),
                value.get("coverage_status"),
                value.get("comparison_policy"),
            ))
    else:
        values = [_item(raw)]

    result = {}
    for value in values:
        if value["norm_key"] and _is_structured_key(value["key"]):
            result[value["norm_key"]] = value
            continue
        if warnings is not None and str(value.get("key") or "").strip():
            warnings.append({
                "code": "filtered_unstructured_key",
                "dimension": dimension,
                "side": side,
                "key_preview": re.sub(r"\s+", " ", str(value["key"]))[:80],
                "message": "已过滤不适合作为结构化比较键的输入",
            })
    return result


def _dimension(data, name):
    for alias in DIMENSIONS[name]:
        if alias in data:
            return True, data[alias], alias
    return False, None, None


def _policy(name, req_meta, code_meta):
    policy = (
        req_meta.get("comparison_policy")
        or code_meta.get("comparison_policy")
        or DIMENSION_POLICIES.get(name, "identity" if name in IDENTITY_DIMENSIONS else "value")
    )
    if policy not in {"identity", "value"}:
        raise ValueError(f"维度 {name} 的 comparison_policy 非法: {policy}")
    return policy


def _coverage(meta, present):
    return str(meta.get("coverage_status") or ("complete" if present else "not_provided"))


def _finding(
    dimension,
    key,
    classification,
    reason,
    requirement=None,
    code=None,
    comparison_policy="value",
):
    def public(value):
        if value is None:
            return None
        return {
            "key": value["key"],
            "value": value["value"],
            "evidence": value["evidence"],
            "coverage_status": value.get("coverage_status"),
        }

    return {
        "dimension": dimension,
        "key": key,
        "classification": classification,
        "comparison_policy": comparison_policy,
        "reason": reason,
        "requirement": public(requirement),
        "code": public(code),
    }


def compare_structured(requirement_facts, code_evidence, dimensions=None):
    """只比较输入中的结构化键和值；不从关键词推断领域语义。"""
    if not isinstance(requirement_facts, dict) or not isinstance(code_evidence, dict):
        raise TypeError("requirement_facts 和 code_evidence 必须是对象")

    selected = list(dimensions or DIMENSIONS)
    unknown = [name for name in selected if name not in DIMENSIONS]
    if unknown:
        raise ValueError(f"未知比较维度: {', '.join(unknown)}")

    findings, inspected, warnings = [], [], []
    for name in selected:
        req_present, req_container, req_alias = _dimension(requirement_facts, name)
        code_present, code_container, code_alias = _dimension(code_evidence, name)
        req_raw, req_meta = _unwrap_dimension(req_container)
        code_raw, code_meta = _unwrap_dimension(code_container)
        policy = _policy(name, req_meta, code_meta)
        req_coverage = _coverage(req_meta, req_present)
        code_coverage = _coverage(code_meta, code_present)
        req = _items(req_raw, warnings, name, "requirement")
        code = _items(code_raw, warnings, name, "code")
        inspected.append({
            "dimension": name,
            "requirement_source": req_alias,
            "code_source": code_alias,
            "requirement_count": len(req),
            "code_count": len(code),
            "comparison_policy": policy,
            "requirement_coverage_status": req_coverage,
            "code_coverage_status": code_coverage,
        })
        if code_present and code_coverage not in COMPLETE_COVERAGE:
            warnings.append({
                "code": "partial_dimension_coverage",
                "dimension": name,
                "side": "code",
                "coverage_status": code_coverage,
                "message": "代码维度覆盖不完整，未命中项只能判为证据不足",
            })
        if not req_present:
            continue

        for norm_key, req_item in req.items():
            code_item = code.get(norm_key)
            if code_item is None:
                complete = code_present and code_coverage in COMPLETE_COVERAGE
                label = "缺失" if complete else "证据不足"
                reason = (
                    "代码证据已完整覆盖该维度，但未找到同键实现"
                    if complete
                    else "代码证据未提供或未完整覆盖该维度，无法判断是否实现"
                )
            elif policy == "identity":
                label, reason = "一致", "同键标识证据一致；属性值不属于该维度的判定条件"
            elif req_item["norm_value"] and not code_item["norm_value"]:
                label, reason = "证据不足", "已找到同键实现，但代码证据缺少可比较的值"
            elif req_item["norm_value"] and req_item["norm_value"] != code_item["norm_value"]:
                label, reason = "不一致", "同键需求值与代码实现值不同"
            else:
                label, reason = "一致", "同键结构化证据一致"
            findings.append(_finding(
                name,
                req_item["key"],
                label,
                reason,
                req_item,
                code_item,
                policy,
            ))

        if code_present:
            requirement_complete = req_coverage in COMPLETE_COVERAGE
            for norm_key, code_item in code.items():
                if norm_key in req:
                    continue
                label = "额外实现" if requirement_complete else "证据不足"
                reason = (
                    "代码证据包含需求结构中未声明的同维度项"
                    if requirement_complete
                    else "需求维度覆盖不完整，无法判断代码项是否属于额外实现"
                )
                findings.append(_finding(
                    name,
                    code_item["key"],
                    label,
                    reason,
                    code=code_item,
                    comparison_policy=policy,
                ))

    counts = Counter(item["classification"] for item in findings)
    summary = {name: counts.get(name, 0) for name in CLASSIFICATIONS}
    summary["总计"] = len(findings)
    return {
        "schema_version": "1.1.0",
        "summary": summary,
        "dimensions": inspected,
        "items": findings,
        "warnings": warnings,
    }


def format_findings(result, include_consistent=False):
    return [
        f"[{item['classification']}] {item['dimension']}.{item['key']}: {item['reason']}"
        for item in result.get("items", [])
        if include_consistent or item["classification"] != "一致"
    ]


def _load(path):
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"结构化证据文件必须是 JSON 对象: {path}")
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description="比较结构化需求事实与代码证据")
    parser.add_argument("requirement_json")
    parser.add_argument("code_json")
    parser.add_argument("output_json", nargs="?")
    args = parser.parse_args(argv)
    result = compare_structured(_load(args.requirement_json), _load(args.code_json))
    if args.output_json:
        output = Path(args.output_json)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(output)
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
