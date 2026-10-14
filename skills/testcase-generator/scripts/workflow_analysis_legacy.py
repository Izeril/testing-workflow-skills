#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Requirement ingestion, code-target analysis, and analysis artifacts."""

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from output_requirement_fields import extract_explicit_output_fields
from workflow_git_runner import run_git
from cross_skill_adapter import CrossSkillDependencyError

from workflow_common import (
    _compact_text,
    _get_internal_dir,
    _normalize_output_layout,
    _stable_id,
    _validate_output_layout,
    _workflow_timestamp,
    _write_internal_json,
    collect_git_diff,
    compare_structured,
    extract_business_logic,
    extract_requirement_anchors,
    inspect_git_freshness,
    log_console,
    resolve_precise_git_ref,
    select_impact_files,
)

# 比较器只通过 workflow_common 暴露的跨技能适配器调用。

def categorize_documents(md_files, base_dir):
    """分类文档：需求文档 vs 提测文档"""
    log_console("\n📋 分类文档...")

    requirement_docs = []
    test_docs = []
    design_docs = []
    interface_docs = []

    for filepath in md_files:
        rel_path = os.path.relpath(filepath, base_dir)
        basename = os.path.basename(filepath).lower()
        dirname = os.path.basename(os.path.dirname(filepath)).lower()

        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()[:2000]
        except (OSError, UnicodeError):
            continue

        # 分类识别
        if '提测' in dirname or '提测' in basename or 'PRJ-' in basename:
            test_docs.append(filepath)
            log_console(f"  📋 提测文档: {rel_path}")
        elif '设计' in dirname or '设计' in basename or '方案' in basename:
            design_docs.append(filepath)
            log_console(f"  📐 设计方案: {rel_path}")
        elif '接口' in dirname or '接口' in basename:
            interface_docs.append(filepath)
            log_console(f"  🔗 接口文档: {rel_path}")
        elif '需求' in dirname or '需求' in basename or any(kw in content for kw in ['需求背景', '需求描述']):
            requirement_docs.append(filepath)
            log_console(f"  📝 需求文档: {rel_path}")
        else:
            requirement_docs.append(filepath)

    return requirement_docs, test_docs, design_docs, interface_docs


# ============================================================================
# 步骤②：需求分析
# ============================================================================

def extract_latest_requirement(content: str) -> str:
    """
    提取最新需求（分割线标记识别）

    分割线格式：
    - === 分割线，以上为最新需求 ===
    - ==== 分割线 ====
    - --- 分割线 ---
    """
    patterns = [
        r'===+\s*分割线.*?===+\s*',
        r'---+\s*分割线.*?---+\s*',
        r'===+\s*$'
    ]

    for pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            return content[:match.start()]

    return content


def read_all_docs(filepaths):
    """读取并合并所有文档内容"""
    all_content = []
    for filepath in filepaths:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
                filename = os.path.basename(filepath)
                all_content.append(f"\n=== {filename} ===\n{content}")
        except Exception as e:
            log_console(f"  ⚠️ 读取失败: {filepath} - {e}")
    return '\n'.join(all_content)



def extract_branch_from_content(doc_content: str) -> str:
    """Extract a development branch from requirement or release-test docs."""
    label_pattern = r'(?:\u6d89\u53ca\u5f00\u53d1\u5206\u652f|\u5f00\u53d1\u5206\u652f)'
    branch_token = r'((?:origin[\\/])?(?:feat|feature|bugfix|hotfix|release|dev|test|uat|master)[A-Za-z0-9_.\\/\-]*)'
    m = re.search(label_pattern + r'[\s\S]{0,200}?' + branch_token, doc_content, re.IGNORECASE)
    if not m:
        m = re.search(r'(?:\u5206\u652f)[^\n]*?' + branch_token, doc_content, re.IGNORECASE)
    if not m:
        return ''
    return m.group(1).replace('\\_', '_').replace('\\/', '/').strip()


def _split_services(raw_value: str) -> List[str]:
    """规范提测材料中的服务列表。"""
    if not raw_value:
        return []
    value = re.sub(r'[`*_>#]', '', str(raw_value)).strip()
    value = re.split(r'[；;。]', value, maxsplit=1)[0]
    return [item.strip() for item in re.split(r'[、,，/\n]+', value) if item.strip()]


def extract_services_from_content(doc_content: str) -> List[str]:
    """按单份提测材料提取服务，避免跨语言文档串目标。"""
    for pattern in [r'涉及服务\s*[：:]\s*([^\n]+)', r'服务名称\s*[：:]\s*([^\n]+)']:
        match = re.search(pattern, doc_content, re.IGNORECASE)
        if match:
            return _split_services(match.group(1))
    return []


def infer_document_language(doc_path: Path, doc_content: str) -> str:
    """只依据当前提测材料识别语言，不从其他文档借用结论。"""
    source = f'{doc_path.name}\n{doc_content[:4000]}'.lower()
    if 'python' in source:
        return 'python'
    if 'java' in source or 'spring boot' in source or 'mybatis' in source:
        return 'java'
    return ''


def _safe_target_id(value: str, fallback: str) -> str:
    target_id = re.sub(r'[^a-zA-Z0-9_-]+', '-', value or '').strip('-').lower()
    return target_id or fallback


