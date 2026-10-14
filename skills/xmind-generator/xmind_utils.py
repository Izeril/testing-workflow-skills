#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared helpers for XMind ZIP-based test artifacts."""

from __future__ import annotations

import json
import ntpath
import os
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional


EMPTY_THUMBNAIL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\xf8\x0f\x00\x00\x01\x00\x01"
    b"\x00\x18\xdd\x8d\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _absolute_path(path: str | Path) -> str:
    """Return an absolute path without requiring the target to exist."""
    raw_path = os.fspath(path)
    if os.name == "nt":
        return ntpath.abspath(raw_path)
    return str(Path(raw_path).expanduser().absolute())


def _filesystem_path(path: str | Path) -> str:
    """Add the Windows extended-length prefix while leaving other platforms unchanged."""
    absolute_path = _absolute_path(path)
    if os.name != "nt" or absolute_path.startswith("\\\\?\\"):
        return absolute_path
    if absolute_path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute_path.lstrip("\\")
    return "\\\\?\\" + absolute_path


def gen_id(length: int = 16) -> str:
    return uuid.uuid4().hex[:length]


def make_node(
    title: str,
    markers: Optional[List[Dict]] = None,
    children: Optional[List[Dict]] = None,
    node_class: str = "attached topic",
) -> Dict:
    node = {
        "id": gen_id(),
        "class": node_class,
        "title": title,
        "titleUnedited": False,
    }
    if markers:
        node["markers"] = markers
    if children:
        node["children"] = {"attached": children}
    return node


def make_sheet(root_topic: Dict, title: str) -> Dict:
    return {
        "id": gen_id(),
        "revisionId": gen_id(),
        "class": "sheet",
        "rootTopic": root_topic,
        "title": title,
    }


def default_metadata(active_sheet_id: Optional[str] = None) -> Dict:
    metadata = {
        "dataStructureVersion": "2",
        "modifier": "Vana 22.11.3771.202305311822",
        "layoutEngineVersion": "3",
        "creator": {"name": "Vana", "version": "25.01.01061"},
    }
    if active_sheet_id:
        metadata["activeSheetId"] = active_sheet_id
    return metadata


def default_manifest() -> Dict:
    return {
        "file-entries": {
            "content.json": {},
            "metadata.json": {},
            "manifest.json": {},
            "Thumbnails/thumbnail.png": {},
        }
    }


def read_xmind_sheets(xmind_path: str | Path) -> List[Dict]:
    with zipfile.ZipFile(_filesystem_path(xmind_path), "r") as zf:
        content = json.loads(zf.read("content.json"))
    if isinstance(content, dict):
        return content.get("sheets", [])
    return content


def iter_topics(topic: Dict, path: Optional[List[str]] = None) -> Iterable[tuple[Dict, List[str]]]:
    current_path = (path or []) + [topic.get("title", "")]
    yield topic, current_path
    for child in topic.get("children", {}).get("attached", []):
        yield from iter_topics(child, current_path)


def _write_zip_payload(
    archive_path: str | Path,
    content: List[Dict],
    metadata: Dict,
    manifest: Dict,
    source_xmind: Optional[str | Path] = None,
) -> None:
    copied_entries = set()
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("content.json", json.dumps(content, ensure_ascii=False, indent=2))
        zf.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        copied_entries.update({"content.json", "metadata.json", "manifest.json"})

        if source_xmind:
            with zipfile.ZipFile(_filesystem_path(source_xmind), "r") as src:
                for item in src.namelist():
                    if item not in copied_entries:
                        zf.writestr(item, src.read(item))
                        copied_entries.add(item)

        if "Thumbnails/thumbnail.png" not in copied_entries:
            zf.writestr("Thumbnails/thumbnail.png", EMPTY_THUMBNAIL_PNG)


def _reserve_path(path: str | Path) -> Path:
    candidate = Path(path)
    filesystem_candidate = _filesystem_path(candidate)
    os.makedirs(_filesystem_path(candidate.parent), exist_ok=True)
    descriptor = os.open(
        filesystem_candidate,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
    )
    os.close(descriptor)
    return candidate


def reserve_versioned_path(
    output_dir: str | Path,
    stem: str,
    suffix: str = ".xmind",
    start: int = 1,
) -> Path:
    """Atomically reserve the next versioned filename for concurrent writers."""
    output_dir = Path(output_dir)
    version = max(1, int(start))
    while True:
        candidate = output_dir / f"{stem}_v{version}{suffix}"
        try:
            return _reserve_path(candidate)
        except FileExistsError:
            version += 1


def reserve_unique_path(preferred_path: str | Path) -> Path:
    """Reserve a preferred name, then use ``_v2`` and above if it exists."""
    preferred = Path(preferred_path)
    try:
        return _reserve_path(preferred)
    except FileExistsError:
        version = 2
        while True:
            candidate = preferred.with_name(f"{preferred.stem}_v{version}{preferred.suffix}")
            try:
                return _reserve_path(candidate)
            except FileExistsError:
                version += 1


def release_reserved_path(path: str | Path) -> None:
    """Remove an unused zero-byte reservation after a failed build."""
    filesystem_path = _filesystem_path(path)
    try:
        if os.path.getsize(filesystem_path) == 0:
            os.unlink(filesystem_path)
    except FileNotFoundError:
        pass

def write_xmind_zip(
    output_path: str | Path,
    content: List[Dict],
    metadata: Optional[Dict] = None,
    manifest: Optional[Dict] = None,
    source_xmind: Optional[str | Path] = None,
) -> str:
    """Build in a short temp directory and atomically publish to a long-path-safe target."""
    requested_output = os.fspath(output_path)
    absolute_output = _absolute_path(output_path)
    filesystem_output = _filesystem_path(absolute_output)
    output_parent = ntpath.dirname(absolute_output) if os.name == "nt" else str(Path(absolute_output).parent)
    filesystem_parent = _filesystem_path(output_parent)
    os.makedirs(filesystem_parent, exist_ok=True)

    metadata = metadata or default_metadata(content[0].get("id") if content else None)
    manifest = manifest or default_manifest()
    staging_name = f".xmind-{uuid.uuid4().hex[:10]}.tmp"
    staging_path = ntpath.join(output_parent, staging_name) if os.name == "nt" else str(Path(output_parent) / staging_name)
    filesystem_staging = _filesystem_path(staging_path)

    try:
        with tempfile.TemporaryDirectory(prefix="xmind-") as temp_dir:
            short_archive = str(Path(temp_dir) / "artifact.xmind")
            _write_zip_payload(short_archive, content, metadata, manifest, source_xmind)
            shutil.copyfile(short_archive, filesystem_staging)
        os.replace(filesystem_staging, filesystem_output)
    finally:
        if os.path.exists(filesystem_staging):
            os.unlink(filesystem_staging)

    return requested_output


def create_xmind_file(output_path: str | Path, root_topic: Dict, project_name: str) -> str:
    """Backward-compatible helper used by testcase-generator and old scripts."""
    sheet = make_sheet(root_topic, project_name)
    metadata = default_metadata(sheet["id"])
    return write_xmind_zip(output_path, [sheet], metadata, default_manifest())


# Backward-compatible alias for older scripts.
gid = gen_id
