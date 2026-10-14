"""语法级关键结构提取：Java 可选 tree-sitter，Python 使用标准库 AST。"""

import ast
import re
from typing import Any


VALIDATION_NAMES = {
    "validate", "check", "checkargument", "checkstate", "isvalid",
    "requirenonnull", "notnull", "notblank", "notempty", "valid",
}


def _normalize(text: str, limit: int = 240) -> str:
    return re.sub(r'\s+', ' ', text or '').strip()[:limit]


def _load_java_parser():
    errors = []
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_java

        language = Language(tree_sitter_java.language())
        try:
            parser = Parser(language)
        except TypeError:
            parser = Parser()
            parser.language = language
        return parser, ""
    except Exception as exc:
        errors.append(f"tree_sitter_java: {exc}")

    try:
        from tree_sitter_languages import get_parser

        return get_parser("java"), ""
    except Exception as exc:
        errors.append(f"tree_sitter_languages: {exc}")
    return None, "; ".join(errors)


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _node_text(source_bytes, node):
    return source_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _tree_sitter_java_syntax(source, parser):
    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    root = tree.root_node
    if_rules, throw_rules, validations = [], [], []
    for node in _walk(root):
        if node.type == "if_statement":
            condition_node = node.child_by_field_name("condition")
            consequence = node.child_by_field_name("consequence")
            alternative = node.child_by_field_name("alternative")
            condition = _normalize(_node_text(source_bytes, condition_node)) if condition_node else ""
            complexity_count = condition.count("&&") + condition.count("||")
            if_rules.append(
                {
                    "condition": condition.strip("() "),
                    "action": _normalize(_node_text(source_bytes, consequence)) if consequence else "",
                    "else_action": _normalize(_node_text(source_bytes, alternative)) if alternative else "无",
                    "line": node.start_point[0] + 1,
                    "complexity": "complex" if complexity_count > 2 else ("medium" if complexity_count else "simple"),
                    "has_else": alternative is not None,
                    "source": "tree-sitter-java",
                }
            )
        elif node.type == "throw_statement":
            throw_rules.append(
                {
                    "statement": _normalize(_node_text(source_bytes, node)),
                    "line": node.start_point[0] + 1,
                    "source": "tree-sitter-java",
                }
            )
        elif node.type == "method_invocation":
            name_node = node.child_by_field_name("name")
            name = _node_text(source_bytes, name_node) if name_node else ""
            folded = name.casefold()
            if folded in VALIDATION_NAMES or folded.startswith(("validate", "check")):
                validations.append(
                    {
                        "field": name,
                        "validation": "校验调用",
                        "condition": _normalize(_node_text(source_bytes, node)),
                        "line": node.start_point[0] + 1,
                        "source": "tree-sitter-java",
                    }
                )
        elif "annotation" in node.type:
            text = _normalize(_node_text(source_bytes, node))
            if any(f"@{name}" in text for name in ("NotNull", "NotBlank", "NotEmpty", "Valid")):
                validations.append(
                    {
                        "field": text,
                        "validation": "校验注解",
                        "condition": text,
                        "line": node.start_point[0] + 1,
                        "source": "tree-sitter-java",
                    }
                )
    return {
        "engine": "tree-sitter-java",
        "parser_available": True,
        "fallback_reason": "",
        "parse_errors": ["tree-sitter 报告语法错误"] if root.has_error else [],
        "if_else_rules": if_rules,
        "throw_rules": throw_rules,
        "validation_rules": validations,
    }


