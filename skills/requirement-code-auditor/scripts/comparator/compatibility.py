"""兼容入口：将旧类调用委托给领域无关的结构化比较器。"""

try:
    from .comparator import compare_structured, format_findings, main
except ImportError:
    from comparator import compare_structured, format_findings, main


class ReqCodeComparator:
    """兼容旧调用方的包装类，不包含任何具体项目规则。"""

    def __init__(self, req_info, code_changes, dimensions=None):
        self.req_info = req_info
        self.code_changes = code_changes
        self.dimensions = dimensions
        self.result = None

    def compare_structured(self):
        self.result = compare_structured(self.req_info, self.code_changes, self.dimensions)
        return self.result

    def compare(self):
        """返回旧调用方期望的问题字符串列表。"""
        return format_findings(self.compare_structured())


if __name__ == "__main__":
    import sys
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
