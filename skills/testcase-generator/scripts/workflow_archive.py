#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Knowledge-base archival, review-diff analysis, and root-cause learning."""

import json
import zipfile
from datetime import datetime

from workflow_common import log_console
from workflow_refine import _is_xmind_testpoint_topic
def run_archive(req_dir, output_dir, project_info, kb_manager, user_config):
    """
    步骤⑦：入库沉淀

    输入：全部产出物
    输出：知识库更新 + 进化日志 + 华库提炼
    """
    log_console("\n" + "=" * 60)
    log_console("📦 步骤⑦ 入库沉淀")
    log_console("=" * 60)

    if not kb_manager:
        log_console("  ⚠️ 知识库未初始化，无法入库")
        return None

    # 1. 重复入库检查
    # 从 output_dir 路径提取项目名称（如果 project_info 为空）
    doc_name = project_info.get('project_name', '')
    if not doc_name:
        # 从输出目录的父目录名提取
        parent_name = output_dir.parent.name
        doc_name = parent_name.replace('【', '').replace('】', '')
        if not doc_name:
            doc_name = '需求'
    date = datetime.now().strftime('%Y%m%d')

    check_result = kb_manager.check_duplicate_archive(doc_name, 'requirement', date)
    log_console(f"  🔍 重复检查: {check_result}")

    # 2. 差异对比分析（AI 原始生成 vs 用户评审后）
    diff_stats = _analyze_review_diff(output_dir, doc_name)
    log_console(f"  ✅ 差异分析: 新增={diff_stats['added']}, 删除={diff_stats['deleted']}, 修改={diff_stats['modified']}, 确认={diff_stats['confirmed']}")

    # 3. 根因分析
    root_causes = _analyze_root_causes(diff_stats, output_dir)
    log_console(f"  ✅ 根因归纳: {len(root_causes)} 条")

    # 4. 文件命名规范化
    # 尝试从知识库获取当前编号
    next_num = kb_manager.get_next_archive_number() if hasattr(kb_manager, 'get_next_archive_number') else '001'
    prefix = f'{next_num}_{doc_name}_{date}'

    # 5. 入库文件（从输出目录根目录和.ai-internal目录查找）
    internal_dir = output_dir / '.ai-internal'
    files_to_archive = [
        ('需求分析.md', 'requirements', f'{prefix}.md', [internal_dir, output_dir]),
        ('代码评审报告.md', 'tech-solutions', f'TR_{prefix}.md', [internal_dir, output_dir]),
        ('*评审报告.md', 'review-reports', f'{prefix}_评审报告.md', [output_dir]),
        ('*测试点.json', 'testcases', f'{prefix}_测试点.json', [internal_dir, output_dir]),
        ('测试用例.json', 'testcases', f'{prefix}_测试用例.json', [internal_dir, output_dir]),
    ]

    archived_count = 0
    for src_pattern, target_dir, new_name, search_dirs in files_to_archive:
        src_path = None
        # 在多个目录中查找
        for search_dir in search_dirs:
            if '*' in src_pattern:
                matches = list(search_dir.glob(src_pattern))
                if matches:
                    src_path = matches[0]
                    break
            else:
                candidate = search_dir / src_pattern
                if candidate.exists():
                    src_path = candidate
                    break

        if src_path and src_path.exists():
            kb_manager.archive_file(src_path, target_dir, new_name)
            loc = '根目录' if src_path.parent == output_dir else '.ai-internal'
            log_console(f"  ✅ 入库: {new_name} (从 {loc}/{src_path.name})")
            archived_count += 1
        else:
            log_console(f"  ⚠️ 未找到文件: {src_pattern}")

    # 6. 更新进化日志
    record = {
        'name': doc_name,
        'date': datetime.now().strftime('%Y-%m-%d'),
        'diff_stat': diff_stats,
        'root_causes': root_causes,
        'archived_files': archived_count
    }
    kb_manager.update_evolution_log(record)
    log_console(f"  ✅ 进化日志已更新")

    # 7. 华库提炼
    essence_result = kb_manager.extract_essence([])
    log_console(f"  ✅ 华库提炼: {essence_result}")

    # 8. 更新索引
    kb_manager.update_index()
    log_console(f"  ✅ 知识库索引已更新")

    log_console(f"\n  📦 入库完成: {archived_count} 个文件已入库")
    return kb_manager.kb_path


