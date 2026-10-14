#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thin CLI orchestrator for the testcase-generator workflow."""

import argparse
import json
import sys
from pathlib import Path
from source_documents import prepare_source_documents

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import workflow_common as _common
import workflow_analysis as _analysis
import workflow_review as _review
import workflow_testpoint as _testpoint
import workflow_refine as _refine
import workflow_archive as _archive
from skill_errors import GateError, InputError, normalize_error
from workflow_common import (
    SKILL_DIR,
    WORKFLOW_RUN_ID,
    _get_internal_dir,
    _install_workflow_failure_hook,
    _validate_output_layout,
    append_execution_trace,
    begin_stage,
    check_and_initialize,
    configure_structured_logging,
    extract_business_logic,
    get_config,
    load_workflow_state,
    log_console,
    resolve_requirement_dir,
    record_active_stage_failure,
    save_workflow_state,
    scan_and_convert_docs,
)
from workflow_analysis import (
    _load_analysis_context,
    run_requirement_analysis,
)
from workflow_review import run_requirement_review
from workflow_testpoint import run_testpoint_generate
from workflow_refine import (
    _get_confirmed_xmind,
    finalize_review_xmind,
    run_testcase_refine,
)
from workflow_archive import run_archive

# 旧调用方和测试仍可从 main 访问阶段函数；实现所有权仍归各阶段模块。
for _module in (_common, _analysis, _review, _testpoint, _refine, _archive):
    for _name in getattr(_module, "__all__", ()):
        globals().setdefault(_name, getattr(_module, _name))

# Patch-compatible public adapters retained for existing callers and tests.
SKILL_DIR = _common.SKILL_DIR
extract_business_logic = _common.extract_business_logic
_CLI_OUTPUT_DIR = None


def load_config(project_dir=None):
    original_skill_dir = _common.SKILL_DIR
    _common.SKILL_DIR = SKILL_DIR
    try:
        return _common.load_config(project_dir)
    finally:
        _common.SKILL_DIR = original_skill_dir


def run_code_analysis(*args, **kwargs):
    kwargs.setdefault("business_logic_extractor", extract_business_logic)
    return _analysis.run_code_analysis(*args, **kwargs)

