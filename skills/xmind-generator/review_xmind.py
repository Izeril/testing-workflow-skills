#!/usr/bin/env python3
"""
评审版XMind生成器 - 支持自动加载项目专用规则库
自动识别项目类型，新项目自动学习规则
"""
import os
import sys
import json
import re
import zipfile
import uuid
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from datetime import datetime
from collections import Counter

from xmind_utils import (
    gen_id,
    release_reserved_path,
    reserve_versioned_path,
    write_xmind_zip,
)

# 规则配置文件路径
RULES_CONFIG_PATH = Path(__file__).parent / 'rules_config.json'
_RULES_CONFIG_OVERRIDE = ContextVar('xmind_rules_config_path', default=None)


def _active_rules_config_path():
    override = _RULES_CONFIG_OVERRIDE.get()
    return Path(override) if override else RULES_CONFIG_PATH.with_name('rules_config.local.json')


@contextmanager
def rules_config_scope(output_dir):
    """Keep learned project rules inside this output instead of the shared skill."""
    scoped_path = Path(output_dir) / '.ai-internal' / 'xmind_rules_config.json'
    token = _RULES_CONFIG_OVERRIDE.set(scoped_path)
    try:
        yield scoped_path
    finally:
        _RULES_CONFIG_OVERRIDE.reset(token)

@contextmanager
def rules_config_write_lock(timeout_seconds=10.0):
    """Serialize read-modify-write updates for one project's learned rules."""
    target = _active_rules_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(f'.{target.name}.lock')
    deadline = time.monotonic() + timeout_seconds
    descriptor = None
    while descriptor is None:
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError(f'等待 XMind 规则锁超时: {lock_path}')
            time.sleep(0.02)
    os.close(descriptor)
    try:
        yield target
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass

