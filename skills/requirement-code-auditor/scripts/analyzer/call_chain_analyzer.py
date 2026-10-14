#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
调用链分析器
从Java代码中提取方法调用关系，理解数据流转路径

使用方法:
    from call_chain_analyzer import analyze_call_chain

    result = analyze_call_chain(java_content)
"""

import re
from typing import Dict, List, Any, Set, Tuple


def extract_method_definitions(content: str) -> List[Dict[str, Any]]:
    """
    提取方法定义

    Returns:
        [
            {
                'name': 'methodName',
                'return_type': 'void',
                'parameters': ['String param1', 'int param2'],
                'line_start': 10,
                'line_end': 50,
                'access_modifier': 'public',
                'annotations': ['@Override', '@Transactional']
            }
        ]
    """
    methods = []
    lines = content.split('\n')

    # 匹配方法定义的正则
    method_pattern = re.compile(
        r'(?:(public|private|protected)\s+)?'  # 访问修饰符
        r'(?:static\s+)?'
        r'(?:final\s+)?'
        r'(?:synchronized\s+)?'
        r'([\w<>\[\],\s]+?)\s+'  # 返回类型
        r'(\w+)\s*'  # 方法名
        r'\(([^)]*)\)'  # 参数
        r'(?:\s+throws\s+[\w,\s]+)?'  # 异常声明
        r'\s*\{',  # 方法体开始
        re.MULTILINE
    )

    # 提取注解
    annotation_pattern = re.compile(r'@(\w+)(?:\([^)]*\))?')

    for i, line in enumerate(lines):
        match = method_pattern.search(line)
        if match:
            access = match.group(1) or 'default'
            return_type = match.group(2).strip()
            name = match.group(3)
            params = [p.strip() for p in match.group(4).split(',') if p.strip()]

            # 查找方法前的注解（最多5行）
            annotations = []
            for j in range(max(0, i-5), i):
                ann_matches = annotation_pattern.findall(lines[j])
                annotations.extend(ann_matches)

            methods.append({
                'name': name,
                'return_type': return_type,
                'parameters': params,
                'line_start': i,
                'access_modifier': access,
                'annotations': annotations,
                'calls': [],  # 后续填充
            })

    return methods


def extract_method_calls(content: str, method_name: str = None) -> List[Dict[str, Any]]:
    """
    提取方法调用

    Args:
        content: Java代码内容
        method_name: 如果指定，只提取该方法内的调用

    Returns:
        [
            {
                'caller': 'callingMethod',
                'callee': 'calledMethod',
                'line': 25,
                'context': '调用前后的代码'
            }
        ]
    """
    calls = []
    lines = content.split('\n')

    # 匹配方法调用：object.method() 或 method()
    call_pattern = re.compile(
        r'(?:(\w+)\.)?(\w+)\s*\(([^)]*)\)'  # object.method(args) 或 method(args)
    )

    # 过滤掉Java关键字和常见非方法调用
    keywords = {'if', 'else', 'for', 'while', 'switch', 'catch', 'return', 'new', 'class', 'interface'}

    for i, line in enumerate(lines):
        # 跳过注释和字符串
        if line.strip().startswith('//') or line.strip().startswith('*'):
            continue

        matches = call_pattern.findall(line)
        for match in matches:
            obj = match[0]
            method = match[1]

            # 过滤关键字和构造函数
            if method.lower() in keywords or method == 'class':
                continue

            calls.append({
                'caller': method_name or 'unknown',
                'callee': method,
                'object': obj,
                'line': i + 1,
                'context': line.strip()[:100],  # 截取前100字符
            })

    return calls


def extract_data_flow(content: str) -> List[Dict[str, Any]]:
    """
    从代码中提取数据流转路径

    Returns:
        [
            {
                'from': 'DAO/Mapper方法',
                'to': 'Service方法',
                'data_type': '数据类型',
                'description': '流转描述'
            }
        ]
    """
    flows = []
    lines = content.split('\n')

    # 识别常见数据流转模式
    patterns = [
        # mapper.selectXxx() → 赋值给变量
        r'(\w+)\s*=\s*(\w+)\.(\w+)\s*\(([^)]*)\)',
        # service.callXxx() → 返回数据
        r'return\s+(\w+)\.(\w+)\s*\(([^)]*)\)',
        # 方法参数传递
        r'(\w+)\.(\w+)\s*\((\w+)\)',
    ]

    for i, line in enumerate(lines):
        for pattern in patterns:
            matches = re.findall(pattern, line)
            for match in matches:
                if len(match) == 4:
                    var_name, obj, method, args = match
                    flows.append({
                        'from': f'{obj}.{method}()',
                        'to': var_name,
                        'data_type': 'unknown',
                        'description': f'从 {obj}.{method}() 获取数据赋值给 {var_name}',
                        'line': i + 1,
                    })
                elif len(match) == 3:
                    obj, method, arg = match
                    flows.append({
                        'from': arg,
                        'to': f'{obj}.{method}()',
                        'data_type': 'unknown',
                        'description': f'将 {arg} 传递给 {obj}.{method}()',
                        'line': i + 1,
                    })

    return flows


def extract_class_relationships(content: str) -> Dict[str, Any]:
    """
    提取类之间的关系（继承、实现、依赖）

    Returns:
        {
            'class_name': 'ClassName',
            'extends': 'ParentClass',
            'implements': ['Interface1', 'Interface2'],
            'dependencies': ['Dependency1', 'Dependency2'],
            'injections': ['@Autowired的依赖']
        }
    """
    result = {
        'class_name': '',
        'extends': None,
        'implements': [],
        'dependencies': [],
        'injections': [],
    }

    # 提取类名
    class_match = re.search(r'(?:public\s+)?class\s+(\w+)', content)
    if class_match:
        result['class_name'] = class_match.group(1)

    # 提取继承
    extends_match = re.search(r'class\s+\w+\s+extends\s+(\w+)', content)
    if extends_match:
        result['extends'] = extends_match.group(1)

    # 提取实现接口
    implements_match = re.search(r'class\s+\w+(?:\s+extends\s+\w+)?\s+implements\s+([\w,\s]+?)(?:\s*\{)', content)
    if implements_match:
        result['implements'] = [i.strip() for i in implements_match.group(1).split(',')]

    # 提取@Autowired注入
    autowired_pattern = re.compile(r'@Autowired\s+(?:private|protected|public)\s+([\w<>]+)\s+(\w+)')
    matches = autowired_pattern.findall(content)
    for match in matches:
        type_name = match[0]
        var_name = match[1]
        result['injections'].append({
            'type': type_name,
            'name': var_name,
        })

    # 提取import依赖
    import_pattern = re.compile(r'import\s+([\w.]+);')
    imports = import_pattern.findall(content)
    result['dependencies'] = imports

    return result


def build_call_graph(content: str) -> Dict[str, Any]:
    """
    构建方法调用图

    Returns:
        {
            'methods': [方法定义列表],
            'calls': [方法调用列表],
            'entry_points': [入口方法（如@XxlJob、@RequestMapping等）],
            'data_flows': [数据流转列表]
        }
    """
    methods = extract_method_definitions(content)
    all_calls = []
    entry_points = []

    # 提取每个方法内的调用
    for method in methods:
        # 提取方法体（简化版：从方法定义到下一个方法定义或类结束）
        start_line = method['line_start']
        next_method_line = methods[methods.index(method) + 1]['line_start'] if methods.index(method) + 1 < len(methods) else len(content.split('\n'))

        method_body = '\n'.join(content.split('\n')[start_line:next_method_line])
        calls = extract_method_calls(method_body, method['name'])
        method['calls'] = calls
        all_calls.extend(calls)

        # 识别入口方法
        if any(ann in method['annotations'] for ann in ['XxlJob', 'RequestMapping', 'GetMapping', 'PostMapping', 'Test']):
            entry_points.append(method['name'])

    data_flows = extract_data_flow(content)

    return {
        'methods': methods,
        'calls': all_calls,
        'entry_points': entry_points,
        'data_flows': data_flows,
    }


def analyze_call_chain(content: str) -> Dict[str, Any]:
    """
    完整分析调用链

    Args:
        content: Java代码内容

    Returns:
        {
            'class_info': 类关系信息,
            'call_graph': 调用图,
            'entry_points': 入口方法,
            'data_flows': 数据流转,
            'business_flow_description': 业务流程描述
        }
    """
    class_info = extract_class_relationships(content)
    call_graph = build_call_graph(content)

    # 生成业务流程描述
    flows = call_graph['data_flows']
    entry_points = call_graph['entry_points']

    business_flow_desc = ''
    if entry_points:
        business_flow_desc += f'入口方法: {", ".join(entry_points)}\n'

    if flows:
        business_flow_desc += '数据流转:\n'
        for flow in flows[:5]:  # 最多显示5个
            business_flow_desc += f'  {flow["description"]}\n'

    return {
        'class_info': class_info,
        'call_graph': call_graph,
        'entry_points': entry_points,
        'data_flows': flows,
        'business_flow_description': business_flow_desc,
    }


def generate_call_chain_tests(call_chain_analysis: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    基于调用链分析生成测试用例

    Args:
        call_chain_analysis: analyze_call_chain返回的结果

    Returns:
        测试用例列表
    """
    test_cases = []

    # 1. 入口方法测试
    if call_chain_analysis['entry_points']:
        for entry in call_chain_analysis['entry_points']:
            test_cases.append({
                'name': f'入口方法测试: {entry}',
                'precondition': '准备测试环境',
                'steps': [
                    f'调用入口方法 {entry}',
                    '验证方法执行成功',
                    '验证返回结果符合预期',
                ],
                'priority': 'P1',
            })

    # 2. 数据流转测试
    if call_chain_analysis['data_flows']:
        test_cases.append({
            'name': '数据流转测试',
            'precondition': '准备测试数据',
            'steps': [
                f'验证 {flow["description"]}'
                for flow in call_chain_analysis['data_flows'][:3]
            ],
            'priority': 'P1',
        })

    # 3. 依赖注入测试
    if call_chain_analysis['class_info']['injections']:
        test_cases.append({
            'name': '依赖注入测试',
            'precondition': 'Spring容器已启动',
            'steps': [
                f'验证 {inj["name"]} ({inj["type"]}) 正确注入'
                for inj in call_chain_analysis['class_info']['injections'][:3]
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

        @Autowired
        private DailyFinancialMapper mapper;

        @Autowired
        private DailyFinancialStarrocksMapper starrocksMapper;

        @XxlJob("getReportData")
        public void getReportData() {
            List<DailyFinancialDto> list = starrocksMapper.getFromStarRocks();
            for (DailyFinancialDto dto : list) {
                mapper.insert(dto);
            }
        }

        public List<DailyFinancialDto> getFinancialList(String date) {
            return mapper.selectByDate(date);
        }
    }
    '''

    analysis = analyze_call_chain(java_example)

    print(f"类名: {analysis['class_info']['class_name']}")
    print(f"入口方法: {analysis['entry_points']}")
    print(f"依赖注入: {len(analysis['class_info']['injections'])}个")
    for inj in analysis['class_info']['injections']:
        print(f"  - {inj['name']} ({inj['type']})")
    print(f"\n数据流转:")
    for flow in analysis['data_flows']:
        print(f"  - {flow['description']}")

    print(f"\n生成测试用例:")
    test_cases = generate_call_chain_tests(analysis)
    for tc in test_cases:
        print(f"  - {tc['name']} (优先级: {tc['priority']})")
