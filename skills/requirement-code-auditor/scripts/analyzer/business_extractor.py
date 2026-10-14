#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
业务逻辑提取器（优化版）
从代码变更中提取业务逻辑、数据流转、处理规则、配置值等关键信息

优化内容：
1. SQL深度分析（数据流转、过滤条件、聚合逻辑）
2. 调用链分析（方法调用关系、数据流转路径）
3. 业务规则提取（if/else判断、异常处理、校验规则）
4. 配置值提取（@Value注解、常量、cron表达式）
5. 流程图生成（可视化业务流程）

使用方法:
    from business_extractor import extract_business_logic

    logic = extract_business_logic(代码仓库路径, 开发分支, 基线版本)
"""

import os
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Any

from ..git_runner import GitCommandRejected, run_git

# 导入各个分析器
from .sql_analyzer import analyze_sql_content, generate_sql_test_cases
from .call_chain_analyzer import analyze_call_chain, generate_call_chain_tests
from .business_rule_extractor import extract_business_rules, generate_business_rule_tests
from .config_extractor import extract_config_values, generate_config_tests
from .flow_diagram_generator import generate_flow_diagram
from .syntax_analyzer import analyze_python_syntax
from .output_layer_analyzer import analyze_output_layer
from .technology_inventory import (
    classify_source_file,
    mark_unreadable,
    summarize_technology_coverage,
)


def run_git_command(args: List[str], cwd: str = None) -> str:
    """Run Git with an argument list; never invoke a shell."""
    result = run_git(cwd or os.getcwd(), args, timeout=30, executor=subprocess.run)
    return result.stdout.strip() if result.returncode == 0 else ""


def get_file_changes(repo_path: str, branch: str, baseline: str = None, selected_files: List[str] = None) -> Dict[str, str]:
    """Collect changed files with a baseline diff or branch log."""
    if baseline:
        output = run_git_command(["diff", f"{baseline}...{branch}", "--name-status"], repo_path)
    else:
        output = run_git_command(["log", branch, "--name-status", "--oneline", "--no-merges"], repo_path)

    changes = {}
    for line in output.split("\n"):
        if not line or line.startswith("commit ") or line.strip().startswith("Merge") or len(line.strip()) < 5:
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            status = parts[0]
            file_path = parts[-1]
            if status in ("A", "M") or status.startswith("R"):
                changes[file_path] = status

    if selected_files is not None:
        allowed = set(selected_files)
        changes = {path: status for path, status in changes.items() if path in allowed}
    return changes


def get_file_content(repo_path: str, branch: str, file_path: str) -> str:
    """Read one file from the selected immutable Git ref."""
    return run_git_command(["show", f"{branch}:{file_path}"], repo_path)

def extract_api_endpoints(content: str) -> List[str]:
    """从代码中提取API端点"""
    endpoints = []

    patterns = [
        r'@RequestMapping\s*\(\s*["\']([^"\']+)["\']',
        r'@GetMapping\s*\(\s*["\']([^"\']+)["\']',
        r'@PostMapping\s*\(\s*["\']([^"\']+)["\']',
        r'@PutMapping\s*\(\s*["\']([^"\']+)["\']',
        r'@DeleteMapping\s*\(\s*["\']([^"\']+)["\']',
        r'@XxlJob\s*\(\s*["\']([^"\']+)["\']',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, content)
        endpoints.extend(matches)

    return list(set(endpoints))


def extract_config_keys(content: str) -> List[str]:
    """从代码中提取配置键"""
    keys = []

    patterns = [
        r'@Value\s*\(\s*["\']\$\{([^:}]+)',
        r'[\w]+\s*=\s*["\']([^"\']+\.[\w]+\.[\w]+)["\']',
        r'spring\.[\w\.]+',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, content)
        keys.extend(matches)

    return list(set(keys))


def extract_business_logic(repo_path: str, branch: str, baseline: str = None, selected_files: List[str] = None) -> Dict[str, Any]:
    """Extract implementation evidence and always report per-file analysis coverage."""
    if not baseline:
        baseline = "origin/master"

    file_changes = get_file_changes(repo_path, branch, baseline, selected_files)
    result = {
        "data_flow": [],
        "processing_rules": [],
        "exception_handling": [],
        "scheduled_tasks": [],
        "api_endpoints": [],
        "config_changes": [],
        "business_keywords": [],
        "file_changes": file_changes,
        "sql_analysis": [],
        "call_chains": [],
        "business_rules": {
            "if_else_rules": [],
            "switch_rules": [],
            "exception_rules": [],
            "throw_rules": [],
            "validation_rules": [],
            "logging_patterns": [],
            "key_decision_points": [],
            "business_rules_summary": "",
        },
        "config_values": {
            "value_annotations": [],
            "constants": [],
            "properties": [],
            "cron_expressions": [],
        },
        "flow_diagram": "",
        "test_cases": [],
        "entry_points": [],
        "data_flows": [],
        "technology_coverage": {},
        "syntax_analysis": [],
        "python_analysis": [],
        "analysis_warnings": [],
        "output_layer": {},
    }

    business_terms = [
        "迁移", "同步", "转换", "映射", "计算", "统计", "汇总", "过滤", "校验",
        "解析", "提取", "合并", "分组", "排序", "定时任务", "job", "handler",
        "cron", "调度", "触发", "数据源", "切换", "starrocks", "mysql", "oracle",
        "分表", "分片", "报表", "账单", "损益", "财会", "分析", "异常", "错误",
        "重试", "降级", "告警", "超时", "失败",
    ]
    sql_suffixes = {".sql", ".ddl", ".dml", ".psql", ".pls", ".pks", ".pkb"}
    config_suffixes = {".yml", ".yaml", ".properties"}

    coverage_records = []
    rule_summaries = []
    selected_sources = {}

    def merge_tagged(target: Dict[str, Any], source: Dict[str, Any], file_path: str, language: str) -> None:
        for key, values in source.items():
            if key in target and isinstance(target[key], list) and isinstance(values, list):
                for value in values:
                    if isinstance(value, dict):
                        target[key].append({**value, "source_file": file_path, "language": language})
                    else:
                        target[key].append(value)

    for file_path in file_changes:
        content = get_file_content(repo_path, branch, file_path)
        coverage = classify_source_file(file_path, content)
        if not content:
            coverage_records.append(mark_unreadable(coverage))
            continue
        coverage_records.append(coverage)
        selected_sources[file_path] = content
        suffix = os.path.splitext(file_path)[1].casefold()

        result["api_endpoints"].extend(extract_api_endpoints(content))
        result["config_changes"].extend(extract_config_keys(content))
        for job in re.findall(r'@XxlJob\s*\(\s*["\']([^"\']+)["\']', content):
            result["scheduled_tasks"].append({"name": job, "file": file_path})
        for term in business_terms:
            if term.casefold() in content.casefold() and term not in result["business_keywords"]:
                result["business_keywords"].append(term)

        if suffix == ".xml" and any(token in content.casefold() for token in ("<select", "<insert", "<update", "<delete")):
            analysis = analyze_sql_content(content)
            result["sql_analysis"].append({"file": file_path, "kind": "mybatis-xml", "analysis": analysis})
            for test in generate_sql_test_cases(analysis):
                result["test_cases"].append({**test, "source_file": file_path})

        elif suffix in sql_suffixes:
            analysis = analyze_sql_content(content)
            result["sql_analysis"].append({"file": file_path, "kind": "sql-or-procedure", "analysis": analysis})
            for test in generate_sql_test_cases(analysis):
                result["test_cases"].append({**test, "source_file": file_path})

        if suffix == ".java":
            result["call_chains"].append({"file": file_path, **analyze_call_chain(content)})
            business_rules = extract_business_rules(content)
            merge_tagged(result["business_rules"], business_rules, file_path, "java")
            if business_rules.get("business_rules_summary"):
                rule_summaries.append(f"{file_path}: {business_rules['business_rules_summary']}")
            metadata = {"file": file_path, **business_rules.get("analysis_metadata", {})}
            result["syntax_analysis"].append(metadata)
            if metadata.get("fallback_reason"):
                # Tree-sitter 不可用时只能声明“部分覆盖”，不能沿用 Java 完整分析状态。
                coverage["status"] = "partial"
                coverage["analyzer"] = metadata.get("engine") or "balanced-token-fallback"
                coverage["fallback_reason"] = metadata["fallback_reason"]
                coverage["warning"] = (
                    f"{file_path}: Java AST 降级为结构化解析；"
                    f"{metadata['fallback_reason']}"
                )
            for test in generate_business_rule_tests(business_rules):
                result["test_cases"].append({**test, "source_file": file_path})

            config_values = extract_config_values(content)
            merge_tagged(result["config_values"], config_values, file_path, "java")
            for ep_type, ep_name in re.findall(
                r'@(GetMapping|PostMapping|PutMapping|DeleteMapping|XxlJob)\s*\(\s*["\']([^"\']+)["\']',
                content,
            ):
                result["entry_points"].append({
                    "type": ep_type, "name": ep_name, "file": file_path, "language": "java"
                })

        elif suffix == ".py":
            python_result = analyze_python_syntax(content)
            result["python_analysis"].append({"file": file_path, **python_result})
            merge_tagged(
                result["business_rules"],
                {
                    "if_else_rules": python_result["if_else_rules"],
                    "throw_rules": python_result["throw_rules"],
                    "validation_rules": python_result["validation_rules"],
                },
                file_path,
                "python",
            )
            for entry in python_result["entry_points"]:
                result["entry_points"].append({
                    "type": "python-route",
                    "name": entry["name"],
                    "file": file_path,
                    "language": "python",
                    "decorator": entry["decorator"],
                })

        if suffix in config_suffixes:
            config_values = extract_config_values(content)
            merge_tagged(result["config_values"], config_values, file_path, "configuration")

    if any(path.endswith((".java", ".xml")) for path in selected_sources):
        repository_paths = run_git_command(
            ["ls-tree", "-r", "--name-only", branch], repo_path
        ).splitlines()
        output_sources = dict(selected_sources)
        service_symbols = set()
        for path, content in selected_sources.items():
            stem = Path(path).stem
            if path.endswith(".java") and re.search(r"(?:Service|Facade|Manager|Handler|Impl)$", stem):
                service_symbols.add(stem)
                service_symbols.update(
                    re.findall(r"\bimplements\s+([A-Z]\w*Service)\b", content)
                )
        if service_symbols:
            grep_args = ["grep", "-l", "-F"]
            for symbol in sorted(service_symbols):
                grep_args.extend(["-e", symbol])
            grep_args.extend([branch, "--", "*.java"])
            known_paths = set(repository_paths)
            for matched in run_git_command(grep_args, repo_path).splitlines():
                related = matched[len(branch) + 1:] if matched.startswith(f"{branch}:") else matched
                if related not in known_paths or related in output_sources:
                    continue
                related_content = get_file_content(repo_path, branch, related)
                if re.search(r"@(?:RestController|Controller|GetMapping|PostMapping|PutMapping|DeleteMapping|RequestMapping)\b", related_content):
                    output_sources[related] = related_content
        result["output_layer"] = analyze_output_layer(
            output_sources,
            repository_paths,
            lambda path: get_file_content(repo_path, branch, path),
        )
        result["output_layer"]["context_files"] = [
            path for path in output_sources if path not in selected_sources
        ]

    result["api_endpoints"] = list(dict.fromkeys(result["api_endpoints"]))
    result["config_changes"] = list(dict.fromkeys(result["config_changes"]))
    result["business_rules"]["business_rules_summary"] = "\n".join(rule_summaries)

    coverage_summary = summarize_technology_coverage(coverage_records)
    parser_warnings = []
    for item in result["syntax_analysis"]:
        if item.get("fallback_reason"):
            parser_warnings.append(f"{item['file']}: Java AST fallback - {item['fallback_reason']}")
        parser_warnings.extend(f"{item['file']}: {error}" for error in item.get("parse_errors", []))
    for item in result["python_analysis"]:
        parser_warnings.extend(f"{item['file']}: {error}" for error in item.get("parse_errors", []))
    result["technology_coverage"] = coverage_summary
    result["analysis_warnings"] = list(dict.fromkeys(coverage_summary["warnings"] + parser_warnings))

    if result["entry_points"]:
        try:
            result["flow_diagram"] = generate_flow_diagram(
                "business_flow", {"entry_points": result["entry_points"]}
            )
        except Exception as exc:
            result["flow_diagram"] = f"Flow diagram generation failed: {exc}"

    return result

def analyze_business_flow(logic: Dict[str, Any]) -> Dict[str, Any]:
    """
    分析业务流转
    """
    flow = {
        'data_sources': [],
        'processing_steps': [],
        'outputs': [],
    }

    keywords = logic.get('business_keywords', [])
    jobs = logic.get('scheduled_tasks', [])
    sql_analysis = logic.get('sql_analysis', [])

    for sql_info in sql_analysis:
        analysis = sql_info.get('analysis', {})
        if 'tables' in analysis:
            for table in analysis['tables']:
                if table not in flow['data_sources']:
                    flow['data_sources'].append(table)

    for job in jobs:
        flow['processing_steps'].append({
            'step': job.get('name', ''),
            'type': 'scheduled_task',
        })

    for keyword in keywords:
        if keyword in ['迁移', '同步', '转换', '映射']:
            flow['processing_steps'].append({
                'step': keyword,
                'type': 'data_transform',
            })

    if '报表' in keywords or '统计' in keywords:
        flow['outputs'].append('报表/统计结果')

    return flow


def _cli_error(error_code, message, exit_code, **context):
    import json
    import sys
    payload = {
        "schema_version": "1.0.0",
        "error_code": error_code,
        "exit_code": exit_code,
        "message": message,
        "context": context,
    }
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
    return exit_code


def main(argv=None):
    """Run the extractor CLI with stable machine-readable failures."""
    import argparse
    import json
    import sys
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="提取指定 Git 变更范围的业务实现证据")
    parser.add_argument("repo_path")
    parser.add_argument("branch")
    parser.add_argument("--baseline", default="origin/master")
    cli_args = parser.parse_args(argv)
    repo_path = Path(cli_args.repo_path)
    if not repo_path.is_dir() or not (repo_path / ".git").exists():
        return _cli_error(
            "invalid_repository",
            f"不是有效的 Git 仓库: {repo_path}",
            2,
            repo_path=str(repo_path),
        )
    try:
        logic = extract_business_logic(str(repo_path), cli_args.branch, cli_args.baseline)
    except GitCommandRejected as exc:
        return _cli_error(exc.code, str(exc), 3, command=exc.command)
    except Exception as exc:
        return _cli_error(
            "unexpected_error",
            str(exc) or type(exc).__name__,
            1,
            error_type=type(exc).__name__,
        )
    print(json.dumps(logic, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