def _normalize_target_registry(repo_config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """读取仓库注册表；未配置时仅允许旧版单目标回退。"""
    raw_targets = (repo_config or {}).get('targets') or (repo_config or {}).get('repositories') or []
    if not isinstance(raw_targets, list):
        return []
    normalized = []
    for index, item in enumerate(raw_targets, 1):
        if not isinstance(item, dict) or not item.get('path'):
            continue
        languages = item.get('languages') or item.get('language') or []
        services = item.get('services') or item.get('service') or []
        if isinstance(languages, str):
            languages = [languages]
        if isinstance(services, str):
            services = _split_services(services)
        normalized.append({
            'target_id': item.get('target_id') or item.get('key') or f'registry-{index}',
            'repo_path': item.get('path'),
            'baseline': item.get('baseline') or (repo_config or {}).get('baseline') or 'origin/master',
            'languages': [str(value).lower() for value in languages],
            'services': services,
            'refresh_remote_refs': bool(item.get('refresh_remote_refs', repo_config.get('refresh_remote_refs', True))),
        })
    return normalized


def _load_code_target_manifest(manifest_path: Optional[str]) -> Tuple[List[Dict[str, Any]], List[str]]:
    if not manifest_path:
        return [], []
    path = Path(manifest_path)
    if not path.exists():
        return [], [f'代码目标清单不存在：{path}']
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        return [], [f'代码目标清单无法读取：{exc}']
    targets = data.get('targets', data.get('code_targets', [])) if isinstance(data, dict) else data
    if not isinstance(targets, list):
        return [], ['代码目标清单格式错误：targets 必须是数组']
    return [item for item in targets if isinstance(item, dict)], []


def build_code_targets(
    test_docs: List[Path], req_dir: Path, repo_config: Optional[Dict[str, Any]] = None,
    manifest_path: Optional[str] = None, force_legacy: bool = False,
    legacy_repo_path: Optional[str] = None, legacy_branch: Optional[str] = None,
    legacy_baseline: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """建立“文档 -> 服务/语言 -> 仓库/分支”的显式代码目标清单。"""
    repo_config = repo_config or {}
    registry = _normalize_target_registry(repo_config)
    manifest_targets, errors = _load_code_target_manifest(manifest_path)
    if force_legacy:
        return ([{
            'target_id': 'cli-legacy', 'source_doc': 'CLI 参数', 'language': '', 'services': [],
            'repo_path': legacy_repo_path, 'branch': legacy_branch,
            'baseline': legacy_baseline or repo_config.get('baseline') or 'origin/master',
            'mapping_source': 'cli', 'status': 'pending',
            'refresh_remote_refs': bool(repo_config.get('refresh_remote_refs', True)),
        }], errors)
    if manifest_targets:
        targets = []
        for index, item in enumerate(manifest_targets, 1):
            target = dict(item)
            target['target_id'] = _safe_target_id(str(target.get('target_id') or target.get('key') or target.get('service') or target.get('branch') or ''), f'manifest-{index}')
            target['repo_path'] = target.get('repo_path') or target.get('path')
            target['language'] = str(target.get('language') or '').lower()
            target['services'] = _split_services(target.get('services', '')) if isinstance(target.get('services'), str) else (target.get('services') or [])
            target['baseline'] = target.get('baseline') or repo_config.get('baseline') or 'origin/master'
            target['source_doc'] = target.get('source_doc') or f'代码目标清单：{manifest_path}'
            target['mapping_source'], target['status'] = 'manifest', 'pending'
            target.setdefault('refresh_remote_refs', bool(repo_config.get('refresh_remote_refs', True)))
            targets.append(target)
        return targets, errors

    candidates = []
    for index, doc_path in enumerate(test_docs, 1):
        try:
            content = doc_path.read_text(encoding='utf-8')
        except OSError as exc:
            errors.append(f'无法读取提测材料 {doc_path.name}：{exc}')
            continue
        services = extract_services_from_content(content)
        candidates.append({
            'target_id': _safe_target_id(f'{infer_document_language(doc_path, content)}-{services[0] if services else extract_branch_from_content(content)}', f'doc-{index}'),
            'source_doc': str(doc_path.relative_to(req_dir)),
            'source_sha256': hashlib.sha256(content.encode('utf-8')).hexdigest(),
            'language': infer_document_language(doc_path, content), 'services': services,
            'branch': extract_branch_from_content(content), 'baseline': repo_config.get('baseline') or 'origin/master',
            'status': 'pending', 'mapping_source': 'document',
            'refresh_remote_refs': bool(repo_config.get('refresh_remote_refs', True)),
        })
    allow_default_repo = len(candidates) == 1 and bool(repo_config.get('path'))
    seen_ids = set()
    for candidate in candidates:
        base_id, suffix = candidate['target_id'], 2
        while candidate['target_id'] in seen_ids:
            candidate['target_id'] = f'{base_id}-{suffix}'
            suffix += 1
        seen_ids.add(candidate['target_id'])
        matched = [registered for registered in registry
                   if (not registered['languages'] or candidate['language'] in registered['languages'])
                   and bool(set(candidate['services']) & set(registered['services']))]
        if len(matched) == 1:
            registered = matched[0]
            candidate.update(repo_path=registered['repo_path'], baseline=registered['baseline'], registry_target_id=registered['target_id'], mapping_source='registry')
        elif len(matched) > 1:
            candidate.update(status='blocked', block_reason='仓库注册表匹配到多个目标，无法唯一确定代码仓库')
        elif allow_default_repo:
            candidate.update(repo_path=repo_config.get('path'), baseline=repo_config.get('baseline') or 'origin/master', mapping_source='legacy-single-target')
        else:
            candidate.update(status='blocked', block_reason='多服务需求未配置仓库映射；请提供 --code-targets 或 repo.targets')
    return candidates, errors


def detect_repository_languages(repo_path: str) -> List[str]:
    """Detect mono-repository languages without requiring build files at its root."""
    root = Path(repo_path)
    languages = set()
    if not root.is_dir():
        return []
    for current, dirs, files in os.walk(root):
        rel_depth = len(Path(current).relative_to(root).parts)
        if rel_depth >= 4:
            dirs[:] = []
        dirs[:] = [name for name in dirs if name not in {'.git', 'target', 'build', 'node_modules'}]
        names = set(files)
        if names & {'pom.xml', 'build.gradle', 'build.gradle.kts'}:
            languages.add('java')
        if names & {'pyproject.toml', 'setup.py', 'requirements.txt', 'Pipfile'}:
            languages.add('python')
        if languages == {'java', 'python'}:
            break
    return sorted(languages)

def _resolve_git_ref_strict(repo_path: str, ref: str) -> str:
    """Resolve short branch names against origin first, then local refs."""
    return resolve_precise_git_ref(repo_path, ref, prefer_remote=not str(ref).startswith('origin/'))


def preflight_code_target(target: Dict[str, Any]) -> Dict[str, Any]:
    """Use a verified remote commit snapshot; local checkout drift is evidence, not a blocker."""
    result = dict(target)
    if result.get('status') == 'blocked':
        return result
    repo_path = result.get('repo_path')
    if not repo_path or not Path(repo_path).is_dir():
        return dict(result, status='blocked', block_reason='目标仓库不存在或未配置')
    if not (Path(repo_path) / '.git').exists():
        return dict(result, status='blocked', block_reason='目标路径不是 Git 仓库')
    repo_languages = detect_repository_languages(repo_path)
    if result.get('language') and result['language'] not in repo_languages:
        return dict(result, status='blocked', repository_languages=repo_languages, block_reason=f'语言不匹配：提测材料为 {result["language"]}，仓库识别为 {"/".join(repo_languages) or "未知"}')
    freshness = inspect_git_freshness(
        repo_path, result.get('branch', ''), result.get('baseline', 'origin/master'),
        refresh_remote=bool(result.get('refresh_remote_refs', True)),
    )
    freshness_status = freshness.get('freshness_status')
    if freshness_status == 'latest_unverified':
        return dict(result, status='latest_unverified', repository_languages=repo_languages,
                    freshness=freshness, block_reason=freshness.get('freshness_reason', '远程最新性未验证且无可用远程快照，禁止分析'))
    branch = freshness.get('analysis_branch_ref', '')
    baseline = freshness.get('analysis_baseline_ref', '')
    if not branch or not baseline:
        return dict(result, status='latest_unverified', repository_languages=repo_languages, freshness=freshness,
                    block_reason='缺少可验证的远程开发分支或基线快照，禁止分析')
    try:
        diff_evidence = collect_git_diff(repo_path, baseline, branch)
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        return dict(result, status='blocked', repository_languages=repo_languages, freshness=freshness, block_reason=f'无法读取远程快照差异：{exc}')
    raw_changed_files = diff_evidence.get('raw_changed_files', [])
    if not raw_changed_files:
        return dict(result, status='blocked', repository_languages=repo_languages, freshness=freshness, block_reason='目标分支相对基线无代码变更，禁止生成代码影响结论')
    status = 'verified_stale' if freshness_status in {'remote_unreachable_cached', 'cached_remote_unverified'} else 'verified'
    return dict(result, status=status, branch=branch, baseline=baseline,
                repository_languages=repo_languages, freshness=freshness,
                baseline_fallback_risk=False,
                preflight_changed_files=[item['path'] for item in raw_changed_files], **diff_evidence)

def iter_source_markdown_files(req_dir: Path):
    from source_documents import cached_source_paths
    saved_sources = cached_source_paths(req_dir)
    if saved_sources is not None:
        return saved_sources
    """Return source docs only; exclude generated workflow artifacts."""
    output_dir_name = '\u6d4b\u8bd5\u5206\u6790'
    excluded_parts = {output_dir_name, '.ai-internal', '.ai-archive'}
    generated_names = {
        '需求分析.md',
        '代码评审报告.md',
        '详细测试点.md',
        '流程度量报告.md',
        '测试点生成报告.md',
        '02_测试设计说明.md',
    }
    source_files = []
    for p in Path(req_dir).rglob('*.md'):
        if any(part in excluded_parts for part in p.parts):
            continue
        if p.name in generated_names or p.name.endswith('_评审报告.md'):
            continue
        source_files.append(p)
    return source_files

def _unique_text(values: List[Any]) -> List[str]:
    """按出现顺序去重，并清理常见 Markdown 标记。"""
    result, seen = [], set()
    for value in values:
        text = re.sub(r'[`*>#|]', '', str(value or ''))
        text = re.sub(r'\s+', ' ', text).strip(' ：:;,，；')
        key = text.casefold()
        if text and key not in seen:
            result.append(text)
            seen.add(key)
    return result


def _extract_markdown_section(doc_content: str, names: List[str], limit: int = 1200) -> str:
    """兼容 ATX 标题、加粗标签、普通标签和 Markdown 表格字段。"""
    label = '|'.join(re.escape(name) for name in names)
    patterns = [
        (
            rf'^#{{1,6}}\s*(?:{label})\s*$\n(?P<value>.*?)(?=^#{{1,6}}\s|\Z)',
            re.MULTILINE | re.DOTALL,
        ),
        (rf'^\s*\*\*(?:{label})\*\*\s*[：:]\s*(?P<value>.+)$', re.MULTILINE),
        (rf'^\s*(?:{label})\s*[：:]\s*(?P<value>.+)$', re.MULTILINE),
        (rf'^\|\s*(?:{label})\s*\|\s*(?P<value>[^|]+)\|', re.MULTILINE),
    ]
    for pattern, flags in patterns:
        match = re.search(pattern, doc_content, flags)
        if not match:
            continue
        value = re.sub(r'\n{3,}', '\n\n', match.group('value')).strip()
        if value and not re.fullmatch(r'[-:|\s]+', value):
            return value[:limit]
    return ''


def _extract_named_list(doc_content: str, names: List[str]) -> List[str]:
    raw = _extract_markdown_section(doc_content, names, limit=2000)
    if not raw:
        return []
    values = re.split(r'[\n、,，；;]+', raw)
    return _unique_text(value.lstrip('-+*0123456789.、 ') for value in values)


_IDENTIFIER_FACT_DIMENSIONS = {
    'fields', 'apis', 'tables', 'jobs', 'configs', 'errors',
    'actors', 'scenarios', 'integrations',
}
_DESCRIPTIVE_FACT_DIMENSIONS = {
    'rules', 'state_transitions', 'calculations', 'data_compatibility',
}


def _is_absence_marker(value: Any) -> bool:
    text = re.sub(r'\s+', '', str(value or '')).strip('：:;；,，。.-_')
    return text.casefold() in {'无', '无明确证据', '不涉及', '不适用', '暂无', 'none', 'null', 'n/a', 'na'}


def _is_structured_fact_candidate(value: Any) -> bool:
    """Reject document containers while allowing concise business assertions."""
    raw = str(value or '').strip()
    if not raw or _is_absence_marker(raw):
        return False
    if raw.startswith(('|', '>', '```', '{', '[{')):
        return False
    if re.search(r'''(?s)[{\[]\s*["'][^"']+["']\s*:''', raw):
        return False
    if re.search(r'(?i)(告警编号|问题直达|stack\s*trace|traceback)', raw):
        return False
    return True

def _is_safe_structured_key(value: Any, limit: int = 120) -> bool:
    """Return whether a value is a short semantic identifier, not raw payload."""
    raw = str(value or '')
    text = re.sub(r'\s+', ' ', raw).strip()
    if not text or len(text) > limit or '\n' in raw or '\r' in raw:
        return False
    if text.startswith(('{', '[{')) or text.endswith(('}]',)):
        return False
    if re.search(r'''(?s)[{\[]\s*["'][^"']+["']\s*:''', text):
        return False
    if text.count('|') >= 2 or text.startswith(('>', '```')):
        return False
    if re.fullmatch(
        r'(?i)(business_rules_summary|if_else_rules|exception_rules|validation_rules|value_annotations|source_file|analysis_warnings)',
        text,
    ):
        return False
    if re.search(r'(?i)(告警编号|问题直达|stack\s*trace|traceback|exception\s*information)', text):
        return False
    return True


def _structured_fact_key(dimension: str, title: str, content: Any) -> str:
    """Separate stable comparison identity from full requirement evidence."""
    text = re.sub(r'\s+', ' ', str(content or '')).strip(' ：:;,，；')
    label = re.sub(r'\s+', ' ', str(title or dimension or '需求项')).strip(' ：:;,，；')
    if dimension in _IDENTIFIER_FACT_DIMENSIONS and _is_safe_structured_key(text):
        return text
    if dimension not in _DESCRIPTIVE_FACT_DIMENSIONS and _is_safe_structured_key(text):
        return text
    base = label if _is_safe_structured_key(label, 48) else str(dimension or '需求项')
    digest = hashlib.sha256(
        f'{dimension}:{title}:{text}'.encode('utf-8')
    ).hexdigest()[:8].upper()
    return base if dimension == 'rules' else f'{base}-{digest}'

def extract_project_info(doc_content):
    """从多种 Markdown 版式提取项目信息，为分析质量门禁提供稳定字段。"""
    info = {
        'project_name': '',
        'background': '',
        'problem': '',
        'solution': '',
        'root_cause': '',
        'services': [],
        'tables': [],
        'interfaces': [],
        'jobs': [],
        'branch': '',
        'risk_level': '中'
    }

    # 项目名称优先使用明确字段，避免把“需求文档”等文件分隔标题当作项目名。
    explicit_name = _extract_markdown_section(doc_content, ['项目名称', '需求名称', '需求标题'], 200)
    if explicit_name:
        info['project_name'] = re.sub(
            r'^[#*_`\s]+|[#*_`\s]+$', '',
            _compact_text(explicit_name, 160),
        )
    patterns = [
        r'PRJ-\d+\s*[+＋\s]+\s*(.+?)(?:\n|$)',
        r'【泾渭云\d+】(.+?)(?:\n|$)',
        r'(?m)^#\s+(?!需求文档|提测文档|设计方案)(.+?)\s*$'
    ]
    if not info['project_name']:
        for pattern in patterns:
            m = re.search(pattern, doc_content)
            if m:
                info['project_name'] = re.sub(
                    r'^[#*_`\s]+|[#*_`\s]+$', '',
                    _compact_text(m.group(1), 160),
                )
                break

    info['background'] = _extract_markdown_section(
        doc_content, ['需求背景', '业务背景', '项目背景', '背景'], 1200
    )
    info['problem'] = _extract_markdown_section(
        doc_content, ['问题描述', '现状问题', '业务问题', '需求描述', '现状'], 1200
    )
    info['solution'] = _extract_markdown_section(
        doc_content, ['解决方案', '实现方案', '技术方案', '设计方案', '需求内容', '改造方案'], 1600
    )
    info['root_cause'] = _extract_markdown_section(
        doc_content, ['根因分析', '原因分析', '问题原因', '根因'], 1000
    )
    info['services'] = _extract_named_list(doc_content, ['涉及服务', '服务名称', '影响服务'])
    info['tables'] = _extract_named_list(doc_content, ['涉及表', '涉及数据表', '影响表', '表名'])
    info['interfaces'] = _extract_named_list(doc_content, ['涉及接口', '接口清单', '接口地址', 'API'])

    # 清单缺失时只补充具有稳定语法的对象，不用通用业务词推断。
    info['tables'] = _unique_text(info['tables'] + re.findall(
        r'\b(?:t_|tb_|biz_|sys_)[A-Za-z0-9_]{2,}\b', doc_content, re.IGNORECASE
    ))
    info['interfaces'] = _unique_text(info['interfaces'] + re.findall(
        r'\b(?:GET|POST|PUT|DELETE|PATCH)\s+(/[A-Za-z0-9_./{}\-]+)', doc_content, re.IGNORECASE
    ))

    # 定时任务
    job_matches = re.findall(r'(?:定时任务|JobHandler)[^\n]*?([A-Za-z_]+JobHandler)', doc_content)
    info['jobs'] = list(set(job_matches))

    # 开发分支
    info['branch'] = extract_branch_from_content(doc_content)

    # 风险评估
    m = re.search(r'风险[评估]?[：:\s]*([高中低])', doc_content)
    if m:
        info['risk_level'] = m.group(1)

    return info


def _resolve_commit_sha(repo_path, ref):
    try:
        result = run_git(repo_path, ['rev-parse', '--verify', f'{ref}^{{commit}}'], timeout=20, executor=subprocess.run)
        return result.stdout.strip() if result.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def _selection_cache_hash(selected_files):
    normalized = sorted(str(path) for path in (selected_files or []))
    return hashlib.sha256('\n'.join(normalized).encode('utf-8')).hexdigest()


def _get_code_analysis_cache_file(output_dir, repo_path, branch_sha, baseline_sha, selected_files=None):
    cache_source = f"{os.path.normpath(str(repo_path))}|{branch_sha}|{baseline_sha}|{_selection_cache_hash(selected_files)}"
    cache_key = hashlib.sha256(cache_source.encode('utf-8')).hexdigest()[:24]
    return _get_internal_dir(output_dir) / f'.code_analysis_cache_{cache_key}.json'


def _load_code_analysis_cache(cache_file, branch_sha, baseline_sha, selected_files=None):
    if not cache_file.exists():
        return None
    try:
        with open(cache_file, 'r', encoding='utf-8') as f:
            cache_data = json.load(f)
        if cache_data.get('schema_version') != '2.2.0':
            return None
        cache_time = cache_data.get('cache_time')
        if cache_time and (datetime.now() - datetime.fromisoformat(cache_time)).total_seconds() > 86400:
            return None
        if cache_data.get('branch_sha') != branch_sha or cache_data.get('baseline_sha') != baseline_sha:
            return None
        if cache_data.get('selection_hash') != _selection_cache_hash(selected_files):
            return None
        return cache_data.get('analysis_result')
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _save_code_analysis_cache(cache_file, repo_path, branch, baseline, branch_sha, baseline_sha, selected_files, analysis_result):
    cache_data = {
        'schema_version': '2.2.0', 'cache_time': datetime.now().isoformat(),
        'repo_path': str(repo_path), 'branch': branch, 'baseline': baseline,
        'branch_sha': branch_sha, 'baseline_sha': baseline_sha,
        'selection_hash': _selection_cache_hash(selected_files),
        'analysis_result': analysis_result,
    }
    with open(cache_file, 'w', encoding='utf-8') as f:
        json.dump(cache_data, f, ensure_ascii=False, indent=2)


def resolve_git_ref(repo_path, ref):
    """Return a git ref that exists locally, trying origin/<ref> when needed."""
    if not ref:
        return ref
    candidates = [ref]
    if not ref.startswith('origin/') and '/' not in ref.split('/')[0]:
        candidates.append(f'origin/{ref}')
    for candidate in candidates:
        try:
            result = run_git(
                repo_path, ['rev-parse', '--verify', candidate],
                timeout=10, executor=subprocess.run,
            )
            if result.returncode == 0:
                return candidate
        except Exception:
            continue
    return ref


def run_code_analysis(repo_path, branch, baseline, output_dir, selected_files=None, branch_sha='', baseline_sha='', business_logic_extractor=None):
    """Run extractors against an immutable selected-file snapshot and SHA-keyed cache."""
    extractor = business_logic_extractor or extract_business_logic
    if not extractor:
        return None
    selected_files = list(selected_files or [])
    branch_sha = branch_sha or _resolve_commit_sha(repo_path, branch)
    baseline_sha = baseline_sha or _resolve_commit_sha(repo_path, baseline)
    if not branch_sha or not baseline_sha:
        log_console('  ⚠️ 无法解析分支或基线 SHA，禁止使用代码分析缓存')
        return None
    cache_file = _get_code_analysis_cache_file(output_dir, repo_path, branch_sha, baseline_sha, selected_files)
    cached_result = _load_code_analysis_cache(cache_file, branch_sha, baseline_sha, selected_files)
    if cached_result:
        log_console('  💾 使用 SHA 一致的代码分析缓存')
        return cached_result
    log_console(f"  🔍 精准分析代码变更: {branch}({branch_sha[:12]}) vs {baseline}({baseline_sha[:12]})（{len(selected_files)} 个已选文件）")
    try:
        logic = extractor(repo_path, branch_sha, baseline_sha, selected_files=selected_files or None)
        result = {
            'files_changed': list(logic.get('file_changes', {}).keys()),
            'new_files': [f for f, s in logic.get('file_changes', {}).items() if s == 'A'],
            'modified_files': [f for f, s in logic.get('file_changes', {}).items() if s == 'M'],
            'scheduled_tasks': logic.get('scheduled_tasks', []),
            'api_endpoints': logic.get('api_endpoints', []),
            'sql_analysis': logic.get('sql_analysis', []),
            'call_chains': logic.get('call_chains', []),
            'output_layer': logic.get('output_layer', {}),
            'business_rules': logic.get('business_rules', {}),
            'config_values': logic.get('config_values', {}),
            'business_keywords': logic.get('business_keywords', []),
            'entry_points': logic.get('entry_points', []),
            'flow_diagram': logic.get('flow_diagram', ''),
            'technology_coverage': normalize_technology_coverage(
                logic.get('technology_coverage', {})
            ),
            'syntax_analysis': logic.get('syntax_analysis', []),
            'python_analysis': logic.get('python_analysis', []),
            'analysis_warnings': logic.get('analysis_warnings', []),
        }
        _save_code_analysis_cache(cache_file, repo_path, branch, baseline, branch_sha, baseline_sha, selected_files, result)
        log_console(f"  ✅ 代码分析完成: 已选文件 {len(result['files_changed'])} 个，API {len(result['api_endpoints'])} 个，SQL {len(result['sql_analysis'])} 个")
        coverage = result.get('technology_coverage') or {}
        if coverage.get('gaps'):
            log_console(f"  ⚠️ 分析覆盖缺口: {len(coverage['gaps'])} 个文件（详见 technology_coverage）")
        return result
    except CrossSkillDependencyError:
        # Dependency failures must retain exit 4 at the stage/CLI boundary.
        # Returning None would incorrectly turn a missing engine into an
        # ordinary analysis failure.
        raise
    except Exception as e:
        log_console(f"  ⚠️ 代码分析失败: {e}")
        return None

def _safe_len(value):
    if value is None:
        return 0
    if isinstance(value, dict):
        return len(value)
    if isinstance(value, (list, tuple, set)):
        return len(value)
    return 1 if value else 0


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    if isinstance(value, dict):
        return [{'key': k, 'value': v} for k, v in value.items()]
    return [value]


def normalize_technology_coverage(raw_coverage: Any) -> Dict[str, Any]:
    """将单目标字典或多目标列表统一为一个稳定的覆盖契约。"""
    if isinstance(raw_coverage, dict) and 'value' not in raw_coverage:
        result = dict(raw_coverage)
        result.setdefault('files', [])
        result.setdefault('gaps', [])
        result.setdefault('warnings', [])
        result.setdefault('targets', [])
        result.setdefault('status_counts', {})
        result.setdefault('language_counts', {})
        result.setdefault('applicable_source_files', len(result.get('files', [])))
        result.setdefault('fully_analyzed_files', 0)
        result.setdefault('total_changed_files', len(result.get('files', [])))
        result.setdefault(
            'complete',
            bool(result.get('applicable_source_files')) and not result.get('gaps'),
        )
        return result

    entries = []
    for wrapper in _as_list(raw_coverage):
        if isinstance(wrapper, dict) and isinstance(wrapper.get('value'), dict):
            entries.append((wrapper.get('target_id', 'unknown'), wrapper['value']))
        elif isinstance(wrapper, dict):
            entries.append((wrapper.get('target_id', 'unknown'), wrapper))

    files, gaps, warnings, targets = [], [], [], []
    status_counts, language_counts = {}, {}
    for target_id, item in entries:
        normalized = normalize_technology_coverage(item)
        targets.append({'target_id': target_id, **normalized})
        for record in normalized.get('files', []):
            tagged = dict(record) if isinstance(record, dict) else {'file': str(record)}
            tagged.setdefault('target_id', target_id)
            files.append(tagged)
        for record in normalized.get('gaps', []):
            tagged = dict(record) if isinstance(record, dict) else {'detail': str(record)}
            tagged.setdefault('target_id', target_id)
            gaps.append(tagged)
        warnings.extend(f'[{target_id}] {value}' for value in normalized.get('warnings', []))
        for key, value in normalized.get('status_counts', {}).items():
            status_counts[key] = status_counts.get(key, 0) + value
        for key, value in normalized.get('language_counts', {}).items():
            language_counts[key] = language_counts.get(key, 0) + value

    return {
        'files': files,
        'targets': targets,
        'total_changed_files': sum(item.get('total_changed_files', 0) for _, item in entries),
        'applicable_source_files': sum(item.get('applicable_source_files', 0) for _, item in entries),
        'fully_analyzed_files': sum(item.get('fully_analyzed_files', 0) for _, item in entries),
        'complete': bool(entries) and all(bool(item.get('complete')) for _, item in entries),
        'status_counts': status_counts,
        'language_counts': language_counts,
        'gaps': gaps,
        'warnings': list(dict.fromkeys(warnings)),
    }


def _output_layers(code_analysis):
    analysis = code_analysis or {}
    layers = analysis.get('output_layers') or []
    if analysis.get('output_layer'):
        layers = [*layers, analysis['output_layer']]
    return [layer for layer in layers if isinstance(layer, dict)]


def build_requirement_facts(project_info, doc_content, code_analysis=None, extracted_data=None):
    """在分析阶段生成权威需求事实；测试设计阶段只能扩充，不能另起事实源。"""
    extracted_data = extracted_data or {}
    source_path = '.ai-internal/normalized_requirement.md'
    items = []

    def add(dimension, title, content, confidence='high', source=source_path):
        if content in (None, '', []) or _is_absence_marker(content):
            return
        stable_key = _structured_fact_key(dimension, title, content)
        comparable = dimension not in {'rules', 'services', 'code_files'}
        items.append({
            'fact_id': _stable_id('REQ', f'{dimension}:{title}:{content}', len(items)),
            'dimension': dimension,
            'type': '代码证据' if source.startswith('code_analysis') else '明确需求',
            'title': title,
            'content': content,
            'source': source,
            'confidence': confidence,
            'stable_key': stable_key,
            'comparable': comparable,
        })

    for key, title in (
        ('project_name', '项目名称'), ('background', '需求背景'), ('problem', '问题描述'),
        ('root_cause', '根因分析'), ('solution', '解决方案'),
    ):
        add('rules', title, project_info.get(key))
    for key, dimension in (
        ('services', 'services'), ('tables', 'tables'),
        ('interfaces', 'apis'), ('jobs', 'jobs'),
    ):
        for value in project_info.get(key, []) or []:
            add(dimension, key, value)
    for table in extracted_data.get('tables', []) or []:
        add('tables', '结构化提取表', table)
    for config in extracted_data.get('configs', []) or []:
        add('configs', '配置项', config)
    for status in extracted_data.get('status_codes', []) or []:
        add('errors', '状态码', status)
    known_endpoints = [
        item.get('endpoint', '')
        for layer in _output_layers(code_analysis)
        for item in layer.get('endpoints', [])
    ]
    for field in extract_explicit_output_fields(doc_content, known_endpoints):
        endpoint = field['endpoint']
        add('fields', '明确输出字段', f"{endpoint}.{field['field']}" if endpoint else field['field'],
            'high' if endpoint else 'medium')
        if not field['comparable']:
            items[-1]['comparable'] = False
    for file_name in (code_analysis or {}).get('files_changed', [])[:80]:
        add('code_files', '代码变更文件', file_name, 'medium', 'code_analysis.files_changed')

    dimensions = {}
    for item in items:
        if not item.get('comparable'):
            continue
        dimensions.setdefault(item['dimension'], {})[item['stable_key']] = {
            'content': item['content'], 'source': item['source'],
        }
    if 'fields' in dimensions:
        dimensions['fields'] = {
            'schema_version': '1.1.0', 'items': dimensions['fields'],
            'coverage_status': 'partial', 'comparison_policy': 'identity',
            'source': 'explicit_output_fields',
        }
    return {'items': items, **dimensions}


def assess_requirement_facts(project_info, facts_payload):
    """核心需求字段为空时形成硬门禁，禁止输出带“待补充”的通过报告。"""
    scope_values = sum(
        len(project_info.get(key, []) or [])
        for key in ('services', 'tables', 'interfaces', 'jobs')
    )
    code_scope = sum(
        1 for item in facts_payload.get('items', [])
        if item.get('dimension') == 'code_files'
    )
    checks = [
        ('Q1', '项目标识', bool(project_info.get('project_name')), '项目名称/需求标题'),
        ('Q2', '业务背景或问题', bool(project_info.get('background') or project_info.get('problem')), '需求背景、问题描述或现状'),
        ('Q3', '解决方案', bool(project_info.get('solution')), '解决方案、实现方案、技术方案或需求内容'),
        ('Q4', '影响范围', bool(scope_values or code_scope), '服务、表、接口、任务或已核验代码变更'),
    ]
    gates = [{
        'gate_id': gate_id,
        'name': name,
        'status': 'passed' if passed else 'failed',
        'blocking': not passed,
        'message': f"{'已识别' if passed else '未识别'}：{expected}",
    } for gate_id, name, passed, expected in checks]
    blocking = [item for item in gates if item['status'] == 'failed']
    return {
        'summary': {
            'status': 'blocked' if blocking else 'passed',
            'blocking_count': len(blocking),
            'fact_count': len(facts_payload.get('items', [])),
        },
        'gates': gates,
    }


def _detect_codegraph_index(repo_path):
    if not repo_path:
        return {'available': False, 'path': '', 'note': '未配置代码仓库'}
    candidates = [Path(repo_path) / '.codegraph' / 'codegraph.db', Path(repo_path) / '.codegraph']
    for candidate in candidates:
        if candidate.exists():
            return {'available': True, 'path': str(candidate), 'note': '检测到 codegraph 索引，可用于后续影响选择加速'}
    return {'available': False, 'path': '', 'note': '未检测到 codegraph 索引，当前使用已有代码分析结果降级生成影响图'}


def _classify_impact_confidence(code_analysis):
    if not code_analysis:
        return "none"
    coverage = normalize_technology_coverage(
        code_analysis.get("technology_coverage") or {}
    )
    has_coverage_gap = bool(coverage.get("gaps")) or coverage.get("complete") is False
    strong = (
        _safe_len(code_analysis.get("entry_points"))
        + _safe_len(code_analysis.get("api_endpoints"))
        + _safe_len(code_analysis.get("sql_analysis"))
    )
    if has_coverage_gap:
        return "medium" if strong >= 3 else "low"
    if strong >= 3:
        return "high"
    if code_analysis.get("files_changed") or code_analysis.get("business_rules") or code_analysis.get("config_values"):
        return "medium"
    return "low"

def _collect_precise_impact(target_results: List[Dict[str, Any]]) -> Dict[str, List[Any]]:
    """Flatten per-target selection evidence for the workflow-wide impact contract."""
    fields = ('raw_changed_files', 'candidate_files', 'selected_files', 'related_test_files', 'excluded_files', 'selection_evidence')
    merged = {field: [] for field in fields}
    for target in target_results or []:
        target_id = target.get('target_id', 'unknown')
        for field in fields:
            for item in target.get(field, []) or []:
                value = dict(item) if isinstance(item, dict) else {'path': str(item)}
                value.setdefault('target_id', target_id)
                merged[field].append(value)
    return merged
def generate_code_impact_json(output_dir, repo_path, branch, baseline, code_analysis, status='completed', skip_reason='', target_results=None, manifest_errors=None):
    """生成代码影响选择底座，供测试设计、深度模式和回归范围复用。"""
    internal_dir = _get_internal_dir(output_dir)
    impact_path = internal_dir / 'code_impact.json'
    code_analysis = code_analysis or {}
    codegraph_index = _detect_codegraph_index(repo_path)
    files_changed = _as_list(code_analysis.get('files_changed'))
    target_results = target_results or []
    manifest_errors = manifest_errors or []
    precision = _collect_precise_impact(target_results)
    technology_coverage = normalize_technology_coverage(
        code_analysis.get("technology_coverage") or {}
    )
    payload = {
        'schema_version': '1.3.0',
        'workflow_version': 'testcase-generator-2026.07',
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'status': status,
        'skip_reason': skip_reason,
        'repo_path': str(repo_path) if repo_path else '',
        'branch': branch or '',
        'baseline': baseline or '',
        'baseline_fallback_risk': any(target.get('baseline_fallback_risk') for target in target_results),
        'comparison_evidence': [{key: target.get(key, '') for key in ('target_id', 'baseline_sha', 'branch_sha', 'merge_base_sha', 'comparison_command')} for target in target_results],
        'freshness_evidence': [{'target_id': target.get('target_id', 'unknown'), **(target.get('freshness') or {})} for target in target_results],
        'raw_changed_files': precision['raw_changed_files'],
        'candidate_files': precision['candidate_files'],
        'selected_files': precision['selected_files'],
        'related_test_files': precision['related_test_files'],
        'excluded_files': precision['excluded_files'],
        'selection_evidence': precision['selection_evidence'],
        'analysis_strategy': 'precise_impact_selector' if target_results else ('existing_code_analyzer' if code_analysis else 'skipped'),
        'fallback_strategy': 'codegraph_unavailable_or_not_invoked' if not codegraph_index.get('available') else 'codegraph_available_not_required',
        'codegraph_index': codegraph_index,
        'impact_confidence': _classify_impact_confidence(code_analysis),
        'summary': {
            'raw_changed_count': len(precision['raw_changed_files']),
            'candidate_count': len(precision['candidate_files']),
            'selected_count': len(precision['selected_files']),
            'excluded_count': len(precision['excluded_files']),
            'files_changed': len(files_changed),
            'new_files': _safe_len(code_analysis.get('new_files')),
            'modified_files': _safe_len(code_analysis.get('modified_files')),
            'entry_points': _safe_len(code_analysis.get('entry_points')),
            'api_endpoints': _safe_len(code_analysis.get('api_endpoints')),
            'scheduled_tasks': _safe_len(code_analysis.get('scheduled_tasks')),
            'sql_analysis': _safe_len(code_analysis.get('sql_analysis')),
            'verified_output_fields': sum(len(layer.get('verified_fields') or []) for layer in _output_layers(code_analysis)),
            'business_rules': _safe_len(code_analysis.get('business_rules')),
            'config_values': _safe_len(code_analysis.get('config_values')),
            'applicable_source_files': technology_coverage.get('applicable_source_files', 0),
            'fully_analyzed_files': technology_coverage.get('fully_analyzed_files', 0),
            'coverage_gap_files': len(technology_coverage.get('gaps', [])),
        },
        'impact_scope': {
            'files_changed': files_changed[:80],
            'entry_points': _as_list(code_analysis.get('entry_points'))[:30],
            'api_endpoints': _as_list(code_analysis.get('api_endpoints'))[:50],
            'scheduled_tasks': _as_list(code_analysis.get('scheduled_tasks'))[:30],
            'sql_analysis': _as_list(code_analysis.get('sql_analysis'))[:50],
            'output_layers': _output_layers(code_analysis)[:20],
            'business_rules': _as_list(code_analysis.get('business_rules'))[:50],
            'config_values': _as_list(code_analysis.get('config_values'))[:50],
            'business_keywords': _as_list(code_analysis.get('business_keywords'))[:50],
            'technology_coverage': technology_coverage,
            'syntax_analysis': _as_list(code_analysis.get('syntax_analysis'))[:50],
            'python_analysis': _as_list(code_analysis.get('python_analysis'))[:50],
        },
        'test_design_hints': [],
        'unconfirmed_evidence': [],
    }
    if target_results:
        payload['code_targets'] = [{
            key: value for key, value in target.items() if key != 'analysis'
        } for target in target_results]
        payload['target_summary'] = {
            'total': len(target_results),
            'completed': sum(1 for target in target_results if target.get('status') == 'completed'),
            'completed_stale': sum(1 for target in target_results if target.get('status') == 'completed_stale'),
            'blocked': sum(1 for target in target_results if target.get('status') == 'blocked'),
            'latest_unverified': sum(1 for target in target_results if target.get('status') == 'latest_unverified'),
        }
    if payload['summary']['scheduled_tasks']:
        payload['test_design_hints'].append('命中定时任务，测试设计需覆盖触发时机、重复执行、失败重试和幂等')
    if payload['summary']['api_endpoints']:
        payload['test_design_hints'].append('命中接口入口，测试设计需覆盖参数、鉴权、异常返回和上下游契约')
    if payload['summary']['verified_output_fields']:
        payload['test_design_hints'].append('命中接口输出字段赋值证据，测试设计需核对字段取值、空值和序列化结果')
    if payload['summary']['sql_analysis']:
        payload['test_design_hints'].append('命中 SQL/数据写入，测试设计需覆盖数据一致性、事务边界和回滚')
    if status != 'completed':
        payload['unconfirmed_evidence'].append(skip_reason or '代码分析未完成，代码影响证据缺失')
    payload['unconfirmed_evidence'].extend(manifest_errors)
    payload['unconfirmed_evidence'].extend(_as_list(code_analysis.get('analysis_warnings')))
    payload['unconfirmed_evidence'].extend(
        f"{layer.get('target_id', '代码目标')}：{warning}"
        for layer in _output_layers(code_analysis)
        for warning in layer.get('warnings', [])
    )
    payload['unconfirmed_evidence'].extend(
        f"{target.get('target_id', 'unknown')}：{target.get('block_reason')}"
        for target in target_results if target.get('status') == 'blocked' and target.get('block_reason')
    )
    with open(impact_path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log_console(f"  📄 代码影响范围: {impact_path}")
    return impact_path


def _target_value(target_id: str, value: Any) -> Any:
    if isinstance(value, str):
        return f'[{target_id}] {value}'
    return {'target_id': target_id, 'value': value}


def aggregate_code_target_results(target_results: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], str, str]:
    """汇总各目标证据，同时保留 target_id，供旧版下游继续消费。"""
    aggregate = {
        'files_changed': [], 'new_files': [], 'modified_files': [], 'scheduled_tasks': [],
        'api_endpoints': [], 'sql_analysis': [], 'business_rules': [], 'config_values': [],
        'business_keywords': [], 'entry_points': [], 'flow_diagram': '',
        'call_chains': [], 'output_layers': [],
        'technology_coverage': {}, 'syntax_analysis': [], 'python_analysis': [], 'analysis_warnings': [],
    }
    completed = [target for target in target_results if target.get('status') in {'completed', 'completed_stale'} and target.get('analysis')]
    coverage_by_target = []
    for target in completed:
        analysis, target_id = target['analysis'], target['target_id']
        for key in ('files_changed', 'new_files', 'modified_files'):
            aggregate[key].extend([f'[{target_id}] {value}' for value in _as_list(analysis.get(key))])
        for key in ('scheduled_tasks', 'api_endpoints', 'sql_analysis', 'business_keywords', 'entry_points', 'call_chains', 'syntax_analysis', 'python_analysis', 'analysis_warnings'):
            aggregate[key].extend([_target_value(target_id, value) for value in _as_list(analysis.get(key))])
        if analysis.get('output_layer'):
            aggregate['output_layers'].append({'target_id': target_id, **analysis['output_layer']})
        if analysis.get('technology_coverage'):
            coverage_by_target.append(_target_value(target_id, analysis.get('technology_coverage')))
        if analysis.get('business_rules'):
            aggregate['business_rules'].append(_target_value(target_id, analysis.get('business_rules')))
        if analysis.get('config_values'):
            aggregate['config_values'].append(_target_value(target_id, analysis.get('config_values')))
    aggregate['technology_coverage'] = normalize_technology_coverage(coverage_by_target)
    if not target_results:
        return aggregate, 'skipped', '未发现可分析的代码目标'
    latest_unverified = [target for target in target_results if target.get('status') == 'latest_unverified']
    if latest_unverified:
        reasons = [target.get('block_reason') for target in latest_unverified if target.get('block_reason')]
        return aggregate, 'latest_unverified', '；'.join(reasons) or '远程最新性未验证，禁止完整分析'
    completed_stale = [target for target in completed if target.get('status') == 'completed_stale']
    if len(completed) == len(target_results):
        if completed_stale:
            return aggregate, 'completed_stale', '远程不可达，基于已缓存的远程提交快照完成分析，最新性未验证'
        return aggregate, 'completed', ''
    if completed:
        return aggregate, 'partial', '部分代码目标未通过预检，聚合结果仅包含已完成目标'
    reasons = [target.get('block_reason') for target in target_results if target.get('block_reason')]
    return aggregate, 'blocked', '；'.join(reasons) or '所有代码目标均未通过预检'


def write_code_target_results(output_dir: Path, target_results: List[Dict[str, Any]], manifest_errors: List[str]) -> Path:
    """保存可追溯的目标清单和分目标代码证据。"""
    internal_dir = _get_internal_dir(output_dir)
    target_dir = internal_dir / 'code_impacts'
    target_dir.mkdir(parents=True, exist_ok=True)
    for target in target_results:
        with open(target_dir / f"{target.get('target_id', 'unknown')}.json", 'w', encoding='utf-8') as f:
            json.dump(target, f, ensure_ascii=False, indent=2)
    manifest_path = internal_dir / 'code_targets.json'
    with open(manifest_path, 'w', encoding='utf-8') as f:
        json.dump({
            'schema_version': '1.0.0', 'generated_at': datetime.now().isoformat(timespec='seconds'),
            'manifest_errors': manifest_errors, 'targets': target_results,
        }, f, ensure_ascii=False, indent=2)
    log_console(f"  📄 代码目标清单: {manifest_path}")
    return manifest_path

def _public_evidence_text(value: Any, limit: int = 180) -> str:
    """把内部结构压缩为用户可读的一行证据，避免整段 JSON 泄漏到报告。"""
    if isinstance(value, dict) and 'value' in value and 'target_id' in value:
        return f"[{value['target_id']}] {_public_evidence_text(value['value'], limit)}"
    if isinstance(value, dict):
        preferred = [
            value.get(key) for key in ('name', 'path', 'file', 'title', 'method', 'endpoint')
            if value.get(key)
        ]
        if preferred:
            return _compact_text(' | '.join(map(str, preferred)), limit)
    return _compact_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True)
        if isinstance(value, (dict, list)) else value,
        limit,
    )


