"""Single safe Git process contract for testcase-generator analysis stages."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Sequence


FORBIDDEN_COMMANDS = {
    "push", "pull", "merge", "rebase", "reset", "checkout", "switch",
    "branch", "clean", "commit", "tag", "worktree", "clone", "init",
    "remote",
}


class GitRunnerError(RuntimeError):
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
    timeout: int = 60,
    allow_fetch: bool = False,
    text: bool = True,
    input_data: str | bytes | None = None,
    executor: Callable[..., Any] = subprocess.run,
) -> GitResult:
    """Run Git using an argument list; only explicit targeted fetch may mutate refs."""
    if isinstance(args, (str, bytes)):
        raise TypeError("Git arguments must be a sequence, not a shell command string")
    values = [str(arg) for arg in args]
    if not values:
        raise ValueError("Git arguments cannot be empty")
    verb = values[0].casefold()
    command = ["git", "-C", os.fspath(repo_path), *values]
    if verb in FORBIDDEN_COMMANDS or (verb == "fetch" and not allow_fetch):
        raise GitCommandRejected(
            f"testcase-generator 禁止未授权 Git 写操作: {verb}", command=command
        )
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("GIT_OPTIONAL_LOCKS", "0")
    kwargs = {
        "shell": False,
        "capture_output": True,
        "text": text,
        "timeout": timeout,
        "env": env,
    }
    if text:
        kwargs.update(encoding="utf-8", errors="replace")
    if input_data is not None:
        kwargs["input"] = input_data
    try:
        completed = executor(command, **kwargs)
    except subprocess.TimeoutExpired as exc:
        return GitResult(command, -1, "", f"Git command timed out after {timeout}s: {exc}", True)
    except OSError as exc:
        return GitResult(command, -1, "", str(exc), False)
    stdout = completed.stdout or ("" if text else b"")
    stderr = completed.stderr or ("" if text else b"")
    if not text:
        stdout = stdout.decode("utf-8", errors="replace")
        stderr = stderr.decode("utf-8", errors="replace")
    return GitResult(
        command,
        int(completed.returncode),
        str(stdout),
        str(stderr),
        False,
    )


def stable_patch_id(
    repo_path: str | os.PathLike[str],
    producer_args: Sequence[str],
    *,
    timeout: int = 90,
    executor: Callable[..., Any] = subprocess.run,
) -> str:
    """Calculate stable patch-id without a shell pipeline."""
    produced = run_git(
        repo_path, producer_args, timeout=timeout, text=False, executor=executor
    )
    if produced.returncode != 0:
        return ""
    consumed = run_git(
        repo_path,
        ["patch-id", "--stable"],
        timeout=timeout,
        input_data=produced.stdout,
        executor=executor,
    )
    if consumed.returncode != 0:
        return ""
    value = consumed.stdout.strip()
    return value.split()[0] if value else ""
