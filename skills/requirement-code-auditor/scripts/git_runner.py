"""Single safe Git process contract for code-analyzer."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Sequence


FORBIDDEN_COMMANDS = {
    "push", "pull", "merge", "rebase", "reset", "checkout", "switch",
    "branch", "clean", "commit", "tag", "worktree", "clone", "init",
    "remote", "fetch",
}


class GitRunnerError(RuntimeError):
    """Base error carrying stable command evidence."""

    code = "git_runner_error"

    def __init__(self, message: str, *, command=None, returncode=-1, stderr=""):
        super().__init__(message)
        self.command = list(command or [])
        self.returncode = int(returncode)
        self.stderr = str(stderr or "")


class GitCommandRejected(GitRunnerError):
    code = "git_command_rejected"


@dataclass(frozen=True)
class GitResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def run_git(
    repo_path: str | os.PathLike[str],
    args: Sequence[str],
    *,
    timeout: int = 30,
    executor: Callable[..., Any] = subprocess.run,
) -> GitResult:
    """Run a read-only Git command without a shell or credential prompt."""
    if isinstance(args, (str, bytes)):
        raise TypeError("Git arguments must be a sequence, not a shell command string")
    values = [str(arg) for arg in args]
    if not values:
        raise ValueError("Git arguments cannot be empty")
    verb = values[0].casefold()
    command = ["git", "-C", os.fspath(repo_path), *values]
    if verb in FORBIDDEN_COMMANDS:
        raise GitCommandRejected(
            f"code-analyzer 禁止 Git 写操作: {verb}", command=command
        )
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("GIT_OPTIONAL_LOCKS", "0")
    try:
        completed = executor(
            command,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return GitResult(command, -1, "", f"Git command timed out after {timeout}s: {exc}", True)
    except OSError as exc:
        return GitResult(command, -1, "", str(exc), False)
    return GitResult(
        command,
        int(completed.returncode),
        str(completed.stdout or ""),
        str(completed.stderr or ""),
        False,
    )