def _markdown_bullets(values, empty='- 无明确证据', limit=20):
    lines = [
        f"- {_public_evidence_text(value)}"
        for value in _as_list(values)[:limit]
        if _public_evidence_text(value)
    ]
    return '\n'.join(lines) or empty


def _unique_identifiers(values: List[Any]) -> List[str]:
    """Deduplicate stable identifiers without stripping underscores or dots."""
    result, seen = [], set()
    for value in values:
        text = re.sub(r'\s+', ' ', str(value or '')).strip().strip('`"\'')
        key = text.casefold()
        if text and key not in seen:
            result.append(text)
            seen.add(key)
    return result

def _comparison_coverage_status(code_analysis: Dict[str, Any]) -> str:
    raw_coverage = (code_analysis or {}).get('technology_coverage')
    if not raw_coverage:
        return 'unknown'
    coverage = normalize_technology_coverage(raw_coverage)
    return 'complete' if coverage.get('complete') else 'partial'


def _comparison_dimension(
    name: str,
    items: List[Any],
    code_analysis: Dict[str, Any],
    comparison_policy: Optional[str] = None,
) -> Dict[str, Any]:
    identity_dimensions = {'apis', 'tables', 'jobs', 'actors'}
    return {
        'schema_version': '1.1.0',
        'items': items,
        'coverage_status': _comparison_coverage_status(code_analysis),
        'comparison_policy': comparison_policy or (
            'identity' if name in identity_dimensions else 'value'
        ),
        'source': name,
    }


