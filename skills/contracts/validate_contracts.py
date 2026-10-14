"""Validate active workflow entrypoints and artifact contracts; archive metadata is not executable."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import sys


CONTRACT_DIR = Path(__file__).resolve().parent
SKILLS_ROOT = CONTRACT_DIR.parent
DEFAULT_MANIFEST = CONTRACT_DIR / "skill_contracts.json"
EXPECTED_SKILLS = {
    "doc-to-md",
    "requirement-code-auditor",
    "testcase-generator",
    "xmind-generator",
}
ARCHIVED_SKILLS = {"code-analyzer", "req-code-comparator"}
REQUIRED_WORKFLOW_SKILLS = EXPECTED_SKILLS
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
SCHEMA_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}


class ContractValidationError(ValueError):
    error_code = "contract_validation_failed"
    exit_code = 3


def load_manifest(path=DEFAULT_MANIFEST):
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _defined_symbols(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    symbols = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            symbols.update(target.id for target in targets if isinstance(target, ast.Name))
    return symbols


def _resolve_skill_file(relative_path: str):
    path = (SKILLS_ROOT / relative_path).resolve()
    if SKILLS_ROOT.resolve() not in path.parents or not path.is_file():
        raise ContractValidationError(f"契约文件不存在或越界: {relative_path}")
    return path


def _resolve_schema_ref(reference: str):
    if not isinstance(reference, str) or not reference:
        raise ContractValidationError("schema_ref 必须是非空字符串")
    relative_path, separator, pointer = reference.partition("#")
    path = (CONTRACT_DIR / relative_path).resolve()
    if CONTRACT_DIR.resolve() not in path.parents or not path.is_file():
        raise ContractValidationError(f"schema_ref 文件不存在或越界: {reference}")
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if separator:
        if pointer and not pointer.startswith("/"):
            raise ContractValidationError(f"schema_ref JSON Pointer 非法: {reference}")
        for raw_part in pointer.split("/")[1:] if pointer else []:
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if not isinstance(value, dict) or part not in value:
                raise ContractValidationError(f"schema_ref 指针不存在: {reference}")
            value = value[part]
    if not isinstance(value, dict):
        raise ContractValidationError(f"schema_ref 必须指向对象: {reference}")
    return value


def _schema_types(schema):
    expected = schema.get("type")
    if expected is None:
        return []
    values = expected if isinstance(expected, list) else [expected]
    if not values or any(value not in SCHEMA_TYPES for value in values):
        raise ContractValidationError(f"schema type 非法: {expected}")
    return values


def _validate_schema_definition(schema, path="$schema"):
    if not isinstance(schema, dict):
        raise ContractValidationError(f"{path} 必须是对象")
    types = _schema_types(schema)
    required = schema.get("required", [])
    if not isinstance(required, list) or any(not isinstance(item, str) or not item for item in required):
        raise ContractValidationError(f"{path}.required 必须是非空字段名列表")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise ContractValidationError(f"{path}.properties 必须是对象")
    if required and types and "object" not in types:
        raise ContractValidationError(f"{path}.required 只能用于 object")
    for name, child in properties.items():
        _validate_schema_definition(child, f"{path}.properties.{name}")
    if "items" in schema:
        if types and "array" not in types:
            raise ContractValidationError(f"{path}.items 只能用于 array")
        _validate_schema_definition(schema["items"], f"{path}.items")
    if "unique_by" in schema and (types and "array" not in types):
        raise ContractValidationError(f"{path}.unique_by 只能用于 array")
    if "enum" in schema and (not isinstance(schema["enum"], list) or not schema["enum"]):
        raise ContractValidationError(f"{path}.enum 不能为空")
    if "pattern" in schema:
        if not isinstance(schema["pattern"], str):
            raise ContractValidationError(f"{path}.pattern 必须是字符串")
        try:
            re.compile(schema["pattern"])
        except re.error as exc:
            raise ContractValidationError(f"{path}.pattern 非法: {exc}") from exc
    return True


def _matches_type(value, expected):
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _validate_schema_value(value, schema, path="$"):
    expected_types = _schema_types(schema)
    if expected_types and not any(_matches_type(value, expected) for expected in expected_types):
        raise ContractValidationError(
            f"{path} 类型错误: 期望 {'/'.join(expected_types)}，实际 {type(value).__name__}"
        )
    if "const" in schema and value != schema["const"]:
        raise ContractValidationError(f"{path} 期望固定值 {schema['const']!r}，实际 {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise ContractValidationError(f"{path} 取值 {value!r} 不在允许范围 {schema['enum']}")
    if isinstance(value, str) and len(value) < int(schema.get("min_length", 0)):
        raise ContractValidationError(f"{path} 长度小于 {schema['min_length']}")
    if isinstance(value, str) and schema.get("pattern") and not re.fullmatch(schema["pattern"], value):
        raise ContractValidationError(f"{path} 不匹配格式 {schema['pattern']}")
    if isinstance(value, dict):
        missing = [name for name in schema.get("required", []) if name not in value]
        if missing:
            raise ContractValidationError(f"{path} 缺少字段: {', '.join(missing)}")
        for name, child_schema in schema.get("properties", {}).items():
            if name in value:
                _validate_schema_value(value[name], child_schema, f"{path}.{name}")
    if isinstance(value, list):
        if len(value) < int(schema.get("min_items", 0)):
            raise ContractValidationError(f"{path} 条目数小于 {schema['min_items']}")
        child_schema = schema.get("items")
        if child_schema:
            for index, item in enumerate(value):
                _validate_schema_value(item, child_schema, f"{path}[{index}]")
        unique_by = schema.get("unique_by")
        if unique_by:
            seen = set()
            for index, item in enumerate(value):
                if not isinstance(item, dict) or unique_by not in item:
                    continue
                marker = json.dumps(item[unique_by], ensure_ascii=False, sort_keys=True)
                if marker in seen:
                    raise ContractValidationError(
                        f"{path}[{index}].{unique_by} 重复: {item[unique_by]!r}"
                    )
                seen.add(marker)
    return True


def validate_manifest(manifest):
    if not SEMVER.fullmatch(str(manifest.get("schema_version", ""))):
        raise ContractValidationError("skill_contracts.schema_version 必须是语义版本")
    skills = manifest.get("skills")
    if not isinstance(skills, dict) or set(skills) != EXPECTED_SKILLS:
        raise ContractValidationError("skill_contracts.skills 必须且只能声明四个活动工作流技能")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ContractValidationError("skill_contracts.artifacts 不能为空")

    if set(manifest.get("workflow_skills", REQUIRED_WORKFLOW_SKILLS)) != REQUIRED_WORKFLOW_SKILLS:
        raise ContractValidationError("workflow_skills 必须声明四个当前工作流技能")
    archives = manifest.get("archived_skills", {})
    if not isinstance(archives, dict) or set(archives) - ARCHIVED_SKILLS:
        raise ContractValidationError("archived_skills 只能登记已归档的两个旧技能")
    for skill_name, metadata in archives.items():
        if not isinstance(metadata, dict) or any(key in metadata for key in ("public_entrypoints", "cli")):
            raise ContractValidationError(f"{skill_name} 归档元数据不得包含可调用入口")
        if metadata.get("lifecycle") != "archived" or metadata.get("runtime_callable") is not False:
            raise ContractValidationError(f"{skill_name} 必须标记为不可调用的归档技能")
        if metadata.get("archive_path") != f"../skills-archive/{skill_name}" or metadata.get("replacement_skill") != "requirement-code-auditor":
            raise ContractValidationError(f"{skill_name} 归档位置或替代技能非法")
    entrypoint_count = 0
    for skill_name, contract in skills.items():
        entrypoints = contract.get("public_entrypoints")
        if not isinstance(entrypoints, list) or not entrypoints:
            raise ContractValidationError(f"{skill_name} 缺少 public_entrypoints")
        _resolve_skill_file(contract.get("cli", ""))
        output_artifact = contract.get("output_artifact")
        if output_artifact not in artifacts:
            raise ContractValidationError(f"{skill_name} 引用了未知产物契约: {output_artifact}")
        for entrypoint in entrypoints:
            module_path = _resolve_skill_file(entrypoint.get("path", ""))
            symbol = entrypoint.get("symbol", "")
            if symbol not in _defined_symbols(module_path):
                raise ContractValidationError(
                    f"公开入口不存在: {entrypoint.get('path')}::{symbol}"
                )
            entrypoint_count += 1

    nested_schema_count = 0
    for artifact_name, contract in artifacts.items():
        if not SEMVER.fullmatch(str(contract.get("contract_version", ""))):
            raise ContractValidationError(f"{artifact_name}.contract_version 非法")
        if contract.get("producer") not in EXPECTED_SKILLS:
            raise ContractValidationError(f"{artifact_name}.producer 未声明")
        required = (
            contract.get("required_keys")
            or contract.get("required_entries")
            or contract.get("required_properties")
        )
        if not isinstance(required, list) or not required:
            raise ContractValidationError(f"{artifact_name} 缺少必需字段/条目")
        if contract.get("schema_ref"):
            _validate_schema_definition(
                _resolve_schema_ref(contract["schema_ref"]),
                f"artifacts.{artifact_name}.schema",
            )
            nested_schema_count += 1
    return {
        "schema_version": "1.0.0",
        "status": "passed",
        "skill_count": len(skills),
        "entrypoint_count": entrypoint_count,
        "workflow_skill_count": len(REQUIRED_WORKFLOW_SKILLS),
        "skipped_compatibility_skills": [],
        "archived_skill_count": len(archives),
        "artifact_count": len(artifacts),
        "nested_schema_count": nested_schema_count,
    }


def validate_payload(manifest, artifact_name, payload):
    contract = manifest.get("artifacts", {}).get(artifact_name)
    if not contract:
        raise ContractValidationError(f"未知产物契约: {artifact_name}")
    if contract.get("format") != "json-object":
        raise ContractValidationError(f"{artifact_name} 不是 JSON 对象契约")
    if not isinstance(payload, dict):
        raise ContractValidationError(f"{artifact_name} 必须是 JSON 对象")
    missing = [key for key in contract.get("required_keys", []) if key not in payload]
    if missing:
        raise ContractValidationError(f"{artifact_name} 缺少字段: {', '.join(missing)}")
    expected_version = contract.get("schema_version")
    if expected_version and payload.get("schema_version") != expected_version:
        raise ContractValidationError(
            f"{artifact_name}.schema_version 期望 {expected_version}，实际 {payload.get('schema_version')}"
        )
    if contract.get("schema_ref"):
        _validate_schema_value(payload, _resolve_schema_ref(contract["schema_ref"]))
    return True


def validate_artifact_file(manifest, artifact_name, path):
    artifact_path = Path(path)
    if not artifact_path.is_file():
        raise ContractValidationError(f"产物文件不存在: {artifact_path}")
    try:
        with artifact_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"产物 JSON 读取失败: {artifact_path}: {exc}") from exc
    validate_payload(manifest, artifact_name, payload)
    return {"artifact": artifact_name, "path": str(artifact_path), "status": "passed"}


def configure_utf8_stdio():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv=None):
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="验证活动工作流入口、归档元数据与产物契约")
    parser.add_argument("manifest", nargs="?", default=str(DEFAULT_MANIFEST))
    parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="验证一个 JSON 产物，可重复指定",
    )
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        report = validate_manifest(manifest)
        payload_reports = []
        for value in args.artifact:
            if "=" not in value:
                raise ContractValidationError("--artifact 必须使用 NAME=PATH")
            artifact_name, artifact_path = value.split("=", 1)
            payload_reports.append(
                validate_artifact_file(manifest, artifact_name.strip(), artifact_path)
            )
        report["payload_count"] = len(payload_reports)
        if payload_reports:
            report["payloads"] = payload_reports
    except Exception as exc:
        print(json.dumps({
            "schema_version": "1.0.0",
            "status": "failed",
            "error_code": getattr(exc, "error_code", "contract_validation_failed"),
            "message": str(exc),
        }, ensure_ascii=False))
        return int(getattr(exc, "exit_code", 3))
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
