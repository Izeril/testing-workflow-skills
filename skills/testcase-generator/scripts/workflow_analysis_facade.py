#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evidence-driven analysis facade with strict Git/UAT/Codegraph governance.

``workflow_analysis_legacy`` retains the mature document and source-analysis
implementation.  This facade adds the new governance stages without copying
or forking those parsers, keeping ``main.py`` a thin orchestrator.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

import workflow_analysis_legacy as _legacy

# Preserve every historical module attribute, including private compatibility
# helpers imported directly by workflow_review/workflow_testpoint.
globals().update(
    {
        name: value
        for name, value in vars(_legacy).items()
        if not name.startswith("__")
    }
)

from business_impact_gate import assess_business_impact_gate, business_impact_execution
from business_impact_report import write_business_impact_report
from business_impact_translator import translate_business_impacts
from code_review_report import write_code_review_report as write_tester_code_review_report
from code_analysis_admission import assess_code_analysis_admission
from codegraph_impact_provider import prepare_codegraph_artifacts
from git_snapshot_manager import capture_git_snapshot
from requirement_commit_scope import resolve_requirement_commit_scope
from uat_merge_gate import assess_uat_merge


_legacy_build_code_targets = _legacy.build_code_targets
_legacy_build_requirement_facts = _legacy.build_requirement_facts
_legacy_build_code_evidence = _legacy.build_code_evidence
_legacy_preflight_code_target = _legacy.preflight_code_target
_legacy_run_requirement_analysis = _legacy.run_requirement_analysis


def build_code_targets(
    test_docs,
    req_dir,
    repo_config=None,
    manifest_path=None,
    force_legacy=False,
    legacy_repo_path=None,
    legacy_branch=None,
    legacy_baseline=None,
):
    """Extend every legacy target with UAT, scope and Codegraph governance."""
    repo_config = dict(repo_config or {})
    targets, errors = _legacy_build_code_targets(
        test_docs,
        req_dir,
        repo_config,
        manifest_path,
        force_legacy,
        legacy_repo_path,
        legacy_branch,
        legacy_baseline,
    )
    registry = {
        str(item.get("target_id") or item.get("key") or ""): item
        for item in repo_config.get("targets", [])
        if isinstance(item, dict)
    }
    for target in targets:
        configured = registry.get(str(target.get("registry_target_id") or ""), {})
        target.setdefault("codegraph_policy", configured.get("codegraph_policy") or repo_config.get("codegraph_policy", "risk_based"))
        target.setdefault("critical_impact_paths", configured.get("critical_impact_paths") or [])
        target.setdefault(
            "uat_branch",
            configured.get("uat_branch") or repo_config.get("uat_branch") or "origin/uat_merge",
        )
        target.setdefault(
            "analysis_mode",
            configured.get("analysis_mode") or repo_config.get("analysis_mode") or "uat",
        )
        target.setdefault(
            "require_codegraph_for_uat",
            bool(configured.get(
                "require_codegraph_for_uat",
                repo_config.get("require_codegraph_for_uat", True),
            )),
        )
        # Manifest fields are already retained by the legacy loader.  These
        # defaults make document-derived and CLI targets follow the same model.
        for key in (
            "requirement_id",
            "commit_range",
            "pull_request",
            "shared_branch",
            "changed_files",
        ):
            if key not in target and key in configured:
                target[key] = configured[key]
    return targets, errors


