"""不持久化用户原文件的本地文本提取。"""
from __future__ import annotations

import io
import platform
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


MAX_UPLOAD_BYTES = 12 * 1024 * 1024
MAX_EXTRACTED_CHARS = 100_000
MAX_DOCX_XML_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 60
SUPPORTED_EXTENSIONS = {".txt", ".md", ".docx", ".pdf", ".png", ".jpg", ".jpeg", ".webp"}


class DocumentParseError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def extract_document(filename: str, content: bytes, content_type: str = "") -> dict[str, Any]:
    """验证上传边界并提取文本，不持久化用户原文件。"""
    safe_name = Path((filename or "").replace("\x00", "")).name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise DocumentParseError(
            "UNSUPPORTED_FILE_TYPE",
            "仅支持 TXT、MD、DOCX、PDF、PNG、JPG 和 WEBP 文件。",
        )
    if not content:
        raise DocumentParseError("EMPTY_FILE", "文件内容为空。")
    if len(content) > MAX_UPLOAD_BYTES:
        raise DocumentParseError("FILE_TOO_LARGE", "单个文件不能超过 12 MB。")

    if suffix in {".txt", ".md"}:
        text = _extract_plain_text(content)
    elif suffix == ".docx":
        text = _extract_docx(content)
    elif suffix == ".pdf":
        text = _extract_pdf(content)
    else:
        text = _extract_image(content, suffix, content_type)

    text = _normalize_text(text)
    if not text:
        raise DocumentParseError("NO_TEXT_FOUND", "未从文件中识别到可用文字。")
    if len(text) > MAX_EXTRACTED_CHARS:
        text = text[:MAX_EXTRACTED_CHARS]
    return {
        "filename": safe_name,
        "file_type": suffix.removeprefix("."),
        "text": text,
        "char_count": len(text),
    }


def _extract_plain_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DocumentParseError("TEXT_ENCODING_ERROR", "文本编码无法识别，请转为 UTF-8 后重试。")


def _extract_docx(content: bytes) -> str:
    if not content.startswith(b"PK"):
        raise DocumentParseError("INVALID_DOCX", "文件扩展名是 DOCX，但内容不是有效的 Word 文档。")
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > MAX_DOCX_XML_BYTES or (
                info.compress_size and info.file_size / info.compress_size > 200
            ):
                raise DocumentParseError("UNSAFE_DOCX", "Word 文档解压后过大，已拒绝解析。")
            xml = archive.read(info)
    except DocumentParseError:
        raise
    except (KeyError, zipfile.BadZipFile, OSError) as exc:
        raise DocumentParseError("INVALID_DOCX", "无法读取 Word 文档，请确认文件未损坏。") from exc

    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise DocumentParseError("INVALID_DOCX", "Word 文档内容损坏。") from exc
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs = []
    for paragraph in root.iter(f"{namespace}p"):
        pieces = []
        for node in paragraph.iter():
            if node.tag == f"{namespace}t" and node.text:
                pieces.append(node.text)
            elif node.tag == f"{namespace}tab":
                pieces.append("\t")
            elif node.tag in {f"{namespace}br", f"{namespace}cr"}:
                pieces.append("\n")
        value = "".join(pieces).strip()
        if value:
            paragraphs.append(value)
    return "\n".join(paragraphs)


def _extract_pdf(content: bytes) -> str:
    if not content.startswith(b"%PDF-"):
        raise DocumentParseError("INVALID_PDF", "文件扩展名是 PDF，但内容不是有效 PDF。")
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise DocumentParseError("PDF_SUPPORT_MISSING", "未安装 PDF 解析依赖，请重新安装项目。") from exc
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise DocumentParseError("ENCRYPTED_PDF", "不支持加密 PDF，请解除密码后重试。")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise DocumentParseError("PDF_TOO_LONG", f"PDF 最多支持 {MAX_PDF_PAGES} 页。")
        return "\n\n".join((page.extract_text() or "") for page in reader.pages)
    except DocumentParseError:
        raise
    except Exception as exc:
        raise DocumentParseError("INVALID_PDF", "PDF 解析失败，请确认文件未损坏。") from exc


def _extract_image(content: bytes, suffix: str, content_type: str = "") -> str:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
            if width * height > 40_000_000:
                raise DocumentParseError("IMAGE_TOO_LARGE", "图片像素过大，请压缩后重试。")
            image.verify()
    except DocumentParseError:
        raise
    except Exception as exc:
        raise DocumentParseError("INVALID_IMAGE", "文件扩展名是图片，但内容无法读取。") from exc

    # Close before an external process opens it: Windows locks open temp files.
    # The directory context removes the image on both success and OCR failure.
    with tempfile.TemporaryDirectory(prefix="interview-ocr-") as directory:
        image_path = Path(directory) / ("input" + suffix)
        image_path.write_bytes(content)
        if platform.system() == "Darwin" and Path("/usr/bin/swift").exists():
            return _ocr_with_macos_vision(str(image_path))
        if shutil.which("tesseract"):
            return _ocr_with_tesseract(str(image_path))
    raise DocumentParseError(
        "OCR_UNAVAILABLE",
        "当前系统没有可用的本地 OCR；macOS 可使用 Vision，其他系统请安装 Tesseract。",
    )


def _ocr_with_macos_vision(path: str) -> str:
    # 脚本作为 package data 与 backend 一起分发，避免非 editable 安装丢失 OCR 资源。
    script = Path(__file__).resolve().parent / "ocr_image.swift"
    try:
        result = subprocess.run(
            ["/usr/bin/swift", str(script), path],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        return result.stdout
    except (subprocess.SubprocessError, OSError) as exc:
        raise DocumentParseError("OCR_FAILED", "本地 Vision 文字识别失败，请使用更清晰的图片。") from exc


def _ocr_with_tesseract(path: str) -> str:
    try:
        result = subprocess.run(
            ["tesseract", path, "stdout", "-l", "chi_sim+eng"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
        return result.stdout
    except (subprocess.SubprocessError, OSError) as exc:
        raise DocumentParseError("OCR_FAILED", "Tesseract 文字识别失败。") from exc


def _normalize_text(text: str) -> str:
    lines = [line.rstrip() for line in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    result = []
    blank = False
    for line in lines:
        if line.strip():
            result.append(line.strip())
            blank = False
        elif not blank and result:
            result.append("")
            blank = True
    return "\n".join(result).strip()
