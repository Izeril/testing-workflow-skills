"""Persist online requirement sources in standard folders and reuse them offline."""
from __future__ import annotations
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MANIFEST = ".ai-internal/source_documents_manifest.json"
KINDS = {"requirement": "requirement", "需求描述": "requirement", "需求文档": "requirement",
         "test": "test", "提测文档": "test", "design": "design", "设计方案": "design",
         "interface": "interface", "接口文档": "interface"}
FOLDERS = {"requirement": "需求文档", "test": "提测文档", "design": "需求文档", "interface": "需求文档"}

def _json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"来源清单不可读：{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"来源清单必须为 JSON 对象：{path}")
    return value

def _inside(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"来源文件路径越出需求目录：{path}")
    return resolved

def _kind(record: dict) -> str:
    result = KINDS.get(record.get("kind", ""))
    if not result:
        raise ValueError(f"不支持的来源类型：{record.get('kind')}")
    return result

def _cached_record(root: Path, record: dict) -> dict:
    kind = _kind(record)
    raw_path = record.get("local_path") or record.get("markdown")
    if not raw_path:
        raise ValueError("来源清单缺少 local_path")
    path = Path(raw_path)
    if not path.is_absolute():
        path = root / path
    path = _inside(root, path)
    if not path.is_relative_to((root / FOLDERS[kind]).resolve()) or path.suffix.lower() != ".md":
        raise ValueError(f"来源正文必须保存到标准文档目录：{path}")
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"本地来源不可读，请补齐或显式刷新：{path}") from exc
    if not text.strip():
        raise ValueError(f"本地来源正文为空：{path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    expected = record.get("markdown_sha256")
    return {**record, "kind": kind, "local_path": str(path),
            "current_sha256": digest, "cache_status": "local_modified" if expected and digest != expected else "local_available"}

def load_cached_sources(requirement_dir: Path) -> list[dict]:
    root = Path(requirement_dir).resolve()
    path = root / MANIFEST
    if not path.exists():
        return []
    payload = _json(path)
    records = payload.get("documents")
    if not isinstance(records, list) or not records:
        raise ValueError("来源清单 documents 必须是非空列表")
    if any(not isinstance(item, dict) for item in records):
        raise ValueError("来源记录必须是对象")
    return [_cached_record(root, item) for item in records]

def cached_source_paths(requirement_dir: Path) -> list[Path] | None:
    """A manifest fixes the source scope; generated and older files are excluded."""
    root = Path(requirement_dir)
    if not (root / MANIFEST).exists():
        return None
    return [Path(item["local_path"]) for item in load_cached_sources(root)]

def resolve_cached_url(requirement_dir: Path, url: str) -> str | None:
    for record in load_cached_sources(requirement_dir):
        if record.get("url") == url:
            return record["local_path"]
    return None

def _filename(record: dict, kind: str) -> str:
    explicit = record.get("filename")
    if explicit:
        if Path(explicit).name != explicit or any(c in explicit for c in "\\/:") or not explicit.lower().endswith(".md"):
            raise ValueError("filename 必须是单个 Markdown 文件名")
        name = explicit
    else:
        title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(record.get("title") or kind)).strip(" .")[:100]
        suffix = {"design": "_设计方案", "interface": "_接口文档", "test": "_提测文档"}.get(kind, "")
        name = title + suffix + ".md"
    if name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1,10)), *(f"LPT{i}" for i in range(1,10))}:
        raise ValueError("filename 不能是 Windows 保留名称")
    return name