# 内置规则模板库 - 用于自动学习
RULE_TEMPLATES = {
    'startup': {
        'pattern': '能否正常启动|能否正常执行|能否正常运行|任务.*启动',
        'steps_template': '1.执行{action}\n2.观察启动状态和日志\n3.验证执行状态',
        'expected_template': '{action}正常启动，状态为RUNNING'
    },
    'logging': {
        'pattern': '日志.*正确|日志.*完整|日志记录',
        'steps_template': '1.执行{action}\n2.查询日志表/文件\n3.检查日志字段完整性',
        'expected_template': '日志记录完整，关键字段正确'
    },
    'data_integrity': {
        'pattern': '数据完整性|数据.*完整|数据总量|数据一致性',
        'steps_template': '1.查询{table}数据总量\n2.对比源数据\n3.验证数据一致性',
        'expected_template': '{table}数据总量一致，无缺失'
    },
    'field_mapping': {
        'pattern': '字段映射|字段.*对应|字段正确性',
        'steps_template': '1.查看配置中字段映射规则\n2.查询目标表结构\n3.逐一验证字段对应关系',
        'expected_template': '字段映射正确，类型匹配'
    },
    'exception': {
        'pattern': '异常.*处理|错误.*处理|失败.*处理',
        'steps_template': '1.模拟{service}异常场景\n2.观察系统响应\n3.检查错误记录',
        'expected_template': '{service}异常正确处理，日志记录完整'
    },
    'recovery': {
        'pattern': '恢复.*能力|断点续传|中断.*继续',
        'steps_template': '1.模拟中断场景\n2.恢复后观察续传情况\n3.验证数据完整性',
        'expected_template': '恢复后自动续传，数据完整'
    },
    'api': {
        'pattern': '接口.*调用|接口.*返回|接口.*正常',
        'steps_template': '1.调用接口\n2.检查返回状态和数据\n3.验证返回格式',
        'expected_template': '接口正常响应，返回数据正确'
    },
    'performance': {
        'pattern': '性能.*符合|执行时间|响应时间|资源占用',
        'steps_template': '1.执行操作并记录时间\n2.监控资源使用情况\n3.对比性能阈值',
        'expected_template': '性能指标在预期范围内'
    },
    'config': {
        'pattern': '配置.*正确|配置.*生效|配置.*加载',
        'steps_template': '1.查看配置中心配置项\n2.验证配置加载状态\n3.检查配置生效情况',
        'expected_template': '配置正确加载并生效'
    },
    'concurrent': {
        'pattern': '并发.*处理|并发.*能力',
        'steps_template': '1.多线程并发执行操作\n2.观察系统响应\n3.检查数据一致性',
        'expected_template': '并发正确处理，无冲突'
    },
    'ui_display': {
        'pattern': '页面.*展示|页面.*显示|页面.*刷新',
        'steps_template': '1.打开目标页面\n2.检查数据展示\n3.验证显示正确性',
        'expected_template': '页面数据展示正确'
    },
    'full_sync': {
        'pattern': '全量同步.*启动|全量.*同步任务',
        'steps': '1.登录ETL服务管理后台\n2.触发全量同步定时任务\n3.观察任务启动日志',
        'expected': '全量同步任务正常启动，状态为RUNNING'
    },
    'incr_sync': {
        'pattern': '增量同步.*启动|增量.*同步任务',
        'steps': '1.确认已有全量同步数据\n2.触发增量同步定时任务\n3.观察增量同步日志',
        'expected': '增量同步任务正常启动，日志显示sync_type=INCR'
    },
    'sftp_connection': {
        'pattern': 'SFTP.*连接|SFTP.*中断',
        'steps': '1.模拟SFTP连接中断\n2.观察同步任务状态\n3.恢复连接后检查续传',
        'expected': 'SFTP连接中断正确处理，恢复后自动续传'
    },
    'starrocks_query': {
        'pattern': 'StarRocks.*查询|StarRocks.*数据',
        'steps': '1.执行StarRocks查询语句\n2.验证查询结果\n3.检查数据准确性',
        'expected': 'StarRocks查询正常返回数据'
    },
    'mongodb_log': {
        'pattern': 'MongoDB.*日志|MongoDB.*写入',
        'steps': '1.同步过程中监控MongoDB写入\n2.查询日志表记录\n3.验证日志完整性',
        'expected': 'MongoDB日志正确写入，字段完整'
    },
    'apollo_config': {
        'pattern': 'Apollo.*配置|Apollo.*加载',
        'steps': '1.查看Apollo配置中心\n2.检查同步相关配置\n3.验证配置加载状态',
        'expected': 'Apollo配置正确加载，配置项完整'
    },
    'hkb_api': {
        'pattern': '获客宝.*查询|获客宝.*接口',
        'steps': '1.登录获客宝系统\n2.调用企业工商信息查询接口\n3.检查返回状态',
        'expected': '获客宝接口正常响应，数据完整'
    },
    'data_diff': {
        'pattern': '差异率|数据.*对比|新旧.*对比',
        'steps': '1.对比新旧数据源数据\n2.计算差异率\n3.验证差异符合预期',
        'expected': '数据差异率符合预期，关键字段一致'
    },
}

def load_rules_config():
    """Load project state, or the generic baseline with workstation overrides."""
    active_path = _active_rules_config_path()
    if _RULES_CONFIG_OVERRIDE.get() is not None and active_path.exists():
        with open(active_path, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    config = {'common_templates': {}, 'project_rules': {}, 'project_detection': {}}
    if RULES_CONFIG_PATH.exists():
        with open(RULES_CONFIG_PATH, 'r', encoding='utf-8') as handle:
            config.update(json.load(handle))
    local_path = RULES_CONFIG_PATH.with_name('rules_config.local.json')
    if local_path.exists():
        with open(local_path, 'r', encoding='utf-8') as handle:
            config.update(json.load(handle))
    return config


def save_rules_config(config):
    """Atomically save rules to the active project scope."""
    target = _active_rules_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f'.{target.name}.{uuid.uuid4().hex[:10]}.tmp')
    try:
        with open(staging, 'w', encoding='utf-8') as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
        os.replace(staging, target)
    finally:
        if staging.exists():
            staging.unlink()

