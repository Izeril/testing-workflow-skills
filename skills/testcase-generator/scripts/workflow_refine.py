#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reviewed-XMind backflow, finalization, testcase refinement, and executable XMind."""

import json
import re
import shutil
import subprocess
import sys
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

from decision_register import finalization_blocking_issues
from cross_skill_adapter import get_skill_file

from workflow_common import (
    _get_internal_dir,
    _load_internal_json_payload,
    _workflow_timestamp,
        _write_internal_json,
    gid,
    log_console,
)
from workflow_testpoint import (
    _match_testpoint_to_review_question,
    _match_testpoint_to_risk,
    _match_testpoint_to_state,
    generate_internal_json_compatibility_report,
)
def assess_priority(title: str) -> str:
    """优先级评定"""
    P1_KEYWORDS = [
        '异常', '错误', '超时', '失败', '非法', '容错', '恢复',
        '核心', '主流程', '关键', '检索', '查询', '计算', '校验',
        '完整', '准确', '一致性', '丢失', '泄漏', '映射', '转换',
        '安全', '权限', '认证', '加密', '合规', '敏感',
        '崩溃', '阻塞', '死锁', '连接', '中断',
        '接口', '集成', '交互', '上下游', '依赖'
    ]

    for kw in P1_KEYWORDS:
        if kw in title:
            return 'P1'
    return 'P2'
def _topic_marker_ids(topic):
    return [m.get('markerId', '') for m in topic.get('markers', [])]


def _topic_children(topic):
    return topic.get('children', {}).get('attached', [])


def _is_review_scope_path(path):
    if not path:
        return False
    scope_keywords = ['功能测试', '异常测试', '边界测试', '接口测试', '数据验证', '配置测试', '性能测试', '集成测试', '安全测试']
    return any(any(keyword in str(part) for keyword in scope_keywords) for part in path)


def _is_xmind_testpoint_topic(topic, path=None):
    title = topic.get('title', '')
    children = _topic_children(topic)
    has_steps = any('操作步骤' in c.get('title', '') for c in children)
    has_expected = any('预期结果' in c.get('title', '') for c in children)
    if has_steps and has_expected:
        return True
    if children:
        return False
    if not title or title in ['无', '?']:
        return False
    if any(skip in title for skip in ['KYM', '待确认问题', '评审标记约定', '参考资料', '涉及信息汇总', '是否涉及性能测试']):
        return False
    if any(label in title for label in ['前置条件', '操作步骤', '预期结果']):
        return False
    return _is_review_scope_path(path or [])


def _collect_reviewed_xmind_nodes(topic, nodes=None, path=None):
    if nodes is None:
        nodes = []
    if path is None:
        path = []
    title = topic.get('title', '')
    current_path = path + [title]
    if _is_xmind_testpoint_topic(topic, current_path):
        child_text = ' '.join(c.get('title', '') for c in _topic_children(topic))
        marker_ids = _topic_marker_ids(topic)
        nodes.append({
            'title': title,
            'normalized_title': _normalize_match_text(title),
            'path': current_path,
            'marker_ids': marker_ids,
            'is_deleted': any('flag-red' in marker for marker in marker_ids),
            'is_added': any('flag-green' in marker for marker in marker_ids),
            'text': f"{title} {child_text}",
        })
    for child in _topic_children(topic):
        _collect_reviewed_xmind_nodes(child, nodes, current_path)
    return nodes


def _load_reviewed_xmind_nodes(xmind_path):
    import zipfile
    try:
        with zipfile.ZipFile(xmind_path, 'r') as zf:
            content_json = json.loads(zf.read('content.json'))
            root_topic = content_json[0]['rootTopic']
            return _collect_reviewed_xmind_nodes(root_topic)
    except Exception as e:
        raise RuntimeError(f"读取评审终稿 XMind 失败: {e}")


def _normalize_match_text(text):
    text = re.sub(r'\s+', '', str(text or '').lower())
    text = re.sub(r'[【\[].*?[】\]]', '', text)
    text = re.sub(r'[^0-9a-z\u4e00-\u9fa5]+', '', text)
    return text[:80]


