#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Structured JSONL logging with a backwards-compatible console mirror."""

from __future__ import annotations

import builtins
import json
import logging
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Optional


_LOGGER = logging.getLogger("testcase-generator")
_LOGGER.setLevel(logging.DEBUG)
_LOGGER.propagate = False
_CONTEXT = {"run_id": "", "stage": "", "stage_started_at": None, "log_file": ""}
_LOCK = threading.RLock()


class _JsonLineFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "run_id": getattr(record, "run_id", ""),
            "stage": getattr(record, "stage", ""),
            "event": getattr(record, "event", "message"),
            "message": record.getMessage(),
        }
        context = getattr(record, "context", None)
        if isinstance(context, dict):
            payload.update(context)
        return json.dumps(payload, ensure_ascii=False, default=str)


class _JsonLineHandler(logging.Handler):
    """Append one record per open/close cycle to avoid persistent Windows locks."""

    def __init__(self, path: Path, max_bytes: int, backup_count: int):
        super().__init__()
        self.path = path
        self.max_bytes = max_bytes
        self.backup_count = backup_count

    def _rotate(self) -> None:
        if not self.path.exists() or self.path.stat().st_size < self.max_bytes:
            return
        for index in range(self.backup_count, 0, -1):
            source = self.path if index == 1 else self.path.with_name(f"{self.path.name}.{index - 1}")
            target = self.path.with_name(f"{self.path.name}.{index}")
            if source.exists():
                if target.exists():
                    target.unlink()
                source.replace(target)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._rotate()
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(self.format(record) + "\n")
        except Exception:
            self.handleError(record)

def configure_structured_logging(
    output_dir: Path | str,
    run_id: str,
    relative_path: str = ".ai-internal/workflow_log.jsonl",
) -> Path:
    """Configure a rotating JSONL log under the workflow internal directory."""
    log_path = Path(output_dir) / relative_path
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        current = Path(_CONTEXT["log_file"]) if _CONTEXT.get("log_file") else None
        _CONTEXT.update(run_id=run_id, log_file=str(log_path))
        if current == log_path and any(
            isinstance(handler, _JsonLineHandler) for handler in _LOGGER.handlers
        ):
            return log_path
        for handler in list(_LOGGER.handlers):
            _LOGGER.removeHandler(handler)
            handler.close()
        handler = _JsonLineHandler(log_path, max_bytes=5 * 1024 * 1024, backup_count=3)
        handler.setFormatter(_JsonLineFormatter())
        _LOGGER.addHandler(handler)
    return log_path


def set_log_stage(stage: str, started_at: Optional[float] = None) -> None:
    with _LOCK:
        _CONTEXT["stage"] = stage
        _CONTEXT["stage_started_at"] = started_at
    log_event("INFO", f"阶段开始: {stage}", event="stage_started")


def clear_log_stage(stage: str = "") -> None:
    with _LOCK:
        if not stage or _CONTEXT.get("stage") == stage:
            _CONTEXT["stage"] = ""
            _CONTEXT["stage_started_at"] = None


def log_event(level: str, message: str, event: str = "message", **context: Any) -> None:
    """Write one structured event when a workflow log has been configured."""
    with _LOCK:
        if not _LOGGER.handlers:
            return
        extra = {
            "run_id": _CONTEXT.get("run_id", ""),
            "stage": context.pop("stage", None) or _CONTEXT.get("stage", ""),
            "event": event,
            "context": context,
        }
        _LOGGER.log(getattr(logging, str(level).upper(), logging.INFO), str(message), extra=extra)


def _infer_level(message: str) -> str:
    if any(token in message for token in ("❌", "失败", "错误", "异常")):
        return "ERROR"
    if any(token in message for token in ("⚠", "警告", "未找到", "跳过")):
        return "WARNING"
    return "INFO"


def log_console(*values: Any, **kwargs: Any) -> None:
    """Mirror print-compatible output to console and the structured JSONL log."""
    explicit_level = kwargs.pop("level", None)
    sep = kwargs.get("sep", " ")
    end = kwargs.get("end", "\n")
    destination = kwargs.get("file")
    builtins.print(*values, **kwargs)
    if destination not in (None, sys.stdout, sys.stderr):
        return
    message = sep.join(str(value) for value in values)
    if end and end != "\n":
        message += end
    if message.strip():
        log_event(explicit_level or _infer_level(message), message, event="console")


def get_structured_log_path() -> Optional[Path]:
    value = _CONTEXT.get("log_file")
    return Path(value) if value else None

def shutdown_structured_logging() -> None:
    """Close log handlers so embedded callers can release Windows file handles."""
    with _LOCK:
        for handler in list(_LOGGER.handlers):
            _LOGGER.removeHandler(handler)
            handler.close()
        _CONTEXT.update(stage="", stage_started_at=None, log_file="")
