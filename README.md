# testing-workflow-skills

testing-workflow-skills 用于 Codex 的测试工作流，与已有 jwy-testi-expert（WorkBuddy/VS Code 五步流水线）独立并存。两者分别维护、安装和更新。

本仓库的公开主入口是 `skills/testcase-generator/SKILL.md`，运行 CLI 是 `skills/testcase-generator/scripts/main.py`。四个技能与 `contracts` 必须来自同一 commit，保持同级目录。GitHub 仓库为 [Izeril/testing-workflow-skills](https://github.com/Izeril/testing-workflow-skills)，按私有仓库维护。

## 目录与维护范围

```text
skills/
  testcase-generator/       主入口、编排、提示词、模板、回归
  doc-to-md/                MHTML/HTML/OLE2 Word 转换
  requirement-code-auditor/ 需求事实、代码分析、五分类审计
  xmind-generator/          评审版及可导入 XMind
  contracts/                中央入口、产物 schema、验证器
```

本机源码：`D:\PythonWorkSpace\com\testing-workflow-skills`。首版安装：`D:\用户故事\AI_Project\.codex\skills` 下的上述五个目录。备份、下载副本与安装记录：`D:\用户故事\AI_Project\.ai-internal\testing-workflow-skills`。不将整个业务工作区加入 Git。

维护仅在本仓库进行。业务仓库的 Git 保护继续生效：审计器拒绝写命令；主流程只允许现有明确授权的定向 fetch。不能通过删除保护来满足技能仓库的提交需要。

用户级同名技能保留，不按名称清理。当前工作区已在会话技能清单中提供项目级 `.codex/skills` 路径；使用时明确指定该 `SKILL.md` 并核对 `cross_skill_adapter.SKILLS_ROOT`。其他 Codex 版本的发现位置应按 [官方技能文档](https://learn.chatgpt.com/docs/build-skills) 和实际会话清单核对，不能将复制成功当成已启用。

## 依赖与配置

Python 3.10+、Git；首次基线已用 Python 3.13.5 验证。使用已有兼容环境；缺少依赖时安装：

```powershell
python -m pip install -r requirements.txt
```

`PyYAML` 负责配置；`markdownify` 负责 HTML/MHTML 转换。`tree-sitter` + `tree-sitter-java` 为当前验证过的 Java 语法解析实现；`tree-sitter-languages` 是代码中的可选回退，不是新增必需依赖。OLE2 二进制 `.doc` 需要外部 LibreOffice/soffice；缺失时明确返回依赖错误。HTML/MHTML 转换不需要 LibreOffice。

`testcase-generator` 配置顺序保持为通用 `config.yaml` → 本机 `config.local.yaml` → `TESTCASE_*` → CLI。`requirement-code-auditor` 保持原有本机覆盖、显式配置及 `AUDITOR_*` 优先级。未新增配置中心或 `TESTCASE_CONFIG_PATH`。

审计器通用配置不含实际内网地址。首次安装由管理员在 **安装目录** `requirement-code-auditor/config.local.yaml` 写入实际 `gitnexus.url`，或使用 `AUDITOR_GITNEXUS_URL`。Token 使用 `AUDITOR_GITNEXUS_TOKEN`，不提交、不写入产物。没有地址的远程调用返回 `gitnexus_url_missing`、退出码 2；显式本地 Git 和注入测试客户端继续可用。

两技能的 `config.local.yaml`、XMind 的 `rules_config.local.json`、真实业务基线清单、业务材料、知识库和运行产物均不跟踪。本仓库不运行 jwy 安装目录中的代码。归档 `code-analyzer` / `req-code-comparator` 仅用于本机测试对照，不属于生产依赖。

## 检查方式

在本仓库根目录执行中央契约：

```powershell
python skills/contracts/validate_contracts.py
```

分别进入以下目录运行既有回归；每条命令后检查退出码，失败时停止发布或安装：

```powershell
Push-Location skills/testcase-generator
python -X utf8 -m unittest discover -s tests -v
Pop-Location
Push-Location skills/doc-to-md
python -X utf8 -m unittest discover -s tests -v
Pop-Location
Push-Location skills/requirement-code-auditor
python -X utf8 -m unittest discover -s tests -v
Pop-Location
Push-Location skills/xmind-generator
python -X utf8 -m unittest discover -s tests -v
Pop-Location
```

纯下载副本中，真实业务基线及归档对照缺失会明确显示 `skipped`，不计为通过；其余合成测试继续执行。真实样本在本机工作区单独验证，不上传正文。本机保留的四份基线清单可以放回安装目录相应 `tests/fixtures` 位置；这属于本机测试材料，并由 `.gitignore` 排除。

## 首次下载与安装

1. 当前需求运行结束后操作。确认 GitHub 身份、私有仓库权限、安装根目录和五组件清单。
2. 将当前五组件完整备份到上述本机记录目录的 `previous`，包含两个组件的本机配置与真实测试清单。备份已存在时先归档它，禁止覆盖唯一可用备份。
3. 对比当前安装与上次记录的 commit 下载副本，先保存并归并未入库代码改动。不要只看版本号。
4. 下载明确版本到全新目录；不要在运行中的安装目录执行 Git checkout。

```powershell
$download = 'D:\用户故事\AI_Project\.ai-internal\testing-workflow-skills\download-v1.0.0'
git clone --no-checkout https://github.com/Izeril/testing-workflow-skills.git $download
# 确认上一条成功，再继续。
git -C $download checkout --detach v1.0.0
git -C $download rev-parse HEAD
git -C $download remote -v
```

5. 在完整下载目录运行上述契约和回归，确认五组件及资源齐全；检查失败时保留安装现状。
6. 同步前验证五个实际绝对路径都是安装根目录的直接子目录，且没有符号链接/junction 指向别处。复制、移动或清理只处理五个指定组件，不替换整个 `.codex/skills`。
7. 保留本机配置后，逐个替换指定组件。下面的手工片段假定 `previous` 已成功备份且下载检查全部通过；执行前填入已验证的版本下载目录。

```powershell
$ErrorActionPreference = 'Stop'
$install = [IO.Path]::GetFullPath('D:\用户故事\AI_Project\.codex\skills')
$download = [IO.Path]::GetFullPath('D:\用户故事\AI_Project\.ai-internal\testing-workflow-skills\download-v1.0.0')
$previous = [IO.Path]::GetFullPath('D:\用户故事\AI_Project\.ai-internal\testing-workflow-skills\previous')
$components = @('testcase-generator','doc-to-md','requirement-code-auditor','xmind-generator','contracts')
# 先对所有源/备份/目标完成检查，再开始任何替换。
foreach ($name in $components) {
    $target = [IO.Path]::GetFullPath((Join-Path $install $name))
    if ((Split-Path $target -Parent) -ne $install) { throw '目标越界' }
    foreach ($path in @($target, (Join-Path "$download\skills" $name), (Join-Path $previous $name))) {
        if (-not (Test-Path -LiteralPath $path -PathType Container)) { throw "缺少组件: $path" }
        $items = @((Get-Item -LiteralPath $path)) + @(Get-ChildItem -LiteralPath $path -Force -Recurse)
        if ($items | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }) { throw "存在重解析路径: $path" }
    }
}
foreach ($name in $components) {
    $target = [IO.Path]::GetFullPath((Join-Path $install $name))
    if ((Split-Path $target -Parent) -ne $install) { throw '目标越界' }
    Remove-Item -LiteralPath $target -Recurse -Force
    Copy-Item -LiteralPath (Join-Path "$download\skills" $name) -Destination $install -Recurse
    $localConfig = Join-Path (Join-Path $previous $name) 'config.local.yaml'
    if (Test-Path -LiteralPath $localConfig) { Copy-Item -LiteralPath $localConfig -Destination $target }
}
# 真实基线清单按需从 previous 恢复到相应 tests/fixtures，不覆盖源码文件。
```

XMind 更新时同样从 `previous/xmind-generator/rules_config.local.json` 恢复本机规则。首版从旧规则与嵌入 KYM 映射提取为该文件，保留本机项目行为；通用 `rules_config.json` 只分发公共模板，不包含历史项目学习记录。

8. 再跑安装目录的契约和既有回归，用分开的需求工作副本完成实际主流程。确认实际入口、四技能与 contracts 均来自安装根目录，输出没有落入另一体系目录。保留现有人工评审、终稿和归档门禁。
9. 在 `installation.md` 写明仓库 URL、commit/tag、安装路径、日期、通过/跳过/未执行项目。本机新建的审计器配置须保留实际连接值。
10. 若会话仍显示旧技能来源，开启新聊天或重启后再次核对会话技能清单；旧聊天的缓存不能证明刷新完成。

## 更新与手工恢复

日常维护：在本仓库修改 → 相关既有检查 → `git diff --check` → 检查待跟踪文件 → commit/push → 下载指定 commit/tag → 重复以上备份/检查/受限同步流程。重要且已验证版本使用独立 tag；首版为 `v1.0.0`。不强制 Release、PR 或发布流水线。

更新前不得覆盖尚未入库的安装副本改动。成功更新后留一份上一版备份。同步或安装验证失败时立即停止并手工恢复：先确认 `previous` 五组件齐全、实际路径都在备份根目录内且不含 reparse point；将每个当前安装组件移到本机记录目录中的全新失败副本目录，再把 `previous` 对应组件复制回安装根目录，禁止清理整棵技能根目录。恢复后重新跑契约和主流程，并记录恢复版本；备份副本不被消耗。

恢复范围仅为五组件和本机配置。历史需求文档、评审结论、知识库和已生成产物不会随技能回滚。恢复后首次修改仍在独立源码仓库进行。

## 并存边界与验收

jwy 采用自己的仓库、入口、安装方式、commit/tag 与输出工作副本。本机核对发现该源码与用户级 `jwy-testing-expert-skill` 入口存在；当前机器没有 WorkBuddy 专家插件安装目录，不能把静态文件校验当成 WorkBuddy UI 运行通过。用户级同名 `testcase-generator` 归属不明确，保持原状，当前 Codex 工作区使用明确的项目级路径。

首版验收涵盖：完整下载目录的契约/既有回归；安装后的实际主流程与路径；配置保留和上一版恢复；受保护的 jwy 源码、用户级入口与其他技能文件指纹不变。每项真实运行结果以本机 `installation.md` 为准，不把缺少材料的跳过算成通过。