def _match_xmind_node_to_testpoint(node, testpoints):
    node_title = node.get('normalized_title') or _normalize_match_text(node.get('title', ''))
    if not node_title:
        return None
    for tp in testpoints:
        title = _normalize_match_text(tp.get('title', ''))
        if not title:
            continue
        if title == node_title or title.startswith(node_title) or node_title.startswith(title[:20]):
            return tp
    return None


def _mapping_by_id(coverage_matrix):
    return {m.get('coverage_id'): m for m in coverage_matrix.get('mappings', []) if m.get('coverage_id')}


def _is_blocking_mapping(mapping):
    if not mapping:
        return False
    coverage_type = mapping.get('coverage_type')
    priority = mapping.get('priority')
    if coverage_type in ['risk', 'state_transition'] and priority == 'P1':
        return True
    if coverage_type == 'review_question' and priority in ['P1', 'P2']:
        return True
    return False


def _match_green_node_to_mapping(node, coverage_matrix):
    pseudo_tp = {'title': node.get('title', ''), 'category': '', 'type': '', 'steps': node.get('text', ''), 'expected': ''}
    for risk in coverage_matrix.get('risks', []):
        if _match_testpoint_to_risk(pseudo_tp, risk):
            return risk.get('risk_id')
    for state in coverage_matrix.get('state_transitions', []):
        if _match_testpoint_to_state(pseudo_tp, state):
            return state.get('state_id')
    for question in coverage_matrix.get('review_questions', []):
        if _match_testpoint_to_review_question(pseudo_tp, question):
            return question.get('question_id')
    return None


def run_xmind_backflow_gate(xmind_path, output_dir):
    """人工评审 XMind 回灌关键门禁：先检查 P1/G3/G4 唯一覆盖是否被破坏。"""
    coverage_matrix, coverage_path = _load_internal_json_payload(output_dir, 'coverage_matrix.json')
    testpoints_payload, testpoints_path = _load_internal_json_payload(output_dir, 'testpoints.json')
    report_path = _get_internal_dir(output_dir) / 'xmind_backflow_report.json'

    if not coverage_matrix or not testpoints_payload:
        report = {
            'status': 'warning',
            'message': '缺少 coverage_matrix.json 或 testpoints.json，无法执行 XMind 回灌门禁',
            'coverage_matrix_path': str(coverage_path),
            'testpoints_path': str(testpoints_path),
            'blocking_issues': [],
            'warnings': ['内部覆盖产物缺失，已跳过关键覆盖破坏校验'],
        }
        _write_internal_json(output_dir, 'xmind_backflow_report.json', report)
        log_console(f"  ⚠️ XMind 回灌门禁跳过: 内部覆盖产物缺失")
        return report

    nodes = _load_reviewed_xmind_nodes(xmind_path)
    testpoints = testpoints_payload.get('test_points', [])
    mapping_lookup = _mapping_by_id(coverage_matrix)
    red_nodes = [n for n in nodes if n.get('is_deleted')]
    green_nodes = [n for n in nodes if n.get('is_added')]
    blocking_issues = []
    warnings = []

    for node in red_nodes:
        tp = _match_xmind_node_to_testpoint(node, testpoints)
        if not tp:
            warnings.append({'type': 'deleted_unmatched', 'title': node.get('title', ''), 'message': 'flag-red 删除节点未匹配到原始测试点'})
            continue
        for coverage_id in tp.get('coverage_ids', []):
            mapping = mapping_lookup.get(coverage_id)
            if not _is_blocking_mapping(mapping):
                continue
            covered_by = mapping.get('covered_by', [])
            if len(covered_by) <= 1 or tp.get('testpoint_id') in covered_by and len(covered_by) == 1:
                blocking_issues.append({
                    'type': 'deleted_unique_coverage',
                    'title': node.get('title', ''),
                    'testpoint_id': tp.get('testpoint_id'),
                    'coverage_id': coverage_id,
                    'coverage_type': mapping.get('coverage_type'),
                    'priority': mapping.get('priority'),
                    'message': 'flag-red 删除了关键覆盖对象的唯一测试点',
                })

    for node in green_nodes:
        matched_id = _match_green_node_to_mapping(node, coverage_matrix)
        if not matched_id:
            warnings.append({'type': 'added_unmapped', 'title': node.get('title', ''), 'message': 'flag-green 新增测试点未能映射到已有需求/风险/状态/评审问题，需人工确认'})

    status = 'blocked' if blocking_issues else 'passed'
    report = {
        'status': status,
        'xmind_path': str(xmind_path),
        'statistics': {
            'testpoint_nodes': len(nodes),
            'flag_red_deleted': len(red_nodes),
            'flag_green_added': len(green_nodes),
            'blocking_count': len(blocking_issues),
            'warning_count': len(warnings),
        },
        'blocking_issues': blocking_issues,
        'warnings': warnings,
    }
    _write_internal_json(output_dir, 'xmind_backflow_report.json', report)
    log_console(f"  📄 XMind 回灌门禁报告: {report_path}")
    if blocking_issues:
        details = '; '.join(item['message'] + ':' + item.get('title', '') for item in blocking_issues[:3])
        raise RuntimeError(f"XMind 回灌门禁未通过，阻止生成最终用例: {details}")
    if warnings:
        log_console(f"  ⚠️ XMind 回灌门禁存在待确认项: {len(warnings)} 个")
    else:
        log_console(f"  ✅ XMind 回灌门禁通过")
    return report

