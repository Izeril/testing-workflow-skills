"""Idempotent knowledge-base archive transaction and machine-readable receipt."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


SCHEMA_VERSION = "1.0.0"


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    staging.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(staging, path)


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    staging.write_text(content, encoding="utf-8")
    os.replace(staging, path)


@contextmanager
def _archive_lock(kb_path: Path, timeout_seconds: float = 10.0):
    lock_path = kb_path / ".archive.lock"
    kb_path.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    descriptor = None
    while descriptor is None:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, f"pid={os.getpid()}\n".encode("utf-8"))
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f"等待知识库归档锁超时: {lock_path}")
            time.sleep(0.05)
    try:
        yield
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _manifest_path(kb_path: Path) -> Path:
    return kb_path / ".archive-manifest.json"


def _load_manifest(kb_path: Path) -> dict[str, Any]:
    path = _manifest_path(kb_path)
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "records": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"知识库归档清单不可读: {path}: {exc}") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise ValueError(f"知识库归档清单结构非法: {path}")
    return payload


def _save_manifest(kb_path: Path, payload: dict[str, Any]) -> None:
    payload["schema_version"] = SCHEMA_VERSION
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _atomic_json(_manifest_path(kb_path), payload)


def _next_number(kb_path: Path, manifest: dict[str, Any]) -> str:
    values: list[int] = []
    for directory in ("requirements", "review-reports", "tech-solutions", "testcases"):
        path = kb_path / directory
        if not path.exists():
            continue
        for item in path.iterdir():
            match = re.match(r"(?:TR_)?(\d+)_", item.name)
            if match:
                values.append(int(match.group(1)))
    for record in manifest.get("records", []):
        try:
            values.append(int(record.get("archive_number", 0)))
        except (TypeError, ValueError):
            continue
    return f"{max(values, default=0) + 1:03d}"


def _pick(directory: Path, pattern: str, excluded: set[str] | None = None) -> Path | None:
    excluded = excluded or set()
    matches = sorted(
        (item for item in directory.glob(pattern) if item.is_file() and item.name not in excluded),
        key=lambda item: (item.stat().st_mtime, item.name),
        reverse=True,
    )
    return matches[0] if matches else None


def _discover_sources(output_dir: Path) -> list[dict[str, Any]]:
    internal = output_dir / ".ai-internal"
    candidates = [
        ("requirement", _pick(output_dir, "需求分析.md") or _pick(internal, "需求分析.md"), "requirements", ".md"),
        ("tech_solution", _pick(output_dir, "代码评审报告.md") or _pick(internal, "代码评审报告.md"), "tech-solutions", ".md"),
        ("review_report", _pick(output_dir, "*评审报告.md", {"代码评审报告.md"}), "review-reports", "_评审报告.md"),
        ("testpoints", _pick(internal, "*测试点.json") or _pick(output_dir, "*测试点.json"), "testcases", "_测试点.json"),
        ("testcases_json", _pick(internal, "测试用例.json") or _pick(output_dir, "测试用例.json"), "testcases", "_测试用例.json"),
        ("testcases_excel", _pick(output_dir, "*测试用例*.xlsx"), "testcases", "_测试用例.xlsx"),
    ]
    executable = [
        item for item in output_dir.glob("*.xmind")
        if "评审" not in item.name and "测试点_" not in item.name
    ]
    if executable:
        chosen = sorted(executable, key=lambda item: item.stat().st_mtime, reverse=True)[0]
        candidates.append(("executable_xmind", chosen, "testcases", "_可导入.xmind"))
    result = []
    for role, source, target_dir, suffix in candidates:
        if source and source.exists():
            result.append({
                "role": role,
                "source": source,
                "target_dir": target_dir,
                "suffix": suffix,
                "sha256": _digest(source),
            })
    return result


def _content_digest(sources: list[dict[str, Any]]) -> str:
    canonical = [
        {"role": item["role"], "sha256": item["sha256"]}
        for item in sorted(sources, key=lambda item: item["role"])
    ]
    return hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _safe_copy(source: Path, target: Path) -> bool:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if _digest(source) == _digest(target):
            return False
        raise FileExistsError(f"归档目标已存在且内容不同: {target}")
    shutil.copy2(source, target)
    return True


def _normalize_root_causes(root_causes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in root_causes:
        category = item.get("category") or item.get("type")
        if not category:
            continue
        result.append({
            "category": category,
            "scenario": item.get("scenario") or item.get("description") or "",
            "improvement": item.get("improvement") or "补充对应规则与回归样本",
            "count": int(item.get("count", 1) or 1),
        })
    return result


def _update_evolution_once(kb_manager: Any, record: dict[str, Any]) -> bool:
    log_file = Path(kb_manager.kb_path) / "EVOLUTION_LOG.md"
    if not log_file.exists():
        kb_manager._create_evolution_log()
    content = log_file.read_text(encoding="utf-8") if log_file.exists() else "# 进化日志\n"
    marker = f"<!-- archive_id:{record['archive_id']} -->"
    if marker in content:
        return False
    block = marker + kb_manager._format_evolution_record(record)
    _atomic_text(log_file, content.rstrip() + "\n\n" + block.strip() + "\n")
    return True


def _write_receipt(output_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        **payload,
    }
    _atomic_json(output_dir / ".ai-internal" / "archive_receipt.json", payload)
    return payload


def run_archive_idempotent(
    req_dir: Path,
    output_dir: Path,
    project_info: dict[str, Any],
    kb_manager: Any,
    user_config: dict[str, Any],
    analyze_review_diff: Callable[[Path, str], dict[str, int]],
    analyze_root_causes: Callable[[dict[str, int], Path], list[dict[str, Any]]],
    log_console: Callable[..., None],
) -> dict[str, Any]:
    """Archive once per logical project/content digest and resume safely on rerun."""
    output_dir = Path(output_dir)
    if not kb_manager or not getattr(kb_manager, "kb_path", None):
        log_console("  ⚠️ 知识库未初始化，无法入库")
        return _write_receipt(output_dir, {
            "status": "unavailable",
            "completed": False,
            "reason": "knowledge_base_not_initialized",
            "archived_files": [],
        })

    kb_path = Path(kb_manager.kb_path)
    doc_name = str(project_info.get("project_name") or output_dir.parent.name or "需求")
    doc_name = doc_name.replace("【", "").replace("】", "").strip() or "需求"
    logical_key = re.sub(r"\s+", " ", doc_name).casefold()
    sources = _discover_sources(output_dir)
    if not sources:
        log_console("  ⚠️ 未找到任何可归档产物")
        return _write_receipt(output_dir, {
            "status": "blocked_no_artifacts",
            "completed": False,
            "reason": "no_archivable_artifacts",
            "logical_key": logical_key,
            "archived_files": [],
        })

    digest = _content_digest(sources)
    archive_id = "ARC-" + hashlib.sha256(
        f"{logical_key}\n{digest}".encode("utf-8")
    ).hexdigest()[:16]

    with _archive_lock(kb_path):
        manifest = _load_manifest(kb_path)
        existing = next((
            record for record in reversed(manifest.get("records", []))
            if record.get("logical_key") == logical_key
            and record.get("content_digest") == digest
        ), None)
        if existing:
            log_console(f"  ♻️ 内容已归档，复用 {existing.get('archive_id', archive_id)}")
            return _write_receipt(output_dir, {
                **existing,
                "status": "already_archived",
                "completed": True,
            })

        number = _next_number(kb_path, manifest)
        date_token = datetime.now().strftime("%Y%m%d")
        prefix = f"{number}_{doc_name}_{date_token}"
        archived_files = []
        for item in sources:
            base = f"TR_{prefix}" if item["role"] == "tech_solution" else prefix
            target = kb_path / item["target_dir"] / f"{base}{item['suffix']}"
            _safe_copy(item["source"], target)
            archived_files.append({
                "role": item["role"],
                "source": str(item["source"]),
                "target": str(target),
                "sha256": item["sha256"],
            })
            log_console(f"  ✅ 入库: {target.name}")

        diff_stats = analyze_review_diff(output_dir, doc_name)
        root_causes = _normalize_root_causes(analyze_root_causes(diff_stats, output_dir))
        record = {
            "archive_id": archive_id,
            "archive_number": number,
            "logical_key": logical_key,
            "project_name": doc_name,
            "content_digest": digest,
            "archived_at": datetime.now().isoformat(timespec="seconds"),
            "status": "archived",
            "completed": True,
            "archived_files": archived_files,
            "diff_stats": diff_stats,
            "root_causes": root_causes,
        }
        _update_evolution_once(kb_manager, record)
        essence_result = kb_manager.extract_essence(root_causes)
        kb_manager.update_index()
        record["essence_result"] = essence_result
        manifest.setdefault("records", []).append(record)
        _save_manifest(kb_path, manifest)

    log_console(f"  📦 入库完成: {len(archived_files)} 个文件已入库")
    return _write_receipt(output_dir, record)
