"""Validate public artifact contracts and remote audit safety invariants."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable


REQUIRED = {
    "requirement_facts": ("schema_version", "producer", "items", "quality_summary"),
    "code_analysis": ("files_changed", "technology_coverage"),
    "code_evidence": ("schema_version", "producer", "coverage_summary"),
    "requirement_code_comparison": ("schema_version", "summary", "items"),
    "requirement_code_audit": ("schema_version", "producer", "run_id", "workflow_version", "status", "mode", "inputs", "targets", "quality_summary", "warnings", "artifacts"),
}


def _walk_json(value: Any, path: str = "$") -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                yield f"{path}: JSON 对象键必须是字符串"
            yield from _walk_json(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_json(child, f"{path}[{index}]")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        yield f"{path}: 不可序列化类型 {type(value).__name__}"


def validate_artifact(name: str, payload: Dict[str, Any]) -> list[str]:
    issues = []
    if not isinstance(payload, dict):
        return [f"{name}: 根节点必须是对象"]
    for key in REQUIRED.get(name, ()):
        if key not in payload:
            issues.append(f"{name}: 缺少必填字段 {key}")
    if name == "requirement_code_comparison" and payload.get("schema_version") != "1.1.0":
        issues.append("requirement_code_comparison: schema_version 必须为 1.1.0")
    if name == "code_analysis" and payload.get("analysis_mode") == "gitnexus_remote":
        coverage = payload.get("technology_coverage") or {}
        if coverage.get("complete") is True:
            issues.append("code_analysis: GitNexus 远程模式不得宣称全局覆盖 complete")
        if "dimension_coverage" not in payload and "dimension_coverage" not in coverage:
            issues.append("code_analysis: GitNexus 远程模式缺少逐维度覆盖状态")
        if "analysis_capabilities" not in payload:
            issues.append("code_analysis: GitNexus 远程模式缺少能力/索引准入信息")
    if name == "requirement_code_audit" and payload.get("provider") == "gitnexus" and payload.get("status") == "passed":
        issues.append("requirement_code_audit: GitNexus 远程模式不得输出无条件 passed")
    issues.extend(_walk_json(payload))
    try:
        json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        issues.append(f"{name}: JSON 序列化失败：{exc}")
    return issues


def validate_artifacts(artifacts: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    issues = []
    for name, payload in artifacts.items():
        issues.extend(validate_artifact(name, payload))
    return {"schema_version": "1.0.0", "status": "passed" if not issues else "failed", "artifact_count": len(artifacts), "issues": issues}


def validate_json_file(path: str | Path, name: str | None = None) -> Dict[str, Any]:
    file_path = Path(path)
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    artifact_name = name or file_path.stem
    return {"path": str(file_path), "issues": validate_artifact(artifact_name, payload)}