def require_final_decisions(output_dir: Path, stage: str) -> None:
    """Block final artifacts while severe decisions are incomplete or deferred."""
    decision_path = _get_internal_dir(Path(output_dir)) / 'decision_register.json'
    if not decision_path.exists():
        raise RuntimeError(
            f'{stage} 决策门禁未通过：缺少 .ai-internal/decision_register.json'
        )
    try:
        payload = json.loads(decision_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f'{stage} 决策门禁未通过：决策登记不可读') from exc
    blockers = finalization_blocking_issues(payload)
    if blockers:
        raise RuntimeError(
            f'{stage} 决策门禁未通过：' + '；'.join(blockers[:12])
        )

def finalize_review_xmind(output_dir: Path) -> Path:
    """Freeze the user-reviewed XMind internally after explicit confirmation."""
    require_final_decisions(Path(output_dir), 'finalize')
    review_files = sorted(Path(output_dir).glob('*_评审_*_v*.xmind'), key=lambda item: item.stat().st_mtime, reverse=True)
    if not review_files:
        raise RuntimeError('未找到评审版 XMind，无法生成内部确认终稿')
    source = review_files[0]
    target = _get_internal_dir(output_dir) / 'approved_review_final.xmind'
    shutil.copy2(source, target)
    # Ensure the user-edited review file is a valid XMind ZIP before refinement.
    with zipfile.ZipFile(target, 'r') as zf:
        required = {'content.json', 'metadata.json', 'manifest.json'}
        if not required.issubset(set(zf.namelist())):
            raise RuntimeError('评审版 XMind 缺少标准包文件，无法确认终稿')
    _write_internal_json(output_dir, 'approved_review_final.json', {
        'source_review_xmind': str(source), 'approved_xmind': str(target),
        'approved_at': _workflow_timestamp(), 'status': 'approved',
    })
    log_console(f"  📄 已确认终稿（内部）: {target}")
    return target


def _get_confirmed_xmind(output_dir: Path) -> Path:
    internal_final = _get_internal_dir(output_dir) / 'approved_review_final.xmind'
    if internal_final.exists():
        return internal_final
    # 兼容任意公司/责任人命名的评审终稿
    legacy_candidates = list(Path(output_dir).glob('*_评审_终稿.xmind'))
    if legacy_candidates:
        return legacy_candidates[0]
    raise RuntimeError('未确认评审终稿：请先审核评审版 XMind 并执行 --step finalize')