def build_code_evidence(code_analysis):
    """将代码分析结果映射到带覆盖状态的结构化比较维度。"""
    code_analysis = code_analysis or {}

    def values(field, preferred_keys=()):
        result = []
        for item in _as_list(code_analysis.get(field)):
            if isinstance(item, dict) and 'value' in item and 'target_id' in item:
                item = item['value']
            if isinstance(item, dict):
                value = str(next((item.get(key) for key in preferred_keys if item.get(key)), None) or _public_evidence_text(item))
                if _is_safe_structured_key(value):
                    result.append({'name': value, 'source': field})
            elif item not in (None, ''):
                value = str(item)
                if _is_safe_structured_key(value):
                    result.append({'name': value, 'source': field})
        return result

    tables = []
    for sql_entry in code_analysis.get('sql_analysis', []) or []:
        if isinstance(sql_entry, dict) and 'value' in sql_entry and 'target_id' in sql_entry:
            sql_entry = sql_entry['value']
        if not isinstance(sql_entry, dict):
            continue
        analysis = sql_entry.get('analysis', sql_entry)
        if not isinstance(analysis, dict):
            continue
        table_data = analysis.get('tables') or {}
        if isinstance(table_data, dict):
            tables.extend(table_data.get('sources', []) or [])
            tables.extend(table_data.get('targets', []) or [])
        elif isinstance(table_data, list):
            tables.extend(table_data)

    sql_text = json.dumps(code_analysis.get('sql_analysis', []), ensure_ascii=False)
    tables.extend(re.findall(
        r'(?i)\b(?:from|join|into|update|table)\s+([A-Za-z_][A-Za-z0-9_.$#]*)',
        sql_text,
    ))
    tables = _unique_identifiers(tables)
    config_text = json.dumps(code_analysis.get('config_values', {}), ensure_ascii=False)
    configs = _unique_text(re.findall(
        r'(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+){2,})',
        config_text,
    ))
    output_fields = []
    for layer in _output_layers(code_analysis):
        for field in layer.get('verified_fields', []):
            key = field.get('field_key')
            if key and _is_safe_structured_key(key):
                output_fields.append({
                    'stable_key': key,
                    'evidence': (
                        (f"{field.get('mapper_file')}:{field.get('mapper_line')} -> "
                         if field.get('mapper_file') else '')
                        + f"{field.get('mapping_file')}:{field.get('mapping_line')} -> "
                        + f"{field.get('file')}:{field.get('line')}"
                    ),
                    'source_expression': field.get('source_expression', ''),
                    'source_column': field.get('source_column', ''),
                })
    candidates = {
        'apis': values('api_endpoints', ('name', 'path', 'endpoint')),
        'tables': [{'name': value, 'source': 'sql_analysis'} for value in tables],
        'jobs': values('scheduled_tasks', ('name', 'method')),
        'configs': [{'name': value, 'source': 'config_values'} for value in configs],
        'rules': values('business_rules', ('title', 'name', 'key')),
        'errors': values('analysis_warnings'),
        'fields': output_fields,
    }
    # 空维度必须省略；存在的维度携带覆盖状态，partial/unknown 不得产生假缺失。
    evidence = {
        key: _comparison_dimension(key, value, code_analysis, 'identity' if key == 'fields' else None)
        for key, value in candidates.items()
        if value
    }
    if 'fields' in evidence:
        evidence['fields']['coverage_status'] = 'partial'
    return evidence

