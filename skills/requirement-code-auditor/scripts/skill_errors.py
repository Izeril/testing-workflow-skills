"""Stable workflow errors and CLI exit codes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class AuditorError(RuntimeError):
    error_code: str
    message: str
    exit_code: int = 1
    context: Dict[str, Any] = field(default_factory=dict)
    recoverable: bool = False
    next_action: str = ""

    def __post_init__(self):
        RuntimeError.__init__(self, self.message)

    def to_payload(self, run_id: str) -> Dict[str, Any]:
        return {
            "schema_version": "1.0.0",
            "producer": "requirement-code-auditor",
            "run_id": run_id,
            "error_code": self.error_code,
            "exit_code": self.exit_code,
            "message": self.message,
            "context": self.context,
            "recoverable": self.recoverable,
            "next_action": self.next_action,
        }


def blocked(error_code: str, message: str, **context: Any) -> AuditorError:
    return AuditorError(
        error_code,
        message,
        3,
        context,
        True,
        "修正需求材料、开发分支或仓库映射后重新执行审计",
    )
