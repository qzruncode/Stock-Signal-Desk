"""Deterministic presentation for Shenwan industry-index retrievals."""

from __future__ import annotations

import math
import re
from typing import Any, Optional


def _text(value: Any, fallback: str = "—") -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text or fallback


def _cell(value: Any, fallback: str = "—") -> str:
    return _text(value, fallback).replace("|", "\\|")


def _value(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip():
            return value
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _format_number(value: Any) -> str:
    number = _number(value)
    if number is None:
        return _text(value)
    return f"{number:,.2f}"


def _latest_industry_index_result(
    evidence: Optional[list[dict[str, Any]]],
) -> dict[str, Any] | None:
    for packet in reversed(evidence or []):
        if not isinstance(packet, dict) or packet.get("tool") != "get_industry_index_context":
            continue
        result = packet.get("result")
        if isinstance(result, dict):
            return result
    return None


def _history_summary(history: dict[str, Any]) -> list[str]:
    items = [item for item in history.get("items") or [] if isinstance(item, dict)]
    if history.get("success") is not True:
        return [f"- 日线未取得：{_text(history.get('error'), '数据源没有返回可用日线。')}"]
    if not items:
        return ["- 日线未取得：数据源返回为空，不能据此推导区间表现。"]

    ordered = sorted(
        items,
        key=lambda item: _text(_value(item, ("日期", "date", "交易日")), ""),
    )
    first, last = ordered[0], ordered[-1]
    first_date = _text(_value(first, ("日期", "date", "交易日")))
    last_date = _text(_value(last, ("日期", "date", "交易日")))
    first_close = _value(first, ("收盘", "收盘价", "close", "最新价"))
    last_close = _value(last, ("收盘", "收盘价", "close", "最新价"))
    start = _number(first_close)
    end = _number(last_close)
    if start is None or end is None or start == 0:
        return [
            f"- 已返回 **{len(ordered)} 个交易日**日线，覆盖 {first_date} 至 {last_date}；"
            "但未取得可计算区间涨跌的收盘字段。"
        ]
    change = (end / start - 1) * 100
    return [
        f"- 已返回 **{len(ordered)} 个交易日**日线，覆盖 {first_date} 至 {last_date}。",
        f"- 收盘从 {_format_number(first_close)} 变为 {_format_number(last_close)}，"
        f"区间变动 **{change:+.2f}%**（按首末收盘价机械计算）。",
    ]


def _component_rows(component_result: dict[str, Any]) -> list[str]:
    items = [item for item in component_result.get("items") or [] if isinstance(item, dict)]
    if component_result.get("success") is not True:
        return [f"- 成分股未取得：{_text(component_result.get('error'), '数据源没有返回可用成分股。')}"]
    if not items:
        return ["- 成分股未取得：数据源返回为空。"]

    lines = [
        f"共 **{len(items)} 只**；以下逐条列出本次源数据返回的全部成分股。",
        "",
        "| 序号 | 证券代码 | 证券名称 |",
        "|---:|---|---|",
    ]
    for position, item in enumerate(items, start=1):
        code = _value(item, ("证券代码", "股票代码", "成分券代码", "代码", "symbol"))
        name = _value(item, ("证券名称", "股票名称", "成分券名称", "名称", "name"))
        if code is None and name is None:
            details = "；".join(f"{_cell(key)}：{_cell(value)}" for key, value in item.items())
            lines.append(f"| {position} | — | {details or '源数据字段为空'} |")
        else:
            lines.append(f"| {position} | {_cell(code)} | {_cell(name)} |")
    return lines


def build_industry_index_context_answer(
    evidence: Optional[list[dict[str, Any]]],
) -> Optional[str]:
    """Render source-owned index facts without a lossy synthesis pass."""
    result = _latest_industry_index_result(evidence)
    if result is None:
        return None

    matches = [item for item in result.get("matches") or [] if isinstance(item, dict)]
    if not matches:
        errors = [str(item) for item in result.get("errors") or [] if item]
        warning = "；".join(errors) or "当前目录未找到匹配指数。"
        return f"## 申万行业指数查询未完成\n\n- {warning}"

    histories = result.get("histories") if isinstance(result.get("histories"), dict) else {}
    components = result.get("components") if isinstance(result.get("components"), dict) else {}
    lines = [
        "## 申万行业指数查询结果",
        "",
        f"- 查询类别：{_text(result.get('index_type'))}",
        f"- 数据源：{_text(result.get('source'))}",
        f"- 本次获取时间：{_text(result.get('data_time') or result.get('retrieved_at'))}",
    ]
    for match in matches:
        code = _text(_value(match, ("指数代码", "代码")))
        name = _text(_value(match, ("指数名称", "名称")), code)
        history = histories.get(code)
        component_result = components.get(code)
        lines.extend(["", f"### {name}（{code}）", "", "#### 历史表现", ""])
        lines.extend(_history_summary(history) if isinstance(history, dict) else ["- 日线未取得：本轮没有对应指数代码的历史结果。"])
        if isinstance(component_result, dict):
            lines.extend(["", "#### 完整成分股", ""])
            lines.extend(_component_rows(component_result))

    errors = [str(item) for item in result.get("errors") or [] if item]
    lines.extend(
        [
            "",
            "#### 分类边界",
            "",
            f"- {_text(result.get('membership_boundary'))}",
        ]
    )
    if errors:
        lines.append("- 本轮存在部分数据缺失：" + "；".join(errors))
    return "\n".join(lines)


__all__ = ["build_industry_index_context_answer"]
