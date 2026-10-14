#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
配置提取器
从Java代码、配置文件、注解中提取实际配置值

使用方法:
    from config_extractor import extract_config_values

    configs = extract_config_values(file_content)
"""

import re
from typing import Dict, List, Any, Tuple


def extract_value_annotations(content: str) -> List[Dict[str, Any]]:
    """
    提取@Value注解的配置值

    Returns:
        [
            {
                'field': 'financialMaxCount',
                'config_key': 'capitalconf.financial.maxCount',
                'default_value': '300000',
                'line': 15
            }
        ]
    """
    configs = []
    lines = content.split('\n')

    # 匹配@Value("${config.key:default}")
    value_pattern = re.compile(r'@Value\s*\(\s*"\$\{([^:}]+)(?::([^}]*))?\}"\s*\)')

    for i, line in enumerate(lines):
        match = value_pattern.search(line)
        if match:
            config_key = match.group(1)
            default_value = match.group(2) if match.group(2) else '无默认值'

            # 提取字段名（下一行）
            field_name = ''
            if i + 1 < len(lines):
                field_match = re.search(r'(?:private|public|protected)\s+\w+\s+(\w+)', lines[i + 1])
                if field_match:
                    field_name = field_match.group(1)

            configs.append({
                'field': field_name,
                'config_key': config_key,
                'default_value': default_value,
                'line': i + 1,
                'source': '@Value注解',
            })

    return configs


def extract_properties_config(content: str) -> List[Dict[str, Any]]:
    """
    从.properties或.yml内容提取配置

    Returns:
        [
            {
                'config_key': 'spring.datasource.url',
                'config_value': 'jdbc:mysql://localhost:3306/db',
                'source': '配置文件'
            }
        ]
    """
    configs = []
    lines = content.split('\n')

    # 匹配key=value格式
    kv_pattern = re.compile(r'^([^#][\w.]+)\s*=\s*(.+)$')

    for line in lines:
        match = kv_pattern.match(line.strip())
        if match:
            key = match.group(1)
            value = match.group(2)

            configs.append({
                'config_key': key,
                'config_value': value,
                'source': '配置文件',
            })

    return configs


def extract_constant_values(content: str) -> List[Dict[str, Any]]:
    """
    提取常量定义

    Returns:
        [
            {
                'constant_name': 'FINANCIAL_EXCLUDE_STATISTICS',
                'constant_value': '"FINANCIAL_EXCLUDE_STATISTICS"',
                'line': 10
            }
        ]
    """
    constants = []
    lines = content.split('\n')

    # 匹配public static final
    const_pattern = re.compile(
        r'public\s+static\s+final\s+(\w+)\s+(\w+)\s*=\s*(.+?);'
    )

    for i, line in enumerate(lines):
        match = const_pattern.search(line)
        if match:
            type_name = match.group(1)
            const_name = match.group(2)
            value = match.group(3).strip().strip('"').strip("'")

            constants.append({
                'constant_name': const_name,
                'constant_value': value,
                'type': type_name,
                'line': i + 1,
                'source': '常量定义',
            })

    return constants


def extract_cron_expressions(content: str) -> List[Dict[str, Any]]:
    """
    提取定时任务cron表达式

    Returns:
        [
            {
                'job_name': 'getReportData',
                'cron': '0 0 1 * * ?',
                'description': '每天凌晨1点执行',
                'line': 20
            }
        ]
    """
    cron_configs = []
    lines = content.split('\n')

    # 匹配@Scheduled(cron = "...")
    cron_pattern = re.compile(r'@Scheduled\s*\(\s*cron\s*=\s*"([^"]+)"')

    for i, line in enumerate(lines):
        match = cron_pattern.search(line)
        if match:
            cron = match.group(1)

            # 查找方法名
            job_name = ''
            for j in range(i, min(i + 5, len(lines))):
                method_match = re.search(r'public\s+\w+\s+(\w+)\s*\(', lines[j])
                if method_match:
                    job_name = method_match.group(1)
                    break

            # 解析cron描述（简化版）
            description = parse_cron(cron)

            cron_configs.append({
                'job_name': job_name,
                'cron': cron,
                'description': description,
                'line': i + 1,
                'source': '@Scheduled注解',
            })

    return cron_configs


def parse_cron(cron: str) -> str:
    """
    简化解析cron表达式（仅支持基本格式）
    """
    parts = cron.split()
    if len(parts) >= 5:
        minute, hour, day, month, day_of_week = parts[:5]

        desc_parts = []
        if hour != '*':
            desc_parts.append(f'{hour}点')
        if minute != '0' and minute != '*':
            desc_parts.append(f'{minute}分')

        if desc_parts:
            return f'每天{"".join(desc_parts)}执行'
        else:
            return '频繁执行'

    return '未知'


def extract_config_values(content: str) -> Dict[str, Any]:
    """
    完整提取配置值

    Args:
        content: 文件内容（Java代码或配置文件）

    Returns:
        {
            'value_annotations': @Value注解配置,
            'properties': 配置文件内容,
            'constants': 常量定义,
            'cron_expressions': 定时任务cron,
            'config_summary': 配置总结,
        }
    """
    value_annotations = extract_value_annotations(content)
    properties = extract_properties_config(content)
    constants = extract_constant_values(content)
    cron_expressions = extract_cron_expressions(content)

    # 生成总结
    summary_parts = []
    if value_annotations:
        summary_parts.append(f'发现 {len(value_annotations)} 个@Value配置')
    if properties:
        summary_parts.append(f'发现 {len(properties)} 个配置项')
    if constants:
        summary_parts.append(f'发现 {len(constants)} 个常量')
    if cron_expressions:
        summary_parts.append(f'发现 {len(cron_expressions)} 个定时任务')

    config_summary = '\n'.join(summary_parts) if summary_parts else '未发现明显配置'

    return {
        'value_annotations': value_annotations,
        'properties': properties,
        'constants': constants,
        'cron_expressions': cron_expressions,
        'config_summary': config_summary,
    }


def generate_config_tests(configs: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    基于配置生成测试用例

    Args:
        configs: extract_config_values返回的结果

    Returns:
        测试用例列表
    """
    test_cases = []

    # 1. @Value配置测试
    if configs['value_annotations']:
        for config in configs['value_annotations'][:5]:
            test_cases.append({
                'name': f'配置测试: {config["config_key"]}',
                'precondition': f'配置 {config["config_key"]}={config["default_value"]}',
                'steps': [
                    f'验证字段 {config["field"]} 正确加载配置',
                    f'验证默认值: {config["default_value"]}',
                    '验证配置变更生效',
                ],
                'priority': 'P2',
            })

    # 2. 常量测试
    if configs['constants']:
        test_cases.append({
            'name': '常量验证测试',
            'precondition': '系统已启动',
            'steps': [
                f'验证常量 {const["constant_name"]} = {const["constant_value"]}'
                for const in configs['constants'][:5]
            ],
            'priority': 'P2',
        })

    # 3. 定时任务测试
    if configs['cron_expressions']:
        for cron in configs['cron_expressions']:
            test_cases.append({
                'name': f'定时任务测试: {cron["job_name"]}',
                'precondition': f'Cron: {cron["cron"]} ({cron["description"]})',
                'steps': [
                    '验证定时任务按cron表达式执行',
                    '验证任务执行结果正确',
                    '验证任务失败时重试',
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
    @Service
    public class DailyFinancialServiceImpl {

        @Value("${capitalconf.financial.maxCount:300000}")
        private Integer financialMaxCount;

        @Value("${starrocks.url:jdbc:mysql://localhost:9030}")
        private String starrocksUrl;

        public static final String FINANCIAL_EXCLUDE_STATISTICS = "FINANCIAL_EXCLUDE_STATISTICS";
    }
    '''

    configs = extract_config_values(java_example)

    print(f"配置总结:\n{configs['config_summary']}")
    print(f"\n@Value配置: {len(configs['value_annotations'])}个")
    for config in configs['value_annotations']:
        print(f"  - {config['config_key']} = {config['default_value']}")

    print(f"\n常量: {len(configs['constants'])}个")
    for const in configs['constants']:
        print(f"  - {const['constant_name']} = {const['constant_value']}")

    print(f"\n生成测试用例:")
    test_cases = generate_config_tests(configs)
    for tc in test_cases:
        print(f"  - {tc['name']} (优先级: {tc['priority']})")
