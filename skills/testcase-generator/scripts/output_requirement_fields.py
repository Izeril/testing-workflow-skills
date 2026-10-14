"""Extract only explicitly named response fields from requirement Markdown."""

from __future__ import annotations

import re


OUTPUT_LABEL = re.compile(r"返回字段|响应字段|输出字段|出参|结果字段|报表字段")
FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ROUTE = re.compile(r"(?i)\b(?:GET|POST|PUT|DELETE|PATCH)\s+(/[\w{}./-]+)")
PATH = re.compile(r"(?:接口路径|请求路径)\s*[:：]\s*(/[\w{}./-]+)")


def extract_explicit_output_fields(doc_content: str, known_endpoints: list[str]) -> list[dict]:
    """Return field facts; an unscoped field is retained but is not comparable."""
    endpoints = list(dict.fromkeys(value for value in known_endpoints if value))
    current_endpoint = ""
    active = False
    results = []
    for raw in doc_content.splitlines():
        line = raw.strip()
        route = ROUTE.search(line) or PATH.search(line)
        if route:
            current_endpoint = route.group(1).rstrip("/") or "/"
        if line.startswith("#"):
            active = bool(OUTPUT_LABEL.search(line))
            continue
        label = re.match(r"^(?:返回字段|响应字段|输出字段|出参|结果字段|报表字段)\s*[:：]\s*(.*)$", line)
        if label:
            active = True
            line = label.group(1).strip()
        elif re.match(r"^(?:入参|请求字段|数据库字段|表字段)\s*[:：]", line):
            active = False
        if not active or not line or line.startswith(chr(96) * 3):
            continue
        candidates = []
        if line.startswith("|"):
            cells = [cell.strip().strip(chr(96)) for cell in line.strip("|").split("|")]
            if cells:
                candidates = [cells[0]]
        elif label:
            candidates = [
                part.strip().strip(chr(96))
                for part in re.split(r"[、,，；;\s]+", line)
            ]
        else:
            bullet = re.match(r"^[-*+]\s*" + chr(96) + r"?([A-Za-z_][A-Za-z0-9_]*)", line)
            if bullet:
                candidates = [bullet.group(1)]
        for field in candidates:
            if not FIELD.fullmatch(field) or field.casefold() in {"field", "type"}:
                continue
            endpoint = current_endpoint or (endpoints[0] if len(endpoints) == 1 else "")
            item = {"field": field, "endpoint": endpoint, "comparable": bool(endpoint)}
            if item not in results:
                results.append(item)
    return results
