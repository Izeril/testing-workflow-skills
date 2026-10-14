#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared runtime, configuration, preflight, artifact layout, and workflow metrics."""
import hashlib
import copy
import os
import sys
import json
import subprocess
import shutil
import time
import uuid
import re
import yaml
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from impact_selector import (
    collect_git_diff,
    extract_requirement_anchors,
    inspect_git_freshness,
    resolve_git_ref as resolve_precise_git_ref,
    select_impact_files,
)
from workflow_logging import (
    clear_log_stage,
    configure_structured_logging,
    get_structured_log_path,
    log_console,
    log_event,
    set_log_stage,
    shutdown_structured_logging,
)
from workflow_metrics import write_workflow_metrics

WORKFLOW_RUN_ID = f"{datetime.now():%Y%m%dT%H%M%S}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
_ACTIVE_STAGE_CONTEXT = {}
_CURRENT_REQ_DIR = None

# ============================================================================
# 路径配置
# ============================================================================

SKILL_DIR = Path(__file__).parent.parent

# 跨技能调用只允许通过路径校验适配器，不向全局 sys.path 注入技能目录。
from cross_skill_adapter import (
    compare_structured,
    convert_doc_to_md,
    create_xmind_file,
    extract_business_logic,
    gid,
    make_node,
)
from skill_errors import ConversionError, ExternalDependencyError

try:
    from knowledge_base_manager import KnowledgeBaseManager
except ImportError:
    KnowledgeBaseManager = None
    log_console("⚠️ knowledge_base_manager 模块未找到，知识库功能不可用")
# 默认配置（仓库路径必须通过 config.yaml 配置，不设机器级默认值）
DEFAULT_REPO_PATH = ""

# ============================================================================
# 配置加载
# ============================================================================

