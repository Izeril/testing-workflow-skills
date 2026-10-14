# GitNexus Provider

审计器默认使用 `gitnexus` 代码来源；本地 Git 仅通过显式 `AUDITOR_CODE_PROVIDER=local_git` 或自定义配置启用。

## 运行配置

```yaml
code_source:
  provider: gitnexus
  allow_local_fallback: false

gitnexus:
  url: ""  # 本机覆盖；示例地址 https://gitnexus.example.invalid/api/mcp
  protocol_version: 2025-03-26
  connect_timeout_seconds: 10
  request_timeout_seconds: 60
  max_queries_per_fact: 3
  max_symbols_per_fact: 10
  include_content: true
```

支持环境变量：`AUDITOR_CODE_PROVIDER`、`AUDITOR_GITNEXUS_URL`、`AUDITOR_GITNEXUS_TOKEN`、`AUDITOR_GITNEXUS_TIMEOUT`。凭据只在进程内使用，不写入配置和审计产物。

## 零配置自动发现

远程模式默认不要求用户维护目标注册表。Provider 读取提测文档中的服务或显式仓库候选，并与 `list_repos` 返回的仓库标识、服务别名做精确匹配。

提测文档至少包含：

```text
涉及开发分支：feature/xxx
涉及服务：jwy3
```

也可以写明：

```text
代码仓库：jwy3
```

每个目标必须唯一映射仓库，并且提测文档必须提供开发分支。Provider 会先执行 `list_repos` 自动发现仓库，再用带 `branch` 的只读查询确认分支范围；无法确认或匹配多个时阻断，不回退到默认工作区或本地仓库。管理员仍可使用 `repo.targets` 作为可选固定映射，但普通用户不需要编辑它。

## 证据边界

调用链、影响关系、模块聚类和相关度只能帮助定位。支持比较结论的代码证据至少包含仓库、分支、文件、符号和可比较源码/行为值。没有这些锚点的结果只进入 `graph_evidence`，不会支持“一致”。

远程模式的全局覆盖永远不是 `complete`。SQL/MyBatis、配置和存储过程按维度披露 `verified`、`partial`、`unknown` 或 `unsupported`；SQL 必需维度未验证时，整体状态保持 `warning`。
