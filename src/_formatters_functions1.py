"""Function group 1 extracted from src/formatters.py."""

from __future__ import annotations

from src.formatters import (
    re,
    List,
    markdown2,
    TRUNCATION_SUFFIX,
    PAGE_MARKER_PREFIX,
    PAGE_MARKER_SAFE_BYTES,
    PAGE_MARKER_SAFE_LEN,
    MIN_MAX_WORDS,
    MIN_MAX_BYTES,
    _SPECIAL_CHAR_RANGE,
    _SPECIAL_CHAR_REGEX,
 )

__all__ = ['_page_marker', '_append_page_markers', '_remove_trailing_separator', '_is_special_char', '_count_special_chars', '_effective_len', '_slice_at_effective_len', 'markdown_to_html_document', 'markdown_to_plain_text', '_bytes', '_chunk_by_max_bytes', 'chunk_content_by_max_bytes', 'slice_at_max_bytes']

def _page_marker(i: int, total: int) -> str:
    return f"{PAGE_MARKER_PREFIX} {i+1}/{total}"

def _append_page_markers(chunks: List[str]) -> List[str]:
    """Append stable page markers without mutating caller-owned lists."""
    total_chunks = len(chunks)
    return [f"{chunk}{_page_marker(index, total_chunks)}" for index, chunk in enumerate(chunks)]

def _remove_trailing_separator(chunks: List[str], separator: str) -> None:
    """Remove the artificial trailing separator from the final chunk in-place."""
    if not separator or not chunks:
        return

    if chunks[-1].endswith(separator):
        chunks[-1] = chunks[-1][: -len(separator)]

def _is_special_char(c: str) -> bool:
    """判断字符是否为特殊字符

    Args:
        c: 字符

    Returns:
        True 如果字符为特殊字符，False 否则
    """
    if len(c) != 1:
        return False
    cp = ord(c)
    return _SPECIAL_CHAR_RANGE[0] <= cp <= _SPECIAL_CHAR_RANGE[1]

def _count_special_chars(s: str) -> int:
    """
    计算字符串中的特殊字符数量

    Args:
        s: 字符串
    """
    # reg find all (0x10000, 0xFFFFF)
    match = _SPECIAL_CHAR_REGEX.findall(s)
    return len(match)

def _effective_len(s: str, special_char_len: int = 2) -> int:
    """
    计算字符串的有效长度

    Args:
        s: 字符串
        special_char_len: 每个特殊字符的长度，默认为 2

    Returns:
        s 的有效长度
    """
    n = len(s)
    n += _count_special_chars(s) * (special_char_len - 1)
    return n

def _slice_at_effective_len(s: str, effective_len: int, special_char_len: int = 2) -> tuple[str, str]:
    """
    按有效长度分割字符串

    Args:
        s: 字符串
        effective_len: 有效长度
        special_char_len: 每个特殊字符的长度，默认为 2

    Returns:
        分割后的前、后部分字符串
    """
    if _effective_len(s, special_char_len) <= effective_len:
        return s, ""

    s_ = s[:effective_len]
    n_special_chars = _count_special_chars(s_)
    residual_lens = n_special_chars * (special_char_len - 1) + len(s_) - effective_len
    while residual_lens > 0:
        residual_lens -= special_char_len if _is_special_char(s_[-1]) else 1
        s_ = s_[:-1]
    return s_, s[len(s_) :]