def preflight_code_target(target: Dict[str, Any]) -> Dict[str, Any]:
    """Apply admission, strict snapshot, requirement scope and UAT merge gates."""
    result = dict(target)
    admission = assess_code_analysis_admission(result)
    result["admission"] = admission
    if not admission.get("admitted"):
        status = admission.get("status", "blocked_unsupported_target")
        return {
            **result,
            "status": "skipped_missing_branch" if status == "skipped_missing_branch" else "blocked",
            "admission_status": status,
            "block_reason": admission.get("reason", ""),
        }

    repo_path = str(result.get("repo_path") or "")
    languages = _legacy.detect_repository_languages(repo_path)
    if result.get("language") and result["language"] not in languages:
        return {
            **result,
            "status": "blocked",
            "admission_status": "blocked_unsupported_target",
            "repository_languages": languages,
            "block_reason": (
                f'语言不匹配：提测材料为 {result["language"]}，'
                f'仓库识别为 {"/".join(languages) or "未知"}'
            ),
        }

    snapshot = capture_git_snapshot(result)
    result["git_snapshot"] = snapshot
    if snapshot.get("snapshot_status") == "blocked":
        return {
            **result,
            "status": "blocked",
            "admission_status": "blocked_remote_refresh",
            "repository_languages": languages,
            "block_reason": (
                "远程引用刷新或解析失败："
                + "、".join(snapshot.get("missing_refs", []))
            ),
        }

    requested_mode = str(result.get("analysis_mode") or "uat")
    if requested_mode == "uat" and not snapshot.get("formal_ready"):
        # Development evidence remains useful, but it must never be presented
        # as the verified UAT environment result.
        result["formal_admission_status"] = "blocked_remote_refresh"
        result["formal_block_reason"] = (
            "正式 UAT 分析必须在同一次运行中成功刷新 dev/master/uat"
        )
    else:
        result["formal_admission_status"] = "admitted"

    scope = resolve_requirement_commit_scope(repo_path, result, snapshot)
    result["change_scope"] = scope
    if scope.get("status") != "admitted":
        return {
            **result,
            "status": "blocked",
            "admission_status": scope.get("status"),
            "repository_languages": languages,
            "block_reason": scope.get("reason", "本需求提交范围无法唯一确定"),
        }
    changed_files = scope.get("raw_changed_files") or []
    if not changed_files:
        return {
            **result,
            "status": "blocked",
            "repository_languages": languages,
            "block_reason": "目标提交范围内无代码变更，禁止生成代码影响结论",
        }

    uat_gate = assess_uat_merge(repo_path, scope, snapshot)
    effective_mode = (
        "uat"
        if requested_mode == "uat"
        and snapshot.get("formal_ready")
        and uat_gate.get("formal_ready")
        else "development"
    )
    analysis_ref = (
        snapshot.get("uat_ref")
        if effective_mode == "uat"
        else snapshot.get("development_ref")
    )
    analysis_sha = (
        snapshot.get("uat_sha")
        if effective_mode == "uat"
        else snapshot.get("development_sha")
    )
    verified = snapshot.get("snapshot_status") == "verified"
    return {
        **result,
        "status": "verified" if verified else "verified_stale",
        "branch": analysis_ref,
        "development_branch": snapshot.get("development_ref", ""),
        "baseline": snapshot.get("baseline_ref", ""),
        "uat_branch": snapshot.get("uat_ref", ""),
        "development_sha": snapshot.get("development_sha", ""),
        "branch_sha": analysis_sha,
        "baseline_sha": snapshot.get("baseline_sha", ""),
        "uat_sha": snapshot.get("uat_sha", ""),
        "merge_base_sha": scope.get("merge_base_sha", ""),
        "comparison_command": scope.get("comparison_command", ""),
        "raw_changed_files": changed_files,
        "uat_merge_gate": uat_gate,
        "effective_analysis_mode": effective_mode,
        "repository_languages": languages,
        "freshness": {
            "freshness_status": snapshot.get("snapshot_status"),
            "fetch_status": snapshot.get("fetch_status"),
            "analysis_branch_ref": analysis_ref,
            "analysis_baseline_ref": snapshot.get("baseline_ref"),
            "analysis_branch_sha": analysis_sha,
            "analysis_baseline_sha": snapshot.get("baseline_sha"),
            "remote_refs_before": snapshot.get("refs_before"),
            "remote_refs_after": snapshot.get("refs_after"),
            "partial_remote_update_detected": snapshot.get("partial_update"),
        },
        "baseline_fallback_risk": False,
        "preflight_changed_files": [item["path"] for item in changed_files],
    }


def _add_fact(
    payload: Dict[str, Any],
    dimension: str,
    title: str,
    content: str,
    source: str,
) -> None:
    raw = str(content or "").strip()
    text = re.sub(r"\s+", " ", raw).strip(" ：:;,，；")
    if not text or not _legacy._is_structured_fact_candidate(raw):
        return
    stable_key = _legacy._structured_fact_key(dimension, title, text)
    items = payload.setdefault("items", [])
    if any(
        item.get("dimension") == dimension and str(item.get("content")) == text
        for item in items
    ):
        return
    fact_id = _legacy._stable_id("RQ", f"{dimension}:{title}:{text}", len(items))
    items.append(
        {
            "fact_id": fact_id,
            "dimension": dimension,
            "type": "明确需求",
            "title": title,
            "content": text,
            "source": source,
            "confidence": "high",
            "stable_key": stable_key,
            "comparable": True,
        }
    )
    payload.setdefault(dimension, {})[stable_key] = {"content": text, "source": source}


