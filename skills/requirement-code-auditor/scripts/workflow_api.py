"""Pure workflow component entry points backed by the auditor engines.

These compatibility functions preserve the original workflow signatures and
payloads. They do not run directory ingestion, choose a remote provider, or
write audit artifacts; the calling workflow owns its admission and lifecycle.
"""

from __future__ import annotations

from .analyzer.business_extractor import extract_business_logic as _extract_business_logic
from .comparator import compare_structured as _compare_structured


def extract_business_logic(*args, **kwargs):
    """Analyze the caller's explicit Git snapshot with the canonical engine."""
    return _extract_business_logic(*args, **kwargs)


def compare_structured(*args, **kwargs):
    """Compare the caller's structured facts with the canonical engine."""
    return _compare_structured(*args, **kwargs)


__all__ = ["extract_business_logic", "compare_structured"]
