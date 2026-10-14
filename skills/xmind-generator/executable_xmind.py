#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate an importable testcase XMind from a reviewed final XMind."""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from xmind_utils import (
    default_manifest,
    default_metadata,
    gen_id,
    make_node,
    make_sheet,
    read_xmind_sheets,
    release_reserved_path,
    reserve_unique_path,
    write_xmind_zip,
)


FILTER_TITLE_PARTS = [
    "KYM",
    "待确认问题",
    "评审标记约定",
    "参考资料",
    "tips",
    "涉及信息汇总",
    "是否涉及性能测试",
]

CASE_NAME_SKIP_TITLE_PARTS = FILTER_TITLE_PARTS + ["\u6d4b\u8bd5\u5206\u6790", "\u91d1\u8776\u5f81\u4fe1", "\u6d89\u53ca\u63a5\u53e3", "\u6d89\u53ca\u5b57\u6bb5"]

COMPACT_TEMPLATE_HINT = ""

P1_KEYWORDS = [
    "异常",
    "错误",
    "超时",
    "失败",
    "非法",
    "容错",
    "恢复",
    "核心",
    "主流程",
    "关键",
    "检索",
    "查询",
    "计算",
    "校验",
    "完整",
    "准确",
    "一致",
    "丢失",
    "映射",
    "转换",
    "安全",
    "权限",
    "认证",
    "加密",
    "合规",
    "敏感",
    "崩溃",
    "阻塞",
    "死锁",
    "连接",
    "中断",
    "接口",
    "集成",
    "交互",
    "上下游",
    "依赖",
]

STEP_LABELS = ("操作步骤",)
EXPECTED_LABELS = ("预期结果",)
PRECONDITION_LABELS = ("前置条件",)


TEST_SCOPE_TITLE_PARTS = (
    "功能测试",
    "异常测试",
    "边界测试",
    "接口测试",
    "数据验证",
    "配置测试",
    "性能测试",
    "集成测试",
    "安全测试",
)

SCENARIO_TITLE_PARTS = (
    "正常场景",
    "边界场景",
    "异常场景",
    "异常分支",
    "兼容场景",
)


def extract_date_from_path(path: str | Path) -> str:
    match = re.search(r"(20\d{6})", str(path))
    if match:
        return match.group(1)
    return datetime.now().strftime("%Y%m%d")


def marker_ids(node: Dict) -> set[str]:
    return {m.get("markerId", "") for m in node.get("markers", [])}


def has_marker(node: Dict, marker_id: str) -> bool:
    return marker_id in marker_ids(node)


def has_any_label(text: str, labels: Tuple[str, ...]) -> bool:
    return any(label in text for label in labels)


def strip_label(text: str, labels: Tuple[str, ...]) -> str:
    result = text
    for label in labels:
        result = result.replace(f"{label}：", "")
        result = result.replace(f"{label}:", "")
        result = result.replace(label, "")
    return result.strip()


def children_of(node: Dict) -> List[Dict]:
    return node.get("children", {}).get("attached", [])


def should_skip_subtree(node: Dict) -> bool:
    title = node.get("title", "")
    if any(part in title for part in FILTER_TITLE_PARTS):
        return True
    return has_marker(node, "flag-red")


def looks_like_test_point(node: Dict, path: Optional[List[str]] = None) -> bool:
    own_title = node.get("title", "")
    if (
        has_any_label(own_title, PRECONDITION_LABELS)
        or has_any_label(own_title, STEP_LABELS)
        or has_any_label(own_title, EXPECTED_LABELS)
    ):
        return False

    children = children_of(node)
    titles = [child.get("title", "") for child in children]
    if any(has_any_label(title, STEP_LABELS) for title in titles):
        return True

    # 兼容旧评审版结构：操作步骤可能挂在前置条件节点下面。
    for child in children:
        if has_any_label(child.get("title", ""), PRECONDITION_LABELS):
            nested_titles = [n.get("title", "") for n in children_of(child)]
            if nested_titles:
                return True

    if path and is_review_scope_path(path) and children:
        ids = marker_ids(node)
        has_case_marker = any(
            marker_id.startswith("priority-") or marker_id in {"flag-green", "flag-red"}
            for marker_id in ids
        )
        if not has_case_marker:
            return False

    return is_compact_test_point(node)