def load_config(project_dir: Optional[Path] = None) -> Dict:
    """
    加载 skill 内置配置。

    查找顺序：
    1. 当前 skill 根目录的 config.yaml（随 skill 分发）
    2. 内置默认值
    """
    defaults = {
        'project': {'name': '', 'product': '', 'company': '', 'manager': ''},
        'repo': {'path': DEFAULT_REPO_PATH, 'baseline': 'origin/master'},
        'env': {'default': 'FAT/UAT', 'pre_condition': '1. FAT/UAT环境已部署\n2. 服务正常运行'},
        'workflow': {'state_file': '.ai-internal/workflow_state.json', 'trace_file': '.ai-internal/execution_trace.json', 'log_file': '.ai-internal/workflow_log.jsonl', 'metrics_file': '.ai-internal/workflow_metrics.json', 'metrics_report': '流程度量报告.md', 'cache_hours': 24, 'default_step': 'all'},
        'output': {'dir_name': '测试分析', 'internal_dir': '.ai-internal'},
        'knowledge_base': {'frequency_threshold': 3, 'top_n': 10},
        'testcase': {'priority_keywords': [], 'case_type_rules': {}},
    }

    def deep_merge(base, override):
        for key, value in override.items():
            if isinstance(value, dict) and isinstance(base.get(key), dict):
                deep_merge(base[key], value)
            else:
                base[key] = value

    def merge_yaml(path: Path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            if isinstance(data, dict):
                deep_merge(defaults, data)
        except Exception as exc:
            log_console(f"  ⚠️ 配置读取失败，已跳过 {path}: {exc}")

    # 分发默认值与本机覆盖分离；环境变量最后覆盖，便于 CI 和临时任务。
    skill_config = SKILL_DIR / 'config.yaml'
    if skill_config.exists():
        merge_yaml(skill_config)
    local_config = SKILL_DIR / 'config.local.yaml'
    if local_config.exists():
        merge_yaml(local_config)

    environment_overrides = {
        'TESTCASE_PRODUCT': ('project', 'product'),
        'TESTCASE_COMPANY': ('project', 'company'),
        'TESTCASE_MANAGER': ('project', 'manager'),
        'TESTCASE_REPO_PATH': ('repo', 'path'),
        'TESTCASE_BASELINE': ('repo', 'baseline'),
        'TESTCASE_UAT_BRANCH': ('repo', 'uat_branch'),
    }
    for env_name, (section, key) in environment_overrides.items():
        value = os.environ.get(env_name)
        if value is not None and value.strip():
            defaults.setdefault(section, {})[key] = value.strip()

    return defaults


# 配置缓存必须按 skill 位置和项目目录隔离。返回深拷贝，防止调用方
# 对配置字典的就地修改污染同一进程内的后续阶段或其他项目。
_CONFIG_CACHE: Dict[Tuple[str, str], Dict[str, Any]] = {}


def _config_cache_key(project_dir: Optional[Path] = None) -> Tuple[str, str]:
    skill_key = str(Path(SKILL_DIR).resolve())
    project_key = str(Path(project_dir).resolve()) if project_dir is not None else ""
    return skill_key, project_key


def clear_config_cache(project_dir: Optional[Path] = None) -> None:
    """Clear every cached config, or only the current skill/project entry."""
    if project_dir is None:
        _CONFIG_CACHE.clear()
        return
    _CONFIG_CACHE.pop(_config_cache_key(project_dir), None)


def get_config(project_dir: Optional[Path] = None, force_reload: bool = False):
    key = _config_cache_key(project_dir)
    if force_reload or key not in _CONFIG_CACHE:
        _CONFIG_CACHE[key] = load_config(project_dir)
    return copy.deepcopy(_CONFIG_CACHE[key])


# ============================================================================
# 步骤①：前置检查
# ============================================================================

def is_requirement_package_dir(path: Path) -> bool:
    """Return True for a concrete requirement package, not the AI_Project container."""
    if not path.is_dir():
        return False
    requirement_doc_dir = '\u9700\u6c42\u6587\u6863'
    release_test_dir = '\u63d0\u6d4b\u6587\u6863'
    if (path / requirement_doc_dir).exists() or (path / release_test_dir).exists():
        return True
    doc_suffixes = {'.doc', '.docx', '.md', '.pdf'}
    return any(child.is_file() and child.suffix.lower() in doc_suffixes for child in path.iterdir())


def find_requirement_package_candidates(base_dir: Path, limit: int = 10):
    """Find concrete requirement packages below a container directory."""
    candidates = []
    if not base_dir.is_dir():
        return candidates

    for child in base_dir.iterdir():
        if not child.is_dir() or child.name.startswith('.'):
            continue
        if is_requirement_package_dir(child):
            candidates.append(child)
            continue
        for grandchild in child.iterdir():
            if grandchild.is_dir() and not grandchild.name.startswith('.') and is_requirement_package_dir(grandchild):
                candidates.append(grandchild)

    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[:limit]


def resolve_requirement_dir(input_dir: Path) -> Path:
    """Resolve the concrete requirement package before creating output files."""
    input_dir = input_dir.resolve()
    if is_requirement_package_dir(input_dir):
        return input_dir

    candidates = find_requirement_package_candidates(input_dir)
    if len(candidates) == 1:
        log_console(f"  Resolved requirement package: {candidates[0]}")
        return candidates[0]

    if candidates:
        candidate_text = '\n'.join(f"    - {p}" for p in candidates[:5])
        raise ValueError(
            f"Please pass a concrete requirement package directory, not a container directory: {input_dir}\n"
            f"Candidate packages:\n{candidate_text}"
        )

    raise ValueError(f"No requirement or release-test documents found under: {input_dir}")


def verify_runtime_environment():
    """提前验证 Windows 子进程和 Git，避免阶段“未执行但退出码正常”的假象。"""
    executable = str(Path(sys.executable).resolve())
    if 'windowsapps' in executable.casefold():
        raise RuntimeError(
            f"当前 Python 来自 WindowsApps 别名（{executable}），"
            "请改用已安装的 python.exe，避免子进程权限和路径漂移。"
        )
    try:
        child = subprocess.run(
            [executable, '-c', 'print("runtime-preflight-ok")'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=20,
            check=False,
        )
    except OSError as exc:
        detail = str(exc)
        if getattr(exc, 'winerror', None) == 1385 or '1385' in detail:
            raise RuntimeError(
                "Windows 子进程启动失败（1385）：Codex 沙箱账户缺少所需登录类型。"
                "请检查 CodexSandboxUsers 的 SeInteractiveLogonRight，"
                "修复后重新启动当前任务。"
            ) from exc
        raise RuntimeError(f"Python 子进程启动失败：{exc}") from exc
    if child.returncode != 0 or 'runtime-preflight-ok' not in child.stdout:
        raise RuntimeError(
            f"Python 子进程自检失败（退出码 {child.returncode}）："
            f"{(child.stderr or child.stdout).strip()}"
        )
    try:
        git = subprocess.run(
            ['git', '--version'], capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=20, check=False,
        )
    except OSError as exc:
        raise RuntimeError(f"Git 无法启动：{exc}") from exc
    if git.returncode != 0:
        raise RuntimeError(f"Git 自检失败：{(git.stderr or git.stdout).strip()}")
    return {
        'python_executable': executable,
        'python_child_process': 'passed',
        'git_version': git.stdout.strip(),
    }


def check_and_initialize(project_dir: Path) -> tuple:
    """
    Preflight checks and initialization.

    Returns:
        (knowledge base manager, output directory)
    """
    log_console("\n" + "=" * 60)
    log_console("Preflight check")
    log_console("=" * 60)
    runtime_report = verify_runtime_environment()
    log_console(
        f"  Runtime preflight passed: {runtime_report['git_version']}"
    )

    # Keep the requirement package and the .skill project root separate.
    requirement_dir = resolve_requirement_dir(project_dir)
    output_dir = requirement_dir / '\u6d4b\u8bd5\u5206\u6790'
    output_dir.mkdir(parents=True, exist_ok=True)
    workflow_cfg = get_config(requirement_dir).get('workflow', {})
    configure_structured_logging(
        output_dir, WORKFLOW_RUN_ID,
        workflow_cfg.get('log_file', '.ai-internal/workflow_log.jsonl'),
    )
    project_root = requirement_dir
    skill_dir = project_root / '.skill'
    if not skill_dir.exists():
        parent = requirement_dir.parent
        for _ in range(3):
            if (parent / '.skill').exists():
                skill_dir = parent / '.skill'
                project_root = parent
                break
            parent = parent.parent

    kb_manager = None
    if KnowledgeBaseManager:
        kb_manager = KnowledgeBaseManager(str(project_root), str(SKILL_DIR))
        if not kb_manager.is_initialized():
            log_console("  Knowledge base not initialized; creating it...")
            kb_manager.initialize()
            log_console(f"  Knowledge base created: {kb_manager.kb_path}")
        else:
            log_console(f"  Knowledge base initialized: {kb_manager.kb_path}")

    _normalize_output_layout(output_dir)

    return kb_manager, output_dir


def scan_and_convert_docs(directory):
    """扫描并转换 DOC；任一转换失败都阻断前置检查，禁止静默继续。"""
    log_console("\n📂 扫描文档...")

    converted_files = []
    md_files = []
    conversion_failures = []

    for root, dirs, files in os.walk(directory):
        dirs[:] = [d for d in dirs if not d.startswith('.')]

        for filename in files:
            filepath = os.path.join(root, filename)

            if filename.endswith('.doc') and convert_doc_to_md:
                log_console(f"  📄 发现 DOC 文件: {filename}")
                output_dir = os.path.dirname(filepath)
                try:
                    md_path = convert_doc_to_md(filepath, output_dir)
                    if md_path:
                        converted_files.append((filepath, md_path))
                        md_files.append(md_path)
                        log_console(f"     ✅ 已转换为: {os.path.basename(md_path)}")
                except Exception as e:
                    failure = {
                        "path": filepath,
                        "error_code": str(getattr(e, "error_code", "conversion_failed")),
                        "error_type": type(e).__name__,
                        "message": str(e),
                    }
                    conversion_failures.append(failure)
                    log_console(f"     ❌ 转换失败: {e}")

            elif filename.endswith('.md'):
                md_files.append(filepath)

    if conversion_failures:
        summary = "；".join(
            f"{item['path']}: {item['message']}" for item in conversion_failures
        )
        error_type = (
            ExternalDependencyError
            if any(item["error_code"] in {
                "external_dependency_failed", "cross_skill_dependency_failed"
            } for item in conversion_failures)
            else ConversionError
        )
        raise error_type(
            "DOC 转换质量门禁未通过，禁止继续分析：" + summary,
            stage="check",
            context={"failures": conversion_failures},
        )
    log_console(f"  ✅ 发现 {len(md_files)} 个 MD 文件")
    return converted_files, md_files

# Shared artifact layout
def _get_internal_dir(output_dir):
    """获取内部文件目录（存放中间产物）"""
    internal_dir = output_dir / '.ai-internal'
    internal_dir.mkdir(parents=True, exist_ok=True)
    return internal_dir



def _is_allowed_root_artifact(name: str, stage: str = '') -> bool:
    user_markdown = {
        '需求分析.md',
        '业务影响分析.md',
        '测试设计模型.md',
        '覆盖矩阵.md',
        '代码评审报告.md',
        '代码分析报告.md',
        '需求实现审计报告.md',
        '02_测试设计说明.md',
        '详细测试点.md',
        '测试点生成报告.md',
        '流程度量报告.md',
    }
    if name in user_markdown or name.endswith('_评审报告.md'):
        return True
    if re.fullmatch(r'.+_评审_\d{8}_v[1-9]\d*\.xmind', name):
        return True
    return stage == 'refine' and name == '评审差异预览报告.md'


def _normalize_output_layout(output_dir):
    """Archive known legacy root artifacts; unknown files remain gate failures."""
    output_dir = Path(output_dir)
    internal_dir = _get_internal_dir(output_dir)
    legacy_dir = internal_dir / 'runs' / datetime.now().strftime('%Y%m%d%H%M%S') / 'legacy'
    # 历史版本曾把用户报告误写到内部目录；规范化时恢复到用户可见根目录。
    for report_name in ('需求分析.md', '代码评审报告.md', '测试设计模型.md', '覆盖矩阵.md'):
        internal_report = internal_dir / report_name
        root_report = output_dir / report_name
        if not internal_report.exists():
            continue
        if root_report.exists():
            legacy_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(internal_report), str(legacy_dir / report_name))
        else:
            shutil.move(str(internal_report), str(root_report))
    legacy_code_review = internal_dir / 'code_review_report.md'
    if legacy_code_review.exists():
        legacy_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy_code_review), str(legacy_dir / legacy_code_review.name))
    legacy_markers = ('人工复核', '_XMind源', '评审_终稿', '测试产物收敛优化建议')
    for item in list(output_dir.iterdir()):
        if not item.is_file():
            continue
        if item.suffix.lower() == '.json' or item.name.startswith('.code_analysis_cache_'):
            target = internal_dir / item.name
            if target.exists():
                target.unlink()
            shutil.move(str(item), str(target))
        elif any(marker in item.name for marker in legacy_markers):
            legacy_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(item), str(legacy_dir / item.name))