def run_testcase_refine(xmind_path, output_dir, user_config):
    """
    步骤⑤：用例细化

    输入：评审后XMind + 用户配置
    输出：测试用例JSON + 可导入版XMind

    自动前置：用例细化前自动同步评审变更到JSON
    """
    require_final_decisions(Path(output_dir), 'refine')

    log_console("\n" + "=" * 60)
    log_console("📝 步骤⑤ 用例细化")
    log_console("=" * 60)

    # ⭐ 自动同步评审变更（必要操作，无需用户通知）
    log_console("\n  🔄 同步评审变更到JSON...")
    sync_script = get_skill_file('xmind-generator', 'sync_review_changes.py')
    if sync_script.exists():
        # 查找评审报告.md
        review_md = None
        for f in output_dir.glob('*评审报告.md'):
            review_md = f
            break

        # 查找评审终稿.xmind
        final_xmind = None
        for f in output_dir.glob('*评审_终稿.xmind'):
            final_xmind = f
            break

        if review_md and final_xmind:
            try:
                result = subprocess.run(
                    [sys.executable, str(sync_script), str(review_md), str(final_xmind), str(output_dir)],
                    capture_output=True, text=True, timeout=30
                )
                if result.returncode == 0:
                    log_console(f"  ✅ 评审变更同步完成")
                else:
                    log_console(f"  ⚠️ 同步脚本执行异常: {result.stderr}")
            except Exception as e:
                log_console(f"  ⚠️ 同步评审变更失败: {e}")
        else:
            log_console(f"  ⚠️ 未找到评审报告或终稿XMind，跳过同步")
    else:
        log_console(f"  ⚠️ 同步脚本未找到: {sync_script}")

    log_console(f"\n  📄 输入: {xmind_path}")
    log_console(f"  📋 配置: caseGroup={user_config.get('caseGroup')}, version={user_config.get('version')}")

    # 1. XMind 回灌关键门禁：阻止人工评审破坏 P1/G3/G4 唯一覆盖
    run_xmind_backflow_gate(xmind_path, output_dir)

    # 2. 从评审终稿XMind提取测试用例
    test_cases = _extract_testcases_from_xmind(xmind_path)
    log_console(f"  ✅ 提取测试用例: {len(test_cases)} 个")

    # 3. 评定优先级
    for tc in test_cases:
        tc['priority'] = assess_priority(tc.get('title', ''))
    p1_count = sum(1 for tc in test_cases if tc['priority'] == 'P1')
    p2_count = sum(1 for tc in test_cases if tc['priority'] == 'P2')
    log_console(f"  ✅ 优先级评定: P1={p1_count}, P2={p2_count}")

    # 4. 生成测试用例JSON
    internal_dir = _get_internal_dir(output_dir)
    json_path = internal_dir / '测试用例.json'
    testcase_data = {
        "schema_version": "1.0.0",
        "workflow_version": "testcase-generator-2026.07",
        "generated_at": _workflow_timestamp(),
        "metadata": {
            "title": "测试用例",
            "version": user_config.get('version', ''),
            "caseGroup": user_config.get('caseGroup', ''),
            "manager": (user_config.get('manager') or '测试负责人'),
            "relateReqCode": user_config.get('relateReqCode', ''),
            "statistics": {
                "total_cases": len(test_cases),
                "p1_count": p1_count,
                "p2_count": p2_count
            }
        },
        "testcases": test_cases
    }
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(testcase_data, f, ensure_ascii=False, indent=2)
    log_console(f"  📄 测试用例 JSON: {json_path}")
    generate_internal_json_compatibility_report(output_dir)

    # 5. 生成可导入版XMind
    executable_path = _generate_executable_xmind(test_cases, output_dir, xmind_path.parent, user_config)
    log_console(f"  📄 可导入版 XMind: {executable_path}")

    return executable_path, json_path


def _extract_testcases_from_xmind(xmind_path):
    """从评审终稿XMind提取测试用例"""
    import zipfile

    test_cases = []

    try:
        with zipfile.ZipFile(xmind_path, 'r') as zf:
            content_json = json.loads(zf.read('content.json'))
            root_topic = content_json[0]['rootTopic']
            _traverse_for_testcases(root_topic, test_cases, path=[])
    except Exception as e:
        log_console(f"  ⚠️ 读取XMind文件失败: {e}")
        return []

    return test_cases


