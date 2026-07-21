"""UI-only Agent progress markers and persistence cleanup."""

from __future__ import annotations

PROGRESS_MARKERS = (
    "正在理解问题并规划需要查询的数据...",
    "正在调用数据工具...",
    "已完成多轮数据查询，正在生成最终总结...",
    "正在拆解问题并规划研究路径...",
    "正在检索和核验关键证据...",
    "正在整理证据并形成结论...",
    "正在拆分标准任务并校验执行流程...",
    "正在执行已校验的标准任务...",
    "正在汇总标准任务结果...",
)


def strip_agent_progress(text: str) -> str:
    cleaned = str(text or "")
    for marker in PROGRESS_MARKERS:
        cleaned = cleaned.replace(marker, "")
    return cleaned.strip()


__all__ = ["PROGRESS_MARKERS", "strip_agent_progress"]