def generate_requirement_code_comparison(
    output_dir, requirement_facts, code_analysis, code_evidence_builder=None,
):
    """保存需求事实与代码证据的同一份比较结果，供分析、评审和测试设计复用。"""
    if not code_analysis:
        result = {
            'schema_version': '1.1.0',
            'status': 'skipped',
            'skip_reason': '没有已完成的代码分析，未执行需求代码比较',
            'summary': {'总计': 0},
            'items': [],
            'warnings': ['没有已完成的代码分析，未执行需求代码比较'],
        }
    else:
        if not compare_structured:
            raise RuntimeError('代码分析已完成，但需求代码比较器不可用')
        evidence_builder = code_evidence_builder or build_code_evidence
        result = compare_structured(requirement_facts, evidence_builder(code_analysis))
        result['status'] = 'completed'
    path = _write_internal_json(output_dir, 'requirement_code_comparison.json', result)
    return result, path


def _comparison_markdown(comparison):
    if comparison.get('status') == 'skipped':
        return '- 未执行需求代码比较：没有已完成的代码分析。'
    groups = {'一致': [], '缺失': [], '不一致': [], '额外实现': [], '证据不足': []}
    for item in comparison.get('items', []):
        groups.setdefault(item.get('classification', '证据不足'), []).append(item)
    lines = []
    for label in ('不一致', '缺失', '额外实现', '证据不足', '一致'):
        lines.extend(['', f"### {label}", ''])
        values = groups.get(label, [])
        if not values:
            lines.append('- 无')
            continue
        for item in values[:30]:
            key = str(item.get('key', '')).strip()
            if not _is_safe_structured_key(key):
                digest = hashlib.sha256(key.encode('utf-8')).hexdigest()[:8].upper()
                key = f"未命名结构项-{digest}"
            lines.append(
                f"- `{item.get('dimension', '')}.{key}`："
                f"{item.get('reason', '')}"
            )
    return '\n'.join(lines)


