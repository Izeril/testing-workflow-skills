#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test-design model, coverage matrix, quality gates, and review XMind generation."""

import hashlib
import json
import re
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from decision_register import decision_is_complete

from cross_skill_adapter import get_skill_file, validate_contract_payload
from workflow_common import (
    _compact_text,
    _get_internal_dir,
    _load_internal_json_payload,
    _safe_artifact_name,
    _stable_id,
    _workflow_timestamp,
    _write_internal_json,
    create_xmind_file,
        make_node,
    save_workflow_state,
    log_console,
)
from workflow_analysis import (
    _as_list,
    _read_normalized_requirement,
    _validate_testpoint_source_consistency,
    _validate_output_layout,
    build_requirement_facts,
)
from workflow_review import _extract_test_data, _generate_quality_testpoints
def _score_workflow_mode(doc_content, extracted_data, code_analysis, review_questions=None, state_transitions=None):
    text = doc_content.lower()
    review_questions = review_questions or []
    state_transitions = state_transitions or []
    score_items = []

    def add(feature, weight, reason, evidence=None, source='确定性规则'):
        score_items.append({
            'feature': feature,
            'weight': weight,
            'reason': reason,
            'evidence': _compact_text(evidence or reason, 120),
            'source': source,
        })

    feature_rules = [
        ('状态流转', 4, ['状态流转', '状态码', '流转', '结果码'], '存在状态迁移或结果码判断'),
        ('定时任务', 4, ['定时任务', '调度', 'xxl-job', 'cron'], '存在定时/调度执行链路'),
        ('异步回调通知', 4, ['异步', '回调', '通知', 'mq', 'webhook'], '存在异步、回调、通知或消息链路'),
        ('幂等防重', 3, ['幂等', '重复', 'redis', '缓存', '防重'], '存在重复触发或缓存防重风险'),
        ('数据一致性', 3, ['一致性', '多表', '写入', '同步', '数据流转'], '存在多表写入或数据流转风险'),
        ('外部接口', 3, ['外部接口', '第三方', '接口', '上下游'], '存在外部接口或上下游交互'),
        ('安全合规', 4, ['权限', '安全', '认证', '敏感', '金额', '资金'], '涉及安全、权限、敏感或资金类风险'),
        ('AI/规则引擎', 3, ['ai', 'llm', '规则库', '模型'], '涉及 AI、LLM 或规则类判断'),
    ]
    for feature, weight, keywords, reason in feature_rules:
        hits = [kw for kw in keywords if kw.lower() in text]
        if hits:
            add(feature, weight, f"{reason}：{', '.join(hits)}", ', '.join(hits))

    if extracted_data:
        if len(extracted_data.get('tables', [])) >= 3:
            add('多表影响', 2, f"提取到 {len(extracted_data.get('tables', []))} 张表", source='提测/设计文档')
        if extracted_data.get('status_codes'):
            add('状态码/错误码', 2, f"提取到 {len(extracted_data.get('status_codes', []))} 个状态码", source='提测/设计文档')
        if extracted_data.get('configs'):
            add('配置边界', 1, f"提取到 {len(extracted_data.get('configs', []))} 个配置项", source='提测/设计文档')

    if code_analysis:
        if code_analysis.get('files_changed'):
            add('代码影响范围', min(4, 1 + len(code_analysis.get('files_changed', [])) // 5), f"涉及 {len(code_analysis.get('files_changed', []))} 个变更文件", source='code_analysis.files_changed')
        if code_analysis.get('scheduled_tasks'):
            add('代码定时任务', 4, f"代码分析命中 {len(code_analysis.get('scheduled_tasks', []))} 个定时任务", source='code_analysis.scheduled_tasks')
        if code_analysis.get('api_endpoints'):
            add('代码接口入口', 2, f"代码分析命中 {len(code_analysis.get('api_endpoints', []))} 个接口入口", source='code_analysis.api_endpoints')
        if code_analysis.get('sql_analysis'):
            add('代码SQL/数据写入', 3, '代码分析命中 SQL 或数据写入证据', source='code_analysis.sql_analysis')

    if state_transitions:
        add('状态模型', 4 if len(state_transitions) >= 2 else 3, f"提取到 {len(state_transitions)} 条状态流转", source='test_design_model.state_transitions')
    important_questions = [q for q in review_questions if q.get('severity') in ['P1', 'P2']]
    if important_questions:
        add('评审问题', 3 if len(important_questions) >= 2 else 2, f"存在 {len(important_questions)} 个严重/重要评审问题", source='review_questions')

    total_score = sum(item['weight'] for item in score_items)
    features = [item['feature'] for item in score_items]
    has_deep_trigger = any(item['feature'] in ['安全合规', '代码SQL/数据写入'] for item in score_items) and code_analysis and len(code_analysis.get('files_changed', [])) >= 10
    if has_deep_trigger or total_score >= 14:
        selected_mode, mode_label = 'deep', '深度模式'
        strategy = 'codegraph_impact_selection_required_with_fallback'
    elif total_score >= 7 or any(item['feature'] in ['状态流转', '定时任务', '异步回调通知', '状态模型'] for item in score_items):
        selected_mode, mode_label = 'standard', '标准模式'
        strategy = 'codegraph_impact_selection_preferred'
    elif total_score > 0:
        selected_mode, mode_label = 'fast_plus', '快速+模式'
        strategy = 'lightweight_evidence_check'
    else:
        selected_mode, mode_label = 'fast', '快速模式'
        strategy = 'no_code_analysis_by_default'
        add('低风险默认', 0, '未命中复杂风险特征，保持快速模式')

    reasons = [item['reason'] for item in score_items if item['weight'] > 0]
    if not reasons:
        reasons = ['未命中复杂风险特征']
    return {
        'selected_mode': selected_mode,
        'mode_label': mode_label,
        'mode_reason': reasons[:8],
        'mode_score': total_score,
        'score_items': score_items,
        'trigger_features': features,
        'code_analysis_strategy': strategy,
        'selected_by': 'auto_rule_score',
        'confirmed_by_user': False,
    }


def _internal_json_contracts():
    return {
        'requirement_facts.json': {'required': ['schema_version', 'workflow_version', 'generated_at', 'items'], 'owner_step': 'testpoint/design'},
        'analysis_quality_gate.json': {'required': ['schema_version', 'workflow_version', 'generated_at', 'summary', 'gates'], 'owner_step': 'analysis'},
        'requirement_code_comparison.json': {'required': ['schema_version', 'workflow_version', 'generated_at', 'summary', 'items'], 'owner_step': 'analysis', 'schema_version': '1.1.0'},
        'business_impact.json': {'required': ['schema_version', 'workflow_version', 'generated_at', 'analysis_mode', 'items', 'evidence_catalog', 'summary'], 'owner_step': 'analysis', 'schema_version': '1.1.0'},
        'source_inventory.json': {'required': ['schema_version', 'workflow_version', 'generated_at', 'documents', 'attachments', 'images', 'summary'], 'owner_step': 'review'},
        'decision_register.json': {'required': ['schema_version', 'items'], 'owner_step': 'review', 'workflow_required': False, 'schema_version': '1.0.0'},
        'business_impact_gate.json': {'required': ['schema_version', 'summary', 'analysis_mode', 'gates'], 'owner_step': 'analysis', 'workflow_required': False, 'schema_version': '1.1.0'},
        'test_design_model.json': {'required': ['schema_version', 'workflow_version', 'generated_at', 'mode'], 'owner_step': 'testpoint/design'},
        'coverage_matrix.json': {'required': ['schema_version', 'workflow_version', 'generated_at', 'mappings', 'statistics'], 'owner_step': 'testpoint/design'},
        'testpoints.json': {'required': ['schema_version', 'workflow_version', 'generated_at', 'test_points'], 'owner_step': 'testpoint/design'},
        'quality_gate_report.json': {'required': ['schema_version', 'workflow_version', 'generated_at', 'summary', 'gates'], 'owner_step': 'testpoint/design'},
        'consistency_report.json': {'required': ['schema_version', 'workflow_version', 'generated_at', 'summary', 'findings', 'decision'], 'owner_step': 'testpoint/design'},
        'consistency_fix_suggestions.json': {'required': ['schema_version', 'workflow_version', 'generated_at', 'summary', 'suggestions'], 'owner_step': 'testpoint/design'},
        'code_impact.json': {'required': ['schema_version', 'workflow_version', 'generated_at', 'status', 'summary', 'impact_scope'], 'owner_step': 'analysis', 'schema_version': '1.3.0'},
        'xmind_candidate_filter_report.json': {'required': ['schema_version', 'workflow_version', 'generated_at', 'summary', 'excluded'], 'owner_step': 'testpoint/xmind'},
        'xmind_backflow_report.json': {'required': ['schema_version', 'workflow_version', 'generated_at', 'status'], 'owner_step': 'refine'},
        'execution_trace.json': {'required': ['schema_version', 'workflow_version', 'generated_at', 'events'], 'owner_step': 'all'},
        'workflow_metrics.json': {'required': ['schema_version', 'workflow_version', 'generated_at', 'summary', 'stages', 'artifacts'], 'owner_step': 'all'},
        'workflow_state.json': {'required': ['schema_version', 'workflow_version', 'generated_at', 'last_step'], 'owner_step': 'all'},
        '测试用例.json': {'required': ['schema_version', 'workflow_version', 'generated_at', 'metadata', 'testcases'], 'owner_step': 'refine'},
    }


_NESTED_ARTIFACT_CONTRACTS = {
    'requirement_facts.json': 'requirement_facts',
    'test_design_model.json': 'test_design_model',
    'coverage_matrix.json': 'coverage_matrix',
    'quality_gate_report.json': 'quality_gate_report',
    'business_impact.json': 'business_impact',
    'testpoints.json': 'testpoints',
    'decision_register.json': 'decision_register',
    'source_inventory.json': 'source_inventory',
    'workflow_metrics.json': 'workflow_metrics',
}


def _assess_internal_json_payload(filename, payload):
    current_workflow = 'testcase-generator-2026.07'
    contract = _internal_json_contracts().get(filename, {'required': ['schema_version', 'workflow_version', 'generated_at'], 'owner_step': 'unknown'})
    required = contract.get('required', [])
    workflow_required = contract.get('workflow_required', True)
    expected_schema = contract.get('schema_version')
    missing = [key for key in required if key not in payload]
    schema_version = payload.get('schema_version')
    workflow_version = payload.get('workflow_version')
    status = 'ok'
    action = 'reuse'
    severity = 'info'
    reasons = []
    if missing:
        status = 'warning'
        action = 'regenerate_recommended'
        severity = 'warning'
        reasons.append(f"缺少字段: {', '.join(missing)}")
    if not schema_version:
        status = 'incompatible'
        action = 'regenerate_required'
        severity = 'error'
        reasons.append('缺少 schema_version，按旧产物处理')
    elif expected_schema and schema_version != expected_schema:
        status = 'incompatible'
        action = 'regenerate_required'
        severity = 'error'
        reasons.append(f"schema_version={schema_version} 与产物契约 {expected_schema} 不一致")
    if workflow_required:
        if not workflow_version:
            if status == 'ok':
                status = 'warning'
                severity = 'warning'
                action = 'regenerate_recommended'
            reasons.append('缺少 workflow_version，无法判断跨版本兼容性')
        elif workflow_version != current_workflow:
            if status != 'incompatible':
                status = 'warning'
                severity = 'warning'
                action = 'regenerate_recommended'
            reasons.append(f"workflow_version={workflow_version} 与当前 {current_workflow} 不一致")
    nested_contract = _NESTED_ARTIFACT_CONTRACTS.get(filename)
    nested_contract_checked = False
    nested_contract_error = ''
    if nested_contract and not missing and schema_version:
        try:
            validate_contract_payload(nested_contract, payload)
            nested_contract_checked = True
        except Exception as exc:
            status = 'incompatible'
            action = 'regenerate_required'
            severity = 'error'
            nested_contract_error = str(exc)
            reasons.append(f'嵌套契约失败: {exc}')
    if not reasons:
        reasons.append('版本、必要字段与嵌套契约检查通过')
    return {
        'file': filename,
        'status': status,
        'severity': severity,
        'action': action,
        'owner_step': contract.get('owner_step', 'unknown'),
        'schema_version': schema_version or '',
        'expected_schema_version': expected_schema or '',
        'workflow_version': workflow_version or '',
        'missing_required_fields': missing,
        'nested_contract': nested_contract or '',
        'nested_contract_checked': nested_contract_checked,
        'nested_contract_error': nested_contract_error,
        'reasons': reasons,
    }


def generate_internal_json_compatibility_report(output_dir):
    """扫描 .ai-internal 下的 JSON 产物，输出兼容性报告；只提示，不自动删除。"""
    internal_dir = _get_internal_dir(output_dir)
    report_name = 'internal_json_compatibility_report.json'
    files = []
    for path in sorted(internal_dir.glob('*.json')):
        if path.name == report_name:
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                payload = json.load(f)
            if not isinstance(payload, dict):
                files.append({'file': path.name, 'status': 'invalid', 'severity': 'error', 'action': 'manual_fix_required', 'reasons': ['JSON 顶层不是对象']})
                continue
            item = _assess_internal_json_payload(path.name, payload)
            item['size'] = path.stat().st_size
            files.append(item)
        except Exception as e:
            files.append({'file': path.name, 'status': 'invalid', 'severity': 'error', 'action': 'manual_fix_required', 'reasons': [f'JSON 读取失败: {e}']})
    summary = {
        'status': 'failed' if any(f.get('status') in ['invalid', 'incompatible'] for f in files) else 'warning' if any(f.get('status') == 'warning' for f in files) else 'passed',
        'total_files': len(files),
        'ok_count': sum(1 for f in files if f.get('status') == 'ok'),
        'warning_count': sum(1 for f in files if f.get('status') == 'warning'),
        'incompatible_count': sum(1 for f in files if f.get('status') == 'incompatible'),
        'invalid_count': sum(1 for f in files if f.get('status') == 'invalid'),
    }
    report = {'summary': summary, 'files': files, 'policy': {'schema_version_policy': 'per_artifact_contract', 'current_workflow_version': 'testcase-generator-2026.07', 'non_destructive': True}}
    path = _write_internal_json(output_dir, report_name, report)
    log_console(f"  📄 内部JSON兼容性报告: {path}")
    return report, path

def _select_workflow_mode(doc_content, extracted_data, code_analysis):
    mode = _score_workflow_mode(doc_content, extracted_data, code_analysis)
    return mode['selected_mode'], mode['mode_label'], mode['mode_reason']

def _build_requirement_facts(project_info, extracted_data, code_analysis, doc_content=''):
    """兼容旧调用；事实构建统一委托给分析阶段的权威实现。"""
    payload = build_requirement_facts(
        project_info, doc_content, code_analysis, extracted_data
    )
    return payload.get('items', [])


def _build_risk_items(doc_content, code_analysis):
    risk_defs = [
        ('RISK-STATE', '状态流转覆盖', ['状态', '状态码', '流转', '结果码'], 'P1'),
        ('RISK-ASYNC', '异步/回调/通知覆盖', ['异步', '回调', '通知', 'mq', 'webhook'], 'P1'),
        ('RISK-IDEMPOTENT', '幂等与重复触发覆盖', ['幂等', '重复', 'redis', '缓存'], 'P1'),
        ('RISK-EXCEPTION', '异常降级覆盖', ['异常', '失败', '超时', '错误', '降级'], 'P1'),
        ('RISK-DATA', '数据一致性覆盖', ['一致性', '多表', '写入', '同步', '数据'], 'P1'),
        ('RISK-CONFIG', '配置边界覆盖', ['配置', 'apollo', '参数'], 'P2'),
        ('RISK-SECURITY', '权限/安全覆盖', ['权限', '安全', '认证', '敏感'], 'P1'),
    ]
    text = doc_content.lower()
    risks = []
    for prefix, title, keywords, priority in risk_defs:
        hits = [kw for kw in keywords if kw.lower() in text]
        if hits:
            risks.append({'risk_id': _stable_id(prefix, title + ','.join(hits), len(risks)), 'title': title, 'priority': priority, 'trigger_features': hits, 'source': '内置确定性规则', 'confidence': 'medium'})
    if code_analysis and code_analysis.get('scheduled_tasks'):
        risks.append({'risk_id': _stable_id('RISK-JOB', '定时任务覆盖', len(risks)), 'title': '定时任务覆盖', 'priority': 'P1', 'trigger_features': ['scheduled_tasks'], 'source': 'code_analysis.scheduled_tasks', 'confidence': 'medium'})
    return risks


def _normalize_state_label(value):
    value = _compact_text(value, 24)
    value = re.sub(r'^(状态|结果|任务|流程)[：:：\s]*', '', value)
    value = re.sub(r'[，。；;、].*$', '', value)
    return value.strip('"“”\' ')


def _add_state_transition(transitions, seen, from_state, to_state, trigger, source, evidence, priority='P1', confidence='medium'):
    from_state = _normalize_state_label(from_state)
    to_state = _normalize_state_label(to_state)
    if not from_state or not to_state or from_state == to_state:
        return
    key = (from_state, to_state, trigger)
    if key in seen:
        return
    seen.add(key)
    title = f"{from_state} -> {to_state}"
    transitions.append({
        'state_id': _stable_id('STATE', title + trigger, len(transitions)),
        'title': title,
        'from_state': from_state,
        'to_state': to_state,
        'trigger': trigger or '需求描述触发',
        'priority': priority,
        'source': source,
        'evidence': _compact_text(evidence, 160),
        'confidence': confidence,
    })


def _extract_state_transitions(doc_content, code_analysis):
    transitions = []
    seen = set()
    status_keywords = ['状态', '状态码', '流转', '变更', '变为', '转为', '成功', '失败', '完成', '回调', '通知', '审核', '采集', '处理']
    for line in doc_content.splitlines():
        line = _compact_text(line, 180)
        if not line or not any(kw in line for kw in status_keywords):
            continue
        for match in re.finditer(r'([\u4e00-\u9fa5A-Za-z0-9_\-/]{2,24})\s*(?:->|→|=>|到|至|变为|转为|流转为)\s*([\u4e00-\u9fa5A-Za-z0-9_\-/]{2,24})', line):
            _add_state_transition(transitions, seen, match.group(1), match.group(2), '显式状态流转', '需求/设计文档', line)
        if '最终失败' in line and any(kw in line for kw in ['通知', '触发', '回调']):
            _add_state_transition(transitions, seen, '最终失败', '通知/回调触发', '失败结果触发', '需求/设计文档', line)
        if '成功' in line and '失败' in line and any(kw in line for kw in ['状态', '结果', '任务']):
            _add_state_transition(transitions, seen, '成功', '失败', '正反向状态结果', '需求/设计文档', line, priority='P2')
        if '处理中' in line and '完成' in line:
            _add_state_transition(transitions, seen, '处理中', '完成', '处理完成', '需求/设计文档', line)
    if code_analysis:
        for rule in code_analysis.get('business_rules', [])[:30]:
            rule_text = _compact_text(rule, 180)
            if any(kw in rule_text for kw in status_keywords):
                for match in re.finditer(r'([\u4e00-\u9fa5A-Za-z0-9_\-/]{2,24})\s*(?:->|→|=>|到|至|变为|转为)\s*([\u4e00-\u9fa5A-Za-z0-9_\-/]{2,24})', rule_text):
                    _add_state_transition(transitions, seen, match.group(1), match.group(2), '代码规则状态流转', 'code_analysis.business_rules', rule_text, confidence='medium')
        for api in code_analysis.get('api_endpoints', [])[:20]:
            api_text = _compact_text(api, 120)
            if any(kw in api_text for kw in ['callback', '回调', 'status', '状态']):
                _add_state_transition(transitions, seen, '外部回调', '本地状态更新', '接口回调', 'code_analysis.api_endpoints', api_text, confidence='low')
    return transitions


def _review_issue_severity(text):
    if any(kw in text for kw in ['阻断', '严重', 'P1', '高风险', '必须', '中高']):
        return 'P1'
    if any(kw in text for kw in ['重要', 'P2', 'major', '风险', '不明确', '待确认', '需确认', '建议补充']):
        return 'P2'
    return 'P3'


def _issue_has_decision(text):
    return any(kw in text for kw in ['已确认', '结论：', '处理决策', '决策：', '无需处理', '已解决', '评审结论'])


def _clean_review_issue_line(line):
    line = _compact_text(line, 120)
    line = re.sub(r'^[-*+\d\.、\s]+', '', line)
    if '|' in line:
        cells = [c.strip() for c in line.split('|') if c.strip() and set(c.strip()) != {'-'}]
        line = '；'.join(cells[:3])
    return line


def _extract_review_questions(output_dir, project_info, quality_issues):
    questions = []
    seen = set()
    internal_dir = _get_internal_dir(Path(output_dir))
    decision_items = {}
    decision_path = internal_dir / 'decision_register.json'
    if decision_path.exists():
        try:
            decision_payload = json.loads(decision_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            decision_payload = {}
        decision_items = {
            str(item.get('issue_id')): item
            for item in decision_payload.get('items', [])
            if isinstance(item, dict) and item.get('issue_id')
        }

    def add_question(title, source, raw_text, confidence='medium'):
        title = _clean_review_issue_line(title)
        if not title or len(title) < 4 or title in seen:
            return
        seen.add(title)
        severity = _review_issue_severity(raw_text or title)
        questions.append({
            'question_id': _stable_id('RQ', title, len(questions)),
            'title': title,
            'severity': severity,
            'source': source,
            'evidence': _compact_text(raw_text or title, 180),
            'decision_status': 'decided' if _issue_has_decision(raw_text or title) else 'pending',
            'requires_testpoint': severity in ['P1', 'P2'],
            'confidence': confidence,
        })

    # 优先消费评审阶段的结构化决策，避免再次扫描 Markdown 时把表头、
    # 说明文字和已经处理的条目误识别为新的待确认问题。
    review_data_files = sorted(
        _get_internal_dir(Path(output_dir)).glob('*评审数据.json'),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for review_data_path in review_data_files[:1]:
        try:
            review_payload = json.loads(review_data_path.read_text(encoding='utf-8'))
        except Exception:
            review_payload = {}
        severity_map = {'critical': 'P1', 'major': 'P2', 'minor': 'P3'}
        for category in review_payload.get('categories', []):
            for item in category.get('items', []):
                title = _clean_review_issue_line(item.get('title', ''))
                if not title or title in seen:
                    continue
                seen.add(title)
                issue_id = item.get('id') or _stable_id('RQ', title, len(questions))
                registered = decision_items.get(str(issue_id), {})
                decision_source = registered or item
                decision = str(decision_source.get('decision', '')).strip()
                decision_type = str(decision_source.get('decision_type', '')).strip()
                severity = severity_map.get(str(item.get('severity', '')).lower(), 'P3')
                complete = decision_is_complete(decision_source)
                decision_status = (
                    'deferred' if complete and decision_type == 'defer'
                    else 'decided' if complete
                    else 'pending'
                )
                rejected = complete and decision_type in {'reject', 'not_applicable'}
                affects_test_design = bool(
                    decision_source.get('affects_test_design', severity in ['P1', 'P2'])
                )
                questions.append({
                    'question_id': issue_id,
                    'title': title,
                    'severity': severity,
                    'source': decision_path.name if registered else review_data_path.name,
                    'evidence': _compact_text(
                        f"{item.get('description', '')}；评审决策：{decision}；类型：{decision_type}", 180
                    ),
                    'decision_status': decision_status,
                    'decision_type': decision_type,
                    'requires_testpoint': severity in ['P1', 'P2']
                    and affects_test_design
                    and not rejected,
                    'confidence': 'high',
                })
        if questions:
            return questions

    for issue in project_info.get('pending_issues', [])[:20]:
        add_question(str(issue), 'project_info.pending_issues', str(issue), 'high')

    for qi in quality_issues[:20]:
        title = f"测试点质量问题：{qi.get('title', '')}"
        raw = f"{title}；{', '.join(qi.get('issues', []))}"
        add_question(title, 'quality_issues', raw, 'medium')

    review_files = sorted(output_dir.glob('*评审报告.md')) + sorted(output_dir.glob('01_*.md'))
    issue_keywords = ['待确认', '需确认', '不明确', '风险', '严重', '重要', '建议', '问题', '遗漏', '冲突']
    for report in review_files[:3]:
        try:
            content = report.read_text(encoding='utf-8')
        except Exception:
            continue
        for line in content.splitlines():
            if any(kw in line for kw in issue_keywords):
                cleaned = _clean_review_issue_line(line)
                if cleaned and not cleaned.startswith('#'):
                    add_question(cleaned, report.name, line, 'medium')
            if len(questions) >= 40:
                break
    return questions


def _load_curated_testpoints(output_dir):
    """Load a requirement-specific reviewed seed without hard-coding domain rules."""
    seed_path = _get_internal_dir(Path(output_dir)) / 'curated_testpoints.json'
    if not seed_path.exists():
        return None, seed_path
    payload = json.loads(seed_path.read_text(encoding='utf-8'))
    testpoints = payload.get('test_points') if isinstance(payload, dict) else None
    if not isinstance(testpoints, list) or not testpoints:
        raise RuntimeError(f'人工精炼测试点文件无有效 test_points：{seed_path}')
    required_fields = ('title', 'category', 'type', 'steps', 'expected')
    for index, point in enumerate(testpoints, 1):
        missing = [field for field in required_fields if not str(point.get(field, '')).strip()]
        if missing:
            raise RuntimeError(
                f'人工精炼测试点第 {index} 项缺少字段：{", ".join(missing)}'
            )
        point.setdefault('source_type', '评审结论推导')
        point.setdefault('confidence', 'high')
        point.setdefault('candidate_quality', 'high')
        point.setdefault('xmind_entry_status', 'ready')
        point.setdefault('requires_human_confirm', False)
    return testpoints, seed_path


def _match_text_to_item(testpoint, title, features=None):
    haystack = f"{testpoint.get('title', '')} {testpoint.get('category', '')} {testpoint.get('type', '')} {testpoint.get('steps', '')} {testpoint.get('expected', '')}".lower()
    candidates = [str(title or '')]
    candidates.extend(features or [])
    tokens = []
    for candidate in candidates:
        tokens.extend([t for t in re.split(r'[/与和\s，,；;：:、\-]+', str(candidate).lower()) if len(t) >= 2])
    return any(token in haystack for token in tokens)


def _match_testpoint_to_risk(testpoint, risk):
    return _match_text_to_item(testpoint, risk.get('title', ''), risk.get('trigger_features', []))


def _match_testpoint_to_state(testpoint, state):
    return _match_text_to_item(testpoint, state.get('title', ''), [state.get('from_state', ''), state.get('to_state', ''), state.get('trigger', '')])


def _match_testpoint_to_review_question(testpoint, question):
    return _match_text_to_item(testpoint, question.get('title', ''), [question.get('evidence', '')[:40]])


def _attach_testpoint_metadata(testpoints, risks, state_transitions=None, review_questions=None):
    state_transitions = state_transitions or []
    review_questions = review_questions or []
    for idx, tp in enumerate(testpoints):
        tp.setdefault('testpoint_id', _stable_id('TP', tp.get('title', ''), idx))
        tp.setdefault('source_type', 'AI生成')
        tp.setdefault('coverage_ids', [])
        for risk in risks:
            if _match_testpoint_to_risk(tp, risk) and risk['risk_id'] not in tp['coverage_ids']:
                tp['coverage_ids'].append(risk['risk_id'])
        for state in state_transitions:
            if _match_testpoint_to_state(tp, state) and state['state_id'] not in tp['coverage_ids']:
                tp['coverage_ids'].append(state['state_id'])
        for question in review_questions:
            if not question.get('requires_testpoint', True):
                continue
            if _match_testpoint_to_review_question(tp, question) and question['question_id'] not in tp['coverage_ids']:
                tp['coverage_ids'].append(question['question_id'])
        if len(tp['coverage_ids']) > 1 and 'COVERAGE-GENERAL' in tp['coverage_ids']:
            tp['coverage_ids'].remove('COVERAGE-GENERAL')
        if not tp['coverage_ids'] and (risks or state_transitions or review_questions):
            tp['coverage_ids'].append('COVERAGE-GENERAL')
    return testpoints


def _auto_repair_p1_risk_gaps(testpoints, risks):
    covered = {cid for tp in testpoints for cid in tp.get('coverage_ids', [])}
    repaired = []
    for risk in risks:
        if risk.get('priority') != 'P1' or risk['risk_id'] in covered:
            continue
        idx = len(testpoints) + len(repaired)
        tp = {'testpoint_id': _stable_id('TP-AUTO', risk['risk_id'], idx), 'title': f"{risk['title']}验证", 'category': '风险补充测试', 'type': '异常' if '异常' in risk['title'] else '功能', 'steps': f"1. 准备命中{risk['title']}的测试数据或环境\n2. 触发对应业务流程并记录关键返回、表数据或缓存变化\n3. 对照需求和评审结论确认风险被覆盖", 'expected': f"{risk['title']}有明确验证结果；异常或边界场景不会导致主流程不可控失败", 'auto_repaired': True, 'repair_source_gate': 'G2', 'coverage_ids': [risk['risk_id']], 'coverage_target': risk['risk_id'], 'source_matrix_item': f"coverage_matrix.risks.{risk['risk_id']}", 'confidence': 'medium', 'requires_human_confirm': True}
        repaired.append(tp)
    if repaired:
        log_console(f"  🔧 G2 自动补充 P1 风险测试点: {len(repaired)} 个")
        testpoints.extend(repaired)
    return testpoints, repaired


def _auto_repair_state_gaps(testpoints, state_transitions):
    covered = {cid for tp in testpoints for cid in tp.get('coverage_ids', [])}
    repaired = []
    for state in state_transitions:
        if state.get('priority') != 'P1' or state['state_id'] in covered:
            continue
        idx = len(testpoints) + len(repaired)
        title = f"{state['from_state']}到{state['to_state']}状态流转验证"
        tp = {
            'testpoint_id': _stable_id('TP-AUTO-STATE', state['state_id'], idx),
            'title': title,
            'category': '状态流转测试',
            'type': '流程',
            'steps': f"1. 构造业务对象处于{state['from_state']}状态\n2. 触发{state.get('trigger', '对应业务动作')}并观察状态变化\n3. 校验状态、通知/回调、表数据或日志副作用",
            'expected': f"状态从{state['from_state']}正确流转到{state['to_state']}；未出现重复触发、漏触发或状态回退",
            'auto_repaired': True,
            'repair_source_gate': 'G3',
            'coverage_ids': [state['state_id']],
            'coverage_target': state['state_id'],
            'source_matrix_item': f"coverage_matrix.state_transitions.{state['state_id']}",
            'confidence': state.get('confidence', 'medium'),
            'requires_human_confirm': True,
        }
        repaired.append(tp)
    if repaired:
        log_console(f"  🔧 G3 自动补充状态流转测试点: {len(repaired)} 个")
        testpoints.extend(repaired)
    return testpoints, repaired


def _auto_repair_review_question_gaps(testpoints, review_questions):
    covered = {cid for tp in testpoints for cid in tp.get('coverage_ids', [])}
    repaired = []
    for question in review_questions:
        if question.get('severity') not in ['P1', 'P2'] or question.get('decision_status') == 'decided' or question['question_id'] in covered:
            continue
        idx = len(testpoints) + len(repaired)
        tp = {
            'testpoint_id': _stable_id('TP-AUTO-RQ', question['question_id'], idx),
            'title': f"评审问题确认验证：{question['title'][:24]}",
            'category': '评审问题验证',
            'type': '确认',
            'steps': f"1. 复核评审问题：{question['title']}\n2. 准备能体现该问题边界或风险的测试数据\n3. 执行业务流程并记录实际结果，提交评审确认",
            'expected': '评审问题有明确处理决策或可验证结果；未确认前不作为最终验收结论',
            'auto_repaired': True,
            'repair_source_gate': 'G4',
            'coverage_ids': [question['question_id']],
            'coverage_target': question['question_id'],
            'source_matrix_item': f"coverage_matrix.review_questions.{question['question_id']}",
            'confidence': question.get('confidence', 'medium'),
            'requires_human_confirm': True,
        }
        repaired.append(tp)
    if repaired:
        log_console(f"  🔧 G4 自动补充评审问题验证点: {len(repaired)} 个")
        testpoints.extend(repaired)
    return testpoints, repaired


def _build_coverage_matrix(requirement_facts, risks, state_transitions, review_questions, testpoints):
    mappings = []
    for risk in risks:
        covered_by = [tp['testpoint_id'] for tp in testpoints if risk['risk_id'] in tp.get('coverage_ids', [])]
        mappings.append({'coverage_id': risk['risk_id'], 'coverage_type': 'risk', 'title': risk['title'], 'priority': risk['priority'], 'covered_by': covered_by, 'status': 'covered' if covered_by else 'uncovered'})
    for state in state_transitions:
        covered_by = [tp['testpoint_id'] for tp in testpoints if state['state_id'] in tp.get('coverage_ids', [])]
        mappings.append({'coverage_id': state['state_id'], 'coverage_type': 'state_transition', 'title': state['title'], 'priority': state['priority'], 'from_state': state['from_state'], 'to_state': state['to_state'], 'covered_by': covered_by, 'status': 'covered' if covered_by else 'uncovered'})
    for question in review_questions:
        covered_by = [tp['testpoint_id'] for tp in testpoints if question['question_id'] in tp.get('coverage_ids', [])]
        status = 'decided' if question.get('decision_status') == 'decided' else ('covered' if covered_by else 'uncovered')
        mappings.append({'coverage_id': question['question_id'], 'coverage_type': 'review_question', 'title': question['title'], 'priority': question['severity'], 'decision_status': question.get('decision_status', 'pending'), 'covered_by': covered_by, 'status': status})
    p1_risks = [r for r in risks if r.get('priority') == 'P1']
    covered_p1 = [m for m in mappings if m['coverage_type'] == 'risk' and m['priority'] == 'P1' and m['covered_by']]
    critical_states = [s for s in state_transitions if s.get('priority') == 'P1']
    covered_states = [m for m in mappings if m['coverage_type'] == 'state_transition' and m.get('priority') == 'P1' and m['covered_by']]
    important_questions = [q for q in review_questions if q.get('severity') in ['P1', 'P2']]
    handled_questions = [m for m in mappings if m['coverage_type'] == 'review_question' and m.get('priority') in ['P1', 'P2'] and (m.get('covered_by') or m.get('decision_status') == 'decided')]
    return {
        'requirement_facts': requirement_facts,
        'risks': risks,
        'state_transitions': state_transitions,
        'review_questions': review_questions,
        'mappings': mappings,
        'statistics': {
            'requirement_fact_count': len(requirement_facts),
            'risk_count': len(risks),
            'p1_risk_count': len(p1_risks),
            'p1_risk_covered': len(covered_p1),
            'p1_risk_coverage_rate': 1.0 if not p1_risks else round(len(covered_p1) / len(p1_risks), 4),
            'state_transition_count': len(state_transitions),
            'critical_state_transition_count': len(critical_states),
            'critical_state_transition_covered': len(covered_states),
            'critical_state_transition_coverage_rate': 1.0 if not critical_states else round(len(covered_states) / len(critical_states), 4),
            'review_question_count': len(review_questions),
            'important_review_question_count': len(important_questions),
            'important_review_question_handled': len(handled_questions),
            'important_review_question_coverage_rate': 1.0 if not important_questions else round(len(handled_questions) / len(important_questions), 4),
            'testpoint_count': len(testpoints),
        }
    }


def _extract_tokens_for_consistency(value):
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value or '')
    tokens = []
    for token in re.split(r'[^0-9A-Za-z\u4e00-\u9fa5_]+', text.lower()):
        if len(token) >= 2:
            tokens.append(token)
    return set(tokens)


def _has_token_overlap(left, right):
    left_tokens = _extract_tokens_for_consistency(left)
    right_tokens = _extract_tokens_for_consistency(right)
    if not left_tokens or not right_tokens:
        return False
    return bool(left_tokens & right_tokens)


def _load_code_impact_for_consistency(output_dir):
    payload, path = _load_internal_json_payload(output_dir, 'code_impact.json')
    if payload:
        return payload, path
    return {
        'status': 'missing',
        'impact_confidence': 'none',
        'summary': {},
        'impact_scope': {},
        'unconfirmed_evidence': ['未找到 code_impact.json，无法进行代码影响一致性校验'],
    }, path


def generate_consistency_report(output_dir, requirement_facts, coverage_matrix, gate_report):
    """生成需求-代码-测试覆盖一致性报告。第一阶段只提示，不直接阻断。"""
    code_impact, code_impact_path = _load_code_impact_for_consistency(output_dir)
    mappings = coverage_matrix.get('mappings', [])
    risks = coverage_matrix.get('risks', [])
    states = coverage_matrix.get('state_transitions', [])
    questions = coverage_matrix.get('review_questions', [])
    findings = []

    def add(level, category, title, detail, evidence=None, recommendation=''):
        findings.append({
            'level': level,
            'category': category,
            'title': title,
            'detail': detail,
            'evidence': evidence or {},
            'recommendation': recommendation,
        })

    code_status = code_impact.get('status', 'missing')
    impact_scope = code_impact.get('impact_scope', {})
    code_summary = code_impact.get('summary', {})
    if code_status not in ['completed']:
        add('C2', '代码证据缺失', '代码影响证据不可用', code_impact.get('skip_reason') or '代码影响分析未完成', {'code_impact_path': str(code_impact_path)}, '深度模式下需要补充代码影响证据或人工确认跳过原因')

    if code_summary.get('files_changed', 0) and not risks and not states:
        add('C2', '代码-测试设计脱节', '存在代码变更但未形成风险或状态模型', f"代码变更文件 {code_summary.get('files_changed')} 个，但覆盖矩阵未生成风险/状态对象", {'files_changed': code_summary.get('files_changed')}, '检查需求文档是否缺少业务流程描述，或补充代码影响到测试设计模型')

    uncovered_blocking = [m for m in mappings if m.get('priority') in ['P1', 'P2'] and m.get('coverage_type') in ['risk', 'state_transition', 'review_question'] and not m.get('covered_by') and m.get('decision_status') != 'decided']
    for mapping in uncovered_blocking[:20]:
        level = 'C1' if mapping.get('priority') == 'P1' else 'C2'
        add(level, '覆盖缺口', f"{mapping.get('title', '')} 未覆盖", '严重/重要覆盖对象没有测试点或处理决策', {'coverage_id': mapping.get('coverage_id'), 'coverage_type': mapping.get('coverage_type')}, '补充测试点或在评审报告中给出处理决策')

    code_objects = []
    for key in ['entry_points', 'api_endpoints', 'scheduled_tasks', 'sql_analysis', 'business_rules', 'config_values']:
        for item in _as_list(impact_scope.get(key)):
            code_objects.append({'type': key, 'value': item})
    coverage_objects = risks + states + questions
    unmatched_code = []
    for obj in code_objects[:80]:
        if not any(_has_token_overlap(obj.get('value'), cov) for cov in coverage_objects):
            unmatched_code.append(obj)
    if unmatched_code and code_impact.get('impact_confidence') in ['high', 'medium']:
        add('C3', '代码影响未映射', f"{len(unmatched_code)} 个代码影响对象未映射到覆盖矩阵", '存在代码入口/SQL/配置/规则未关联到风险、状态或评审问题', {'samples': unmatched_code[:10]}, '后续深度模式可按样例补充影响选择和测试映射')

    fact_items = requirement_facts or coverage_matrix.get('requirement_facts', [])
    inferred_or_low = [f for f in fact_items if f.get('confidence') in ['low', 'medium'] or '推断' in f.get('type', '')]
    if inferred_or_low:
        add('C4', '需求事实置信度', f"{len(inferred_or_low)} 个需求事实需要确认", '存在中低置信度或推断类需求事实', {'samples': inferred_or_low[:10]}, '评审时确认这些事实是否能进入正式测试依据')

    gate_blocking = [g for g in gate_report.get('gates', []) if g.get('blocking') and g.get('status') == 'failed']
    if gate_blocking:
        add('C1', '质量门禁', '存在阻断级质量门禁失败', '质量门禁报告中仍有阻断项', {'gates': gate_blocking}, '先处理阻断门禁，再生成下游产物')

    level_order = {'C1': 0, 'C2': 1, 'C3': 2, 'C4': 3}
    findings.sort(key=lambda item: level_order.get(item.get('level'), 99))
    summary = {
        'status': 'attention_required' if any(f['level'] in ['C1', 'C2'] for f in findings) else 'passed_with_notes' if findings else 'passed',
        'c1_count': sum(1 for f in findings if f.get('level') == 'C1'),
        'c2_count': sum(1 for f in findings if f.get('level') == 'C2'),
        'c3_count': sum(1 for f in findings if f.get('level') == 'C3'),
        'c4_count': sum(1 for f in findings if f.get('level') == 'C4'),
        'finding_count': len(findings),
    }
    report = {
        'summary': summary,
        'inputs': {
            'code_impact_path': str(code_impact_path),
            'code_impact_status': code_status,
            'impact_confidence': code_impact.get('impact_confidence', 'none'),
            'requirement_fact_count': len(fact_items),
            'coverage_mapping_count': len(mappings),
        },
        'findings': findings,
        'decision': {
            'blocking': False,
            'reason': '第一阶段一致性报告只提示，不直接阻断；深度模式稳定后可将 C1 接入硬门禁',
        },
    }
    path = _write_internal_json(output_dir, 'consistency_report.json', report)
    log_console(f"  📄 一致性校验报告: {path}")
    return report, path

def _run_minimal_quality_gates(coverage_matrix, testpoints):
    gate_results = []
    def add(gate_id, name, status, message, blocking=False):
        gate_results.append({'gate_id': gate_id, 'name': name, 'status': status, 'message': message, 'blocking': blocking})
    missing_source = [f for f in coverage_matrix.get('requirement_facts', []) if not f.get('source')]
    add('G1', '来源完整性', 'passed' if not missing_source else 'failed', f"无来源需求事实 {len(missing_source)} 个", bool(missing_source))
    uncovered_p1 = [m for m in coverage_matrix.get('mappings', []) if m.get('coverage_type') == 'risk' and m.get('priority') == 'P1' and not m.get('covered_by')]
    add('G2', 'P1 风险覆盖', 'passed' if not uncovered_p1 else 'failed', f"未覆盖 P1 风险 {len(uncovered_p1)} 个", bool(uncovered_p1))
    uncovered_states = [m for m in coverage_matrix.get('mappings', []) if m.get('coverage_type') == 'state_transition' and m.get('priority') == 'P1' and not m.get('covered_by')]
    add('G3', '状态流转覆盖', 'passed' if not uncovered_states else 'failed', f"未覆盖关键状态流转 {len(uncovered_states)} 条", bool(uncovered_states))
    uncovered_questions = [m for m in coverage_matrix.get('mappings', []) if m.get('coverage_type') == 'review_question' and m.get('priority') in ['P1', 'P2'] and not m.get('covered_by') and m.get('decision_status') != 'decided']
    add('G4', '评审问题覆盖', 'passed' if not uncovered_questions else 'failed', f"未处理严重/重要评审问题 {len(uncovered_questions)} 个", bool(uncovered_questions))
    matrix_ids = {m['coverage_id'] for m in coverage_matrix.get('mappings', [])}
    outside = [tp.get('testpoint_id', tp.get('title', '')) for tp in testpoints if (not tp.get('coverage_ids') or (set(tp.get('coverage_ids', [])) == {'COVERAGE-GENERAL'} and matrix_ids))]
    add('G5', '矩阵外测试点', 'warning' if outside else 'passed', f"矩阵外或泛化测试点 {len(outside)} 个", False)
    placeholder_words = ['待补充', 'TODO', '占位', 'xxx', 'XXX']
    placeholder_hits = []
    for tp in testpoints:
        combined = f"{tp.get('title', '')} {tp.get('steps', '')} {tp.get('expected', '')}"
        if any(word in combined for word in placeholder_words):
            placeholder_hits.append(tp.get('testpoint_id', tp.get('title', '')))
    add('G6', '占位内容', 'passed' if not placeholder_hits else 'failed', f"残留占位内容 {len(placeholder_hits)} 处", bool(placeholder_hits))
    blocking = any(item['blocking'] and item['status'] == 'failed' for item in gate_results)
    return {'summary': {'status': 'blocked' if blocking else 'passed', 'blocking_count': sum(1 for item in gate_results if item['blocking'] and item['status'] == 'failed'), 'warning_count': sum(1 for item in gate_results if item['status'] == 'warning')}, 'gates': gate_results}


def _refresh_gate_summary(gate_report):
    gates = gate_report.get('gates', [])
    blocking_count = sum(1 for item in gates if item.get('blocking') and item.get('status') == 'failed')
    gate_report['summary'] = {
        'status': 'blocked' if blocking_count else 'passed',
        'blocking_count': blocking_count,
        'warning_count': sum(1 for item in gates if item.get('status') == 'warning'),
    }
    return gate_report


def _apply_deep_consistency_gate(gate_report, consistency_report, mode_details):
    """深度模式下将 C1 一致性问题升级为硬门禁。"""
    selected_mode = mode_details.get('selected_mode')
    c1_count = consistency_report.get('summary', {}).get('c1_count', 0)
    gates = [item for item in gate_report.get('gates', []) if item.get('gate_id') != 'G7']
    if selected_mode == 'deep' and c1_count:
        gate = {
            'gate_id': 'G7',
            'name': '深度一致性',
            'status': 'failed',
            'message': f"深度模式存在 C1 一致性问题 {c1_count} 个",
            'blocking': True,
        }
        consistency_report['decision'] = {
            'blocking': True,
            'reason': '深度模式下 C1 一致性问题已接入硬门禁，需处理后再生成 XMind',
        }
    else:
        status = 'passed' if c1_count == 0 else 'warning'
        gate = {
            'gate_id': 'G7',
            'name': '深度一致性',
            'status': status,
            'message': f"当前模式 {selected_mode or 'unknown'}，C1 一致性问题 {c1_count} 个",
            'blocking': False,
        }
        if not consistency_report.get('decision'):
            consistency_report['decision'] = {'blocking': False, 'reason': '未触发深度一致性硬门禁'}
    gates.append(gate)
    gate_report['gates'] = gates
    return _refresh_gate_summary(gate_report), consistency_report


def _build_consistency_fix_suggestions(coverage_matrix, gate_report, consistency_report, mode_details):
    """把一致性问题转换成可执行修复建议，供硬门禁阻断后直接处理。"""
    mappings = {m.get('coverage_id'): m for m in coverage_matrix.get('mappings', []) if m.get('coverage_id')}
    suggestions = []
    seen = set()

    def add(source_type, source_id, priority, action, title, detail, evidence=None, requires_human_confirm=False):
        key = (source_type, source_id or title, action)
        if key in seen:
            return
        seen.add(key)
        suggestions.append({
            'suggestion_id': _stable_id('FIX', f"{source_type}|{source_id}|{action}|{title}", len(suggestions)),
            'source_type': source_type,
            'source_id': source_id,
            'priority': priority,
            'action': action,
            'title': title,
            'detail': detail,
            'evidence': evidence or {},
            'requires_human_confirm': requires_human_confirm,
        })

    action_by_gate = {
        'G1': ('补齐需求事实来源', '为无来源需求事实补充文档章节、表格、接口字段或代码证据。'),
        'G2': ('补充 P1 风险测试点', '为未覆盖的 P1 风险补充正向、异常、边界和数据一致性测试点。'),
        'G3': ('补充关键状态流转测试点', '为缺失的关键状态边补充触发条件、前置状态、目标状态和异常回滚验证。'),
        'G4': ('处理严重/重要评审问题', '补充测试点，或在评审报告中给出明确处理决策后再进入下游。'),
        'G6': ('清理占位内容', '替换待补充、TODO、占位、xxx 等不可执行描述。'),
        'G7': ('处理深度一致性 C1', '先处理一致性报告中的 C1 问题，再生成 XMind 或导入用例。'),
    }
    for gate in gate_report.get('gates', []):
        if gate.get('blocking') and gate.get('status') == 'failed':
            title, detail = action_by_gate.get(gate.get('gate_id'), ('处理阻断门禁', gate.get('message', '')))
            add('quality_gate', gate.get('gate_id'), 'P1', 'resolve_quality_gate', title, detail, {'gate': gate}, True)

    for finding in consistency_report.get('findings', []):
        level = finding.get('level')
        if level not in ['C1', 'C2']:
            continue
        priority = 'P1' if level == 'C1' else 'P2'
        evidence = finding.get('evidence', {}) or {}
        coverage_id = evidence.get('coverage_id')
        category = finding.get('category', '')
        source_id = coverage_id or finding.get('title')
        if category == '覆盖缺口' and coverage_id:
            mapping = mappings.get(coverage_id, {})
            coverage_type = evidence.get('coverage_type') or mapping.get('coverage_type')
            if coverage_type == 'review_question':
                add('consistency_finding', source_id, priority, 'add_testpoint_or_review_decision', finding.get('title', '覆盖缺口'), '补充可执行测试点；若不纳入测试，需在评审报告中给出处理决策。', {'finding': finding, 'mapping': mapping}, True)
            else:
                add('consistency_finding', source_id, priority, 'add_testpoint', finding.get('title', '覆盖缺口'), '围绕该风险/状态流转补充前置条件、步骤、期望结果、异常分支和数据校验。', {'finding': finding, 'mapping': mapping}, False)
        elif category == '质量门禁':
            for gate in evidence.get('gates', []):
                title, detail = action_by_gate.get(gate.get('gate_id'), ('处理阻断门禁', gate.get('message', '')))
                add('consistency_finding', gate.get('gate_id'), priority, 'resolve_quality_gate', title, detail, {'finding': finding, 'gate': gate}, True)
        elif category == '代码证据缺失':
            add('consistency_finding', source_id, priority, 'complete_code_impact_evidence', finding.get('title', '代码证据缺失'), '重新执行代码影响分析，或记录人工确认的跳过原因、影响范围和风险接受结论。', {'finding': finding}, True)
        elif category == '代码-测试设计脱节':
            add('consistency_finding', source_id, priority, 'map_code_change_to_test_design', finding.get('title', '代码-测试设计脱节'), '将代码变更入口、SQL、配置或规则映射为风险、状态流转或评审问题，并补充对应测试点。', {'finding': finding}, True)
        else:
            add('consistency_finding', source_id, priority, 'manual_review', finding.get('title', '一致性问题'), finding.get('recommendation') or finding.get('detail', ''), {'finding': finding}, True)

    priority_order = {'P1': 0, 'P2': 1, 'P3': 2}
    suggestions.sort(key=lambda item: (priority_order.get(item.get('priority'), 9), item.get('action', ''), item.get('title', '')))
    blocking_actions = [s for s in suggestions if s.get('priority') == 'P1']
    summary = {
        'status': 'action_required' if blocking_actions else 'attention_required' if suggestions else 'passed',
        'suggestion_count': len(suggestions),
        'p1_count': sum(1 for s in suggestions if s.get('priority') == 'P1'),
        'p2_count': sum(1 for s in suggestions if s.get('priority') == 'P2'),
        'requires_human_confirm_count': sum(1 for s in suggestions if s.get('requires_human_confirm')),
        'selected_mode': mode_details.get('selected_mode'),
    }
    return {'summary': summary, 'suggestions': suggestions}



def _grade_candidate_testpoint(suggestion, mapping, coverage_type):
    """评估自动补点候选是否适合进入 XMind。"""
    priority = suggestion.get('priority', 'P2')
    evidence = suggestion.get('evidence', {}) or {}
    finding = evidence.get('finding', {}) or {}
    has_mapping = bool(mapping.get('coverage_id') or mapping.get('title'))
    has_finding = bool(finding.get('title') or finding.get('category'))
    quality = 'medium' if has_mapping and has_finding else 'low'
    if priority == 'P1' and has_mapping and has_finding and coverage_type in ['risk', 'state_transition']:
        quality = 'high'
    if quality == 'high':
        return quality, 'ready_after_review', '覆盖对象和一致性发现均明确，人工确认后可进入 XMind'
    if quality == 'medium':
        return quality, 'review_required', '候选点具备覆盖对象，但仍需人工补充业务数据或断言细节'
    return quality, 'hold_for_manual_refine', '候选点证据不足，需人工完善后再进入 XMind'

def _auto_repair_from_fix_suggestions(testpoints, fix_suggestions):
    """把可自动处理的一致性修复建议转换为候选测试点。"""
    covered = {cid for tp in testpoints for cid in tp.get('coverage_ids', [])}
    repaired = []
    plural_by_type = {
        'risk': 'risks',
        'state_transition': 'state_transitions',
        'review_question': 'review_questions',
    }
    for suggestion in fix_suggestions.get('suggestions', []):
        if suggestion.get('action') != 'add_testpoint':
            continue
        target_id = suggestion.get('source_id')
        if not target_id or target_id in covered:
            continue
        evidence = suggestion.get('evidence', {}) or {}
        mapping = evidence.get('mapping', {}) or {}
        finding = evidence.get('finding', {}) or {}
        coverage_type = mapping.get('coverage_type') or (finding.get('evidence', {}) or {}).get('coverage_type') or 'risk'
        title = mapping.get('title') or finding.get('title') or suggestion.get('title') or target_id
        candidate_quality, xmind_entry_status, xmind_entry_reason = _grade_candidate_testpoint(suggestion, mapping, coverage_type)
        idx = len(testpoints) + len(repaired)
        if coverage_type == 'state_transition':
            from_state = mapping.get('from_state', '起始状态')
            to_state = mapping.get('to_state', '目标状态')
            tp_title = f"一致性补充：{title}"
            category = '一致性状态流转补充测试'
            tp_type = '流程'
            steps = f"1. 构造业务对象处于{from_state}\n2. 触发{title}对应的业务动作\n3. 校验状态流转到{to_state}，并检查表数据、日志或外部副作用"
            expected = f"{title}状态流转有明确验证结果；异常路径不会导致状态错乱、重复处理或漏处理"
        else:
            tp_title = f"一致性补充：{title}"
            category = '一致性风险补充测试' if coverage_type == 'risk' else '一致性覆盖补充测试'
            tp_type = '异常' if any(word in title for word in ['异常', '失败', '错误', '超时']) else '功能'
            steps = f"1. 准备命中{title}的测试数据、配置或外部依赖\n2. 执行对应业务流程并记录接口返回、表数据、缓存、日志等证据\n3. 对照需求、代码影响和评审结论确认覆盖结果"
            expected = f"{title}有明确可复核的验证结果；关键数据一致，异常或边界场景处理符合预期"
        tp = {
            'testpoint_id': _stable_id('TP-AUTO-CONS', target_id, idx),
            'title': tp_title,
            'category': category,
            'type': tp_type,
            'steps': steps,
            'expected': expected,
            'auto_repaired': True,
            'repair_source_gate': 'G7',
            'repair_source_suggestion': suggestion.get('suggestion_id'),
            'coverage_ids': [target_id],
            'coverage_target': target_id,
            'source_matrix_item': f"coverage_matrix.{plural_by_type.get(coverage_type, coverage_type)}.{target_id}",
            'confidence': candidate_quality,
            'candidate_quality': candidate_quality,
            'xmind_entry_status': xmind_entry_status,
            'xmind_entry_reason': xmind_entry_reason,
            'requires_human_confirm': True,
        }
        repaired.append(tp)
        covered.add(target_id)
    if repaired:
        log_console(f"  [修复] G7 自动补充一致性候选测试点: {len(repaired)} 个")
        testpoints.extend(repaired)
    return testpoints, repaired

def _write_test_design_summary(output_dir, mode_label, mode_reason, coverage_matrix, quality_gate_report, fix_suggestions=None):
    design_path = output_dir / '02_测试设计说明.md'
    stats = coverage_matrix.get('statistics', {})
    risks = coverage_matrix.get('risks', [])
    states = coverage_matrix.get('state_transitions', [])
    questions = coverage_matrix.get('review_questions', [])
    mappings = coverage_matrix.get('mappings', [])
    risk_rows = []
    for risk in risks[:8]:
        mapping = next((m for m in mappings if m['coverage_id'] == risk['risk_id']), {})
        covered = ', '.join(mapping.get('covered_by', [])) or '未覆盖'
        risk_rows.append(f"| {risk['title']} | {risk['priority']} | {', '.join(risk.get('trigger_features', []))} | {covered} |")
    state_rows = []
    for state in states[:8]:
        mapping = next((m for m in mappings if m['coverage_id'] == state['state_id']), {})
        covered = ', '.join(mapping.get('covered_by', [])) or '未覆盖'
        state_rows.append(f"| {state['from_state']} | {state['to_state']} | {state.get('trigger', '')} | {covered} |")
    question_rows = []
    for question in questions[:8]:
        mapping = next((m for m in mappings if m['coverage_id'] == question['question_id']), {})
        handled = ', '.join(mapping.get('covered_by', [])) or question.get('decision_status', 'pending')
        question_rows.append(f"| {question['title']} | {question['severity']} | {question.get('decision_status', 'pending')} | {handled} |")
    gate_rows = [f"| {g['gate_id']} {g['name']} | {g['status']} | {g['message']} |" for g in quality_gate_report.get('gates', [])]
    fix_suggestions = fix_suggestions or {}
    xmind_entry_summary = fix_suggestions.get('xmind_entry_summary', {})
    candidate_quality_summary = fix_suggestions.get('candidate_quality_summary', {})
    hold_items = [item for item in fix_suggestions.get('auto_applied', []) if item.get('xmind_entry_status') == 'hold_for_manual_refine']
    hold_rows = []
    for item in hold_items[:10]:
        title = str(item.get('title', '')).replace('|', '/')
        target = str(item.get('coverage_target', '')).replace('|', '/')
        quality = item.get('candidate_quality', '')
        status = item.get('xmind_entry_status', '')
        hold_rows.append(f"| {item.get('testpoint_id', '')} | {title} | {target} | {quality} | {status} |")
    content = f"""# 测试设计说明

## 1. 测试范围

- 本次分析模式：{mode_label}
- 模式选择原因：{', '.join(mode_reason)}
- 模式评分：{coverage_matrix.get('mode_selection', {}).get('mode_score', 0)}
- 代码分析策略：{coverage_matrix.get('mode_selection', {}).get('code_analysis_strategy', 'unknown')}
- 需求事实数量：{stats.get('requirement_fact_count', 0)}
- 测试点数量：{stats.get('testpoint_count', 0)}

## 2. 高风险点

| 风险点 | 优先级 | 命中特征 | 覆盖测试点 |
|---|---|---|---|
{chr(10).join(risk_rows) if risk_rows else '| 暂无高风险点 | - | - | - |'}

## 3. 核心流程/状态

| 起始状态 | 目标状态 | 触发条件 | 覆盖测试点 |
|---|---|---|---|
{chr(10).join(state_rows) if state_rows else '| 暂无明确状态流转 | - | - | - |'}

## 4. 条件组合覆盖

当前阶段优先覆盖 P1 风险、关键状态边、严重/重要评审问题、异常分支、配置边界和数据一致性；复杂决策表将在标准模式增强阶段展开。

## 5. 覆盖检查结论

- P1 风险覆盖：{stats.get('p1_risk_covered', 0)}/{stats.get('p1_risk_count', 0)}
- 关键状态流转覆盖：{stats.get('critical_state_transition_covered', 0)}/{stats.get('critical_state_transition_count', 0)}
- 严重/重要评审问题处理：{stats.get('important_review_question_handled', 0)}/{stats.get('important_review_question_count', 0)}
- 质量门禁状态：{quality_gate_report.get('summary', {}).get('status', 'unknown')}

| 评审问题 | 等级 | 决策状态 | 处理/覆盖 |
|---|---|---|---|
{chr(10).join(question_rows) if question_rows else '| 暂无严重/重要评审问题 | - | - | - |'}

| 门禁 | 状态 | 说明 |
|---|---|---|
{chr(10).join(gate_rows)}

## 6. XMind候选入场

- 可评审后进入 XMind：{xmind_entry_summary.get('ready_after_review', 0)}
- 需评审补充后进入 XMind：{xmind_entry_summary.get('review_required', 0)}
- 暂缓进入 XMind：{xmind_entry_summary.get('hold_for_manual_refine', 0)}
- 候选质量分布：high={candidate_quality_summary.get('high', 0)}，medium={candidate_quality_summary.get('medium', 0)}，low={candidate_quality_summary.get('low', 0)}

| 测试点ID | 候选标题 | 覆盖目标 | 质量 | 入场状态 |
|---|---|---|---|---|
{chr(10).join(hold_rows) if hold_rows else '| 暂无暂缓进入 XMind 的低置信候选 | - | - | - | - |'}

附录：

- `.ai-internal/requirement_facts.json`
- `.ai-internal/test_design_model.json`
- `.ai-internal/coverage_matrix.json`
- `.ai-internal/consistency_report.json`
- `.ai-internal/consistency_fix_suggestions.json`
- `.ai-internal/xmind_candidate_filter_report.json`
- `.ai-internal/internal_json_compatibility_report.json`
- `.ai-internal/testpoints.json`
- `.ai-internal/quality_gate_report.json`
"""
    with open(design_path, 'w', encoding='utf-8') as f:
        f.write(content)
    return design_path



def _write_test_design_user_markdown(output_dir, test_design_model, coverage_matrix):
    """生成测试分析根目录下供用户直接查看的测试设计模型和覆盖矩阵。"""
    output_dir = Path(output_dir)
    model_path = output_dir / '测试设计模型.md'
    matrix_path = output_dir / '覆盖矩阵.md'
    mode = test_design_model.get('mode', {}) if isinstance(test_design_model, dict) else {}
    model_lines = [
        '# 测试设计模型', '', '## 测试模式', '',
        f"- 选择模式：{mode.get('mode_label', mode.get('selected_mode', '未说明'))}",
        f"- 选择原因：{'；'.join(mode.get('mode_reason', [])) or '未说明'}",
        f"- 模式评分：{mode.get('mode_score', '未说明')}",
        f"- 代码分析策略：{mode.get('code_analysis_strategy', '未说明')}", '',
        '## 风险模型', '', '| 风险ID | 风险标题 | 优先级 | 覆盖特征 |', '|---|---|---|---|',
    ]
    for item in test_design_model.get('risks', []):
        model_lines.append(f"| {item.get('risk_id', '')} | {item.get('title', '')} | {item.get('priority', '')} | {', '.join(item.get('trigger_features', []))} |")
    if not test_design_model.get('risks'):
        model_lines.append('| - | 暂无风险项 | - | - |')
    model_lines += ['', '## 状态流转模型', '', '| 状态ID | 起始状态 | 目标状态 | 触发条件 |', '|---|---|---|---|']
    for item in test_design_model.get('state_transitions', []):
        model_lines.append(f"| {item.get('state_id', '')} | {item.get('from_state', '')} | {item.get('to_state', '')} | {item.get('trigger', '')} |")
    if not test_design_model.get('state_transitions'):
        model_lines.append('| - | 暂无明确状态流转 | - | - |')
    model_lines += ['', '## 评审问题模型', '', '| 问题ID | 问题标题 | 严重度 | 决策状态 |', '|---|---|---|---|']
    for item in test_design_model.get('review_questions', []):
        model_lines.append(f"| {item.get('question_id', '')} | {item.get('title', '')} | {item.get('severity', '')} | {item.get('decision_status', 'pending')} |")
    if not test_design_model.get('review_questions'):
        model_lines.append('| - | 暂无评审问题 | - | - |')
    model_lines += ['', '## 结构化原始数据', '', '- `.ai-internal/test_design_model.json`', '- `.ai-internal/requirement_facts.json`', '- `.ai-internal/quality_gate_report.json', '']
    model_path.write_text('\n'.join(model_lines), encoding='utf-8')
    stats = coverage_matrix.get('statistics', {}) if isinstance(coverage_matrix, dict) else {}
    matrix_lines = ['# 覆盖矩阵', '', f"- 需求事实：{stats.get('requirement_fact_count', 0)} 条", f"- 测试点：{stats.get('testpoint_count', 0)} 个", f"- P1风险覆盖：{stats.get('p1_risk_covered', 0)}/{stats.get('p1_risk_count', 0)}", '', '## 覆盖映射', '', '| 覆盖ID | 类型 | 覆盖目标 | 优先级 | 状态 | 对应测试点 |', '|---|---|---|---|---|---|']
    for item in coverage_matrix.get('mappings', []):
        covered_by = ', '.join(item.get('covered_by', [])) or '未覆盖'
        matrix_lines.append(f"| {item.get('coverage_id', '')} | {item.get('coverage_type', '')} | {item.get('title', '')} | {item.get('priority', '')} | {item.get('status', '')} | {covered_by} |")
    if not coverage_matrix.get('mappings'):
        matrix_lines.append('| - | - | 暂无覆盖映射 | - | - | - |')
    matrix_lines += ['', '## 结构化原始数据', '', '- `.ai-internal/coverage_matrix.json`', '- `.ai-internal/testpoints.json`', '- `.ai-internal/consistency_report.json`', '']
    matrix_path.write_text('\n'.join(matrix_lines), encoding='utf-8')
    return model_path, matrix_path
def build_design_baseline_and_gate(req_dir, output_dir, project_info, doc_content, extracted_data, code_analysis, testpoints, quality_issues):
    requirement_facts_payload = build_requirement_facts(
        project_info, doc_content, code_analysis, extracted_data
    )
    requirement_facts = requirement_facts_payload.get('items', [])
    risks = _build_risk_items(doc_content, code_analysis)
    state_transitions = _extract_state_transitions(doc_content, code_analysis)
    review_questions = _extract_review_questions(output_dir, project_info, quality_issues)
    mode_details = _score_workflow_mode(doc_content, extracted_data, code_analysis, review_questions, state_transitions)
    mode = mode_details['selected_mode']
    mode_label = mode_details['mode_label']
    mode_reason = mode_details['mode_reason']
    testpoints = _attach_testpoint_metadata(testpoints, risks, state_transitions, review_questions)
    testpoints, repaired_risks = _auto_repair_p1_risk_gaps(testpoints, risks)
    testpoints, repaired_states = _auto_repair_state_gaps(testpoints, state_transitions)
    testpoints, repaired_questions = _auto_repair_review_question_gaps(testpoints, review_questions)
    testpoints = _attach_testpoint_metadata(testpoints, risks, state_transitions, review_questions)
    coverage_matrix = _build_coverage_matrix(requirement_facts, risks, state_transitions, review_questions, testpoints)
    coverage_matrix['mode_selection'] = mode_details
    gate_report = _run_minimal_quality_gates(coverage_matrix, testpoints)
    consistency_report, consistency_path = generate_consistency_report(output_dir, requirement_facts, coverage_matrix, gate_report)
    gate_report, consistency_report = _apply_deep_consistency_gate(gate_report, consistency_report, mode_details)
    consistency_path = _write_internal_json(output_dir, 'consistency_report.json', consistency_report)
    fix_suggestions = _build_consistency_fix_suggestions(coverage_matrix, gate_report, consistency_report, mode_details)
    testpoints, repaired_consistency = _auto_repair_from_fix_suggestions(testpoints, fix_suggestions)
    if repaired_consistency:
        testpoints = _attach_testpoint_metadata(testpoints, risks, state_transitions, review_questions)
        coverage_matrix = _build_coverage_matrix(requirement_facts, risks, state_transitions, review_questions, testpoints)
        coverage_matrix['mode_selection'] = mode_details
        gate_report = _run_minimal_quality_gates(coverage_matrix, testpoints)
        consistency_report, consistency_path = generate_consistency_report(output_dir, requirement_facts, coverage_matrix, gate_report)
        gate_report, consistency_report = _apply_deep_consistency_gate(gate_report, consistency_report, mode_details)
        consistency_path = _write_internal_json(output_dir, 'consistency_report.json', consistency_report)
        fix_suggestions = _build_consistency_fix_suggestions(coverage_matrix, gate_report, consistency_report, mode_details)
    applied_consistency_repairs = [
        {
            'suggestion_id': tp.get('repair_source_suggestion'),
            'testpoint_id': tp.get('testpoint_id'),
            'coverage_target': tp.get('coverage_target'),
            'title': tp.get('title'),
            'candidate_quality': tp.get('candidate_quality'),
            'xmind_entry_status': tp.get('xmind_entry_status'),
        }
        for tp in repaired_consistency
    ]
    fix_suggestions.setdefault('summary', {})['auto_applied_count'] = len(applied_consistency_repairs)
    fix_suggestions['auto_applied'] = applied_consistency_repairs
    candidate_quality_summary = {'high': 0, 'medium': 0, 'low': 0}
    xmind_entry_summary = {'ready_after_review': 0, 'review_required': 0, 'hold_for_manual_refine': 0}
    for item in applied_consistency_repairs:
        quality = item.get('candidate_quality') or 'low'
        status = item.get('xmind_entry_status') or 'hold_for_manual_refine'
        candidate_quality_summary[quality] = candidate_quality_summary.get(quality, 0) + 1
        xmind_entry_summary[status] = xmind_entry_summary.get(status, 0) + 1
    fix_suggestions['candidate_quality_summary'] = candidate_quality_summary
    fix_suggestions['xmind_entry_summary'] = xmind_entry_summary
    fix_suggestions.setdefault('summary', {})['xmind_ready_after_review_count'] = xmind_entry_summary.get('ready_after_review', 0)
    fix_suggestions.setdefault('summary', {})['xmind_review_required_count'] = xmind_entry_summary.get('review_required', 0)
    fix_suggestions.setdefault('summary', {})['xmind_hold_count'] = xmind_entry_summary.get('hold_for_manual_refine', 0)
    repaired_points = repaired_risks + repaired_states + repaired_questions + repaired_consistency
    test_design_model = {
        'mode': mode_details,
        'risks': risks,
        'state_transitions': state_transitions,
        'review_questions': review_questions,
        'quality_issues': quality_issues,
        'consistency_summary': consistency_report.get('summary', {}),
        'fix_suggestion_summary': fix_suggestions.get('summary', {}),
    }
    requirement_facts_path = _write_internal_json(
        output_dir, 'requirement_facts.json', requirement_facts_payload
    )
    design_model_path = _write_internal_json(output_dir, 'test_design_model.json', test_design_model)
    coverage_matrix_path = _write_internal_json(output_dir, 'coverage_matrix.json', coverage_matrix.copy())
    testpoints_path = _write_internal_json(output_dir, 'testpoints.json', {'test_points': testpoints})
    fix_suggestions_path = _write_internal_json(output_dir, 'consistency_fix_suggestions.json', fix_suggestions)
    gate_path = _write_internal_json(output_dir, 'quality_gate_report.json', {**gate_report, 'auto_repaired_count': len(repaired_points), 'auto_repaired_by_gate': {'G2': len(repaired_risks), 'G3': len(repaired_states), 'G4': len(repaired_questions), 'G7': len(repaired_consistency)}})
    design_summary_path = _write_test_design_summary(output_dir, mode_label, mode_reason, coverage_matrix, gate_report, fix_suggestions)
    test_design_model_md_path, coverage_matrix_md_path = _write_test_design_user_markdown(output_dir, test_design_model, coverage_matrix)
    save_workflow_state(req_dir, 'design', selected_mode=mode, mode_label=mode_label, mode_reason=mode_reason, mode_score=mode_details.get('mode_score'), score_items=mode_details.get('score_items'), code_analysis_strategy=mode_details.get('code_analysis_strategy'), quality_gate_status=gate_report.get('summary', {}).get('status'), pending_issues=gate_report.get('summary', {}).get('blocking_count', 0))
    compatibility_report, compatibility_path = generate_internal_json_compatibility_report(output_dir)
    log_console(f"  📄 测试设计说明: {design_summary_path}")
    log_console(f"  📄 需求事实: {requirement_facts_path}")
    log_console(f"  📄 测试设计模型: {design_model_path}")
    log_console(f"  📄 覆盖矩阵: {coverage_matrix_path}")
    log_console(f"  📄 一致性校验: {consistency_path}")
    log_console(f"  📄 一致性修复建议: {fix_suggestions_path}")
    log_console(f"  📄 内部JSON兼容性: {compatibility_path}")
    log_console(f"  📄 结构化测试点: {testpoints_path}")
    log_console(f"  📄 质量门禁报告: {gate_path}")
    if gate_report.get('summary', {}).get('status') == 'blocked':
        blocking = [g for g in gate_report.get('gates', []) if g.get('blocking') and g.get('status') == 'failed']
        details = '; '.join(f"{g['gate_id']} {g['message']}" for g in blocking)
        raise RuntimeError(f"质量门禁未通过，阻止生成 XMind: {details}")
    return testpoints, coverage_matrix, gate_report
def load_rules_config():
    """加载规则库配置"""
    rules_path = get_skill_file('xmind-generator', 'rules_config.json')
    with open(rules_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    local_path = rules_path.with_name('rules_config.local.json')
    if local_path.exists():
        # Resolve through the sibling boundary before reading optional local data.
        local_path = get_skill_file('xmind-generator', 'rules_config.local.json')
        with open(local_path, 'r', encoding='utf-8') as f:
            config.update(json.load(f))
    return config


def generate_test_points(doc_content, code_analysis, project_info):
    """根据需求、代码分析生成测试点"""
    td = make_node
    doc_lower = doc_content.lower()
    ta_children = []

    # StarRocks/数据迁移
    if any(k in doc_lower for k in ['starrocks', '迁移', '同步', '数据源']):
        ta_children.append(
            td("StarRocks数据迁移测试", markers=[{"markerId": "priority-1"}], children=[
                td("数据同步完整性测试", markers=[{"markerId": "priority-1"}], children=[
                    td("前置条件：中台ODS已完成数据拉取"),
                    td("1.验证ODS层数据与源表数据一致性", markers=[{"markerId": "task-done"}]),
                    td("2.验证DWD层清洗结果正确", markers=[{"markerId": "task-done"}]),
                    td("预期结果：数据总量一致，无缺失"),
                ]),
                td("数据流转时序测试", markers=[{"markerId": "priority-1"}], children=[
                    td("前置条件：定时任务按预期执行"),
                    td("1.验证ODS拉取完成时间", markers=[{"markerId": "task-done"}]),
                    td("2.验证中台清洗完成时间", markers=[{"markerId": "task-done"}]),
                    td("预期结果：各阶段按时完成"),
                ]),
            ])
        )

    # 分表/Sharding
    if any(k in doc_lower for k in ['分表', 'sharding', '分片']):
        ta_children.append(
            td("分表功能测试", markers=[{"markerId": "priority-1"}], children=[
                td("分表路由测试", markers=[{"markerId": "priority-1"}], children=[
                    td("前置条件：分表已按日期创建"),
                    td("1.验证数据路由到正确分表", markers=[{"markerId": "task-done"}]),
                    td("2.验证范围查询限制", markers=[{"markerId": "task-done"}]),
                    td("预期结果：路由正确，限制生效"),
                ]),
            ])
        )

    # 定时任务
    if code_analysis and code_analysis.get('scheduled_tasks'):
        job_tests = []
        for job in code_analysis.get('scheduled_tasks', []):
            job_name = job.get('name', '')
            job_tests.append(
                td(f"{job_name}执行测试", markers=[{"markerId": "priority-1"}], children=[
                    td("前置条件：定时任务已配置并启用"),
                    td("1.验证定时任务按预期时间执行", markers=[{"markerId": "task-done"}]),
                    td("2.验证任务执行成功后数据正确更新", markers=[{"markerId": "task-done"}]),
                    td("预期结果：任务执行成功，数据更新正确"),
                ])
            )
        if job_tests:
            ta_children.append(td("定时任务测试", markers=[{"markerId": "priority-1"}], children=job_tests))

    # 报表/统计
    if any(k in doc_lower for k in ['报表', '损益', '统计']):
        ta_children.append(
            td("报表功能测试", markers=[{"markerId": "priority-1"}], children=[
                td("报表数据准确性测试", markers=[{"markerId": "priority-1"}], children=[
                    td("前置条件：数据已同步"),
                    td("1.验证统计字段计算正确", markers=[{"markerId": "task-done"}]),
                    td("2.验证损益计算正确", markers=[{"markerId": "task-done"}]),
                    td("预期结果：报表数据准确"),
                ]),
            ])
        )

    # 异常和边界测试（通用）
    ta_children.append(
        td("异常和边界测试", markers=[{"markerId": "priority-1"}], children=[
            td("数据异常测试", markers=[{"markerId": "priority-1"}], children=[
                td("前置条件：存在异常数据"),
                td("1.验证空数据处理正确", markers=[{"markerId": "task-done"}]),
                td("2.验证数据格式异常处理正确", markers=[{"markerId": "task-done"}]),
                td("预期结果：异常正确处理，无崩溃"),
            ]),
            td("边界值测试", markers=[{"markerId": "priority-2"}], children=[
                td("前置条件：准备边界数据"),
                td("1.验证边界条件处理正确", markers=[{"markerId": "task-done"}]),
                td("预期结果：边界值正确处理"),
            ]),
        ])
    )

    return ta_children



def _filter_xmind_candidate_testpoints(output_dir, testpoints):
    """低置信候选只保留在 JSON 待办中，不进入评审 XMind。"""
    included = []
    excluded = []
    for tp in testpoints:
        status = tp.get('xmind_entry_status', 'ready')
        should_exclude = status == 'hold_for_manual_refine'
        tp['xmind_included'] = not should_exclude
        if should_exclude:
            tp['xmind_exclude_reason'] = tp.get('xmind_entry_reason') or '候选点需人工完善后再进入 XMind'
            excluded.append({
                'testpoint_id': tp.get('testpoint_id'),
                'title': tp.get('title'),
                'coverage_target': tp.get('coverage_target'),
                'candidate_quality': tp.get('candidate_quality', tp.get('confidence', '')),
                'xmind_entry_status': status,
                'reason': tp.get('xmind_exclude_reason'),
            })
        else:
            tp.setdefault('xmind_exclude_reason', '')
            included.append(tp)
    report = {
        'summary': {
            'total_testpoints': len(testpoints),
            'included_count': len(included),
            'excluded_count': len(excluded),
            'hold_for_manual_refine_count': len(excluded),
        },
        'excluded': excluded,
    }
    report_path = _write_internal_json(output_dir, 'xmind_candidate_filter_report.json', report)
    if excluded:
        log_console(f"  [过滤] 低置信候选未进入 XMind: {len(excluded)} 个，详见 {report_path}")
    return included, report_path

def generate_xmind_review(req_dir, output_dir, project_info, test_points, kb_manager=None):
    """生成评审版XMind"""
    td = make_node

    # 项目名称（优先从 project_info 获取，为空时从目录路径提取）
    project_name = project_info.get('project_name', '')
    if not project_name:
        # 从需求目录自身名称提取，保留【】符号
        project_name = Path(req_dir).name

    # 日期层级：从需求目录路径提取（匹配目录名格式：20260604）
    date_str = None
    # 从 req_dir 路径提取日期（如 D:\...\20260604\项目名）
    date_match = re.search(r'[\\/]([2]\d{7})[\\/]', str(req_dir))
    if date_match:
        date_str = date_match.group(1)

    if not date_str:
        # 兜底：从 output_dir 路径提取
        date_match = re.search(r'([2]\d{7})', str(output_dir))
        date_str = date_match.group(1) if date_match else datetime.now().strftime('%Y%m%d')

    year = date_str[:4]
    month = date_str[:6]
    day = date_str

    # KYM节点优先使用已评审精炼元数据，避免把 Markdown 表格片段当作核心变更。
    review_metadata = {}
    curated_path = _get_internal_dir(Path(output_dir)) / 'curated_testpoints.json'
    if curated_path.exists():
        try:
            review_metadata = json.loads(
                curated_path.read_text(encoding='utf-8')
            ).get('review_metadata', {})
        except Exception:
            review_metadata = {}
    solution = review_metadata.get('core_change') or project_info.get('solution', '')
    if not solution:
        solution = "未从需求和评审结论中提取到明确核心变更"
    table_names = review_metadata.get('tables') or '、'.join(project_info.get('tables', []))
    interface_names = review_metadata.get('interfaces') or '、'.join(project_info.get('interfaces', []))

    kym_node = td("KYM", children=[
        td(f"项目名称：{project_name}"),
        td(f"核心变更：{solution}"),
        td(f"涉及表：{table_names or '未识别'}"),
        td(f"涉及接口：{interface_names or '未识别'}"),
        td(f"定时任务：{len(project_info.get('jobs', []))} 个"),
        td(f"风险评估：{project_info.get('risk_level', '中')}风险"),
    ])

    # 转换测试点为新格式 → XMind格式
    ta_children = _convert_testpoints_to_xmind(test_points)

    # 测试分析节点
    ta_node = td("测试分析", children=ta_children)

    # 待确认问题节点
    pending_issues = list(project_info.get('pending_issues', []))
    review_data_files = sorted(
        _get_internal_dir(Path(output_dir)).glob('*评审数据.json'),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for review_data_path in review_data_files[:1]:
        try:
            review_payload = json.loads(review_data_path.read_text(encoding='utf-8'))
        except Exception:
            review_payload = {}
        structured_pending = [
            f"{item.get('id', '')}：{item.get('title', '')}"
            for category in review_payload.get('categories', [])
            for item in category.get('items', [])
            if str(item.get('status', '')).strip() == '待确认'
        ]
        if structured_pending:
            pending_issues = structured_pending
            break
    if pending_issues:
        pending_children = [td(f"问题{i+1}：{issue}") for i, issue in enumerate(pending_issues[:5])]
    else:
        pending_children = [
            td("无待确认问题"),
        ]
    pending_node = td("待确认问题", children=pending_children)

    # 评审标记约定节点
    marker_node = td("📖 评审标记约定（请阅读）", children=[
        td("🟢 flag-green：评审新增的测试点 → 执行版保留"),
        td("🔴 flag-red：评审删除的测试点 → 执行版过滤"),
        td("✏️ 标题变更：修改测试点 → AI自动识别"),
        td("📄 无标记：未变测试点 → 直接纳入"),
    ])

    # 公司品牌节点（从配置读取）
    company = str(project_info.get('company') or '测试组织').strip()
    jdzx_node = td(company, markers=[{"markerId": "star-red"}], children=[
        kym_node, ta_node, pending_node, marker_node
    ])

    # 项目节点
    project_node = td(project_name, children=[jdzx_node])

    # 日期层级
    day_node = td(day, children=[project_node])
    month_node = td(month, children=[day_node])
    year_node = td(year, children=[month_node])

    # 根节点
    root_topic = year_node

    # 生成XMind文件（文件名日期用系统当前日期，内部层级用需求目录日期）
    file_date = datetime.now().strftime('%Y%m%d')
    manager = str(project_info.get('manager') or '测试负责人').strip()
    review_version = str(project_info.get('review_version') or 'v1').strip()
    review_path = output_dir / f'{company}_{manager}_评审_{file_date}_{review_version}.xmind'
    create_xmind_file(str(review_path), root_topic, project_name)
    log_console(f"  📄 评审版 XMind: {review_path}")
    log_console('  ⏸️ 终稿 XMind 将在人工评审确认后生成')
    return review_path, None

def _convert_testpoints_to_xmind(test_points):
    """
    将新格式测试点转换为XMind节点结构

    输入: list of dicts with title, category, type, steps, expected
    输出: XMind node list
    """
    td = make_node

    # 按类别分组
    by_category = {}
    for tp in test_points:
        cat = tp.get('category', '其他测试')
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(tp)

    ta_children = []
    for cat_name, cat_points in by_category.items():
        by_type = {}
        for tp in cat_points:
            by_type.setdefault(tp.get('type', '功能'), []).append(tp)

        type_nodes = []
        for point_type, typed_points in by_type.items():
            point_nodes = []
            for tp in typed_points:
                marker = {
                    "markerId": "priority-1" if tp.get('priority') == 'P1' else "priority-2"
                }
                # 评审版节点只展示可快速扫描的需求验证点。
                # 数据、执行提示和详细预期放入笔记，避免标题承载用例细节。
                tp_node = td(_compose_review_testpoint_title(tp), markers=[marker])
                note = _compose_review_note(tp)
                if note:
                    tp_node['notes'] = {
                        'plain': {
                            'content': note,
                        }
                    }
                point_nodes.append(tp_node)
            type_nodes.append(
                td(f"{point_type}场景（{len(point_nodes)}个测试点）", children=point_nodes)
            )

        if type_nodes:
            ta_children.append(
                td(f"{cat_name}（{len(cat_points)}个测试点）", children=type_nodes)
            )

    return ta_children


def generate_testpoint_json(output_dir, project_info, test_points):
    """生成测试点JSON"""
    internal_dir = _get_internal_dir(output_dir)
    doc_name = project_info.get('project_name', '')
    if not doc_name:
        parent_name = output_dir.parent.name
        doc_name = parent_name.replace('【', '').replace('】', '')
        if not doc_name:
            doc_name = '需求'
    doc_name = _safe_artifact_name(doc_name)
    json_path = internal_dir / f'{doc_name}_测试点.json'
    for stale_path in internal_dir.glob('*_测试点.json'):
        if stale_path != json_path:
            stale_path.unlink()

    # 统计
    by_type = {}
    for tp in test_points:
        tp_type = tp.get('type', '功能')
        by_type[tp_type] = by_type.get(tp_type, 0) + 1

    data = {
        "schema_version": "1.0.0",
        "workflow_version": "testcase-generator-2026.07",
        "generated_at": _workflow_timestamp(),
        "metadata": {
            "title": f"{doc_name}_测试点",
            "version": "v1.0-ai",
            "source_requirement": doc_name,
            "knowledge_base_used": True,
            "essence_library_applied": True
        },
        "statistics": {
            "total_testpoints": len(test_points),
            "by_source": {"requirement": 0, "review": 0, "tech": 0, "knowledge_base": len(test_points)},
            "by_type": by_type
        },
        "test_points": [
            {
                "testpoint_id": tp.get('testpoint_id', _stable_id('TP', tp.get('title', ''), idx)),
                "title": tp.get('title', ''),
                "review_text": _compose_review_testpoint_text(tp),
                "category": tp.get('category', ''),
                "type": tp.get('type', ''),
                "priority": tp.get('priority', 'P2'),
                "steps": tp.get('steps', ''),
                "expected": tp.get('expected', ''),
                "coverage_ids": tp.get('coverage_ids', []),
                "source_type": tp.get('source_type', 'AI生成'),
                "auto_repaired": tp.get('auto_repaired', False),
                "repair_source_gate": tp.get('repair_source_gate', ''),
                "coverage_target": tp.get('coverage_target', ''),
                "source_matrix_item": tp.get('source_matrix_item', ''),
                "requires_human_confirm": tp.get('requires_human_confirm', False),
                "confidence": tp.get('confidence', ''),
                "candidate_quality": tp.get('candidate_quality', tp.get('confidence', '')),
                "xmind_entry_status": tp.get('xmind_entry_status', 'ready'),
                "xmind_entry_reason": tp.get('xmind_entry_reason', ''),
                "xmind_included": tp.get('xmind_included', True),
                "xmind_exclude_reason": tp.get('xmind_exclude_reason', '')
            }
            for idx, tp in enumerate(test_points)
        ]
    }

    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    log_console(f"  📄 测试点 JSON: {json_path}")
    return json_path
def _review_condition_from_steps(point):
    first_line = str(point.get('steps', '')).splitlines()[0].strip()
    first_line = re.sub(r'^\d+[.、]\s*', '', first_line)
    first_line = re.sub(r'^(分别)?(?:准备|设置|配置)\s*', '', first_line)
    first_line = re.sub(r'^将\s*', '', first_line)
    first_line = re.sub(r'^为(.+?)准备', r'\1存在', first_line)
    return first_line.rstrip('。；;，, ')


def _compose_review_testpoint_title(point):
    """Return a concise review title that states the requirement rule to verify."""
    title = str(point.get('title', '')).strip().rstrip('。；;，, ')
    pending = '【待确认】' if point.get('requires_human_confirm') else ''
    return f'{pending}{title}'


def _compose_review_testpoint_text(point):
    """Compatibility field for JSON consumers; keep it as the concise review title."""
    text = _compose_review_testpoint_title(point)
    point['review_text'] = text
    return text


def _compose_review_note(point):
    """Put execution-oriented information in the XMind note, not the topic title."""
    coverage_ids = [
        str(item).strip()
        for item in point.get('coverage_ids', [])
        if str(item).strip() and str(item).strip() != 'COVERAGE-GENERAL'
    ]
    note_lines = []
    if coverage_ids:
        note_lines.append(f"覆盖依据：{'、'.join(coverage_ids)}")

    steps = [
        re.sub(r'^\d+[.、]\s*', '', line.strip())
        for line in str(point.get('steps', '')).splitlines()
        if line.strip()
    ]
    if steps:
        note_lines.append(f'测试数据：{steps[0]}')
    if len(steps) > 1:
        note_lines.append(f"执行提示：{'；'.join(steps[1:])}")

    expected = str(point.get('expected', '')).strip()
    if expected:
        note_lines.append(f'预期结果：{expected}')
    return '\n'.join(note_lines)


def _write_detailed_testpoints_md(output_dir, testpoints, project_info=None):
    """生成轻量评审 Markdown；标题写验证规则，执行信息写入笔记。"""
    project_info = project_info or {}
    lines = ['# 测试点评审清单', '', f'> 测试点总数：{len(testpoints)}', '']
    review_metadata = {}
    curated_path = _get_internal_dir(Path(output_dir)) / 'curated_testpoints.json'
    if curated_path.exists():
        try:
            review_metadata = json.loads(
                curated_path.read_text(encoding='utf-8')
            ).get('review_metadata', {})
        except Exception:
            review_metadata = {}
    core_change = review_metadata.get('core_change') or _compact_text(
        project_info.get('solution', ''), 180
    )
    impact_business = review_metadata.get('impact_business') or '、'.join(
        dict.fromkeys(point.get('category', '其他测试') for point in testpoints)
    )
    lines.extend([
        '## KYM',
        '',
        f"- 项目名称：{project_info.get('project_name', '')}",
        f"- 核心变更：{core_change}",
        f"- 涉及表/存储：{review_metadata.get('tables', '')}",
        f"- 涉及服务：{review_metadata.get('interfaces', '')}",
        f"- 影响业务：{impact_business}",
        '',
    ])
    review_data_files = sorted(
        _get_internal_dir(Path(output_dir)).glob('*评审数据.json'),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    pending_items = []
    for review_data_path in review_data_files[:1]:
        try:
            review_payload = json.loads(review_data_path.read_text(encoding='utf-8'))
        except Exception:
            review_payload = {}
        pending_items = [
            f"{item.get('id', '')}：{item.get('title', '')}"
            for category in review_payload.get('categories', [])
            for item in category.get('items', [])
            if str(item.get('status', '')).strip() == '待确认'
        ]
    lines.extend(['## 待确认问题', ''])
    lines.extend([f'- {item}' for item in pending_items] or ['- 无'])
    lines.append('')

    numerals = '一二三四五六七八九十'
    grouped = {}
    for point in testpoints:
        grouped.setdefault(point.get('category', '其他测试'), []).append(point)
    for section_index, (category, points) in enumerate(grouped.items(), 1):
        numeral = numerals[section_index - 1] if section_index <= len(numerals) else str(section_index)
        lines.extend([f'## {numeral}、{category}', ''])
        by_type = {}
        for point in points:
            by_type.setdefault(point.get('type', '功能'), []).append(point)
        for type_index, (point_type, typed_points) in enumerate(by_type.items(), 1):
            lines.extend([f'### {section_index}.{type_index} {point_type}场景', ''])
            for point_index, point in enumerate(typed_points, 1):
                priority = point.get('priority', 'P2')
                review_title = _compose_review_testpoint_title(point)
                lines.append(
                    f'- {section_index}.{type_index}.{point_index} {review_title} [{priority}]'
                )
                note = _compose_review_note(point)
                if note:
                    note_lines = note.splitlines()
                    lines.append(f'  - 笔记：{note_lines[0]}')
                    lines.extend(f'    {line}' for line in note_lines[1:])
            lines.append('')
    review_texts = [_compose_review_testpoint_title(point) for point in testpoints]
    forbidden_phrases = (
        '验证正常', '正确处理', '按约定', '支持时', '不支持时', '待建立',
    )
    vague_points = [
        text for text in review_texts
        if any(phrase in text for phrase in forbidden_phrases)
    ]
    if vague_points:
        raise RuntimeError(
            '测试点评审文本仍有模糊预期：' + '；'.join(vague_points[:3])
        )
    if len(review_texts) != len(set(review_texts)):
        raise RuntimeError('测试点评审文本存在重复项，禁止生成 XMind')
    opaque_points = []
    for text in review_texts:
        if any(term in text for term in ('主表', '比例公式', '认缴公式', '兜底')):
            opaque_points.append(text)
            continue
        if '求和' in text and not re.search(r'[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*', text):
            opaque_points.append(text)
    if opaque_points:
        raise RuntimeError(
            '测试点标题仍含未展开术语，请写明表字段或计算关系：'
            + '；'.join(opaque_points[:3])
        )
    content = '\n'.join(lines)
    internal_path = _get_internal_dir(Path(output_dir)) / 'xmind_source.md'
    user_path = Path(output_dir) / '详细测试点.md'
    internal_path.write_text(content, encoding='utf-8')
    user_path.write_text(content, encoding='utf-8')
    return user_path


def _write_testpoint_generation_report(output_dir, testpoints, gate_report):
    """Write both machine-readable diagnostics and a concise user-visible report."""
    summary = gate_report.get('summary', {}) if isinstance(gate_report, dict) else {}
    payload = {
        'artifact_kind': '测试点评审',
        'review_structure': '标题展示需求验证点；测试数据、执行提示和详细预期写入笔记',
        'contains_testcase_steps': False,
        'testpoint_count': len(testpoints),
        'quality_gate_status': summary.get('status', 'unknown'),
        'blocking_count': summary.get('blocking_count', 0),
        'generated_at': _workflow_timestamp(),
    }
    _write_internal_json(output_dir, 'testpoint_generation_report.json', dict(payload))
    user_path = Path(output_dir) / '测试点生成报告.md'
    user_path.write_text(
        '\n'.join([
            '# 测试点生成报告',
            '',
            f"- 产物类型：{payload['artifact_kind']}（不是测试用例）",
            f"- 描述结构：{payload['review_structure']}",
            '- 执行步骤：不在评审版展示，评审通过后再细化',
            f"- 测试点数量：{payload['testpoint_count']}",
            f"- 质量门禁：{payload['quality_gate_status']}",
            f"- 阻断项：{payload['blocking_count']}",
            f"- 生成时间：{payload['generated_at']}",
            '',
        ]),
        encoding='utf-8',
    )
    return user_path

def _require_testpoint_inputs(output_dir, code_analysis):
    """测试设计阶段只能建立在已完成的分析和评审产物之上。"""
    output_dir = Path(output_dir)
    internal_dir = _get_internal_dir(output_dir)
    required = [
        output_dir / '需求分析.md',
        internal_dir / 'requirement_facts.json',
        internal_dir / 'analysis_quality_gate.json',
        internal_dir / 'requirement_code_comparison.json',
        internal_dir / 'code_impact.json',
        internal_dir / 'code_targets.json',
        internal_dir / 'analysis_context.json',
    ]
    if code_analysis:
        required.append(output_dir / '代码评审报告.md')
    missing = [str(path) for path in required if not path.exists()]
    impact_payload, _ = _load_internal_json_payload(output_dir, 'code_impact.json')
    analysis_gate, _ = _load_internal_json_payload(output_dir, 'analysis_quality_gate.json')
    if analysis_gate.get('summary', {}).get('status') != 'passed':
        missing.append('测试分析/.ai-internal/analysis_quality_gate.json 未通过')
    if code_analysis:
        if not isinstance(impact_payload, dict) or not impact_payload.get('selected_files') or not impact_payload.get('selection_evidence'):
            missing.append('测试分析/.ai-internal/code_impact.json 中缺少已确认影响文件或选择证据')
        elif impact_payload.get('status') not in {'completed', 'completed_stale'}:
            missing.append(f"代码影响分析未完成或最新性未验证：{impact_payload.get('status')}；{impact_payload.get('skip_reason', '')}")
    else:
        comparison_payload, _ = _load_internal_json_payload(output_dir, 'requirement_code_comparison.json')
        if impact_payload.get('status') != 'skipped' or comparison_payload.get('status') != 'skipped':
            missing.append('无代码分析时必须明确跳过需求代码比较；代码目标阻断时不能按纯需求流程放行')
    review_reports = list(output_dir.glob('*评审报告.md'))
    review_data = list(internal_dir.glob('*评审数据.json'))
    if not review_reports:
        missing.append('测试分析/*评审报告.md')
    if not review_data:
        missing.append('测试分析/.ai-internal/*评审数据.json')
    if missing:
        raise RuntimeError('测试点阶段前置门禁未通过：' + '；'.join(missing))

def _validate_xmind_testpoint_count(output_dir: Path, xmind_path: Path, expected_count: int) -> Dict[str, Any]:
    """Verify count and ensure the review XMind contains no testcase-detail nodes."""
    actual_count, detail_node_count, error = -1, -1, ''
    try:
        with zipfile.ZipFile(xmind_path, 'r') as zf:
            sheets = json.loads(zf.read('content.json').decode('utf-8'))
        def find_topic(node, title):
            if not isinstance(node, dict):
                return None
            if node.get('title') == title:
                return node
            for child in node.get('children', {}).get('attached', []):
                found = find_topic(child, title)
                if found:
                    return found
            return None
        analysis_node = find_topic(sheets[0].get('rootTopic', {}), '测试分析')
        if analysis_node:
            # Variable and scenario grouping can add intermediate levels. Count only
            # priority-marked review points, never structural grouping topics.
            actual_count = 0
            def count_priority_points(node):
                nonlocal actual_count
                marker_ids = {
                    marker.get('markerId', '')
                    for marker in node.get('markers', [])
                    if isinstance(marker, dict)
                }
                if marker_ids.intersection({'priority-1', 'priority-2'}):
                    actual_count += 1
                for child in node.get('children', {}).get('attached', []):
                    count_priority_points(child)
            count_priority_points(analysis_node)
        else:
            # xmind-generator/review_xmind.py structure:
            # test section -> numbered scenario category -> review-point leaves.
            actual_count = 0
            def count_review_points(node):
                nonlocal actual_count
                title = str(node.get('title', ''))
                children = node.get('children', {}).get('attached', [])
                if re.match(r'^\d+\.\d+\s+', title):
                    actual_count += len(children)
                    return
                for child in children:
                    count_review_points(child)
            count_review_points(sheets[0].get('rootTopic', {}))

        detail_node_count = 0
        def count_detail_nodes(node):
            nonlocal detail_node_count
            title = str(node.get('title', '')).strip()
            if re.match(r'^(前置条件|操作步骤|预期结果)[：:]', title):
                detail_node_count += 1
            for child in node.get('children', {}).get('attached', []):
                count_detail_nodes(child)
        count_detail_nodes(sheets[0].get('rootTopic', {}))
    except Exception as exc:
        error = str(exc)
    report = {
        'expected_count': expected_count,
        'actual_count': actual_count,
        'testcase_detail_node_count': detail_node_count,
        'review_structure': 'testpoint_only' if detail_node_count == 0 else 'contains_testcase_details',
        'xmind_path': str(xmind_path),
        'error': error,
    }
    report['status'] = (
        'passed'
        if not error and actual_count == expected_count and actual_count > 0
        and detail_node_count == 0
        else 'failed'
    )
    _write_internal_json(output_dir, 'xmind_consistency_gate.json', report)
    if report['status'] == 'failed':
        failed_dir = _get_internal_dir(output_dir) / 'runs' / datetime.now().strftime('%Y%m%d%H%M%S') / 'failed'
        failed_dir.mkdir(parents=True, exist_ok=True)
        if xmind_path.exists():
            shutil.move(str(xmind_path), str(failed_dir / xmind_path.name))
        raise RuntimeError(
            f"XMind 测试点评审结构门禁未通过：期望 {expected_count}，"
            f"实际 {actual_count}，用例明细节点 {detail_node_count}；{error}"
        )
    return report

def run_testpoint_generate(req_dir, output_dir, project_info, code_analysis, kb_manager=None):
    """
    步骤④：测试点生成（质量保障版）

    输入：需求文档 + 评审报告 + 技术方案 + 华库
    输出：测试点评审 Markdown + 评审版 XMind + 测试点 JSON

    质量保障机制：
    1. 提取提测文档中的具体数据（表名、字段、配置、状态码、图片引用）
    2. AI基于需求理解推导测试点框架
    3. 将具体数据填充到测试点
    4. 评审版仅输出条件、测试内容和明确预期
    5. 执行步骤留到评审通过后的测试用例细化阶段
    """
    log_console("\n" + "=" * 60)
    log_console("🧪 步骤④ 测试点生成（质量保障版）")
    log_console("=" * 60)

    _require_testpoint_inputs(output_dir, code_analysis)
    project_info = dict(project_info or {})
    cached_project_name = str(project_info.get('project_name', '')).strip()
    project_info.setdefault('document_title', cached_project_name)
    project_info['project_name'] = Path(req_dir).name.strip()
    project_info['project_name_source'] = 'requirement_package_directory'

    # 1. 读取华库
    essence_omissions = []
    if kb_manager:
        essence_dir = kb_manager.kb_path / 'patterns' / '精华库'
        for file_name in ['common_omissions.md', 'improvement_patterns.md']:
            file_path = essence_dir / file_name
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    essence_omissions.append(f.read())
                log_console(f"  📚 华库学习: {file_name}")

    # 2. 仅读取已裁剪的当前需求版本，禁止历史版本串入测试点。
    doc_content, source_meta = _read_normalized_requirement(output_dir)
    log_console(f"  📄 读取当前需求标准输入: {source_meta['character_count']} 字符")    # 3. 提取测试原材料
    extracted_data = _extract_test_data(doc_content)
    log_console(f"  ✅ 提取数据: 表{len(extracted_data['tables'])}个, 字段{len(extracted_data['fields'])}个, 配置{len(extracted_data['configs'])}个, 状态码{len(extracted_data['status_codes'])}个, 图片引用{len(extracted_data['images'])}个")

    # 4. AI推导 + 数据填充 + 质量保障
    testpoints, quality_issues = _generate_quality_testpoints(doc_content, extracted_data, essence_omissions, project_info)
    curated_testpoints, curated_path = _load_curated_testpoints(output_dir)
    if curated_testpoints:
        testpoints = curated_testpoints
        quality_issues = []
        log_console(f"  ✅ 使用已评审精炼测试点: {len(testpoints)} 个（{curated_path}）")
    else:
        log_console(f"  ✅ AI推导测试点: {len(testpoints)} 个")

    if quality_issues:
        log_console(f"  ⚠️ 质量检查发现 {len(quality_issues)} 个问题:")
        for qi in quality_issues[:3]:
            log_console(f"     - {qi['title']}: {', '.join(qi['issues'])}")

    # 5. 生成测试设计基线 + 覆盖矩阵 + 基础质量门禁，门禁通过后才生成 XMind
    testpoints, coverage_matrix, gate_report = build_design_baseline_and_gate(
        req_dir, output_dir, project_info, doc_content, extracted_data, code_analysis, testpoints, quality_issues
    )
    _validate_testpoint_source_consistency(
        Path(output_dir), project_info, source_meta, testpoints
    )
    _write_internal_json(output_dir, 'testpoints.json', {'test_points': testpoints})

    # 6. 质量门禁通过后生成测试点评审 Markdown，不展开测试用例步骤。
    detailed_path = _write_detailed_testpoints_md(output_dir, testpoints, project_info)
    _write_internal_json(output_dir, 'testpoints.json', {'test_points': testpoints})
    report_path = _write_testpoint_generation_report(output_dir, testpoints, gate_report)
    log_console(f"  📄 详细测试点: {detailed_path}")
    log_console(f"  📄 测试点生成报告: {report_path}")

    # 7. 低置信候选保留在 JSON 待办中，不进入评审 XMind
    xmind_testpoints, xmind_filter_path = _filter_xmind_candidate_testpoints(output_dir, testpoints)

    # 8. 转换为XMind格式并生成
    review_path, final_path = generate_xmind_review(req_dir, output_dir, project_info, xmind_testpoints, kb_manager)
    _validate_xmind_testpoint_count(output_dir, review_path, len(xmind_testpoints))

    # 9. 生成 JSON（保留完整测试点和 XMind 过滤状态）
    json_path = generate_testpoint_json(output_dir, project_info, testpoints)

    required_user_files = [
        '需求分析.md', review_path.name, '详细测试点.md', '测试点生成报告.md',
        '测试设计模型.md', '覆盖矩阵.md',
        '.ai-internal/normalized_requirement.md', '.ai-internal/xmind_source.md',
        '.ai-internal/source_consistency_gate.json', '.ai-internal/xmind_consistency_gate.json',
        '.ai-internal/requirement_facts.json', '.ai-internal/test_design_model.json',
        '.ai-internal/coverage_matrix.json', '.ai-internal/testpoints.json',
        '.ai-internal/quality_gate_report.json',
    ]
    if code_analysis:
        required_user_files.append('代码评审报告.md')
    _validate_output_layout(output_dir, 'testpoint', required_files=tuple(required_user_files))

    return review_path, final_path, json_path

__all__ = [
    name for name, value in globals().items()
    if callable(value) and getattr(value, "__module__", None) == __name__
]
