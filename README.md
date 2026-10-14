# testing-workflow-skills

testing-workflow-skills 用于 Codex 的测试工作流，与已有 jwy-testi-expert（WorkBuddy/VS Code 五步流水线）独立并存。两者分别维护、安装和更新。

对外整包名称是 **testing-workflow**。仓库链接：[Izeril/testing-workflow-skills](https://github.com/Izeril/testing-workflow-skills)。通用整包版本为 `v1.1.0`，支持下方标准安装器命令。旧 `v1.0.0` 没有整包入口，不能使用下方整包安装参数安装它。

## 把仓库链接发给助手安装

可以直接对 Codex 说：

> 请从 https://github.com/Izeril/testing-workflow-skills 安装 testing-workflow 完整包，使用已验证的 v1.1.0。读取仓库 README，使用现有 skill-installer，从 skills 路径整包安装，名称 testing-workflow。核对本机有效的用户技能目录、Python 依赖和实际发现结果；保留已有 jwy 与其他技能。目标已存在时按更新流程处理。使用我自己的需求目录，不复制维护者配置。

仓库为私有，同事需有读取权限并使用自己的 GitHub 登录。不得分发维护者令牌。

助手先定位本机已有的 skill-installer，使用其安装脚本：

```text
python "<skill-installer目录>/scripts/install-skill-from-github.py" --repo Izeril/testing-workflow-skills --path skills --name testing-workflow --ref v1.1.0
```

当前安装器默认把技能安装到 `CODEX_HOME/skills`，未设置时使用用户目录下 `.codex/skills`；其他宿主可能使用用户级 `.agents/skills`。按实际安装器与宿主核对，必要时增加 `--dest "<已确认有效的用户技能根目录>"`。参见 [官方技能发现说明](https://learn.chatgpt.com/docs/build-skills)。不能仅因目录复制成功就认定技能已启用。

安装器在系统临时目录下载，并复制整个 `skills` 为 `testing-workflow`。首次目标不存在即可安装；不要求已有五组件、业务项目或上一版备份。目标已存在时安装器会停止，转到下方更新流程。

不要分别安装内部四技能，也不要把安装目标设成业务需求目录；本包不需要创建任何维护者同名目录。

## 安装后的结构

```text
testing-workflow/
  SKILL.md                   对外入口
  requirements.txt           本包依赖清单
  testcase-generator/        核心主入口、脚本、提示词、模板、回归
  doc-to-md/                 Word 转 Markdown
  requirement-code-auditor/  代码分析与需求实现审计
  xmind-generator/           XMind 生成及转换
  contracts/                 中央入口和产物契约
```

四组件与 contracts 均在本包内保持同级，脚本根据自身位置定位资源。它们不占用用户技能根目录中的通用同名组件，不从 jwy 目录读取运行代码。

仓库根 `requirements.txt` 引用 `skills/requirements.txt`；安装后的包根直接包含同一依赖清单。源码位置、安装位置与业务需求位置彼此独立。

## 首次检查和开始使用

1. 检查 Python 3.10+、Git 和本包组件完整性。已验证环境为 Python 3.13.5。
2. 使用已有兼容环境；缺少依赖时执行：

```text
python -m pip install -r "<包根目录>/requirements.txt"
python "<包根目录>/contracts/validate_contracts.py"
```

`PyYAML` 读取配置，`markdownify` 转换 HTML/MHTML。`tree-sitter` 和 `tree-sitter-java` 用于 Java 语法解析；代码保留无解析器时的明确降级，`tree-sitter-languages` 是可选回退。仅 OLE2 二进制 Word `.doc` 转换需要外部 LibreOffice/soffice，未安装时说明缺少该条件依赖。

3. 核对宿主发现 `testing-workflow`，并确认内部入口来自 `<包根目录>/testcase-generator`。如果宿主递归扫描出包内四个子技能，在该宿主标准 `skills.config` 中仅将这四个安装路径的 `SKILL.md` 设置为 `enabled = false`，保留外层 `testing-workflow/SKILL.md` 启用；核对路径属于本包后再写入，不停用其他项目或用户级同名入口。该设置只停用子技能的宿主入口声明，不影响外层按文件读取核心指令或调用包内脚本。技能通常在下一轮可用；未刷新时重启后再查。若现有项目级同名核心技能仍存在，明确使用 testing-workflow 的包内路径，不自行删除旧来源。
4. 提供你自己的具体需求目录，例如：

> 使用 testing-workflow 分析需求目录「<本机需求目录>」，先完成需求分析和评审。

也可执行核心 CLI：

```text
python "<包根目录>/testcase-generator/scripts/main.py" "<本机需求目录>" --step all
```

保留现有评审确认、XMind 终稿和归档关口；`--step all` 完成分析与评审后停在确认关口。运行输出为该需求的 `测试分析/`，JSON/状态/门禁证据位于其 `.ai-internal/`，知识库沿用需求/项目的 `.skill` 规则。

仅适用代码分析等功能时提供自己的仓库、开发分支或服务连接。纯文档流程不要求维护者源码、真实测试样本或本机配置。指定了代码目标但分析失败时仍按现有规则阻断，不能把失败伪装成不适用。

## 可选本机配置

本机覆盖文件均位于自己的安装包内，不随仓库分发：

| 文件 | 用途 |
| --- | --- |
| testcase-generator/config.local.yaml | 自己的产品、负责人、业务仓库等可选值 |
| requirement-code-auditor/config.local.yaml | 自己的审计器连接与可选配置 |
| xmind-generator/rules_config.local.json | 自己的项目初始规则与 KYM 映射 |

主入口保持 `config.yaml → config.local.yaml → TESTCASE_* → CLI` 的适用优先级。审计器保持其既有显式配置、`AUDITOR_*` 和 CLI 优先级，不另建配置中心。

GitNexus 地址无内置真实服务值，需要远程审计时由使用者在本机 `gitnexus.url` 或 `AUDITOR_GITNEXUS_URL` 提供。Token 使用 `AUDITOR_GITNEXUS_TOKEN`，不提交、不写入业务产物。缺少地址的远程调用明确返回 `gitnexus_url_missing`、退出码 2；本地代码分析需提供自己的仓库与分支。无代码目标的文档流程保持可用。

## 更新及恢复

结束当前业务运行后操作。只处理用户技能根目录下的 **testing-workflow** 这一个完整包。

1. 核对目标路径、当前版本和来源；对比当前包与上次安装的源码版本，先保存并归并未入库的代码修改。
2. 使用同一个标准安装器，将指定新 commit/tag 安装到系统临时目录中的全新技能根目录（`--dest` 指向该临时目录）。在新包中跑契约及适用回归，失败时保留当前安装。
3. 将当前完整 testing-workflow 备份到本用户的临时或缓存目录，记录旧版本、备份绝对路径和日期。已有备份不得直接覆盖；确认新备份完整后再处理旧备份。
4. 在复制/移动前解析实际绝对路径，确认安装目标是技能根目录的直接子目录 testing-workflow；备份和临时新包处于预定临时/缓存根内。检查根及子项没有指向其他位置的 junction/symlink。
5. 用完整新包替换已确认目标，再恢复上表三个本机覆盖文件（原先存在才复制）。若使用者有自备测试材料，同样先保存并单独恢复，禁止将它们入库。
6. 核对分发文件、版本、入口、配置以及实际运行；记录结果。成功后保留一份上一版完整备份。

标准安装器不会覆盖已存在的目录，因此必须先在临时目录获取和验证新包，再进行受限替换；不得通过清理整个技能根目录来安装。

失败时，将当前失败包移到临时/缓存区域的全新位置，再把完整备份复制回原 testing-workflow 路径，保留备份不消耗。复验契约、实际入口和主流程，并记录恢复版本。

更新和恢复均不改业务需求、评审结论、知识库及历史产物。安装下载、备份和简单 Markdown 安装记录不放在业务项目根，不为安装创建业务 `.ai-internal/testing-workflow-skills`。运行期的需求内部证据目录继续保留。

## 维护者检查

源码只在本 Git 仓库维护；提交和推送前核对独立 remote。业务审计 Git 保护保持原状。重要验证版本使用 tag，日常通过 commit 追溯，不要求 Release 或发布流水线。

在仓库根执行 `python skills/contracts/validate_contracts.py`。分别进入四个组件目录执行既有回归，例如：

```text
python -X utf8 -m unittest discover -s tests -v
```

整包安装后从包根对应组件目录运行同一命令。没有维护者真实业务清单和归档对照时，对应测试明确跳过，不能计为通过。通用安装验证不要求这些私有材料；维护者真实基线在原本机工作区另行验证。

发布前核对实际 Git 跟踪列表，排除本机覆盖、凭据、实际内网默认值、真实业务材料、运行产物和缓存。验证至少覆盖不同临时安装根和不同需求目录，其中含中文及空格，另做本机覆盖保留与完整恢复。安装采用已有 skill-installer，不新增自研下载器、同步系统或分发测试平台。
