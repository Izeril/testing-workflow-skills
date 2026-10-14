#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Evidence-based selection of code files affected by a requirement.

This module deliberately keeps Git collection separate from source analysis.  A
Git diff answers "what changed"; it cannot, by itself, answer "what is in
scope for this requirement".  The selector produces an auditable bridge
between those two questions.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, List, Tuple

from workflow_logging import log_event
from workflow_git_runner import run_git


SOURCE_SUFFIXES = {'.java', '.kt', '.groovy', '.scala', '.xml', '.yml', '.yaml', '.properties', '.py', '.js', '.jsx', '.ts', '.tsx', '.vue', '.sql', '.ddl', '.dml', '.psql', '.pls', '.pks', '.pkb'}
TEST_MARKERS = ('test', 'tests', 'mocktest', 'it', 'spec')
EXCLUDED_PARTS = {
    '.agent', '.agents', '.understand-anything', 'docs', 'doc', 'delivery',
    'target', 'build', 'dist', 'log', 'logs', 'tmp', 'temp', 'generated',
}
EXCLUDED_SUFFIXES = {'.md', '.txt', '.pdf', '.doc', '.docx', '.zip', '.jar', '.class', '.log'}


def _git(repo_path: str, args: List[str], timeout: int = 60) -> Tuple[str, str]:
    result = run_git(repo_path, args, timeout=timeout, executor=subprocess.run)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'git command failed')
    return result.stdout, result.stderr


def resolve_git_ref(repo_path: str, ref: str, prefer_remote: bool = False) -> str:
    """Resolve a ref to a commit, preferring origin for short branch names."""
    if not ref:
        return ''
    candidates = [ref]
    if ref.startswith('origin/'):
        # A missing remote baseline may fall back to its local counterpart;
        # callers record that fallback as a risk in the impact report.
        candidates.append(ref[len('origin/'):])
    else:
        candidates = [f'origin/{ref}', ref] if prefer_remote else [ref, f'origin/{ref}']
    for candidate in dict.fromkeys(candidates):
        try:
            _git(repo_path, ['rev-parse', '--verify', f'{candidate}^{{commit}}'], timeout=20)
            return candidate
        except (OSError, subprocess.TimeoutExpired, RuntimeError):
            continue
    return ''


def _optional_commit(repo_path: str, ref: str) -> str:
    """Return a SHA for an optional local/remote ref without raising."""
    if not ref:
        return ''
    try:
        sha, _ = _git(repo_path, ['rev-parse', '--verify', f'{ref}^{{commit}}'], timeout=20)
        return sha.strip()
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        return ''


def _short_ref(ref: str) -> str:
    return ref[len('origin/'):] if str(ref).startswith('origin/') else str(ref)


def _snapshot_remote_refs(repo_path: str, refs: Iterable[str]) -> Dict[str, str]:
    return {ref: _optional_commit(repo_path, ref) for ref in refs if ref}


