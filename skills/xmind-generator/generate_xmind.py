"""
需求分析与测试点 XMind 生成器
结合需求文档和开发提测文档，生成结构化的测试点 XMind 文件

使用方法:
    python generate_xmind.py <需求文档路径> [输出目录]
    python generate_xmind.py <需求文档路径> <提测文档路径> [输出目录]
"""

import json
import zipfile
import os
import sys
import uuid
import time
import re
from pathlib import Path

# 导入本技能内聚的模板引擎

from template_engine import load_template, detect_template, render_template
from xmind_utils import gen_id
from xmind_utils import release_reserved_path, reserve_versioned_path, write_xmind_zip


def make_topic(title, markers=None, children=None):
    """创建 XMind 主题"""
    topic = {
        "id": gen_id(),
        "class": "attached",
        "title": title,
        "titleUnedited": False
    }
    if markers:
        topic["markers"] = [{"markerId": m} for m in markers]
    if children:
        topic["children"] = {"attached": children}
    return topic


def build_test_case(name, preconditions, steps):
    """构建单个测试用例

    结构:
    测试用例名称
    ├── 前置条件：1.xxx、2.xxx
    │   ├── 1.操作步骤
    │   │   └── 1.预期结果
    │   ├── 2.操作步骤
    │   │   └── 2.预期结果
    │   └── 3.操作步骤
    │       └── 3.预期结果

    Args:
        name: 测试用例名称
        preconditions: 前置条件列表 ["条件1", "条件2", ...]
        steps: 操作步骤列表 [(步骤描述, 预期结果), ...]  至少3步
    """
    # 前置条件文本
    precond_text = "、".join(f"{i+1}.{p}" for i, p in enumerate(preconditions))
    precond_topic = make_topic("前置条件：" + precond_text)

    # 每个步骤作为前置条件的子节点
    step_children = []
    for i, (step, expect) in enumerate(steps, 1):
        step_children.append(make_topic(f"{i}.{step}", None, [
            make_topic(f"{i}.{expect}", markers=["task-done"])
        ]))

    precond_topic["children"] = {"attached": step_children}

    return make_topic(name, None, [precond_topic])


def parse_markdown(filepath: str) -> dict:
    """解析 Markdown 文件，提取需求信息"""
    if not os.path.exists(filepath):
        return {}

    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    info = {
        'title': '',
        'background': '',
        'problem': '',
        'root_cause': '',
        'solution': '',
        'goals': [],
        'parameters': [],
        'logic': [],
        'impact': [],
        'test_cases': [],
        'raw_content': content[:2000]  # 用于模板检测
    }

    # 提取标题（从需求文档内容或文件路径）
    # 优先从文件路径中提取（目录名称包含【泾渭云...】）
    req_dir = os.path.basename(os.path.dirname(filepath))
    parent_dir = os.path.basename(os.path.dirname(os.path.dirname(filepath)))

    if '泾渭云' in req_dir:
        info['title'] = req_dir
    elif '泾渭云' in parent_dir:
        info['title'] = parent_dir
    else:
        # 从内容中提取
        title_match = re.search(r'标题：\s*([^\*#\n]+)', content)
        if title_match:
            info['title'] = title_match.group(1).strip()

    # 提取问题描述
    if '问题描述' in content:
        prob_match = re.search(r'问题描述[】:]\s*(.+?)(?=\n\n|\n【|\n##)', content, re.DOTALL)
        if prob_match:
            info['problem'] = prob_match.group(1).strip()

    # 提取根因
    if '根因' in content:
        cause_match = re.search(r'根因[分析】:]\s*(.+?)(?=\n\n|\n【|\n##)', content, re.DOTALL)
        if cause_match:
            info['root_cause'] = cause_match.group(1).strip()

    # 提取解决方案
    if '解决方案' in content:
        sol_match = re.search(r'解决方案[】:]\s*(.+?)(?=\n\n|\n【|\n##)', content, re.DOTALL)
        if sol_match:
            info['solution'] = sol_match.group(1).strip()

    # 提取参数表格
    if '参数名称' in content:
        param_pattern = re.findall(r'\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|', content)
        info['parameters'] = [p for p in param_pattern if p[0] != '---' and '参数' not in p[0]]

    # 提取测试用例
    if '测试用例' in content or '测试项' in content:
        tc_pattern = re.findall(r'(?:TC\d+|\d+)\s*[、.]\s*(.+?)(?=\n\d+[、.]|\n\n|$)', content, re.DOTALL)
        info['test_cases'] = [tc.strip() for tc in tc_pattern if tc.strip()]

    return info