def _analyze_review_diff(output_dir, doc_name):
    """
    差异对比分析：AI原始生成 vs 用户评审后

    从评审终稿XMind提取flag-green/red标记统计差异
    """
    diff_stats = {
        'added': 0,       # flag-green
        'deleted': 0,     # flag-red
        'modified': 0,    # 标题变更但无标记
        'confirmed': 0    # 无标记未变更
    }

    # 查找评审终稿
    final_xmind = None
    for f in output_dir.glob('*评审_终稿.xmind'):
        final_xmind = f
        break

    if not final_xmind or not final_xmind.exists():
        log_console(f"  ⚠️ 未找到评审终稿XMind，使用默认差异统计")
        return diff_stats

    try:
        with zipfile.ZipFile(final_xmind, 'r') as zf:
            content_json = json.loads(zf.read('content.json'))
            root_topic = content_json[0]['rootTopic']

            # 递归遍历统计标记
            _count_review_markers(root_topic, diff_stats)
    except Exception as e:
        log_console(f"  ⚠️ 读取评审终稿失败: {e}")

    return diff_stats


def _count_review_markers(topic, diff_stats, path=None):
    """Count review markers on reviewed XMind testpoint nodes."""
    if path is None:
        path = []
    title = topic.get('title', '')
    current_path = path + [title]
    markers = topic.get('markers', [])
    children = topic.get('children', {}).get('attached', [])

    has_green = any('flag-green' in m.get('markerId', '') for m in markers)
    has_red = any('flag-red' in m.get('markerId', '') for m in markers)

    if _is_xmind_testpoint_topic(topic, current_path):
        if has_green:
            diff_stats['added'] += 1
        elif has_red:
            diff_stats['deleted'] += 1
        else:
            diff_stats['confirmed'] += 1

    for child in children:
        _count_review_markers(child, diff_stats, current_path)


def _analyze_root_causes(diff_stats, output_dir):
    """
    根因分析：基于差异统计归纳遗漏根因

    根据评审新增/删除的测试点，归纳常见遗漏类型
    """
    root_causes = []

    if diff_stats['added'] > 0:
        root_causes.append({
            'type': '场景遗漏',
            'description': f'评审新增 {diff_stats["added"]} 个测试点，AI原始生成时未覆盖',
            'count': diff_stats['added']
        })

    if diff_stats['deleted'] > 0:
        root_causes.append({
            'type': '过度生成',
            'description': f'评审删除 {diff_stats["deleted"]} 个测试点，AI原始生成时超出需求范围',
            'count': diff_stats['deleted']
        })

    # 尝试从评审报告中提取具体问题类型
    review_md = None
    for f in output_dir.glob('*评审报告.md'):
        review_md = f
        break

    if review_md:
        try:
            with open(review_md, 'r', encoding='utf-8') as f:
                content = f.read()

            # 提取评审问题分类
            if '数据一致性' in content or '数据流转' in content:
                root_causes.append({
                    'type': '数据一致性',
                    'description': '数据流转全链路异常场景未覆盖',
                    'count': 1
                })

            if '配置' in content and '边界' in content:
                root_causes.append({
                    'type': '配置边界',
                    'description': '配置项边界场景（空值、非法值、重复值）未覆盖',
                    'count': 1
                })

            if '异常' in content and '降级' in content:
                root_causes.append({
                    'type': '降级策略',
                    'description': '异常场景的降级处理策略未覆盖',
                    'count': 1
                })
        except Exception as e:
            log_console(f"  ⚠️ 读取评审报告失败: {e}")

    return root_causes

__all__ = [
    name for name, value in globals().items()
    if callable(value) and getattr(value, "__module__", None) == __name__
]
# The legacy analyzers remain authoritative; the archive transaction is isolated
# so idempotency, locking, receipts, and manifest recovery do not grow this stage.
from archive_contract import run_archive_idempotent as _run_archive_idempotent


def run_archive(req_dir, output_dir, project_info, kb_manager, user_config):
    log_console("\n" + "=" * 60)
    log_console("📦 步骤⑦ 入库沉淀")
    log_console("=" * 60)
    return _run_archive_idempotent(
        req_dir,
        output_dir,
        project_info,
        kb_manager,
        user_config,
        _analyze_review_diff,
        _analyze_root_causes,
        log_console,
    )