def is_compact_test_point(node: Dict) -> bool:
    """识别旧版紧凑测试点：场景节点下面直接挂预期结果叶子。"""
    title = node.get("title", "")
    if (
        has_any_label(title, PRECONDITION_LABELS)
        or has_any_label(title, STEP_LABELS)
        or has_any_label(title, EXPECTED_LABELS)
    ):
        return False
    children = children_of(node)
    if not children:
        return False
    return all(not children_of(child) for child in children)


def is_review_scope_path(path: Optional[List[str]]) -> bool:
    if not path:
        return False
    return any(any(part in title for part in TEST_SCOPE_TITLE_PARTS) for title in path)


def is_review_leaf_test_point(node: Dict, path: Optional[List[str]], include_deleted: bool = False) -> bool:
    """识别新版评审版叶子测试点。

    评审版允许用户给叶子测试点打 flag-red。转换用例时要过滤删除项，
    但生成差异预览报告时必须把删除项统计出来，所以通过 include_deleted
    控制是否把 flag-red 节点视为有效测试点。
    """
    title = clean_title(node.get("title", ""))
    if not title or title in {"无", "?"}:
        return False
    if children_of(node):
        return False
    if any(part in title for part in FILTER_TITLE_PARTS):
        return False
    if not include_deleted and should_skip_subtree(node):
        return False
    if (
        has_any_label(title, PRECONDITION_LABELS)
        or has_any_label(title, STEP_LABELS)
        or has_any_label(title, EXPECTED_LABELS)
    ):
        return False
    if any(part in title for part in SCENARIO_TITLE_PARTS):
        return False
    return is_review_scope_path(path)


def is_leaf_compact_test_point(node: Dict, path: Optional[List[str]]) -> bool:
    """识别可进入可导入版转换的新版评审叶子测试点。"""
    return is_review_leaf_test_point(node, path, include_deleted=False)


def is_review_test_point(node: Dict, path: Optional[List[str]], include_deleted: bool = False) -> bool:
    """统一判断评审终稿里的测试点节点，供统计、预览和转换复用。"""
    if is_review_leaf_test_point(node, path, include_deleted=include_deleted):
        return True
    return looks_like_test_point(node, path)


def compact_template_kind() -> str:
    hint = COMPACT_TEMPLATE_HINT.upper()
    if "NS004" in hint:
        return "ns004"
    if "RPA" in hint or "日志AI分析" in COMPACT_TEMPLATE_HINT or "采集程序日志" in COMPACT_TEMPLATE_HINT:
        return "rpa_log"
    return "generic"


def collect_compact_case_fields(node: Dict, path: Optional[List[str]] = None) -> Tuple[List[str], List[str], List[str]]:
    """把评审版覆盖意图转换为可导入用例字段。"""
    scenario = clean_title(node.get("title", "")).strip()
    leaf_expected = [child.get("title", "").strip() for child in children_of(node) if child.get("title", "").strip()]
    expected_text = "；".join(leaf_expected) if leaf_expected else scenario or "验证结果符合评审测试点"
    context_parts = [clean_title(part) for part in (path or []) if clean_title(part)]
    context_parts = [part for part in context_parts if not any(skip in part for skip in CASE_NAME_SKIP_TITLE_PARTS)]
    context = " / ".join(context_parts[-3:]) if context_parts else scenario
    kind = compact_template_kind()

    if kind == "ns004":
        steps = [
            f"准备满足场景的数据：{scenario}" if scenario else "准备满足测试场景的数据",
            "调用NS004或对应接口查询目标数据",
            "记录响应状态码、返回记录数和关键字段",
        ]
        expected_steps = [
            "测试数据准备完成",
            "NS004或对应接口调用成功",
            expected_text,
        ]
    elif kind == "rpa_log":
        steps = [
            f"准备满足场景的日志或任务数据：{scenario}" if scenario else "准备满足测试场景的日志数据",
            "执行RPA日志采集或日志AI分析相关流程",
            "查看日志解析结果、任务状态和落库数据",
        ]
        expected_steps = [
            "测试数据准备完成",
            "RPA日志流程成功触发",
            expected_text,
        ]
    else:
        steps = [
            f"准备满足场景的数据：{scenario}" if scenario else f"准备满足测试场景的数据：{context}",
            "执行对应业务操作或调用相关接口",
            "查看系统处理结果、数据记录或接口响应",
        ]
        expected_steps = [
            "测试数据准备完成",
            "对应业务操作或接口调用已触发",
            expected_text,
        ]

    preconditions = [f"准备满足测试场景的数据：{scenario}"] if scenario else []
    return preconditions, steps, expected_steps

