"""Inventory Markdown inputs, normalize the current requirement and parse release-test targets."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


GENERATED_NAMES = {"需求实现审计报告.md", "代码分析报告.md", "需求分析.md", "代码评审报告.md", "详细测试点.md"}
EXCLUDED_PARTS = {"测试分析", ".ai-internal", ".ai-archive"}
BRANCH_TOKEN = re.compile(
    r"(?:origin[\\/])?(?:feat|feature|bugfix|hotfix|release|dev|test|uat|master)(?:[A-Za-z0-9_.\\/\-]*)",
    re.IGNORECASE,
)


def iter_source_markdown(requirement_dir: Path) -> Iterable[Path]:
    for path in sorted(requirement_dir.rglob("*.md")):
        if any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        if path.name in GENERATED_NAMES or path.name.endswith("_评审报告.md"):
            continue
        yield path


def classify_document(path: Path, content: str) -> str:
    name = path.name.casefold()
    parent = path.parent.name.casefold()
    if "提测" in name or "提测" in parent or "prj-" in name:
        return "release_test"
    if "设计" in name or "设计" in parent or "方案" in name:
        return "design"
    if "接口" in name or "接口" in parent:
        return "interface"
    if "需求" in name or "需求" in parent or any(token in content[:2000] for token in ("需求背景", "需求描述")):
        return "requirement"
    return "requirement_supplement"


def extract_latest_requirement(content: str) -> str:
    for pattern in (
        r"===+\s*分割线.*?===+\s*",
        r"---+\s*分割线.*?---+\s*",
        r"===+\s*$",
    ):
        match = re.search(pattern, content, re.IGNORECASE | re.MULTILINE)
        if match:
            return content[: match.start()].rstrip()
    return content.rstrip()


def extract_branches(content: str) -> List[str]:
    regions = []
    for pattern in (r"(?:涉及开发分支|开发分支)[^\n]{0,240}", r"(?:^|\n)[^\n]*分支[^\n]*"):
        regions.extend(match.group(0) for match in re.finditer(pattern, content, re.IGNORECASE))
    candidates = []
    for region in regions:
        for match in BRANCH_TOKEN.findall(region):
            value = match.replace("\\_", "_").replace("\\/", "/").strip("` ，,；;")
            if value and value.casefold() not in {item.casefold() for item in candidates}:
                candidates.append(value)
    return candidates


def extract_services(content: str) -> List[str]:
    for pattern in (r"涉及服务\s*[：:]\s*([^\n]+)", r"服务名称\s*[：:]\s*([^\n]+)"):
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            value = re.sub(r"[`*_>#]", "", match.group(1))
            value = re.split(r"[；;。]", value, maxsplit=1)[0]
            return [item.strip() for item in re.split(r"[、,，/\n]+", value) if item.strip()]
    return []


def extract_repo_keys(content: str) -> List[str]:
    """Extract explicit repository identifiers from a release document."""
    candidates: List[str] = []
    pattern = r"(?:repo[_ ]?key|repository|代码仓库|仓库名称|目标仓库|仓库)\s*[：:]\s*([^\n]+)"
    for match in re.finditer(pattern, content, re.IGNORECASE):
        value = re.sub(r"[`*_>#]", "", match.group(1))
        value = re.split(r"[；;。]", value, maxsplit=1)[0]
        for item in re.split(r"[、,，/\s]+", value):
            item = item.strip("` ，,；;()（）")
            if item and item.casefold() not in {candidate.casefold() for candidate in candidates}:
                candidates.append(item)
    return candidates

def infer_language(path: Path, content: str) -> str:
    sample = f"{path.name}\n{content[:4000]}".casefold()
    if "python" in sample:
        return "python"
    if any(token in sample for token in ("java", "spring boot", "mybatis")):
        return "java"
    return ""


def ingest_requirement_directory(requirement_dir: str | Path) -> Tuple[List[Dict[str, Any]], str, List[Dict[str, Any]]]:
    root = Path(requirement_dir).expanduser().resolve()
    inventory: List[Dict[str, Any]] = []
    normalized_sections: List[str] = []
    release_docs: List[Dict[str, Any]] = []
    for path in iter_source_markdown(root):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            inventory.append({"relative_path": str(path.relative_to(root)), "read_error": str(exc), "participates_requirement": False, "participates_target_resolution": False})
            continue
        doc_type = classify_document(path, content)
        relative = str(path.relative_to(root))
        record = {
            "relative_path": relative,
            "document_type": doc_type,
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "participates_requirement": doc_type != "release_test",
            "participates_target_resolution": doc_type == "release_test",
        }
        inventory.append(record)
        if record["participates_requirement"]:
            normalized_sections.append(f"\n=== {relative} ===\n{extract_latest_requirement(content)}")
        if record["participates_target_resolution"]:
            release_docs.append({"path": path, "relative_path": relative, "content": content, "sha256": record["sha256"]})
    return inventory, "\n".join(normalized_sections).strip(), release_docs
