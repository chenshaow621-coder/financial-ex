import re
import os
from typing import List
from docx import Document
from docx.document import Document as _Document
from docx.oxml.text.paragraph import CT_P
from docx.oxml.table import CT_Tbl
from docx.table import _Cell, Table
from docx.text.paragraph import Paragraph


def clean_text(text: str) -> str:
    """
    清洗文本：去除标签和多余空白
    """
    if not text:
        return ""
    # 1. 去除奇怪的标签或转义符
    text = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", "", text)
    # 2. 去除首尾空白
    return text.strip()


def iter_block_items(parent):
    """
    关键函数：按文档顺序遍历所有元素（包括段落和表格）
    """
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


def load_and_chunk_docx(file_path: str, chunk_size_limit: int = 1000) -> List[str]:
    """
    读取 docx 并按条款逻辑切分（支持读取表格内容 + 强制熔断防止超时）
    """
    if not os.path.exists(file_path):
        print("错误: 文件未找到 -> " + file_path)
        return []

    try:
        doc = Document(file_path)
    except Exception as e:
        print(f"读取文件失败: {e}")
        return []

    full_text = []

    # === 1. 读取逻辑 (保持你原有的优秀逻辑，支持表格) ===
    # 注意：确保 iter_block_items 和 clean_text 函数在你的文件中已定义
    for block in iter_block_items(doc):
        # 1. 如果是段落
        if isinstance(block, Paragraph):
            cleaned = clean_text(block.text)
            if cleaned:
                full_text.append(cleaned)

        # 2. 如果是表格
        elif isinstance(block, Table):
            for row in block.rows:
                row_cells = [clean_text(cell.text) for cell in row.cells]
                if any(row_cells):
                    row_text = " | ".join(row_cells)
                    full_text.append(f"[表格内容] {row_text}")

    # === 2. 切分逻辑 (🟢 核心升级部分) ===
    chunks = []
    current_chunk = []
    current_length = 0

    # 匹配 "第一条", "一、" 等作为切分点
    article_pattern = re.compile(r"^\s*(第[零一二三四五六七八九十百]+条|[一二三四五六七八九十]+、)")

    for line in full_text:
        is_new_article = article_pattern.match(line)
        line_len = len(line)

        # --- 逻辑升级 ---
        # 条件 A (优雅切分): 遇到新条款，且缓冲区已有一定内容 (比如 >300字，避免把标题切得太碎)
        should_split_nice = is_new_article and current_length > 300

        # 条件 B (暴力熔断): 没遇到条款，但缓冲区快爆了 (加上这一行 > 限制值)
        # 这是为了解决《通知》类文档开头一大段废话导致超时的问题
        should_split_force = (current_length + line_len) > chunk_size_limit

        if should_split_nice or should_split_force:
            chunks.append("\n".join(current_chunk))
            current_chunk = []
            current_length = 0

            # 可选：如果你想知道哪里触发了强制切分，可以把下面这行注释打开
            # if should_split_force: print(f"  -> ⚠️ 触发强制切分 (当前块已达 {current_length} 字)")

        current_chunk.append(line)
        current_length += line_len

    # 加入最后剩余的部分
    if current_chunk:
        chunks.append("\n".join(current_chunk))

    file_name = os.path.basename(file_path)
    print(f"已加载: {file_name}, 共 {len(full_text)} 行, 切分为 {len(chunks)} 个块")

    return chunks


# 简单测试块
if __name__ == "__main__":
    print("Data Loader (Table Supported) 模块加载成功")