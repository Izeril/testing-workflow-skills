"""XMind 模板发现、检测与渲染。"""

import json
import re
from pathlib import Path

from xmind_utils import gen_id


TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
PLACEHOLDER_RE = re.compile(r"{([A-Za-z0-9_]+)}")


def _template_path(template_name):
    if not template_name or Path(template_name).name != template_name:
        raise ValueError(f"非法模板名称: {template_name}")
    path = (TEMPLATE_DIR / f"{template_name}.json").resolve()
    if path.parent != TEMPLATE_DIR.resolve():
        raise ValueError(f"模板路径越界: {template_name}")
    return path


def load_template(template_name):
    path = _template_path(template_name)
    if not path.is_file():
        raise FileNotFoundError(f"模板文件不存在: {path}")
    with path.open("r", encoding="utf-8") as handle:
        template = json.load(handle)
    if not isinstance(template, dict) or not isinstance(template.get("structure"), dict):
        raise ValueError(f"模板结构无效: {path}")
    return template


def get_available_templates():
    templates = []
    if not TEMPLATE_DIR.is_dir():
        return templates
    for path in sorted(TEMPLATE_DIR.glob("*.json")):
        template = load_template(path.stem)
        templates.append(
            {
                "name": template.get("name", path.stem),
                "title": template.get("title", ""),
                "keywords": template.get("keywords", []),
                "description": template.get("description", ""),
                "min_keyword_matches": template.get("min_keyword_matches", 2),
            }
        )
    return templates


def detect_template(requirement_content, fallback="generic"):
    """依据各模板自带的 keywords 元数据选择模板。"""
    content = (requirement_content or "").casefold()
    best_name, best_score = fallback, (0, 0)
    for template in get_available_templates():
        name = template["name"]
        if name == fallback:
            continue
        keywords = [str(keyword).strip().casefold() for keyword in template.get("keywords", []) if str(keyword).strip()]
        matches = [keyword for keyword in keywords if keyword in content]
        minimum = max(1, int(template.get("min_keyword_matches", 2)))
        strong_match = any(len(keyword) >= 6 for keyword in matches)
        if len(matches) < minimum and not strong_match:
            continue
        score = (sum(len(keyword) for keyword in matches), len(matches))
        if score > best_score:
            best_name, best_score = name, score
    return best_name


def _make_topic(title, markers=None, children=None):
    topic = {
        "id": gen_id(),
        "class": "attached",
        "title": title,
        "titleUnedited": False,
    }
    if markers:
        topic["markers"] = [{"markerId": marker} for marker in markers]
    if children:
        topic["children"] = {"attached": children}
    return topic


def render_template(template, req_info, test_info):
    """将模板定义渲染为 XMind topic 列表。"""
    structure = template["structure"]
    for required in ("kym", "test_analysis"):
        if required not in structure:
            raise ValueError(f"模板缺少 structure.{required}")

    context = {}
    context.update(test_info or {})
    context.update(req_info or {})
    dynamic_sections = template.get("dynamic_sections", {})

    def replace_placeholders(text):
        return PLACEHOLDER_RE.sub(lambda match: str(context.get(match.group(1), "待补充")), str(text))

    def build_topic(topic_def):
        if not isinstance(topic_def, dict) or "title" not in topic_def:
            raise ValueError("topic 定义必须是包含 title 的对象")
        markers = []
        if "priority" in topic_def:
            markers.append(f"priority-{topic_def['priority']}")
        markers.extend(topic_def.get("markers", []))

        children = None
        child_data = topic_def.get("children")
        if isinstance(child_data, list):
            children = [build_topic(child) for child in child_data]
        elif isinstance(child_data, str):
            section = dynamic_sections.get(child_data, [])
            children = [build_topic(child) for child in section]
        return _make_topic(replace_placeholders(topic_def["title"]), markers or None, children)

    topics = [build_topic(structure["kym"]), build_topic(structure["test_analysis"])]
    root_title = template.get("root_title", "测试组织")
    root_markers = template.get("root_markers", ["star-red"])
    return [_make_topic(root_title, root_markers, topics)]
