"""Stable, path-verified adapters for sibling Codex skills.

Sibling skills are deliberately not installed as one Python package.  This
module is the only testcase-generator boundary allowed to load their public
Python entrypoints or resolve their CLI files.
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
import importlib.util
from pathlib import Path
import re
import sys
from threading import RLock
from typing import Any


SKILLS_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_ENTRYPOINTS = {
    "requirement-code-auditor": {
        "module": "scripts/workflow_api.py",
        "symbol": "extract_business_logic",
        "symbols": ("extract_business_logic", "compare_structured"),
        "package": True,
    },
    "doc-to-md": {"module": "doc_to_md.py", "symbol": "convert_doc_to_md"},
    "xmind-generator": {
        "module": "xmind_utils.py",
        "symbols": ("gen_id", "make_node", "create_xmind_file"),
    },
}

CONTRACT_COMPONENT = {
    "module": "validate_contracts.py",
    "symbols": ("load_manifest", "validate_payload", "validate_artifact_file"),
}

_LOCK = RLock()
_MODULE_CACHE: dict[tuple[str, str], Any] = {}


class CrossSkillDependencyError(ImportError):
    """A declared sibling skill entrypoint is missing or invalid."""

    error_code = "cross_skill_dependency_failed"
    exit_code = 4


def _skill_root(skill_name: str) -> Path:
    if skill_name not in PUBLIC_ENTRYPOINTS:
        raise CrossSkillDependencyError(f"未声明的跨技能依赖: {skill_name}")
    root = (SKILLS_ROOT / skill_name).resolve()
    if root.parent != SKILLS_ROOT.resolve() or not root.is_dir():
        raise CrossSkillDependencyError(f"技能目录不存在或越界: {skill_name}")
    return root


def get_skill_file(skill_name: str, relative_path: str) -> Path:
    """Resolve a sibling skill file and prevent path traversal."""
    root = _skill_root(skill_name)
    path = (root / relative_path).resolve()
    if root not in path.parents or not path.is_file():
        raise CrossSkillDependencyError(
            f"技能文件不存在或越界: {skill_name}/{relative_path}"
        )
    return path


def get_contract_file(relative_path: str) -> Path:
    """Resolve a central contract file without exposing the skills root."""
    root = (SKILLS_ROOT / "contracts").resolve()
    path = (root / relative_path).resolve()
    if root not in path.parents or not path.is_file():
        raise CrossSkillDependencyError(f"契约文件不存在或越界: {relative_path}")
    return path


@contextmanager
def _scoped_import_path(path: Path):
    """Expose sibling imports only while executing one verified module."""
    value = str(path)
    sys.path.insert(0, value)
    try:
        yield
    finally:
        try:
            sys.path.remove(value)
        except ValueError:
            pass


def _load_module(skill_name: str, module_file: str):
    key = (skill_name, module_file)
    with _LOCK:
        if key in _MODULE_CACHE:
            return _MODULE_CACHE[key]
        module_path = (
            get_contract_file(module_file)
            if skill_name == "skill-contracts"
            else get_skill_file(skill_name, module_file)
        )
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", f"{skill_name}_{module_path.stem}")
        module_name = f"_codex_sibling_{safe_name}"
        # Auditor components use relative imports.  Give this entrypoint a
        # unique package namespace rooted at its verified scripts directory,
        # so generic names such as scripts/analyzer never enter sys.path.
        package_mode = bool(PUBLIC_ENTRYPOINTS.get(skill_name, {}).get("package"))
        spec = importlib.util.spec_from_file_location(
            module_name, module_path,
            submodule_search_locations=[str(module_path.parent)] if package_mode else None,
        )
        if spec is None or spec.loader is None:
            raise CrossSkillDependencyError(f"无法创建模块加载器: {module_path}")
        module = importlib.util.module_from_spec(spec)
        modules_before = set(sys.modules)
        sys.modules[module_name] = module
        try:
            import_scope = nullcontext() if package_mode else _scoped_import_path(module_path.parent)
            with import_scope:
                spec.loader.exec_module(module)
        except Exception as exc:
            # Remove partially imported children too; a retry must not reuse
            # components from a failed package initialization.
            for name in tuple(sys.modules):
                if name not in modules_before and (
                    name == module_name or name.startswith(module_name + ".")
                ):
                    sys.modules.pop(name, None)
            raise CrossSkillDependencyError(
                f"加载跨技能入口失败: {skill_name}/{module_file}: {exc}"
            ) from exc
        _MODULE_CACHE[key] = module
        return module


def load_public_symbol(skill_name: str, symbol: str | None = None):
    contract = PUBLIC_ENTRYPOINTS.get(skill_name)
    if not contract:
        raise CrossSkillDependencyError(f"未声明的跨技能依赖: {skill_name}")
    target = symbol or contract.get("symbol")
    allowed = {contract.get("symbol"), *contract.get("symbols", ())}
    allowed.discard(None)
    if target not in allowed:
        raise CrossSkillDependencyError(f"未公开的跨技能符号: {skill_name}.{target}")
    module = _load_module(skill_name, contract["module"])
    value = getattr(module, target, None)
    if not callable(value):
        raise CrossSkillDependencyError(f"跨技能公开入口不可调用: {skill_name}.{target}")
    return value


def extract_business_logic(*args, **kwargs):
    return load_public_symbol("requirement-code-auditor", "extract_business_logic")(*args, **kwargs)


def convert_doc_to_md(*args, **kwargs):
    return load_public_symbol("doc-to-md")(*args, **kwargs)


def compare_structured(*args, **kwargs):
    return load_public_symbol("requirement-code-auditor", "compare_structured")(*args, **kwargs)


def gid(*args, **kwargs):
    return load_public_symbol("xmind-generator", "gen_id")(*args, **kwargs)


def make_node(*args, **kwargs):
    return load_public_symbol("xmind-generator", "make_node")(*args, **kwargs)


def create_xmind_file(*args, **kwargs):
    return load_public_symbol("xmind-generator", "create_xmind_file")(*args, **kwargs)

def load_contract_symbol(symbol: str):
    """Load one allow-listed central contract validator function."""
    if symbol not in CONTRACT_COMPONENT["symbols"]:
        raise CrossSkillDependencyError(f"未公开的契约符号: {symbol}")
    module = _load_module("skill-contracts", CONTRACT_COMPONENT["module"])
    value = getattr(module, symbol, None)
    if not callable(value):
        raise CrossSkillDependencyError(f"契约入口不可调用: {symbol}")
    return value


def validate_contract_payload(artifact_name: str, payload):
    manifest = load_contract_symbol("load_manifest")()
    return load_contract_symbol("validate_payload")(manifest, artifact_name, payload)
