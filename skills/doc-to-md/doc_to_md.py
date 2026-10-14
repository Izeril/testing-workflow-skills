"""将 MHTML、HTML 或 OLE2 Word .doc 转换为 UTF-8 Markdown。"""

import argparse
import email
from email import policy
import hashlib
import html as html_lib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from urllib.parse import unquote, urlparse


OLE2_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")
DECODE_ENCODINGS = ("utf-8", "gb18030", "gbk", "gb2312")


class DocToMarkdownError(RuntimeError):
    """Base error for the stable doc-to-md CLI contract."""

    error_code = "doc_to_md_failed"
    exit_code = 1

    def __init__(self, message, *, context=None):
        super().__init__(message)
        self.context = dict(context or {})

    def as_dict(self):
        return {
            "schema_version": "1.0.0",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "error_code": self.error_code,
            "exit_code": self.exit_code,
            "error_type": type(self).__name__,
            "message": str(self),
            "context": self.context,
        }


class InputError(DocToMarkdownError):
    error_code = "invalid_input"
    exit_code = 2


class ConversionError(DocToMarkdownError):
    error_code = "conversion_failed"
    exit_code = 6


class UnsupportedDocFormatError(ConversionError):
    """输入格式无法由当前环境安全转换。"""


class ExternalDependencyError(UnsupportedDocFormatError):
    error_code = "external_dependency_failed"
    exit_code = 4


class AttachmentValidationError(UnsupportedDocFormatError):
    """MHTML 图片附件缺失、冲突或内容类型不可信。"""


class OutputConflictError(UnsupportedDocFormatError):
    """输出路径类型与转换产物冲突。"""

