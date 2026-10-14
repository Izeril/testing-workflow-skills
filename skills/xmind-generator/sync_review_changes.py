#!/usr/bin/env python3
"""
评审变更同步脚本 - 将用户评审修改同步到 JSON 中间产物

功能：
1. 从评审报告.md 提取处理决策，同步到评审数据.json
2. 从评审终稿.xmind 提取 flag-green/red 标记，同步到测试点.json
3. 基于更新后的数据重新生成可导入版 XMind

用法：
    python sync_review_changes.py <评审报告.md路径> <评审终稿.xmind路径> [输出目录]
"""
import os
import sys
import json
import re
import zipfile
from pathlib import Path
from datetime import datetime


def sync_review_json(review_md_path, final_xmind_path, output_dir=None):
    """
    同步评审变更到 JSON 文件

    Args:
        review_md_path: 评审报告.md 路径
        final_xmind_path: 评审终稿.xmind 路径
        output_dir: 输出目录（默认与评审报告同级）
    """
    review_md_path = Path(review_md_path)
    final_xmind_path = Path(final_xmind_path)

    if output_dir is None:
        output_dir = review_md_path.parent
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"开始同步评审变更...")
    print(f"评审报告: {review_md_path}")
    print(f"评审终稿: {final_xmind_path}")
    print(f"输出目录: {output_dir}")
    print()

    # 1. 同步评审数据 JSON
    review_data_path = _find_json_file(output_dir, "评审数据.json")
    if review_data_path:
        print(f"[1/2] 同步评审数据.json: {review_data_path.name}")
        _sync_review_decisions(review_md_path, review_data_path)
    else:
        print(f"[1/2] 未找到评审数据.json，跳过同步")

    # 2. 同步测试点 JSON（从终稿 XMind 提取标记）
    testpoint_json_path = _find_json_file(output_dir, "测试点.json")
    if testpoint_json_path:
        print(f"\n[2/2] 同步测试点.json: {testpoint_json_path.name}")
        _sync_testpoint_marks(final_xmind_path, testpoint_json_path)
    else:
        print(f"\n[2/2] 未找到测试点.json，跳过同步")

    print("\n同步完成！")


def _find_json_file(output_dir, suffix):
    """查找匹配的 JSON 文件"""
    for f in output_dir.glob(f"*{suffix}"):
        return f
    return None


def _sync_review_decisions(review_md_path, review_data_path):
    """
    从评审报告.md 提取处理决策，同步到评审数据.json
    """
    # 读取评审报告
    with open(review_md_path, 'r', encoding='utf-8') as f:
        md_content = f.read()

    # 读取评审数据 JSON
    with open(review_data_path, 'r', encoding='utf-8') as f:
        review_data = json.load(f)

    # 提取处理决策（匹配 RQ-001: 处理决策：xxx 格式）
    decision_pattern = re.compile(
        r'^(RQ|FU|IC|TC|RK)-(\d+)[:：].*?处理决策[：:]\s*(.+)$',
        re.MULTILINE
    )

    decisions = {}
    for match in decision_pattern.finditer(md_content):
        prefix = match.group(1)
        num = match.group(2)
        decision = match.group(3).strip()
        issue_id = f"{prefix}-{num}"
        decisions[issue_id] = decision

    # 更新 JSON 中的 decisions
    updated_count = 0
    for category in review_data.get('categories', []):
        for item in category.get('items', []):
            item_id = item.get('id', '')
            if item_id in decisions:
                old_decision = item.get('decision', '')
                item['decision'] = decisions[item_id]
                item['status'] = '已处理' if decisions[item_id] else '待解决'
                if old_decision != decisions[item_id]:
                    updated_count += 1
                    print(f"  更新 {item_id}: {old_decision} -> {decisions[item_id]}")

    # 写回 JSON
    with open(review_data_path, 'w', encoding='utf-8') as f:
        json.dump(review_data, f, ensure_ascii=False, indent=2)

    print(f"  共更新 {updated_count} 个处理决策")


