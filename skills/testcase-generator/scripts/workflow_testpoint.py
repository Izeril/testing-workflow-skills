#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility exports for the business-impact-aware testpoint workflow."""

from pathlib import Path

import workflow_testpoint_legacy as _legacy
import workflow_testpoint_facade as _facade

globals().update(
    {
        name: value
        for name, value in vars(_legacy).items()
        if not name.startswith("__")
    }
)
globals().update(
    {
        name: value
        for name, value in vars(_facade).items()
        if not name.startswith("__")
    }
)


def _require_testpoint_inputs(output_dir, code_analysis):
    """Use the enhanced gate when its artifacts exist; preserve old resumes."""
    internal = Path(output_dir) / ".ai-internal"
    enhanced_artifacts = (
        internal / "business_impact.json",
        internal / "business_impact_gate.json",
        internal / "codegraph_query_plan.json",
        internal / "codegraph_impact.json",
        internal / "uat_merge_gate.json",
    )
    if code_analysis or any(path.exists() for path in enhanced_artifacts):
        return _facade._require_testpoint_inputs(output_dir, code_analysis)
    return _legacy._require_testpoint_inputs(output_dir, code_analysis)


run_testpoint_generate = _facade.run_testpoint_generate
run_testpoint_generate.__module__ = __name__

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | set(getattr(_facade, "__all__", ()))
    | {"_require_testpoint_inputs"}
)