def inspect_git_freshness(
    repo_path: str,
    branch_ref: str,
    baseline_ref: str,
    refresh_remote: bool = True,
) -> Dict[str, Any]:
    """Refresh only requested refs by default; allow explicit cached-only inspection."""
    branch_short = _short_ref(branch_ref)
    baseline_short = _short_ref(baseline_ref or 'origin/master')
    remote_branch_ref = f'origin/{branch_short}' if branch_short else ''
    remote_baseline_ref = f'origin/{baseline_short}' if baseline_short else ''
    report: Dict[str, Any] = {
        'freshness_status': 'latest_unverified',
        'fetch_requested': bool(refresh_remote),
        'fetch_command': '',
        'fetch_checked_at': datetime.now().isoformat(timespec='seconds'),
        'requested_branch': branch_ref or '', 'requested_baseline': baseline_ref or 'origin/master',
        'local_branch_ref': branch_short, 'remote_branch_ref': remote_branch_ref,
        'local_baseline_ref': baseline_short, 'remote_baseline_ref': remote_baseline_ref,
        'worktree_dirty': False, 'uncommitted_changes': [], 'uncommitted_changes_included': False,
        'analysis_source': 'remote_snapshot',
    }
    try:
        porcelain, _ = _git(repo_path, ['status', '--porcelain=v1'], timeout=30)
        report['uncommitted_changes'] = [line for line in porcelain.splitlines() if line]
        report['worktree_dirty'] = bool(report['uncommitted_changes'])
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        report['worktree_status_error'] = str(exc)

    remote_refs = tuple(dict.fromkeys(ref for ref in (remote_branch_ref, remote_baseline_ref) if ref))
    refs_before = _snapshot_remote_refs(repo_path, remote_refs)
    report['remote_refs_before'] = refs_before
    if not refresh_remote:
        report.update(
            fetch_status='skipped',
            freshness_reason='未启用远程刷新；使用本地缓存的 origin 引用，最新性未验证',
            freshness_warning='本次已关闭远程刷新，仅使用缓存引用，不能形成正式 UAT 结论',
            remote_branch_sha=refs_before.get(remote_branch_ref, ''),
            remote_baseline_sha=refs_before.get(remote_baseline_ref, ''),
        )
        if report['remote_branch_sha'] and report['remote_baseline_sha']:
            report.update(
                freshness_status='cached_remote_unverified',
                analysis_source='cached_remote_snapshot',
                analysis_branch_ref=remote_branch_ref,
                analysis_baseline_ref=remote_baseline_ref,
                analysis_branch_sha=report['remote_branch_sha'],
                analysis_baseline_sha=report['remote_baseline_sha'],
            )
        log_event(
            'WARNING', report['freshness_reason'], event='git_remote_refresh_skipped',
            repo_path=repo_path, requested_refs=list(remote_refs),
        )
        return report

    refspecs = []
    for short_ref in dict.fromkeys(ref for ref in (branch_short, baseline_short) if ref):
        refspecs.append(f'+refs/heads/{short_ref}:refs/remotes/origin/{short_ref}')
    fetch_args = ['fetch', '--no-tags', 'origin', *refspecs]
    report['fetch_command'] = 'git ' + ' '.join(fetch_args)
    try:
        _git(repo_path, fetch_args, timeout=90)
        report['fetch_status'] = 'succeeded'
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        refs_after = _snapshot_remote_refs(repo_path, remote_refs)
        changed_refs = {
            ref: {'before': refs_before.get(ref, ''), 'after': refs_after.get(ref, '')}
            for ref in remote_refs if refs_before.get(ref, '') != refs_after.get(ref, '')
        }
        report.update(
            fetch_status='failed',
            freshness_reason=f'无法连接远程或刷新指定引用：{exc}',
            fetch_warning='刷新失败；未执行自动回滚，以避免覆盖并发产生的合法引用更新。请检查 remote_ref_changes 后重试。',
            partial_remote_update_detected=bool(changed_refs),
            remote_ref_changes=changed_refs,
            remote_refs_after=refs_after,
        )
        report['remote_branch_sha'] = refs_after.get(remote_branch_ref, '')
        report['remote_baseline_sha'] = refs_after.get(remote_baseline_ref, '')
        log_event(
            'ERROR', report['freshness_reason'], event='git_remote_refresh_failed',
            repo_path=repo_path, partial_remote_update_detected=bool(changed_refs),
            remote_ref_changes=changed_refs,
        )
        if report['remote_branch_sha'] and report['remote_baseline_sha']:
            report.update(
                freshness_status='remote_unreachable_cached', analysis_source='cached_remote_snapshot',
                analysis_branch_ref=remote_branch_ref, analysis_baseline_ref=remote_baseline_ref,
                analysis_branch_sha=report['remote_branch_sha'], analysis_baseline_sha=report['remote_baseline_sha'],
            )
        return report

    report['remote_refs_after'] = _snapshot_remote_refs(repo_path, remote_refs)
    report['local_branch_sha'] = _optional_commit(repo_path, branch_short)
    report['local_baseline_sha'] = _optional_commit(repo_path, baseline_short)
    report['remote_branch_sha'] = _optional_commit(repo_path, remote_branch_ref)
    report['remote_baseline_sha'] = _optional_commit(repo_path, remote_baseline_ref)
    missing_remote = [label for label, key in (('开发分支', 'remote_branch_sha'), ('基线分支', 'remote_baseline_sha')) if not report.get(key)]
    if missing_remote:
        report['freshness_reason'] = f'远程未找到{"、".join(missing_remote)}，无法验证最新性'
        return report

    report.update(
        freshness_status='verified', analysis_branch_ref=remote_branch_ref, analysis_baseline_ref=remote_baseline_ref,
        analysis_branch_sha=report['remote_branch_sha'], analysis_baseline_sha=report['remote_baseline_sha'],
    )
    mismatches = []
    for label, local_key, remote_key in (
        ('开发分支', 'local_branch_sha', 'remote_branch_sha'),
        ('基线分支', 'local_baseline_sha', 'remote_baseline_sha'),
    ):
        if report.get(local_key) and report[local_key] != report[remote_key]:
            mismatches.append(label)
    report['local_checkout_status'] = 'diverged' if mismatches else 'aligned'
    if mismatches:
        report['local_remote_mismatches'] = mismatches
        report['freshness_warning'] = f'本地与远程 {"、".join(mismatches)} SHA 不一致；本次仍以远程快照分析，未使用本地工作副本'
    return report

