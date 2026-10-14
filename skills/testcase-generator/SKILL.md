---
name: testcase-generator
description: 中文企业需求评审与测试用例生成的统一工作流，包含需求事实、严格代码准入、开发预分析、UAT 合入门禁、Codegraph 影响定位、业务影响翻译、测试设计、覆盖矩阵、质量门禁、七阶段日志和度量。
---

# 测试用例生成器

将本技能作为需求分析、需求评审、测试点生成、评审版 XMind、测试用例细化和知识库归档的主入口。

## 强制规则

1. 每个阶段开始前读取 `prompts/` 下对应提示词。
2. 认定上一阶段完成前，核对适用环节的有效输入、实际执行证据和门禁结果；不适用环节须明确记录 skipped 及原因，不以空产物或文件存在认定完成。
3. 多阶段工作维护可见清单；需要评审的内容保留人工确认关口。
4. 默认输出到需求目录的 `测试分析/`；根目录只放用户 Markdown/XMind，JSON、缓存、日志和状态只放 `.ai-internal/`。
5. 测试点阶段不得绕过测试设计模型、覆盖矩阵、来源一致性和质量门禁；门禁失败时禁止生成或汇报最终 XMind。
6. 每个标准阶段写入成功/失败事件，并刷新 `流程度量报告.md`、`execution_trace.json`、`workflow_log.jsonl` 和 `workflow_metrics.json`。
7. 远程业务仓库只读：禁止 pull、push、merge、远程分支创建/删除、重置用户工作区和业务代码修改。
8. 代码结论必须能回溯到需求事实、Git/SHA、源码证据、调用链和置信度；Codegraph 只定位候选，最终结论回到目标 SHA 源码。

## 前置检查

1. 定位需求目录，并按需向上查找 `.skill/project.yaml`。
2. 配置优先级固定为：通用 `config.yaml` → 本机可选 `config.local.yaml` → `TESTCASE_*` 环境变量 → CLI 参数；不得从 `.qwen/skills` 或项目旧配置替代运行配置。
3. 需要知识库且尚未初始化时，使用 `templates/knowledge-base-template/` 和 `templates/project.yaml`。
4. 旧版 `.doc` 使用 `doc-to-md`；已有 Markdown 直接读取。
5. 创建或复用需求目录下的 `测试分析/`。

## 在线文档保存与复用

有需求/提测 URL 时，先核对本地来源清单，再读取网页。取得正文后必须通过主入口 --source-documents 保存到需求文档/和提测文档/；引用设计放需求文档/。版本、获取时间、URL、摘要及原始响应放 .ai-internal/。默认复用本地正文，用户要求更新时才显式刷新，不能只存临时文件或报告缓存。详见 [来源缓存与索引策略](docs/来源缓存与索引策略.md)。

## 阶段与所有权

- 需求分析：读取阶段提示词，通过 scripts/main.py --step analysis 调度。代码分析与需求代码比较属于同一个条件环节：统一由 requirement-code-auditor 的 scripts/workflow_api.py 提供代码分析和需求比较：先取得有效代码证据，再调用同包的结构化比较引擎；没有已完成的代码分析时不执行比较；无代码目标记录 skipped，已指定目标但分析失败保留 blocked 并阻止进入代码测试设计。明确标注的接口输出字段与已定位的返回对象赋值按接口及字段比较；仅有 DTO 声明、SQL 结果映射或未解析的间接返回时保留追踪缺口，未命中项只能判为证据不足。需求分析、评审和测试点继续使用本工作流的原有产物链。
- 需求评审：读取 `prompts/requirement_review.md`，先生成 `source_inventory.json` 并识别本地可用、远程缺失、不可读和不适用的文档/附件/图片，再结合知识库精华产出评审报告、结构化问题、`decision_register.json` 和 `review_decision_gate.json`；重跑时按 issue_id 保留人工决策。
- 测试点生成：读取 `prompts/testpoint_generate.md`，有有效代码证据时消费 `RQ → BI → RC → TP`，无代码目标时消费 `RQ → TP` 并明确跳过代码相关检查，生成测试设计模型、覆盖矩阵、质量门禁、详细测试点和评审版 XMind。
- 测试用例细化：读取 `prompts/testcase_refine.md`，仅在 critical/major 决策完整且不存在 `defer` 后，基于已确认评审终稿生成 JSON、Excel 和可导入 XMind。
- 知识库归档：读取 `prompts/knowledge_base_archive.md`，仅在用户确认后归档；按项目与内容摘要幂等写入，生成 `archive_receipt.json`，知识库不可用或无可归档产物时不得记录阶段成功。