def build_requirement_facts(
    project_info,
    doc_content,
    code_analysis=None,
    extracted_data=None,
):
    """Extend the authoritative fact model with stable business dimensions."""
    payload = _legacy_build_requirement_facts(
        project_info, doc_content, code_analysis, extracted_data
    )
    source = ".ai-internal/normalized_requirement.md"
    patterns = {
        "actors": (
            "用户/角色",
            r"(?:用户|角色|操作人|适用对象)\s*[：:]\s*([^\n]+)",
        ),
        "scenarios": (
            "业务场景",
            r"(?:业务场景|使用场景|适用场景)\s*[：:]\s*([^\n]+)",
        ),
        "state_transitions": (
            "状态流转",
            r"([A-Za-z0-9_\u4e00-\u9fff]{2,20})\s*(?:->|→|流转至|变更为)\s*([A-Za-z0-9_\u4e00-\u9fff]{2,20})",
        ),
        "calculations": (
            "计算公式",
            r"(?:计算公式|计算规则|公式)\s*[：:]\s*([^\n]+)",
        ),
        "integrations": (
            "上下游契约",
            r"(?:外部系统|下游系统|上游系统|消息主题)\s*[：:]\s*([^\n]+)",
        ),
        "data_compatibility": (
            "历史数据兼容",
            r"([^\n]*(?:历史数据|存量数据|向后兼容)[^\n]*)",
        ),
    }
    for dimension, (title, pattern) in patterns.items():
        for match in re.finditer(pattern, doc_content or "", re.IGNORECASE):
            value = " → ".join(match.groups()) if len(match.groups()) > 1 else match.group(1)
            _add_fact(payload, dimension, title, value, source)
    for reviewed_fact in project_info.get("reviewed_requirement_facts", []):
        if not isinstance(reviewed_fact, dict):
            continue
        _add_fact(
            payload,
            "rules",
            str(reviewed_fact.get("title", "")),
            str(reviewed_fact.get("content", "")),
            str(reviewed_fact.get("source") or source),
        )
    return payload


def build_code_evidence(code_analysis):
    """Add stable behavior dimensions while preserving the core comparator input."""
    evidence = _legacy_build_code_evidence(code_analysis)
    code_analysis = code_analysis or {}

    def values(field: str) -> List[Dict[str, str]]:
        result = []
        for item in _legacy._as_list(code_analysis.get(field)):
            text = _legacy._public_evidence_text(item)
            if text and _legacy._is_safe_structured_key(text):
                result.append({"name": text, "source": field})
        return result

    rules = values("business_rules")
    additions = {
        "scenarios": values("entry_points") + values("api_endpoints") + values("scheduled_tasks"),
        "state_transitions": [
            item for item in rules
            if re.search(r"(?i)(status|state|状态|->|→)", item["name"])
        ],
        "calculations": [
            item for item in rules
            if re.search(r"(?i)(sum|count|amount|capital|金额|合计|计算|公式)", item["name"])
        ],
        "integrations": values("api_endpoints"),
        "side_effects": values("sql_analysis"),
    }
    for name, items in additions.items():
        if items:
            evidence[name] = _legacy._comparison_dimension(name, items, code_analysis)
    return {key: value for key, value in evidence.items() if value}