def _sync_testpoint_marks(final_xmind_path, testpoint_json_path):
    """
    从评审终稿.xmind 提取 flag-green/red 标记，同步到测试点.json
    """
    # 读取终稿 XMind 的 content.json
    xmind_testpoints = _extract_testpoints_from_xmind(final_xmind_path)

    # 读取测试点 JSON
    with open(testpoint_json_path, 'r', encoding='utf-8') as f:
        testpoint_data = json.load(f)

    # 构建测试点标题到节点的映射
    title_to_node = {}
    for module in testpoint_data.get('modules', []):
        for tp in module.get('testpoints', []):
            title_to_node[tp['title']] = tp

    # 同步标记
    updated_count = 0
    for xmind_tp in xmind_testpoints:
        title = xmind_tp['title']
        if title in title_to_node:
            node = title_to_node[title]
            old_marks = node.get('review_marks', [])

            # 更新标记
            marks = []
            if xmind_tp.get('flag_green'):
                marks.append('flag-green')
            if xmind_tp.get('flag_red'):
                marks.append('flag-red')
            if xmind_tp.get('modified'):
                marks.append('modified')

            node['review_marks'] = marks

            # 如果标记有变化，计数
            if set(old_marks) != set(marks):
                updated_count += 1
                mark_text = ', '.join(marks) if marks else '无标记'
                print(f"  更新 {title[:30]}... -> {mark_text}")

    # 更新统计信息
    total = len(xmind_testpoints)
    green = sum(1 for tp in xmind_testpoints if tp.get('flag_green'))
    red = sum(1 for tp in xmind_testpoints if tp.get('flag_red'))

    testpoint_data['review_summary'] = {
        'total_testpoints': total,
        'flag_green_count': green,
        'flag_red_count': red,
        'unchanged_count': total - green - red
    }

    # 写回 JSON
    with open(testpoint_json_path, 'w', encoding='utf-8') as f:
        json.dump(testpoint_data, f, ensure_ascii=False, indent=2)

    print(f"  共更新 {updated_count} 个测试点标记")
    print(f"  评审统计: 新增 {green} 个, 删除 {red} 个, 未变 {total - green - red} 个")


def _extract_testpoints_from_xmind(xmind_path):
    """
    从评审终稿 XMind 提取测试点及标记
    """
    testpoints = []

    try:
        with zipfile.ZipFile(xmind_path, 'r') as zf:
            content_json = json.loads(zf.read('content.json'))
            root_topic = content_json[0]['rootTopic']
            _traverse_testpoints(root_topic, testpoints)
    except Exception as e:
        print(f"  警告: 读取XMind文件失败: {e}")
        return []

    return testpoints


def _traverse_testpoints(topic, testpoints, path=None):
    """
    递归遍历 XMind 节点，提取测试点及标记
    """
    if path is None:
        path = []

    title = topic.get('title', '')
    markers = topic.get('markers', [])
    current_path = path + [title]

    # 检查标记
    flag_green = any('flag-green' in m.get('markerId', '') for m in markers)
    flag_red = any('flag-red' in m.get('markerId', '') for m in markers)

    # 检查是否包含操作步骤和预期结果（测试点特征）
    children = topic.get('children', {}).get('attached', [])
    has_steps = any('操作步骤' in c.get('title', '') for c in children)
    has_expected = any('预期结果' in c.get('title', '') for c in children)

    if has_steps and has_expected:
        testpoints.append({
            'title': title,
            'path': current_path,
            'flag_green': flag_green,
            'flag_red': flag_red,
            'modified': flag_green or flag_red  # 有标记的视为已修改
        })

    # 递归遍历
    for child in children:
        _traverse_testpoints(child, testpoints, current_path)


def main():
    if len(sys.argv) < 3:
        print("用法: python sync_review_changes.py <评审报告.md路径> <评审终稿.xmind路径> [输出目录]")
        print("\n功能:")
        print("  1. 从评审报告.md 提取处理决策，同步到评审数据.json")
        print("  2. 从评审终稿.xmind 提取 flag-green/red 标记，同步到测试点.json")
        sys.exit(1)

    review_md_path = sys.argv[1]
    final_xmind_path = sys.argv[2]
    output_dir = sys.argv[3] if len(sys.argv) > 3 else None

    sync_review_json(review_md_path, final_xmind_path, output_dir)


if __name__ == "__main__":
    import sys
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    main()