模块边界：

- `main.py`：参数与阶段调度，不承载分析规则。
- `workflow_common.py`：配置、预检、目录、状态和度量。
- `cross_skill_adapter.py`：兄弟技能唯一调用边界；按白名单加载公开符号、校验真实路径、限制 sys.path 暴露范围，并集中解析 XMind CLI/规则文件。
- `workflow_git_runner.py`：skill 内唯一仓库 Git 进程执行器；定向 fetch 必须显式授权，其他写操作一律拒绝。
- `skill_errors.py`：稳定异常类型、错误码与 CLI 退出码。
- `workflow_analysis.py`：稳定公共入口。
- `workflow_analysis_legacy.py`：成熟文档/源码分析内核。
- `workflow_analysis_facade.py`：代码准入、Git 快照、提交范围、UAT、Codegraph 和业务影响编排。
- `workflow_testpoint.py`：稳定公共入口。
- `workflow_testpoint_legacy.py`：成熟测试设计与 XMind 内核。
- `workflow_testpoint_facade.py`：BI/RC 测试点、覆盖矩阵和新增门禁。
- 其他阶段由对应 `workflow_*.py` 所有。

不得把新增规则重新堆叠到 `main.py`，也不得在不同模块复制正则或分析路径。调用兄弟技能时禁止裸导入、全局注入技能目录或自行拼接兄弟路径，只能经过 `cross_skill_adapter.py`。

## 开发预分析与正式 UAT 分析

配置 `repo.analysis_mode`（默认 `uat`；需要左移分析时显式设置 `development`）：

- `development`：使用 `origin/master...origin/开发分支` 形成左移预分析。未联网刷新时可使用缓存远程引用，但必须标记 `cached_unverified`，不得宣称已进入 UAT。
- `uat`：形成正式测试环境结论。默认定向刷新开发分支、基线和 `uat_branch`；任一引用缺失、失败或部分更新都阻断正式结论。离线分析必须显式使用 `--no-refresh-remote`，且只能保留预分析。

定向刷新只允许：

- `origin/开发分支`
- `origin/master`（或目标配置的 baseline）
- `origin/uat_merge`（或目标配置的 uat_branch）

不得使用 `--prune`，不得回滚或混用部分更新后的新旧 SHA。

共享开发分支必须提供 `commit_range`、PR、稳定 `requirement_id` 或确认 `changed_files`；缺失时状态为 `blocked_ambiguous_commit_scope`。需求关键词只排序，不得从已确认 Git 变更范围中硬删除文件。

UAT 门禁联合使用：

- 提交祖先关系；
- `git cherry` 补丁等价；
- stable patch-id；
- 目标文件内容核验。

状态固定为 `fully_merged`、`partially_merged`、`not_merged`、`indeterminate`。只有 `fully_merged` 可继续正式 UAT 结论。

## Codegraph 与敏捷提交

默认 repo.codegraph_policy 为 risk_based：先固定本次开发、基线和 UAT 提交；索引滞后提示风险并继续源码分析。图证据有效时接受，否则按冻结 SHA 补核关键入口、调用链、读写/配置和本需求变更文件。只有关键覆盖未完成时阻断正式结论，不因全库 SHA 不同一概阻断。

