"""Conservative Java response and MyBatis output evidence extraction."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Callable


_MAPPING = re.compile(r"@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)\s*\(([^)]*)\)", re.S)
_METHOD = re.compile(r"\b(?:public|protected)\s+([\w<>?,.\[\] ]+?)\s+(\w+)\s*\([^)]*\)\s*\{", re.S)
_DTO = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:DTO|VO|Response))\b")
_SETTER = re.compile(r"\b(\w+)\.set([A-Z]\w*)\s*\(([^;]*?)\)\s*;", re.S)
_FIELD = re.compile(
    r"^\s*(?:private|protected|public)\s+(?!static\b|transient\b)"
    r"[\w<>?,.\[\] ]+\s+(\w+)\s*(?:=[^;]*)?;",
    re.M,
)


def _line(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _attribute(text: str, name: str) -> str:
    match = re.search(r"\b" + re.escape(name) + r"\s*=\s*['\"]([^'\"]+)['\"]", text)
    return match.group(1) if match else ""


def _path(annotation: str) -> str:
    match = re.search(r"(?:\b(?:value|path)\s*=\s*)?['\"]([^'\"]+)['\"]", annotation)
    return match.group(1) if match else ""


def _join_path(prefix: str, suffix: str) -> str:
    return "/" + "/".join(part.strip("/") for part in (prefix, suffix) if part.strip("/"))


def _body_end(source: str, start: int) -> int:
    depth = 0
    state = "code"
    index = start
    while index < len(source):
        ch = source[index]
        nxt = source[index + 1] if index + 1 < len(source) else ""
        if state == "line":
            if ch == "\n":
                state = "code"
        elif state == "block":
            if ch == "*" and nxt == "/":
                state = "code"
                index += 1
        elif state in {"'", '"'}:
            if ch == "\\":
                index += 1
            elif ch == state:
                state = "code"
        elif ch == "/" and nxt == "/":
            state = "line"
            index += 1
        elif ch == "/" and nxt == "*":
            state = "block"
            index += 1
        elif ch in {"'", '"'}:
            state = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return len(source)


def _dto_fields(source: str, path: str) -> list[dict]:
    fields = []
    for match in _FIELD.finditer(source):
        previous = source[max(0, match.start() - 180):match.start()]
        if re.search(r"@JsonIgnore\s*$", previous):
            continue
        annotation = re.search(r"@JsonProperty\s*\(\s*['\"]([^'\"]+)['\"]\s*\)\s*$", previous)
        fields.append({
            "field": annotation.group(1) if annotation else match.group(1),
            "java_field": match.group(1),
            "file": path,
            "line": _line(source, match.start()),
            "evidence_level": "declared",
        })
    return fields


def _return_variable(body: str) -> str:
    expressions = re.findall(r"\breturn\s+([^;]+);", body)
    if not expressions:
        return ""
    variables = []
    for expression in expressions:
        expression = expression.strip()
        match = re.fullmatch(r"([A-Za-z_]\w*)", expression)
        if not match:
            match = re.fullmatch(
                r"(?:ResponseEntity\.ok|\w+\.success|\w+\.ok)\s*\(\s*([A-Za-z_]\w*)\s*\)",
                expression,
            )
        if not match:
            return ""
        variables.append(match.group(1))
    return variables[0] if len(set(variables)) == 1 else ""


def _service_return_context(
    controller_source: str,
    controller_body: str,
    returned: str,
    path_index: dict[str, list[str]],
    sources: dict[str, str],
    read_source: Callable[[str], str],
) -> tuple[str, str, str, int, str] | None:
    """Follow one explicit controller-to-service return edge."""
    expressions = re.findall(r"\breturn\s+([^;]+);", controller_body)
    if returned:
        assigned = re.search(
            r"\b" + re.escape(returned) + r"\s*=\s*(\w+)\.(\w+)\s*\(",
            controller_body,
        )
        calls = [assigned.groups()] if assigned else []
    else:
        calls = []
        for expression in expressions:
            calls.extend(re.findall(r"\b(\w+)\.(\w+)\s*\(", expression))
    for obj, method in calls:
        declaration = re.search(
            r"\b([A-Z]\w+)\s+" + re.escape(obj) + r"\s*(?:[;=])",
            controller_source,
        )
        if not declaration:
            continue
        candidates = path_index.get(declaration.group(1), [])
        if len(candidates) != 1:
            continue
        service_path = candidates[0]
        service_source = sources.get(service_path) or read_source(service_path)
        if not service_source:
            continue
        signatures = [match for match in _METHOD.finditer(service_source) if match.group(2) == method]
        if not signatures:
            implementation_paths = path_index.get(declaration.group(1) + "Impl", [])
            implementation_paths = [
                path for path in implementation_paths
                if path.endswith(".java")
                and re.search(
                    r"\bimplements\s+" + re.escape(declaration.group(1)) + r"\b",
                    sources.get(path) or read_source(path),
                )
            ]
            if len(implementation_paths) == 1:
                service_path = implementation_paths[0]
                service_source = sources.get(service_path) or read_source(service_path)
                signatures = [
                    match for match in _METHOD.finditer(service_source)
                    if match.group(2) == method
                ]
        if len(signatures) != 1:
            continue
        signature = signatures[0]
        end = _body_end(service_source, signature.end() - 1)
        service_body = service_source[signature.end():end]
        service_returned = _return_variable(service_body)
        if service_returned:
            return service_body, service_source, service_path, signature.end(), service_returned
    return None


def _upstream_mybatis_column(
    expression: str,
    method_body: str,
    class_source: str,
    path_index: dict[str, list[str]],
    sources: dict[str, str],
    read_source: Callable[[str], str],
) -> dict:
    """Trace a simple entity getter to one MyBatis resultMap property."""
    getter = re.fullmatch(r"(\w+)\.get([A-Z]\w*)\s*\(\s*\)", expression)
    if not getter:
        return {}
    source_var = getter.group(1)
    property_name = getter.group(2)[0].lower() + getter.group(2)[1:]
    assignment = re.search(
        r"\b" + re.escape(source_var) + r"\s*=\s*(\w+)\.(\w+)\s*\(",
        method_body,
    )
    if not assignment:
        return {}
    mapper_obj, mapper_method = assignment.groups()
    declaration = re.search(
        r"\b([A-Z]\w*Mapper)\s+" + re.escape(mapper_obj) + r"\b",
        class_source,
    )
    if not declaration:
        return {}
    mapper_paths = [
        path for path in path_index.get(declaration.group(1), [])
        if path.endswith(".xml")
    ]
    if len(mapper_paths) != 1:
        return {}
    mapper_path = mapper_paths[0]
    mapper_source = sources.get(mapper_path) or read_source(mapper_path)
    if not mapper_source:
        return {}
    queries = [
        query for query in re.finditer(r"<select\b([^>]*)>(.*?)</select\s*>", mapper_source, re.S | re.I)
        if _attribute(query.group(1), "id") == mapper_method
    ]
    if len(queries) != 1:
        return {}
    map_id = _attribute(queries[0].group(1), "resultMap")
    if not map_id:
        return {}
    for mapping in re.finditer(r"<resultMap\b([^>]*)>(.*?)</resultMap\s*>", mapper_source, re.S | re.I):
        if _attribute(mapping.group(1), "id") != map_id:
            continue
        for node in re.finditer(r"<(?:id|result)\b([^>]*)/?>", mapping.group(2), re.I):
            if _attribute(node.group(1), "property") == property_name:
                column = _attribute(node.group(1), "column")
                if column:
                    return {
                        "source_field": property_name,
                        "source_column": column,
                        "mapper_method": mapper_method,
                        "mapper_file": mapper_path,
                        "mapper_line": _line(mapper_source, mapping.start(2) + node.start()),
                    }
    return {}


def analyze_output_layer(
    sources: dict[str, str], repository_paths: list[str], read_source: Callable[[str], str]
) -> dict:
    """Return confirmed field assignments and unresolved output candidates.

    Coverage stays partial because branch specific serializers and indirect
    service returns cannot be proven by this extractor.
    """
    result = {
        "schema_version": "1.0.0",
        "endpoints": [],
        "verified_fields": [],
        "declared_fields": [],
        "sql_results": [],
        "warnings": [],
        "coverage_status": "partial",
    }
    snapshot_cache: dict[str, str] = {}

    def source_at(path: str) -> str:
        if path in sources:
            return sources[path]
        if path not in snapshot_cache:
            snapshot_cache[path] = read_source(path)
        return snapshot_cache[path]

    path_index: dict[str, list[str]] = {}
    for path in [*repository_paths, *sources]:
        candidates = path_index.setdefault(PurePosixPath(path).stem, [])
        if path not in candidates:
            candidates.append(path)

    for path, source in sources.items():
        if not path.endswith(".java"):
            continue
        class_path = ""
        class_match = re.search(r"@RequestMapping\s*\(([^)]*)\)[\s\S]{0,350}?\bclass\s+\w+", source)
        if class_match:
            class_path = _path(class_match.group(1))
        for annotation in _MAPPING.finditer(source):
            signature = _METHOD.search(source, annotation.end())
            if not signature or signature.start() - annotation.end() > 500:
                continue
            if re.search(r"\bclass\s+\w+", source[annotation.end():signature.start()]):
                continue
            endpoint = _join_path(class_path, _path(annotation.group(2)))
            end = _body_end(source, signature.end() - 1)
            body = source[signature.end():end]
            returned = _return_variable(body)
            mapping_body, mapping_source, mapping_path = body, source, path
            mapping_start = signature.end()
            if not returned or not any(match.group(1) == returned for match in _SETTER.finditer(body)):
                service_context = _service_return_context(
                    source, body, returned, path_index, sources, source_at
                )
                if service_context:
                    mapping_body, mapping_source, mapping_path, mapping_start, returned = service_context
            dto_types = _DTO.findall(signature.group(1))
            if not dto_types:
                dto_types = [
                    token for token in re.findall(r"\b[A-Z][A-Za-z0-9_]*\b", signature.group(1))
                    if len(path_index.get(token, [])) == 1
                    and token not in {"ResponseEntity", "Result", "Object", "Map", "List", "Page"}
                ]
            record = {
                "endpoint": endpoint,
                "method": signature.group(2),
                "return_type": signature.group(1).strip(),
                "file": path,
                "line": _line(source, annotation.start()),
                "returned_variable": returned,
                "dto_type": dto_types[-1] if dto_types else "",
                "status": "unresolved",
            }
            result["endpoints"].append(record)
            if not returned or not dto_types:
                result["warnings"].append(
                    f"{path}:{record['line']} {endpoint}: 返回对象或 DTO 类型未解析"
                )
                continue
            candidates = path_index.get(dto_types[-1], [])
            if len(candidates) != 1:
                result["warnings"].append(
                    f"{path}:{record['line']} {endpoint}: DTO {dto_types[-1]} 路径不唯一或未找到"
                )
                continue
            dto_path = candidates[0]
            dto_source = source_at(dto_path)
            if not dto_source:
                result["warnings"].append(f"{dto_path}: DTO 源码不可读")
                continue
            fields = _dto_fields(dto_source, dto_path)
            record["status"] = "partial"
            record["dto_file"] = dto_path
            for field in fields:
                item = {
                    **field,
                    "endpoint": endpoint,
                    "field_key": f"{endpoint}.{field['field']}",
                }
                result["declared_fields"].append(item)
                setter = next(
                    (match for match in _SETTER.finditer(mapping_body)
                     if match.group(1) == returned
                     and match.group(2)[0].lower() + match.group(2)[1:] == field["java_field"]),
                    None,
                )
                if setter:
                    expression = setter.group(3).strip()
                    upstream = _upstream_mybatis_column(
                        expression, mapping_body, mapping_source,
                        path_index, sources, source_at,
                    )
                    result["verified_fields"].append({
                        **item,
                        "source_expression": expression,
                        "mapping_file": mapping_path,
                        "mapping_line": _line(mapping_source, mapping_start + setter.start()),
                        "evidence_level": "returned_object_assignment",
                        **upstream,
                    })
            if not fields:
                result["warnings"].append(f"{dto_path}: 未解析出可见输出字段")

    for path, source in sources.items():
        if not path.endswith(".xml"):
            continue
        result_maps: dict[str, list[dict]] = {}
        for mapping in re.finditer(r"<resultMap\b([^>]*)>(.*?)</resultMap\s*>", source, re.S | re.I):
            map_id = _attribute(mapping.group(1), "id")
            if not map_id:
                continue
            pairs = []
            for node in re.finditer(r"<(?:id|result)\b([^>]*)/?>", mapping.group(2), re.I):
                column = _attribute(node.group(1), "column")
                prop = _attribute(node.group(1), "property")
                if column and prop:
                    pairs.append({
                        "column": column, "field": prop, "file": path,
                        "line": _line(source, mapping.start(2) + node.start()),
                    })
            result_maps[map_id] = pairs
        for query in re.finditer(r"<select\b([^>]*)>(.*?)</select\s*>", source, re.S | re.I):
            query_id = _attribute(query.group(1), "id")
            if query_id:
                result["sql_results"].append({
                    "id": query_id,
                    "file": path,
                    "line": _line(source, query.start()),
                    "field_mappings": result_maps.get(_attribute(query.group(1), "resultMap"), []),
                    "status": "partial",
                })
    return result
