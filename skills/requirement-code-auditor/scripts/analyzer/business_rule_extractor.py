#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
业务规则提取器
从Java代码中提取if/else业务判断、过滤条件、异常处理等业务规则

使用方法:
    from business_rule_extractor import extract_business_rules

    rules = extract_business_rules(java_content)
"""

import re
from typing import Dict, List, Any, Tuple

from .syntax_analyzer import analyze_java_syntax


def extract_if_else_rules(content: str) -> List[Dict[str, Any]]:
    """
    提取if/else业务判断规则

    Returns:
        [
            {
                'condition': 'use_flag != 10 && is_history == 0',
                'action': '执行正常逻辑',
                'else_action': '跳过该数据',
                'line': 25,
                'complexity': 'simple'  # simple/medium/complex
            }
        ]
    """
    rules = []
    lines = content.split('\n')

    # 匹配if语句
    if_pattern = re.compile(r'\bif\s*\((.+?)\)\s*\{')

    i = 0
    while i < len(lines):
        line = lines[i]
        match = if_pattern.search(line)

        if match:
            condition = match.group(1).strip()
            line_start = i + 1

            # 提取if块内容（简化版：到匹配的}为止）
            brace_count = 0
            if_body = []
            has_else = False
            else_body = []

            for j in range(i, min(i + 50, len(lines))):  # 最多查找50行
                for char in lines[j]:
                    if char == '{':
                        brace_count += 1
                    elif char == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            # 检查是否有else
                            rest_of_line = lines[j][lines[j].index('}') + 1:].strip()
                            if rest_of_line.startswith('else'):
                                has_else = True
                                # 提取else块
                                if j + 1 < len(lines) and '{' in lines[j + 1]:
                                    for k in range(j + 1, min(j + 30, len(lines))):
                                        else_body.append(lines[k].strip())
                                        if '}' in lines[k]:
                                            break
                            break

                if brace_count == 0:
                    break

                if_body.append(lines[j].strip())

            # 判断复杂度
            condition_complexity = 'simple'
            if '&&' in condition or '||' in condition:
                condition_complexity = 'medium'
            if condition.count('&&') + condition.count('||') > 2:
                condition_complexity = 'complex'

            # 提取关键动作（前3行）
            action_summary = '\n'.join([l for l in if_body[:3] if l and not l.startswith('//')])
            else_action_summary = '\n'.join([l for l in else_body[:3] if l and not l.startswith('//')])

            rules.append({
                'condition': condition,
                'action': action_summary[:100],
                'else_action': else_action_summary[:100] if has_else else '无',
                'line': line_start,
                'complexity': condition_complexity,
                'has_else': has_else,
            })

        i += 1

    return rules


def extract_switch_rules(content: str) -> List[Dict[str, Any]]:
    """
    提取switch/case业务规则

    Returns:
        [
            {
                'expression': 'status',
                'cases': [
                    {'value': '1', 'action': '处理成功'},
                    {'value': '2', 'action': '处理失败'},
                ]
            }
        ]
    """
    rules = []

    # 匹配switch语句
    switch_pattern = re.compile(r'\bswitch\s*\((.+?)\)\s*\{')
    switch_matches = list(switch_pattern.finditer(content))

    for match in switch_matches:
        expression = match.group(1).strip()
        start_pos = match.end()

        # 提取case分支
        cases = []
        case_pattern = re.compile(r'\bcase\s+(.+?)\s*:\s*(.+?)(?=\bcase\b|\bdefault\b|\})', re.DOTALL)

        # 在switch块内查找
        switch_body = content[start_pos:start_pos + 2000]  # 最多2000字符
        case_matches = case_pattern.findall(switch_body)

        for case_match in case_matches:
            value = case_match[0].strip()
            action = case_match[1].strip()[:100]
            cases.append({
                'value': value,
                'action': action,
            })

        rules.append({
            'expression': expression,
            'cases': cases,
        })

    return rules


def extract_exception_handling(content: str) -> List[Dict[str, Any]]:
    """
    提取异常处理规则

    Returns:
        [
            {
                'try_block': '执行数据库操作',
                'catch_type': 'SQLException',
                'catch_action': '记录日志并抛出异常',
                'has_finally': True,
                'line': 30
            }
        ]
    """
    rules = []
    lines = content.split('\n')

    # 匹配try-catch
    try_pattern = re.compile(r'\btry\s*\{')
    catch_pattern = re.compile(r'\bcatch\s*\(\s*(\w+)(?:\s+\w+)?\s*\)\s*\{')

    i = 0
    while i < len(lines):
        line = lines[i]

        if try_pattern.search(line):
            line_start = i + 1
            try_body = []
            catch_info = None
            has_finally = False

            # 提取try块
            for j in range(i, min(i + 50, len(lines))):
                if catch_pattern.search(lines[j]):
                    catch_match = catch_pattern.search(lines[j])
                    catch_type = catch_match.group(1)

                    # 提取catch块
                    catch_body = []
                    for k in range(j, min(j + 30, len(lines))):
                        catch_body.append(lines[k].strip())
                        if '}' in lines[k]:
                            break

                    catch_info = {
                        'type': catch_type,
                        'action': '\n'.join(catch_body[:3])[:100],
                    }
                    break

                if 'finally' in lines[j]:
                    has_finally = True
                    break

                try_body.append(lines[j].strip())

            try_summary = '\n'.join([l for l in try_body[:3] if l and not l.startswith('//')])

            rules.append({
                'try_block': try_summary[:100],
                'catch_type': catch_info['type'] if catch_info else '无',
                'catch_action': catch_info['action'] if catch_info else '无',
                'has_finally': has_finally,
                'line': line_start,
            })

        i += 1

    return rules


def extract_validation_rules(content: str) -> List[Dict[str, Any]]:
    """
    提取数据校验规则（参数校验、数据合法性检查）

    Returns:
        [
            {
                'field': 'taskId',
                'validation': '非空校验',
                'condition': 'taskId != null',
                'error_message': 'taskId不能为空',
                'line': 15
            }
        ]
    """
    rules = []
    lines = content.split('\n')

    # 匹配常见校验模式
    patterns = [
        # if (xxx == null) throw/return
        r'if\s*\(\s*(\w+)\s*==\s*null\s*\)\s*(?:throw|return)',
        # if (StringUtils.isEmpty(xxx))
        r'(?:StringUtils|ObjectUtils)\.(?:isEmpty|isBlank|isNull)\s*\(\s*(\w+)\s*\)',
        # @NotBlank, @NotNull, @Valid
        r'@(?:NotBlank|NotNull|NotEmpty|Valid)\s*\w*\s*(?:\w+)',
        # if (xxx <= 0)
        r'if\s*\(\s*(\w+)\s*(?:<=|<|>=|>)\s*(\d+)\s*\)',
    ]

    for i, line in enumerate(lines):
        for pattern in patterns:
            matches = re.findall(pattern, line)
            for match in matches:
                if isinstance(match, tuple):
                    field = match[0]
                else:
                    field = match

                rules.append({
                    'field': field,
                    'validation': '参数校验',
                    'condition': line.strip()[:100],
                    'line': i + 1,
                })

    return rules


def extract_logging_patterns(content: str) -> List[Dict[str, Any]]:
    """
    提取日志模式，理解业务关键点和告警点

    Returns:
        [
            {
                'level': 'error',
                'message': '消费数量超限：{}',
                'condition': 'countPay > 300000',
                'line': 45,
                'business_meaning': '业务告警点'
            }
        ]
    """
    logs = []
    lines = content.split('\n')

    # 匹配日志语句
    log_patterns = [
        (r'logger\.(error|warn|info|debug)\s*\(\s*"([^"]+)"', 'logger'),
        (r'log\.(error|warn|info|debug)\s*\(\s*"([^"]+)"', 'log'),
        (r'LOG\.(error|warn|info|debug)\s*\(\s*"([^"]+)"', 'LOG'),
    ]

    for i, line in enumerate(lines):
        for pattern, log_type in log_patterns:
            matches = re.findall(pattern, line, re.IGNORECASE)
            for match in matches:
                level = match[0]
                message = match[1]

                # 判断业务含义
                business_meaning = '普通日志'
                if level == 'error':
                    business_meaning = '错误/告警点'
                elif '超限' in message or '失败' in message or '异常' in message:
                    business_meaning = '业务告警点'
                elif '完成' in message or '成功' in message:
                    business_meaning = '关键成功点'

                logs.append({
                    'level': level,
                    'message': message,
                    'condition': line.strip()[:100],
                    'line': i + 1,
                    'business_meaning': business_meaning,
                })

    return logs


def extract_business_rules(content: str) -> Dict[str, Any]:
    """提取 Java 业务规则；关键 if/throw/validate 优先采用语法级结果。"""
    syntax = analyze_java_syntax(content)

    if_else_rules = []
    for raw in syntax["if_else_rules"]:
        condition = raw.get("condition", "")
        complexity_count = condition.count("&&") + condition.count("||")
        if_else_rules.append({
            **raw,
            "complexity": raw.get(
                "complexity",
                "complex" if complexity_count > 2 else ("medium" if complexity_count else "simple"),
            ),
            "has_else": raw.get("has_else", bool(raw.get("else_action"))),
            "else_action": raw.get("else_action") or "无",
        })

    switch_rules = extract_switch_rules(content)
    exception_rules = extract_exception_handling(content)
    logging_patterns = extract_logging_patterns(content)

    validation_candidates = extract_validation_rules(content) + syntax["validation_rules"]
    validation_rules = []
    seen_validations = set()
    for rule in validation_candidates:
        key = (rule.get("line"), rule.get("field"), rule.get("condition"), rule.get("validation"))
        if key not in seen_validations:
            seen_validations.add(key)
            validation_rules.append(rule)

    key_decision_points = []
    for rule in if_else_rules:
        if rule["complexity"] in ["medium", "complex"] or rule["has_else"]:
            key_decision_points.append({
                "condition": rule["condition"],
                "line": rule["line"],
                "description": f'第{rule["line"]}行: if({rule["condition"]})',
                "source": rule.get("source", syntax["engine"]),
            })

    summary_parts = []
    if if_else_rules:
        summary_parts.append(f"发现 {len(if_else_rules)} 个if/else业务判断")
    if switch_rules:
        summary_parts.append(f"发现 {len(switch_rules)} 个switch/case分支")
    if exception_rules:
        summary_parts.append(f"发现 {len(exception_rules)} 个异常处理块")
    if syntax["throw_rules"]:
        summary_parts.append(f"发现 {len(syntax['throw_rules'])} 个显式throw")
    if validation_rules:
        summary_parts.append(f"发现 {len(validation_rules)} 个数据校验点")
    error_logs = [item for item in logging_patterns if item["level"] == "error"]
    if error_logs:
        summary_parts.append(f"发现 {len(error_logs)} 个错误/告警日志")
    if syntax.get("fallback_reason"):
        summary_parts.append(f"Java AST降级: {syntax['fallback_reason']}")

    return {
        "if_else_rules": if_else_rules,
        "switch_rules": switch_rules,
        "exception_rules": exception_rules,
        "throw_rules": syntax["throw_rules"],
        "validation_rules": validation_rules,
        "logging_patterns": logging_patterns,
        "business_rules_summary": "\n".join(summary_parts) if summary_parts else "未发现明显业务规则",
        "key_decision_points": key_decision_points,
        "analysis_metadata": {
            "engine": syntax["engine"],
            "parser_available": syntax["parser_available"],
            "fallback_reason": syntax.get("fallback_reason", ""),
            "parse_errors": syntax.get("parse_errors", []),
        },
    }

def generate_business_rule_tests(rules: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    基于业务规则生成测试用例

    Args:
        rules: extract_business_rules返回的结果

    Returns:
        测试用例列表
    """
    test_cases = []

    # 1. 业务判断测试
    if rules['if_else_rules']:
        for rule in rules['if_else_rules'][:5]:  # 最多5个
            test_cases.append({
                'name': f'业务判断测试: if({rule["condition"][:50]}...)',
                'precondition': '准备满足和不满足条件的测试数据',
                'steps': [
                    f'准备满足条件的数据 → 执行: {rule["action"][:50]}',
                    f'准备不满足条件的数据 → 执行: {rule["else_action"][:50]}' if rule['has_else'] else '准备不满足条件的数据 → 跳过',
                    '验证判断逻辑正确',
                ],
                'priority': 'P1' if rule['complexity'] in ['medium', 'complex'] else 'P2',
            })

    # 2. 异常处理测试
    if rules['exception_rules']:
        for rule in rules['exception_rules'][:3]:
            test_cases.append({
                'name': f'异常处理测试: catch({rule["catch_type"]})',
                'precondition': '模拟异常场景',
                'steps': [
                    f'模拟 {rule["catch_type"]} 异常',
                    f'验证异常处理: {rule["catch_action"][:50]}',
                    '验证系统状态正确',
                ],
                'priority': 'P1',
            })

    # 3. 数据校验测试
    if rules['validation_rules']:
        test_cases.append({
            'name': '数据校验测试',
            'precondition': '准备合法和非法数据',
            'steps': [
                f'验证字段 {rule["field"]} 的校验逻辑'
                for rule in rules['validation_rules'][:3]
            ],
            'priority': 'P1',
        })

    # 4. 日志告警测试
    error_logs = [l for l in rules['logging_patterns'] if l['level'] == 'error']
    if error_logs:
        test_cases.append({
            'name': '日志告警测试',
            'precondition': '准备触发告警的场景',
            'steps': [
                f'验证错误日志: {log["message"][:50]}'
                for log in error_logs[:3]
            ],
            'priority': 'P1',
        })

    return test_cases