def _validate_output_layout(output_dir, stage, required_files=()):
    """Enforce the three-class user artifact contract and internal separation."""
    output_dir = Path(output_dir)
    internal_dir = _get_internal_dir(output_dir)
    required_paths = [output_dir / name for name in required_files]
    missing = [str(path) for path in required_paths if not path.exists()]
    unexpected_root = [str(item) for item in output_dir.iterdir() if item.name != '.ai-internal' and item.is_file() and not _is_allowed_root_artifact(item.name, stage)]
    allowed_internal_md = {'normalized_requirement.md', 'xmind_source.md'}
    internal_markdown = [str(path) for path in internal_dir.glob('*.md') if path.name not in allowed_internal_md]
    issues = [f'缺少产物: {path}' for path in missing]
    issues.extend(f'根目录存在非白名单产物: {path}' for path in unexpected_root)
    issues.extend(f'.ai-internal 存在未登记 Markdown: {path}' for path in internal_markdown)
    report = {'stage': stage, 'status': 'passed' if not issues else 'failed', 'required_files': [str(path) for path in required_paths], 'missing_files': missing, 'unexpected_root_files': unexpected_root, 'internal_markdown_files': internal_markdown, 'issues': issues, 'checked_at': _workflow_timestamp()}
    _write_internal_json(output_dir, 'artifact_manifest.json', report)
    if issues:
        raise RuntimeError(f'产物目录门禁未通过（{stage}）：' + '；'.join(issues[:8]))
    return report

