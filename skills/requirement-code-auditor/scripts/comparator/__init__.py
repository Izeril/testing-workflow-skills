"""Structured comparison component interfaces."""

from .comparator import CLASSIFICATIONS, DIMENSIONS, compare_structured, format_findings


def compare_from_artifacts(requirement_facts, code_evidence, dimensions=None):
    """Replay component comparison without reading or writing default artifacts."""
    return compare_structured(requirement_facts, code_evidence, dimensions)


__all__ = [
    "CLASSIFICATIONS",
    "DIMENSIONS",
    "compare_structured",
    "compare_from_artifacts",
    "format_findings",
]
