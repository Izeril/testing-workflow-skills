#!/usr/bin/env python3
"""Evaluate reproducible quality thresholds against real project golden artifacts."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Callable


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
WORKSPACE_ROOT = SKILL_DIR.parents[2]
DEFAULT_MANIFEST = SKILL_DIR / "tests" / "fixtures" / "real_project_quality_baseline.json"
CONTRACT_VALIDATOR_PATH = SKILL_DIR.parent / "contracts" / "validate_contracts.py"

BENIGN_CLASSIFICATIONS = {"一致", "证据不足"}
HIGH_RISK_CLASSIFICATIONS = {"缺失", "不一致", "额外实现"}


class BaselineConfigurationError(ValueError):
    """Raised when a quality-baseline manifest is malformed."""


def configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (AttributeError, OSError):
                pass


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise BaselineConfigurationError(f"JSON 顶层必须为对象: {path}")
    return payload


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rate(passed: int, total: int) -> float:
    return round(passed / total, 6) if total else 1.0


def _json_pointer(payload: Any, pointer: str) -> Any:
    if pointer in ("", "/"):
        return payload
    if not pointer.startswith("/"):
        raise BaselineConfigurationError(f"JSON Pointer 必须以 / 开头: {pointer}")
    current = payload
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as exc:
                raise KeyError(pointer) from exc
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            raise KeyError(pointer)
    return current


def _load_contract_validator():
    spec = importlib.util.spec_from_file_location(
        "real_project_contract_validator", CONTRACT_VALIDATOR_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载中央契约验证器: {CONTRACT_VALIDATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_comparator() -> Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]:
    if str(SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPT_DIR))
    from workflow_analysis_legacy import compare_structured

    return compare_structured


def _validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != "1.0.0":
        raise BaselineConfigurationError("真实项目质量基线 schema_version 必须为 1.0.0")
    projects = manifest.get("projects")
    if not isinstance(projects, list) or not projects:
        raise BaselineConfigurationError("projects 必须为非空数组")
    project_ids = [project.get("id") for project in projects if isinstance(project, dict)]
    if len(project_ids) != len(projects) or len(set(project_ids)) != len(project_ids):
        raise BaselineConfigurationError("每个项目必须有唯一 id")
    if not isinstance(manifest.get("thresholds"), dict):
        raise BaselineConfigurationError("thresholds 必须为对象")


def _collect_evidence(payload: dict[str, Any], kind: str) -> tuple[int, int]:
    passed = total = 0
    items = payload.get("items", [])
    if not isinstance(items, list):
        return passed, total
    if kind == "requirement_facts":
        for item in items:
            if isinstance(item, dict):
                total += 1
                passed += int(bool(str(item.get("source", "")).strip()))
    elif kind == "comparison":
        for item in items:
            if not isinstance(item, dict):
                continue
            for side in ("requirement", "code"):
                value = item.get(side)
                if isinstance(value, dict):
                    total += 1
                    passed += int(bool(str(value.get("evidence", "")).strip()))
    elif kind == "business_impact":
        for item in items:
            if isinstance(item, dict):
                total += 1
                evidence_ids = item.get("evidence_ids")
                passed += int(
                    isinstance(evidence_ids, list)
                    and bool(evidence_ids)
                    and all(isinstance(value, str) and value.strip() for value in evidence_ids)
                )
    else:
        raise BaselineConfigurationError(f"未知 evidence_kind: {kind}")
    return passed, total


def _semantic_labels(payload: dict[str, Any]) -> list[tuple[str, str, str]]:
    labels = []
    for item in payload.get("items", []):
        if isinstance(item, dict):
            labels.append(
                (str(item.get("dimension", "")), str(item.get("key", "")), str(item.get("classification", "")))
            )
    return labels


def evaluate_manifest(
    manifest: dict[str, Any],
    project_root: Path,
    *,
    comparator: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
    contract_validator: Any | None = None,
) -> dict[str, Any]:
    """Evaluate a manifest without mutating any source artifact."""
    _validate_manifest(manifest)
    project_root = project_root.resolve()
    comparator = comparator or _load_comparator()
    contract_validator = contract_validator or _load_contract_validator()
    contract_manifest = contract_validator.load_manifest()

    counters = {
        "projects_present": 0,
        "artifact_integrity_passed": 0,
        "artifact_integrity_total": 0,
        "contract_passed": 0,
        "contract_total": 0,
        "key_fields_passed": 0,
        "key_fields_total": 0,
        "evidence_passed": 0,
        "evidence_total": 0,
        "coverage_passed": 0,
        "coverage_total": 0,
        "semantic_labels_passed": 0,
        "semantic_labels_total": 0,
        "high_risk_false_positives": 0,
        "benign_labels_total": 0,
    }
    project_results: list[dict[str, Any]] = []

    for project in manifest["projects"]:
        source_root = project_root / Path(project["source_root"])
        project_result = {
            "id": project["id"],
            "source_root": project["source_root"],
            "present": source_root.is_dir(),
            "artifacts": [],
            "semantic_replays": [],
        }
        counters["projects_present"] += int(source_root.is_dir())
        payloads: dict[str, dict[str, Any]] = {}

        for artifact in project.get("artifacts", []):
            path = source_root / artifact["filename"]
            artifact_result = {
                "filename": artifact["filename"],
                "present": path.is_file(),
                "integrity": False,
                "contract": None,
                "checks": [],
            }
            counters["artifact_integrity_total"] += 1
            if not path.is_file():
                project_result["artifacts"].append(artifact_result)
                continue

            data = path.read_bytes()
            integrity = (
                len(data) == artifact["size"]
                and hashlib.sha256(data).hexdigest() == artifact["sha256"]
            )
            artifact_result["integrity"] = integrity
            counters["artifact_integrity_passed"] += int(integrity)
            try:
                payload = json.loads(data.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("JSON 顶层不是对象")
                payloads[artifact["filename"]] = payload
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                artifact_result["parse_error"] = str(exc)
                project_result["artifacts"].append(artifact_result)
                continue

            if artifact.get("contract"):
                counters["contract_total"] += 1
                try:
                    contract_validator.validate_payload(
                        contract_manifest, artifact["contract"], payload
                    )
                    artifact_result["contract"] = True
                    counters["contract_passed"] += 1
                except Exception as exc:  # preserve precise validator message in report
                    artifact_result["contract"] = False
                    artifact_result["contract_error"] = str(exc)

            for check in artifact.get("checks", []):
                counters["key_fields_total"] += 1
                try:
                    actual = _json_pointer(payload, check["path"])
                    if "equals" in check:
                        passed = actual == check["equals"]
                        reported_actual = actual
                    elif "count" in check:
                        reported_actual = len(actual) if isinstance(actual, (list, dict)) else None
                        passed = reported_actual == check["count"]
                    elif "minimum" in check:
                        passed = isinstance(actual, (int, float)) and actual >= check["minimum"]
                        reported_actual = actual
                    else:
                        raise BaselineConfigurationError(
                            f"检查缺少 equals/count/minimum: {project['id']} {check['path']}"
                        )
                    artifact_result["checks"].append(
                        {"path": check["path"], "passed": passed, "actual": reported_actual}
                    )
                    counters["key_fields_passed"] += int(passed)
                except (KeyError, TypeError) as exc:
                    artifact_result["checks"].append(
                        {"path": check["path"], "passed": False, "error": str(exc)}
                    )

            if artifact.get("evidence_kind"):
                passed, total = _collect_evidence(payload, artifact["evidence_kind"])
                counters["evidence_passed"] += passed
                counters["evidence_total"] += total
                artifact_result["evidence"] = {"passed": passed, "total": total}

            if artifact.get("coverage_metrics"):
                for pointer in artifact["coverage_metrics"]:
                    counters["coverage_total"] += 1
                    try:
                        value = _json_pointer(payload, pointer)
                        passed = isinstance(value, (int, float)) and value >= 1.0
                    except (KeyError, TypeError):
                        value, passed = None, False
                    counters["coverage_passed"] += int(passed)
                    artifact_result.setdefault("coverage", []).append(
                        {"path": pointer, "value": value, "passed": passed}
                    )

            project_result["artifacts"].append(artifact_result)

        for replay in project.get("semantic_replays", []):
            requirement_path = source_root / replay["requirement_facts"]
            evidence_path = source_root / replay["code_evidence"]
            expected_path = source_root / replay["expected_comparison"]
            replay_result = {"id": replay["id"], "passed": False}
            if not all(path.is_file() for path in (requirement_path, evidence_path, expected_path)):
                replay_result["error"] = "语义重放输入不完整"
                project_result["semantic_replays"].append(replay_result)
                continue
            actual = comparator(
                _load_json(requirement_path),
                _load_json(evidence_path),
            )
            expected = _load_json(expected_path)
            actual_labels = _semantic_labels(actual)
            expected_labels = _semantic_labels(expected)
            label_count = max(len(expected_labels), len(actual_labels))
            counters["semantic_labels_total"] += label_count
            for index in range(label_count):
                expected_label = expected_labels[index] if index < len(expected_labels) else None
                actual_label = actual_labels[index] if index < len(actual_labels) else None
                matched = expected_label is not None and actual_label == expected_label
                counters["semantic_labels_passed"] += int(matched)
                if expected_label and expected_label[2] in BENIGN_CLASSIFICATIONS:
                    counters["benign_labels_total"] += 1
                    if actual_label and actual_label[2] in HIGH_RISK_CLASSIFICATIONS:
                        counters["high_risk_false_positives"] += 1
            replay_result.update(
                {
                    "passed": actual_labels == expected_labels,
                    "expected_label_count": len(expected_labels),
                    "actual_label_count": len(actual_labels),
                }
            )
            project_result["semantic_replays"].append(replay_result)

        project_results.append(project_result)

    metrics = {
        "project_count": len(manifest["projects"]),
        "projects_present": counters["projects_present"],
        "artifact_integrity_rate": _rate(
            counters["artifact_integrity_passed"], counters["artifact_integrity_total"]
        ),
        "contract_pass_rate": _rate(counters["contract_passed"], counters["contract_total"]),
        "key_field_accuracy": _rate(
            counters["key_fields_passed"], counters["key_fields_total"]
        ),
        "evidence_traceability_rate": _rate(
            counters["evidence_passed"], counters["evidence_total"]
        ),
        "priority_coverage_rate": _rate(
            counters["coverage_passed"], counters["coverage_total"]
        ),
        "semantic_label_accuracy": _rate(
            counters["semantic_labels_passed"], counters["semantic_labels_total"]
        ),
        "high_risk_false_positive_rate": _rate(
            counters["high_risk_false_positives"], counters["benign_labels_total"]
        ),
    }

    threshold_results = []
    for name, expected in manifest["thresholds"].items():
        if name.startswith("min_"):
            metric_name = name[4:]
            actual = metrics.get(metric_name)
            passed = actual is not None and actual >= expected
        elif name.startswith("max_"):
            metric_name = name[4:]
            actual = metrics.get(metric_name)
            passed = actual is not None and actual <= expected
        else:
            raise BaselineConfigurationError(f"阈值名称必须以 min_ 或 max_ 开头: {name}")
        threshold_results.append(
            {"threshold": name, "metric": metric_name, "expected": expected, "actual": actual, "passed": passed}
        )

    return {
        "schema_version": "1.0.0",
        "status": "passed" if all(item["passed"] for item in threshold_results) else "failed",
        "manifest": str(DEFAULT_MANIFEST),
        "project_root": str(project_root),
        "metrics": metrics,
        "counts": counters,
        "thresholds": threshold_results,
        "projects": project_results,
    }


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="评估真实项目黄金样本质量阈值")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--project-root", default=str(WORKSPACE_ROOT))
    parser.add_argument("--report", help="可选 JSON 报告输出路径")
    args = parser.parse_args(argv)
    try:
        manifest_path = Path(args.manifest).resolve()
        report = evaluate_manifest(_load_json(manifest_path), Path(args.project_root))
        report["manifest"] = str(manifest_path)
        output = json.dumps(report, ensure_ascii=False, indent=2)
        if args.report:
            report_path = Path(args.report).resolve()
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(output + "\n", encoding="utf-8")
        print(output)
        return 0 if report["status"] == "passed" else 3
    except (OSError, json.JSONDecodeError, BaselineConfigurationError) as exc:
        print(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "status": "failed",
                    "error_code": "quality_baseline_invalid",
                    "message": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