def run_auto_mode(req_dir, args, user_config, repo_path, baseline, repo_config=None):
    """Run only to the next mandatory human-review boundary."""
    repo_config = dict(repo_config or {})
    if getattr(args, "refresh_remote", False):
        repo_config["refresh_remote_refs"] = True
    if getattr(args, "no_refresh_remote", False):
        repo_config["refresh_remote_refs"] = False
    workflow_cfg = get_config(req_dir).get("workflow", {})
    configure_structured_logging(
        req_dir / "测试分析", WORKFLOW_RUN_ID,
        workflow_cfg.get("log_file", ".ai-internal/workflow_log.jsonl"),
    )
    _install_workflow_failure_hook(req_dir)
    log_console("\n" + "=" * 60)
    log_console("🔄 自动流转模式：分析 → 评审 → 人工确认 → 测试点/评审版 XMind")
    log_console("=" * 60)

    started = begin_stage("check")
    kb_manager, output_dir = check_and_initialize(req_dir)
    local_output_dir = req_dir / "测试分析"
    if local_output_dir.exists():
        output_dir = local_output_dir
    scan_and_convert_docs(req_dir)
    append_execution_trace(
        req_dir, "check", "passed", started, output_dir, "自动模式前置检查完成"
    )
    save_workflow_state(req_dir, "check")

    started = begin_stage("analysis", output_dir)
    project_info, code_analysis = run_requirement_analysis(
        req_dir, output_dir, repo_path, args.branch, baseline, kb_manager,
        repo_config=repo_config, code_targets_path=args.code_targets,
        force_legacy=bool(args.repo or args.branch),
    )
    append_execution_trace(
        req_dir,
        "analysis",
        "passed",
        started,
        output_dir,
        "自动模式需求分析完成",
        code_analysis_available=bool(code_analysis),
    )
    save_workflow_state(req_dir, "analysis")

    started = begin_stage("review", output_dir)
    run_requirement_review(req_dir, output_dir, project_info or {}, kb_manager)
    append_execution_trace(
        req_dir, "review", "passed", started, output_dir, "自动模式需求评审完成"
    )
    save_workflow_state(req_dir, "review")

    log_console("\n⏸️ 请先确认评审报告；确认前不会生成测试点、评审 XMind 或终稿。")
    try:
        confirm = input("是否确认评审并生成测试点/评审版 XMind? (y/n): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        log_console("\n已停在评审确认关口")
        return
    if confirm != "y":
        log_console("已停在评审确认关口")
        return

    started = begin_stage("testpoint", output_dir)
    run_testpoint_generate(req_dir, output_dir, project_info or {}, code_analysis, kb_manager)
    append_execution_trace(
        req_dir,
        "testpoint",
        "passed",
        started,
        output_dir,
        "评审确认后生成测试点和评审版 XMind",
    )
    save_workflow_state(req_dir, "testpoint")
    log_console("\n⏸️ 已生成评审版 XMind。完成人工增删改并确认终稿后，请执行 --step refine。")

def main():
    """主函数"""
    global _CLI_OUTPUT_DIR
    _CLI_OUTPUT_DIR = None
    parser = argparse.ArgumentParser(description='测试用例生成统一入口（知识库驱动 + 持续进化）')
    parser.add_argument('directory', help='需求文档目录')
    parser.add_argument('--repo', '-r', default=None, help='代码仓库路径（默认读取 config.yaml）')
    parser.add_argument('--branch', '-b', help='开发分支')
    parser.add_argument('--baseline', default=None, help='基线版本（默认读取 config.yaml）')
    parser.add_argument('--code-targets', help='代码目标清单 JSON；多仓库需求建议显式提供')
    parser.add_argument('--refresh-remote', action='store_true',
                       help='刷新本次分析所需的远程分支引用（当前默认已开启，保留此参数用于兼容）')
    parser.add_argument('--no-refresh-remote', action='store_true',
                       help='显式关闭远程刷新，仅使用本地缓存引用；正式 UAT 结论将被阻断')
    parser.add_argument('--step', '-s',
                       choices=['check', 'analysis', 'review', 'testpoint', 'finalize', 'refine', 'archive', 'all'],
                       default='all',
                       help='执行特定步骤')
    parser.add_argument('--caseGroup', help='功能路径')
    parser.add_argument('--version', '-v', help='版本号')
    parser.add_argument('--manager', '-m', help='责任人（默认读取 config.yaml）')
    parser.add_argument('--relateReqCode', help='需求编码')
    parser.add_argument('--force', '-f', action='store_true', help='强制覆盖')
    parser.add_argument('--auto', '-a', action='store_true',
                       help='自动流转模式：自动执行连续步骤，在关键节点暂停等待用户确认')
    parser.add_argument('--resume', action='store_true',
                       help='断点续传：从上次中断的步骤继续执行')

    parser.add_argument('--source-documents', help='本次已获取网页正文来源清单 JSON，保存到标准目录')
    parser.add_argument('--refresh-documents', action='store_true', help='显式刷新正文，需配合 --source-documents')
    parser.add_argument('--codegraph-policy', choices=['risk_based', 'strict'], help='默认风险补核，strict 显式要求图索引一致')
    parser.add_argument('--impact-evidence', help='目标 SHA 的图查询及关键影响源码补核 JSON')

    args = parser.parse_args()

    req_dir = Path(args.directory)
    if not req_dir.exists():
        raise InputError(f"目录不存在: {req_dir}")
    if args.step in {'check', 'analysis', 'all'} or args.source_documents or args.refresh_documents:
        sources = prepare_source_documents(req_dir, args.source_documents, refresh=args.refresh_documents)
        for document in sources['documents']:
            if document.get('cache_status') == 'local_modified':
                log_console(f"本地来源已修改，按当前正文分析，不宣称网页版本最新：{document['local_path']}")
    try:
        req_dir = resolve_requirement_dir(req_dir)
    except ValueError as exc:
        raise InputError(str(exc)) from exc

    # 加载配置
    cfg = get_config(req_dir)
    _CLI_OUTPUT_DIR = req_dir / cfg['output']['dir_name']
    repo_path = args.repo or cfg['repo']['path']
    if not repo_path:
        log_console("未配置代码仓库；若提测材料未指定可用代码目标，将跳过代码比较并继续需求评审与测试点流程。")
    baseline = args.baseline or cfg['repo']['baseline']
    repo_config = dict(cfg.get('repo', {}))
    if args.codegraph_policy:
        repo_config['codegraph_policy'] = args.codegraph_policy
    if args.impact_evidence:
        repo_config['codegraph_impact_path'] = args.impact_evidence
    if args.refresh_remote:
        repo_config['refresh_remote_refs'] = True
    if args.no_refresh_remote:
        repo_config['refresh_remote_refs'] = False
    workflow_cfg = cfg.get('workflow', {})
    configure_structured_logging(
        req_dir / cfg['output']['dir_name'], WORKFLOW_RUN_ID,
        workflow_cfg.get('log_file', '.ai-internal/workflow_log.jsonl'),
    )
    _install_workflow_failure_hook(req_dir)

    log_console("=" * 60)
    log_console("🚀 测试用例生成统一入口")
    log_console("   知识库驱动 + 持续进化 + 完整闭环")
    log_console("=" * 60)
    log_console(f"📁 目标目录: {req_dir}")
    if args.repo is None:
        log_console(f"📂 仓库路径: {repo_path} (来自 config.yaml)")

    # 用户配置（命令行参数优先，回退到 config.yaml，最后回退到空）
    user_config = {
        'caseGroup': args.caseGroup or '',
        'version': args.version or '',
        'manager': args.manager or cfg['project']['manager'],
        'company': cfg['project']['company'],
        'relateReqCode': args.relateReqCode or ''
    }

    # 断点续传
    if args.resume:
        state = load_workflow_state(req_dir)
        if state:
            args.step = state.get('last_step', 'all')
            log_console(f"🔄 断点续传: 从步骤 '{args.step}' 继续")
        else:
            log_console("⚠️ 未找到断点状态，从头开始执行")

    # 执行步骤
    if args.auto:
        run_auto_mode(req_dir, args, user_config, repo_path, baseline, repo_config)
        return

    kb_manager = None
    output_dir = None
    project_info = {}
    code_analysis = None

    if args.step == 'check' or args.step == 'all':
        step_started = begin_stage('check', output_dir)
        kb_manager, output_dir = check_and_initialize(req_dir)
        local_output_dir = req_dir / cfg['output']['dir_name']
        if local_output_dir.exists():
            output_dir = local_output_dir
        scan_and_convert_docs(req_dir)
        append_execution_trace(req_dir, 'check', 'passed', step_started, output_dir, '前置检查完成')
        save_workflow_state(req_dir, 'check')

    if args.step == 'analysis' or args.step == 'all':
        step_started = begin_stage('analysis', output_dir)
        if not output_dir:
            kb_manager, output_dir = check_and_initialize(req_dir)
            local_output_dir = req_dir / cfg['output']['dir_name']
            if local_output_dir.exists():
                output_dir = local_output_dir
        project_info, code_analysis = run_requirement_analysis(
            req_dir, output_dir, repo_path, args.branch, baseline, kb_manager,
            repo_config=repo_config, code_targets_path=args.code_targets,
            force_legacy=bool(args.repo or args.branch),
        )
        # 注入配置中的公司/责任人信息，供下游 XMind 生成使用
        project_info.setdefault('company', cfg['project']['company'])
        project_info.setdefault('manager', cfg['project']['manager'])
        _validate_output_layout(output_dir, 'analysis', required_files=(
            '需求分析.md', '.ai-internal/code_impact.json',
            '.ai-internal/code_targets.json', '.ai-internal/analysis_context.json',
            '.ai-internal/requirement_facts.json',
            '.ai-internal/analysis_quality_gate.json',
            '.ai-internal/requirement_code_comparison.json',
        ))
        append_execution_trace(req_dir, 'analysis', 'passed', step_started, output_dir, '需求分析完成', code_analysis_available=bool(code_analysis))
        save_workflow_state(req_dir, 'analysis')

    if args.step == 'review' or args.step == 'all':
        step_started = begin_stage('review', output_dir)
        if not output_dir:
            kb_manager, output_dir = check_and_initialize(req_dir)
            local_output_dir = req_dir / cfg['output']['dir_name']
            if local_output_dir.exists():
                output_dir = local_output_dir
        run_requirement_review(req_dir, output_dir, project_info or {}, kb_manager)
        if not list(output_dir.glob('*评审报告.md')) or not list(_get_internal_dir(output_dir).glob('*评审数据.json')):
            raise RuntimeError('需求评审产物不完整，禁止进入测试设计阶段')
        _validate_output_layout(output_dir, 'review')
        append_execution_trace(req_dir, 'review', 'passed', step_started, output_dir, '需求评审完成')
        save_workflow_state(req_dir, 'review')

    if args.step == 'testpoint':
        step_started = begin_stage('testpoint', output_dir)
        if not output_dir:
            kb_manager, output_dir = check_and_initialize(req_dir)
            local_output_dir = req_dir / cfg['output']['dir_name']
            if local_output_dir.exists():
                output_dir = local_output_dir
        if not project_info or code_analysis is None:
            recovered_project_info, recovered_code_analysis = _load_analysis_context(output_dir)
            project_info = project_info or recovered_project_info
            if code_analysis is None:
                code_analysis = recovered_code_analysis
        # 确保 company/manager 存在（断点续传时 project_info 可能来自缓存）
        if project_info:
            project_info.setdefault('company', cfg['project']['company'])
            project_info.setdefault('manager', cfg['project']['manager'])
        run_testpoint_generate(req_dir, output_dir, project_info or {}, code_analysis, kb_manager)
        append_execution_trace(req_dir, 'testpoint', 'passed', step_started, output_dir, '测试点生成完成')
        save_workflow_state(req_dir, 'testpoint')

    if args.step == 'finalize':
        step_started = begin_stage('finalize', output_dir)
        if not output_dir:
            kb_manager, output_dir = check_and_initialize(req_dir)
            local_output_dir = req_dir / cfg['output']['dir_name']
            if local_output_dir.exists():
                output_dir = local_output_dir
        finalize_review_xmind(output_dir)
        _validate_output_layout(output_dir, 'finalize')
        append_execution_trace(req_dir, 'finalize', 'passed', step_started, output_dir, '已确认评审版 XMind 并生成内部终稿')
        save_workflow_state(req_dir, 'finalize')
    if args.step == 'refine':
        step_started = begin_stage('refine', output_dir)
        if not output_dir:
            kb_manager, output_dir = check_and_initialize(req_dir)
            local_output_dir = req_dir / cfg['output']['dir_name']
            if local_output_dir.exists():
                output_dir = local_output_dir
        xmind_path = _get_confirmed_xmind(output_dir)
        run_testcase_refine(xmind_path, output_dir, user_config)
        append_execution_trace(req_dir, 'refine', 'passed', step_started, output_dir, '用例细化完成')
        save_workflow_state(req_dir, 'refine')

    if args.step == 'archive':
        step_started = begin_stage('archive', output_dir)
        if not output_dir:
            kb_manager, output_dir = check_and_initialize(req_dir)
            local_output_dir = req_dir / cfg['output']['dir_name']
            if local_output_dir.exists():
                output_dir = local_output_dir
        archive_result = run_archive(req_dir, output_dir, project_info or {}, kb_manager, user_config)
        if not isinstance(archive_result, dict) or not archive_result.get('completed'):
            reason = (archive_result or {}).get('reason', 'archive_not_completed') if isinstance(archive_result, dict) else 'archive_not_completed'
            raise GateError(
                f'知识库归档未完成: {reason}',
                stage='archive',
                context={'reason': reason},
            )
        append_execution_trace(
            req_dir, 'archive', 'passed', step_started, output_dir, '知识库归档完成',
            archive_id=archive_result.get('archive_id', ''),
            archive_status=archive_result.get('status', ''),
        )
        save_workflow_state(
            req_dir, 'archive', archive_id=archive_result.get('archive_id', ''),
            archive_status=archive_result.get('status', ''),
        )

    if args.step == 'all':
        log_console("\n" + "=" * 60)
        log_console("✅ 阶段执行完成，已停在评审确认闸口")
        log_console("=" * 60)
        log_console(f"📁 输出目录: {output_dir}")
        log_console("\n下一步:")
        log_console("  1. 阅读「评审报告.md」并处理待确认问题")
        log_console("  2. 确认评审后执行: python main.py <目录> --step testpoint")
        log_console("  3. 修改评审版 XMind 后执行: python main.py <目录> --step finalize，再执行 --step refine")
        log_console("  4. 用例确认后执行: python main.py <目录> --step archive")


def _write_cli_error(error):
    """Write the last CLI failure as machine-readable evidence when possible."""
    if not _CLI_OUTPUT_DIR:
        return None
    path = Path(_CLI_OUTPUT_DIR) / ".ai-internal" / "last_error.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(error.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def cli_entrypoint():
    """Map stable workflow errors to documented process exit codes."""
    try:
        return int(main() or 0)
    except KeyboardInterrupt:
        log_console("操作已由用户中断")
        return 130
    except Exception as exc:
        error = normalize_error(exc)
        try:
            record_active_stage_failure(error)
        except Exception:
            pass
        try:
            _write_cli_error(error)
        except Exception:
            pass
        log_console(f"❌ [{error.error_code}] {error}")
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(cli_entrypoint())
