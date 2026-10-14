"""Consume one authoritative requirement-code-auditor run without regenerating facts."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Tuple


class AuditorArtifactError(RuntimeError):
    """Raised when the authoritative audit bundle is missing or inconsistent."""


def _load(path: Path) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AuditorArtifactError(f"缺少需求实现审计器正式产物：{path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise AuditorArtifactError(f"需求实现审计器产物不可读：{path}：{exc}") from exc
    if not isinstance(payload, dict):
        raise AuditorArtifactError(f"需求实现审计器产物根节点必须是对象：{path}")
    return payload


def legacy_workflow_enabled(repo_config: Dict[str, Any] | None = None) -> bool:
    value = str(os.getenv("TESTCASE_LEGACY_WORKFLOW", "")).strip().casefold()
    return bool((repo_config or {}).get("legacy_workflow")) or value in {"1", "true", "yes", "on"}


def write_legacy_audit_log(output_dir: Path, reason: str) -> Path:
    legacy_dir = output_dir / "legacy" / ".ai-internal"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    path = legacy_dir / "legacy_workflow_audit.json"
    path.write_text(json.dumps({
        "schema_version": "1.0.0",
        "producer": "testcase-generator",
        "legacy_workflow": True,
        "enabled_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason or "explicit configuration",
        "output_isolated": True,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def consume_authoritative_audit(req_dir: Path, output_dir: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Validate producer/run/schema/SHA and expose existing facts to downstream design."""
    req_dir = Path(req_dir)
    output_dir = Path(output_dir)
    internal = output_dir / ".ai-internal"
    audit = _load(internal / "requirement_code_audit.json")
    if audit.get("schema_version") != "1.0.0" or audit.get("producer") != "requirement-code-auditor":
        raise AuditorArtifactError("正式审计产物 producer 或 schema_version 不符合 requirement-code-auditor 1.0.0")
    if audit.get("mode") != "audit" or audit.get("status") in {"blocked", "failed"}:
        raise AuditorArtifactError(f"正式审计未完成可消费的 audit 运行：status={audit.get('status')} mode={audit.get('mode')}")
    run_id = str(audit.get("run_id") or "")
    if not run_id:
        raise AuditorArtifactError("正式审计产物缺少 run_id")

    artifacts = {
        "requirement_facts": _load(internal / "requirement_facts.json"),
        "code_analysis": _load(internal / "code_analysis.json"),
        "code_evidence": _load(internal / "code_evidence.json"),
        "comparison": _load(internal / "requirement_code_comparison.json"),
        "code_targets": _load(internal / "code_targets.json"),
    }
    for name in ("requirement_facts", "code_analysis", "code_evidence", "comparison", "code_targets"):
        artifact_run_id = str(artifacts[name].get("run_id") or "")
        if artifact_run_id != run_id:
            raise AuditorArtifactError(f"{name} 的 run_id 与正式审计不一致，禁止混用两套证据")
    if artifacts["comparison"].get("schema_version") != "1.1.0":
        raise AuditorArtifactError("requirement_code_comparison.schema_version 必须为 1.1.0")

    audit_targets = audit.get("targets") or []
    declared_targets = artifacts["code_targets"].get("targets") or []
    declared_by_id = {str(item.get("target_id")): item for item in declared_targets}
    for target in audit_targets:
        target_id = str(target.get("target_id") or "")
        target_sha = str(target.get("target_sha") or "")
        if not target_id or not target_sha:
            raise AuditorArtifactError("正式审计代码目标缺少 target_id 或 target_sha")
        if target_id not in declared_by_id:
            raise AuditorArtifactError(f"代码目标 {target_id} 未出现在 code_targets.json")

    project_info = {
        "project_name": req_dir.name,
        "project_name_source": "requirement_package_directory",
        "branch": "、".join(dict.fromkeys(str(item.get("branch")) for item in audit_targets if item.get("branch"))),
        "services": [service for item in audit_targets for service in (item.get("services") or [])],
        "auditor_producer": "requirement-code-auditor",
        "auditor_run_id": run_id,
        "auditor_status": audit.get("status"),
    }
    context = {
        "schema_version": "1.0.0",
        "producer": "testcase-generator",
        "authoritative_producer": "requirement-code-auditor",
        "authoritative_run_id": run_id,
        "project_info": project_info,
        "code_analysis": artifacts["code_analysis"],
        "requirement_facts_path": str(internal / "requirement_facts.json"),
        "requirement_code_comparison_path": str(internal / "requirement_code_comparison.json"),
        "requirement_code_audit_path": str(internal / "requirement_code_audit.json"),
    }
    (internal / "analysis_context.json").write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    compatibility_report = output_dir / "需求分析.md"
    compatibility_report.write_text(
        "# 需求分析\n\n"
        f"本阶段只消费需求实现审计器正式产物，未重新生成需求事实或比较结论。\n\n"
        f"- 权威生产者：`requirement-code-auditor`\n"
        f"- 权威运行：`{run_id}`\n"
        f"- 审计状态：`{audit.get('status')}`\n"
        f"- 详细报告：`需求实现审计报告.md`\n",
        encoding="utf-8",
    )
    return project_info, artifacts["code_analysis"]
