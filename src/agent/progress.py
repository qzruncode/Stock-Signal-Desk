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

# These strings describe a terminal run status.  They are useful in the
# execution trace, but they are not an assistant answer and must not become a
# normal message in the conversation transcript.
NON_ANSWER_AGENT_MESSAGES = frozenset(
    {
        "上游模型服务返回超时；已保留已有工具观察和证据。",
        "模型服务暂时不可用；已保留已有工具观察和证据。",
        "本轮工具/循环预算已耗尽；已保留已有观察并停止继续调用。",
        "本轮模型调用、Token 或费用预算已耗尽；已保留已有工具观察和证据。",
    }
)


def strip_agent_progress(text: str) -> str:
    cleaned = str(text or "")
    for marker in PROGRESS_MARKERS:
        cleaned = cleaned.replace(marker, "")
    return cleaned.strip()


def is_non_answer_agent_message(text: str) -> bool:
    """Return whether text is a runtime status, rather than an answer."""
    return str(text or "").strip() in NON_ANSWER_AGENT_MESSAGES


__all__ = [
    "NON_ANSWER_AGENT_MESSAGES",
    "PROGRESS_MARKERS",
    "is_non_answer_agent_message",
    "strip_agent_progress",
]