def _traverse_for_testcases(topic, test_cases, path=None):
    '''Traverse reviewed XMind and extract executable testcase drafts.'''
    if path is None:
        path = []

    title = topic.get('title', '')
    current_path = path + [title]
    children = topic.get('children', {}).get('attached', [])

    has_steps = False
    has_expected = False
    steps_text = ''
    expected_text = ''
    precondition = ''

    for child in children:
        child_title = child.get('title', '')
        if '操作步骤' in child_title:
            has_steps = True
            steps_text = child_title.replace('操作步骤：', '').replace('操作步骤:', '').strip()
        elif '预期结果' in child_title:
            has_expected = True
            expected_text = child_title.replace('预期结果：', '').replace('预期结果:', '').strip()
        elif '前置条件' in child_title:
            precondition = child_title.replace('前置条件：', '').replace('前置条件:', '').strip()

    is_structured_case = has_steps and has_expected
    is_compact_leaf = _is_xmind_testpoint_topic(topic, current_path) and not is_structured_case

    if is_structured_case or is_compact_leaf:
        clean_title = re.sub(r'^\d+\.\d+\.\d+\s+', '', title)
        clean_title = re.sub(r'\s*[?(].*?[?)]', '', clean_title).strip()
        if len(clean_title) > 80:
            clean_title = clean_title[:80]

        tc_type = '功能测试'
        for p in current_path:
            if '异常' in p or '边界' in p:
                tc_type = '异常/边界'
                break
            if '接口' in p:
                tc_type = '接口测试'
                break

        if is_compact_leaf:
            precondition = f'准备满足测试场景的数据：{clean_title}'
            steps_text = f'''1.准备满足场景的数据：{clean_title}
2.执行对应业务操作或调用相关接口
3.查看系统处理结果、数据记录或接口响应'''
            expected_text = f'''1.测试数据准备完成
2.对应业务操作或接口调用已触发
3.{clean_title}'''

        test_cases.append({
            'title': clean_title,
            'category': _extract_category(current_path),
            'type': tc_type,
            'precondition': precondition or '无特殊前置条件',
            'steps': steps_text,
            'expected': expected_text,
            'path': current_path
        })
        return

    for child in children:
        _traverse_for_testcases(child, test_cases, current_path)


def _extract_category(path):
    """Extract testcase category from the reviewed XMind path."""
    test_categories = ['功能测试', '异常测试', '边界测试', '接口测试', '数据验证', '配置测试', '性能测试', '集成测试', '安全测试']
    for p in path:
        if p in test_categories or any(cat in p for cat in test_categories):
            return p
    for i, p in enumerate(path):
        if p == '测试分析' and i + 1 < len(path):
            return path[i + 1]
    return '功能测试'


def _generate_executable_xmind(test_cases, output_dir, source_dir, user_config=None):
    """生成可导入版XMind"""
    user_config = user_config or {}
    company = str(user_config.get('company') or '测试组织').strip()
    manager = (user_config.get('manager') or '测试负责人')

    # 尝试使用executable_xmind.py脚本
    executable_script = get_skill_file('xmind-generator', 'executable_xmind.py')
    if executable_script.exists():
        try:
            # 使用终稿XMind生成（glob 兼容任意命名）
            final_candidates = list(output_dir.glob('*_评审_终稿.xmind'))
            final_xmind = final_candidates[0] if final_candidates else None
            if final_xmind and final_xmind.exists():
                result = subprocess.run(
                    [sys.executable, str(executable_script), str(final_xmind), str(output_dir),
                     '--company', company, '--manager', str(manager)],
                    capture_output=True, text=True, timeout=60
                )
                if result.returncode == 0:
                    # 查找生成的可导入版（兼容任意命名）
                    exe_candidates = [f for f in output_dir.glob('*.xmind')
                                    if '评审' not in f.name and f != final_xmind]
                    if exe_candidates:
                        return str(exe_candidates[0])
                else:
                    log_console(f"  ⚠️ executable_xmind.py执行异常: {result.stderr}")
        except Exception as e:
            log_console(f"  ⚠️ executable_xmind.py执行失败: {e}")

    # 降级：手动构建可导入版XMind
    return _build_executable_xmind_manual(test_cases, output_dir, company, manager)


