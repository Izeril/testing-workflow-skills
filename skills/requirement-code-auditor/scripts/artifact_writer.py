"""Run-isolated artifact staging, output locking and evidence-rich reports."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict

from .skill_errors import AuditorError


class ArtifactSession:
    def __init__(self, output_dir: str | Path, run_id: str, internal_name: str = ".ai-internal"):
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.internal_dir = self.output_dir / internal_name
        self.run_id = run_id
        self.lock_path = self.output_dir / ".requirement-code-auditor.lock"
        self.stage_dir = self.internal_dir / ".runs" / run_id
        self.artifacts: Dict[str, str] = {}
        self._lock_fd = None

    def __enter__(self):
        self.output_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._lock_fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise AuditorError("output_locked", "同一输出目录已有审计任务正在写入", 3, {"output_dir": str(self.output_dir), "lock": str(self.lock_path)}, True, "等待当前审计结束，或选择不同 output_dir") from exc
        os.write(self._lock_fd, self.run_id.encode("utf-8"))
        self.stage_dir.mkdir(parents=True, exist_ok=False)
        return self

    def write_json(self, name: str, payload: Dict[str, Any]) -> Path:
        path = self.stage_dir / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        self.artifacts[name] = str(Path(".ai-internal") / name)
        return path

    def write_text(self, name: str, content: str) -> Path:
        path = self.stage_dir / name
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        self.artifacts[name] = str(Path(".ai-internal") / name)
        return path

    def commit(self, public_files: Dict[str, str] | None = None) -> None:
        self.internal_dir.mkdir(parents=True, exist_ok=True)
        for staged in self.stage_dir.iterdir():
            if staged.is_file():
                os.replace(staged, self.internal_dir / staged.name)
        for name, content in (public_files or {}).items():
            temp = self.output_dir / f".{name}.{self.run_id}.tmp"
            temp.write_text(content.rstrip() + "\n", encoding="utf-8")
            os.replace(temp, self.output_dir / name)

    def __exit__(self, exc_type, exc, traceback):
        if self._lock_fd is not None:
            os.close(self._lock_fd)
        if self.lock_path.exists():
            self.lock_path.unlink()
        if self.stage_dir.exists():
            shutil.rmtree(self.stage_dir, ignore_errors=True)


def _anchor_text(value: Any) -> str:
    if not isinstance(value, dict):
        return str(value or "")
    parts = []
    for key in ("repo", "branch", "file", "line", "symbol", "symbol_uid", "source"):
        if value.get(key) not in (None, ""):
            parts.append(f"{key}={value[key]}")
    if value.get("content"):
        parts.append(f"源码：{str(value['content']).strip()[:240]}")
    return "; ".join(parts)


def render_audit_report(audit: Dict[str, Any], comparison: Dict[str, Any]) -> str:
    status_labels = {"passed": "通过", "passed_with_limits": "有限通过", "warning": "有风险", "blocked": "阻断", "failed": "失败"}
    remote = audit.get("provider") == "gitnexus"
    lines = [
        "# 需求实现审计报告", "",
        f"- 审计状态：{status_labels.get(audit.get('status'), audit.get('status'))}",
        f"- 代码来源：{'GitNexus 远程知识图谱' if remote else '本地 Git 仓库'}",
        f"- 运行标识：`{audit.get('run_id', '')}`", f"- 模式：`{audit.get('mode', '')}`", "",
        "## 代码目标与分支", "",
    ]
    for target in audit.get("targets", []):
        repo = target.get("repo_key") or target.get("repo_path") or ""
        lines.append(f"- `{target.get('target_id', '')}`：仓库 `{repo}`，分支 `{target.get('branch', '')}` ← `{target.get('baseline', '')}`，索引提交 `{target.get('index_commit', '') or '未提供'}`，分支验证 `{target.get('branch_scope', '') or '未提供'}`")
    if remote:
        lines.extend(["", "> 本次审计基于 GitNexus 已索引符号、调用关系和可返回源码执行。未获得完整 Git 分支文件差异，因此未命中的实现默认判为“证据不足”，不能等同于代码缺失。", ""])
    grouped = {name: [] for name in ("一致", "缺失", "不一致", "额外实现", "证据不足")}
    for item in comparison.get("items", []):
        grouped.setdefault(item.get("classification", "证据不足"), []).append(item)
    for label in ("一致", "缺失", "不一致", "额外实现", "证据不足"):
        lines.extend(["", f"## {label}", ""])
        if not grouped[label]:
            lines.append("- 无")
        for item in grouped[label]:
            lines.append(f"- `{item.get('dimension')}.{item.get('key')}`：{item.get('reason')}")
            if item.get("requirement"):
                lines.append(f"  - 需求证据：{_anchor_text(item['requirement'].get('evidence')) or item['requirement'].get('evidence', '')}")
            if item.get("code"):
                lines.append(f"  - 代码证据：{_anchor_text(item['code'].get('evidence')) or item['code'].get('evidence', '')}")
            code_item = item.get("code") or {}
            if code_item.get('coverage_status'):
                lines.append(f"  - coverage_status: `{code_item['coverage_status']}`")
    lines.extend(["", "## 测试关注点", ""])
    warnings = audit.get("warnings", [])
    lines.extend([f"- {item}" for item in warnings] or ["- 无额外警告"])
    lines.extend(["", "## 待确认事项", ""])
    review = audit.get("quality_summary", {}).get("human_review_reasons", [])
    lines.extend([f"- {item}" for item in review] or ["- 无"])
    lines.extend(["", "## 技术追溯", "", "- 内部审计产物：`.ai-internal/requirement_code_audit.json`", "- 比较产物：`.ai-internal/requirement_code_comparison.json`"])
    return "\n".join(lines)


def render_code_report(code_analysis: Dict[str, Any]) -> str:
    coverage = code_analysis.get("technology_coverage") or code_analysis.get("coverage_summary") or {}
    remote = code_analysis.get("analysis_mode") == "gitnexus_remote"
    lines = ["# 代码分析报告", "", f"- 代码来源：{'GitNexus 远程知识图谱' if remote else '本地 Git 仓库'}", f"- 目标仓库：{code_analysis.get('repo_key') or code_analysis.get('repository', '')}", f"- 目标分支：{code_analysis.get('branch', '')}", f"- 索引提交：{code_analysis.get('index_commit', '') or '未提供'}", f"- 分支验证状态：{code_analysis.get('branch_scope', '') or '未提供'}", f"- 变更文件数：{len(code_analysis.get('files_changed', []))}", f"- 覆盖结论：{coverage.get('status') or ('完整' if coverage.get('complete') else '部分覆盖')}", ""]
    if remote:
        lines.extend(["> 远程图谱结果不是完整 Git 分支差异；未返回文件/符号/源码行为证据的维度只能作为证据不足。", ""])
    lines.extend(["## 逐维度覆盖", ""])
    dimension_coverage = code_analysis.get("dimension_coverage") or coverage.get("dimension_coverage") or {}
    lines.extend([f"- `{key}`：`{value}`" for key, value in sorted(dimension_coverage.items())] or ["- 未提供"])
    lines.extend(["", "## 变更文件", ""])
    lines.extend([f"- `{path}`" for path in code_analysis.get("files_changed", [])] or ["- 远程模式不提供完整变更文件清单"])
    lines.extend(["", "## 分析警告", ""])
    lines.extend([f"- {item}" for item in code_analysis.get("analysis_warnings", [])] or ["- 无"])
    gaps = coverage.get("gaps", [])
    lines.extend(["", "## 代码侧不可审计区", "", "- MyBatis XML/SQL：" + ("部分验证" if code_analysis.get("sql_evidence_required") else "未触发 SQL 必需门禁"), "- 动态 SQL 条件：部分验证", "- 配置文件：" + ("部分验证" if dimension_coverage.get("configs") != "verified" else "已验证"), "- 存储过程：未验证"])
    return "\n".join(lines)