def clean_title(title: str) -> str:
    title = re.sub(r"^_?\d+(?:\.\d+)*[\.\、\s_]*", "", title)
    title = re.sub(r"\s*\[[Pp]\d+\]\s*", "", title)
    title = re.sub(r"\s*[（(][^）)]*测试点[^）)]*[）)]", "", title)
    return title.strip()


def get_priority(node: Dict) -> str:
    for marker_id in marker_ids(node):
        if marker_id.startswith("priority-"):
            return marker_id

    title = node.get("title", "")
    if "[P1]" in title or any(keyword in title for keyword in P1_KEYWORDS):
        return "priority-1"
    return "priority-2"


def split_lines(text: str) -> List[str]:
    if not text:
        return []

    parts: List[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        fragments = re.split(r"(?=\d+[\.\、])", line)
        parts.extend(fragment.strip() for fragment in fragments if fragment.strip())

    cleaned = []
    for part in parts:
        cleaned.append(re.sub(r"^\d+[\.\、]\s*", "", part).strip())
    return [item for item in cleaned if item]


def collect_case_fields(node: Dict) -> Tuple[List[str], List[str], List[str]]:
    preconditions: List[str] = []
    steps: List[str] = []
    expected: List[str] = []

    def walk(current: Dict, under_steps: bool = False) -> None:
        title = current.get("title", "").strip()
        current_children = children_of(current)

        if has_any_label(title, PRECONDITION_LABELS):
            text = strip_label(title, PRECONDITION_LABELS)
            if text and text not in ("无", "鏃"):
                preconditions.extend(split_lines(text) or [text])
            for child in current_children:
                walk(child, under_steps=False)
            return

        if has_any_label(title, STEP_LABELS):
            text = strip_label(title, STEP_LABELS)
            if text:
                steps.extend(split_lines(text) or [text])
            for child in current_children:
                walk(child, under_steps=True)
            return

        if has_any_label(title, EXPECTED_LABELS):
            text = strip_label(title, EXPECTED_LABELS)
            if text:
                expected.extend(split_lines(text) or [text])
            return

        if under_steps and title:
            steps.extend(split_lines(title) or [title])
            for child in current_children:
                child_title = child.get("title", "")
                if has_any_label(child_title, EXPECTED_LABELS):
                    walk(child, under_steps=False)
            return

        for child in current_children:
            walk(child, under_steps=False)

    walk(node)

    if not steps:
        steps = ["执行操作", "检查系统响应", "记录测试结果"]
    while len(steps) < 3:
        steps.append("记录测试结果")

    if not expected:
        expected = ["符合预期"]
    while len(expected) < len(steps):
        expected.append(expected[-1] if expected else "符合预期")

    return preconditions, steps, expected


def build_case_node(node: Dict, case_name: str, path: Optional[List[str]] = None) -> Dict:
    if is_compact_test_point(node) or is_leaf_compact_test_point(node, path):
        preconditions, steps, expected = collect_compact_case_fields(node, path)
    else:
        preconditions, steps, expected = collect_case_fields(node)
    markers = [{"markerId": get_priority(node)}]

    step_nodes = []
    for idx, step in enumerate(steps, 1):
        step_title = step if re.match(r"^\d+[\.\、]", step) else f"{idx}.{step}"
        expected_text = expected[idx - 1] if idx - 1 < len(expected) else "符合预期"
        expected_text = re.sub(r"^\d+[\.\、]\s*", "", expected_text).strip()
        step_nodes.append(
            make_node(
                step_title,
                children=[make_node(f"预期结果{idx}: {expected_text}")],
            )
        )

    precondition_title = "前置条件：无"
    if preconditions:
        precondition_title = "前置条件：" + "；".join(preconditions)

    return make_node(
        case_name,
        markers=markers,
        children=[make_node(precondition_title, children=step_nodes)],
    )


REVIEW_STATUS_LABELS = {
    "added": "新增",
    "deleted": "删除",
    "modified": "修改",
    "normal": "未变化",
}

REVIEW_ACTION_LABELS = {
    "added": "纳入可导入版，并按评审标题细化前置条件、步骤和预期结果",
    "deleted": "不纳入可导入版",
    "modified": "按评审终稿标题重新细化生成",
    "normal": "按原测试点保留并细化生成",
}


def priority_label(node: Dict) -> str:
    """把 XMind 优先级标记转换为中文报告里更容易读的 P1/P2。"""
    priority = get_priority(node)
    if priority == "priority-1":
        return "P1"
    return "P2"


def normalize_review_title(title: str) -> str:
    """生成重复标题检查用的归一化文本。"""
    return re.sub(r"\s+", "", clean_title(title)).lower()


def format_review_path(path: List[str]) -> str:
    """把 XMind 路径压缩成评审报告可读的业务路径。"""
    parts: List[str] = []
    for part in path:
        clean = clean_title(part)
        if not clean:
            continue
        if re.fullmatch(r"20\d{2,6}", clean):
            continue
        if any(skip in clean for skip in CASE_NAME_SKIP_TITLE_PARTS):
            continue
        parts.append(clean)
    return " > ".join(parts[-4:]) if parts else "未识别路径"


def classify_review_status(node: Dict) -> Tuple[str, List[str]]:
    """根据评审标记判断测试点在可导入版中的处理方式。"""
    ids = marker_ids(node)
    notes: List[str] = []
    if "flag-green" in ids and "flag-red" in ids:
        notes.append("同一测试点同时存在 flag-green 和 flag-red，请人工确认保留还是删除")
    if "flag-red" in ids:
        return "deleted", notes
    if "flag-green" in ids:
        return "added", notes
    if node.get("titleUnedited") is False:
        notes.append("XMind 标记标题已编辑；报告展示评审终稿标题，原始标题需以原评审版为准")
        return "modified", notes
    return "normal", notes


def collect_review_diff_preview(sheets: List[Dict], review_final_path: Path) -> Dict:
    """收集评审终稿差异，生成给用户确认的预览数据。"""
    groups = {"added": [], "deleted": [], "modified": [], "normal": []}
    warnings: List[Dict] = []
    all_items: List[Dict] = []

    def append_warning(warning_type: str, title: str, current_path: List[str], message: str) -> None:
        warnings.append({
            "type": warning_type,
            "title": clean_title(title),
            "path": format_review_path(current_path),
            "message": message,
        })

    def walk(node: Dict, path: Optional[List[str]] = None) -> None:
        title = node.get("title", "")
        current_path = (path or []) + [title]
        if any(part in title for part in FILTER_TITLE_PARTS):
            return

        ids = marker_ids(node)
        is_test_node = is_review_test_point(node, current_path, include_deleted=True)
        has_review_marker = "flag-green" in ids or "flag-red" in ids
        if has_review_marker and not is_test_node:
            append_warning(
                "marker_on_non_test_node",
                title,
                current_path,
                "评审标记打在非测试点节点上，转换时不会按单条用例处理",
            )

        if is_test_node:
            status, notes = classify_review_status(node)
            item = {
                "title": clean_title(title),
                "path": format_review_path(current_path),
                "priority": priority_label(node),
                "status": status,
                "status_label": REVIEW_STATUS_LABELS[status],
                "action": REVIEW_ACTION_LABELS[status],
                "will_generate": status != "deleted",
                "markers": sorted(ids),
                "notes": notes,
                "ambiguous": any("请人工确认" in note or "同一测试点" in note for note in notes),
            }
            groups[status].append(item)
            all_items.append(item)
            if status == "deleted":
                return

        for child in children_of(node):
            walk(child, current_path)

    for sheet in sheets:
        root = sheet.get("rootTopic")
        if root:
            walk(root)

    title_index: Dict[str, List[Dict]] = {}
    for item in all_items:
        key = normalize_review_title(item["title"])
        if key:
            title_index.setdefault(key, []).append(item)
    for duplicate_items in title_index.values():
        if len(duplicate_items) <= 1:
            continue
        duplicate_title = duplicate_items[0]["title"]
        for item in duplicate_items:
            item["ambiguous"] = True
            item["notes"].append("存在同名测试点，生成可导入版前建议确认路径是否区分明确")
        warnings.append({
            "type": "duplicate_title",
            "title": duplicate_title,
            "path": "；".join(item["path"] for item in duplicate_items),
            "message": "评审终稿存在同名测试点，可能影响人工核对",
        })

    statistics = {
        "added": len(groups["added"]),
        "deleted": len(groups["deleted"]),
        "modified": len(groups["modified"]),
        "normal": len(groups["normal"]),
        "will_generate": sum(1 for item in all_items if item["will_generate"]),
        "ambiguous": sum(1 for item in all_items if item["ambiguous"]),
        "warning_count": len(warnings),
    }
    return {
        "schema_version": "1.0.0",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_xmind": str(review_final_path),
        "statistics": statistics,
        "groups": groups,
        "warnings": warnings,
    }


def markdown_escape(value: object) -> str:
    """转义 Markdown 表格中的竖线，避免标题内容撑破表格。"""
    return str(value or "").replace("|", "\\|")


def append_diff_table(lines: List[str], title: str, items: List[Dict]) -> None:
    """向 Markdown 报告追加一个差异分组表。"""
    lines.append(f"## {title}")
    if not items:
        lines.append("无")
        lines.append("")
        return
    lines.append("| 序号 | 优先级 | 路径 | 测试点 | 处理方式 | 备注 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for index, item in enumerate(items, 1):
        notes = "；".join(item.get("notes") or []) or "-"
        lines.append(
            f"| {index} | {markdown_escape(item['priority'])} | {markdown_escape(item['path'])} | "
            f"{markdown_escape(item['title'])} | {markdown_escape(item['action'])} | {markdown_escape(notes)} |"
        )
    lines.append("")


def write_review_diff_preview(review_final_path: Path, output_dir: Path, preview: Dict) -> Tuple[Path, Path]:
    """输出评审差异预览报告，Markdown 给人看，JSON 给流程追溯。"""
    internal_dir = output_dir / ".ai-internal"
    internal_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / "评审差异预览报告.md"
    json_path = internal_dir / "评审差异预览报告.json"

    stats = preview["statistics"]
    lines = [
        "# 评审差异预览报告",
        "",
        f"生成时间：{preview['generated_at']}",
        f"来源 XMind：{review_final_path}",
        "",
        "## 一、统计概览",
        "| 类型 | 数量 | 处理方式 |",
        "| --- | ---: | --- |",
        f"| 新增测试点 | {stats['added']} | 纳入可导入版 |",
        f"| 删除测试点 | {stats['deleted']} | 不纳入可导入版 |",
        f"| 修改测试点 | {stats['modified']} | 按评审终稿标题重新细化 |",
        f"| 未变化测试点 | {stats['normal']} | 保留并细化 |",
        f"| 预计生成用例 | {stats['will_generate']} | 删除项之外的测试点 |",
        f"| 待人工确认 | {stats['warning_count']} | 生成前建议查看 |",
        "",
    ]
    append_diff_table(lines, "二、新增测试点", preview["groups"]["added"])
    append_diff_table(lines, "三、删除测试点", preview["groups"]["deleted"])
    append_diff_table(lines, "四、修改测试点", preview["groups"]["modified"])
    append_diff_table(lines, "五、未变化测试点", preview["groups"]["normal"])

    lines.append("## 六、待人工确认")
    if preview["warnings"]:
        lines.append("| 序号 | 类型 | 路径 | 测试点 | 说明 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for index, warning in enumerate(preview["warnings"], 1):
            lines.append(
                f"| {index} | {markdown_escape(warning['type'])} | {markdown_escape(warning['path'])} | "
                f"{markdown_escape(warning['title'])} | {markdown_escape(warning['message'])} |"
            )
    else:
        lines.append("无")
    lines.extend([
        "",
        "## 七、生成规则确认",
        "- flag-green：按新增测试点纳入可导入版。",
        "- flag-red：按删除测试点过滤，不进入可导入版。",
        "- 标题已编辑：按评审终稿标题重新细化生成。",
        "- 无评审标记：按未变化测试点保留。",
        "- 每条进入可导入版的用例仍需满足操作步骤不少于 3 条。",
        "",
    ])

    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(preview, f, ensure_ascii=False, indent=2)
    return markdown_path, json_path


def summarize_review_marks(root_topic: Dict) -> Counter:
    summary: Counter = Counter()

    def walk(node: Dict, path: Optional[List[str]] = None) -> None:
        current_path = (path or []) + [node.get("title", "")]
        title = node.get("title", "")
        if any(part in title for part in FILTER_TITLE_PARTS):
            return

        is_test_node = is_review_test_point(node, current_path, include_deleted=True)
        ids = marker_ids(node)
        if "flag-red" in ids:
            if is_test_node:
                summary["deleted"] += 1
            return
        if "flag-green" in ids and is_test_node:
            summary["added"] += 1
        elif node.get("titleUnedited") is False and is_test_node:
            summary["modified"] += 1
        elif is_test_node:
            summary["normal"] += 1

        for child in children_of(node):
            walk(child, current_path)

    walk(root_topic)
    return summary


def extract_cases_from_root(root_topic: Dict) -> List[Dict]:
    cases: List[Dict] = []

    def walk(node: Dict, path: Optional[List[str]] = None) -> None:
        if should_skip_subtree(node):
            return

        title = node.get("title", "")
        current_path = (path or []) + [title]

        if is_leaf_compact_test_point(node, current_path) or looks_like_test_point(node, current_path):
            path_parts = [
                clean_title(part)
                for part in current_path
                if part
                and not re.match(r"^\u3010\u6cfe\u6e2d\u4e91\d+\u3011", part)
                and not re.match(r"^20\d{2,6}$", part)
                and not any(skip in part for skip in CASE_NAME_SKIP_TITLE_PARTS)
            ]
            if len(path_parts) >= 3:
                case_name = "_".join(part for part in path_parts[-3:] if part)
            elif len(path_parts) >= 2:
                case_name = "_".join(part for part in path_parts[-2:] if part)
            else:
                case_name = clean_title(title)
            cases.append(build_case_node(node, case_name, current_path))
            return

        for child in children_of(node):
            walk(child, current_path)

    walk(root_topic)
    return cases


def extract_project_name(review_path: str | Path, output_dir: Path) -> str:
    candidates = [
        output_dir.parent.name,
        Path(review_path).parent.parent.name,
        Path(review_path).parent.name,
    ]
    for candidate in candidates:
        if candidate and "测试分析" not in candidate and "评审" not in candidate:
            return candidate
    return "项目"


def extract_date_from_sheets(sheets: List[Dict], fallback_path: str | Path) -> str:
    for sheet in sheets:
        root = sheet.get("rootTopic", {})
        level1 = children_of(root)
        if level1:
            level2 = children_of(level1[0])
            if level2:
                title = level2[0].get("title", "")
                if re.fullmatch(r"20\d{6}", title):
                    return title
    return extract_date_from_path(fallback_path)


def build_output_content(cases: List[Dict], project_name: str, date_str: str, company: str = '测试组织') -> List[Dict]:
    year = date_str[:4]
    month = date_str[:6]

    func_node = make_node("功能测试", children=cases, node_class="attached")
    brand_node = make_node(
        company,
        markers=[{"markerId": "star-red"}],
        children=[func_node],
    )
    project_node = make_node(project_name, children=[brand_node])
    day_node = make_node(date_str, children=[project_node])
    month_node = make_node(month, children=[day_node])
    root = make_node(year, children=[month_node])
    return [make_sheet(root, year)]


def extract_executable(review_final_path: str | Path, output_dir: Optional[str | Path] = None, company: str = '测试组织', manager: str = '测试负责人') -> str:
    global COMPACT_TEMPLATE_HINT
    review_final_path = Path(review_final_path)
    output_dir = Path(output_dir) if output_dir else review_final_path.parent
    COMPACT_TEMPLATE_HINT = f"{review_final_path} {output_dir}"
    output_dir.mkdir(parents=True, exist_ok=True)

    sheets = read_xmind_sheets(review_final_path)
    all_cases: List[Dict] = []
    diff_preview = collect_review_diff_preview(sheets, review_final_path)
    summary: Counter = Counter(diff_preview["statistics"])

    for sheet in sheets:
        root = sheet.get("rootTopic")
        if not root:
            continue
        all_cases.extend(extract_cases_from_root(root))

    project_name = extract_project_name(review_final_path, output_dir)
    date_str = extract_date_from_sheets(sheets, output_dir)
    content = build_output_content(all_cases, project_name, date_str, company)
    metadata = default_metadata(content[0]["id"])
    manifest = default_manifest()

    output_path = reserve_unique_path(output_dir / f"{company}_{manager}.xmind")
    try:
        write_xmind_zip(
            output_path, content, metadata, manifest, source_xmind=review_final_path
        )
    except Exception:
        release_reserved_path(output_path)
        raise

    p1_count = sum(
        1
        for case in all_cases
        if any(marker.get("markerId") == "priority-1" for marker in case.get("markers", []))
    )
    p2_count = sum(
        1
        for case in all_cases
        if any(marker.get("markerId") == "priority-2" for marker in case.get("markers", []))
    )

    preview_md_path, preview_json_path = write_review_diff_preview(review_final_path, output_dir, diff_preview)

    print(f"评审标记统计: 新增={summary['added']} 删除={summary['deleted']} 修改={summary['modified']} 未变={summary['normal']}")
    print(f"评审差异预览报告: {preview_md_path}")
    print(f"生成可导入XMind: {output_path}")
    print(f"测试用例数: {len(all_cases)}; 优先级: P1={p1_count}, P2={p2_count}")

    report_dir = output_dir / ".ai-internal"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "可导入版转换报告.json"
    report = {
        "source_xmind": str(review_final_path),
        "output_xmind": str(output_path),
        "statistics": {
            "added": summary["added"],
            "deleted": summary["deleted"],
            "modified": summary["modified"],
            "normal": summary["normal"],
            "total_cases": len(all_cases),
            "p1_count": p1_count,
            "p2_count": p2_count,
        },
        "review_diff_preview": {
            "markdown": str(preview_md_path),
            "json": str(preview_json_path),
        },
        "rules": {
            "min_steps_per_case": 3,
            "flag_red_filtered": True,
            "flag_green_included": True,
            "compact_review_leaf_supported": True,
            "detail_generation_stage": "importable_xmind",
        },
    }
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"转换报告: {report_path}")

    return str(output_path)


def generate_executable_xmind(review_final_path: str | Path, output_dir: Optional[str | Path] = None, company: str = '测试组织', manager: str = '测试负责人') -> str:
    return extract_executable(review_final_path, output_dir, company, manager)


def main(review_final_path: str, output_dir: Optional[str] = None, company: str = '测试组织', manager: str = '测试负责人') -> None:
    generate_executable_xmind(review_final_path, output_dir, company, manager)


if __name__ == "__main__":
    import sys
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")

    import argparse
    parser = argparse.ArgumentParser(description='从评审终稿生成可导入版XMind')
    parser.add_argument('review_final_path', help='评审终稿路径')
    parser.add_argument('output_dir', nargs='?', default=None, help='输出目录')
    parser.add_argument('--company', default='测试组织', help='公司名称')
    parser.add_argument('--manager', default='测试负责人', help='责任人')
    args = parser.parse_args()

    if not os.path.exists(args.review_final_path):
        print(f"文件不存在: {args.review_final_path}")
        sys.exit(1)

    main(args.review_final_path, args.output_dir, args.company, args.manager)