# Shared structured-artifact helpers
def _safe_artifact_name(value, fallback='需求'):
    """将需求名称转换为 Windows 和跨平台均可用的产物文件名。"""
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', str(value or ''))
    text = re.sub(r'\s+', ' ', text).strip(' .')
    return text or fallback

def _workflow_timestamp():
    return datetime.now().isoformat(timespec='seconds')


def _stable_id(prefix, text, index):
    raw = f"{prefix}|{index}|{text}"
    digest = hashlib.md5(raw.encode('utf-8')).hexdigest()[:8].upper()
    return f"{prefix}-{index + 1:03d}-{digest}"


def _write_internal_json(output_dir, filename, data):
    internal_dir = _get_internal_dir(output_dir)
    json_path = internal_dir / filename
    payload = dict(data or {})
    envelope = {
        'schema_version': payload.pop('schema_version', '1.0.0'),
        'workflow_version': payload.pop('workflow_version', 'testcase-generator-2026.07'),
        'generated_at': payload.pop('generated_at', _workflow_timestamp()),
        **payload,
    }
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(envelope, f, ensure_ascii=False, indent=2)
    return json_path


def _compact_text(text, limit=80):
    cleaned = re.sub(r'\s+', ' ', str(text or '')).strip(' |:-#\t')
    return cleaned[:limit] if cleaned else ''