def analyze_test_points_for_rules(md_content):
    """分析测试点文档，识别规则分布"""
    rule_counts = Counter()

    lines = md_content.split('\n')
    for line in lines:
        match = re.match(r'^- \d+\.\d+\.\d+ (.+)$', line.strip())
        if match:
            title = match.group(1)
            for rule_name, template in RULE_TEMPLATES.items():
                pattern = template.get('pattern', '')
                if pattern and re.search(pattern, title):
                    rule_counts[rule_name] += 1
                    break

    return rule_counts

def _auto_learn_rules_unlocked(md_content, project_type):
    """
    自动学习新规则 - 新项目时自动运行
    """
    print(f"检测到新项目类型: {project_type}，自动学习规则...")

    # 分析规则分布
    rule_counts = analyze_test_points_for_rules(md_content)
    print(f"识别到 {len(rule_counts)} 种规则类型")

    # 加载现有配置
    config = load_rules_config()
    project_rules = config.get('project_rules', {})
    existing_rules = project_rules.get(project_type, {})

    # 添加新规则
    added_count = 0
    for rule_name, count in rule_counts.items():
        if count >= 2:  # 至少出现2次才添加
            if rule_name not in existing_rules:
                template = RULE_TEMPLATES.get(rule_name, {})
                if template:
                    existing_rules[rule_name] = {
                        'pattern': template.get('pattern', ''),
                        'steps': template.get('steps', ''),
                        'expected': template.get('expected', '')
                    }
                    added_count += 1

    if added_count > 0:
        print(f"自动学习添加 {added_count} 条新规则")

        # 更新配置
        project_rules[project_type] = existing_rules
        config['project_rules'] = project_rules

        # 添加项目识别规则
        detection = config.get('project_detection', {})
        dir_patterns = detection.get('directory_patterns', {})
        if project_type not in dir_patterns:
            dir_patterns[project_type] = project_type.replace('_', '.*')

        detection['directory_patterns'] = dir_patterns
        config['project_detection'] = detection

        # 保存配置
        save_rules_config(config)
    else:
        print("未发现新规则需要学习")

    return added_count

def auto_learn_rules(md_content, project_type):
    with rules_config_write_lock():
        return _auto_learn_rules_unlocked(md_content, project_type)

def detect_project_type(project_name, md_content=None):
    """
    自动识别项目类型，新项目自动学习规则
    """
    config = load_rules_config()
    detection = config.get('project_detection', {})

    detected_type = '_default'

    # 从目录名识别（已有模式）
    dir_patterns = detection.get('directory_patterns', {})
    for project_type, pattern in dir_patterns.items():
        if re.search(pattern, project_name):
            detected_type = project_type
            break

    # 从文档内容识别（已有关键词）
    if detected_type == '_default' and md_content:
        content_keywords = detection.get('content_keywords', {})
        for project_type, keywords in content_keywords.items():
            match_count = sum(1 for kw in keywords if kw in md_content)
            if match_count >= 2:
                detected_type = project_type
                break

    # 如果仍未识别，从目录名提取项目类型（智能提取）
    if detected_type == '_default' and project_name:
        # 提取项目名称中的关键词作为项目类型
        # 例如：【演示产品20260101】【专项】演示数据同步 -> 演示数据同步
        match = re.search(r'【[^】]*】([^【]+)', project_name)
        if match:
            # 提取核心关键词
            name_part = match.group(1).strip()
            # 提取关键业务词（去掉通用词如"重构"、"专项"等）
            name_part = re.sub(r'重构|专项|优化|升级|迁移|切换', '', name_part)
            name_part = name_part.strip()
            if name_part:
                detected_type = name_part
        elif project_name:
            # 直接使用目录名作为项目类型
            detected_type = project_name

    # 检查是否是新项目（规则库中不存在）
    project_rules = config.get('project_rules', {})
    if detected_type not in project_rules and detected_type != '_default':
        # 新项目，自动学习规则
        auto_learn_rules(md_content, detected_type)

    return detected_type

