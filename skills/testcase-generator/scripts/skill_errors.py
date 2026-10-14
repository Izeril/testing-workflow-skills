"""Minimal stable error and CLI exit-code contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any


class SkillError(RuntimeError):
    error_code = "skill_error"
    exit_code = 1

    def __init__(self, message: str, *, stage: str = "", context: dict[str, Any] | None = None):
        super().__init__(message)
        self.stage = stage
        self.context = dict(context or {})

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "error_code": self.error_code,
            "exit_code": self.exit_code,
            "error_type": type(self).__name__,
            "stage": self.stage,
            "message": str(self),
            "context": self.context,
        }


class InputError(SkillError):
    error_code = "invalid_input"
    exit_code = 2


class GateError(SkillError):
    error_code = "gate_blocked"
    exit_code = 3


class AnalysisError(SkillError):
    error_code = "analysis_failed"
    exit_code = 5


class ConversionError(SkillError):
    error_code = "conversion_failed"
    exit_code = 6


class ExternalDependencyError(SkillError):
    error_code = "external_dependency_failed"
    exit_code = 4


class UnexpectedSkillError(SkillError):
    error_code = "unexpected_error"
    exit_code = 1


def normalize_error(exc: BaseException) -> SkillError:
    if isinstance(exc, SkillError):
        return exc

    foreign_code = str(getattr(exc, "error_code", "") or "")
    foreign_context = dict(getattr(exc, "context", {}) or {})
    foreign_context.update({
        "original_type": type(exc).__name__,
        "original_error_code": foreign_code,
    })
    stage = str(getattr(exc, "stage", "") or "")
    error_types = {
        "invalid_input": InputError,
        "gate_blocked": GateError,
        "analysis_failed": AnalysisError,
        "conversion_failed": ConversionError,
        "external_dependency_failed": ExternalDependencyError,
        "cross_skill_dependency_failed": ExternalDependencyError,
    }
    mapped = error_types.get(foreign_code)
    if mapped:
        return mapped(str(exc) or type(exc).__name__, stage=stage, context=foreign_context)
    return UnexpectedSkillError(str(exc) or type(exc).__name__, context=foreign_context)
