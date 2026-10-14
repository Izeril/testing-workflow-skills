#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
代码上下文分析器
从变更代码提取业务逻辑，用于生成针对性测试点

核心功能：
1. 从提测文档提取开发自测用例（作为参考）
2. 分析变更代码的if/else判断条件
3. 分析变更代码的try-catch异常处理
4. 分析Mapper XML的SQL变更
5. 生成针对性的测试点提示
"""

import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List

from ..git_runner import run_git

def _validate_git_ref(ref: str) -> str:
    value = str(ref or "")
    if not value or value.startswith("-") or any(char in value for char in "\x00\r\n"):
        raise ValueError(f"非法 Git 引用: {value!r}")
    return value


def _run_git(repo_path: str, args: List[str], timeout: int):
    return run_git(repo_path, args, timeout=timeout, executor=subprocess.run)


def _relative_repo_path(file_path: str, repo_path: str) -> str:
    try:
        return Path(file_path).resolve().relative_to(Path(repo_path).resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"文件不在代码仓库内: {file_path}") from exc



def extract_dev_test_cases_from_test_doc(test_doc_content: str) -> List[Dict]:
    """从提测文档提取开发自测用例（作为参考，不硬加入评审文档）"""
    dev_test_cases = []

    # 格式1: TC001、TC002 等编号格式
    tc_pattern = r'(TC\d+)[\s:：]+(.+?)(?:\n|$)'
    tc_matches = re.findall(tc_pattern, test_doc_content)
    for tc_id, tc_desc in tc_matches:
        dev_test_cases.append({
            'id': tc_id,
            'description': tc_desc.strip(),
            'source': 'dev_test'
        })

    # 格式2: 步骤格式（1. xxx 2. xxx）
    step_pattern = r'(?:自测|测试)[步骤]*[：:\n]+\s*((?:\d+[\.、]\s*.+?\n)+)'
    step_matches = re.findall(step_pattern, test_doc_content)
    for steps_block in step_matches:
        steps = re.findall(r'\d+[\.、]\s*(.+?)\n', steps_block)
        if steps:
            dev_test_cases.append({
                'id': 'DEV_STEPS',
                'description': '开发自测步骤',
                'steps': steps,
                'source': 'dev_test'
            })

    # 格式3: 表格格式（| 序号 | 测试项 | 预期结果 |）
    table_pattern = r'\|\s*序号\s*\|\s*测试项\s*\|\s*预期结果\s*\|\s*\n((?:\|.+\|\n)+)'
    table_matches = re.findall(table_pattern, test_doc_content)
    for table_block in table_matches:
        rows = re.findall(r'\|\s*(\d+)\s*\|\s*([^|]+)\s*\|\s*([^|]+)\s*\|', table_block)
        for row in rows:
            dev_test_cases.append({
                'id': f'TABLE_{row[0]}',
                'description': row[1].strip(),
                'expected': row[2].strip(),
                'source': 'dev_test_table'
            })

    return dev_test_cases


def analyze_changed_code_context(repo_path: str, branch: str, baseline: str = 'master', changed_files: List[str] = None) -> Dict:
    """分析变更代码的上下文，理解业务逻辑

    返回：
    - 变更文件的业务逻辑摘要
    - if/else判断条件（含测试提示）
    - try-catch异常处理（含测试提示）
    - SQL变更（含测试提示）
    """
    context_analysis = {
        'changed_files_detail': [],
        'business_logic': [],
        'decision_points': [],
        'exception_handling': [],
        'sql_changes': [],
        'dev_test_reference': []
    }

    if not os.path.exists(repo_path):
        return context_analysis

    try:
        # 获取变更文件列表
        if not changed_files:
            revision_range = f"{_validate_git_ref(baseline)}...{_validate_git_ref(branch)}"
            result = _run_git(repo_path, ["diff", "--name-only", revision_range, "--"], 30)
            changed_files = result.stdout.strip().split('\n') if result.stdout else []

        # 分析Java文件（限制数量）
        java_files = [f for f in changed_files if f.endswith('.java')][:20]
        for file_path in java_files:
            full_path = os.path.join(repo_path, file_path)
            if os.path.exists(full_path):
                file_context = analyze_java_file_context(full_path, repo_path, branch, baseline)
                context_analysis['changed_files_detail'].append(file_context)

                if file_context.get('decision_points'):
                    context_analysis['decision_points'].extend(file_context['decision_points'])
                if file_context.get('exception_handling'):
                    context_analysis['exception_handling'].extend(file_context['exception_handling'])
                if file_context.get('business_logic'):
                    context_analysis['business_logic'].extend(file_context['business_logic'])

        # 分析Mapper XML文件
        xml_files = [f for f in changed_files if f.endswith('.xml')][:10]
        for file_path in xml_files:
            full_path = os.path.join(repo_path, file_path)
            if os.path.exists(full_path):
                file_context = analyze_mapper_xml_context(full_path, repo_path, branch, baseline)
                context_analysis['changed_files_detail'].append(file_context)

                if file_context.get('sql_changes'):
                    context_analysis['sql_changes'].extend(file_context['sql_changes'])

    except Exception as e:
        print(f"代码上下文分析异常: {e}")

    return context_analysis


def analyze_java_file_context(file_path: str, repo_path: str, branch: str, baseline: str) -> Dict:
    """分析单个Java文件的变更上下文"""
    file_context = {
        'file': file_path,
        'class_name': '',
        'methods': [],
        'decision_points': [],
        'exception_handling': [],
        'business_logic': [],
        'annotations': []
    }

    try:
        # 获取文件变更内容
        relative_path = _relative_repo_path(file_path, repo_path)
        revision_range = f"{_validate_git_ref(baseline)}...{_validate_git_ref(branch)}"
        result = _run_git(repo_path, ["diff", revision_range, "--", relative_path], 10)
        diff_content = result.stdout

        if not diff_content:
            return file_context

        # 分析新增的行
        added_lines = [line[1:] for line in diff_content.split('\n') if line.startswith('+') and not line.startswith('+++')]

        # 提取类名
        class_match = re.search(r'class\s+(\w+)', diff_content)
        if class_match:
            file_context['class_name'] = class_match.group(1)

        # 提取注解
        annotations = re.findall(r'@(\w+)\s*(?:\([^)]*\)|)', diff_content)
        file_context['annotations'] = annotations

        # 提取if/else判断条件
        if_pattern = r'if\s*\(([^)]+)\)'
        if_matches = re.findall(if_pattern, '\n'.join(added_lines))
        for condition in if_matches:
            simplified = simplify_condition(condition)
            file_context['decision_points'].append({
                'condition': simplified,
                'original': condition,
                'test_hint': generate_test_hint_from_condition(simplified)
            })

        # 提取try-catch异常处理
        catch_pattern = r'catch\s*\((\w+Exception)\s*\w+\)'
        catch_matches = re.findall(catch_pattern, diff_content)
        for exc in catch_matches:
            file_context['exception_handling'].append({
                'exception_type': exc,
                'test_hint': f"模拟{exc}异常，验证异常处理正确"
            })

        # 提取方法调用
        method_call_pattern = r'\.(\w+)\s*\('
        method_calls = re.findall(method_call_pattern, '\n'.join(added_lines))
        unique_calls = list(set(method_calls))[:10]
        for method in unique_calls:
            if method not in ['get', 'set', 'toString', 'equals', 'hashCode', 'valueOf', 'forEach', 'stream']:
                file_context['business_logic'].append({
                    'method': method,
                    'test_hint': f"验证{method}方法调用结果正确"
                })

        # 提取新增方法定义
        method_def_pattern = r'(?:public|private|protected)\s+\w+\s+(\w+)\s*\([^)]*\)'
        method_defs = re.findall(method_def_pattern, diff_content)
        file_context['methods'] = method_defs

    except Exception as e:
        print(f"分析文件 {file_path} 异常: {e}")

    return file_context


def analyze_mapper_xml_context(file_path: str, repo_path: str, branch: str, baseline: str) -> Dict:
    """分析Mapper XML文件的变更上下文"""
    file_context = {
        'file': file_path,
        'namespace': '',
        'sql_changes': [],
        'tables_involved': []
    }

    try:
        relative_path = _relative_repo_path(file_path, repo_path)
        revision_range = f"{_validate_git_ref(baseline)}...{_validate_git_ref(branch)}"
        result = _run_git(repo_path, ["diff", revision_range, "--", relative_path], 10)
        diff_content = result.stdout

        if not diff_content:
            return file_context

        # 提取namespace
        ns_match = re.search(r'namespace\s*=\s*"([^"]+)"', diff_content)
        if ns_match:
            file_context['namespace'] = ns_match.group(1)

        # 分析新增的SQL
        added_lines = [line[1:] for line in diff_content.split('\n') if line.startswith('+') and not line.startswith('+++')]

        # 提取涉及的表
        tables = re.findall(r'(?:from|into|join|update)\s+(\w+)', '\n'.join(added_lines), re.IGNORECASE)
        file_context['tables_involved'] = list(set(tables))

        # 提取WHERE条件
        where_pattern = r'where\s+(.+?)(?:\n|<|order|group|limit)'
        where_matches = re.findall(where_pattern, '\n'.join(added_lines), re.IGNORECASE | re.DOTALL)
        for where_clause in where_matches:
            simplified = simplify_where_clause(where_clause)
            file_context['sql_changes'].append({
                'type': 'WHERE',
                'condition': simplified,
                'test_hint': generate_test_hint_from_where(simplified)
            })

        # 提取聚合函数
        agg_pattern = r'(SUM|COUNT|AVG|MAX|MIN)\s*\(([^)]+)\)'
        agg_matches = re.findall(agg_pattern, '\n'.join(added_lines), re.IGNORECASE)
        for func, field in agg_matches:
            file_context['sql_changes'].append({
                'type': 'AGGREGATION',
                'function': func,
                'field': field,
                'test_hint': f"验证{func}({field})聚合结果正确"
            })

    except Exception as e:
        print(f"分析Mapper {file_path} 异常: {e}")

    return file_context


def simplify_condition(condition: str) -> str:
    """简化条件表达式，提取关键业务逻辑"""
    simplified = condition
    simplified = re.sub(r'\w+\.get\w+\(\)', '属性', simplified)
    simplified = re.sub(r'\w+\.is\w+\(\)', '状态', simplified)
    simplified = re.sub(r'!=\s*null', '不为空', simplified)
    simplified = re.sub(r'==\s*null', '为空', simplified)
    simplified = re.sub(r'==\s*\d+', '等于某值', simplified)
    simplified = re.sub(r'>\s*\d+', '大于某值', simplified)
    simplified = re.sub(r'<\s*\d+', '小于某值', simplified)
    simplified = re.sub(r'StringUtils\.isEmpty\([^)]+\)', '字符串为空', simplified)
    simplified = re.sub(r'StringUtils\.isNotEmpty\([^)]+\)', '字符串不为空', simplified)
    simplified = re.sub(r'CollectionUtils\.isEmpty\([^)]+\)', '集合为空', simplified)
    simplified = re.sub(r'CollectionUtils\.isNotEmpty\([^)]+\)', '集合不为空', simplified)
    simplified = re.sub(r'\s*&&\s*', ' 且 ', simplified)
    simplified = re.sub(r'\s*\|\|\s*', ' 或 ', simplified)
    return simplified.strip()


def simplify_where_clause(where_clause: str) -> str:
    """简化WHERE条件"""
    simplified = where_clause
    simplified = re.sub(r'\s+', ' ', simplified)
    simplified = re.sub(r'=\s*\{[^}]+\}', '等于参数', simplified)
    simplified = re.sub(r'like\s*\{[^}]+\}', '模糊匹配参数', simplified)
    simplified = re.sub(r'in\s*\([^)]+\)', 'IN列表', simplified)
    return simplified.strip()[:100]


def generate_test_hint_from_condition(condition: str) -> str:
    """从条件生成测试提示"""
    hints = []

    if '为空' in condition or '不为空' in condition:
        hints.append("验证空值处理正确")

    if '大于' in condition or '小于' in condition:
        hints.append("验证边界值处理正确")

    if '等于某值' in condition:
        hints.append("验证特定值处理正确")

    if '状态' in condition:
        hints.append("验证状态判断正确")

    if '属性' in condition:
        hints.append("验证属性值判断正确")

    if '且' in condition or '或' in condition:
        hints.append("验证组合条件判断正确")

    return hints[0] if hints else f"验证条件【{condition}】判断正确"


def generate_test_hint_from_where(where_clause: str) -> str:
    """从WHERE条件生成测试提示"""
    hints = []

    if '等于参数' in where_clause:
        hints.append("验证参数精确匹配过滤正确")

    if '模糊匹配参数' in where_clause:
        hints.append("验证模糊匹配过滤正确")

    if 'IN列表' in where_clause:
        hints.append("验证IN列表过滤正确")

    if 'between' in where_clause.lower():
        hints.append("验证范围过滤正确")

    return hints[0] if hints else f"验证过滤条件【{where_clause[:50]}】正确"


def generate_test_points_from_code_context(context_analysis: Dict, doc_keywords: List[str]) -> List[Dict]:
    """基于代码上下文生成针对性测试点

    输入：
    - context_analysis: 代码上下文分析结果
    - doc_keywords: 需求文档关键词

    输出：
    - 测试点列表（含标题、步骤、优先级）
    """
    test_points = []
    tp_index = 1

    # 1. 基于决策点生成测试点
    for dp in context_analysis.get('decision_points', []):
        condition = dp.get('condition', '')
        test_hint = dp.get('test_hint', '')

        test_point = {
            'title': f"{test_hint}",
            'priority': 1,
            'steps': [
                f"前置条件：满足条件【{condition}】",
                f"1.构造满足条件的数据",
                f"2.执行业务操作",
                f"3.{test_hint}",
                f"4.构造不满足条件的数据",
                f"5.验证反向条件处理正确"
            ]
        }
        test_points.append(test_point)
        tp_index += 1

    # 2. 基于异常处理生成测试点
    for exc in context_analysis.get('exception_handling', []):
        exc_type = exc.get('exception_type', '')
        test_hint = exc.get('test_hint', '')

        test_point = {
            'title': f"{test_hint}",
            'priority': 1,
            'steps': [
                f"前置条件：业务流程正常",
                f"1.模拟{exc_type}异常场景",
                f"2.验证异常被正确捕获",
                f"3.验证异常处理逻辑（日志/告警/回滚）",
                f"4.验证不影响其他业务流程"
            ]
        }
        test_points.append(test_point)
        tp_index += 1

    # 3. 基于SQL变更生成测试点
    for sql_change in context_analysis.get('sql_changes', []):
        change_type = sql_change.get('type', '')
        test_hint = sql_change.get('test_hint', '')

        if change_type == 'WHERE':
            condition = sql_change.get('condition', '')
            test_point = {
                'title': f"{test_hint}",
                'priority': 1,
                'steps': [
                    f"前置条件：数据库有测试数据",
                    f"1.构造满足过滤条件的数据",
                    f"2.执行查询操作",
                    f"3.验证返回数据正确",
                    f"4.构造不满足条件的数据",
                    f"5.验证被正确过滤"
                ]
            }
        elif change_type == 'AGGREGATION':
            func = sql_change.get('function', '')
            field = sql_change.get('field', '')
            test_point = {
                'title': f"{test_hint}",
                'priority': 1,
                'steps': [
                    f"前置条件：数据已准备",
                    f"1.执行聚合查询",
                    f"2.手动计算验证{func}({field})结果",
                    f"3.验证空值处理正确",
                    f"4.验证边界值（0、负数）处理"
                ]
            }
        else:
            test_point = {
                'title': test_hint,
                'priority': 2,
                'steps': [
                    "前置条件：数据已准备",
                    f"1.{test_hint}"
                ]
            }

        test_points.append(test_point)
        tp_index += 1

    # 4. 基于文档关键词补充通用测试点
    if any(k in doc_keywords for k in ['分表', 'sharding']):
        test_points.append({
            'title': '分表路由测试',
            'priority': 1,
            'steps': [
                '前置条件：分表已创建',
                '1.插入数据验证路由正确',
                '2.查询数据验证跨分表正确',
                '3.验证分表不存在时异常处理'
            ]
        })

    if any(k in doc_keywords for k in ['定时任务', 'job', 'xxl-job']):
        test_points.append({
            'title': '定时任务执行测试',
            'priority': 1,
            'steps': [
                '前置条件：定时任务已配置',
                '1.等待或触发任务执行',
                '2.检查执行日志',
                '3.验证执行结果正确'
            ]
        })

    if any(k in doc_keywords for k in ['starrocks', '同步', '迁移']):
        test_points.append({
            'title': '数据同步完整性测试',
            'priority': 1,
            'steps': [
                '前置条件：源数据已准备',
                '1.执行数据同步',
                '2.对比源和目标数据量',
                '3.抽查关键字段一致性'
            ]
        })

    return test_points


if __name__ == '__main__':
    import sys
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    # 测试
    test_doc = """
    TC001: 测试数据同步功能
    TC002: 测试定时任务执行

    自测步骤：
    1. 启动服务
    2. 执行查询
    3. 验证结果
    """

    dev_cases = extract_dev_test_cases_from_test_doc(test_doc)
    print(f"提取到 {len(dev_cases)} 个开发自测用例")
    for case in dev_cases:
        print(f"  - {case['id']}: {case.get('description', '')}")
