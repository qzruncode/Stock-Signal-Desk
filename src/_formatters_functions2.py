"""Function group 2 extracted from src/formatters.py."""

from __future__ import annotations

from src.formatters import (
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

__all__ = ['_chunk_by_separators', '_chunk_by_max_words', 'chunk_content_by_max_words']

def _chunk_by_separators(content: str) -> tuple[list[str], str]:
    """
    通过分割线等特殊字符将消息内容分割为多个区块

    Args:
        content: 完整消息内容

    Returns:
        sections: 分割后的区块列表
        separator: 区块之间的分隔符，None 表示无法分割
    """
    # 智能分割：优先按 "---" 分隔（股票之间的分隔线）
    # 其次尝试各级标题分割
    if "\n---\n" in content:
        sections = content.split("\n---\n")
        separator = "\n---\n"
    elif "\n# " in content:
        # 按 # 分割 (兼容一级标题)
        parts = content.split("\n# ")
        sections = [parts[0]] + [f"# {p}" for p in parts[1:]]
        separator = "\n"
    elif "\n## " in content:
        # 按 ## 分割 (兼容二级标题)
        parts = content.split("\n## ")
        sections = [parts[0]] + [f"## {p}" for p in parts[1:]]
        separator = "\n"
    elif "\n### " in content:
        # 按 ### 分割
        parts = content.split("\n### ")
        sections = [parts[0]] + [f"### {p}" for p in parts[1:]]
        separator = "\n"
    elif "\n**" in content:
        # 按 ** 加粗标题分割 (兼容 AI 未输出标准 Markdown 标题的情况)
        parts = content.split("\n**")
        sections = [parts[0]] + [f"**{p}" for p in parts[1:]]
        separator = "\n"
    elif "\n" in content:
        # 按 \n 分割
        sections = content.split("\n")
        separator = "\n"
    else:
        return [content], ""
    return sections, separator

def _chunk_by_max_words(content: str, max_words: int, special_char_len: int = 2) -> list[str]:
    """
    按字数分割消息内容

    Args:
        content: 完整消息内容
        max_words: 单条消息最大字数
        special_char_len: 每个特殊字符的长度，默认为 2

    Returns:
        分割后的区块列表
    """
    if _effective_len(content, special_char_len) <= max_words:
        return [content]
    if max_words < MIN_MAX_WORDS:
        raise ValueError(f"max_words={max_words} < {MIN_MAX_WORDS}, 可能陷入无限递归。")

    sections = []
    suffix = TRUNCATION_SUFFIX
    effective_max_words = max_words - len(suffix)  # 预留后缀，避免边界超限
    if effective_max_words <= 0:
        effective_max_words = max_words
        suffix = ""

    while True:
        chunk, content = _slice_at_effective_len(content, effective_max_words, special_char_len)
        if content.strip() != "":
            sections.append(chunk + suffix)
        else:
            # 最后一段了，直接添加并离开循环
            sections.append(chunk)
            break
    return sections

def chunk_content_by_max_words(
    content: str, max_words: int, special_char_len: int = 2, add_page_marker: bool = False
) -> list[str]:
    """
    按字数智能分割消息内容

    Args:
        content: 完整消息内容
        max_words: 单条消息最大字数
        special_char_len: 每个特殊字符的长度，默认为 2
        add_page_marker: 是否添加分页标记

    Returns:
        分割后的区块列表
    """

    def _chunk(content: str, max_words: int, special_char_len: int = 2) -> list[str]:
        if max_words < MIN_MAX_WORDS:
            # Safe guard，避免无限递归
            # 理论上，max_words在每次递归中可以减小到无限小，但实际中不太可能发生，
            # 除非每次_chunk_by_separators都能成功返回分隔符，且max_words初始值太小。
            raise ValueError(f"max_words={max_words} < {MIN_MAX_WORDS}, 可能陷入无限递归。")

        if _effective_len(content, special_char_len) <= max_words:
            return [content]

        sections, separator = _chunk_by_separators(content)
        if separator == "" and len(sections) == 1:
            # 无法智能分割，则强制按字数分割
            return _chunk_by_max_words(content, max_words, special_char_len)

        chunks = []
        current_chunk = []
        current_word_len = 0
        separator_len = len(separator) if separator else 0
        effective_max_words = max_words - separator_len  # 预留分割符长度，避免边界超限

        for section in sections:
            section += separator
            section_word_len = _effective_len(section, special_char_len)

            # 如果单个 section 就超长，需要强制截断
            if section_word_len > max_words:
                # 先保存当前积累的内容
                if current_chunk:
                    chunks.append("".join(current_chunk))
                    current_chunk = []
                    current_word_len = 0

                # 强制截断这个超长 section
                section_chunks = _chunk(section[:-separator_len], effective_max_words, special_char_len)
                section_chunks[-1] = section_chunks[-1] + separator
                chunks.extend(section_chunks)
                continue

            # 检查加入后是否超长
            if current_word_len + section_word_len > max_words:
                # 保存当前块，开始新块
                if current_chunk:
                    chunks.append("".join(current_chunk))
                current_chunk = [section]
                current_word_len = section_word_len
            else:
                current_chunk.append(section)
                current_word_len += section_word_len

        # 添加最后一块
        if current_chunk:
            chunks.append("".join(current_chunk))

        _remove_trailing_separator(chunks, separator)
        return chunks

    if add_page_marker:
        max_words = max_words - PAGE_MARKER_SAFE_LEN

    chunks = _chunk(content, max_words, special_char_len)
    if add_page_marker:
        chunks = _append_page_markers(chunks)
    return chunks