def prepare_source_documents(requirement_dir: Path, import_path: str | None = None, *, refresh: bool = False) -> dict:
    """Import tool-fetched Markdown; this function never performs network requests."""
    root = Path(requirement_dir).resolve()
    if refresh and not import_path:
        raise ValueError("--refresh-documents 需要 --source-documents 提供本次重新获取的正文")
    manifest_path = root / MANIFEST
    if not import_path:
        records = load_cached_sources(root)
        return {"status": "local_reused" if records else "unconfigured", "documents": records, "network_requests": 0}
    incoming = _json(Path(import_path))
    entries = incoming.get("documents")
    if not isinstance(entries, list) or not entries:
        raise ValueError("--source-documents 的 documents 必须是非空列表")
    # Explicit refresh can repair missing files; ordinary reuse must fail visibly.
    previous = _json(manifest_path).get("documents", []) if manifest_path.exists() else []
    if not isinstance(previous, list):
        raise ValueError("既有来源清单 documents 非法")
    old_by_url = {x.get("url"): x for x in previous if isinstance(x, dict) and x.get("url")}
    planned, destinations, urls = [], set(), set()
    for item in entries:
        if not isinstance(item, dict) or not str(item.get("url", "")).startswith(("https://", "http://")):
            raise ValueError("在线来源需提供类型和完整 URL")
        kind = _kind(item)
        if item["url"] in urls:
            raise ValueError("来源清单存在重复 URL")
        urls.add(item["url"])
        old = old_by_url.get(item["url"])
        if old and not refresh:
            saved = _cached_record(root, old)
            dest = Path(saved["local_path"])
            if dest in destinations:
                raise ValueError("多个来源不能共用一个正文文件")
            destinations.add(dest)
            planned.append((saved, None, None))
            continue
        content = item.get("markdown")
        if content is None:
            if not item.get("markdown_path"):
                raise ValueError("来源需提供 markdown 或 markdown_path")
            content = Path(item["markdown_path"]).read_text(encoding="utf-8-sig")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("在线来源正文为空")
        dest = _inside(root, root / FOLDERS[kind] / _filename(item, kind))
        if dest in destinations:
            raise ValueError("多个来源不能共用一个正文文件")
        if dest.exists() and not (refresh and old and Path(old.get("local_path", "")).resolve() == dest):
            raise ValueError(f"不能覆盖来源不明的既有文档：{dest}")
        destinations.add(dest)
        data = content.encode("utf-8")
        raw = item.get("raw_payload")
        if item.get("raw_json_path"):
            raw = _json(Path(item["raw_json_path"]))
        raw_dest = root / ".ai-internal/remote_sources" / (hashlib.sha256(item["url"].encode()).hexdigest()[:16]+".json")
        saved = {"kind": kind, "title": str(item.get("title") or dest.stem), "url": item["url"],
                 "page_id": item.get("page_id"), "page_version": item.get("page_version", item.get("version")),
                 "page_updated_at": item.get("page_updated_at", item.get("updated")),
                 "fetched_at": item.get("fetched_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "local_path": str(dest), "markdown_sha256": hashlib.sha256(data).hexdigest(),
                 "original_payload": str(raw_dest) if raw is not None else None}
        planned.append((saved, data, raw))
    # Validate the whole batch before replacing any authoritative source.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    if manifest_path.exists() and refresh:
        previous_manifest = _inside(root, root / ".ai-internal/source_history" / stamp / "source_documents_manifest.json")
        previous_manifest.parent.mkdir(parents=True, exist_ok=True)
        previous_manifest.write_bytes(manifest_path.read_bytes())
    for saved, data, raw in planned:
        if data is None:
            continue
        dest = Path(saved["local_path"])
        if dest.exists():
            history = _inside(root, root / ".ai-internal/source_history" / stamp / dest.name)
            history.parent.mkdir(parents=True, exist_ok=True)
            history.write_bytes(dest.read_bytes())
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        if raw is not None:
            raw_path = _inside(root, Path(saved["original_payload"]))
            if raw_path.exists():
                history = _inside(root, root / ".ai-internal/source_history" / stamp / raw_path.name)
                history.parent.mkdir(parents=True, exist_ok=True)
                history.write_bytes(raw_path.read_bytes())
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": "1.0.0", "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "reuse_policy": {"default_source": "local_snapshot", "refresh_requires_explicit_request": True},
               "documents": [entry[0] for entry in planned]}
    manifest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return {"status": "refreshed" if refresh else "saved_or_reused",
            "documents": load_cached_sources(root), "network_requests": 0}