旧索引仅作导航，不宣称已同步或 UAT 已确认。source_verified 由逐字源码、行号、SHA、范围及关键路径核验支持，空证明或错误原文不能放行。严格索引模式仅在用户明确需要时用 --codegraph-policy strict。详见 [来源缓存与索引策略](docs/来源缓存与索引策略.md)。

### Codegraph 成本与停止门禁

Codegraph 维护不是需求分析、测试点或 XMind 请求的隐含授权。先核对目标 SHA、HEAD、索引状态和变更文件数；只有用户授权且 HEAD 对齐、可增量同步时才允许维护。
严格模式下，工作树与目标不一致时，索引维护保持 `blocked_index_mismatch` 并停止；源码分析仍可继续。不得为了绕过门禁自动 clone 仓库、创建 worktree、复制 `.codegraph`、重建索引、reset 或 checkout。
预计运行超过 60 秒、写入超过 200 MB 或复制完整仓库/索引时，先说明成本和替代方案并取得明确同意。长任务一次有界等待，超时检查后停止。索引门禁不触发自动维护或扩大范围。

## 业务影响与测试点

先按实际代码执行证据区分条件分支：
- 无代码目标且清单无错误：业务影响记录为 `status: skipped`、`analysis_mode: not_applicable` 并说明原因；不生成用户业务影响报告，旧报告移入内部历史目录。需求分析和需求测试点继续执行，覆盖链为 `RQ → TP`。
- 已指定目标但准入失败、分析失败或没有已分析变更文件：记录 blocked，不能改成“无代码”；禁止进入代码测试设计。
- 完成了有效变更文件分析：才翻译业务影响。没有有效影响对象时不能因为错误数为0而通过。
- 跳过项不计为门禁通过，也不计入适用门禁通过率分母；测试点入口核对目标、执行状态、影响及比较结果是否一致。旧空报告需要重跑分析生成明确跳过记录，不能凭文件存在续跑。


每条 `business_impact.json` 至少包含影响对象、场景、触发、前置条件、新行为、规则、数据/状态/上下游/异常影响、回归范围、置信度和证据 ID。
生成业务影响前必须执行“筛选 → 归并 → 业务翻译 → 测试表达”：

- 同一定时任务同时出现在 API、调度任务和入口点时只保留一个验证对象，优先认定为调度任务；SQL 按业务表归并。
- 仅保留与当前验证对象相关的规则、配置和数据证据；禁止把解析器字典、SQL 分析对象、`source_file` 等原始结构直接输出给测试人员。
- 用户报告必须明确验证对象、预期结果、数据检查、异常恢复和测试关注点；代码路径与证据编号放到独立的“技术追溯”区。
- `BI-G7` 可读性、`BI-G8` 可执行性、`BI-G9` 场景去重均为硬门禁，任一失败都不得把业务影响作为合格输入传给测试点阶段。
代码评审报告同样执行“结论前置 → 风险翻译 → 测试动作 → 放行条件 → 技术追溯”：

- 用户正文先展示放行建议和 P0/P1 必测风险，不按解析器维度罗列入口、API、SQL 或比较器键值。
- 同一入口不得在入口、任务、API 中重复出现；验证对象复用归并后的业务影响对象。
- 每条 P0/P1 必须写明“可能出现的问题、测试人员怎么验证、放行条件”；源码路径、SHA 和完整人工复核原文保留在内部证据。
- `CR-G1` 结论、`CR-G2` 可执行性、`CR-G3` 可读性、`CR-G4` 对象去重均为硬门禁，失败时不得进入测试点阶段。

置信度：

- `confirmed`：直接变更 + UAT 完整合入 + Codegraph + 源码覆盖完整。
- `high`：直接入口/一跳 + 源码行为证据。
- `medium`：间接或覆盖部分完整。
- `potential/unknown`：动态调用、不支持技术栈或证据不足，仅进入待确认。