def _mask_java(source: str) -> str:
    """Mask comments and quoted literals while preserving offsets and newlines."""
    chars = list(source)
    i = 0
    state = "code"
    quote = ""
    while i < len(chars):
        ch = chars[i]
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if state == "code":
            if ch == "/" and nxt == "/":
                chars[i] = chars[i + 1] = " "
                state = "line_comment"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                chars[i] = chars[i + 1] = " "
                state = "block_comment"
                i += 2
                continue
            if ch in {'"', "'"}:
                quote = ch
                chars[i] = " "
                state = "string"
                i += 1
                continue
        elif state == "line_comment":
            if ch == "\n":
                state = "code"
            else:
                chars[i] = " "
            i += 1
            continue
        elif state == "block_comment":
            if ch == "*" and nxt == "/":
                chars[i] = chars[i + 1] = " "
                state = "code"
                i += 2
                continue
            if ch != "\n":
                chars[i] = " "
            i += 1
            continue
        elif state == "string":
            if ch == "\\":
                chars[i] = " "
                if i + 1 < len(chars) and chars[i + 1] != "\n":
                    chars[i + 1] = " "
                i += 2
                continue
            if ch == quote:
                chars[i] = " "
                state = "code"
            elif ch != "\n":
                chars[i] = " "
            i += 1
            continue
        i += 1
    return "".join(chars)


def _balanced_end(masked: str, start: int, opening: str, closing: str) -> int | None:
    if start >= len(masked) or masked[start] != opening:
        return None
    depth = 0
    for index in range(start, len(masked)):
        if masked[index] == opening:
            depth += 1
        elif masked[index] == closing:
            depth -= 1
            if depth == 0:
                return index
    return None


def _line_number(source: str, offset: int) -> int:
    return source.count("\n", 0, offset) + 1


def _statement_end(masked: str, start: int) -> int:
    brace = masked.find("{", start)
    semicolon = masked.find(";", start)
    if brace >= 0 and (semicolon < 0 or brace < semicolon):
        end = _balanced_end(masked, brace, "{", "}")
        return end + 1 if end is not None else len(masked)
    return semicolon + 1 if semicolon >= 0 else min(len(masked), start + 300)


def _fallback_java(source: str, reason: str) -> dict[str, Any]:
    masked = _mask_java(source)
    if_rules: list[dict[str, Any]] = []
    throw_rules: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []

    for match in re.finditer(r"\bif\s*\(", masked):
        open_paren = masked.find("(", match.start())
        close_paren = _balanced_end(masked, open_paren, "(", ")")
        if close_paren is None:
            continue
        condition = _normalize(source[open_paren + 1 : close_paren])
        action_end = _statement_end(masked, close_paren + 1)
        action = _normalize(source[close_paren + 1 : action_end])
        cursor = action_end
        while cursor < len(masked) and masked[cursor].isspace():
            cursor += 1
        else_action = ""
        if masked.startswith("else", cursor):
            else_start = cursor + len("else")
            else_end = _statement_end(masked, else_start)
            else_action = _normalize(source[else_start:else_end])
        if_rules.append(
            {
                "condition": condition,
                "action": action,
                "else_action": else_action,
                "has_else": bool(else_action),
                "line": _line_number(source, match.start()),
                "source": "balanced-token-fallback",
            }
        )

    for match in re.finditer(r"\bthrow\s+", masked):
        end = masked.find(";", match.end())
        if end < 0:
            end = min(len(masked), match.end() + 300)
        throw_rules.append(
            {
                "statement": _normalize(source[match.start() : end + 1]),
                "line": _line_number(source, match.start()),
                "source": "balanced-token-fallback",
            }
        )

    call_pattern = re.compile(
        r"\b(?P<name>(?:validate|check|verify|assert)[A-Za-z0-9_]*)\s*\(",
        re.IGNORECASE,
    )
    for match in call_pattern.finditer(masked):
        open_paren = masked.find("(", match.start())
        close_paren = _balanced_end(masked, open_paren, "(", ")")
        end = close_paren + 1 if close_paren is not None else _statement_end(masked, match.end())
        validations.append(
            {
                "field": match.group("name"),
                "validation": "校验调用",
                "condition": _normalize(source[match.start() : end]),
                "line": _line_number(source, match.start()),
                "source": "balanced-token-fallback",
            }
        )
    for match in re.finditer(r"@(NotNull|NotBlank|NotEmpty|Valid)\b", masked):
        validations.append(
            {
                "field": match.group(0),
                "validation": "校验注解",
                "condition": match.group(0),
                "line": _line_number(source, match.start()),
                "source": "balanced-token-fallback",
            }
        )

    return {
        "engine": "balanced-token-fallback",
        "parser_available": False,
        "fallback_reason": reason,
        "parse_errors": [],
        "if_else_rules": if_rules,
        "throw_rules": throw_rules,
        "validation_rules": validations,
    }