if __name__ == '__main__':
    import sys
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    # 示例用法
    java_example = '''
    public void processFinancialData(DailyFinancialDto dto) {
        // 参数校验
        if (dto == null) {
            throw new IllegalArgumentException("dto不能为空");
        }

        // 业务判断
        if (dto.getUseFlag() != 10 && dto.getIsHistory() == 0) {
            if (dto.getCountPay() > 300000) {
                logger.error("消费数量超限：{}", JSON.toJSONString(dto));
                return;
            }

            try {
                mapper.insert(dto);
                logger.info("数据插入成功");
            } catch (SQLException e) {
                logger.error("数据库异常", e);
                throw new RuntimeException(e);
            }
        } else {
            logger.debug("跳过无效数据");
        }
    }
    '''

    rules = extract_business_rules(java_example)

    print(f"业务规则总结:\n{rules['business_rules_summary']}")
    print(f"\nif/else规则: {len(rules['if_else_rules'])}个")
    for rule in rules['if_else_rules']:
        print(f"  - if({rule['condition'][:50]}...) 复杂度: {rule['complexity']}")

    print(f"\n异常处理: {len(rules['exception_rules'])}个")
    for rule in rules['exception_rules']:
        print(f"  - catch({rule['catch_type']})")

    print(f"\n数据校验: {len(rules['validation_rules'])}个")
    for rule in rules['validation_rules']:
        print(f"  - {rule['field']}")

    print(f"\n日志模式: {len(rules['logging_patterns'])}个")
    for log in rules['logging_patterns']:
        print(f"  - [{log['level']}] {log['message'][:50]}")

    print(f"\n生成测试用例:")
    test_cases = generate_business_rule_tests(rules)
    for tc in test_cases:
        print(f"  - {tc['name']} (优先级: {tc['priority']})")