`confirmed/high` 影响必须有测试点；测试点标题使用“验证对象 + 关键条件 + 期望业务行为”，技术路径放笔记。有有效代码证据时，覆盖矩阵必须包含业务影响和需求代码异常项，链路为 `RQ → BI → RC → TP`；无代码目标时使用 `RQ → TP`，不得虚构 BI/RC 节点。

正式 UAT 下，以下任一项阻断 XMind：

- UAT 未完整合入或本次引用最新性未验证；
- 关键影响链没有有效图证据或目标 SHA 源码补核；显式 strict 仍要求图版本一致；
- P0/P1 关键技术覆盖缺口；
- 确定业务影响未覆盖；
- 缺失、不一致、额外实现未形成测试/评审处理；
- 证据不足事项没有处理决策。
- critical/major 评审问题没有完整的决策类型、决策内容、确认人和确认日期。
- 需求正文引用的业务附件或图片为 `linked_missing`/`unreadable`，且对应评审问题没有完整人工决策。

评审终稿确认和测试用例细化阶段还会再次读取 `decision_register.json`。即使 `defer` 决策字段完整，也只能保留为待确认状态，禁止生成终稿或进入细化；必须改为可执行的最终决策后才能继续。

## 配置与错误契约

分发版 `config.yaml` 不包含人员、公司或机器路径；当前工作站值放在不参与分发的 `config.local.yaml`。环境变量支持 `TESTCASE_PRODUCT`、`TESTCASE_COMPANY`、`TESTCASE_MANAGER`、`TESTCASE_REPO_PATH`、`TESTCASE_BASELINE` 和 `TESTCASE_UAT_BRANCH`，同名 CLI 参数优先级最高。

CLI 退出码固定为：0 成功、1 未分类异常、2 输入/配置错误、3 人工或质量门禁阻断、4 外部或跨技能依赖失败、5 分析失败、6 转换失败、130 用户中断。跨技能异常会按 `error_code` 归一化，进入输出目录后发生的失败还会写入 `.ai-internal/last_error.json`，并在已有活动阶段时同步写入执行轨迹；调用方不得只解析控制台文本判断结果。

## 脚本用法

```powershell
python .\scripts\main.py "<requirement_dir>" --step all --force --auto
python .\scripts\main.py "<requirement_dir>" --step analysis
python .\scripts\main.py "<requirement_dir>" --step analysis
python .\scripts\main.py "<requirement_dir>" --step analysis --no-refresh-remote
python .\scripts\main.py "<requirement_dir>" --step review
python .\scripts\main.py "<requirement_dir>" --step testpoint
python .\scripts\main.py "<requirement_dir>" --step refine
python .\scripts\main.py "<requirement_dir>" --step archive
```

多服务或共享分支使用 `--code-targets <JSON>`；详细字段、Codegraph 结果格式和产物说明见 `docs/代码分析优化落地说明.md`。

## 产物

用户可见新增：

- `业务影响分析.md`（仅代码环节适用时生成；无代码目标不生成）

内部新增：

- `code_analysis_admission.json`
- `git_snapshot.json`
- `code_change_scope.json`
- `uat_merge_gate.json`
- `codegraph_query_plan.json`
- `codegraph_impact.json`
- `business_impact.json`
- `business_impact_gate.json`
- `decision_register.json`
- `review_decision_gate.json`
- `source_inventory.json`
- `archive_receipt.json`
- `last_error.json`

既有 `requirement_facts.json`、`requirement_code_comparison.json`、`test_design_model.json`、`coverage_matrix.json`、`quality_gate_report.json` 继续作为下游权威输入。

## 验证

```powershell
python -m unittest discover .codex/skills/testcase-generator/tests -p "test_*.py"
Push-Location .codex/skills/requirement-code-auditor
python -m unittest discover -s tests -t . -p "test_*.py"
Pop-Location
python -m unittest discover .codex/skills/doc-to-md/tests -p "test_*.py"
```

黄金语义样本可单独作为 CI 门禁执行：