def extract_variable(title, var_type):
    """从标题提取变量"""
    config = load_rules_config()
    extraction = config.get('variable_extraction', {})
    var_config = extraction.get(var_type, {})
    pattern = var_config.get('pattern', '')

    if pattern:
        match = re.search(pattern, title)
        if match:
            return match.group(1)

    return None

def get_project_rules(project_type):
    """获取项目专用规则库"""
    config = load_rules_config()
    project_rules = config.get('project_rules', {})
    return project_rules.get(project_type, {})

def get_common_template(template_name):
    """
    获取通用规则模板
    """
    config = load_rules_config()
    if not config:
        return None

    templates = config.get('common_templates', {})
    return templates.get(template_name)

def match_rule(title, rules):
    """
    在规则库中匹配测试点
    返回匹配的规则和变量
    """
    for rule_name, rule in rules.items():
        pattern = rule.get('pattern', '')
        if re.search(pattern, title):
            # 处理模板类型的规则
            if 'steps_template' in rule:
                # 提取变量
                table = extract_variable(title, 'table_name')
                service = extract_variable(title, 'service_name')
                action_match = re.search(r'验证(.+?)是否', title)
                action = action_match.group(1) if action_match else '操作'

                steps = rule['steps_template'].replace('{table}', table or '目标表')
                steps = steps.replace('{service}', service or '目标服务')
                steps = steps.replace('{action}', action)

                expected = rule['expected_template'].replace('{table}', table or '目标表')
                expected = expected.replace('{service}', service or '目标服务')
                expected = expected.replace('{action}', action)

                return {'steps': steps, 'expected': expected}

            # 处理固定规则
            if 'steps' in rule and 'expected' in rule:
                return {'steps': rule['steps'], 'expected': rule['expected']}

    return None

def match_common_template(title):
    """
    在通用模板中匹配测试点
    """
    config = load_rules_config()
    if not config:
        return None

    templates = config.get('common_templates', {})
    for template_name, template in templates.items():
        pattern = template.get('pattern', '')
        if re.search(pattern, title):
            # 提取变量
            table = extract_variable(title, 'table_name')
            service = extract_variable(title, 'service_name')
            action_match = re.search(r'验证(.+?)是否', title)
            action = action_match.group(1) if action_match else '操作'

            steps = template['steps_template'].replace('{table}', table or '目标表')
            steps = steps.replace('{service}', service or '目标服务')
            steps = steps.replace('{action}', action)

            expected = template['expected_template'].replace('{table}', table or '目标表')
            expected = expected.replace('{service}', service or '目标服务')
            expected = expected.replace('{action}', action)

            return {'steps': steps, 'expected': expected}

    return None

def infer_test_details_auto(title, project_type, section_context=None):
    """
    自动推断测试点的操作步骤和预期结果
    优先级：项目专用规则 -> 通用模板 -> 默认推断
    """
    # 1. 尝试匹配项目专用规则
    project_rules = get_project_rules(project_type)
    result = match_rule(title, project_rules)
    if result:
        return result['steps'], result['expected']

    # 2. 尝试匹配通用模板
    result = match_common_template(title)
    if result:
        return result['steps'], result['expected']

    # 3. 尝试匹配默认规则
    default_rules = get_project_rules('_default')
    result = match_rule(title, default_rules)
    if result:
        return result['steps'], result['expected']

    # 4. 最终兜底推断
    action_match = re.search(r'验证(.+?)是否', title)
    if action_match:
        action = action_match.group(1)
        return (
            f'1.执行{action}相关操作\n2.观察执行结果\n3.记录观察数据',
            f'{action}符合预期要求'
        )

    return (
        '1.按照测试点描述执行操作\n2.观察系统响应\n3.记录测试结果',
        '测试点验证结果符合预期'
    )