def extract_requirement_anchors(doc_content: str, project_info: Dict[str, Any] | None = None) -> List[str]:
    """Extract stable identifiers and useful business words from requirement text."""
    candidates: List[str] = []
    patterns = (
        r'\b(?:NS|PRJ)[-_]?\d{3,}\b',
        r'\b[A-Z][A-Za-z0-9]+(?:Service|Controller|Facade|Mapper|DTO|VO|Entity|Test)\b',
        r'\b[a-z][A-Za-z0-9_]*(?:Amount|Capital|Status|Code|Id)\b',
        r'\b[A-Za-z][A-Za-z0-9_]*_(?:id|code|amount|capital|status)\b',
    )
    for pattern in patterns:
        candidates.extend(re.findall(pattern, doc_content, re.IGNORECASE))
    if project_info:
        for key in ('services', 'tables', 'interfaces', 'jobs'):
            candidates.extend(str(value) for value in project_info.get(key, []) if value)

    # Chinese terms of two or more characters are useful for direct source
    # matches, but common document labels are not meaningful anchors.
    ignored = {'需求', '功能', '接口', '字段', '服务', '方案', '测试', '优化', '修改', '金额', '代码', '分析'}
    candidates.extend(word for word in re.findall(r'[\u4e00-\u9fff]{2,12}', doc_content)
                      if word not in ignored)
    unique: List[str] = []
    seen = set()
    for item in candidates:
        value = re.sub(r'^[`#*\s]+|[`*\s]+$', '', str(item)).strip()
        key = value.casefold()
        if len(value) >= 2 and key not in seen:
            unique.append(value)
            seen.add(key)
    return unique[:120]


def collect_git_diff(repo_path: str, baseline: str, branch: str) -> Dict[str, Any]:
    """Collect a three-dot diff and the SHA evidence needed for audit."""
    baseline_sha, _ = _git(repo_path, ['rev-parse', baseline])
    branch_sha, _ = _git(repo_path, ['rev-parse', branch])
    merge_base, _ = _git(repo_path, ['merge-base', baseline, branch])
    output, _ = _git(repo_path, ['diff', '--name-status', f'{baseline}...{branch}'])
    raw_files = []
    for line in output.splitlines():
        parts = line.split('\t')
        if len(parts) < 2:
            continue
        status, path = parts[0], parts[-1]
        raw_files.append({'path': path, 'status': status, 'old_path': parts[1] if status.startswith('R') and len(parts) > 2 else ''})
    return {
        'baseline_sha': baseline_sha.strip(), 'branch_sha': branch_sha.strip(),
        'merge_base_sha': merge_base.strip(),
        'comparison_command': f'git diff --name-status {baseline}...{branch}',
        'raw_changed_files': raw_files,
    }