def _write_governance_artifacts(
    output_dir: Path,
    targets: List[Dict[str, Any]],
    manifest_errors=None,
) -> Dict[str, Path]:
    """Persist each strict stage independently for audit and cache diagnosis."""
    internal = _legacy._get_internal_dir(Path(output_dir))
    artifacts = {
        "code_analysis_admission.json": {
            "schema_version": "1.0.0",
            "targets": [target.get("admission", {}) for target in targets],
        },
        "git_snapshot.json": {
            "schema_version": "1.0.0",
            "targets": [target.get("git_snapshot", {}) for target in targets],
        },
        "code_change_scope.json": {
            "schema_version": "1.0.0",
            "targets": [target.get("change_scope", {}) for target in targets],
        },
        "uat_merge_gate.json": {
            "schema_version": "1.0.0",
            "targets": [target.get("uat_merge_gate", {}) for target in targets],
        },
    }
    paths = {}
    for name, payload in artifacts.items():
        if not targets:
            payload.update(
                status="blocked" if manifest_errors else "skipped",
                skip_reason="" if manifest_errors else "没有代码目标，该代码条件环节未执行",
                block_reason="；".join(map(str, manifest_errors or [])),
            )
        path = internal / name
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        paths[name] = path
    return paths


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def run_requirement_analysis(
    req_dir,
    output_dir,
    repo_path=None,
    branch=None,
    baseline="origin/master",
    kb_manager=None,
    repo_config=None,
    code_targets_path=None,
    force_legacy=False,
):
    """Run the established analysis, review, and test-design evidence chain."""
    repo_config = dict(repo_config or {})
    output_dir = Path(output_dir)
    project_info, code_analysis = _legacy_run_requirement_analysis(
        req_dir,
        output_dir,
        repo_path,
        branch,
        baseline,
        kb_manager,
        repo_config,
        code_targets_path,
        force_legacy,
        analysis_dependencies={
            "build_code_targets": build_code_targets,
            "preflight_code_target": preflight_code_target,
            "build_requirement_facts": build_requirement_facts,
            "build_code_evidence": build_code_evidence,
        },
    )

    output_dir = Path(output_dir)
    internal = _legacy._get_internal_dir(output_dir)
    targets_payload = _load_json(internal / "code_targets.json")
    targets = targets_payload.get("targets") or []

    # Codegraph is a bounded provider: write a plan first, and accept only a
    # separately supplied MCP result whose SHA matches the plan.
    if targets:
        _, codegraph_impact = prepare_codegraph_artifacts(
            output_dir, targets, repo_config.get("codegraph_impact_path"),
        )
    else:
        codegraph_impact = {
            "schema_version": "1.0.0", "status": "skipped",
            "skip_reason": "没有代码目标，未执行代码图查询",
            "targets": [], "summary": {"ready": 0, "blocked": 0, "pending": 0},
        }
        _legacy._write_internal_json(output_dir, "codegraph_impact.json", codegraph_impact)
        _legacy._write_internal_json(output_dir, "codegraph_query_plan.json", {
            "schema_version": "1.0.0", "status": "skipped",
            "skip_reason": codegraph_impact["skip_reason"], "targets": [],
        })
    _legacy.write_code_target_results(
        output_dir,
        targets,
        targets_payload.get("manifest_errors") or [],
    )
    _write_governance_artifacts(output_dir, targets, targets_payload.get("manifest_errors"))

    requirement_facts = _load_json(internal / "requirement_facts.json")
    business_impact = translate_business_impacts(
        requirement_facts,
        targets,
        codegraph_impact,
        project_info or {},
    )
    manifest_errors = targets_payload.get("manifest_errors") or []
    if manifest_errors:
        business_impact.update(
            status="blocked", analysis_mode="code_analysis_blocked",
            block_reason="代码目标清单无效：" + "；".join(map(str, manifest_errors)),
            execution=business_impact_execution(targets, manifest_errors),
            code_target_manifest_errors=manifest_errors,
        )
        business_impact.pop("skip_reason", None)
    gate = assess_business_impact_gate(
        business_impact,
        targets,
        bool(repo_config.get("require_codegraph_for_uat", True)),
    )
    _legacy._write_internal_json(output_dir, "business_impact.json", business_impact)
    _legacy._write_internal_json(output_dir, "business_impact_gate.json", gate)
    write_business_impact_report(output_dir, business_impact, gate)
    if code_analysis is not None:
        write_tester_code_review_report(
            output_dir,
            project_info or {},
            code_analysis,
            _load_json(internal / "code_impact.json"),
            _load_json(internal / "requirement_code_comparison.json"),
            business_impact,
            targets,
        )

    # Enrich the existing compatibility impact artifact instead of introducing
    # a competing source of code-analysis truth.
    code_impact_path = internal / "code_impact.json"
    code_impact = _load_json(code_impact_path)
    code_impact.update(
        {
            "analysis_mode": business_impact.get("analysis_mode"),
            "uat_merge_gate_summary": {
                "fully_merged": sum(
                    1 for target in targets
                    if (target.get("uat_merge_gate") or {}).get("status") == "fully_merged"
                ),
                "blocked": sum(
                    1 for target in targets
                    if str(target.get("analysis_mode") or "uat") == "uat"
                    and (target.get("uat_merge_gate") or {}).get("status") != "fully_merged"
                ),
            },
            "codegraph_summary": codegraph_impact.get("summary", {}),
            "business_impact_status": business_impact.get("status"),
            "business_impact_gate_status": gate.get("summary", {}).get("status"),
        }
    )
    code_impact_path.write_text(
        json.dumps(code_impact, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # Restore the documented development branch in user context; the effective
    # UAT ref remains available per target as immutable technical evidence.
    development_branches = [
        target.get("development_branch")
        for target in targets
        if target.get("development_branch")
    ]
    if development_branches:
        project_info["branch"] = "、".join(dict.fromkeys(development_branches))
    context_path = internal / "analysis_context.json"
    context = _load_json(context_path)
    context.update(
        {
            "project_info": project_info,
            "business_impact_path": str(internal / "business_impact.json"),
            "business_impact_gate_path": str(internal / "business_impact_gate.json"),
            "codegraph_query_plan_path": str(internal / "codegraph_query_plan.json"),
            "codegraph_impact_path": str(internal / "codegraph_impact.json"),
        }
    )
    context_path.write_text(
        json.dumps(context, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return project_info, code_analysis


__all__ = [
    name
    for name, value in globals().items()
    if callable(value) and getattr(value, "__module__", None) == __name__
]
