"""Normalize local and remote evidence while preserving dimension coverage and anchors."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List


IDENTITY_DIMENSIONS = {"apis", "tables", "jobs", "actors"}
DIMENSIONS = ("apis", "fields", "tables", "rules", "sorting", "pagination", "errors", "jobs", "configs", "side_effects", "actors", "scenarios", "state_transitions", "calculations", "integrations", "data_compatibility")


def _iter_values(value: Any) -> Iterable[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return value.values()
    return [value]


def _dimension_status(code_analysis: Dict[str, Any], dimension: str, fallback: str) -> str:
    statuses = code_analysis.get("dimension_coverage") or (code_analysis.get("technology_coverage") or {}).get("dimension_coverage") or {}
    status = statuses.get(dimension)
    if status:
        return str(status)
    return fallback


def _coverage_status(code_analysis: Dict[str, Any]) -> str:
    coverage = code_analysis.get("technology_coverage") or code_analysis.get("coverage_summary") or {}
    if code_analysis.get("analysis_mode") == "gitnexus_remote":
        return str(coverage.get("status") or "partial")
    if not coverage:
        return "unknown"
    if coverage.get("complete") is True and not coverage.get("gaps"):
        return "complete"
    status_counts = coverage.get("status_counts") or {}
    if any(status_counts.get(key, 0) for key in ("partial", "recognized_unanalyzed", "unsupported", "unreadable")) or coverage.get("gaps"):
        return "partial"
    return "complete" if coverage.get("complete") else "unknown"


def _safe_key(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip(" ：:;,，；")
    if not text or len(text) > 120 or "\n" in text or text.startswith(("{", "[", "|", ">", "```")):
        return ""
    return text


def _record(raw: Any, field: str) -> Dict[str, Any] | None:
    if isinstance(raw, dict) and "target_id" in raw and "value" in raw and isinstance(raw.get("value"), dict) and not raw.get("stable_key"):
        raw = raw["value"]
    if isinstance(raw, dict):
        key = next((raw.get(name) for name in ("stable_key", "name", "title", "path", "key", "method") if raw.get(name) not in (None, "")), None)
        value = raw.get("value")
        if value is None and "definition" in raw:
            value = raw["definition"]
        source = raw.get("source_file") or raw.get("file") or raw.get("source") or field
        evidence = raw.get("evidence") or (source if isinstance(source, dict) else None)
        status = raw.get("coverage_status")
    else:
        key, value, source, evidence, status = raw, None, field, None, None
    stable = _safe_key(key)
    if not stable:
        return None
    result: Dict[str, Any] = {"stable_key": stable, "value": value, "source": source}
    if isinstance(raw, dict):
        for name in ("source_file", "file", "line", "target_id", "repository", "branch", "symbol", "source_expression", "source_column", "mapping_file", "mapping_line", "mapper_file", "mapper_line", "evidence_level"):
            if name in raw:
                result[name] = raw[name]
    if evidence is not None:
        result["evidence"] = evidence
    if status:
        result["coverage_status"] = status
    return result


def _generic(code_analysis: Dict[str, Any], field: str, preferred: tuple[str, ...], dimension: str, fallback: str) -> List[Dict[str, Any]]:
    result = []
    for raw in _iter_values(code_analysis.get(field)):
        if isinstance(raw, dict) and "target_id" in raw and isinstance(raw.get("value"), dict) and not raw.get("stable_key"):
            raw = {**raw["value"], "target_id": raw["target_id"]}
        elif isinstance(raw, dict) and "target_id" in raw and "value" in raw and not raw.get("stable_key"):
            raw = {"stable_key": raw["value"], "target_id": raw["target_id"]}
        if isinstance(raw, dict):
            key = next((raw.get(name) for name in ("stable_key", *preferred, "name", "title", "path", "key") if raw.get(name) not in (None, "")), None)
        else:
            key = raw
        item = _record({**raw, "stable_key": key} if isinstance(raw, dict) else key, field)
        if item:
            item["coverage_status"] = item.get("coverage_status") or _dimension_status(code_analysis, dimension, fallback)
            result.append(item)
    return result


def _sql_items(code_analysis: Dict[str, Any], fallback: str) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    tables: List[Dict[str, Any]] = []
    side_effects: List[Dict[str, Any]] = []
    for raw in _iter_values(code_analysis.get("sql_analysis")):
        if isinstance(raw, dict) and "target_id" in raw and isinstance(raw.get("value"), dict) and not raw.get("stable_key"):
            raw = {**raw["value"], "target_id": raw["target_id"]}
        if isinstance(raw, dict) and raw.get("stable_key"):
            item = _record(raw, "sql_analysis")
            if item:
                item["coverage_status"] = item.get("coverage_status") or _dimension_status(code_analysis, "tables", fallback)
                tables.append(item)
            continue
        if not isinstance(raw, dict):
            continue
        analysis = raw.get("analysis", raw)
        table_data = analysis.get("tables") if isinstance(analysis, dict) else None
        sources = table_data.get("sources", []) if isinstance(table_data, dict) else (table_data or [])
        targets = table_data.get("targets", []) if isinstance(table_data, dict) else []
        source = raw.get("file") or raw.get("source_file") or "sql_analysis"
        status = _dimension_status(code_analysis, "tables", fallback)
        for name in [*sources, *targets]:
            item = _record({"stable_key": name, "value": name, "source": source, "coverage_status": status, **({"target_id": raw["target_id"]} if raw.get("target_id") else {})}, "sql_analysis")
            if item:
                tables.append(item)
        for name in targets:
            item = _record({"stable_key": f"write:{name}", "value": {"operation": "write", "table": name}, "source": source, "coverage_status": status, **({"target_id": raw["target_id"]} if raw.get("target_id") else {})}, "sql_analysis")
            if item:
                side_effects.append(item)
    if not tables:
        text = json.dumps(code_analysis.get("sql_analysis", []), ensure_ascii=False)
        for name in re.findall(r"(?i)\b(?:from|join|into|update|table)\s+([A-Za-z_][A-Za-z0-9_.$#]*)", text):
            item = _record({"stable_key": name, "value": name, "source": "sql_analysis", "coverage_status": fallback}, "sql_analysis")
            if item:
                tables.append(item)
    return tables, side_effects



RULE_CATEGORIES = ("if_else_rules", "switch_rules", "exception_rules", "throw_rules", "validation_rules", "logging_patterns", "key_decision_points")
CONFIG_CATEGORIES = ("value_annotations", "properties", "constants", "cron_expressions")


def _component_records(value: Any, categories: tuple[str, ...], target_id: str = ""):
    """Flatten local nested analyzer records and tagged multi-target records."""
    if isinstance(value, list):
        for child in value:
            yield from _component_records(child, categories, target_id)
    elif isinstance(value, dict):
        if "target_id" in value and isinstance(value.get("value"), (dict, list)) and not value.get("stable_key"):
            yield from _component_records(value["value"], categories, str(value["target_id"]))
        elif any(category in value for category in categories):
            for category in categories:
                for child in value.get(category) or []:
                    if isinstance(child, dict):
                        yield {**child, **({"target_id": target_id} if target_id else {})}, category
        else:
            yield {**value, **({"target_id": target_id} if target_id else {})}, ""


def _local_components(code_analysis: Dict[str, Any], field: str, categories: tuple[str, ...], dimension: str, fallback: str):
    result = []
    for raw, category in _component_records(code_analysis.get(field), categories):
        if not category:
            item = _record(raw, field)
        elif dimension == "configs":
            key = raw.get("config_key") or raw.get("constant_name") or raw.get("job_name")
            value = raw.get("config_value", raw.get("default_value", raw.get("constant_value", raw.get("cron"))))
            item = _record({**raw, "stable_key": key, "value": value}, field)
        else:
            source = raw.get("source_file") or raw.get("file")
            # Local syntax has no business stable key. Keep the exact source
            # location and structured behavior, avoiding keyword equivalence.
            key = raw.get("stable_key") or raw.get("name") or raw.get("title")
            if not key and source:
                key = f"{source}:{raw.get('line', '?')}:{category}"
            if not key:
                key = raw.get("condition") or raw.get("statement")
            value = {name: raw[name] for name in ("condition", "action", "else_action", "statement", "exception_type", "handling", "validation", "field", "expression", "value") if name in raw}
            item = _record({**raw, "stable_key": key, "value": value or None}, field)
        if item:
            item["coverage_status"] = item.get("coverage_status") or _dimension_status(code_analysis, dimension, fallback)
            result.append(item)
    return result


def _output_fields(code_analysis: Dict[str, Any]):
    result = []
    layers = list(code_analysis.get("output_layers") or [])
    if code_analysis.get("output_layer"):
        layers.append(code_analysis["output_layer"])
    for layer in layers:
        if not isinstance(layer, dict):
            continue
        for raw in layer.get("verified_fields") or []:
            if not isinstance(raw, dict) or not raw.get("field_key"):
                continue
            value = {name: raw[name] for name in ("source_expression", "source_column") if name in raw}
            item = _record({**raw, "stable_key": raw["field_key"], "value": value or None, "coverage_status": "partial", **({"target_id": layer["target_id"]} if layer.get("target_id") else {})}, "output_layer")
            if item:
                result.append(item)
    return result


def build_code_evidence(code_analysis: Dict[str, Any]) -> Dict[str, Any]:
    code_analysis = code_analysis or {}
    fallback = _coverage_status(code_analysis)
    fields = {
        "apis": ("api_endpoints", ("path", "endpoint", "name")),
        "fields": ("fields", ("field", "name", "key")),
        "rules": ("business_rules", ("stable_key", "title", "name", "condition")),
        "sorting": ("sorting", ("stable_key", "field", "name")),
        "pagination": ("pagination", ("stable_key", "name", "page_size")),
        "errors": ("error_handling", ("error_code", "code", "message", "name")),
        "jobs": ("scheduled_tasks", ("name", "method", "job_name")),
        "configs": ("config_values", ("key", "name", "property")),
        "side_effects": ("side_effects", ("stable_key", "name", "operation")),
        "scenarios": ("scenarios", ("stable_key", "name", "title")),
        "state_transitions": ("state_transitions", ("stable_key", "name", "state")),
        "calculations": ("calculations", ("stable_key", "name", "formula")),
        "integrations": ("integrations", ("stable_key", "name", "system")),
        "actors": ("actors", ("stable_key", "name", "role")),
        "data_compatibility": ("data_compatibility", ("stable_key", "name", "version")),
    }
    dimensions: Dict[str, List[Dict[str, Any]]] = {}
    for dimension, (field, preferred) in fields.items():
        dimensions[dimension] = _generic(code_analysis, field, preferred, dimension, fallback)
    dimensions["rules"] = _local_components(code_analysis, "business_rules", RULE_CATEGORIES, "rules", fallback)
    dimensions["configs"] = _local_components(code_analysis, "config_values", CONFIG_CATEGORIES, "configs", fallback)
    output_fields = _output_fields(code_analysis)
    generic_fields = bool(dimensions["fields"])
    dimensions["fields"].extend(output_fields)
    tables, sql_side_effects = _sql_items(code_analysis, fallback)
    dimensions["tables"] = tables
    dimensions["side_effects"].extend(sql_side_effects)
    # Errors remain a comparable dimension only when a structured error field
    # exists; free-form analysis warnings are limitations, not behaviours.
    if not dimensions["errors"] and code_analysis.get("errors"):
        dimensions["errors"] = _generic(code_analysis, "errors", ("error_code", "code", "message"), "errors", fallback)

    result: Dict[str, Any] = {
        "schema_version": "1.0.0",
        "producer": "requirement-code-auditor",
        "coverage_summary": {"status": fallback},
    }
    for name, items in dimensions.items():
        status = "partial" if name == "fields" and output_fields else _dimension_status(code_analysis, name, fallback)
        if not items and not ((code_analysis.get("dimension_coverage") or {}).get(name)):
            continue
        deduped = {(item.get("target_id", ""), item["stable_key"].casefold()): item for item in items}
        result[name] = {
            "schema_version": "1.1.0",
            "items": list(deduped.values()),
            "coverage_status": status,
            "comparison_policy": "identity" if name in IDENTITY_DIMENSIONS or (name == "fields" and output_fields and not generic_fields) else "value",
            "source": name,
        }
    return result
