---
name: doc-to-md
description: 将旧版 Word `.doc` 文件，包括 Word 导出的 MHTML/HTML 和可由 LibreOffice 转换的 OLE2 二进制文档，转换为干净的 UTF-8 Markdown 并保留中文内容。适用于需求分析、评审或测试生成前规范化需求、设计或测试文档。
---

# DOC 转 Markdown

在下游分析前使用本技能规范化旧版 `.doc` 文档。

## 工作流程

1. 运行脚本；脚本依据 magic bytes/文件头识别 OLE2、MHTML 或 HTML，不依赖扩展名猜测。
2. MHTML/HTML 直接解析；MHTML 内嵌图片按 CID/Content-Location/相对引用关联并经魔数验证后落盘；OLE2 自动探测 LibreOffice/soffice 并先转换为 HTML。
3. 未检测到 OLE2 转换器时立即失败，不创建 Markdown，并按错误消息安装 LibreOffice、指定转换器，或用 Word 另存为网页/MHTML。
4. 转换后检查 Markdown 是否存在乱码、表格缺失或结构丢失，再将其作为分析证据。

## 脚本用法

```powershell
python .\doc_to_md.py "<input.doc>" "[output_dir]"
python .\doc_to_md.py "<input.doc>" "[output_dir]" --converter "C:\Program Files\LibreOffice\program\soffice.exe"
```

脚本入口自行将标准输出和错误输出配置为 UTF-8，不需要设置 `PYTHONIOENCODING`。

## MHTML 图片附件

- 内嵌图片输出到 `<文档名>_assets/`，Markdown 只写相对路径；文件名由顺序和 SHA-256 摘要生成，禁止使用附件原始路径。
- 支持 PNG、JPEG、GIF、BMP、WebP 和 TIFF；`application/octet-stream` 只有在图片魔数有效时才允许。
- 缺失内嵌图片、重复 CID/Content-Location 对应不同内容、MIME 与魔数冲突、输出目录类型冲突均阻断转换，且不得生成 Markdown。
- HTTP(S)、站点绝对路径和 data URI 不冒充内嵌附件；保留为原引用并进入下游来源清单判断。

## OLE2 失败路径

出现“检测到 OLE2”且没有转换器时选择一种处理方式：

- 安装 LibreOffice，并确保 `soffice` 在 `PATH` 中。
- 通过 `--converter` 指定 `soffice.exe`。
- 用 Microsoft Word 打开原文件，另存为“网页/筛选过的网页”或 MHTML，再重新转换。

不得把解析失败、空内容或仅含错误提示的 Markdown 当作转换成功产物。

## 依赖与限制

- 缺少 `markdownify` 且安装涉及网络或环境变更时，先获得用户确认。
- MHTML 内嵌位图可安全落盘；OLE2 经 LibreOffice 生成的外部图片目录当前不随临时 HTML 保留，复杂表格仍需对照原文复核。
- 内容风险较高时，对照原文验证 Markdown。

## 错误契约

CLI 成功返回 0；未分类异常返回 1；输入或显式转换器路径无效返回 2；LibreOffice、`markdownify` 等外部依赖失败返回 4；文档格式、附件或输出冲突导致的转换失败返回 6；用户中断返回 130。失败信息以 `error_payload` 1.0 JSON 写入 stderr，至少包含 `schema_version`、`error_code`、`exit_code`、`message` 和 `context`。

## 公共契约

本技能的公开入口与产物版本由 `../contracts/skill_contracts.json` 统一登记。修改公开函数名、参数、产物必填字段或 schema 版本时，必须同步清单并运行 `python ..\contracts\validate_contracts.py`。
