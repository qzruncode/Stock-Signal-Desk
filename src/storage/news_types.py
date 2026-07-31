# -*- coding: utf-8 -*-
"""Neutral data contracts accepted by news-intelligence persistence."""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class NewsSearchResult:
    """One normalized news result."""

    title: str
    snippet: str
    url: str
    source: str
    published_date: Optional[str] = None

    def to_text(self) -> str:
        date_str = f" ({self.published_date})" if self.published_date else ""
        return f"【{self.source}】{self.title}{date_str}\n{self.snippet}"


@dataclass
class NewsSearchResponse:
    """Normalized result set accepted by the news storage layer."""

    query: str
    results: List[NewsSearchResult]
    provider: str
    success: bool = True
    error_message: Optional[str] = None
    search_time: float = 0.0

    def to_context(self, max_results: int = 5) -> str:
        if not self.success or not self.results:
            return f"搜索 '{self.query}' 未找到相关结果。"

        lines = [f"【{self.query} 搜索结果】（来源：{self.provider}）"]
        for index, result in enumerate(self.results[:max_results], 1):
            lines.append(f"\n{index}. {result.to_text()}")
        return "\n".join(lines)
