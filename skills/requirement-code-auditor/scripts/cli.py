"""Command-line entry for the requirement implementation auditor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    skill_root = Path(__file__).resolve().parents[1]
    if str(skill_root) not in sys.path:
        sys.path.insert(0, str(skill_root))
    from scripts.audit_runner import run_requirement_code_audit
    from scripts.skill_errors import AuditorError
else:
    from .audit_runner import run_requirement_code_audit
    from .skill_errors import AuditorError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从需求目录审计开发分支是否完整实现需求")
    parser.add_argument("requirement_dir")
    parser.add_argument("--repo", dest="repo_path")
    parser.add_argument("--branch")
    parser.add_argument("--baseline")
    parser.add_argument("--code-targets")
    parser.add_argument("--config", dest="config_path")
    parser.add_argument("--output", dest="output_dir")
    parser.add_argument("--mode", choices=("audit", "analyze"), default="audit")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        result = run_requirement_code_audit(**vars(args))
    except AuditorError as exc:
        print(json.dumps(exc.to_payload("uncommitted"), ensure_ascii=False, indent=2), file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(json.dumps({"error_code": "unhandled_error", "exit_code": 1, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps({"status": result["status"], "run_id": result["run_id"], "output_dir": result["output_dir"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
