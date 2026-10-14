#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compatibility export layer for the enhanced analysis workflow."""

import workflow_analysis_legacy as _legacy
import workflow_analysis_facade as _facade

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

from skill_errors import AnalysisError, SkillError, normalize_error

_canonical_run_requirement_analysis = run_requirement_analysis


def run_requirement_analysis(*args, **kwargs):
    """Normalize unknown failures at the analysis-stage ownership boundary."""
    try:
        return _canonical_run_requirement_analysis(*args, **kwargs)
    except SkillError:
        raise
    except Exception as exc:
        mapped = normalize_error(exc)
        if mapped.error_code != "unexpected_error":
            raise mapped from exc
        raise AnalysisError(
            str(exc) or type(exc).__name__,
            stage="analysis",
            context={"original_type": type(exc).__name__},
        ) from exc

# Tests and instrumentation use ``__module__`` to enforce stage ownership.
run_requirement_analysis.__module__ = __name__

__all__ = sorted(
    set(getattr(_legacy, "__all__", ()))
    | set(getattr(_facade, "__all__", ()))
)
