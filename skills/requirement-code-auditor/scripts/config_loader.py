"""Layer distribution, local, environment and invocation configuration."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_CONFIG: Dict[str, Any] = {
    "project": {"product": ""},
    "code_source": {"provider": "gitnexus", "allow_local_fallback": False},
    "gitnexus": {
        "url": "",
        "protocol_version": "2025-03-26",
        "connect_timeout_seconds": 10,
        "request_timeout_seconds": 60,
        "max_retries": 1,
        "max_queries_per_fact": 3,
        "max_symbols_per_fact": 10,
        "include_content": True,
    },
    "repo": {"path": "", "baseline": "origin/master", "targets": []},
    "analysis": {
        "default_mode": "audit",
        "cache_hours": 24,
        "require_release_test_document": True,
        "max_parallel_targets": 2,
        "batch_files": 50,
        "model_batch_files": 20,
        "model_batch_chars": 120000,
        "single_file_chars": 40000,
    },
    "codegraph": {"enabled": False, "index_path": ""},
    "output": {"dir_name": "测试分析", "internal_dir": ".ai-internal"},
    "legacy_workflow": {"enabled": False, "output_dir_name": "legacy"},
}


def _merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _read_mapping(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        import yaml

        payload = yaml.safe_load(text) or {}
    except ImportError:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"配置需要 PyYAML 或 JSON 格式：{path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"配置根节点必须是对象：{path}")
    return payload


def load_config(
    config_path: Optional[str] = None,
    *,
    repo_path: Optional[str] = None,
    baseline: Optional[str] = None,
    output_dir: Optional[str] = None,
) -> Dict[str, Any]:
    skill_root = Path(__file__).resolve().parents[1]
    config = _merge(DEFAULT_CONFIG, _read_mapping(skill_root / "config.yaml"))
    config = _merge(config, _read_mapping(skill_root / "config.local.yaml"))
    selected_config = config_path or os.getenv("AUDITOR_CONFIG_PATH")
    if selected_config:
        config = _merge(config, _read_mapping(Path(selected_config).expanduser().resolve()))

    env_repo = os.getenv("AUDITOR_REPO_PATH") or os.getenv("TESTCASE_REPO_PATH")
    env_baseline = os.getenv("AUDITOR_BASELINE") or os.getenv("TESTCASE_BASELINE")
    env_output = os.getenv("AUDITOR_OUTPUT_DIR")
    env_provider = os.getenv("AUDITOR_CODE_PROVIDER")
    env_gitnexus_url = os.getenv("AUDITOR_GITNEXUS_URL")
    env_gitnexus_token = os.getenv("AUDITOR_GITNEXUS_TOKEN")
    env_gitnexus_timeout = os.getenv("AUDITOR_GITNEXUS_TIMEOUT")
    if env_repo:
        config["repo"]["path"] = env_repo
    if env_baseline:
        config["repo"]["baseline"] = env_baseline
    if env_output:
        config["output"]["path"] = env_output
    if env_provider:
        config["code_source"]["provider"] = env_provider
    if env_gitnexus_url:
        config["gitnexus"]["url"] = env_gitnexus_url
    if env_gitnexus_token:
        # Runtime-only credential; never distribute it in config or artifacts.
        config["gitnexus"]["token"] = env_gitnexus_token
    if env_gitnexus_timeout:
        try:
            config["gitnexus"]["request_timeout_seconds"] = float(env_gitnexus_timeout)
        except ValueError as exc:
            raise ValueError("AUDITOR_GITNEXUS_TIMEOUT 必须是数字") from exc
    if repo_path:
        config["repo"]["path"] = repo_path
    if baseline:
        config["repo"]["baseline"] = baseline
    if output_dir:
        config["output"]["path"] = output_dir
    config["compatibility_warnings"] = [
        name
        for name in ("TESTCASE_REPO_PATH", "TESTCASE_BASELINE")
        if os.getenv(name)
    ]
    return config
