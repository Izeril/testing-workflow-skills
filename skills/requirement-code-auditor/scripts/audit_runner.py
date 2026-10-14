"""A0-A8 requirement implementation audit state machine."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .analysis_batching import LIST_FIELDS, weakest_coverage
from .artifact_writer import ArtifactSession, render_audit_report, render_code_report
from .comparator import compare_structured
from .config_loader import load_config
from .contract_validator import validate_artifacts
from .evidence_normalizer import build_code_evidence
from .evidence_provider import CodeEvidenceProvider
from .provider_factory import create_provider
from .requirement_fact_builder import build_requirement_facts
from .requirement_ingestion import ingest_requirement_directory
from .skill_errors import AuditorError, blocked
from .target_resolver import resolve_code_targets


PRODUCER = "requirement-code-auditor"
WORKFLOW_VERSION = "1.0.0"


def _aggregate(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    analyses = [item["code_analysis"] for item in results]
    if len(analyses) == 1:
        return analyses[0]
    merged: Dict[str, Any] = {
        "schema_version": analyses[0].get("schema_version", "1.0.0") if analyses else "1.0.0",
        "analysis_mode": "gitnexus_remote" if any(item.get("analysis_mode") == "gitnexus_remote" for item in analyses) else "local_git",
        "files_changed": [],
        "file_changes": {},
        "api_endpoints": [],
        "entry_points": [],
        "sql_analysis": [],
        "business_rules": [],
        "processing_rules": [],
        "scheduled_tasks": [],
        "config_values": [],
        "analysis_warnings": [],
        "code_targets": [],
        "graph_evidence": {"processes": [], "symbols": [], "contexts": [], "impacts": [], "traces": []},
        "dimension_coverage": {},
        "sql_evidence_required": False,
    }
    evidence_fields = tuple(dict.fromkeys((*LIST_FIELDS, "business_rules", "config_values")))
    for field in evidence_fields:
        merged.setdefault(field, [])
    merged["output_layers"] = []
    coverage_files = []
    coverage_gaps = []
    dimension_statuses = {}
    for result, analysis in zip(results, analyses):
        target_id = analysis.get("target_id") or result.get("snapshot", {}).get("repository") or "target"
        merged["files_changed"].extend(f"{target_id}:{path}" for path in analysis.get("files_changed", []))
        merged["file_changes"].update({f"{target_id}:{key}": value for key, value in (analysis.get("file_changes") or {}).items()})
        for field in evidence_fields:
            value = analysis.get(field) or []
            values = value if isinstance(value, list) else [value]
            merged[field].extend({"target_id": target_id, "value": item} for item in values)
        for key, value in (analysis.get("graph_evidence") or {}).items():
            if isinstance(value, list):
                merged["graph_evidence"].setdefault(key, []).extend(value)
        for name, status in (analysis.get("dimension_coverage") or {}).items():
            dimension_statuses.setdefault(name, []).append(status)
        layers = list(analysis.get("output_layers") or [])
        if analysis.get("output_layer"):
            layers.append(analysis["output_layer"])
        merged["output_layers"].extend({"target_id": target_id, **layer} for layer in layers if isinstance(layer, dict))
        merged["sql_evidence_required"] = merged["sql_evidence_required"] or bool(analysis.get("sql_evidence_required"))
        coverage = analysis.get("technology_coverage") or {}
        coverage_files.extend({"target_id": target_id, **item} for item in coverage.get("files", []) if isinstance(item, dict))
        coverage_gaps.extend({"target_id": target_id, **item} if isinstance(item, dict) else {"target_id": target_id, "reason": str(item)} for item in coverage.get("gaps", []))
        merged["code_targets"].append(result.get("snapshot", {}))
    merged["dimension_coverage"] = {name: weakest_coverage(statuses) for name, statuses in dimension_statuses.items()}
    merged["files_changed"] = list(dict.fromkeys(merged["files_changed"]))
    merged["technology_coverage"] = {
        "files": coverage_files,
        "gaps": coverage_gaps,
        "total_changed_files": sum((analysis.get("technology_coverage") or {}).get("total_changed_files", len(analysis.get("files_changed", []))) for analysis in analyses),
        "complete": bool(analyses) and all((analysis.get("technology_coverage") or {}).get("complete") is True for analysis in analyses),
        "status": "partial" if any((analysis.get("technology_coverage") or {}).get("status") == "partial" for analysis in analyses) else "unknown",
        "dimension_coverage": merged["dimension_coverage"],
    }
    merged["coverage_summary"] = merged["technology_coverage"]
    merged["analysis_capabilities"] = {str(index): item.get("analysis_capabilities", {}) for index, item in enumerate(results, 1)}
    return merged


def _artifact_refs(session: ArtifactSession) -> Dict[str, str]:
    names = (
        "source_inventory.json", "normalized_requirement.md", "requirement_facts.json",
        "code_targets.json", "code_analysis.json", "code_evidence.json",
        "requirement_code_comparison.json", "requirement_code_audit.json",
        "quality_gate_report.json", "last_error.json",
    )
    return {name: str(Path(".ai-internal") / name) for name in names}


def _audit_status(facts: Dict[str, Any], comparison: Dict[str, Any], code_analysis: Dict[str, Any]) -> str:
    quality = facts.get("quality_summary", {})
    if quality.get("status") == "blocked":
        return "blocked"
    summary = comparison.get("summary", {})
    if code_analysis.get("analysis_mode") == "gitnexus_remote":
        # Remote graph mode is intentionally never an unconditional pass.
        return "warning"
    if any(summary.get(label, 0) for label in ("缺失", "不一致", "额外实现", "证据不足")) or quality.get("human_review_required"):
        return "warning"
    return "passed"


def _provider_name(evidence_provider: Optional[CodeEvidenceProvider], config: Dict[str, Any], repo_path: Optional[str] = None) -> str:
    if evidence_provider is not None:
        return str(getattr(evidence_provider, "name", "local_git"))
    # Supplying --repo is an explicit local compatibility request.  Remote
    # mode itself uses repo_key and never needs a filesystem path.
    if repo_path:
        return "local_git"
    return str((config.get("code_source") or {}).get("provider") or "gitnexus")


def run_requirement_code_audit(
    requirement_dir,
    repo_path=None,
    branch=None,
    baseline=None,
    code_targets=None,
    config_path=None,
    output_dir=None,
    mode="audit",
    *,
    evidence_provider: Optional[CodeEvidenceProvider] = None,
) -> Dict[str, Any]:
    """Run A0-A8; ordinary callers provide only a requirement directory."""
    if mode not in {"audit", "analyze"}:
        raise AuditorError("mode_invalid", "v1 仅支持 audit 或 analyze 模式", 2, {"mode": mode}, False, "删除 compare-only/replay 模式参数")
    root = Path(requirement_dir).expanduser().resolve()
    if not root.is_dir():
        raise AuditorError("requirement_dir_invalid", "需求目录不存在或不是目录", 2, {"requirement_dir": str(root)}, True, "传入具体需求包目录")
    config = load_config(config_path, repo_path=repo_path, baseline=baseline, output_dir=output_dir)
    effective_output = Path(config.get("output", {}).get("path") or root / config["output"]["dir_name"])
    run_id = uuid.uuid4().hex
    session: ArtifactSession | None = None
    try:
        with ArtifactSession(effective_output, run_id, config["output"].get("internal_dir", ".ai-internal")) as session:
            inventory, normalized_text, release_docs = ingest_requirement_directory(root)
            if not inventory or not normalized_text:
                raise blocked("requirement_material_missing", "未找到可读取的 Markdown 需求材料", requirement_dir=str(root))
            if any(item.get("read_error") and item.get("participates_requirement", True) for item in inventory):
                raise blocked("requirement_read_failed", "需求材料读取失败，可能影响审计结论", failures=[item for item in inventory if item.get("read_error")])
            source_payload = {"schema_version": "1.0.0", "producer": PRODUCER, "run_id": run_id, "requirement_dir": str(root), "documents": inventory}
            session.write_json("source_inventory.json", source_payload)
            session.write_text("normalized_requirement.md", normalized_text)

            requirement_facts = build_requirement_facts(normalized_text)
            requirement_facts["run_id"] = run_id
            session.write_json("requirement_facts.json", requirement_facts)
            quality = requirement_facts.get("quality_summary", {})
            if quality.get("status") == "blocked":
                raise blocked("requirement_fact_quality_blocked", "需求事实质量门禁未通过", quality_summary=quality)

            provider_name = _provider_name(evidence_provider, config, repo_path)
            targets, target_errors = resolve_code_targets(
                release_docs,
                config.get("repo", {}),
                repo_path=repo_path,
                branch=branch,
                baseline=baseline,
                code_targets=code_targets,
                provider_name=provider_name,
            )
            if target_errors:
                raise blocked("code_target_manifest_invalid", "代码目标清单不可用", errors=target_errors)
            if not targets:
                raise blocked("code_target_missing", "未获得可审计代码目标", release_test_documents=len(release_docs), provider=provider_name)
            blocked_targets = [item for item in targets if item.get("status") == "blocked"]
            if blocked_targets:
                raise blocked("code_target_ambiguous", "开发分支或仓库无法唯一确定", targets=blocked_targets)
            session.write_json("code_targets.json", {"schema_version": "1.0.0", "producer": PRODUCER, "run_id": run_id, "provider": provider_name, "targets": targets})

            if provider_name == "local_git":
                config.setdefault("code_source", {})["provider"] = "local_git"
            provider = evidence_provider or create_provider(config)
            provider_results = []
            comparable_facts = [item for item in requirement_facts.get("items", []) if item.get("comparable")]
            for target in targets:
                provider_results.append(provider.analyze(target, requirement_anchors=comparable_facts))
            resolved_code_targets = []
            for target, result in zip(targets, provider_results):
                resolved_target = dict(target)
                snapshot = result.get("snapshot", {})
                resolved_target.update({
                    "repo_key": snapshot.get("repo_key", resolved_target.get("repo_key", "")),
                    "index_commit": snapshot.get("index_commit", ""),
                    "index_time": snapshot.get("index_time", ""),
                    "branch_scope": snapshot.get("branch_scope", ""),
                    "audit_eligible": snapshot.get("audit_eligible", False),
                    "status": "completed",
                })
                resolved_code_targets.append(resolved_target)
            session.write_json("code_targets.json", {"schema_version": "1.0.0", "producer": PRODUCER, "run_id": run_id, "provider": provider_name, "targets": resolved_code_targets})
            code_analysis = _aggregate(provider_results)
            code_analysis.setdefault("schema_version", "1.0.0")
            code_analysis["producer"] = PRODUCER
            code_analysis["run_id"] = run_id
            session.write_json("code_analysis.json", code_analysis)
            code_evidence = build_code_evidence(code_analysis)
            code_evidence["run_id"] = run_id
            code_evidence["analysis_mode"] = code_analysis.get("analysis_mode", provider_name)
            code_evidence["limitations"] = code_analysis.get("analysis_warnings", [])
            session.write_json("code_evidence.json", code_evidence)

            comparison: Dict[str, Any] = {"schema_version": "1.1.0", "summary": {"一致": 0, "缺失": 0, "不一致": 0, "额外实现": 0, "证据不足": 0, "总计": 0}, "dimensions": [], "items": [], "warnings": []}
            if mode == "audit":
                comparison = compare_structured(requirement_facts, code_evidence)
                comparison["producer"] = PRODUCER
                comparison["run_id"] = run_id
                session.write_json("requirement_code_comparison.json", comparison)

            resolved_targets = []
            for target, result in zip(targets, provider_results):
                public_target = {key: value for key, value in target.items() if key not in {"content"}}
                snapshot = result.get("snapshot", {})
                public_target.update({
                    "repo_key": snapshot.get("repo_key", public_target.get("repo_key", "")),
                    "target_sha": snapshot.get("target_sha", ""),
                    "baseline_sha": snapshot.get("baseline_sha", ""),
                    "index_commit": snapshot.get("index_commit", ""),
                    "index_time": snapshot.get("index_time", ""),
                    "branch_scope": snapshot.get("branch_scope", ""),
                    "audit_eligible": snapshot.get("audit_eligible", False),
                    "status": "completed",
                })
                resolved_targets.append(public_target)
            warnings = [f"兼容环境变量已弃用：{name}" for name in config.get("compatibility_warnings", [])]
            for result in provider_results:
                capabilities = result.get("analysis_capabilities", {})
                warnings.extend(capabilities.get("limitations", []))
                warnings.extend(capabilities.get("call_chain", {}).get("limitations", []))
            if code_analysis.get("analysis_mode") == "gitnexus_remote":
                warnings.insert(0, "本次审计基于 GitNexus 已索引符号、调用关系和可返回源码执行；未获得完整 Git 分支文件差异，未命中实现默认只能判为证据不足。")
            audit = {
                "schema_version": "1.0.0",
                "producer": PRODUCER,
                "run_id": run_id,
                "workflow_version": WORKFLOW_VERSION,
                "status": _audit_status(requirement_facts, comparison, code_analysis) if mode == "audit" else ("warning" if code_analysis.get("analysis_mode") == "gitnexus_remote" else "passed"),
                "mode": mode,
                "provider": provider_name,
                "inputs": {"requirement_dir": str(root), "requirement_sha256": hashlib.sha256(normalized_text.encode("utf-8")).hexdigest(), "branch_source": "explicit_override" if branch else "release_test_document"},
                "targets": resolved_targets,
                "requirement_facts": {"path": ".ai-internal/requirement_facts.json", "fact_count": quality.get("fact_count", 0)},
                "code_analysis": {"path": ".ai-internal/code_analysis.json", "files_changed": len(code_analysis.get("files_changed", []))},
                "code_evidence": {"path": ".ai-internal/code_evidence.json", "coverage_status": code_evidence.get("coverage_summary", {}).get("status", "unknown")},
                "analysis_capabilities": [item.get("analysis_capabilities", {}) for item in provider_results],
                "requirement_code_comparison": {"path": ".ai-internal/requirement_code_comparison.json" if mode == "audit" else "", "summary": comparison.get("summary", {})},
                "quality_summary": quality,
                "warnings": list(dict.fromkeys(str(item) for item in warnings if item)),
                "artifacts": _artifact_refs(session),
            }
            session.write_json("requirement_code_audit.json", audit)
            validation_inputs = {"requirement_facts": requirement_facts, "code_analysis": code_analysis, "code_evidence": code_evidence, "requirement_code_audit": audit}
            if mode == "audit":
                validation_inputs["requirement_code_comparison"] = comparison
            gate = validate_artifacts(validation_inputs)
            gate.update({"producer": PRODUCER, "run_id": run_id, "requirement_quality": quality})
            session.write_json("quality_gate_report.json", gate)
            if gate["status"] != "passed":
                raise AuditorError("artifact_contract_failed", "统一产物契约校验失败", 6, {"issues": gate["issues"]}, False, "修复产物 schema 后重试")
            public_files = {"代码分析报告.md": render_code_report(code_analysis)}
            if mode == "audit":
                public_files["需求实现审计报告.md"] = render_audit_report(audit, comparison)
            session.commit(public_files)
            audit["output_dir"] = str(effective_output)
            return audit
    except AuditorError as exc:
        if session is not None:
            import json
            session.internal_dir.mkdir(parents=True, exist_ok=True)
            error_path = session.internal_dir / "last_error.json"
            error_path.write_text(json.dumps(exc.to_payload(run_id), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise
