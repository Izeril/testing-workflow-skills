---
name: xmind-generator
description: 基于中文详细测试点 Markdown、模板化需求信息或已评审 XMind 文件，生成、转换并验证 XMind 测试产物。适用于评审版 XMind、模板化测试点 XMind、已评审 XMind 到可导入测试用例的转换、评审标记处理或 XMind ZIP 校验。
---

# XMind 生成器

使用本技能处理 XMind 测试产物。

## 生成评审版

输入必须是 Markdown 详细测试点文档，通常为 `详细测试点.md`。

```powershell
python .\review_xmind.py "<详细测试点.md>" "[output_dir]"
```

## 生成模板化 XMind

```powershell
python .\generate_xmind.py "<需求文档.md>" "[提测文档.md]" "[output_dir]"
```

`template_engine.py` 从本技能的 `templates/*.json` 读取模板，依据各模板 `keywords` 元数据检测类型，未命中时使用 `generic`。模板检测和渲染不得依赖技能目录之外的 Python 模块。

## 生成可导入版

用户完成评审终稿后执行：

```powershell
python .\executable_xmind.py "<评审终稿.xmind>" "[output_dir]"
```

转换前同步输出 `评审差异预览报告.md` 和 `.ai-internal/评审差异预览报告.json`。

## 运行元数据

公开 API 与 CLI 的默认公司、责任人分别为“测试组织”和“测试负责人”，不得在生产模块中内置真实组织或人员。需要定制文件名和根节点时显式传入 `company`/`manager`，或使用 `--company`/`--manager`。

## 评审标记

- `flag-green`：新增测试点；执行版保留。
- `flag-red`：删除测试点；执行版过滤。
- 标题变更但无标记：识别为修改。
- 无标记：按未变更保留。

## 格式规则

XMind 文件是 ZIP 包，至少验证：

- `content.json` 包含 sheet、`rootTopic` 和标题。
- `metadata.json` 包含创建者和结构元数据。
- `manifest.json` 列出必要文件。
- 每个 topic 具有唯一 `id`。

## 约束

- 不要将测试点 JSON 直接传给 `review_xmind.py`。
- 不要让关键评审节点保留占位文本；根据需求和提测证据推导。
- 有未解决问题时列出真实问题；没有时写 `无`。
- 模板领域数据只存放在 `templates/*.json`，模板引擎保持领域无关。
- 标签常量只保留有效 UTF-8 中文；不得用 mojibake 字符串兼容损坏文件。
- 公共 ID、主题和 ZIP 工具函数只保留一个权威定义；不得用后定义覆盖前定义。
- XMind ZIP 必须先在短临时目录构建，再原子发布到目标目录；Windows 文件访问统一使用扩展长度路径前缀，避免深层需求目录触发 260 字符限制。
- 并发生成必须原子预留输出文件名；默认版本化产物按 `_v1`、`_v2` 递增，执行版冲突时使用 `_v2` 等唯一名称，禁止并发任务共用固定临时目录或覆盖对方产物。
- 评审规则学习状态按输出目录隔离到 `.ai-internal/xmind_rules_config.json`，同一项目并发更新必须持有原子锁并以原子替换发布；技能根目录 `rules_config.json` 只作为只读初始基线。
- 出现 `xmind.Workbook` 兼容错误时，使用 `zipfile` 和 JSON 检查或构建 XMind ZIP。
- 可行时打开 ZIP 内容或使用兼容工具验证产物。

## 公共契约

本技能的公开入口与产物版本由 `../contracts/skill_contracts.json` 统一登记。修改公开函数名、参数、产物必填字段或 schema 版本时，必须同步清单并运行 `python ..\contracts\validate_contracts.py`；兼容性破坏必须由契约回归测试显式检出。

本机项目初始规则及 KYM 映射保存在 `rules_config.local.json`，不随仓库分发；读取顺序为通用初始基线 → 本机覆盖，已有输出目录的规则状态仍单独隔离。无输出作用域的显式保存写入本机文件，不改通用基线。更新时保留该本机文件。