def generate_xmind(requirement_path: str, test_doc_path: str = None, output_dir: str = None, versioned: bool = True, template_name: str = None) -> str:
    """
    生成测试点 XMind 文件

    Args:
        requirement_path: 需求文档路径（.md 或 .doc）
        test_doc_path: 开发提测文档路径（可选）
        output_dir: 输出目录，默认为需求文档所在目录下的"测试分析"
        versioned: 是否生成带版本号的文件名（默认 True）
        template_name: 模板名称，如不指定则自动检测

    Returns:
        输出的 XMind 文件路径
    """
    if not os.path.exists(requirement_path):
        raise FileNotFoundError(f"需求文档不存在: {requirement_path}")

    # 解析需求文档
    req_info = parse_markdown(requirement_path)
    test_info = parse_markdown(test_doc_path) if test_doc_path and os.path.exists(test_doc_path) else {}

    # 设置输出目录
    if output_dir is None:
        base_dir = os.path.dirname(requirement_path)
        output_dir = os.path.join(base_dir, "测试分析")

    os.makedirs(output_dir, exist_ok=True)

    # 生成标题
    title = req_info.get('title', test_info.get('title', '需求测试'))

    # 加载模板
    if template_name:
        template = load_template(template_name)
        print(f"使用模板: {template['name']} - {template['title']}")
    else:
        # 自动检测模板
        detected = detect_template(req_info.get('raw_content', ''))
        template = load_template(detected)
        print(f"自动检测模板: {template['name']} - {template['title']}")

    # 渲染模板
    topics = render_template(template, req_info, test_info)

    # 构建 content.json（按照 v1 标准的层级结构）
    from datetime import datetime
    now = datetime.now()
    year = str(now.year)
    month = now.strftime("%Y%m")
    day = now.strftime("%Y%m%d")

    content = [{
        "id": gen_id(),
        "revisionId": str(uuid.uuid4()),
        "class": "sheet",
        "title": f"{title}-测试点",
        "rootTopic": {
            "id": gen_id(),
            "class": "attached topic",
            "title": year,
            "children": {
                "attached": [{
                    "id": gen_id(),
                    "title": month,
                    "children": {
                        "attached": [{
                            "id": gen_id(),
                            "title": day,
                            "children": {
                                "attached": [{
                                    "id": gen_id(),
                                    "class": "attached",
                                    "title": title,
                                    "children": {
                                        "attached": topics
                                    }
                                }]
                            }
                        }]
                    }
                }]
            }
        }
    }]

    metadata = {
        "appVersion": "10.0.0",
        "author": "Qwen Code",
        "created": int(time.time() * 1000),
        "generator": "Qwen Code - Requirement Analysis Skill",
        "title": f"{title}-测试点",
    }
    manifest = {
        "file-entries": {
            "content.json": {},
            "metadata.json": {},
            "manifest.json": {},
            "Thumbnails/thumbnail.png": {},
        }
    }
    # 原子预留版本号；ZIP 在独立短临时目录构建后原子发布。
    reserved = False
    if versioned:
        today = time.strftime("%Y%m%d")
        output_path = reserve_versioned_path(
            Path(output_dir), f"测试点_{today}"
        )
        reserved = True
    else:
        output_path = Path(output_dir) / "测试点.xmind"

    try:
        write_xmind_zip(output_path, content, metadata, manifest)
    except Exception:
        if reserved:
            release_reserved_path(output_path)
        raise

    print(f"XMind 文件已生成: {output_path}")
    return str(output_path)


if __name__ == '__main__':
    import sys
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print("用法: python generate_xmind.py <需求文档路径> [提测文档路径] [输出目录]")
        print("\n示例:")
        print('  python generate_xmind.py "D:\项目\需求.md"')
        print('  python generate_xmind.py "D:\项目\需求.md" "D:\项目\提测.md"')
        print('  python generate_xmind.py "D:\项目\需求.md" "D:\项目\提测.md" "D:\项目\测试分析"')
        sys.exit(1)

    req_path = sys.argv[1]
    test_path = sys.argv[2] if len(sys.argv) > 2 else None
    out_dir = sys.argv[3] if len(sys.argv) > 3 else None

    try:
        generate_xmind(req_path, test_path, out_dir)
    except Exception as e:
        print(f'✗ 生成失败: {e}')
        import traceback
        traceback.print_exc()
        sys.exit(1)
