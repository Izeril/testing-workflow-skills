# 配置参考

普通用户无需修改配置文件。默认入口只需要需求目录：

```powershell
python scripts/cli.py "D:\需求目录"
```

审计器会读取提测文档中的开发分支和服务，调用 GitNexus `list_repos`，按仓库标识或服务别名进行精确自动发现。匹配不到或匹配到多个仓库时阻断，禁止模糊猜测。

## 内置默认值

默认代码来源为 GitNexus。分发包不包含实际服务地址；首次安装由管理员在本机 `config.local.yaml` 配置，或设置 `AUDITOR_GITNEXUS_URL`。已有本机配置会在更新时保留。

```yaml
# config.local.yaml（不得提交）
gitnexus:
  url: "https://gitnexus.example.invalid/api/mcp"  # 替换为本机实际服务地址
```

未配置地址时，远程模式返回 `gitnexus_url_missing` 和退出码 2，不会隐式访问其他服务或回退本地仓库。

普通用户不需要配置 `repo.targets`、`repo_key` 或本地代码路径。

## 管理员可选覆盖

部署管理员可以使用环境变量覆盖默认值：

```text
AUDITOR_CODE_PROVIDER
AUDITOR_GITNEXUS_URL
AUDITOR_GITNEXUS_TOKEN
AUDITOR_GITNEXUS_TIMEOUT
```

Token 只从环境变量读取，不写入 Skill 配置或审计产物。

## 自动发现输入

提测文档至少应包含：

```text
涉及开发分支：feature/xxx
涉及服务：jwy3
```

也可以显式提供：

```text
代码仓库：jwy3
```

服务/仓库与 GitNexus `list_repos` 元数据必须唯一匹配；这属于系统安全门禁，不是要求用户改配置。

## 本地兼容模式

只有排障或对照验证时才显式启用本地 Git：

```powershell
$env:AUDITOR_CODE_PROVIDER = "local_git"
python scripts/cli.py "D:\需求目录" --repo "D:\本地代码仓库" --branch "feature/xxx"
```
