#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Structured comparator facade with stable business dimensions."""

try:
    from . import comparator_core as _core
except ImportError:
    import comparator_core as _core

# Callers must provide stable keys and values for these dimensions.  Adding an
# axis never authorizes the comparator to infer implementation from keywords.
_core.DIMENSIONS.update(
    {
        "actors": ("actors", "users", "roles"),
        "scenarios": ("scenarios", "business_scenarios"),
        "state_transitions": ("state_transitions", "states"),
        "calculations": ("calculations", "formulas"),
        "integrations": ("integrations", "contracts", "external_systems"),
        "data_compatibility": ("data_compatibility", "history_compatibility"),
    }
)

CLASSIFICATIONS = _core.CLASSIFICATIONS
DIMENSIONS = _core.DIMENSIONS
compare_structured = _core.compare_structured
format_findings = _core.format_findings
main = _core.main


if __name__ == "__main__":
    import sys

    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
