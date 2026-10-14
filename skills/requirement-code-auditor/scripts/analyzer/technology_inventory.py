"""Changed-file technology inventory and explicit analysis coverage reporting."""

from collections import Counter
from pathlib import Path
from typing import Any


TECHNOLOGIES = {
    ".java": ("java", "analyzed", "java-syntax+call-chain"),
    ".xml": ("xml/mybatis", "partial", "mybatis-sql-when-detected"),
    ".py": ("python", "analyzed", "python-ast"),
    ".sql": ("sql", "partial", "sql-structure"),
    ".ddl": ("sql", "partial", "sql-structure"),
    ".dml": ("sql", "partial", "sql-structure"),
    ".psql": ("sql", "partial", "sql-structure"),
    ".pls": ("oracle-plsql", "partial", "sql-structure"),
    ".pks": ("oracle-plsql", "partial", "sql-structure"),
    ".pkb": ("oracle-plsql", "partial", "sql-structure"),
    ".js": ("javascript", "recognized_unanalyzed", "inventory-only"),
    ".jsx": ("javascript", "recognized_unanalyzed", "inventory-only"),
    ".ts": ("typescript", "recognized_unanalyzed", "inventory-only"),
    ".tsx": ("typescript", "recognized_unanalyzed", "inventory-only"),
    ".vue": ("vue", "recognized_unanalyzed", "inventory-only"),
    ".kt": ("kotlin", "recognized_unanalyzed", "inventory-only"),
    ".groovy": ("groovy", "recognized_unanalyzed", "inventory-only"),
    ".scala": ("scala", "recognized_unanalyzed", "inventory-only"),
    ".yml": ("configuration", "partial", "config-values"),
    ".yaml": ("configuration", "partial", "config-values"),
    ".properties": ("configuration", "partial", "config-values"),
}

NON_SOURCE_SUFFIXES = {
    ".md", ".txt", ".json", ".csv", ".png", ".jpg", ".jpeg", ".gif", ".svg",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".xmind",
}


def classify_source_file(path: str, content: str | None = None) -> dict[str, Any]:
    suffix = Path(path).suffix.casefold()
    if suffix in TECHNOLOGIES:
        language, status, analyzer = TECHNOLOGIES[suffix]
        if suffix == ".xml":
            text = (content or "").casefold()
            is_mapper = any(token in text for token in ("<select", "<insert", "<update", "<delete", "<mapper"))
            if not is_mapper:
                status, analyzer = "recognized_unanalyzed", "inventory-only"
        warning = ""
        if status == "recognized_unanalyzed":
            warning = f"{path}: recognized as {language}, but no syntax analyzer is configured"
        elif status == "partial":
            warning = f"{path}: {language} analysis is partial ({analyzer})"
        return {
            "file": path,
            "extension": suffix,
            "language": language,
            "status": status,
            "analyzer": analyzer,
            "warning": warning,
        }
    if suffix in NON_SOURCE_SUFFIXES:
        return {
            "file": path,
            "extension": suffix,
            "language": "non-source",
            "status": "not_applicable",
            "analyzer": "inventory-only",
            "warning": "",
        }
    return {
        "file": path,
        "extension": suffix or "(none)",
        "language": "unknown",
        "status": "unsupported",
        "analyzer": "none",
        "warning": f"{path}: unsupported file type; implementation impact was not analyzed",
    }


def mark_unreadable(record: dict[str, Any]) -> dict[str, Any]:
    updated = dict(record)
    updated["status"] = "unreadable"
    updated["warning"] = f"{record['file']}: file content could not be read"
    return updated


def summarize_technology_coverage(records: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(record["status"] for record in records)
    language_counts = Counter(record["language"] for record in records)
    gaps = [
        record for record in records
        if record["status"] in {"partial", "recognized_unanalyzed", "unsupported", "unreadable"}
    ]
    warnings = list(dict.fromkeys(record["warning"] for record in records if record.get("warning")))
    applicable = [record for record in records if record["status"] != "not_applicable"]
    fully_analyzed = [record for record in applicable if record["status"] == "analyzed"]
    return {
        "files": records,
        "total_changed_files": len(records),
        "applicable_source_files": len(applicable),
        "fully_analyzed_files": len(fully_analyzed),
        "complete": bool(applicable) and not gaps,
        "status_counts": dict(sorted(status_counts.items())),
        "language_counts": dict(sorted(language_counts.items())),
        "gaps": gaps,
        "warnings": warnings,
    }