def _load_internal_json_payload(output_dir, filename):
    path = _get_internal_dir(output_dir) / filename
    if not path.exists():
        return {}, path
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f), path
    except Exception as e:
        log_console(f"  ⚠️ 读取内部产物失败 {path}: {e}")
        return {}, path

# Workflow state and execution metrics
def _get_state_file(req_dir):
    """Return the configured workflow state path."""
    cfg = get_config(req_dir)
    output_dir = req_dir / cfg["output"]["dir_name"]
    relative = cfg.get("workflow", {}).get(
        "state_file", ".ai-internal/workflow_state.json"
    )
    path = output_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _get_execution_trace_file(req_dir):
    """Return the configured raw execution trace path."""
    cfg = get_config(req_dir)
    output_dir = req_dir / cfg["output"]["dir_name"]
    relative = cfg.get("workflow", {}).get(
        "trace_file", ".ai-internal/execution_trace.json"
    )
    path = output_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _list_recent_artifacts(output_dir):
    if not output_dir or not Path(output_dir).exists():
        return []
    artifacts = []
    try:
        candidates = list(Path(output_dir).glob('*.md')) + list(Path(output_dir).glob('*.xmind')) + list((Path(output_dir) / '.ai-internal').glob('*.json'))
        for item in sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[:30]:
            artifacts.append({'path': str(item), 'name': item.name, 'size': item.stat().st_size})
    except Exception:
        return artifacts
    return artifacts


def begin_stage(step, output_dir=None):
    """Mark a canonical stage active so uncaught failures are also traced."""
    started_at = time.time()
    _ACTIVE_STAGE_CONTEXT.clear()
    _ACTIVE_STAGE_CONTEXT.update({
        "step": step,
        "started_at": started_at,
        "output_dir": str(output_dir) if output_dir else "",
    })
    if output_dir:
        configure_structured_logging(output_dir, WORKFLOW_RUN_ID)
    set_log_stage(step, started_at)
    return started_at


def record_active_stage_failure(exc):
    """Persist a structured failure for the currently active workflow stage."""
    active = dict(_ACTIVE_STAGE_CONTEXT)
    if not active.get("step") or not _CURRENT_REQ_DIR:
        return False
    append_execution_trace(
        Path(_CURRENT_REQ_DIR),
        active["step"],
        "failed",
        active.get("started_at"),
        Path(active["output_dir"]) if active.get("output_dir") else None,
        str(exc),
        error_type=type(exc).__name__,
        error_code=getattr(exc, "error_code", "unexpected_error"),
    )
    return True


def _install_workflow_failure_hook(req_dir):
    """Record uncaught CLI failures before delegating to Python's normal hook."""
    global _CURRENT_REQ_DIR
    _CURRENT_REQ_DIR = str(req_dir)
    original_hook = sys.excepthook
    if getattr(original_hook, "_workflow_metrics_hook", False):
        return

    def workflow_hook(exc_type, exc_value, traceback):
        record_active_stage_failure(exc_value)
        original_hook(exc_type, exc_value, traceback)

    workflow_hook._workflow_metrics_hook = True
    sys.excepthook = workflow_hook