def _output_layer_markdown(code_analysis):
    layers = _output_layers(code_analysis)
    if not layers:
        return '- 本次没有可用的输出层代码证据。'
    lines = []
    for layer in layers:
        target = layer.get('target_id', '代码目标')
        confirmed = layer.get('verified_fields') or []
        declared = layer.get('declared_fields') or []
        for field in confirmed[:30]:
            lines.append(
                f"- **已定位赋值** [{target}] {field.get('field_key')}: "
                f"{field.get('source_expression') or '取值待确认'} "
                f"{'；SQL 列 ' + field['source_column'] if field.get('source_column') else ''}"
                f"（{field.get('mapper_file') + ':' + str(field.get('mapper_line')) + ' → ' if field.get('mapper_file') else ''}"
                f"{field.get('mapping_file')}:{field.get('mapping_line')} → "
                f"{field.get('file')}:{field.get('line')}）"
            )
        confirmed_keys = {field.get('field_key') for field in confirmed}
        for field in declared[:30]:
            if field.get('field_key') not in confirmed_keys:
                lines.append(
                    f"- **仅见 DTO 声明，取值待追踪** [{target}] "
                    f"{field.get('field_key')}（{field.get('file')}:{field.get('line')}）"
                )
        for query in (layer.get('sql_results') or [])[:10]:
            for mapping in (query.get('field_mappings') or [])[:20]:
                lines.append(
                    f"- **SQL 结果映射，接口传递待追踪** [{target}] "
                    f"{query.get('id')}: {mapping.get('column')} → {mapping.get('field')} "
                    f"（{mapping.get('file')}:{mapping.get('line')}）"
                )
        for warning in (layer.get('warnings') or [])[:10]:
            lines.append(f"- **追踪缺口** [{target}] {warning}")
    return '\n'.join(lines) if lines else '- 已检查变更文件，未定位到明确输出字段赋值；结果保持证据不足。'


