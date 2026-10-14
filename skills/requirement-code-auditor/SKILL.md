---
name: requirement-code-auditor
description: 统一源码分析与需求实现比较，支持 Java/MyBatis、Python、SQL、接口输出字段追踪和五分类审计。用于需求目录实现审计及 testcase-generator 的代码分析环节；目录审计默认 GitNexus，指定本地目标时使用只读 Git。
---

# 需求实现审计器

## 主工作流集成

`testcase-generator` 通过自己的 `cross_skill_adapter.py` 加载本包 `scripts/workflow_api.py`：

- `extract_business_logic(repo_path, branch, baseline=None, selected_files=None)`：只读分析指定分支快照及变更文件，返回兼容代码分析结构。
- `compare_structured(requirement_facts, code_evidence, dimensions=None)`：比较结构化需求与代码证据，保留五分类和 `schema_version: 1.1.0`。

这两个入口为正式工作流组件，使用包内唯一分析/比较引擎，不自动连接远程服务、刷新 Git 引用、读取默认产物目录或生成第二套事实与报告。由调用方明确提供源码目标；主入口继续负责 Git/UAT/Codegraph 准入、需求事实、业务影响、覆盖矩阵及人工决策。没有代码目标时主入口记录 skipped，指定目标却分析失败时保留 blocked。

源码能力包括 Java 语法分支与调用链、Python AST、独立 MyBatis SQL 语句、SQL/DDL/存储过程、配置值、接口返回对象和同分支未变更 DTO/Mapper/相关 Controller 追踪。仅声明、间接返回或不支持技术栈需记录覆盖缺口；没有完整代码维度证据时未命中项只能判为证据不足。Java 解析降级必须披露原因，Git 调用均经 `scripts/git_runner.py` 拒绝写命令。

独立目录审计仍使用下面的默认审计入口；完整审计的 run_id、产物锁与远程准入规则继续适用。

## 执行默认审计

仅要求用户提供具体需求包目录：

```powershell
python <skill-dir>\scripts\cli.py "<requirement_dir>"
```

固定流程：

```text
读取需求目录 → 识别需求/提测材料 → 构造需求事实 → 解析服务、仓库和开发分支
→ GitNexus 索引准入 → 查询实现符号、源码、调用链和影响范围
→ 标准化代码证据 → 比较需求与实现 → 校验契约并输出报告
```

本地 Git 在显式传入 `repo_path`、指定 `AUDITOR_CODE_PROVIDER=local_git` 或配置 `code_source.provider: local_git` 时使用。远程失败不得自动回退本地 Git。

## 审计门禁

- 只读取 Markdown 需求材料；遇到 `.doc/.docx/PDF` 且无已转换 Markdown 时明确阻断。
- 优先使用显式分支，其次使用当前提测文档分支；禁止回退到本地 checkout 分支或跨文档借用分支。
- GitNexus 默认从提测文档的服务/仓库候选和 `list_repos` 元数据自动发现目标仓库；不要求普通用户维护 `repo.targets`。多仓库或服务歧义立即阻断。
- 每个目标先执行 `list_repos`、带 `branch` 的只读查询和索引准入检查；无法确认目标分支时不得查询默认工作区，也不得输出正式通过结论。
- 远程模式不宣称完整 Git 分支差异；未命中实现默认判为“证据不足”，不能等同于代码缺失。
- 调用链、影响范围、模块聚类和语义相关度不能单独支持“一致”；必须保留仓库、分支、文件、符号和源码/行为值。
- 逐维度传播 `verified/partial/unknown/unsupported` 覆盖状态；SQL/MyBatis 必需维度未验证时整体为 `warning`。
- 不执行 Git 写命令，不 clone、fetch、checkout、merge、reset、clean、commit 或 push。

## 配置

首次安装由部署管理员通过本机 `config.local.yaml` 的 `gitnexus.url` 或 `AUDITOR_GITNEXUS_URL` 提供服务地址；分发包不包含真实内网地址。查询参数保留通用默认值，连接配置就绪后审计器会在线自动发现仓库，普通用户无需维护目标注册表。详细说明见 [references/gitnexus-provider.md](references/gitnexus-provider.md)。支持环境变量：

```text
AUDITOR_CODE_PROVIDER
AUDITOR_GITNEXUS_URL
AUDITOR_GITNEXUS_TOKEN
AUDITOR_GITNEXUS_TIMEOUT
```

Skill 分发配置不得包含用户凭据。

## 输出

用户报告写入 `<需求目录>/测试分析/需求实现审计报告.md` 和 `代码分析报告.md`；内部 JSON 写入 `.ai-internal/`。每次运行生成唯一 `run_id`，消费者必须校验 `producer=requirement-code-auditor`、schema、目标索引提交/目标 SHA 和 run_id。失败时读取 `.ai-internal/last_error.json`，不得用残缺产物生成虚假通过报告。

远程报告固定披露：代码来源、目标仓库、目标分支、索引提交、分支验证状态、逐维度覆盖和代码侧不可审计区。状态使用 `warning` 表达有限覆盖，不输出无条件 `passed`。

## 组件级兼容入口

完整目录审计由 `scripts.audit_runner.run_requirement_code_audit()` 所有；主工作流使用 `scripts.workflow_api`。测试和旧调用方还可导入以下兼容入口：

```python
from scripts.analyzer import analyze_code
from scripts.comparator import compare_structured, compare_from_artifacts
from scripts.comparator.compatibility import ReqCodeComparator
from scripts.evidence_normalizer import build_code_evidence
```

所有组件入口都委托同一包内引擎，不得读取默认产物目录或写入第二套正式事实与证据。公共入口与产物登记在 `../contracts/skill_contracts.json`。验证本包：`python -m unittest discover -s tests -t . -p "test_*.py"`；验证主工作流及旧引擎等价性：在项目根目录运行 `python -m unittest discover .codex/skills/testcase-generator/tests -p "test_*.py"`。