def append_execution_trace(req_dir, step, status, started_at=None, output_dir=None, message="", **extra):
    """Append raw evidence and refresh the aggregated metrics dashboard."""
    trace_path = _get_execution_trace_file(req_dir)
    now = time.time()
    if started_at is None:
        started_at = now
    trace = {
        "schema_version": "1.0.0",
        "workflow_version": "testcase-generator-2026.07",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "events": [],
    }
    if trace_path.exists():
        try:
            with open(trace_path, "r", encoding="utf-8") as handle:
                old_trace = json.load(handle)
            if isinstance(old_trace, dict):
                trace["events"] = old_trace.get("events", [])[-199:]
        except Exception:
            trace["events"] = []

    attempt_number = 1 + sum(
        1 for item in trace["events"] if item.get("step") == step
    )
    event = {
        "run_id": WORKFLOW_RUN_ID,
        "step": step,
        "status": status,
        "attempt_number": attempt_number,
        "started_at": datetime.fromtimestamp(started_at).isoformat(timespec="seconds"),
        "finished_at": datetime.fromtimestamp(now).isoformat(timespec="seconds"),
        "duration_seconds": round(now - started_at, 3),
        "message": message,
        "artifacts": _list_recent_artifacts(output_dir) if output_dir else [],
        **extra,
    }
    trace["events"].append(event)
    log_event(
        "ERROR" if status == "failed" else "INFO",
        message or f"阶段{status}: {step}",
        event="stage_completed",
        stage=step,
        status=status,
        duration_seconds=event["duration_seconds"],
        attempt_number=attempt_number,
        **extra,
    )
    try:
        with open(trace_path, "w", encoding="utf-8") as handle:
            json.dump(trace, handle, ensure_ascii=False, indent=2)
    except Exception as exc:
        log_console(f"  ⚠️ 保存执行轨迹失败: {exc}")
        return trace_path

    metrics_output_dir = Path(output_dir) if output_dir else trace_path.parent.parent
    try:
        workflow_cfg = get_config(req_dir).get("workflow", {})
        metrics, metrics_path, report_path = write_workflow_metrics(
            metrics_output_dir,
            trace,
            WORKFLOW_RUN_ID,
            workflow_cfg.get("metrics_file", ".ai-internal/workflow_metrics.json"),
            workflow_cfg.get("metrics_report", "流程度量报告.md"),
        )
        log_console(
            f"  📊 流程度量已更新: {report_path} "
            f"（完成率 {metrics['summary']['workflow_completion_rate'] * 100:.1f}%）"
        )
    except Exception as exc:
        log_console(f"  ⚠️ 保存流程度量失败: {exc}")

    if _ACTIVE_STAGE_CONTEXT.get("step") == step:
        _ACTIVE_STAGE_CONTEXT.clear()
    clear_log_stage(step)
    return trace_path


def save_workflow_state(req_dir, step, **extra):
    """保存当前工作流状态"""
    state_path = _get_state_file(req_dir)
    state = {
        'schema_version': '1.0.0',
        'workflow_version': 'testcase-generator-2026.07',
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'last_step': step,
        'saved_at': datetime.now().isoformat(),
        **extra
    }
    try:
        with open(state_path, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log_console(f"  ⚠️ 保存状态失败: {e}")


def load_workflow_state(req_dir):
    """加载工作流状态"""
    state_path = _get_state_file(req_dir)
    if state_path.exists():
        try:
            with open(state_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return None

__all__ = [
    name for name, value in globals().items()
    if callable(value) and getattr(value, "__module__", None) == __name__
] + [
    "WORKFLOW_RUN_ID", "_ACTIVE_STAGE_CONTEXT", "SKILL_DIR",
    "extract_business_logic", "convert_doc_to_md", "compare_structured",
    "gid", "make_node", "create_xmind_file", "log_console", "log_event",
    "configure_structured_logging", "get_structured_log_path", "shutdown_structured_logging",
]
