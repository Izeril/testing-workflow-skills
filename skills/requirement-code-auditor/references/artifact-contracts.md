# 审计产物契约

内部产物写入 `<输出目录>/.ai-internal/`：`source_inventory.json`、`normalized_requirement.md`、`requirement_facts.json`、`code_targets.json`、`code_analysis.json`、`code_evidence.json`、`requirement_code_comparison.json`、`requirement_code_audit.json`、`quality_gate_report.json` 和失败时的 `last_error.json`。

`requirement_code_comparison.schema_version` 固定为 `1.1.0`。统一产物使用 `1.0.0`，必须包含 `producer=requirement-code-auditor`、唯一 `run_id`、模式、状态、目标索引提交/目标 SHA、质量摘要、警告和产物引用。

GitNexus 远程 `code_analysis` 必须包含 `analysis_mode=gitnexus_remote`、`dimension_coverage`、`analysis_capabilities.index_admission` 和非完整覆盖状态。远程 `requirement_code_audit.status` 不得为无条件 `passed`。

同一正式输出目录只能存在一个写入者。shadow、legacy 与正式产物必须使用隔离目录；消费者校验 producer、run_id、schema_version 和目标索引提交/目标 SHA。
## 主工作流组件契约

`scripts/workflow_api.py` 返回内存中的 `code_analysis` 和 `requirement_code_comparison`，保留旧分析器的字段结构、选定文件范围及五分类语义。调用方负责本次需求事实、运行标识、SHA、输出路径和正式门禁，不要求预先存在 `requirement_code_audit.json`。

源码分析必须保留 `output_layer`、返回字段证据、SQL 映射、`call_chains`、语法/配置规则及逐文件技术覆盖；分批处理需逐类归并，不得只保留第一批或将字典变为键名列表。输出字段仅在具体接口与明确赋值均定位后可比较；DTO 声明和 SQL 结果映射不能单独证明响应字段实现正确。
