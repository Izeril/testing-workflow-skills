#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
流程图生成器
将业务逻辑、数据流转、调用链等信息可视化为文本流程图

使用方法:
    from flow_diagram_generator import generate_flow_diagram

    diagram = generate_flow_diagram(business_logic)
"""

from typing import Dict, List, Any


def generate_data_flow_diagram(data_flow: Dict[str, Any]) -> str:
    """
    生成数据流转图

    Args:
        data_flow: 数据流转信息

    Returns:
        文本流程图字符串
    """
    lines = []
    lines.append("┌─────────────────────────────────────────┐")
    lines.append("│           数据流转图                    │")
    lines.append("└─────────────────────────────────────────┘")
    lines.append("")

    # 数据源
    if data_flow.get('sources'):
        lines.append("📥 数据来源:")
        for source in data_flow['sources']:
            lines.append(f"  └─ {source}")
        lines.append("")

    # 处理过程
    if data_flow.get('processing_steps'):
        lines.append("⚙️  处理过程:")
        for i, step in enumerate(data_flow['processing_steps'], 1):
            lines.append(f"  {i}. {step}")
        lines.append("")

    # 数据目标
    if data_flow.get('targets'):
        lines.append("📤 数据目标:")
        for target in data_flow['targets']:
            lines.append(f"  └─ {target}")
        lines.append("")

    # 过滤条件
    if data_flow.get('filters'):
        lines.append("🔍 过滤条件:")
        for f in data_flow['filters']:
            lines.append(f"  └─ {f}")
        lines.append("")

    return '\n'.join(lines)


def generate_business_flow_diagram(business_logic: Dict[str, Any]) -> str:
    """
    生成业务流程图

    Args:
        business_logic: 业务逻辑信息

    Returns:
        文本流程图字符串
    """
    lines = []
    lines.append("┌─────────────────────────────────────────┐")
    lines.append("│           业务流程图                    │")
    lines.append("└─────────────────────────────────────────┘")
    lines.append("")

    # 入口方法
    if business_logic.get('entry_points'):
        lines.append("🚀 入口方法:")
        for entry in business_logic['entry_points']:
            lines.append(f"  └─ {entry}")
        lines.append("")

    # 关键决策点
    if business_logic.get('key_decision_points'):
        lines.append("🔀 关键决策点:")
        for i, point in enumerate(business_logic['key_decision_points'], 1):
            lines.append(f"  {i}. {point['description']}")
        lines.append("")

    # 数据流转
    if business_logic.get('data_flows'):
        lines.append("🔄 数据流转:")
        for i, flow in enumerate(business_logic['data_flows'], 1):
            lines.append(f"  {i}. {flow['description']}")
        lines.append("")

    # 异常处理
    if business_logic.get('exception_handling'):
        lines.append("⚠️  异常处理:")
        for handling in business_logic['exception_handling']:
            lines.append(f"  └─ {handling}")
        lines.append("")

    return '\n'.join(lines)


def generate_test_flow_diagram(test_cases: List[Dict[str, Any]]) -> str:
    """
    生成测试流程图

    Args:
        test_cases: 测试用例列表

    Returns:
        文本流程图字符串
    """
    lines = []
    lines.append("┌─────────────────────────────────────────┐")
    lines.append("│           测试流程图                    │")
    lines.append("└─────────────────────────────────────────┘")
    lines.append("")

    for i, tc in enumerate(test_cases[:10], 1):  # 最多显示10个
        lines.append(f"📝 测试用例 {i}: {tc['name']}")
        lines.append(f"  优先级: {tc.get('priority', 'P2')}")
        lines.append(f"  前置条件: {tc.get('precondition', '无')}")

        if tc.get('steps'):
            lines.append("  步骤:")
            for j, step in enumerate(tc['steps'], 1):
                lines.append(f"    {j}. {step[:80]}")

        lines.append("")

    return '\n'.join(lines)


def _unique(values: List[str]) -> List[str]:
    return list(dict.fromkeys(value for value in values if value not in (None, "")))


def _normalize_sql_analysis(sql_analysis: Any) -> Dict[str, List[str]]:
    """Aggregate canonical list entries while accepting the legacy dict shape."""
    if isinstance(sql_analysis, dict):
        entries = [sql_analysis]
    elif isinstance(sql_analysis, list):
        entries = sql_analysis
    else:
        raise TypeError(
            "sql_analysis 必须是分析条目列表或旧版字典，"
            f"实际类型: {type(sql_analysis).__name__}"
        )

    sources: List[str] = []
    targets: List[str] = []
    processing_steps: List[str] = []
    filters: List[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise TypeError(
                f"sql_analysis[{index}] 必须是对象，实际类型: {type(entry).__name__}"
            )
        analysis = entry.get("analysis", entry)
        if not isinstance(analysis, dict):
            raise TypeError(
                f"sql_analysis[{index}].analysis 必须是对象，"
                f"实际类型: {type(analysis).__name__}"
            )
        tables = analysis.get("tables") or {}
        if not isinstance(tables, dict):
            raise TypeError(f"sql_analysis[{index}].analysis.tables 必须是对象")
        sources.extend(str(value) for value in tables.get("sources", []) or [])
        targets.extend(str(value) for value in tables.get("targets", []) or [])
        for aggregation in analysis.get("aggregations", []) or []:
            if not isinstance(aggregation, dict):
                continue
            function = aggregation.get("function")
            column = aggregation.get("column")
            if function and column:
                processing_steps.append(f"{function}({column})")
        for condition in analysis.get("filters", []) or []:
            if isinstance(condition, dict) and condition.get("condition"):
                filters.append(str(condition["condition"]))
            elif isinstance(condition, str) and condition.strip():
                filters.append(condition.strip())

    return {
        "sources": _unique(sources),
        "targets": _unique(targets),
        "processing_steps": _unique(processing_steps),
        "filters": _unique(filters),
    }

def generate_complete_flow_diagram(all_analysis: Dict[str, Any]) -> str:
    """
    生成完整的业务流程图（综合所有分析结果）

    Args:
        all_analysis: 所有分析结果的字典

    Returns:
        完整文本流程图字符串
    """
    lines = []

    # 标题
    lines.append("=" * 60)
    lines.append("📊 完整业务流程图")
    lines.append("=" * 60)
    lines.append("")

    # 1. 数据流转图
    sql_analysis = all_analysis.get('sql_analysis')
    if sql_analysis:
        lines.append(generate_data_flow_diagram(_normalize_sql_analysis(sql_analysis)))
        lines.append("")

    # 2. 业务流程图
    if all_analysis.get('business_rules'):
        lines.append(generate_business_flow_diagram({
            'entry_points': all_analysis.get('entry_points', []),
            'key_decision_points': all_analysis['business_rules'].get('key_decision_points', []),
            'data_flows': all_analysis.get('data_flows', []),
            'exception_handling': [f"catch({e['catch_type']})" for e in all_analysis['business_rules'].get('exception_rules', [])],
        }))
        lines.append("")

    # 3. 配置信息
    if all_analysis.get('config'):
        lines.append("⚙️  配置信息:")
        if all_analysis['config'].get('value_annotations'):
            lines.append("  @Value配置:")
            for config in all_analysis['config']['value_annotations'][:5]:
                lines.append(f"    └─ {config['config_key']} = {config['default_value']}")
        lines.append("")

    # 4. 定时任务
    if all_analysis.get('scheduled_tasks'):
        lines.append("⏰ 定时任务:")
        for task in all_analysis['scheduled_tasks']:
            lines.append(f"  └─ {task.get('name', 'unknown')}")
        lines.append("")

    # 5. 业务规则总结
    if all_analysis.get('business_rules'):
        lines.append("📋 业务规则总结:")
        lines.append(f"  {all_analysis['business_rules'].get('business_rules_summary', '无')}")
        lines.append("")

    # 6. 关键测试点
    if all_analysis.get('test_cases'):
        lines.append("🧪 关键测试点:")
        for i, tc in enumerate(all_analysis['test_cases'][:5], 1):
            lines.append(f"  {i}. [{tc.get('priority', 'P2')}] {tc['name'][:60]}")
        lines.append("")

    lines.append("=" * 60)

    return '\n'.join(lines)


def generate_flow_diagram(analysis_type: str, analysis_result: Dict[str, Any]) -> str:
    """
    根据分析类型生成对应的流程图

    Args:
        analysis_type: 分析类型 ('data_flow', 'business_flow', 'test_flow', 'complete')
        analysis_result: 分析结果

    Returns:
        文本流程图字符串
    """
    if analysis_type == 'data_flow':
        return generate_data_flow_diagram(analysis_result)
    elif analysis_type == 'business_flow':
        return generate_business_flow_diagram(analysis_result)
    elif analysis_type == 'test_flow':
        return generate_test_flow_diagram(analysis_result.get('test_cases', []))
    elif analysis_type == 'complete':
        return generate_complete_flow_diagram(analysis_result)
    else:
        return f"未知分析类型: {analysis_type}"


if __name__ == '__main__':
    import sys
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    # 示例用法
    all_analysis = {
        'sql_analysis': {
            'tables': {
                'sources': ['t_front_income_bill_today', 't_front_cost_bill_today'],
                'targets': ['t_sys_daily_financial'],
            },
            'aggregations': [
                {'function': 'SUM', 'column': 'cost'},
                {'function': 'COUNT', 'column': '*'},
            ],
            'filters': [
                {'condition': 'use_flag <> 10'},
                {'condition': 'is_history = 0'},
            ],
        },
        'business_rules': {
            'key_decision_points': [
                {'description': '第25行: if(use_flag != 10 && is_history == 0)'},
                {'description': '第30行: if(countPay > 300000)'},
            ],
            'exception_rules': [
                {'catch_type': 'SQLException'},
            ],
            'business_rules_summary': '发现 3 个if/else业务判断\n发现 1 个异常处理块',
        },
        'entry_points': ['getReportData', 'getReportDataToday'],
        'data_flows': [
            {'description': '从 starrocksMapper 获取数据赋值给 list'},
            {'description': '将 dto 传递给 mapper.insert'},
        ],
        'scheduled_tasks': [
            {'name': 'getReportData'},
            {'name': 'getReportDataToday'},
        ],
        'config': {
            'value_annotations': [
                {'config_key': 'capitalconf.financial.maxCount', 'default_value': '300000'},
            ],
        },
        'test_cases': [
            {'name': '数据源验证测试', 'priority': 'P1', 'precondition': '源表有数据', 'steps': ['验证读取数据']},
            {'name': '过滤条件测试', 'priority': 'P1', 'precondition': '准备测试数据', 'steps': ['验证WHERE条件']},
        ],
    }

    diagram = generate_complete_flow_diagram(all_analysis)
    print(diagram)
