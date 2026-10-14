"""Deterministic large-change batching and coverage-preserving merge."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any, Dict, Iterable, List


# Every observable analyzer dimension must survive a batch boundary. Nested
# business_rules/config_values/output_layer dictionaries keep their shape.
LIST_FIELDS = (
    "api_endpoints", "entry_points", "sql_analysis", "processing_rules",
    "scheduled_tasks", "analysis_warnings", "test_suggestions", "test_cases",
    "call_chains", "syntax_analysis", "python_analysis", "data_flow",
    "data_flows", "exception_handling", "config_changes", "business_keywords",
    "fields", "sorting", "pagination", "error_handling", "side_effects",
    "actors", "scenarios", "state_transitions", "calculations", "integrations",
    "data_compatibility",
)


def weakest_coverage(statuses: Iterable[str], default: str = "unknown") -> str:
    """A later batch/target cannot erase an earlier coverage limitation."""
    values = [str(status) for status in statuses if status]
    if not values:
        return default
    if len(set(values)) == 1:
        return values[0]
    if "unsupported" in values:
        return "unsupported"
    if "unknown" in values:
        return "unknown"
    if any(value in {"partial", "recognized_unanalyzed", "unreadable"} for value in values):
        return "partial"
    return "complete" if "complete" in values else "verified"


def _unique(values):
    result = []
    seen = set()
    for item in values:
        marker = repr(item)
        if marker not in seen:
            result.append(deepcopy(item))
            seen.add(marker)
    return result


def _merge_value(left, right, key=""):
    if left == right:
        return deepcopy(left)
    if isinstance(left, dict) and isinstance(right, dict):
        result = deepcopy(left)
        for child, value in right.items():
            result[child] = _merge_value(result[child], value, child) if child in result else deepcopy(value)
        return result
    if isinstance(left, list) and isinstance(right, list):
        return _unique([*left, *right])
    if key in {"coverage_status", "status"} and isinstance(left, str) and isinstance(right, str):
        return weakest_coverage([left, right])
    if isinstance(left, str) and isinstance(right, str):
        return "\n".join(dict.fromkeys(value for value in (left, right) if value))
    # Mixed representations are retained as records instead of overwriting.
    return _unique((left if isinstance(left, list) else [left]) + (right if isinstance(right, list) else [right]))


def batch_paths(paths: Iterable[str], batch_size: int, max_batches: int = 0) -> tuple[List[List[str]], List[str]]:
    values = list(dict.fromkeys(str(path) for path in paths if str(path)))
    size = max(1, int(batch_size or 50))
    batches = [values[index:index + size] for index in range(0, len(values), size)]
    if max_batches and len(batches) > max_batches:
        processed = batches[:max_batches]
        return processed, [path for batch in batches[max_batches:] for path in batch]
    return batches, []


def merge_analysis_batches(batches: List[Dict[str, Any]], all_files: List[str], unanalysed: List[str]) -> Dict[str, Any]:
    if not batches:
        return {
            "files_changed": list(all_files),
            "file_changes": {},
            "technology_coverage": {"files": [], "gaps": [{"path": path, "status": "recognized_unanalyzed", "reason": "分析预算未覆盖"} for path in unanalysed], "complete": False, "total_changed_files": len(all_files), "fully_analyzed_files": 0},
            "analysis_warnings": ["没有完成任何代码分析批次"],
        }
    merged = {}
    coverage_files = []
    coverage_gaps = []
    dimension_statuses = {}
    for batch in batches:
        for field, value in batch.items():
            if field in {"technology_coverage", "coverage_summary", "files_changed", "analysis_batches", "dimension_coverage"}:
                continue
            merged[field] = _merge_value(merged[field], value, field) if field in merged else deepcopy(value)
        coverage = batch.get("technology_coverage") or {}
        coverage_files.extend(item for item in coverage.get("files", []) if isinstance(item, dict))
        coverage_gaps.extend(coverage.get("gaps", []))
        statuses = batch.get("dimension_coverage") or coverage.get("dimension_coverage") or {}
        for name, status in statuses.items():
            dimension_statuses.setdefault(name, []).append(status)
    for field in LIST_FIELDS:
        merged.setdefault(field, [])
    merged.setdefault("file_changes", {})
    for path in unanalysed:
        record = {"path": path, "status": "recognized_unanalyzed", "reason": "分析批次预算耗尽"}
        coverage_files.append(record)
        coverage_gaps.append(record)
    coverage_files = _unique(coverage_files)
    coverage_gaps = _unique(coverage_gaps)
    status_counts = Counter(str(item.get("status") or "unknown") for item in coverage_files)
    merged["files_changed"] = list(all_files)
    complete = not unanalysed and all((batch.get("technology_coverage") or {}).get("complete") is True for batch in batches)
    merged["technology_coverage"] = {
        "files": coverage_files,
        "gaps": coverage_gaps,
        "total_changed_files": len(all_files),
        "fully_analyzed_files": sum(status in {"analyzed", "complete"} for status in (item.get("status") for item in coverage_files)),
        "status_counts": dict(status_counts),
        "complete": complete,
        "status": "complete" if complete else "partial",
        "unanalysed_files": list(unanalysed),
    }
    if dimension_statuses:
        merged["dimension_coverage"] = {name: weakest_coverage(statuses) for name, statuses in dimension_statuses.items()}
        merged["technology_coverage"]["dimension_coverage"] = merged["dimension_coverage"]
    merged["coverage_summary"] = merged["technology_coverage"]
    merged["analysis_batches"] = {"batch_count": len(batches), "batch_files": [list((batch.get("file_changes") or {}).keys()) for batch in batches], "unanalysed_files": list(unanalysed)}
    if unanalysed:
        merged["analysis_warnings"].append(f"分析预算耗尽，{len(unanalysed)} 个文件未分析，覆盖状态为 partial")
    return merged
