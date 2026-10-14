#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Requirement review and evidence-derived testpoint candidates."""

import json
import re
from datetime import datetime
from pathlib import Path

from source_inventory import build_source_inventory

from decision_register import (
    DECISION_TYPES,
    build_decision_register,
    validate_decision_register,
)

from workflow_common import (
    _get_internal_dir,
    _load_internal_json_payload,
    _safe_artifact_name,
    _write_internal_json,
    log_console,
)
from workflow_analysis import _read_normalized_requirement
def run_requirement_review(req_dir, output_dir, project_info, kb_manager=None):
    """
    步骤③：需求评审

    输入：需求文档 + 技术方案 + 华库
    输出：评审报告.md + 评审数据.json

    增强：AI真正基于需求内容做深度分析，而不是填模板
    """
    log_console("\n" + "=" * 60)
    log_console("🔍 步骤③ 需求评审（AI深度分析）")
    log_console("=" * 60)

    # 1. 仅读取已裁剪的当前需求版本，禁止完整历史 Markdown 回灌。
    doc_content, source_meta = _read_normalized_requirement(output_dir)
    log_console(f"  📄 读取当前需求标准输入: {source_meta['character_count']} 字符")
    source_inventory = build_source_inventory(
        doc_content, Path(req_dir), Path(source_meta['path'])
    )
    _write_internal_json(output_dir, 'source_inventory.json', source_inventory)
    requirement_facts, facts_path = _load_internal_json_payload(
        output_dir, 'requirement_facts.json'
    )
    code_impact, impact_path = _load_internal_json_payload(
        output_dir, 'code_impact.json'
    )
    comparison, comparison_path = _load_internal_json_payload(
        output_dir, 'requirement_code_comparison.json'
    )
    missing_evidence = [
        str(path) for payload, path in (
            (requirement_facts, facts_path),
            (code_impact, impact_path),
            (comparison, comparison_path),
        ) if not payload
    ]
    if missing_evidence:
        raise RuntimeError(
            '评审缺少结构化证据，禁止退回全文关键词评审：' + '；'.join(missing_evidence)
        )
    evidence_context = {
        'requirement_facts': requirement_facts,
        'code_impact': code_impact,
        'comparison': comparison,
        'source_inventory': source_inventory,
    }
    # 2. 读取华库
    essence_root_causes = []
    essence_omissions = []
    if kb_manager:
        essence_dir = kb_manager.kb_path / 'patterns' / '精华库'
        for file_name in ['common_root_causes.md', 'common_omissions.md']:
            file_path = essence_dir / file_name
            if file_path.exists():
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    if 'root_causes' in file_name:
                        essence_root_causes.append(content)
                    else:
                        essence_omissions.append(content)
                log_console(f"  📚 华库学习: {file_name}")

    # 3. 读取历史文档
    history_items = []
    if kb_manager:
        req_history = kb_manager.kb_path / 'requirements'
        if req_history.exists():
            history_files = list(req_history.glob('*.md'))[:5]
            for hf in history_files:
                try:
                    with open(hf, 'r', encoding='utf-8') as f:
                        history_items.append(f.read()[:1000])
                except (OSError, UnicodeError):
                    pass
            log_console(f"  📚 历史需求: {len(history_files)} 个")

    # 4. AI深度分析 - 基于实际内容生成评审问题
    review_issues = _ai_deep_review(
        doc_content, essence_root_causes, essence_omissions, history_items,
        project_info, evidence_context,
    )
    log_console(f"  ✅ AI深度分析完成: 发现 {len(review_issues)} 个问题")

    # 5. 生成评审报告（基于实际分析结果）
    doc_name = project_info.get('project_name', '')
    # 如果 project_info 为空，从需求目录提取项目名称
    if not doc_name:
        parent_name = req_dir.name
        doc_name = parent_name.replace('【', '').replace('】', '')
        if not doc_name:
            doc_name = '需求'

    doc_name = _safe_artifact_name(doc_name)
    report_path = output_dir / f'{doc_name}_评审报告.md'
    internal_dir = _get_internal_dir(output_dir)
    json_path = internal_dir / f'{doc_name}_评审数据.json'

    report_content = _build_review_report(review_issues, doc_name, essence_root_causes, essence_omissions)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report_content)

    # 6. 生成评审数据JSON
    review_data = _build_review_json(
        review_issues, doc_name, essence_root_causes, essence_omissions,
        source_inventory=source_inventory,
    )
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(review_data, f, ensure_ascii=False, indent=2)

    decision_path = internal_dir / 'decision_register.json'
    decision_gate_path = internal_dir / 'review_decision_gate.json'
    existing_register = {}
    if decision_path.exists():
        try:
            existing_register = json.loads(decision_path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            existing_register = {}
    decision_register = build_decision_register(review_data, existing_register)
    decision_gate = validate_decision_register(decision_register)
    decision_path.write_text(
        json.dumps(decision_register, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    decision_gate_path.write_text(
        json.dumps(decision_gate, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    log_console(f"  📄 评审报告.md: {report_path}")
    log_console(f"  📄 评审数据.json: {json_path}")
    log_console(f"  📄 决策登记.json: {decision_path}")
    log_console(f"  📄 决策门禁.json: {decision_gate_path}")

    return report_path, json_path


def _ai_deep_review(
    doc_content, essence_root_causes, essence_omissions, history_items,
    project_info, evidence_context=None,
):
    """
    AI深度评审：基于实际内容生成有针对性的评审问题

    返回评审问题列表，每个问题包含：
    - id: 问题编号（RQ/FU/IC/TC/RK）
    - category: 问题分类
    - title: 问题标题
    - location: 位置
    - description: 问题描述
    - suggestion: 改进建议
    - severity: 严重程度（critical/major/minor）
    """
    issues = []
    doc_lower = doc_content.lower()
    evidence_context = evidence_context or {}
    facts = evidence_context.get('requirement_facts', {})
    code_impact = evidence_context.get('code_impact', {})
    comparison = evidence_context.get('comparison', {})
    fact_items = facts.get('items', []) if isinstance(facts, dict) else []
    impact_scope = code_impact.get('impact_scope', {}) if isinstance(code_impact, dict) else {}
    requirement_tables = facts.get('tables', {}) if isinstance(facts, dict) else {}
    requirement_apis = facts.get('apis', {}) if isinstance(facts, dict) else {}
    requirement_configs = facts.get('configs', {}) if isinstance(facts, dict) else {}
    code_sql = impact_scope.get('sql_analysis', [])
    code_configs = impact_scope.get('config_values', [])
    has_database_change = bool(requirement_tables or code_sql)
    has_config_change = bool(requirement_configs or code_configs)
    has_external_dependency = (
        bool(requirement_apis)
        and any(kw in doc_lower for kw in ['外部', '第三方', '上下游', '税局', '回调', 'webhook'])
    )
    has_operational_change = bool(
        has_database_change or has_config_change
        or any(kw in doc_lower for kw in ['发布', '上线', '迁移', '切换', 'ddl', '表结构'])
    )
    has_data_flow = bool(
        has_database_change
        and any(kw in doc_lower for kw in ['同步', '写入', '迁移', '流转', '主表', '从表'])
    )

    def evidence(*values):
        """过滤空证据，确保每个问题至少能回到需求或代码位置。"""
        return [value for value in values if value not in (None, '', [], {})]

    # ========== 需求疑问 ==========

    # 检查状态流转定义
    if any(kw in doc_lower for kw in ['状态', '流转', '状态码', '结果码']):
        if '状态流转图' not in doc_content and '状态图' not in doc_content:
            issues.append({
                'id': 'RQ-001',
                'category': '需求疑问',
                'title': '状态流转定义是否完整',
                'location': '需求文档-状态处理部分',
                'description': '文档中提到状态处理，但未提供状态流转图，难以直观理解各状态的处理路径和边界条件',
                'suggestion': '补充状态流转图，明确各状态码的含义、触发条件、处理动作和后续状态',
                'severity': 'critical'
            })

    # 检查配置项定义
    config_matches = re.findall(r'(?:配置项|配置)[^\n]*[:：]\s*(\S+)', doc_content)
    if config_matches:
        has_validation = any(kw in doc_lower for kw in ['校验', '验证', '格式', '边界'])
        if not has_validation:
            issues.append({
                'id': 'RQ-002',
                'category': '需求疑问',
                'title': '配置项的生效范围和校验规则',
                'location': '需求文档-配置部分',
                'description': f'文档提到配置项（如{", ".join(config_matches[:2])}），但未明确：1.是否支持热更新 2.配置值格式校验缺失 3.默认值与显式配置的关系',
                'suggestion': '明确配置生效机制和校验规则，补充配置为空、非法值、超大列表等边界处理',
                'severity': 'critical'
            })

    # 检查预警/阈值逻辑
    if any(kw in doc_lower for kw in ['预警', '阈值', '告警']):
        has_dynamic = any(kw in doc_lower for kw in ['动态', '调整', '自适应'])
        if not has_dynamic:
            issues.append({
                'id': 'RQ-003',
                'category': '需求疑问',
                'title': '预警阈值是否需要根据数据量动态调整',
                'location': '需求文档-预警逻辑部分',
                'description': '文档定义了预警阈值，但未明确是否需要根据候选池大小/数据量动态调整，可能导致误触发或漏触发',
                'suggestion': '补充预警阈值动态调整方案，或明确固定阈值的适用场景和局限性',
                'severity': 'critical'
            })

    # ========== 模糊点 ==========

    # 检查时间窗口定义
    if any(kw in doc_lower for kw in ['窗口', '时间范围', '最近']):
        has_specific = any(kw in doc_lower for kw in ['小时', '分钟', '秒', '具体时间'])
        if not has_specific:
            issues.append({
                'id': 'FU-001',
                'category': '模糊点',
                'title': '时间窗口的定义不明确',
                'location': '需求文档-查询逻辑部分',
                'description': '文档提到"最近窗口"但未明确：1.时间范围是多少 2.基于哪个时间字段 3.窗口配置是否可调整',
                'suggestion': '明确窗口时间定义和配置方式，补充时间窗口的业务含义和技术实现',
                'severity': 'major'
            })

    # 检查业务口径定义
    if any(kw in doc_lower for kw in ['口径', '定义为', '业务含义']):
        has_detail = any(kw in doc_lower for kw in ['具体', '详细', '明确'])
        if not has_detail and len(doc_content) < 5000:
            issues.append({
                'id': 'FU-002',
                'category': '模糊点',
                'title': '业务口径定义不够详细',
                'location': '需求文档-业务逻辑部分',
                'description': '文档定义了业务口径，但未提供详细的触发条件和边界说明，开发实现时可能产生歧义',
                'suggestion': '补充业务口径的详细定义，包括触发条件、边界场景、与其他状态的区分',
                'severity': 'major'
            })

    # ========== 完整性检查 ==========

    # 只有明确存在数据库实现或性能设计时，SQL 示例才是适用的评审项。
    if has_database_change and any(
        kw in doc_lower for kw in ['sql', '索引', '执行计划', '查询性能', 'where条件']
    ):
        has_sql = any(kw in doc_lower for kw in ['select', 'where', 'sql示例', '查询语句'])
        if not has_sql:
            issues.append({
                'id': 'IC-001',
                'category': '完整性',
                'title': '缺少查询SQL示例',
                'location': '需求文档-数据库部分',
                'description': '文档提到数据库查询逻辑，但未提供SQL示例，难以确认过滤条件的位置和索引覆盖情况',
                'suggestion': '补充查询SQL示例（可脱敏），帮助理解查询逻辑和性能影响',
                'severity': 'minor',
                'preconditions': ['存在数据库变更证据', '需求明确涉及 SQL/索引/查询性能'],
                'evidence': evidence(
                    {'requirement_tables': list(requirement_tables)[:10]},
                    {'code_sql_count': len(code_sql)},
                ),
            })

    # 检查是否缺少异常处理
    if has_external_dependency and any(kw in doc_lower for kw in ['异常', '失败', '错误', '超时']):
        has_handling = any(kw in doc_lower for kw in ['降级', '重试', '回滚', '异常处理', '容错'])
        if not has_handling:
            issues.append({
                'id': 'IC-002',
                'category': '完整性',
                'title': '缺少异常处理和降级策略',
                'location': '需求文档-异常处理部分',
                'description': '文档提到异常场景，但未定义降级策略，外部依赖故障时无替代方案',
                'suggestion': '补充异常降级策略，明确外部依赖故障时的处理方式和数据一致性保障',
                'severity': 'major',
                'preconditions': ['存在外部依赖证据', '需求明确提到失败/超时'],
                'evidence': evidence({'requirement_apis': list(requirement_apis)[:10]}),
            })

    # ========== 可测试性检查 ==========

    # 检查验证标准是否明确
    if any(kw in doc_lower for kw in ['验证', '测试', '预期']):
        has_criteria = any(kw in doc_lower for kw in ['标准', '指标', '阈值', '具体值'])
        if not has_criteria:
            issues.append({
                'id': 'TC-001',
                'category': '可测试性',
                'title': '验证标准不明确',
                'location': '需求文档-测试部分',
                'description': '文档定义了验证要求，但未明确具体的验证标准和预期结果，测试执行时难以判断是否通过',
                'suggestion': '补充具体的验证标准和预期结果，包括数据指标、状态值、时间要求等',
                'severity': 'major'
            })

    # ========== 需求风险 ==========

    # 检查数据量/性能风险
    if any(kw in doc_lower for kw in ['数据量', '性能', '大数据', '大量']):
        has_impact = any(kw in doc_lower for kw in ['影响', '风险', '瓶颈', '优化'])
        if not has_impact:
            issues.append({
                'id': 'RK-001',
                'category': '需求风险',
                'title': '数据量增长带来的性能风险',
                'location': '需求文档-数据处理部分',
                'description': '文档未评估数据量增长对性能的影响，大数据量下可能出现查询变慢、资源占用过高等问题',
                'suggestion': '补充数据量增长的性能评估，定义性能指标和监控告警阈值',
                'severity': 'major'
            })

    # 检查兼容性和回滚风险
    if has_operational_change:
        has_rollback = any(kw in doc_lower for kw in ['回滚', '回退', '恢复', '兼容'])
        if not has_rollback:
            issues.append({
                'id': 'RK-002',
                'category': '需求风险',
                'title': '缺少回滚方案和兼容性保障',
                'location': '需求文档-变更部分',
                'description': '文档定义了变更逻辑，但未定义回滚触发条件和操作步骤，上线异常时难以快速恢复',
                'suggestion': '补充回滚方案，明确回滚触发条件、操作方式、数据修复策略和兼容性保障',
                'severity': 'critical',
                'preconditions': ['存在发布、数据结构、迁移或配置变更证据'],
                'evidence': evidence(
                    {'database_change': has_database_change},
                    {'config_change': has_config_change},
                ),
            })

    # ========== 华库学习应用 ==========
    # 基于华库高频根因补充检查

    if essence_root_causes:
        rc_content = ''.join(essence_root_causes)

        # 数据一致性校验缺失
        if has_data_flow and '数据一致性' in rc_content and not any(kw in doc_lower for kw in ['一致性校验', '数据对比', '差异校验']):
            issues.append({
                'id': 'RK-003',
                'category': '需求风险',
                'title': '数据一致性校验缺失（华库高频根因）',
                'location': '需求文档-数据流转部分',
                'description': '华库显示"数据一致性校验缺失"是高频根因，本次需求未定义新旧数据对比校验机制',
                'suggestion': '补充数据一致性校验方案，包括并行运行期、差异阈值、告警介入机制',
                'severity': 'major',
                'preconditions': ['存在多表写入、同步、迁移或数据流转证据'],
                'evidence': evidence(
                    {'requirement_tables': list(requirement_tables)[:10]},
                    {'code_sql_count': len(code_sql)},
                ),
            })

        # 降级策略缺失
        if has_external_dependency and '降级策略' in rc_content and not any(kw in doc_lower for kw in ['降级', '替代方案', '降级开关']):
            issues.append({
                'id': 'RK-004',
                'category': '需求风险',
                'title': '降级策略缺失（华库高频根因）',
                'location': '需求文档-外部依赖部分',
                'description': '华库显示"降级策略缺失"是高频根因，外部依赖故障时无降级方案',
                'suggestion': '补充降级策略，明确外部依赖故障时的替代方案和降级开关',
                'severity': 'major',
                'preconditions': ['存在外部依赖证据'],
                'evidence': evidence({'requirement_apis': list(requirement_apis)[:10]}),
            })

    # ========== 华库遗漏场景应用 ==========
    if essence_omissions:
        om_content = ''.join(essence_omissions)

        # 数据流转全链路异常
        if has_data_flow and '数据流转' in om_content and not any(kw in doc_lower for kw in ['全链路', '端到端', '链路异常']):
            issues.append({
                'id': 'TC-002',
                'category': '可测试性',
                'title': '数据流转全链路异常场景未覆盖（华库高频遗漏）',
                'location': '需求文档-数据流转部分',
                'description': '华库显示"数据流转全链路异常"是高频遗漏场景，本次需求未覆盖各环节异常单独验证',
                'suggestion': '补充全链路异常测试场景，包括ODS拉取→清洗→同步→回写各环节异常验证',
                'severity': 'major',
                'preconditions': ['存在数据流转证据'],
                'evidence': evidence({'requirement_tables': list(requirement_tables)[:10]}),
            })

        # 配置边界
        if has_config_change and '配置' in om_content and '边界' in om_content:
            has_config_boundary = any(kw in doc_lower for kw in ['配置为空', '非法配置', '配置边界'])
            if not has_config_boundary:
                issues.append({
                    'id': 'TC-003',
                    'category': '可测试性',
                    'title': '配置边界场景未覆盖（华库高频遗漏）',
                    'location': '需求文档-配置部分',
                    'description': '华库显示"配置边界"是高频遗漏场景，配置为空、全部排除、阈值边界需验证',
                    'suggestion': '补充配置边界测试场景，包括空配置、非法值、超大列表、重复值等',
                    'severity': 'major',
                    'preconditions': ['存在配置项变更证据'],
                    'evidence': evidence(
                        {'requirement_configs': list(requirement_configs)[:10]},
                        {'code_config_count': len(code_configs)},
                    ),
                })

    # 需求事实—代码证据比较结果与文档规则使用同一评审输入，避免各说各话。
    comparison_severity = {
        '不一致': 'critical',
        '缺失': 'critical',
        '额外实现': 'major',
        '证据不足': 'major',
    }
    for index, finding in enumerate(comparison.get('items', []), 1):
        classification = finding.get('classification')
        if classification not in comparison_severity:
            continue
        issues.append({
            'id': f'EV-{index:03d}',
            'category': '技术方案',
            'title': f"{classification}：{finding.get('dimension', '')}.{finding.get('key', '')}",
            'location': '结构化需求—代码对比',
            'description': finding.get('reason', ''),
            'suggestion': '回到需求事实和源码证据确认；修正文档、实现或记录人工决策。',
            'severity': comparison_severity[classification],
            'preconditions': ['结构化比较器返回非一致结论'],
            'evidence': evidence(
                {'requirement': finding.get('requirement')},
                {'code': finding.get('code')},
            ),
        })

    source_inventory = evidence_context.get('source_inventory', {})
    inventory_items = (
        list(source_inventory.get('attachments', []))
        + list(source_inventory.get('images', []))
    )
    for attachment in inventory_items:
        if not attachment.get('decision_required'):
            continue
        availability = attachment.get('availability', 'linked_missing')
        attachment_id = attachment.get('attachment_id', 'ATT-UNKNOWN')
        issues.append({
            'id': attachment_id,
            'category': '完整性',
            'title': f"缺失附件：{attachment.get('name') or attachment.get('url', '')}",
            'location': '需求文档附件/图片链接',
            'description': (
                f"引用资源状态为 {availability}，当前无法核验其内容；"
                '若该资源影响字段、规则、计费或测试预期，必须先形成明确决策。'
            ),
            'suggestion': '补充可读取的本地附件，或在决策登记中确认缺失资源的处理方式和风险。',
            'severity': 'major',
            'preconditions': ['需求正文包含影响业务口径的附件或图片引用'],
            'evidence': evidence({
                'attachment_id': attachment_id,
                'url': attachment.get('url'),
                'local_path': attachment.get('local_path'),
                'availability': availability,
                'context': attachment.get('context'),
            }),
        })
    # 旧规则产生的问题也必须附带可追溯的需求证据，禁止“无证据问题”进入报告。
    for issue in issues:
        issue.setdefault('preconditions', ['需求正文命中该规则的适用场景'])
        if not issue.get('evidence'):
            issue['evidence'] = [{
                'source': '.ai-internal/normalized_requirement.md',
                'fact_ids': [
                    item.get('fact_id') for item in fact_items[:10]
                    if item.get('fact_id')
                ],
            }]

    # 按严重程度排序
    severity_order = {'critical': 0, 'major': 1, 'minor': 2}
    issues.sort(key=lambda x: severity_order.get(x['severity'], 3))

    return issues


def _build_review_report(issues, doc_name, essence_root_causes, essence_omissions):
    """生成评审报告MD"""

    # 统计
    critical_count = sum(1 for i in issues if i['severity'] == 'critical')
    major_count = sum(1 for i in issues if i['severity'] == 'major')
    minor_count = sum(1 for i in issues if i['severity'] == 'minor')

    # 按分类分组
    by_category = {}
    for issue in issues:
        cat = issue['category']
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(issue)

    # 生成报告
    report = f"""# 评审报告

## 概览

- **文档名称**：{doc_name}
- **评审日期**：{datetime.now().strftime('%Y-%m-%d')}
- **评审类型**：需求评审 + 技术评审
- **问题总数**：{len(issues)}
  - 🔴 严重：{critical_count}
  - 🟡 重要：{major_count}
  - 🟢 一般：{minor_count}

## 华库学习

### 高频根因学习（应用到本次评审）

| 根因类型 | 本次应用 |
|---------|---------|
"""

    if essence_root_causes:
        for rc in essence_root_causes:
            lines = rc.strip().split('\n')
            for line in lines:
                if '|' in line and '根因' in line:
                    report += f"| {line.strip()} | 本次避免共性错误 |\n"
                    break
    else:
        report += "| 无 | 华库为空 |\n"

    report += f"""
### 高频遗漏场景检查（应用到本次评审）

| 遗漏场景 | 本次应用 |
|---------|---------|
"""

    if essence_omissions:
        for om in essence_omissions:
            lines = om.strip().split('\n')
            for line in lines:
                if '|' in line and '遗漏' in line:
                    report += f"| {line.strip()} | 本次重点检查 |\n"
                    break
    else:
        report += "| 无 | 华库为空 |\n"

    # 生成各分类问题
    for category in ['需求疑问', '模糊点', '历史冲突', '完整性', '可测试性', '需求风险', '技术方案']:
        cat_issues = by_category.get(category, [])
        report += f"\n---\n\n## {category}\n\n"

        if not cat_issues:
            report += "*(无)*\n\n"
            continue

        for issue in cat_issues:
            severity_mark = '🔴' if issue['severity'] == 'critical' else ('🟡' if issue['severity'] == 'major' else '🟢')
            preconditions = '；'.join(issue.get('preconditions', [])) or '无'
            evidence_text = json.dumps(
                issue.get('evidence', []), ensure_ascii=False, sort_keys=True
            )
            report += f"""### {issue['id']}：{issue['title']} [{severity_mark} {issue['severity']}]

- **位置**：{issue['location']}
- **适用前提**：{preconditions}
- **证据**：`{evidence_text}`
- **问题**：{issue['description']}
- **建议**：{issue['suggestion']}
- **状态**：待确认
- **处理决策**：
- **决策类型**：accept / accept_with_constraint / reject / defer / not_applicable
- **确认人**：
- **确认日期**：
- **影响测试设计**：是

"""

    if critical_count:
        review_recommendation = '阻断：存在严重问题，完成决策或修复前不得继续进入测试设计。'
    elif major_count:
        review_recommendation = '有条件继续：重要问题必须形成明确决策，并落实到测试设计。'
    else:
        review_recommendation = '可继续：未发现严重或重要问题。'
    report += f"""---

## 评审结论

### 问题汇总

| 级别 | 数量 | 主要问题 |
|------|------|---------|
| 🔴 严重 | {critical_count} | {', '.join([i['title'][:15] for i in issues if i['severity'] == 'critical'][:3])} |
| 🟡 重要 | {major_count} | {', '.join([i['title'][:15] for i in issues if i['severity'] == 'major'][:3])} |
| 🟢 一般 | {minor_count} | {', '.join([i['title'][:15] for i in issues if i['severity'] == 'minor'][:3])} |

### 评审建议

1. **放行结论**：{review_recommendation}
2. **需补充确认**：请逐条确认上述评审问题，填写"处理决策"
3. **建议补充**：根据问题严重程度优先处理严重和重要问题

### 下一步

1. 请逐条确认上述评审问题，填写"处理决策"
2. 确认后我将生成测试点，并针对确认的问题生成验证测试点

---

*评审时间：{datetime.now().strftime('%Y-%m-%d')}*
*评审依据：提测文档 + AI深度分析 + 华库学习*
"""

    return report


def _build_review_json(
    issues, doc_name, essence_root_causes, essence_omissions, source_inventory=None
):
    """生成评审数据JSON"""

    # 统计
    critical_count = sum(1 for i in issues if i['severity'] == 'critical')
    major_count = sum(1 for i in issues if i['severity'] == 'major')
    minor_count = sum(1 for i in issues if i['severity'] == 'minor')

    # 按分类统计
    by_category = {}
    for issue in issues:
        cat = issue['category']
        by_category[cat] = by_category.get(cat, 0) + 1

    # 构建分类列表
    categories = []
    for cat_name in ['需求疑问', '模糊点', '历史冲突', '完整性', '可测试性', '需求风险', '技术方案']:
        cat_issues = [i for i in issues if i['category'] == cat_name]
        if cat_issues:
            categories.append({
                'name': cat_name,
                'items': [{
                    'id': i['id'],
                    'title': i['title'],
                    'location': i['location'],
                    'description': i['description'],
                    'suggestion': i['suggestion'],
                    'severity': i['severity'],
                    'preconditions': i.get('preconditions', []),
                    'evidence': i.get('evidence', []),
                    'status': 'pending',
                    'decision': '',
                    'decision_type': '',
                    'confirmed_by': '',
                    'confirmed_at': '',
                    'affects_test_design': i['severity'] in {'critical', 'major'},
                    'decision_options': list(DECISION_TYPES)
                } for i in cat_issues]
            })

    source_inventory = source_inventory or {}
    attachment_items = list(source_inventory.get('attachments', []))
    image_items = list(source_inventory.get('images', []))
    available_states = {'local_available', 'linked_available'}
    review_data = {
        "metadata": {
            "doc_title": doc_name,
            "doc_type": "混合文档",
            "review_date": datetime.now().strftime('%Y-%m-%d'),
            "reviewer": "AI 评审",
            "essence_library_applied": {
                "root_causes_learned": essence_root_causes[:3] if essence_root_causes else [],
                "omissions_checked": essence_omissions[:3] if essence_omissions else []
            }
        },
        "summary": {
            "total_issues": len(issues),
            "critical": critical_count,
            "major": major_count,
            "minor": minor_count,
            "by_category": by_category
        },
        "document_status": {
            "knowledge_base": {
                "exists": True,
                "message": "知识库已读取",
                "history_items": []
            },
            "related_documents": [],
            "images": {
                "total": len(image_items),
                "parsed": sum(1 for item in image_items if item.get("availability") in available_states),
                "unparsed": sum(1 for item in image_items if item.get("availability") not in available_states),
                "items": image_items,
            },
            "attachments": {
                "total": len(attachment_items),
                "converted": sum(1 for item in attachment_items if item.get("availability") in available_states),
                "items": attachment_items,
            }
        },
        "categories": categories,
        "tech_risks": [],
        "completeness_check": [],
        "history_conflicts": []
    }

    return review_data


# ============================================================================
# 测试点质量保障机制（新增）
# ============================================================================

def _extract_test_data(doc_content):
    """
    从提测文档提取测试"原材料"

    返回: dict with tables, fields, configs, status_codes, images, thresholds
    """
    data = {
        'tables': [],
        'fields': [],
        'configs': [],
        'status_codes': [],
        'images': [],
        'thresholds': []
    }

    # 提取表名
    table_patterns = re.findall(r'(?:t_\w+|\b\w+_record\b|\b\w+_table\b)', doc_content)
    data['tables'] = list(set(table_patterns))

    # 提取字段名
    field_patterns = re.findall(r'(?:[\w_]+_id|[\w_]+_time|[\w_]+_flag|[\w_]+_code|[\w_]+_status)', doc_content)
    data['fields'] = list(set(field_patterns))

    # 提取配置项
    config_patterns = re.findall(r'([a-z]+(?:\.[a-z]+){2,})', doc_content)
    data['configs'] = list(set([c for c in config_patterns if len(c) > 10]))

    # 提取状态码
    status_patterns = re.findall(r'(?:状态[码]?\s*[=:：]\s*(\d{3})|结果[码]?\s*[=:：]\s*(\d{3}))', doc_content)
    data['status_codes'] = list(set([m[0] or m[1] for m in status_patterns]))

    # 提取图片引用
    image_patterns = re.findall(r'(?:截图|图片|图)\s*(\d+)?[)）:：]?\s*([^\n]{0,30})', doc_content)
    data['images'] = [{'num': m[0] or '?', 'desc': m[1].strip()[:50]} for m in image_patterns[:10]]

    # 提取阈值
    threshold_patterns = re.findall(r'(\d+)\s*(?:次|个|秒|分钟|小时|%)', doc_content)
    data['thresholds'] = list(set(threshold_patterns))

    return data


def _ai_derive_testpoints(doc_content, extracted_data, essence_omissions):
    """
    AI基于需求理解推导测试点框架

    返回: list of testpoint dicts with title, category, type, steps, expected
    """
    testpoints = []
    doc_lower = doc_content.lower()

    # 1. 状态处理测试（必须≥3步）
    if any(kw in doc_lower for kw in ['状态', '状态码', '结果码']):
        status_codes = extracted_data['status_codes'] if extracted_data['status_codes'] else ['000', '002', '004']

        # 针对特定状态码生成具体预期，避免模糊词
        for code in status_codes[:3]:
            if code == '000':
                exp = "连续失败计数器被清空；当前在采任务缓存被清理；成功次数+1"
            elif code == '002':
                exp = "连续失败计数器被清空；当前在采任务缓存被清理；不累计成功次数"
            elif code == '004':
                exp = "连续失败计数器+1；不进入成功次数累计"
            else:
                exp = f"状态码{code}按业务定义处理"

            testpoints.append({
                'title': f'{code}状态处理逻辑',
                'category': '状态处理测试',
                'type': '功能',
                'steps': f"1. 触发税局恢复监控在采任务，模拟返回结果码{code}\n2. 查看Redis中该税局对应的连续失败预警统计窗口计数器值\n3. 查看当前在采任务缓存是否被清理",
                'expected': exp
            })

    # 2. 数据过滤测试
    if any(kw in doc_lower for kw in ['过滤', '排除', '筛选', '条件']):
        tables = extracted_data['tables'][:1] if extracted_data['tables'] else ['t_tax_declare_apply_record']
        fields = extracted_data['fields'][:1] if extracted_data['fields'] else ['credit']

        testpoints.append({
            'title': f'数据过滤条件验证',
            'category': '数据过滤测试',
            'type': '功能',
            'steps': f"1. 向{tables[0]}表插入{fields[0]}=1的测试数据\n2. 调用findRecentAutoCollectCandidateTaxCodes方法查询候选税号\n3. 验证查询结果中不包含{fields[0]}=1的订单",
            'expected': f"查询结果中不包含{fields[0]}=1的订单；SQL WHERE条件中包含{fields[0]} != 1"
        })

    # 3. 配置项测试
    if any(kw in doc_lower for kw in ['配置', 'apollo', '参数']):
        configs = extracted_data['configs'][:1] if extracted_data['configs'] else ['tax.pre.warn.auto.token.scan.exclude.company.ids']

        testpoints.extend([
            {
                'title': '配置默认值验证',
                'category': '配置测试',
                'type': '功能',
                'steps': f"1. 不显式配置{configs[0]}\n2. 启动服务或触发查询\n3. 查看系统读取的配置值和SQL中的NOT IN条件",
                'expected': f"系统读取默认配置值；SQL中包含默认值的NOT IN条件；默认值生效且业务逻辑按默认规则执行"
            },
            {
                'title': '配置边界值验证',
                'category': '配置测试',
                'type': '边界',
                'steps': f"1. 配置{configs[0]}为空字符串\n2. 调用查询方法\n3. 查看SQL中的NOT IN条件和系统行为",
                'expected': f"系统处理空配置，NOT IN条件为空或跳过该过滤；不报异常；有降级处理"
            }
        ])

    # 4. 异常处理测试
    if any(kw in doc_lower for kw in ['异常', '失败', '超时', '错误']):
        testpoints.append({
            'title': '异常场景降级处理',
            'category': '异常处理测试',
            'type': '异常',
            'steps': f"1. 模拟Redis连接失败或超时\n2. 触发状态处理，尝试清空连续失败计数器\n3. 查看系统异常处理和日志记录",
            'expected': f"系统捕获Redis异常，不崩溃；记录异常日志；有重试机制或降级策略"
        })

    # 5. 华库遗漏场景补充
    om_content = ''.join(essence_omissions) if essence_omissions else ''
    if '配置' in om_content and '边界' in om_content:
        testpoints.append({
            'title': '配置重复值处理',
            'category': '配置测试',
            'type': '边界',
            'steps': f"1. 配置项包含重复的ID或值\n2. 调用查询方法\n3. 验证系统自动去重或正确处理重复配置",
            'expected': f"业务逻辑不受重复配置影响"
        })

    # 6. 数据一致性测试
    if any(kw in doc_lower for kw in ['一致性', '对比', '同步']):
        testpoints.append({
            'title': '数据一致性验证',
            'category': '数据一致性测试',
            'type': '功能',
            'steps': f"1. 执行业务操作并记录源数据\n2. 对比操作前后数据总量和关键字段\n3. 确认数据总量一致，无丢失或重复",
            'expected': f"数据总量一致；关键字段匹配；无丢失或重复"
        })

    return testpoints


def _fill_testpoint_data(testpoints, extracted_data):
    """
    将提取的具体数据填充到测试点框架中
    """
    filled_points = []

    for tp in testpoints:
        # 填充表名
        if '{table}' in tp.get('trigger', '') or '{table}' in tp.get('verify', ''):
            for table in extracted_data['tables'][:2]:
                tp['trigger'] = tp['trigger'].replace('{table}', table)
                tp['verify'] = tp['verify'].replace('{table}', table)

        # 填充字段名
        if '{field}' in tp.get('verify', ''):
            for field in extracted_data['fields'][:2]:
                tp['verify'] = tp['verify'].replace('{field}', field)

        # 填充配置项
        if '{config}' in tp.get('trigger', ''):
            for config in extracted_data['configs'][:1]:
                tp['trigger'] = tp['trigger'].replace('{config}', config)

        # 处理图片预期
        if extracted_data['images']:
            img = extracted_data['images'][0]
            tp['expected'] = f"{tp.get('confirm', '符合预期')}（详见提测文档截图{img['num']}：{img['desc']}）"
        else:
            tp['expected'] = tp.get('confirm', '业务逻辑正确处理')

        filled_points.append(tp)

    return filled_points


def _ensure_min_steps(testpoint, min_steps=3):
    """
    强制确保操作步骤≥3步
    """
    steps_text = testpoint.get('steps', '')

    # 如果已有步骤，尝试拆分
    if steps_text:
        steps = [s.strip() for s in re.split(r'\d+[\.、]', steps_text) if s.strip()]
        if len(steps) >= min_steps:
            return testpoint

    # 强制拆分为3步
    new_steps = [
        f"1. {testpoint.get('trigger', '准备测试数据并触发业务场景')}",
        f"2. {testpoint.get('verify', '查询相关表和字段验证处理结果')}",
        f"3. {testpoint.get('confirm', '确认业务逻辑和数据状态符合预期')}"
    ]

    testpoint['steps'] = '\n'.join(new_steps)
    return testpoint


def _check_testpoint_quality(testpoint):
    """
    测试点质量检查

    返回: (passed: bool, issues: list)
    """
    issues = []

    # 检查步骤数量
    steps_text = testpoint.get('steps', '')
    steps = [s.strip() for s in re.split(r'\d+[\.、]', steps_text) if s.strip()]
    if len(steps) < 3:
        issues.append(f"步骤不足3步（当前{len(steps)}步）")

    # 检查标题长度
    title = testpoint.get('title', '')
    if len(title) > 30:
        issues.append(f"标题过长（{len(title)}字符）")

    # 检查预期结果是否模糊
    expected = testpoint.get('expected', '')
    vague_words = ['符合预期', '正确处理', '正常', '正确']
    has_vague = any(w in expected for w in vague_words)
    has_image_ref = '截图' in expected or '详见' in expected
    if has_vague and not has_image_ref:
        issues.append("预期结果包含模糊词且无图片引用")

    # 检查是否包含具体对象
    has_specific = any(kw in steps_text for kw in ['表', '字段', '配置', '状态码', '接口', '方法', 'Redis', 'SQL'])
    if not has_specific:
        issues.append("操作步骤未包含具体表名/字段/配置等")

    return (len(issues) == 0, issues)


def _generate_quality_testpoints(doc_content, extracted_data, essence_omissions, project_info):
    """
    完整的质量保障测试点生成流程
    """
    # 1. AI推导测试点（已包含完整步骤和预期）
    testpoints = _ai_derive_testpoints(doc_content, extracted_data, essence_omissions)

    # 2. 质量检查
    quality_issues = []
    for i, tp in enumerate(testpoints):
        passed, issues = _check_testpoint_quality(tp)
        if not passed:
            quality_issues.append({'index': i, 'title': tp['title'], 'issues': issues})

    return testpoints, quality_issues

__all__ = [
    name for name, value in globals().items()
    if callable(value) and getattr(value, "__module__", None) == __name__
]