```powershell
python -m unittest discover .codex/skills/testcase-generator/tests -p "test_golden_*.py" -v
```

真实项目质量基线固定 5 个只读项目、18 个 JSON 快照、12 个递归契约产物、25 个关键字段、108 个证据锚点、11 个重点覆盖指标和 15 条人工复核语义标签。发布前运行：

```powershell
python .codex/skills/testcase-generator/scripts/real_project_quality_baseline.py --report .codex/skills/testcase-generator/tests/.reports/real_project_quality_report.json
```

该命令对项目存在性、SHA-256 漂移、契约通过率、关键字段准确率、证据追溯率、重点覆盖率、语义标签准确率及高风险假阳性率执行硬阈值；只读源项目，失败返回退出码 3。离开包含真实样本的工作区时，便携合成回归仍运行，真实工作区用例明确跳过，不得把跳过宣称为真实基线通过。

执行后检查真实输出目录、质量基线报告和 `流程度量报告.md`。流程报告必须同时展示本次请求完成率、七阶段全流程完成率和人工确认关口状态。重要结论必须直接复核源文档、Markdown、XMind、Git/SHA 和源码证据。
## 核心产物嵌套契约

九类关键产物必须满足中央递归契约：`artifact_schemas.json` 管理 `requirement_facts`、`test_design_model`、`coverage_matrix`、`quality_gate_report`；`workflow_artifact_schemas.json` 管理 `business_impact`、`testpoints`、`decision_register`、`source_inventory`、`workflow_metrics`。现有 `internal_json_compatibility_report.json` 会通过跨技能适配器调用中央验证器，按各产物自己的 schema 版本递归检查必填字段、类型、枚举、字符串格式和 ID 唯一性；失败项记录精确 JSON 路径并要求重新生成，不得自动删除或静默修补历史产物。

发布前至少运行：

```powershell
python ..\contracts\validate_contracts.py
python -m unittest discover .\tests -p "test_*contract*.py"
```
## 公共契约

四个当前工作流技能的公开入口、产物 schema 和错误负载统一登记在 `../contracts/skill_contracts.json`；两个已归档技能只保留 `archived_skills` 元数据，不登记可调用入口。修改入口或产物字段后运行 `python ..\contracts\validate_contracts.py`，并执行 `tests/test_skill_contract_manifest.py`；跨技能调用继续通过 `cross_skill_adapter.py`，不得重新注入全局 `sys.path` 或内联复制兄弟技能实现。

## 代码分析与需求比较

本工作流通过 cross_skill_adapter.py 调用 requirement-code-auditor/scripts/workflow_api.py 的 extract_business_logic 与 compare_structured，统一承接代码分析和需求实现比较。主入口负责需求事实、Git/UAT/Codegraph 准入、业务影响和后续评审产物，审计器组件只返回本次源码分析与比较数据，不读取旧审计产物、不重新生成需求事实或写入第二套报告。独立的 scripts/audit_runner.py 仍负责完整目录审计及 GitNexus 远程模式。

主流程所需技能包为 testcase-generator、doc-to-md、requirement-code-auditor、xmind-generator。code-analyzer 和 req-code-comparator 已移到工作区 `.codex/skills-archive/`，仅保留为功能等价回归参照，不作为活动技能。主流程禁止调用归档技能；requirement-code-auditor 缺失或加载失败时报告跨技能依赖失败，不回退到归档包。Java/MyBatis、Python AST、SQL/存储过程、配置、调用链、接口输出字段追踪和五分类比较由审计器包内引擎提供；数据结构与原有下游契约保持兼容。

仅当至少一个代码目标完成了实际变更文件分析时，才运行需求代码比较。未提供代码目标时，在 requirement_code_comparison.json 中记录 status: skipped 和原因，需求分析、评审及测试点仍按原链路继续。若已指定代码目标但分析失败，保留代码准入和测试点门禁，不把失败伪装成“无代码”。