def generate_requirement_analysis_md(
    output_dir, project_info, code_analysis, doc_content,
    requirement_facts=None, quality_gate=None, comparison=None,
):
    """生成只包含已验证字段和结构化对比结论的需求分析报告。"""
    report_path = output_dir / '需求分析.md'
    quality_gate = quality_gate or {}
    comparison = comparison or {}
    output_summary = _output_layer_markdown(code_analysis)

    def section(value):
        return str(value).strip() if value else '文档未单独说明'

    content = f"""# 需求分析报告

## 1. 项目信息

- **项目名称**：{project_info.get('project_name', '')}
- **开发分支**：{project_info.get('branch') or '未获得可验证分支'}
- **风险评估**：{project_info.get('risk_level', '中')}风险
- **分析质量门禁**：{quality_gate.get('summary', {}).get('status', 'unknown')}

## 2. 业务背景与问题

### 需求背景

{section(project_info.get('background'))}

### 问题描述

{section(project_info.get('problem'))}

### 根因分析

{section(project_info.get('root_cause'))}

## 3. 解决方案

{section(project_info.get('solution'))}

## 4. 影响范围

### 服务
{_markdown_bullets(project_info.get('services'))}

### 表
{_markdown_bullets(project_info.get('tables'))}

### 接口
{_markdown_bullets(project_info.get('interfaces'))}

### 定时任务
{_markdown_bullets(project_info.get('jobs'))}

## 5. 代码变更摘要

| 统计项 | 数量 |
|---|---:|
| 文件变更 | {len(code_analysis.get('files_changed', []))} |
| 新增文件 | {len(code_analysis.get('new_files', []))} |
| 修改文件 | {len(code_analysis.get('modified_files', []))} |
| API 端点 | {len(code_analysis.get('api_endpoints', []))} |
| SQL 证据 | {len(code_analysis.get('sql_analysis', []))} |
| 已确认的输出字段赋值 | {sum(len(layer.get('verified_fields') or []) for layer in _output_layers(code_analysis))} |

### 输出层证据

{output_summary}

## 6. 需求—代码结构化对比
{_comparison_markdown(comparison)}

## 7. 证据位置

- 需求事实：`.ai-internal/requirement_facts.json`
- 分析质量门禁：`.ai-internal/analysis_quality_gate.json`
- 代码影响：`.ai-internal/code_impact.json`
- 需求代码对比：`.ai-internal/requirement_code_comparison.json`

---

*生成日期：{datetime.now().strftime('%Y年%m月%d日')}*
"""
    report_path.write_text(content, encoding='utf-8')
    log_console(f"  📄 需求分析.md: {report_path}")
    return report_path


def generate_code_review_report(output_dir, project_info, code_analysis, code_impact=None, comparison=None):
    """生成测试人员版代码评审报告；技术结构只保留在内部 JSON。"""
    from code_review_report import write_code_review_report

    report_path, _, _ = write_code_review_report(
        output_dir,
        project_info or {},
        code_analysis or {},
        code_impact or {},
        comparison or {},
    )
    log_console(f"  📄 代码评审报告.md: {report_path}")
    return report_path

def _normalized_requirement_path(output_dir: Path) -> Path:
    return _get_internal_dir(Path(output_dir)) / 'normalized_requirement.md'


def _write_normalized_requirement(output_dir: Path, content: str) -> Dict[str, Any]:
    """Persist the only requirement-version input allowed for downstream test design."""
    normalized = extract_latest_requirement(content).strip()
    if not normalized:
        raise RuntimeError('当前需求版本裁剪后为空，禁止继续生成评审或测试产物')
    path = _normalized_requirement_path(output_dir)
    path.write_text(normalized + '\n', encoding='utf-8')
    return {
        'path': str(path),
        'source_version': 'latest_before_delimiter' if '分割线' in content else 'single_version',
        'source_sha256': hashlib.sha256(normalized.encode('utf-8')).hexdigest(),
        'character_count': len(normalized),
    }


def _read_normalized_requirement(output_dir: Path) -> Tuple[str, Dict[str, Any]]:
    path = _normalized_requirement_path(output_dir)
    if not path.exists():
        raise RuntimeError(f'缺少当前需求标准输入：{path}；禁止使用完整历史 Markdown 生成下游产物')
    content = path.read_text(encoding='utf-8').strip()
    if not content:
        raise RuntimeError('normalized_requirement.md 为空，禁止继续生成下游产物')
    return content, {
        'path': str(path), 'source_version': 'latest_before_delimiter',
        'source_sha256': hashlib.sha256(content.encode('utf-8')).hexdigest(),
        'character_count': len(content),
    }


