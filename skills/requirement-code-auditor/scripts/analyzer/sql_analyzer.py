#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
SQL深度分析器
从SQL中提取完整的数据流转、过滤条件、聚合逻辑等业务信息

使用方法:
    from sql_analyzer import analyze_sql_content

    result = analyze_sql_content(sql_content)
"""

import re
from typing import Dict, List, Any, Set


def normalize_sql(sql: str) -> str:
    """标准化SQL（去除多余空格、换行等）"""
    # 移除注释
    sql = re.sub(r'--.*?$', '', sql, flags=re.MULTILINE)
    sql = re.sub(r'/\*.*?\*/', '', sql, flags=re.DOTALL)
    # 合并多余空格
    sql = re.sub(r'\s+', ' ', sql).strip()
    return sql



def split_sql_statements(sql_content: str) -> List[str]:
    """Split plain SQL or MyBatis XML into independent SQL bodies.

    MyBatis element names such as ``<update id=...>`` must never be parsed as
    SQL keywords. Analyze each statement body independently so WHERE/ORDER
    clauses cannot leak across neighboring mapper statements.
    """
    mapper_blocks = re.findall(
        r'<(select|insert|update|delete)\b[^>]*>(.*?)</\1\s*>',
        sql_content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if mapper_blocks:
        statements = []
        for _, body in mapper_blocks:
            body = re.sub(r'<!\[CDATA\[|\]\]>', ' ', body, flags=re.IGNORECASE)
            body = re.sub(r'<[^>]+>', ' ', body)
            normalized = body.strip()
            if normalized:
                statements.append(normalized)
        return statements
    return [part for part in re.split(r';\s*', sql_content) if part.strip()]

def extract_tables_from_sql(sql: str) -> Dict[str, Any]:
    """
    从SQL中提取表名及使用方式

    Returns:
        {
            'sources': ['FROM/JOIN的表'],
            'targets': ['INSERT/UPDATE的表'],
            'joins': [{'left': '表A', 'right': '表B', 'condition': 'A.id=B.id'}],
            'aliases': {'别名': '表名'}
        }
    """
    result = {
        'sources': [],
        'targets': [],
        'joins': [],
        'aliases': {},
    }

    sql_normalized = normalize_sql(sql)
    sql_upper = sql_normalized.upper()

    # 提取INSERT INTO 表名
    insert_matches = re.findall(r'INSERT\s+INTO\s+(\w+)', sql_normalized, re.IGNORECASE)
    result['targets'].extend(insert_matches)

    # 提取UPDATE 表名
    update_matches = re.findall(r'UPDATE\s+(\w+)', sql_normalized, re.IGNORECASE)
    result['targets'].extend(update_matches)

    # 提取DELETE FROM 表名
    delete_matches = re.findall(r'DELETE\s+FROM\s+(\w+)', sql_normalized, re.IGNORECASE)
    result['targets'].extend(delete_matches)

    # 提取FROM 表名 [AS] 别名
    from_matches = re.findall(r'\bFROM\s+(\w+)(?:\s+(?:AS\s+)?(\w+))?', sql_normalized, re.IGNORECASE)
    for match in from_matches:
        table_name = match[0]
        alias = match[1] if match[1] else table_name
        if table_name.upper() not in ('SELECT', 'WHERE', 'SET', 'VALUES'):
            result['sources'].append(table_name)
            result['aliases'][alias] = table_name

    # 提取JOIN 表名 [AS] 别名 ON 条件
    join_matches = re.findall(
        r'(?:LEFT\s+|RIGHT\s+|INNER\s+|OUTER\s+)?JOIN\s+(\w+)(?:\s+(?:AS\s+)?(\w+))?\s+ON\s+([^\s]+(?:\s*=\s*[^\s]+)*)',
        sql_normalized,
        re.IGNORECASE
    )
    for match in join_matches:
        table_name = match[0]
        alias = match[1] if match[1] else table_name
        condition = match[2]
        result['sources'].append(table_name)
        result['aliases'][alias] = table_name

        # 提取JOIN关系
        # 尝试解析ON条件中的表关系
        join_relation = {
            'table': table_name,
            'alias': alias,
            'condition': condition,
        }
        result['joins'].append(join_relation)

    # 去重
    result['sources'] = list(set(result['sources']))
    result['targets'] = list(set(result['targets']))

    return result


def extract_filter_conditions(sql: str) -> List[Dict[str, Any]]:
    """
    从SQL中提取过滤条件（WHERE/HAVING）

    Returns:
        [
            {'condition': 'use_flag <> 10', 'type': 'WHERE'},
            {'condition': 'COUNT(*) > 1', 'type': 'HAVING'}
        ]
    """
    conditions = []
    sql_normalized = normalize_sql(sql)

    # 提取WHERE条件
    where_match = re.search(r'\bWHERE\s+(.*?)(?:\bGROUP\b|\bORDER\b|\bLIMIT\b|\bHAVING\b|$)', sql_normalized, re.IGNORECASE | re.DOTALL)
    if where_match:
        where_clause = where_match.group(1).strip()
        # 拆分AND/OR条件
        individual_conditions = re.split(r'\s+AND\s+|\s+OR\s+', where_clause, flags=re.IGNORECASE)
        for cond in individual_conditions:
            cond = cond.strip()
            if cond:
                conditions.append({
                    'condition': cond,
                    'type': 'WHERE',
                })

    # 提取HAVING条件
    having_match = re.search(r'\bHAVING\s+(.*?)(?:\bORDER\b|\bLIMIT\b|$)', sql_normalized, re.IGNORECASE | re.DOTALL)
    if having_match:
        having_clause = having_match.group(1).strip()
        conditions.append({
            'condition': having_clause,
            'type': 'HAVING',
        })

    return conditions


def extract_aggregations(sql: str) -> List[Dict[str, Any]]:
    """
    从SQL中提取聚合函数

    Returns:
        [
            {'function': 'SUM', 'column': 'cost', 'alias': 'total_cost'},
            {'function': 'COUNT', 'column': '*', 'alias': 'record_count'}
        ]
    """
    aggregations = []
    sql_normalized = normalize_sql(sql)

    # 匹配聚合函数
    agg_pattern = r'\b(SUM|COUNT|AVG|MAX|MIN)\s*\(\s*(DISTINCT\s+)?(\*|\w+(?:\.\w+)?)\s*\)(?:\s+AS\s+(\w+))?'
    matches = re.findall(agg_pattern, sql_normalized, re.IGNORECASE)

    for match in matches:
        func_name = match[0]
        is_distinct = bool(match[1])
        column = match[2]
        alias = match[3] if match[3] else f'{func_name.lower()}_{column.replace(".", "_")}'

        aggregations.append({
            'function': func_name.upper(),
            'column': column,
            'is_distinct': is_distinct,
            'alias': alias,
        })

    return aggregations


def extract_group_by(sql: str) -> List[str]:
    """提取GROUP BY字段"""
    sql_normalized = normalize_sql(sql)

    group_match = re.search(r'\bGROUP\s+BY\s+(.*?)(?:\bHAVING\b|\bORDER\b|\bLIMIT\b|$)', sql_normalized, re.IGNORECASE | re.DOTALL)
    if group_match:
        group_clause = group_match.group(1).strip()
        # 拆分字段
        fields = [f.strip() for f in group_clause.split(',')]
        return fields

    return []


def extract_order_by(sql: str) -> List[Dict[str, str]]:
    """提取ORDER BY字段及排序方向"""
    sql_normalized = normalize_sql(sql)

    order_match = re.search(r'\bORDER\s+BY\s+(.*?)(?:\bLIMIT\b|$)', sql_normalized, re.IGNORECASE | re.DOTALL)
    if order_match:
        order_clause = order_match.group(1).strip()
        # 解析字段和方向
        orders = []
        for part in order_clause.split(','):
            part = part.strip()
            if 'DESC' in part.upper():
                field = part.upper().replace('DESC', '').strip()
                orders.append({'field': field, 'direction': 'DESC'})
            elif 'ASC' in part.upper():
                field = part.upper().replace('ASC', '').strip()
                orders.append({'field': field, 'direction': 'ASC'})
            else:
                orders.append({'field': part, 'direction': 'ASC'})  # 默认ASC
        return orders

    return []


def extract_select_columns(sql: str) -> List[str]:
    """提取SELECT的字段"""
    sql_normalized = normalize_sql(sql)

    # 匹配SELECT ... FROM
    select_match = re.search(r'\bSELECT\s+(.*?)\s+\bFROM\b', sql_normalized, re.IGNORECASE | re.DOTALL)
    if select_match:
        select_clause = select_match.group(1).strip()
        if select_clause.upper() == 'DISTINCT':
            return ['DISTINCT']

        # 拆分字段（注意不要拆分函数内的逗号）
        columns = []
        depth = 0
        current = ''
        for char in select_clause:
            if char == '(':
                depth += 1
                current += char
            elif char == ')':
                depth -= 1
                current += char
            elif char == ',' and depth == 0:
                columns.append(current.strip())
                current = ''
            else:
                current += char
        if current:
            columns.append(current.strip())

        return columns

    return []


def extract_subqueries(sql: str) -> List[Dict[str, str]]:
    """提取子查询"""
    subqueries = []
    sql_normalized = normalize_sql(sql)

    # 匹配括号中的SELECT
    subquery_pattern = r'\(\s*(SELECT\s+.*?)\s*\)'
    matches = re.findall(subquery_pattern, sql_normalized, re.IGNORECASE | re.DOTALL)

    for i, match in enumerate(matches, 1):
        subqueries.append({
            'index': i,
            'sql': match,
        })

    return subqueries


def analyze_sql_content(sql_content: str) -> Dict[str, Any]:
    """
    深度分析SQL内容，提取完整业务逻辑

    Args:
        sql_content: SQL内容（可以是单个SQL或多个SQL的组合）

    Returns:
        {
            'tables': {'sources': [], 'targets': [], 'joins': [], 'aliases': {}},
            'filters': [],
            'aggregations': [],
            'group_by': [],
            'order_by': [],
            'select_columns': [],
            'subqueries': [],
            'business_rules': [],  # 从WHERE条件推断的业务规则
            'data_flow': '',       # 数据流转描述
        }
    """
    result = {
        'tables': {'sources': [], 'targets': [], 'joins': [], 'aliases': {}},
        'filters': [],
        'aggregations': [],
        'group_by': [],
        'order_by': [],
        'select_columns': [],
        'subqueries': [],
        'business_rules': [],
        'data_flow': '',
    }

    # 分割多个SQL语句
    sql_statements = split_sql_statements(sql_content)

    all_sources = []
    all_targets = []
    all_joins = []
    all_aliases = {}
    all_filters = []
    all_aggregations = []
    all_group_by = []
    all_order_by = []
    all_select_columns = []
    all_subqueries = []
    all_business_rules = []

    for sql in sql_statements:
        sql = sql.strip()
        if not sql or sql.upper().startswith('--'):
            continue

        # 提取表信息
        tables = extract_tables_from_sql(sql)
        all_sources.extend(tables['sources'])
        all_targets.extend(tables['targets'])
        all_joins.extend(tables['joins'])
        all_aliases.update(tables['aliases'])

        # 提取过滤条件
        filters = extract_filter_conditions(sql)
        all_filters.extend(filters)

        # 提取聚合函数
        aggregations = extract_aggregations(sql)
        all_aggregations.extend(aggregations)

        # 提取GROUP BY
        group_by = extract_group_by(sql)
        if group_by:
            all_group_by.extend(group_by)

        # 提取ORDER BY
        order_by = extract_order_by(sql)
        if order_by:
            all_order_by.extend(order_by)

        # 提取SELECT字段
        select_columns = extract_select_columns(sql)
        if select_columns:
            all_select_columns.extend(select_columns)

        # 提取子查询
        subqueries = extract_subqueries(sql)
        all_subqueries.extend(subqueries)

        # 从WHERE条件推断业务规则
        for f in filters:
            if f['type'] == 'WHERE':
                cond = f['condition']
                # 识别常见业务规则模式
                if '<>' in cond or '!=' in cond:
                    all_business_rules.append(f'排除条件: {cond}')
                elif '=' in cond and 'IS NOT NULL' not in cond.upper():
                    all_business_rules.append(f'过滤条件: {cond}')
                elif 'LIKE' in cond.upper():
                    all_business_rules.append(f'模糊匹配: {cond}')
                elif 'IN' in cond.upper():
                    all_business_rules.append(f'枚举过滤: {cond}')
                elif 'BETWEEN' in cond.upper():
                    all_business_rules.append(f'范围过滤: {cond}')

    # 去重
    result['tables']['sources'] = list(set(all_sources))
    result['tables']['targets'] = list(set(all_targets))
    result['tables']['joins'] = all_joins
    result['tables']['aliases'] = all_aliases
    result['filters'] = all_filters
    result['aggregations'] = all_aggregations
    result['group_by'] = list(set(all_group_by))
    result['order_by'] = all_order_by
    result['select_columns'] = list(set(all_select_columns))
    result['subqueries'] = all_subqueries
    result['business_rules'] = list(set(all_business_rules))

    # 生成数据流转描述
    if result['tables']['sources'] and result['tables']['targets']:
        sources_str = ', '.join(result['tables']['sources'])
        targets_str = ', '.join(result['tables']['targets'])
        result['data_flow'] = f'从 {sources_str} 读取数据，处理后写入 {targets_str}'
    elif result['tables']['sources']:
        sources_str = ', '.join(result['tables']['sources'])
        result['data_flow'] = f'从 {sources_str} 读取数据'
    elif result['tables']['targets']:
        targets_str = ', '.join(result['tables']['targets'])
        result['data_flow'] = f'写入 {targets_str}'

    return result


def generate_sql_test_cases(sql_analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    基于SQL分析结果生成测试用例

    Args:
        sql_analysis: analyze_sql_content返回的结果

    Returns:
        测试用例列表
    """
    test_cases = []

    # 1. 数据源验证测试
    if sql_analysis['tables']['sources']:
        test_cases.append({
            'name': '数据源验证测试',
            'precondition': f'源表 {", ".join(sql_analysis["tables"]["sources"])} 中有测试数据',
            'steps': [
                f'验证从源表 {", ".join(sql_analysis["tables"]["sources"][:2])} 正确读取数据',
                '验证读取的数据量符合预期',
                '验证源数据格式正确',
            ],
            'priority': 'P1',
        })

    # 2. 过滤条件测试
    if sql_analysis['filters']:
        test_cases.append({
            'name': '过滤条件测试',
            'precondition': '准备包含各种条件的测试数据',
            'steps': [
                f'验证WHERE条件: {f["condition"]} → 正确过滤数据'
                for f in sql_analysis['filters'][:3]
            ],
            'priority': 'P1',
        })

    # 3. 聚合逻辑测试
    if sql_analysis['aggregations']:
        test_cases.append({
            'name': '聚合逻辑测试',
            'precondition': '准备包含多条测试数据',
            'steps': [
                f'验证 {agg["function"]}({agg["column"]}) → 计算结果正确'
                for agg in sql_analysis['aggregations'][:3]
            ],
            'priority': 'P1',
        })

    # 4. 分组排序测试
    if sql_analysis['group_by'] or sql_analysis['order_by']:
        steps = []
        if sql_analysis['group_by']:
            steps.append(f'验证按 {", ".join(sql_analysis["group_by"])} 分组 → 分组正确')
        if sql_analysis['order_by']:
            order_str = ', '.join([f'{o["field"]} {o["direction"]}' for o in sql_analysis['order_by'][:2]])
            steps.append(f'验证按 {order_str} 排序 → 排序正确')

        test_cases.append({
            'name': '分组排序测试',
            'precondition': '准备多条不同分组的数据',
            'steps': steps,
            'priority': 'P2',
        })

    # 5. 数据目标验证测试
    if sql_analysis['tables']['targets']:
        test_cases.append({
            'name': '数据目标验证测试',
            'precondition': 'SQL执行完成',
            'steps': [
                f'验证数据正确写入 {", ".join(sql_analysis["tables"]["targets"][:2])}',
                '验证写入的数据量符合预期',
                '验证写入的数据格式正确',
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
    sql_example = '''
    SELECT
        product_id,
        SUM(cost) as total_cost,
        COUNT(*) as record_count
    FROM t_front_income_bill_today income
    LEFT JOIN t_front_cost_bill_today cost ON income.pay_id = cost.pay_id
    WHERE use_flag <> 10
      AND is_history = 0
      AND created_time >= '2026-04-01'
    GROUP BY product_id
    ORDER BY total_cost DESC
    '''

    analysis = analyze_sql_content(sql_example)

    print(f"数据流转: {analysis['data_flow']}")
    print(f"源表: {analysis['tables']['sources']}")
    print(f"目标表: {analysis['tables']['targets']}")
    print(f"过滤条件: {len(analysis['filters'])}个")
    for f in analysis['filters']:
        print(f"  - {f['type']}: {f['condition']}")
    print(f"聚合函数: {len(analysis['aggregations'])}个")
    for agg in analysis['aggregations']:
        print(f"  - {agg['function']}({agg['column']}) AS {agg['alias']}")
    print(f"GROUP BY: {analysis['group_by']}")
    print(f"ORDER BY: {analysis['order_by']}")
    print(f"业务规则: {len(analysis['business_rules'])}个")
    for rule in analysis['business_rules']:
        print(f"  - {rule}")

    print(f"\n生成测试用例:")
    test_cases = generate_sql_test_cases(analysis)
    for tc in test_cases:
        print(f"  - {tc['name']} (优先级: {tc['priority']})")