def _build_executable_xmind_manual(test_cases, output_dir, company='测试组织', manager='测试负责人'):
    """手动构建可导入版XMind（ZIP格式）"""
    import zipfile

    # 按测试大类分组
    categories = {}
    for tc in test_cases:
        cat = tc.get('category', '功能测试')
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(tc)

    # 从源目录提取项目名称和日期
    project_name = output_dir.parent.name
    if '测试分析' in project_name or '评审' in project_name:
        project_name = output_dir.parent.parent.name
    project_name = project_name.replace('【', '').replace('】', '')

    date_match = re.search(r'(20\d{6})', str(output_dir))
    date_str = date_match.group(1) if date_match else datetime.now().strftime('%Y%m%d')
    root_year = date_str[:4]
    year_month = date_str[:6]
    full_date = date_str

    # 构建XMind结构（使用统一的 gen_id）
    gen_id = gid

    func_test_children = []
    for cat_name, cases in categories.items():
        cat_children = []
        for tc in cases:
            case_node = {
                'id': gen_id(),
                'class': 'attached topic',
                'title': f"{tc['title']} [{tc['priority']}]",
                'children': {
                    'attached': [
                        {
                            'id': gen_id(),
                            'class': 'attached topic',
                            'title': f"前置条件：{tc['precondition']}",
                            'children': {
                                'attached': [
                                    {
                                        'id': gen_id(),
                                        'class': 'attached topic',
                                        'title': tc['steps'],
                                        'children': {
                                            'attached': [
                                                {'id': gen_id(), 'class': 'attached topic', 'title': tc['expected']}
                                            ]
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                }
            }
            cat_children.append(case_node)

        if cat_children:
            func_test_children.append({
                'id': gen_id(),
                'class': 'attached topic',
                'title': cat_name,
                'children': {'attached': cat_children}
            })

    # 构建完整层级
    jdzx_children = [
        {
            'id': gen_id(),
            'class': 'attached topic',
            'title': '功能测试',
            'children': {'attached': func_test_children}
        }
    ]

    project_children = [
        {
            'id': gen_id(),
            'class': 'attached topic',
            'title': f'【{project_name}】',
            'children': {
                'attached': [
                    {
                        'id': gen_id(),
                        'class': 'attached topic',
                        'title': company,
                        'markers': [{'markerId': 'star-red'}],
                        'children': {'attached': jdzx_children}
                    }
                ]
            }
        }
    ]

    day_children = [
        {'id': gen_id(), 'class': 'attached topic', 'title': full_date, 'children': {'attached': project_children}},
    ]
    month_children = [
        {'id': gen_id(), 'class': 'attached topic', 'title': year_month, 'children': {'attached': day_children}},
    ]

    root_id = gen_id()
    sheet_id = gen_id()

    content = [{
        'id': sheet_id,
        'revisionId': gen_id(),
        'class': 'sheet',
        'rootTopic': {
            'id': root_id,
            'revisionId': gen_id(),
            'class': 'attached topic',
            'title': root_year,
            'children': {'attached': month_children}
        },
        'title': '可执行测试用例'
    }]

    metadata = {
        'dataStructureVersion': '2',
        'modifier': 'Vana 22.11.3771.202305311822',
        'layoutEngineVersion': '3',
        'activeSheetId': sheet_id,
        'creator': {'name': 'Vana', 'version': '25.01.01061'}
    }

    manifest = {
        'file-entries': {
            'content.json': {},
            'metadata.json': {},
            'Thumbnails/thumbnail.png': {}
        }
    }

    # 写入ZIP文件
    executable_path = output_dir / f'{company}_{manager}.xmind'
    with zipfile.ZipFile(executable_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('content.json', json.dumps(content, ensure_ascii=False, indent=2))
        zf.writestr('metadata.json', json.dumps(metadata, ensure_ascii=False, indent=2))
        zf.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        zf.writestr('Thumbnails/thumbnail.png', b'')

    return str(executable_path)

__all__ = [
    name for name, value in globals().items()
    if callable(value) and getattr(value, "__module__", None) == __name__
]