def _validate_testpoint_source_consistency(output_dir: Path, project_info: Dict[str, Any], source_meta: Dict[str, Any], testpoints: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Reject testpoints not traceable to the normalized current requirement."""
    expected_title = str(project_info.get('project_name') or '').strip()
    issues = []
    for index, point in enumerate(testpoints, 1):
        point['source_version'] = source_meta['source_version']
        point['source_requirement_sha256'] = source_meta['source_sha256']
        point['source_requirement_title'] = expected_title
        if point.get('source_version') != source_meta['source_version']:
            issues.append(f'测试点{index}来源版本不一致')
        if point.get('source_requirement_sha256') != source_meta['source_sha256']:
            issues.append(f'测试点{index}来源摘要不一致')
    report = {
        'status': 'passed' if not issues else 'failed', 'source': source_meta,
        'expected_requirement_title': expected_title, 'testpoint_count': len(testpoints), 'issues': issues,
    }
    _write_internal_json(output_dir, 'source_consistency_gate.json', report)
    if issues:
        raise RuntimeError('来源一致性门禁未通过：' + '；'.join(issues[:5]))
    return report

def run_requirement_analysis(
    req_dir, output_dir, repo_path=None, branch=None, baseline='origin/master', kb_manager=None,
    repo_config=None, code_targets_path=None, force_legacy=False, analysis_dependencies=None,
):
    """
    步骤②：需求分析

    输入：需求目录
    输出：需求分析.md + 代码评审报告.md
    """
    dependencies = dict(analysis_dependencies or {})
    build_code_targets_fn = dependencies.get('build_code_targets') or build_code_targets
    preflight_code_target_fn = dependencies.get('preflight_code_target') or preflight_code_target
    build_requirement_facts_fn = dependencies.get('build_requirement_facts') or build_requirement_facts
    build_code_evidence_fn = dependencies.get('build_code_evidence') or build_code_evidence
    log_console("\n" + "=" * 60)
    log_console("📖 步骤② 需求分析")
    log_console("=" * 60)

    # 读取华库（学习高频根因）
    if kb_manager:
        essence_dir = kb_manager.kb_path / 'patterns' / '精华库'
        root_causes_file = essence_dir / 'common_root_causes.md'
        if root_causes_file.exists():
            log_console("  📚 华库学习: common_root_causes.md")

    # 读取多文档
    req_docs, test_docs, design_docs, interface_docs = categorize_documents(
        iter_source_markdown_files(req_dir), req_dir
    )

    all_docs = req_docs + design_docs + interface_docs + test_docs
    doc_content = read_all_docs(all_docs)

    # 版本裁剪在分析阶段唯一执行；下游只能消费该标准输入。
    latest_content = extract_latest_requirement(doc_content)
    log_console(f"  ✅ 读取文档内容: {len(latest_content)} 字符")
    source_meta = _write_normalized_requirement(output_dir, doc_content)
    log_console(f"  📌 当前需求标准输入: {source_meta['path']}")
    # 提取项目信息
    project_info = extract_project_info(latest_content)
    # 需求包目录是工作流选择范围和产物归属的权威标识。需求正文可能是一个
    # 聚合页面（例如“纳税报告取数优化”），不能覆盖当前具体需求包名称。
    document_title = project_info.get('project_name', '')
    project_info['document_title'] = document_title
    project_info['project_name'] = req_dir.name.strip()
    project_info['project_name_source'] = 'requirement_package_directory'
    project_info['source_version'] = source_meta['source_version']
    project_info['source_requirement_sha256'] = source_meta['source_sha256']
    if not project_info.get('branch'):
        project_info['branch'] = extract_branch_from_content(doc_content)
    log_console(f"  📋 项目名称: {project_info.get('project_name') or '未识别'}")

    # 代码分析：先建立分目标清单，绝不将一个文档的分支回退到另一个默认仓库。
    effective_repo_config = dict(repo_config or {})
    if repo_path and not effective_repo_config.get('path'):
        effective_repo_config['path'] = repo_path
    if baseline and not effective_repo_config.get('baseline'):
        effective_repo_config['baseline'] = baseline
    target_results, manifest_errors = build_code_targets_fn(
        test_docs, req_dir, effective_repo_config, code_targets_path, force_legacy,
        repo_path, branch, baseline,
    )
    # 精准影响选择：Git 原始差异只作为候选池，必须经过需求锚点和关联证据筛选。
    requirement_anchors = extract_requirement_anchors(latest_content, project_info)
    for index, target in enumerate(target_results):
        target = preflight_code_target_fn(target)
        if target.get('status') in {'verified', 'verified_stale'}:
            selection = select_impact_files(
                target['repo_path'], target.get('branch_sha') or target['branch'], target.get('raw_changed_files', []), requirement_anchors,
            )
            target.update(requirement_anchors=requirement_anchors, **selection)
            selected_paths = [item['path'] for item in selection['selected_files']]
            if not selected_paths:
                target.update(status='blocked', block_reason='精准影响选择未命中具有需求或调用链证据的 P1/P2 文件')
            elif not extract_business_logic:
                target.update(status='blocked', block_reason='requirement-code-auditor 未加载，禁止生成代码影响结论')
            else:
                analysis = run_code_analysis(
                    target['repo_path'], target['branch'], target['baseline'], output_dir, selected_paths,
                    target.get('branch_sha', ''), target.get('baseline_sha', ''),
                )
                if analysis and analysis.get('files_changed'):
                    target.update(status='completed_stale' if target.get('status') == 'verified_stale' else 'completed', analysis=analysis)
                else:
                    target.update(status='blocked', block_reason='代码分析未返回已分析的变更文件')
        target_results[index] = target
    write_code_target_results(output_dir, target_results, manifest_errors)
    code_analysis, code_analysis_status, code_analysis_skip_reason = aggregate_code_target_results(target_results)
    if manifest_errors and code_analysis_status == 'skipped':
        code_analysis_status, code_analysis_skip_reason = 'blocked', '；'.join(manifest_errors)
    completed_targets = [target for target in target_results if target.get('status') in {'completed', 'completed_stale'}]
    project_info['branch'] = '、'.join(target['branch'] for target in completed_targets) or project_info.get('branch', '')
    analysis_repo_path = completed_targets[0]['repo_path'] if len(completed_targets) == 1 else ''
    analysis_branch = project_info.get('branch', '')
    analysis_baseline = completed_targets[0]['baseline'] if len(completed_targets) == 1 else 'origin/master'

    # 生成代码影响范围底座；平铺字段供旧下游兼容，code_targets 保留来源追溯。
    impact_path = generate_code_impact_json(
        output_dir, analysis_repo_path, analysis_branch, analysis_baseline, code_analysis,
        code_analysis_status, code_analysis_skip_reason, target_results, manifest_errors,
    )
    code_impact = json.loads(impact_path.read_text(encoding='utf-8'))

    # 需求事实必须在分析阶段先落盘；核心字段不足时禁止生成“通过”的用户报告。
    requirement_facts = build_requirement_facts_fn(
        project_info, latest_content, code_analysis or {}
    )
    requirement_facts_path = _write_internal_json(
        output_dir, 'requirement_facts.json', requirement_facts
    )
    analysis_gate = assess_requirement_facts(project_info, requirement_facts)
    analysis_gate_path = _write_internal_json(
        output_dir, 'analysis_quality_gate.json', analysis_gate
    )
    effective_code_analysis = code_analysis if completed_targets else None
    comparison, comparison_path = generate_requirement_code_comparison(
        output_dir, requirement_facts, effective_code_analysis, build_code_evidence_fn
    )
    if analysis_gate.get('summary', {}).get('status') == 'blocked':
        failed = [
            item['message'] for item in analysis_gate.get('gates', [])
            if item.get('status') == 'failed'
        ]
        raise RuntimeError(
            '需求事实质量门禁未通过，禁止生成需求分析报告：' + '；'.join(failed)
        )

    # 用户报告只消费已通过门禁的事实、对比结论和可追溯代码证据。
    generate_requirement_analysis_md(
        output_dir, project_info, code_analysis or {}, latest_content,
        requirement_facts, analysis_gate, comparison,
    )
    if completed_targets:
        generate_code_review_report(
            output_dir, project_info, code_analysis, code_impact, comparison
        )

    # 保存断点续传所需的上下文，允许单独执行 testpoint 时恢复分析结果。
    _write_internal_json(output_dir, 'analysis_context.json', {
        'project_info': project_info,
        'code_analysis': effective_code_analysis,
        'source_requirement': source_meta,
        'requirement_facts_path': str(requirement_facts_path),
        'analysis_quality_gate_path': str(analysis_gate_path),
        'requirement_code_comparison_path': str(comparison_path),
    })

    return project_info, effective_code_analysis


def _load_analysis_context(output_dir):
    """Restore project/code context for an independently resumed testpoint stage."""
    path = _get_internal_dir(Path(output_dir)) / "analysis_context.json"
    if not path.exists():
        return {}, None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, TypeError):
        return {}, None
    if not isinstance(payload, dict):
        return {}, None
    project_info = payload.get("project_info")
    code_analysis = payload.get("code_analysis")
    return project_info if isinstance(project_info, dict) else {}, code_analysis if isinstance(code_analysis, dict) else None


__all__ = [
    name for name, value in globals().items()
    if callable(value) and getattr(value, "__module__", None) == __name__
]