def markdown_to_html_document(markdown_text: str) -> str:
    """
    Convert Markdown to a complete HTML document (for email, md2img, etc.).

    Uses markdown2 with table and code block support, wraps with inline CSS
    for compact, readable layout. Reused by notification email and md2img.

    Args:
        markdown_text: Raw Markdown content.

    Returns:
        Full HTML document string with DOCTYPE, head, and body.
    """
    html_content = markdown2.markdown(
        markdown_text,
        extras=["tables", "fenced-code-blocks", "break-on-newline", "cuddled-lists"],
    )

    css_style = """
            body {
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
                line-height: 1.5;
                color: #24292e;
                font-size: 14px;
                padding: 15px;
                max-width: 900px;
                margin: 0 auto;
            }
            h1 {
                font-size: 20px;
                border-bottom: 1px solid #eaecef;
                padding-bottom: 0.3em;
                margin-top: 1.2em;
                margin-bottom: 0.8em;
                color: #0366d6;
            }
            h2 {
                font-size: 18px;
                border-bottom: 1px solid #eaecef;
                padding-bottom: 0.3em;
                margin-top: 1.0em;
                margin-bottom: 0.6em;
            }
            h3 {
                font-size: 16px;
                margin-top: 0.8em;
                margin-bottom: 0.4em;
            }
            p {
                margin-top: 0;
                margin-bottom: 8px;
            }
            table {
                border-collapse: collapse;
                width: 100%;
                margin: 12px 0;
                display: block;
                overflow-x: auto;
                font-size: 13px;
            }
            th, td {
                border: 1px solid #dfe2e5;
                padding: 6px 10px;
                text-align: left;
            }
            th {
                background-color: #f6f8fa;
                font-weight: 600;
            }
            tr:nth-child(2n) {
                background-color: #f8f8f8;
            }
            tr:hover {
                background-color: #f1f8ff;
            }
            blockquote {
                color: #6a737d;
                border-left: 0.25em solid #dfe2e5;
                padding: 0 1em;
                margin: 0 0 10px 0;
            }
            code {
                padding: 0.2em 0.4em;
                margin: 0;
                font-size: 85%;
                background-color: rgba(27,31,35,0.05);
                border-radius: 3px;
                font-family: SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace;
            }
            pre {
                padding: 12px;
                overflow: auto;
                line-height: 1.45;
                background-color: #f6f8fa;
                border-radius: 3px;
                margin-bottom: 10px;
            }
            hr {
                height: 0.25em;
                padding: 0;
                margin: 16px 0;
                background-color: #e1e4e8;
                border: 0;
            }
            ul, ol {
                padding-left: 20px;
                margin-bottom: 10px;
            }
            li {
                margin: 2px 0;
            }
        """

    return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                {css_style}
            </style>
        </head>
        <body>
            {html_content}
        </body>
        </html>
        """

def markdown_to_plain_text(markdown_text: str) -> str:
    """
    将 Markdown 转换为纯文本

    移除 Markdown 格式标记，保留可读性
    """
    text = markdown_text

    # 移除标题标记 # ## ###
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)

    # 移除加粗 **text** -> text
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)

    # 移除斜体 *text* -> text
    text = re.sub(r"\*(.+?)\*", r"\1", text)

    # 移除引用 > text -> text
    text = re.sub(r"^>\s+", "", text, flags=re.MULTILINE)

    # 移除列表标记 - item -> item
    text = re.sub(r"^[-*]\s+", "• ", text, flags=re.MULTILINE)

    # 移除分隔线 ---
    text = re.sub(r"^---+$", "────────", text, flags=re.MULTILINE)

    # 移除表格语法 |---|---|
    text = re.sub(r"\|[-:]+\|[-:|\s]+\|", "", text)
    text = re.sub(r"^\|(.+)\|$", r"\1", text, flags=re.MULTILINE)

    # 清理多余空行
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()

def _bytes(s: str) -> int:
    return len(s.encode("utf-8"))

def _chunk_by_max_bytes(content: str, max_bytes: int) -> List[str]:
    if _bytes(content) <= max_bytes:
        return [content]
    if max_bytes < MIN_MAX_BYTES:
        raise ValueError(f"max_bytes={max_bytes} < {MIN_MAX_BYTES}, 可能陷入无限递归。")

    sections: List[str] = []
    suffix = TRUNCATION_SUFFIX
    effective_max_bytes = max_bytes - _bytes(suffix)
    if effective_max_bytes <= 0:
        effective_max_bytes = max_bytes
        suffix = ""

    while True:
        chunk, content = slice_at_max_bytes(content, effective_max_bytes)
        if content.strip() != "":
            sections.append(chunk + suffix)
        else:
            # 最后一段了，直接添加并离开循环
            sections.append(chunk)
            break
    return sections

def chunk_content_by_max_bytes(content: str, max_bytes: int, add_page_marker: bool = False) -> List[str]:
    """
    按字节数智能分割消息内容

    Args:
        content: 完整消息内容
        max_bytes: 单条消息最大字节数
        add_page_marker: 是否添加分页标记

    Returns:
        分割后的区块列表
    """

    def _chunk(content: str, max_bytes: int) -> List[str]:
        # 优先按分隔线/标题分割，保证分页自然
        if max_bytes < MIN_MAX_BYTES:
            raise ValueError(f"max_bytes={max_bytes} < {MIN_MAX_BYTES}, 可能陷入无限递归。")

        if _bytes(content) <= max_bytes:
            return [content]

        sections, separator = _chunk_by_separators(content)
        if separator == "" and len(sections) == 1:
            # 无法智能分割，则强制按字数分割
            return _chunk_by_max_bytes(content, max_bytes)

        chunks: List[str] = []
        current_chunk: List[str] = []
        current_bytes = 0
        separator_bytes = _bytes(separator) if separator else 0
        effective_max_bytes = max_bytes - separator_bytes

        for section in sections:
            section += separator
            section_bytes = _bytes(section)

            # 如果单个 section 就超长，需要强制截断
            if section_bytes > effective_max_bytes:
                # 先保存当前积累的内容
                if current_chunk:
                    chunks.append("".join(current_chunk))
                    current_chunk = []
                    current_bytes = 0

                # 强制按字节截断，避免整段被截断丢失
                section_chunks = _chunk(section[:-separator_bytes], effective_max_bytes)
                section_chunks[-1] = section_chunks[-1] + separator
                chunks.extend(section_chunks)
                continue

            # 检查加入后是否超长
            if current_bytes + section_bytes > effective_max_bytes:
                # 保存当前块，开始新块
                if current_chunk:
                    chunks.append("".join(current_chunk))
                current_chunk = [section]
                current_bytes = section_bytes
            else:
                current_chunk.append(section)
                current_bytes += section_bytes

        # 添加最后一块
        if current_chunk:
            chunks.append("".join(current_chunk))

        _remove_trailing_separator(chunks, separator)

        return chunks

    if add_page_marker:
        max_bytes = max_bytes - PAGE_MARKER_SAFE_BYTES

    chunks = _chunk(content, max_bytes)
    if add_page_marker:
        chunks = _append_page_markers(chunks)
    return chunks

def slice_at_max_bytes(text: str, max_bytes: int) -> tuple[str, str]:
    """
    按字节数截断字符串，确保不会在多字节字符中间截断

    Args:
        text: 要截断的字符串
        max_bytes: 最大字节数

    Returns:
        (截断后的字符串, 剩余未截断内容)
    """
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, ""

    # 从最大字节数开始向前查找，找到完整的 UTF-8 字符边界
    truncated = encoded[:max_bytes]
    while truncated and (truncated[-1] & 0xC0) == 0x80:
        truncated = truncated[:-1]

    truncated = truncated.decode("utf-8", errors="ignore")
    return truncated, text[len(truncated) :]
