"""
OCR 服务，用于处理扫描件 PDF（图片型文档）
改进：不再粗暴全有/全无判断，而是标记每页是否需要 OCR
"""
from __future__ import annotations
import subprocess
import tempfile
from pathlib import Path
from PIL import Image
import logging

from app.config import settings

logger = logging.getLogger(__name__)


def is_tesseract_available() -> bool:
    try:
        subprocess.run(
            [settings.tesseract_cmd, "--version"],
            capture_output=True, timeout=5
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def ocr_image(image: Image.Image, lang: str | None = None) -> str:
    if not is_tesseract_available():
        logger.warning("Tesseract 未安装，OCR 不可用")
        return ""

    lang = lang or settings.ocr_lang
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        image.save(tmp.name, format="PNG")
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [settings.tesseract_cmd, tmp_path, "stdout", "-l", lang, "--psm", "6"],
            capture_output=True, text=True, timeout=60
        )
        text = result.stdout.strip()
        # 过滤典型的 OCR 噪声（全是特殊字符的行）
        lines = text.split("\n")
        filtered = [l for l in lines if not _is_noise_line(l)]
        return "\n".join(filtered)
    except subprocess.TimeoutExpired:
        logger.warning(f"OCR 超时: {tmp_path}")
        return ""
    except Exception as e:
        logger.error(f"OCR 失败: {e}")
        return ""
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _is_noise_line(line: str) -> bool:
    """判断是否为 OCR 噪声行（全是无意义字符）"""
    stripped = line.strip()
    if len(stripped) < 2:
        return True
    # 如果字母/数字/中文字符占比低于 30%，视为噪声
    meaningful = sum(1 for c in stripped if c.isalnum() or "\u4e00" <= c <= "\u9fff")
    return meaningful / max(len(stripped), 1) < 0.3


def ocr_pdf_page(pdf_path: str | Path, page_index: int, dpi: int = 300) -> str:
    try:
        import fitz
    except ImportError:
        logger.warning("PyMuPDF 未安装，无法渲染 PDF 页面")
        return ""

    doc = fitz.open(str(pdf_path))
    if page_index >= len(doc):
        doc.close()
        return ""

    page = doc[page_index]
    pix = page.get_pixmap(dpi=dpi)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    doc.close()

    return ocr_image(img)


def needs_ocr(pdf_path: str | Path, min_meaningful_chars: int = 50) -> bool:
    """
    检测 PDF 是否为扫描件。
    改进：检查前 3 页，只要有一页可提取文本 > min_meaningful_chars 就认为有文字层。
    但仍然可能在后续逐页触发图片 OCR。
    """
    try:
        import pdfplumber
    except ImportError:
        return False

    with pdfplumber.open(str(pdf_path)) as pdf:
        pages_to_check = min(3, len(pdf.pages))
        for page in pdf.pages[:pages_to_check]:
            text = page.extract_text() or ""
            # 统计有意义字符（中英文、数字）
            meaningful = sum(1 for c in text if c.isalnum() or "\u4e00" <= c <= "\u9fff")
            if meaningful >= min_meaningful_chars:
                return False
    return True


def page_text_length(pdf_path: str | Path, page_index: int) -> int:
    """返回指定页的可提取文本长度（用于判断单页是否需要 OCR）"""
    try:
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pdf:
            if page_index >= len(pdf.pages):
                return 0
            text = pdf.pages[page_index].extract_text() or ""
            meaningful = sum(1 for c in text if c.isalnum() or "\u4e00" <= c <= "\u9fff")
            return meaningful
    except Exception:
        return 0