def extract_date_from_path(output_dir):
    r"""
    从需求目录路径提取日期（格式：YYYYMMDD）
    支持模式：
      - <需求工作副本>/测试分析
      - <需求工作副本>/测试分析
    """
    path_str = str(output_dir)

    # 尝试匹配 8 位日期（YYYYMMDD）
    match = re.search(r'(20\d{6})', path_str)
    if match:
        return match.group(1)

    # 兜底：使用当前日期
    return datetime.now().strftime('%Y%m%d')


def parse_kym_info_from_md(md_content, project_name, project_type):
    """Extract KYM fields from the Markdown source instead of falling back to placeholders."""
    labels = [
        '项目名称', '项目类型', '核心变更', '涉及服务', '涉及定时任务',
        '涉及表/存储', '涉及配置', '影响业务'
    ]
    values = {}
    in_kym = False
    for raw_line in md_content.splitlines():
        line = raw_line.strip()
        if line.startswith('## ') and 'KYM' in line:
            in_kym = True
            continue
        if in_kym and line.startswith('## '):
            break
        if not in_kym or not line.startswith('- '):
            continue
        body = line[2:].strip()
        sep = '：' if '：' in body else ':' if ':' in body else None
        if not sep:
            continue
        key, value = body.split(sep, 1)
        key = key.strip()
        value = value.strip()
        if key in labels and value and value not in ('待补充', '无'):
            values[key] = value

    if not values:
        return None

    return [
        f'项目名称：{values.get("项目名称", project_name)}',
        f'项目类型：{values.get("项目类型", project_type)}',
        f'核心变更：{values.get("核心变更", "根据测试点文档覆盖的业务变更生成")}',
        f'影响业务：{values.get("影响业务", "根据测试点文档覆盖的业务范围生成")}',
        *[f'{label}：{values[label]}' for label in labels
          if label in values and label not in ('项目名称', '项目类型', '核心变更', '影响业务')]
    ]
def parse_test_points_from_md(md_content):
    """从Markdown文档解析测试点，包含前置条件、操作步骤、预期结果"""
    test_sections = []
    current_section = None
    current_category = None
    current_test_point = None
    current_field = None  # 'precondition', 'steps', 'expected', 'notes'
    current_content_lines = []

    def save_current_field():
        """保存当前字段内容到当前测试点"""
        nonlocal current_content_lines
        if current_test_point and current_field:
            content = '\n'.join(current_content_lines).strip()
            if content:
                current_test_point[current_field] = content
            current_content_lines = []

    lines = md_content.split('\n')

    for line in lines:
        # 检测新的大类（## 一、xxx）
        section_match = re.match(r'^## [一二三四五六七八九十]+、(.+)$', line.strip())
        if section_match:
            # 保存之前的内容
            save_current_field()
            if current_test_point and current_category:
                current_category['test_points'].append(current_test_point)
                current_test_point = None
            if current_category and current_category.get('test_points'):
                if current_section:
                    current_section['categories'].append(current_category)
            # 旧大类追加到结果列表（避免丢失）
            if current_section:
                test_sections.append(current_section)
            current_section = {
                'title': section_match.group(1),
                'categories': []
            }
            current_category = None
            current_field = None
            continue

        # 检测子类（### 1.1 xxx）
        category_match = re.match(r'^### (\d+\.\d+) (.+)$', line.strip())
        if category_match:
            save_current_field()
            if current_test_point and current_category:
                current_category['test_points'].append(current_test_point)
                current_test_point = None
            # 旧子类追加到当前大类
            if current_category and current_category.get('test_points'):
                if current_section:
                    current_section['categories'].append(current_category)
            current_category = {
                'id': category_match.group(1),
                'title': category_match.group(2),
                'test_points': []
            }
            current_field = None
            continue

        # 检测测试点标题（- 1.1.1 xxx [P1]）
        test_point_match = re.match(r'^- (\d+\.\d+\.\d+) (.+)$', line.strip())
        if test_point_match and current_category:
            save_current_field()
            if current_test_point:
                current_category['test_points'].append(current_test_point)
            current_test_point = {
                'id': test_point_match.group(1),
                'title': test_point_match.group(2)
            }
            current_field = None
            continue

        # 检测字段标题
        if current_test_point:
            if re.match(r'^\s*-\s*前置条件[：:]', line.strip()):
                save_current_field()
                current_field = 'precondition'
                content_after = re.sub(r'^\s*-\s*前置条件[：:]\s*', '', line.strip())
                if content_after:
                    current_content_lines.append(content_after)
                continue
            elif re.match(r'^\s*-\s*操作步骤[：:]', line.strip()):
                save_current_field()
                current_field = 'steps'
                content_after = re.sub(r'^\s*-\s*操作步骤[：:]\s*', '', line.strip())
                if content_after:
                    current_content_lines.append(content_after)
                continue
            elif re.match(r'^\s*-\s*预期结果[：:]', line.strip()):
                save_current_field()
                current_field = 'expected'
                content_after = re.sub(r'^\s*-\s*预期结果[：:]\s*', '', line.strip())
                if content_after:
                    current_content_lines.append(content_after)
                continue
            elif re.match(r'^\s*-\s*笔记[：:]', line.strip()):
                save_current_field()
                current_field = 'notes'
                content_after = re.sub(r'^\s*-\s*笔记[：:]\s*', '', line.strip())
                if content_after:
                    current_content_lines.append(content_after)
                continue

            # 收集字段内容行（跳过空行和分隔符）
            if current_field and line.strip() and not line.strip().startswith('---'):
                current_content_lines.append(
                    line.strip() if current_field == 'notes' else line.rstrip()
                )

    # 保存最后的内容
    save_current_field()
    if current_test_point and current_category:
        current_category['test_points'].append(current_test_point)
    if current_category and current_category.get('test_points'):
        if current_section:
            current_section['categories'].append(current_category)
    if current_section:
        test_sections.append(current_section)

    return test_sections


