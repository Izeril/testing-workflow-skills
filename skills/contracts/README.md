# 工作流技能公共契约

`skill_contracts.json` 的活动 `skills` 只登记四个工作流包：`testcase-generator`、`doc-to-md`、`requirement-code-auditor`、`xmind-generator`。公开 Python 入口、CLI、输入参数和关键输出字段统一校验；核心包及其公开入口缺失仍失败。

旧 `code-analyzer`、`req-code-comparator` 已移动到同级 `.codex/skills-archive/`。它们仅在 `archived_skills` 中保存位置和替代包元数据，标记 `runtime_callable: false`，不登记 CLI 或可调用函数。验证器不会加载、要求安装或回退到归档包；旧包重新进入活动登记时验证失败。仅功能等价回归测试会明确读取归档源码作为参照。

历史产物的 `compatible_producers` 只描述数据兼容性，不构成旧技能的调用入口。

审计器提供完整目录审计入口与 `scripts/workflow_api.py` 的分析/比较组件入口。`code_analysis` 和 `requirement_code_comparison` 的当前生产者为 requirement-code-auditor，历史生产者仍兼容，产物结构和版本不变。

递归契约分为两组：

- `artifact_schemas.json`：`requirement_facts`、`test_design_model`、`coverage_matrix`、`quality_gate_report`。
- `workflow_artifact_schemas.json`：`business_impact`、`testpoints`、`decision_register`、`source_inventory`、`workflow_metrics`。

两组共覆盖 9 类关键产物，检查嵌套必填字段、类型、枚举、字符串格式和稳定 ID 唯一性。兼容报告按产物自己的 schema 版本判断，例如需求代码比较 1.1、业务影响 1.1、代码影响 1.3；未知产物只要求声明版本，不再套用全局 1.0。

验证清单：

```powershell
python .\validate_contracts.py
```

同时验证一个或多个真实 JSON 产物：

```powershell
python .\validate_contracts.py `
  --artifact "business_impact=<path>" `
  --artifact "testpoints=<path>" `
  --artifact "workflow_metrics=<path>"
```

失败返回退出码 3，JSON 消息包含精确字段路径。补充可选字段属于向后兼容；删除或重命名必需字段、改变类型/枚举/分类语义或退出码，必须提升契约主版本并同步正向、负向黄金回归。历史不兼容产物只标记 `regenerate_required`，验证器不会修改或删除文件。XMind ZIP 仍通过结构验证器检查，不能只按扩展名判断。
