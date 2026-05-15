import os
import re
from pathlib import Path
from typing import List

from docx import Document
from docx.document import Document as _Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

DEFAULT_PDF_OCR_LANGUAGE = os.environ.get("PDF_OCR_LANGUAGE", "chi_sim+eng")
DEFAULT_PDF_OCR_DPI = int(os.environ.get("PDF_OCR_DPI", "300") or "300")
DEFAULT_PDF_OCR_MIN_TEXT = int(os.environ.get("PDF_OCR_MIN_TEXT", "40") or "40")
KNOWN_TESSDATA_DIRS = (
    r"C:\Program Files\Tesseract-OCR\tessdata",
    r"C:\Program Files (x86)\Tesseract-OCR\tessdata",
    r"C:\Users\86152\AppData\Local\Programs\Tesseract-OCR\tessdata",
)


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", "", text)
    return text.strip()


def iter_block_items(parent):
    if isinstance(parent, _Document):
        parent_elm = parent.element.body
    elif isinstance(parent, _Cell):
        parent_elm = parent._tc
    else:
        raise ValueError("Something's not right")

    for child in parent_elm.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


def chunk_text_lines(full_text: List[str], chunk_size_limit: int = 1000) -> List[str]:
    chunks = []
    current_chunk = []
    current_length = 0
    article_pattern = re.compile(r"^\s*(第[零一二三四五六七八九十百]+条|[一二三四五六七八九十]+、)")

    for line in full_text:
        is_new_article = article_pattern.match(line)
        line_len = len(line)
        should_split_nice = is_new_article and current_length > 300
        should_split_force = (current_length + line_len) > chunk_size_limit

        if should_split_nice or should_split_force:
            chunks.append("\n".join(current_chunk))
            current_chunk = []
            current_length = 0

        current_chunk.append(line)
        current_length += line_len

    if current_chunk:
        chunks.append("\n".join(current_chunk))
    return chunks


def load_docx_lines(file_path: str) -> List[str]:
    if not os.path.exists(file_path):
        print("错误: 文件未找到 -> " + file_path)
        return []

    try:
        doc = Document(file_path)
    except Exception as exc:
        print(f"读取 docx 失败: {exc}")
        return []

    full_text = []
    for block in iter_block_items(doc):
        if isinstance(block, Paragraph):
            cleaned = clean_text(block.text)
            if cleaned:
                full_text.append(cleaned)
        elif isinstance(block, Table):
            for row in block.rows:
                row_cells = [clean_text(cell.text) for cell in row.cells]
                if any(row_cells):
                    full_text.append(f"[表格内容] {' | '.join(row_cells)}")
    return full_text


def load_pdf_lines(file_path: str) -> List[str]:
    if not os.path.exists(file_path):
        print("错误: 文件未找到 -> " + file_path)
        return []

    try:
        import fitz
    except Exception as exc:
        print(f"PDF 解析依赖不可用: {exc}")
        return []

    full_text = []
    ocr_warning_printed = False
    try:
        with fitz.open(file_path) as pdf:
            for page in pdf:
                text = page.get_text("text") or ""
                lines = [clean_text(line) for line in text.splitlines()]
                lines = [line for line in lines if line]

                if _pdf_page_needs_ocr(lines):
                    ocr_lines = try_ocr_pdf_page(page)
                    if ocr_lines:
                        lines = ocr_lines
                    elif not ocr_warning_printed:
                        print("提示: 当前未检测到可用的 Tesseract/tessdata，扫描版 PDF 将无法 OCR。")
                        ocr_warning_printed = True

                for line in lines:
                    cleaned = clean_text(line)
                    if cleaned:
                        full_text.append(cleaned)
    except Exception as exc:
        print(f"读取 PDF 失败: {exc}")
        return []
    return full_text


def _pdf_page_needs_ocr(lines: List[str]) -> bool:
    if not lines:
        return True
    joined = "".join(lines).strip()
    return len(joined) < DEFAULT_PDF_OCR_MIN_TEXT


def resolve_tessdata_path() -> str | None:
    env_path = str(os.environ.get("TESSDATA_PREFIX", "") or "").strip()
    if env_path and Path(env_path).exists():
        return env_path

    for path in KNOWN_TESSDATA_DIRS:
        if Path(path).exists():
            return path
    return None


def try_ocr_pdf_page(page) -> List[str]:
    tessdata = resolve_tessdata_path()
    if not tessdata:
        return []

    try:
        textpage = page.get_textpage_ocr(
            language=DEFAULT_PDF_OCR_LANGUAGE,
            dpi=DEFAULT_PDF_OCR_DPI,
            full=True,
            tessdata=tessdata,
        )
        text = page.get_text("text", textpage=textpage) or ""
    except Exception as exc:
        print(f"扫描页 OCR 失败: {exc}")
        return []

    lines = [clean_text(line) for line in text.splitlines()]
    return [line for line in lines if line]


def load_and_chunk_docx(file_path: str, chunk_size_limit: int = 1000) -> List[str]:
    full_text = load_docx_lines(file_path)
    chunks = chunk_text_lines(full_text, chunk_size_limit=chunk_size_limit)
    print(f"已加载 {os.path.basename(file_path)}, 共 {len(full_text)} 行, 切分为 {len(chunks)} 个块")
    return chunks


def load_and_chunk_pdf(file_path: str, chunk_size_limit: int = 1000) -> List[str]:
    full_text = load_pdf_lines(file_path)
    chunks = chunk_text_lines(full_text, chunk_size_limit=chunk_size_limit)
    print(f"已加载 {os.path.basename(file_path)}, 共 {len(full_text)} 行, 切分为 {len(chunks)} 个块")
    return chunks


def load_and_chunk_document(file_path: str, chunk_size_limit: int = 1000) -> List[str]:
    suffix = Path(file_path).suffix.lower()
    if suffix == ".docx":
        return load_and_chunk_docx(file_path, chunk_size_limit=chunk_size_limit)
    if suffix == ".pdf":
        return load_and_chunk_pdf(file_path, chunk_size_limit=chunk_size_limit)
    print(f"暂不支持的文件类型: {file_path}")
    return []


if __name__ == "__main__":
    print("Data Loader loaded")