def parse_pending_issues_from_md(md_content):
    """Extract real unresolved review items from the Markdown source."""
    pending = []
    in_pending = False
    for raw_line in md_content.splitlines():
        line = raw_line.strip()
        if line == '## 待确认问题':
            in_pending = True
            continue
        if in_pending and line.startswith('## '):
            break
        if in_pending and re.match(r'^[-*+]\s+', line):
            item = re.sub(r'^[-*+]\s+', '', line).strip()
            if item and item != '无':
                pending.append(item)
    return pending

def create_test_point_node(title, precondition_text='', steps_text='', expected_text='', notes_text=''):
    """Create a lightweight review test point node.

    The review XMind only carries coverage intent. Preconditions, steps and
    expected results are generated when the importable testcase XMind is built.
    """
    markers = []
    clean_title = title

    # Convert title priority marks: [P1] -> priority-1, [P2] -> priority-2.
    p1_match = re.search(r'\[P1\]', title)
    p2_match = re.search(r'\[P2\]', title)
    if p1_match:
        markers.append({'markerId': 'priority-1'})
        clean_title = title.replace('[P1]', '').strip()
    elif p2_match:
        markers.append({'markerId': 'priority-2'})
        clean_title = title.replace('[P2]', '').strip()

    node = {
        'id': gen_id(),
        'class': 'attached topic',
        'title': clean_title,
    }

    if markers:
        node['markers'] = markers
    if notes_text.strip():
        node['notes'] = {
            'plain': {
                'content': notes_text.strip(),
            }
        }

    return node