def analyze_java_syntax(source: str) -> dict[str, Any]:
    """Analyze critical Java structures with tree-sitter when available."""
    parser, error = _load_java_parser()
    if parser is None:
        return _fallback_java(source, error or "tree-sitter Java parser is unavailable")
    try:
        return _tree_sitter_java_syntax(source, parser)
    except Exception as exc:
        return _fallback_java(source, f"tree-sitter Java analysis failed: {exc}")


class _PythonVisitor(ast.NodeVisitor):
    def __init__(self, source: str) -> None:
        self.source = source
        self.if_rules: list[dict[str, Any]] = []
        self.throw_rules: list[dict[str, Any]] = []
        self.validation_rules: list[dict[str, Any]] = []
        self.functions: list[dict[str, Any]] = []
        self.entry_points: list[dict[str, Any]] = []

    def _segment(self, node: ast.AST) -> str:
        return _normalize(ast.get_source_segment(self.source, node) or "")

    def visit_If(self, node: ast.If) -> None:
        self.if_rules.append(
            {
                "condition": self._segment(node.test),
                "action": self._segment(node.body[0]) if node.body else "",
                "else_action": self._segment(node.orelse[0]) if node.orelse else "",
                "line": node.lineno,
                "source": "python-ast",
            }
        )
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        self.throw_rules.append(
            {
                "statement": self._segment(node),
                "line": node.lineno,
                "source": "python-ast",
            }
        )
        self.generic_visit(node)

    def visit_Assert(self, node: ast.Assert) -> None:
        self.validation_rules.append(
            {
                "field": "assert",
                "validation": "断言校验",
                "condition": self._segment(node.test),
                "line": node.lineno,
                "source": "python-ast",
            }
        )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = ""
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        folded = name.casefold()
        if folded.startswith(("validate", "check", "verify")):
            self.validation_rules.append(
                {
                    "field": name,
                    "validation": "校验调用",
                    "condition": self._segment(node),
                    "line": node.lineno,
                    "source": "python-ast",
                }
            )
        self.generic_visit(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        item = {"name": node.name, "line": node.lineno, "async": isinstance(node, ast.AsyncFunctionDef)}
        self.functions.append(item)
        for decorator in node.decorator_list:
            text = self._segment(decorator)
            if any(token in text.casefold() for token in ("route", "get", "post", "put", "delete", "patch")):
                self.entry_points.append({**item, "decorator": text, "source": "python-ast"})
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)


def analyze_python_syntax(source: str) -> dict[str, Any]:
    """Use Python's standard-library AST and expose parse failures explicitly."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {
            "engine": "python-ast",
            "parser_available": True,
            "parse_errors": [f"line {exc.lineno}: {exc.msg}"],
            "if_else_rules": [],
            "throw_rules": [],
            "validation_rules": [],
            "functions": [],
            "entry_points": [],
        }
    visitor = _PythonVisitor(source)
    visitor.visit(tree)
    return {
        "engine": "python-ast",
        "parser_available": True,
        "parse_errors": [],
        "if_else_rules": visitor.if_rules,
        "throw_rules": visitor.throw_rules,
        "validation_rules": visitor.validation_rules,
        "functions": visitor.functions,
        "entry_points": visitor.entry_points,
    }