def configure_utf8_stdio():
    """让 Windows CLI 不依赖 PYTHONIOENCODING 也能稳定输出中文。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def detect_doc_format(input_path):
    """依据 magic bytes/文件头识别 OLE2、MHTML、HTML 或未知格式。"""
    with open(input_path, "rb") as handle:
        header = handle.read(8192)
    if header.startswith(OLE2_MAGIC):
        return "ole2"
    lowered = header.lstrip().lower()
    if b"mime-version:" in lowered or b"content-type: multipart/" in lowered:
        return "mhtml"
    if re.search(br"<(?:!doctype\s+html|html)\b", lowered):
        return "html"
    return "unknown"


def find_libreoffice(explicit_path=None):
    """返回可执行的 LibreOffice/soffice 路径，找不到时返回 None。"""
    if explicit_path:
        candidate = Path(explicit_path).expanduser()
        if not candidate.is_file():
            raise InputError(f"指定的 LibreOffice 转换器不存在: {candidate}", context={"converter": str(candidate)})
        return str(candidate)

    candidates = []
    for command in ("soffice", "libreoffice"):
        found = shutil.which(command)
        if found:
            candidates.append(found)

    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        root = os.environ.get(env_name)
        if not root:
            continue
        candidates.extend(
            [
                str(Path(root) / "LibreOffice" / "program" / "soffice.exe"),
                str(Path(root) / "Programs" / "LibreOffice" / "program" / "soffice.exe"),
            ]
        )

    return next((path for path in candidates if Path(path).is_file()), None)


def _decode_bytes(raw, preferred=None):
    encodings = []
    if preferred:
        encodings.append(preferred)
    encodings.extend(encoding for encoding in DECODE_ENCODINGS if encoding not in encodings)
    for encoding in encodings:
        try:
            return raw.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8-replace"


_IMAGE_TAG_RE = re.compile(r"<(?:img|v:imagedata)\b[^>]*>", re.IGNORECASE | re.DOTALL)
_SRC_ATTR_RE = re.compile(
    r"\bsrc\s*=\s*(?:(['\"])(.*?)\1|([^\s>]+))",
    re.IGNORECASE | re.DOTALL,
)


def _attachment_aliases(*values):
    """Return normalized lookup keys for CID, Content-Location and HTML src."""
    aliases = set()
    for value in values:
        if value is None:
            continue
        decoded = html_lib.unescape(str(value).strip().strip("<>"))
        if not decoded:
            continue
        candidates = [decoded]
        if decoded.casefold().startswith("cid:"):
            candidates.append(decoded[4:])
        parsed = urlparse(decoded)
        if parsed.scheme.casefold() == "file":
            candidates.append(unquote(parsed.path))
        clean = decoded.split("#", 1)[0].split("?", 1)[0].rstrip("/")
        if clean:
            candidates.append(clean)
        for candidate in candidates:
            normalized = unquote(candidate).replace("\\", "/").strip().strip("<>")
            if not normalized:
                continue
            aliases.add(normalized.casefold())
            basename = normalized.rsplit("/", 1)[-1]
            if basename:
                aliases.add(basename.casefold())
    return aliases


def _sniff_image(data, declared_type):
    """Validate image bytes and return a safe extension plus canonical MIME."""
    signatures = (
        ("png", "image/png", lambda raw: raw.startswith(b"\x89PNG\r\n\x1a\n")),
        ("jpg", "image/jpeg", lambda raw: raw.startswith(b"\xff\xd8\xff")),
        ("gif", "image/gif", lambda raw: raw.startswith((b"GIF87a", b"GIF89a"))),
        ("bmp", "image/bmp", lambda raw: raw.startswith(b"BM")),
        ("webp", "image/webp", lambda raw: len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"),
        ("tiff", "image/tiff", lambda raw: raw.startswith((b"II*\x00", b"MM\x00*"))),
    )
    detected = next(((ext, mime) for ext, mime, matcher in signatures if matcher(data)), None)
    if not detected:
        raise AttachmentValidationError("图片附件魔数不受支持或内容为空，未生成 Markdown。")
    extension, canonical_mime = detected
    declared = (declared_type or "application/octet-stream").split(";", 1)[0].strip().casefold()
    aliases = {"application/octet-stream", "application/x-png", canonical_mime}
    if canonical_mime == "image/jpeg":
        aliases.add("image/jpg")
    if declared not in aliases:
        raise AttachmentValidationError(
            f"图片附件 MIME 与内容不一致或不受支持: declared={declared or 'missing'}, detected={canonical_mime}"
        )
    return extension, canonical_mime


def _extract_mhtml(input_path):
    with open(input_path, "rb") as handle:
        message = email.message_from_binary_file(handle, policy=policy.default)

    html_content = ""
    for part in message.walk() if message.is_multipart() else (message,):
        if part.get_content_type() != "text/html" or html_content:
            continue
        raw = part.get_payload(decode=True)
        if raw:
            html_content, encoding = _decode_bytes(raw, part.get_content_charset())
            print(f"使用编码: {encoding}")
        else:
            html_content = part.get_content()

    if not html_content:
        raise UnsupportedDocFormatError("MHTML 文档中没有可转换的 text/html 内容，未生成 Markdown。")

    registry = {}
    parts = message.walk() if message.is_multipart() else (message,)
    for part in parts:
        if part.is_multipart() or part.get_content_type() == "text/html":
            continue
        content_id = (part.get("Content-ID") or "").strip("<>")
        location = part.get("Content-Location") or ""
        filename = part.get_filename() or ""
        aliases = _attachment_aliases(content_id, location, filename)
        if not aliases:
            continue
        data = part.get_payload(decode=True) or b""
        digest = hashlib.sha256(data).hexdigest()
        record = {
            "data": data,
            "declared_type": part.get_content_type(),
            "digest": digest,
            "content_id": content_id,
            "content_location": location,
        }
        for alias in aliases:
            previous = registry.get(alias)
            if previous and previous["digest"] != digest:
                raise AttachmentValidationError(
                    f"MHTML 存在重复图片标识且内容不同: {alias}，未生成 Markdown。"
                )
            registry[alias] = previous or record
    return html_content, registry


def _requires_embedded_image(reference):
    value = html_lib.unescape(reference or "").strip()
    lowered = value.casefold()
    if not value or lowered.startswith(("http://", "https://", "data:", "//", "#", "/")):
        return False
    return True


def _rewrite_embedded_images(html_content, attachment_registry, asset_dir_name):
    """Rewrite embedded MHTML image references to safe relative asset paths."""
    published = {}
    missing = []

    def replace_tag(match):
        tag = match.group(0)
        src_match = _SRC_ATTR_RE.search(tag)
        if not src_match:
            return tag
        reference = src_match.group(2) if src_match.group(1) else src_match.group(3)
        record = next(
            (attachment_registry[key] for key in _attachment_aliases(reference) if key in attachment_registry),
            None,
        )
        if record is None:
            if _requires_embedded_image(reference):
                missing.append(html_lib.unescape(reference))
            return tag
        extension, canonical_mime = _sniff_image(record["data"], record["declared_type"])
        digest = record["digest"]
        if digest not in published:
            published[digest] = {
                **record,
                "extension": extension,
                "canonical_mime": canonical_mime,
                "filename": f"image-{len(published) + 1:03d}-{digest[:12]}.{extension}",
            }
        relative_path = f"{asset_dir_name}/{published[digest]['filename']}"
        if tag.lstrip().casefold().startswith("<v:imagedata"):
            return f'<img src="{relative_path}" alt="文档图片">'
        return tag[:src_match.start()] + f'src="{relative_path}"' + tag[src_match.end():]

    rewritten = _IMAGE_TAG_RE.sub(replace_tag, html_content)
    if missing:
        refs = "、".join(sorted(set(missing))[:5])
        raise AttachmentValidationError(f"MHTML 引用了缺失的内嵌图片: {refs}，未生成 Markdown。")
    return rewritten, list(published.values())

def _extract_html(input_path):
    raw = Path(input_path).read_bytes()
    html_content, encoding = _decode_bytes(raw)
    print(f"使用编码: {encoding}")
    return html_content, {}


def _ole2_error(input_path):
    return ExternalDependencyError(
        "检测到 OLE2 二进制 Word .doc，但当前环境未找到可用的 LibreOffice/soffice 转换器；"
        "未生成 Markdown。解决路径：1）安装 LibreOffice 并将 soffice 加入 PATH 后重试；"
        "2）用 Microsoft Word 打开该文件，另存为“网页/筛选过的网页”或 MHTML 后重试；"
        f"3）通过 --converter 指定 soffice.exe。输入文件: {input_path}"
    )


def _convert_ole2_to_html(input_path, converter_path=None):
    converter = find_libreoffice(converter_path)
    if not converter:
        raise _ole2_error(input_path)

    with tempfile.TemporaryDirectory(prefix="doc-to-md-") as temp_dir:
        command = [
            converter,
            "--headless",
            "--convert-to",
            "html",
            "--outdir",
            temp_dir,
            str(Path(input_path).resolve()),
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ExternalDependencyError(
                f"LibreOffice 转换 OLE2 .doc 失败，未生成 Markdown: {exc}",
                context={"dependency": "LibreOffice", "original_type": type(exc).__name__},
            ) from exc

        html_files = sorted(Path(temp_dir).glob("*.htm*"))
        if completed.returncode != 0 or not html_files:
            detail = (completed.stderr or completed.stdout or "未生成 HTML").strip()
            raise UnsupportedDocFormatError(
                f"LibreOffice 无法转换该 OLE2 .doc，未生成 Markdown。"
                f"可用 Word 另存为网页/MHTML 后重试。转换器输出: {detail}"
            )
        return _extract_html(html_files[0])


def _clean_html(html_content):
    html_content = re.sub(r"<style[^>]*>.*?</style>", "", html_content, flags=re.DOTALL | re.IGNORECASE)
    html_content = re.sub(r"@font-face\{[^}]*\}", "", html_content)
    html_content = re.sub(r"@list[^{]*\{[^}]*\}", "", html_content)
    html_content = re.sub(r"<!--\[if[^\]]*\]>.*?<!\[endif\]-->", "", html_content, flags=re.DOTALL)
    html_content = re.sub(r"<\?xml[^>]*\?>", "", html_content)
    html_content = re.sub(r"<meta[^>]*>", "", html_content, flags=re.IGNORECASE)
    html_content = re.sub(r"<!DOCTYPE[^>]*>", "", html_content, flags=re.IGNORECASE)
    html_content = re.sub(r"</?v:shape\b[^>]*>", "", html_content, flags=re.IGNORECASE)
    html_content = re.sub(r"<v:imagedata\b[^>]*/?>", "![图片缺失]", html_content, flags=re.IGNORECASE)
    html_content = html_content.replace("&nbsp;", " ")
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", html_content)


def _html_to_markdown(html_content, attachments):
    try:
        from markdownify import markdownify
    except ImportError as exc:
        raise ExternalDependencyError(
            "缺少 markdownify；请安装依赖后重试，未生成 Markdown。",
            context={"dependency": "markdownify"},
        ) from exc

    cleaned = _clean_html(html_content)
    markdown_content = markdownify(
        cleaned,
        heading_style="ATX",
        strip=["script", "style", "meta"],
    )
    markdown_content = re.sub(r"^(#{1,6})\s*\*+\s*", r"\1 ", markdown_content, flags=re.MULTILINE)

    links = sorted(set(re.findall(r'href=["\'](https?://[^"\']+)["\']', cleaned)))

    if links:
        markdown_content += "\n\n---\n\n## 文档中的链接\n\n"
        markdown_content += "".join(f"{index}. {link}\n" for index, link in enumerate(links, 1))

    markdown_content = re.sub(r"\n{3,}", "\n\n", markdown_content)
    markdown_content = re.sub(r"[ \t]+$", "", markdown_content, flags=re.MULTILINE)
    return markdown_content.strip(), cleaned, links


def validate_markdown_content(markdown_content, source_html="", expected_image_count=0):
    """转换完成但写盘前执行内容质量门禁，防止乱码、空文档或表格静默丢失。"""
    issues = []
    visible = re.sub(r"\s+", "", markdown_content or "")
    if len(visible) < 8:
        issues.append(f"有效内容过少（{len(visible)} 字符）")

    replacement_count = markdown_content.count("\ufffd")
    if "锟斤拷" in markdown_content or replacement_count > max(2, len(markdown_content) // 500):
        issues.append("检测到疑似乱码或过多 Unicode 替换字符")

    source_has_chinese = any("\u4e00" <= char <= "\u9fff" for char in source_html)
    markdown_has_chinese = any("\u4e00" <= char <= "\u9fff" for char in markdown_content)
    if source_has_chinese and not markdown_has_chinese:
        issues.append("源文档包含中文，但 Markdown 未保留中文内容")

    source_table_count = len(re.findall(r"<table\b", source_html, re.IGNORECASE))
    markdown_table_rows = sum(
        1 for line in markdown_content.splitlines()
        if line.count("|") >= 2
    )
    if source_table_count and markdown_table_rows < 2:
        issues.append(
            f"源文档包含 {source_table_count} 个表格，但 Markdown 未检测到有效表格行"
        )

    markdown_image_count = len(re.findall(r"!\[[^\]]*\]\([^)]+\)", markdown_content))
    if expected_image_count and markdown_image_count < expected_image_count:
        issues.append(
            f"已提取 {expected_image_count} 张图片，但 Markdown 仅保留 {markdown_image_count} 个图片引用"
        )

    report = {
        "status": "passed" if not issues else "failed",
        "issues": issues,
        "markdown_length": len(markdown_content),
        "source_table_count": source_table_count,
        "markdown_table_rows": markdown_table_rows,
        "source_has_chinese": source_has_chinese,
        "markdown_has_chinese": markdown_has_chinese,
        "expected_image_count": expected_image_count,
        "markdown_image_count": markdown_image_count,
    }
    if issues:
        raise UnsupportedDocFormatError(
            "转换内容质量门禁未通过，未生成 Markdown：" + "；".join(issues)
        )
    return report


def _atomic_write(path, data, *, binary=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    os.close(descriptor)
    temp_path = Path(temp_name)
    try:
        if binary:
            temp_path.write_bytes(data)
        else:
            temp_path.write_text(data, encoding="utf-8")
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def convert_doc_to_md(input_path, output_dir=None, converter_path=None):
    input_file = Path(input_path)
    if not input_file.is_file():
        raise InputError(f"输入文件不存在: {input_file}", context={"input_path": str(input_file)})

    destination = Path(output_dir) if output_dir else input_file.parent
    if destination.exists() and not destination.is_dir():
        raise OutputConflictError(f"输出目录与已有文件冲突: {destination}")
    output_path = destination / f"{input_file.stem}.md"
    if output_path.exists() and not output_path.is_file():
        raise OutputConflictError(f"Markdown 输出路径与已有目录冲突: {output_path}")
    asset_dir_name = f"{input_file.stem}_assets"
    asset_dir = destination / asset_dir_name
    if asset_dir.exists() and not asset_dir.is_dir():
        raise OutputConflictError(f"图片输出目录与已有文件冲突: {asset_dir}")

    doc_format = detect_doc_format(input_file)
    if doc_format == "ole2":
        html_content, attachments = _convert_ole2_to_html(input_file, converter_path)
        embedded_images = []
    elif doc_format == "mhtml":
        html_content, attachments = _extract_mhtml(input_file)
        html_content, embedded_images = _rewrite_embedded_images(
            html_content, attachments, asset_dir_name
        )
    elif doc_format == "html":
        html_content, attachments = _extract_html(input_file)
        embedded_images = []
    else:
        raise UnsupportedDocFormatError(
            "无法识别输入文件格式；仅支持 MHTML、HTML 或可由 LibreOffice 转换的 OLE2 .doc。"
            "未生成 Markdown。"
        )

    markdown_content, cleaned_html, links = _html_to_markdown(html_content, embedded_images)
    quality_report = validate_markdown_content(
        markdown_content, cleaned_html, expected_image_count=len(embedded_images)
    )

    destination.mkdir(parents=True, exist_ok=True)
    for image in embedded_images:
        _atomic_write(asset_dir / image["filename"], image["data"], binary=True)
    _atomic_write(output_path, markdown_content)

    print(f"转换成功: {output_path}")
    print(f"原始 HTML 长度: {len(cleaned_html)} 字符")
    print(f"Markdown 长度: {len(markdown_content)} 字符")
    if links:
        print(f"提取链接: {len(links)} 个")
    if embedded_images:
        print(f"图片附件落盘: {len(embedded_images)} 张 -> {asset_dir}")
    print(
        "内容质量门禁: 通过"
        f"（中文保留={quality_report['markdown_has_chinese']}，"
        f"表格行={quality_report['markdown_table_rows']}，"
        f"图片引用={quality_report['markdown_image_count']}）"
    )
    return str(output_path)

def _unexpected_error(exc):
    return {
        "schema_version": "1.0.0",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "error_code": "unexpected_error",
        "exit_code": 1,
        "error_type": type(exc).__name__,
        "message": str(exc) or type(exc).__name__,
        "context": {"original_type": type(exc).__name__},
    }


def main(argv=None):
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="将 MHTML、HTML 或 OLE2 Word .doc 转换为 Markdown")
    parser.add_argument("input_doc", help="输入 .doc 文件")
    parser.add_argument("output_dir", nargs="?", help="输出目录，默认与输入文件相同")
    parser.add_argument("--converter", help="可选的 LibreOffice soffice 可执行文件路径")
    args = parser.parse_args(argv)
    try:
        convert_doc_to_md(args.input_doc, args.output_dir, args.converter)
        return 0
    except KeyboardInterrupt:
        return 130
    except DocToMarkdownError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False), file=sys.stderr)
        return exc.exit_code
    except Exception as exc:
        print(json.dumps(_unexpected_error(exc), ensure_ascii=False), file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