def build_xmind_structure(test_sections, project_name, project_type, kym_info=None, pending_issues=None, date_str=None, company='测试组织'):
    """构建XMind JSON结构"""
    # 如果未提供日期，使用当前日期
    if date_str is None:
        date_str = datetime.now().strftime('%Y%m%d')

    kym_children = []
    if kym_info:
        for item in kym_info:
            kym_children.append({'id': gen_id(), 'class': 'attached topic', 'title': item})
    else:
        # 根据项目类型自动生成合理的KYM信息
        kym_mapping = load_rules_config().get("kym_mapping", {})

        # 提取纯净项目名（去掉日期目录前缀）
        clean_project_name = project_name
        clean_match = re.search(r'[【\[].*?[】\]]\s*(.*)', project_name)
        if clean_match:
            clean_project_name = clean_match.group(1).strip()

        # 使用完整名称精确匹配（同时支持带前缀和不带前缀的键）
        matched_info = kym_mapping.get(project_name) or kym_mapping.get(clean_project_name)

        if matched_info:
            kym_children = [
                {'id': gen_id(), 'class': 'attached topic', 'title': f'项目名称：{project_name}'},
                {'id': gen_id(), 'class': 'attached topic', 'title': f'项目类型：{project_type}'},
                {'id': gen_id(), 'class': 'attached topic', 'title': f'核心变更：{matched_info["核心变更"]}'},
                {'id': gen_id(), 'class': 'attached topic', 'title': f'影响业务：{matched_info["影响业务"]}'},
            ]
        else:
            kym_children = [
                {'id': gen_id(), 'class': 'attached topic', 'title': f'项目名称：{project_name}'},
                {'id': gen_id(), 'class': 'attached topic', 'title': f'项目类型：{project_type}'},
                {'id': gen_id(), 'class': 'attached topic', 'title': '核心变更：根据测试点文档覆盖的业务变更生成'},
                {'id': gen_id(), 'class': 'attached topic', 'title': '影响业务：根据测试点文档覆盖的业务范围生成'},
            ]

    test_analysis_children = []
    total_count = 0

    for section in test_sections:
        section_points = []
        for category in section.get('categories', []):
            cat_points = []
            for tp in category.get('test_points', []):
                prec_text = tp.get('precondition', '')
                steps_text = tp.get('steps', '')
                expected_text = tp.get('expected', '')
                notes_text = tp.get('notes', '')
                cat_points.append(
                    create_test_point_node(
                        tp['title'], prec_text, steps_text, expected_text, notes_text
                    )
                )
                total_count += 1

            if cat_points:
                section_points.append({
                    'id': gen_id(),
                    'class': 'attached topic',
                    'title': f'{category["id"]} {category["title"]}',
                    'children': {'attached': cat_points}
                })

        if section_points:
            test_analysis_children.append({
                'id': gen_id(),
                'class': 'attached topic',
                'title': section['title'],
                'children': {'attached': section_points}
            })

    pending_children = []
    if pending_issues:
        for issue in pending_issues:
            pending_children.append({'id': gen_id(), 'class': 'attached topic', 'title': issue})
    else:
        pending_children = [
            {'id': gen_id(), 'class': 'attached topic', 'title': '无'},
        ]

    convention_children = [
        {'id': gen_id(), 'class': 'attached topic', 'title': '🟢 flag-green：评审新增的测试点'},
        {'id': gen_id(), 'class': 'attached topic', 'title': '🔴 flag-red：评审删除的测试点（不纳入执行版）'},
        {'id': gen_id(), 'class': 'attached topic', 'title': '无标记但标题变更：AI自动识别为修改'},
        {'id': gen_id(), 'class': 'attached topic', 'title': '请直接修改标题内容，AI将自动比对'},
    ]

    jdzx_children = [
        {'id': gen_id(), 'class': 'attached topic', 'title': 'KYM', 'children': {'attached': kym_children}},
    ]
    jdzx_children.extend(test_analysis_children)
    jdzx_children.extend([
        {'id': gen_id(), 'class': 'attached topic', 'title': '待确认问题', 'children': {'attached': pending_children}},
        {'id': gen_id(), 'class': 'attached topic', 'title': '📖 评审标记约定（请阅读）', 'children': {'attached': convention_children}},
    ])

    project_children = [
        {'id': gen_id(), 'class': 'attached topic', 'title': company,
         'markers': [{'markerId': 'star-red'}], 'children': {'attached': jdzx_children}},
    ]

    # 从需求目录日期提取年月层级
    day_children = [
        {'id': gen_id(), 'class': 'attached topic', 'title': project_name, 'children': {'attached': project_children}},
    ]
    month_children = [
        {'id': gen_id(), 'class': 'attached topic', 'title': date_str, 'children': {'attached': day_children}},
    ]
    year_month = date_str[:6]
    year_children = [
        {'id': gen_id(), 'class': 'attached topic', 'title': year_month, 'children': {'attached': month_children}},
    ]

    # 根节点年份从 date_str 提取
    root_year = date_str[:4]

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
            'children': {'attached': year_children}
        },
        'title': '评审版'
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

    return content, metadata, manifest, total_count

