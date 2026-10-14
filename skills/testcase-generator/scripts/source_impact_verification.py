"""Validate bounded, source-based impact proofs at a frozen Git commit."""
from __future__ import annotations
import re
from typing import Any
from workflow_git_runner import run_git

def verify_source_impact(plan: dict, supplied: Any) -> dict:
    problems, checked, relations = [], [], []
    if not isinstance(supplied, dict) or not supplied:
        return {"status": "unknown", "reason": "未提供目标 SHA 的关键影响链源码补核", "verified_files": [], "relations": []}
    sha = str(plan.get("target_sha") or "")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha) or supplied.get("target_sha") != sha:
        problems.append("源码补核 SHA 与冻结分析提交不一致")
    evidence = supplied.get("evidence") or []
    paths = supplied.get("critical_paths") or []
    if supplied.get("coverage_status") != "complete" or supplied.get("unresolved_critical"):
        problems.append("关键影响覆盖未完整或仍存在未决关键缺口")
    if not isinstance(evidence, list) or not evidence or not isinstance(paths, list) or not paths:
        problems.append("源码证据及关键路径必须为非空列表")
    if problems:
        return {"status": "partial", "reason": "；".join(problems), "verified_files": [], "relations": []}
    if any(not isinstance(e, dict) or not isinstance(e.get("file"), str) for e in evidence):
        return {"status": "partial", "reason": "源码证据文件字段必须为字符串", "verified_files": [], "relations": []}
    if not plan.get("required_source_files"):
        return {"status": "partial", "reason": "查询计划缺少本需求变更文件范围", "verified_files": [], "relations": []}
    file_names = {e["file"] for e in evidence}
    if len(file_names) > int(plan.get("max_files", 30)) or len(evidence) > 300:
        return {"status": "partial", "reason": "源码补核超出最大文件或证据数量", "verified_files": [], "relations": []}
    cache, verified = {}, {}
    for e in evidence:
        if not isinstance(e, dict):
            problems.append("源码证据格式非法"); continue
        name = str(e.get("file") or "")
        eid = str(e.get("evidence_id") or "")
        components = name.replace("\\", "/").split("/")
        if not eid or eid in verified or not name or name.startswith(("/", "\\")) or ":" in name or ".." in components:
            problems.append("源码证据 ID 或仓库相对路径非法"); continue
        if name not in cache:
            r = run_git(plan["repo_path"], ["show", f"{sha}:{name}"], timeout=20)
            cache[name] = r.stdout.splitlines() if r.returncode == 0 else None
        lines = cache[name]
        start, end = e.get("start_line"), e.get("end_line", e.get("start_line"))
        if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int) or start < 1 or end < start or lines is None or end > len(lines):
            problems.append(f"{eid} 源码行范围不可核验"); continue
        actual = "\n".join(lines[start-1:end])
        if not str(e.get("source") or "").strip() or e["source"].replace("\r\n", "\n").strip() != actual.strip():
            problems.append(f"{eid} 原文与目标 SHA 源码不一致"); continue
        if e.get("kind") not in {"entry", "call", "rule", "sql", "config"}:
            problems.append(f"{eid} 缺少有效证据类型"); continue
        verified[eid] = e
        checked.append(name)
        if e.get("kind") == "call":
            caller, callee, hop = e.get("caller"), e.get("callee"), e.get("hop", 1)
            if not caller or not callee or isinstance(hop, bool) or not isinstance(hop, int) or not 1 <= hop <= 2 or str(callee).split(".")[-1] not in actual:
                problems.append(f"{eid} 调用关系或跳数不可核验")
            else:
                relations.append({"caller": caller, "callee": callee, "hop": hop, "source_file": name, "line": start, "evidence_id": eid})
    used = set()
    for path in paths:
        ids = path.get("evidence_ids", []) if isinstance(path, dict) else []
        if not isinstance(path, dict) or not path.get("path_id") or not isinstance(ids, list) or not ids or any(x not in verified for x in ids):
            problems.append("关键路径缺少已核验的源码证据"); continue
        used.update(ids)
        path_files = {verified[x]["file"] for x in ids}
        kinds = {verified[x]["kind"] for x in ids}
        if len(path_files) > 1 and "call" not in kinds:
            problems.append(f"{path['path_id']} 跨文件路径缺少实际调用证据")
        if "entry" not in kinds or not kinds.intersection({"rule", "sql", "config"}):
            problems.append(f"{path['path_id']} 未覆盖入口及实际行为")
    covered_files = {verified[eid]["file"] for eid in used}
    missing = set(plan.get("required_source_files", [])) - covered_files
    if missing:
        problems.append("本需求变更文件未被关键路径覆盖：" + "、".join(sorted(missing)))
    required_paths = set(plan.get("required_critical_paths") or [])
    if required_paths - {p.get("path_id") for p in paths if isinstance(p, dict)}:
        problems.append("未覆盖指定关键业务路径")
    return {"status": "partial" if problems else "verified", "reason": "；".join(problems),
            "target_sha": sha, "verified_files": sorted(set(checked)), "critical_path_count": len(paths),
            "relations": relations if not problems else [], "evidence_ids": sorted(verified),
            "unresolved_critical": supplied.get("unresolved_critical", [])}
