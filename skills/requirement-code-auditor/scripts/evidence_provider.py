"""Replaceable code-evidence provider boundary and local repository implementation."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional

from .analysis_batching import batch_paths, merge_analysis_batches
from .analyzer import analyze_code
from .git_runner import run_git
from .skill_errors import AuditorError


class CodeEvidenceProvider(ABC):
    @abstractmethod
    def analyze(self, target: Dict[str, Any], requirement_anchors=None) -> Dict[str, Any]:
        """Return code_analysis, snapshot, coverage and capabilities; never compare."""


def _resolve(repo_path: str, ref: str) -> str:
    result = run_git(repo_path, ["rev-parse", "--verify", f"{ref}^{{commit}}"])
    if result.returncode != 0:
        raise AuditorError(
            "git_ref_missing",
            f"无法解析 Git 引用：{ref}",
            5,
            {"repo_path": repo_path, "ref": ref, "stderr": result.stderr[:400]},
            True,
            "确认本地仓库已经包含目标分支与基线引用",
        )
    return result.stdout.strip()


def detect_codegraph(repo_path: str, target_sha: str, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    config = config or {}
    if not config.get("enabled"):
        return {"provider": "static_text", "codegraph_status": "not_configured", "degraded": False, "limitations": []}
    configured = config.get("index_path")
    index = Path(configured) if configured else Path(repo_path) / ".codegraph"
    if not index.exists():
        return {"provider": "static_text", "codegraph_status": "index_missing", "degraded": True, "limitations": ["Codegraph 索引不存在，已降级到静态源码分析"]}
    metadata_path = index / "metadata.json" if index.is_dir() else index.with_suffix(".json")
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"provider": "static_text", "codegraph_status": "query_failed", "degraded": True, "limitations": ["Codegraph 元数据不可读，已降级"]}
        indexed_sha = str(metadata.get("target_sha") or metadata.get("commit_sha") or "")
        if indexed_sha and indexed_sha != target_sha:
            return {"provider": "static_text", "codegraph_status": "index_stale", "degraded": True, "limitations": ["Codegraph 索引 SHA 与目标 SHA 不匹配"]}
    candidates_path = index / "candidates.json" if index.is_dir() else index.with_name(index.stem + ".candidates.json")
    if not candidates_path.exists():
        return {"provider": "static_text", "codegraph_status": "not_required", "degraded": False, "limitations": ["索引有效，但当前目标没有可用候选文件清单"]}
    try:
        candidate_payload = json.loads(candidates_path.read_text(encoding="utf-8"))
        raw_candidates = candidate_payload.get("candidate_files", candidate_payload.get("files", [])) if isinstance(candidate_payload, dict) else candidate_payload
        candidate_files = [str(item.get("path") if isinstance(item, dict) else item) for item in raw_candidates if (item.get("path") if isinstance(item, dict) else item)]
    except (OSError, json.JSONDecodeError, TypeError):
        return {"provider": "static_text", "codegraph_status": "query_failed", "degraded": True, "limitations": ["Codegraph 候选结果不可读，已降级到静态分析"]}
    return {"provider": "static_with_codegraph", "codegraph_status": "available", "degraded": False, "limitations": [], "candidate_files": candidate_files}


class LocalRepositoryEvidenceProvider(CodeEvidenceProvider):
    def __init__(self, *, codegraph_config: Optional[Dict[str, Any]] = None, batch_files: int = 50, max_batches: int = 0):
        self.codegraph_config = codegraph_config or {}
        self.batch_files = max(1, int(batch_files or 50))
        self.max_batches = max(0, int(max_batches or 0))

    def analyze(self, target: Dict[str, Any], requirement_anchors=None) -> Dict[str, Any]:
        repo_path = str(target.get("repo_path") or "")
        repo = Path(repo_path)
        if not repo.is_dir() or not (repo / ".git").exists():
            raise AuditorError(
                "repository_invalid",
                "目标仓库不存在或不是 Git 仓库",
                5,
                {"repo_path": repo_path, "target_id": target.get("target_id")},
                True,
                "修正仓库注册表或显式 repo_path",
            )
        branch = str(target.get("branch") or "")
        baseline = str(target.get("baseline") or "origin/master")
        branch_sha = _resolve(repo_path, branch)
        baseline_sha = _resolve(repo_path, baseline)
        capabilities = {"call_chain": detect_codegraph(repo_path, branch_sha, self.codegraph_config)}
        selected_files = target.get("selected_files")
        diff_result = run_git(repo_path, ["diff", "--name-only", f"{baseline_sha}...{branch_sha}"])
        if diff_result.returncode != 0:
            raise AuditorError(
                "git_diff_failed",
                "无法读取目标分支相对基线的文件清单",
                5,
                {"target_id": target.get("target_id"), "stderr": diff_result.stderr[:400]},
                True,
                "确认目标分支和基线均可在本地解析",
            )
        all_changed_files = [line.strip() for line in diff_result.stdout.splitlines() if line.strip()]
        requested_files = list(all_changed_files if selected_files is None else selected_files)
        graph_candidates = capabilities["call_chain"].get("candidate_files", [])
        if graph_candidates:
            prioritized = [path for path in graph_candidates if path in requested_files]
            requested_files = prioritized + [path for path in requested_files if path not in prioritized]
        batches, unanalysed = batch_paths(requested_files, self.batch_files, self.max_batches)
        if not batches:
            analysis = analyze_code(repo_path, branch_sha, baseline_sha, selected_files=selected_files)
        else:
            batch_results = [
                analyze_code(repo_path, branch_sha, baseline_sha, selected_files=batch)
                for batch in batches
            ]
            analysis = merge_analysis_batches(batch_results, requested_files, unanalysed)
        analysis.setdefault("files_changed", list((analysis.get("file_changes") or {}).keys()))
        analysis.setdefault("branch", branch)
        analysis.setdefault("baseline", baseline)
        analysis["target_sha"] = branch_sha
        analysis["baseline_sha"] = baseline_sha
        analysis["target_id"] = target.get("target_id")
        coverage = analysis.get("technology_coverage") or analysis.get("coverage_summary") or {}
        analysis["analysis_capabilities"] = capabilities
        return {
            "code_analysis": analysis,
            "snapshot": {
                "repository": repo.name,
                "repo_path": repo_path,
                "branch": branch,
                "baseline": baseline,
                "target_sha": branch_sha,
                "baseline_sha": baseline_sha,
                "files_changed": analysis.get("files_changed", []),
            },
            "coverage": coverage,
            "analysis_capabilities": capabilities,
        }
