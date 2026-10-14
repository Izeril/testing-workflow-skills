---
name: testing-workflow
description: Codex 中文企业测试工作流整包入口，统一执行需求分析、需求评审、测试点与 XMind 用例生成、用例细化和知识库归档；使用包内组件，支持任意需求目录，与 jwy 五步流水线独立维护。
metadata:
  version: 1.1.0
---

# testing-workflow

本文件所在目录是整包根目录。执行任务前，读取 [核心主入口](testcase-generator/SKILL.md)，随后遵循其中的阶段提示词、业务规则与确认门禁。这里不另行定义业务规则。

- 核心 CLI：[testcase-generator/scripts/main.py](testcase-generator/scripts/main.py)。
- 包内依赖：`testcase-generator`、`doc-to-md`、`requirement-code-auditor`、`xmind-generator`、`contracts`；必须同版、同级且位于本包中。
- 明确读取本包的核心入口和脚本，不按同名技能从用户级、项目级或 jwy 安装目录选择实现。
- 使用用户本次提供的需求目录；业务代码路径、分支、服务权限和负责人按适用功能读取用户输入或本机覆盖值。
- 通用依赖清单：[requirements.txt](requirements.txt)。已有兼容 Python 环境可直接使用；仅在需要时处理缺失依赖。
- `config.local.yaml` 和 `xmind-generator/rules_config.local.json` 是可选本机覆盖，首次安装不要求它们存在，也不复制维护者配置。
- 安装和备份使用用户技能目录及系统临时/缓存位置；运行产物沿用需求目录下的 `测试分析/`，内部证据位于其 `.ai-internal/`。

文档流程的命令形式：

```text
python "<本包根目录>/testcase-generator/scripts/main.py" "<本次需求目录>" --step all
```

没有有效代码目标时按核心流程记录跳过代码相关检查。需要代码分析时核对实际仓库、开发分支或服务连接；缺项不猜测。评审、终稿和归档继续遵守核心入口的人工确认要求。