def _is_test_file(path: str) -> bool:
    lower = path.casefold()
    return any(marker in lower for marker in TEST_MARKERS) or '/test/' in f'/{lower}/'


def _exclude_reason(path: str) -> str:
    posix = PurePosixPath(path)
    parts = {part.casefold() for part in posix.parts}
    suffix = posix.suffix.casefold()
    if parts & EXCLUDED_PARTS:
        return '目录属于文档、交付物、构建产物或临时文件'
    if suffix in EXCLUDED_SUFFIXES:
        return '文件类型不是可分析源码或配置'
    if suffix not in SOURCE_SUFFIXES:
        return '文件类型不在默认源码/配置分析范围'
    return ''


def _source_at_ref(repo_path: str, branch: str, path: str) -> str:
    try:
        content, _ = _git(repo_path, ['show', f'{branch}:{path}'], timeout=20)
        return content
    except (OSError, subprocess.TimeoutExpired, RuntimeError):
        return ''


def _production_stems(paths: Iterable[str]) -> List[str]:
    stems = []
    for path in paths:
        stem = PurePosixPath(path).stem
        if not _is_test_file(path) and stem:
            stems.append(stem)
    return stems


def select_impact_files(repo_path: str, branch: str, raw_changed_files: List[Dict[str, str]], anchors: List[str]) -> Dict[str, Any]:
    """Score changed files and attach an inclusion/exclusion reason to every one."""
    candidates, excluded, selected, related_tests, evidence = [], [], [], [], []
    production_stems = _production_stems(item['path'] for item in raw_changed_files)
    anchor_keys = [anchor.casefold() for anchor in anchors if len(anchor) >= 2]

    for item in raw_changed_files:
        path = item['path']
        reason = _exclude_reason(path)
        if reason:
            excluded.append({**item, 'reason': reason, 'layer': '排除文件'})
            continue
        candidates.append(item)

        content = _source_at_ref(repo_path, branch, path)
        searchable = f'{path}\n{content}'.casefold()
        matched = [anchor for anchor, key in zip(anchors, anchor_keys) if key in searchable]
        is_test = _is_test_file(path)
        score, file_evidence = 0, []
        if not is_test:
            score += 5
            file_evidence.append('位于生产源码或运行配置目录')
        if matched:
            score += 5
            file_evidence.append(f"直接命中需求锚点：{', '.join(matched[:8])}")
        covered_stems = [stem for stem in production_stems if stem.casefold() in searchable]
        if is_test and covered_stems:
            score += 3
            file_evidence.append(f"测试覆盖变更生产类：{', '.join(covered_stems[:5])}")
        if any(token in content for token in ('select ', 'insert ', 'update ', 'delete ', '@Select', '@Update')):
            score += 4 if matched else 1
            file_evidence.append('包含 SQL 或 Mapper 数据访问证据')
        if any(stem.casefold() in searchable for stem in production_stems if PurePosixPath(path).stem != stem):
            score += 4
            file_evidence.append('包含变更类名引用，可作为调用链关联证据')

        layer = 'P1' if score >= 8 and not is_test else 'P2' if score >= 4 or is_test and file_evidence else 'P3'
        record = {**item, 'layer': layer, 'score': score, 'matched_anchors': matched, 'evidence': file_evidence}
        if layer in ('P1', 'P2') and file_evidence:
            selected.append(record)
            evidence.append({'file': path, 'layer': layer, 'score': score, 'evidence': file_evidence})
            if is_test:
                related_tests.append(record)
        else:
            excluded.append({**record, 'reason': '缺少需求锚点或调用链关联证据，保留为待确认范围'})

    return {
        'candidate_files': candidates, 'selected_files': selected,
        'related_test_files': related_tests, 'excluded_files': excluded,
        'selection_evidence': evidence,
    }
