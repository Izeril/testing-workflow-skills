#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build an auditable inventory of requirement documents, links and images."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple
from urllib.parse import unquote, urlparse
from source_documents import resolve_cached_url


SCHEMA_VERSION = "1.0.0"
AVAILABILITY = {
    "local_available",
    "linked_available",
    "linked_missing",
    "unreadable",
    "not_applicable",
}
ATTACHMENT_EXTENSIONS = {
    ".doc", ".docx", ".xls", ".xlsx", ".pdf", ".zip", ".rar", ".7z",
    ".json", ".csv", ".sql", ".xmind",
}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp"}
DECISION_CONTEXT = re.compile(
    r"字段|接口|参数|规则|口径|扣费|计费|评分|方案|设计|附件|截图|数据字典",
    re.IGNORECASE,
)


def _link_matches(content: str) -> Iterable[Tuple[str, str, int, int]]:
    occupied = []
    for match in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", content):
        occupied.append(match.span())
        yield "image", match.group(2), match.start(), match.end()
    for match in re.finditer(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)", content):
        occupied.append(match.span())
        yield "attachment", match.group(2), match.start(), match.end()
    for match in re.finditer(r"<(https?://[^>]+)>", content, re.IGNORECASE):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        yield "attachment", match.group(1), match.start(), match.end()


def _clean_target(raw: str) -> str:
    value = str(raw or "").strip()
    if value.startswith("<") and ">" in value:
        value = value[1:value.index(">")]
    else:
        value = re.split(r"\s+[\"']", value, maxsplit=1)[0]
    return unquote(value.strip().strip("<>"))


def _classify_target(target: str, requirement_dir: Path) -> Tuple[str, str | None]:
    if not target or target.startswith("#") or target.lower().startswith(("mailto:", "javascript:")):
        return "not_applicable", None
    parsed = urlparse(target)
    if parsed.scheme in {"http", "https"}:
        saved = resolve_cached_url(requirement_dir, target)
        return ("linked_available", saved) if saved else ("linked_missing", None)
    if parsed.scheme == "file":
        candidate = Path(unquote(parsed.path.lstrip("/")))
    else:
        candidate = Path(target)
        if not candidate.is_absolute():
            candidate = requirement_dir / candidate
    try:
        candidate = candidate.resolve()
    except OSError:
        return "unreadable", str(candidate)
    if not candidate.exists():
        return "linked_missing", str(candidate)
    if not candidate.is_file():
        return "unreadable", str(candidate)
    try:
        with candidate.open("rb") as handle:
            handle.read(1)
    except OSError:
        return "unreadable", str(candidate)
    return "local_available", str(candidate)


def build_source_inventory(
    content: str,
    requirement_dir: Path,
    normalized_path: Path,
) -> Dict[str, Any]:
    requirement_dir = Path(requirement_dir)
    normalized_path = Path(normalized_path)
    attachments = []
    images = []
    seen = set()
    for kind, raw_target, start, end in _link_matches(content):
        target = _clean_target(raw_target)
        key = (kind, target.casefold())
        if not target or key in seen:
            continue
        seen.add(key)
        suffix = Path(urlparse(target).path).suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            kind = "image"
        availability, local_path = _classify_target(target, requirement_dir)
        line_start = content.rfind("\n", 0, start) + 1
        line_end = content.find("\n", end)
        if line_end < 0:
            line_end = len(content)
        context = re.sub(r"\s+", " ", content[line_start:line_end]).strip()
        is_attachment = suffix in ATTACHMENT_EXTENSIONS or kind == "image"
        decision_required = availability in {"linked_missing", "unreadable"} and (
            is_attachment or bool(DECISION_CONTEXT.search(context))
        )
        collection = images if kind == "image" else attachments
        prefix = "IMG" if kind == "image" else "ATT"
        item = {
            "attachment_id": f"{prefix}-{len(collection) + 1:03d}",
            "name": Path(urlparse(target).path).name or target[:80],
            "source_document": "SRC-001",
            "url": target,
            "local_path": local_path,
            "availability": availability,
            "decision_required": decision_required,
            "context": context[:240],
        }
        collection.append(item)
    all_items = attachments + images
    return {
        "schema_version": SCHEMA_VERSION,
        "documents": [
            {
                "source_id": "SRC-001",
                "kind": "requirement",
                "original_path": str(requirement_dir),
                "normalized_path": str(normalized_path),
                "conversion_status": "passed",
            }
        ],
        "attachments": attachments,
        "images": images,
        "summary": {
            "attachment_count": len(attachments),
            "image_count": len(images),
            "linked_missing_count": sum(
                1 for item in all_items if item["availability"] == "linked_missing"
            ),
            "unreadable_count": sum(
                1 for item in all_items if item["availability"] == "unreadable"
            ),
            "decision_required_count": sum(
                1 for item in all_items if item["decision_required"]
            ),
        },
    }
