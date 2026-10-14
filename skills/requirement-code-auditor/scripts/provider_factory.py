"""Create the configured evidence provider while keeping local compatibility."""

from __future__ import annotations

from typing import Any, Dict

from .evidence_provider import LocalRepositoryEvidenceProvider
from .gitnexus_evidence_provider import GitNexusEvidenceProvider
from .skill_errors import AuditorError


def create_provider(config: Dict[str, Any]):
    source = str((config.get("code_source") or {}).get("provider") or "gitnexus").casefold()
    if source == "gitnexus":
        return GitNexusEvidenceProvider(config.get("gitnexus") or {})
    if source == "local_git":
        return LocalRepositoryEvidenceProvider(
            codegraph_config=config.get("codegraph") or {},
            batch_files=(config.get("analysis") or {}).get("batch_files", 50),
            max_batches=(config.get("analysis") or {}).get("max_batches", 0),
        )
    raise AuditorError(
        "provider_invalid",
        f"不支持的代码来源 Provider：{source}",
        2,
        {"provider": source, "supported": ["gitnexus", "local_git"]},
        False,
        "将 AUDITOR_CODE_PROVIDER 设置为 gitnexus 或 local_git",
    )