def find_placeholder_topics(content):
    """Return XMind topic titles that still contain placeholder text."""
    placeholders = ('待补充', '结果待补充', 'TODO', 'xxx', 'XXX')
    hits = []

    def walk(topic):
        title = topic.get('title', '')
        if any(token in title for token in placeholders):
            hits.append(title)
        for child in topic.get('children', {}).get('attached', []):
            walk(child)

    for sheet in content:
        walk(sheet.get('rootTopic', {}))
    return hits

def generate_review_xmind(test_points_path, output_dir=None, kym_info=None, pending_issues=None, company='测试组织', manager='测试负责人'):
    """生成评审版XMind"""
    if not Path(test_points_path).exists():
        print(f"错误: 测试点文件不存在: {test_points_path}")
        return None

    with open(test_points_path, 'r', encoding='utf-8') as f:
        md_content = f.read()

    test_sections = parse_test_points_from_md(md_content)
    if pending_issues is None:
        pending_issues = parse_pending_issues_from_md(md_content)

    if output_dir is None:
        output_dir = Path(test_points_path).parent
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 提取项目名称
    project_name = output_dir.parent.name
    if '测试分析' in project_name or '评审' in project_name:
        project_name = output_dir.parent.parent.name

    # 从需求目录路径提取日期（格式：20260604）
    date_str = extract_date_from_path(output_dir)

    # 自动学习状态按项目输出目录隔离，不再写共享 skill 配置。
    with rules_config_scope(output_dir):
        project_type = detect_project_type(project_name, md_content)
        if kym_info is None:
            kym_info = parse_kym_info_from_md(md_content, project_name, project_type)
        print(f"识别到项目类型: {project_type}")

        content, metadata, manifest, total_count = build_xmind_structure(
            test_sections, project_name, project_type, kym_info, pending_issues, date_str, company
        )
    placeholder_hits = find_placeholder_topics(content)
    if placeholder_hits:
        sample = '\n'.join(f'  - {item}' for item in placeholder_hits[:20])
        raise RuntimeError(f"XMind output still contains placeholder topics:\n{sample}")

    print(f"解析到 {len(test_sections)} 个测试大类，共 {total_count} 个测试点")

    date_str = datetime.now().strftime('%Y%m%d')
    file_path = reserve_versioned_path(
        output_dir, f"{company}_{manager}_评审_{date_str}"
    )
    try:
        write_xmind_zip(file_path, content, metadata, manifest)
    except Exception:
        release_reserved_path(file_path)
        raise

    print(f"评审版XMind生成成功: {file_path}")

    print("终稿将在测试点评审确认后生成")
    return str(file_path), None

def main():
    import argparse
    parser = argparse.ArgumentParser(description='生成评审版XMind')
    parser.add_argument('test_points_path', help='测试点详细分析文档路径')
    parser.add_argument('output_dir', nargs='?', default=None, help='输出目录')
    parser.add_argument('--company', default='测试组织', help='公司名称（XMind节点和文件名）')
    parser.add_argument('--manager', default='测试负责人', help='责任人（文件名）')
    args = parser.parse_args()
    generate_review_xmind(args.test_points_path, args.output_dir, company=args.company, manager=args.manager)

if __name__ == "__main__":
    import sys
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    main()
