# -*- coding: utf-8 -*-
"""Fallback renderers for workflow and staged research results."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

def _template_purpose(content: Any) -> str:
    headings = []
    for match in re.findall(r"^###\s+(?:\d+[.、]?\s*)?(.+?)\s*$", str(content or ""), re.MULTILINE):
        heading = match.strip()
        if heading and heading not in headings and "输出" not in heading:
            headings.append(heading)
    if headings:
        return "覆盖" + "、".join(headings[:6])
    return "自定义股票分析框架"


def _build_workflow_evidence_fallback(
    evidence: Optional[List[Dict[str, Any]]],
) -> Optional[str]:
    """Render successful workflow reads/actions without another model pass."""
    workflow_names = {
        "filter_watchlist_by_theme",
        "manage_watchlist",
        "manage_watchlist_groups",
        "run_stock_analysis",
        "get_analysis_status",
        "search_analysis_history",
        "delete_analysis_history",
        "manage_analysis_templates",
        "run_batch_analysis",
        "manage_batch_run",
        "manage_analysis_schedule",
        "get_notification_status",
        "send_notification",
    }
    for item in reversed(evidence or []):
        if not isinstance(item, dict) or item.get("tool") not in workflow_names:
            continue
        tool_name = str(item.get("tool"))
        result = item.get("result")
        if not isinstance(result, dict) or result.get("success") is False:
            continue

        if tool_name == "filter_watchlist_by_theme":
            items = result.get("items") if isinstance(result.get("items"), list) else []
            group = result.get("group") if isinstance(result.get("group"), dict) else {}
            themes = "、".join(str(theme) for theme in result.get("requested_themes") or []) or "指定主题"
            lines = [
                f"在 **{group.get('name') or '我的自选股'}** 的 "
                f"**{group.get('valid_security_count', group.get('count', 0))} 只有效证券**中，",
                f"按 **{themes}** 主题板块成员关系筛出 **{len(items)} 只**：\n",
            ]
            if items:
                lines.extend(
                    [
                        "| 公司/代码 | 匹配主题 | 主题板块 |",
                        "|---|---|---|",
                    ]
                )
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    name = str(item.get("name") or "未命名")
                    symbol = str(item.get("symbol") or "—")
                    matched = "、".join(str(value) for value in item.get("matched_themes") or []) or "—"
                    boards = "、".join(str(value) for value in item.get("boards") or []) or "—"
                    lines.append(f"| {name}（{symbol}） | {matched} | {boards} |")
            else:
                lines.append("没有找到与这些主题板块相交的自选股。")
            invalid_entries = [str(value) for value in result.get("invalid_entries") or []]
            lines.append(
                "\n> 口径：以上均为 **L1 主题板块成员关系**，不等同已形成相关订单或收入。"
                "如需核验真实业务关联，请继续指定公司。"
            )
            if invalid_entries:
                lines.append("\n已忽略无法识别的自选条目：" + "、".join(invalid_entries))
            return "\n".join(lines)

        if tool_name == "manage_watchlist":
            codes = [str(code) for code in result.get("codes") or []]
            if result.get("action") == "list":
                return f"当前默认自选股共有 **{len(codes)} 只**：" + (
                    "\n\n" + "、".join(codes) if codes else "列表为空。"
                )
            changed = [str(code) for code in result.get("changed") or []]
            verb = "添加" if result.get("action") == "add" else "移除"
            return f"已{verb} **{len(changed)} 只**股票：" + ("、".join(changed) if changed else "没有发生变化。")

        if tool_name == "manage_watchlist_groups":
            if result.get("action") == "list":
                groups = result.get("groups") if isinstance(result.get("groups"), list) else []
                if not groups:
                    return "当前没有自选分组。"
                lines = [
                    f"当前共有 **{len(groups)} 个自选分组**：\n",
                    "| 分组 | 类型 | 股票数量 | 成员 |",
                    "|---|---|---:|---|",
                ]
                for group in groups:
                    if not isinstance(group, dict):
                        continue
                    codes = [str(code) for code in group.get("codes") or []]
                    preview = "、".join(codes[:8]) or "—"
                    if len(codes) > 8:
                        preview += f" 等 {len(codes)} 只"
                    lines.append(
                        f"| {group.get('name') or '未命名'} | "
                        f"{'默认' if group.get('is_default') else '自定义'} | "
                        f"{group.get('count', len(codes))} | {preview} |"
                    )
                return "\n".join(lines)
            message = str(result.get("message") or "").strip()
            if message:
                return message

        if tool_name == "manage_analysis_templates":
            action = result.get("action")
            templates = result.get("items") if isinstance(result.get("items"), list) else []
            if action == "list":
                if not templates:
                    return "当前没有分析模板。你可以告诉我模板名称和分析框架，我会在确认后创建。"
                lines = [
                    f"当前共有 **{len(templates)} 个分析模板**：\n",
                    "| 模板 | 状态 | 用途 |",
                    "|---|---|---|",
                ]
                default_name = ""
                for template in templates:
                    if not isinstance(template, dict):
                        continue
                    name = str(template.get("name") or "未命名模板")
                    is_default = bool(template.get("is_default"))
                    if is_default:
                        default_name = name
                    lines.append(
                        f"| {name} | {'默认' if is_default else '可选'} | "
                        f"{_template_purpose(template.get('content'))} |"
                    )
                if default_name:
                    lines.append(f"\n当前默认模板是 **{default_name}**。发起正式分析时未指定模板，就会使用它。")
                return "\n".join(lines)

            template = result.get("template")
            if isinstance(template, dict):
                name = str(template.get("name") or "未命名模板")
                status = "默认模板" if template.get("is_default") else "可选模板"
                content = str(template.get("content") or "").strip()
                answer = f"## {name}\n\n- 状态：{status}\n- 用途：{_template_purpose(content)}"
                if action == "get" and content:
                    answer += f"\n\n### 模板内容\n\n{content}"
                else:
                    answer += "\n\n模板操作已完成。"
                return answer
            if result.get("deleted"):
                return "分析模板已删除。"

        if tool_name == "search_analysis_history":
            records = result.get("items") if isinstance(result.get("items"), list) else []
            if not records:
                return "没有找到符合条件的正式分析报告。"
            lines = [
                f"找到 **{result.get('total', len(records))} 份**正式分析报告，本页显示 {len(records)} 份：\n",
                "| ID | 股票 | 报告时间 | 类型 |",
                "|---|---|---|---|",
            ]
            for record in records:
                if not isinstance(record, dict):
                    continue
                stock = record.get("stock_name") or record.get("stock_code") or "—"
                code = record.get("stock_code") or ""
                lines.append(
                    f"| {record.get('id', '—')} | {stock}{f'（{code}）' if code else ''} | "
                    f"{record.get('created_at', '—')} | {record.get('report_type', '—')} |"
                )
            lines.append("\n告诉我报告 ID 或股票名称，我可以继续读取完整报告。")
            return "\n".join(lines)

        if tool_name == "get_analysis_status":
            if result.get("mode") == "detail" and isinstance(result.get("task"), dict):
                task = result["task"]
                return (
                    f"分析任务 **{task.get('task_id') or task.get('taskId') or '—'}**："
                    f"{task.get('status') or '未知状态'}，进度 {task.get('progress', 0)}%。\n\n"
                    f"{task.get('message') or task.get('error') or ''}"
                ).rstrip()
            stats = result.get("stats") if isinstance(result.get("stats"), dict) else {}
            return (
                f"当前共 **{stats.get('total', result.get('item_count', 0))} 个**分析任务："
                f"等待 {stats.get('pending', 0)}、运行中 {stats.get('processing', 0)}、"
                f"已完成 {stats.get('completed', 0)}、失败 {stats.get('failed', 0)}。"
            )

        if tool_name == "manage_analysis_schedule" and isinstance(result.get("schedule"), dict):
            schedule = result["schedule"]
            times = schedule.get("times") if isinstance(schedule.get("times"), list) else []
            return (
                f"自动分析计划当前 **{'已启用' if schedule.get('enabled') else '未启用'}**。\n\n"
                f"- 执行时间：{', '.join(map(str, times)) if times else '未设置'}\n"
                f"- 分析模板：{schedule.get('template_id') or '未设置'}"
            )

        if tool_name == "get_notification_status":
            channels = result.get("channels") if isinstance(result.get("channels"), list) else []
            configured = [
                str(channel.get("name") or channel.get("channel"))
                for channel in channels
                if isinstance(channel, dict) and channel.get("configured")
            ]
            if configured:
                return f"已配置通知渠道：**{'、'.join(configured)}**。只有你明确要求发送时，助手才会推送通知。"
            return "通知渠道尚未配置。请先到设置页的“通知设置”中填写企业微信 Webhook。"

        if tool_name == "manage_batch_run" and result.get("action") == "report" and result.get("markdown"):
            return str(result["markdown"]).strip()

        if tool_name == "manage_batch_run" and result.get("action") == "list":
            count = int(result.get("item_count") or 0)
            return f"当前有 **{count} 个**批量分析任务。" if count else "当前没有批量分析任务。"

        if tool_name == "run_stock_analysis":
            return (
                f"正式分析任务已{'存在并继续运行' if result.get('duplicate') else '提交'}："
                f"股票 **{result.get('stock_code') or '—'}**，任务 ID `{result.get('task_id') or '—'}`，"
                f"当前状态 {result.get('status') or '等待中'}。"
            )

        if tool_name == "run_batch_analysis":
            count = len(result.get("stock_codes") or [])
            return f"批量分析已启动，共 **{count} 只股票**。你可以继续问我批次进度。"

        if tool_name == "delete_analysis_history":
            return f"已删除 **{result.get('deleted_count', 0)} 条**分析历史。"

        if tool_name == "send_notification":
            return f"通知已发送至 **{result.get('channel') or '已配置渠道'}**。"

        message = str(result.get("message") or "").strip()
        if message:
            return message
        action = str(result.get("action") or "操作")
        return f"{action} 已完成。"
    return None


def _build_staged_news_search_answer(
    evidence: Optional[List[Dict[str, Any]]],
) -> Optional[str]:
    """Render a selectable news list without turning retrieval into research."""
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for packet in evidence or []:
        if not isinstance(packet, dict) or packet.get("tool") not in {
            "search_news",
            "search_financial_news",
        }:
            continue
        result = packet.get("result")
        if not isinstance(result, dict) or result.get("success") is False:
            continue
        for raw in result.get("items") or []:
            if not isinstance(raw, dict):
                continue
            title = str(raw.get("title") or "").strip()
            url = str(raw.get("url") or raw.get("link") or raw.get("id") or "").strip()
            if not title:
                continue
            key = url.rstrip("/").lower() or re.sub(r"\W+", "", title).lower()
            if not key or key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "title": title,
                    "url": url,
                    "source": str(raw.get("source") or raw.get("author") or "来源未标明").strip(),
                    "published": str(raw.get("published") or "时间未标明").strip()[:10],
                    "summary": re.sub(r"\s+", " ", str(raw.get("summary") or "").strip())[:220],
                }
            )
    if not items:
        return None
    items = items[:12]
    lines = [
        f"已找到 **{len(items)} 条**近期资讯。请回复编号，我再读取该条完整正文；本轮不提前做投资分析。\n",
        "| 编号 | 标题 | 来源 | 时间 | 简述 |",
        "|---:|---|---|---|---|",
    ]
    for index, item in enumerate(items, 1):
        title = f"[{item['title']}]({item['url']})" if item["url"] else item["title"]
        summary = item["summary"] or "—"
        lines.append(f"| {index} | {title} | {item['source']} | {item['published']} | {summary} |")
    lines.append("\n直接回复如“第 2 条”即可。")
    return "\n".join(lines)


def _build_catalyst_analysis_answer(
    evidence: Optional[List[Dict[str, Any]]],
) -> Optional[str]:
    """Render source-bound catalyst results without a second model call."""
    packets = [
        packet
        for packet in evidence or []
        if isinstance(packet, dict) and packet.get("tool") == "analyze_stock_catalysts"
    ]
    if not packets:
        return None

    items: List[Dict[str, Any]] = []
    errors: List[str] = []
    warnings: List[str] = []
    data_times: List[str] = []
    for packet in packets:
        result = packet.get("result")
        if not isinstance(result, dict):
            continue
        items.extend(item for item in result.get("items") or [] if isinstance(item, dict))
        errors.extend(str(item) for item in result.get("errors") or [] if item)
        warnings.extend(str(item) for item in result.get("warnings") or [] if item)
        if result.get("data_time"):
            data_times.append(str(result["data_time"]))
    if not items:
        return "## 未来6—12个月催化核验未完成\n\n" + (
            "；".join(errors) if errors else "本轮没有取得可用的公司催化证据。"
        )

    def clean(value: Any, limit: int = 300) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip().replace("|", "／")[:limit]

    lines: List[str] = []
    for item_index, item in enumerate(items):
        name = clean(item.get("name") or item.get("symbol") or "公司", 80)
        symbol = clean(item.get("symbol"), 20)
        heading = f"{name}（{symbol}）" if symbol and symbol != name else name
        if item_index:
            lines.extend(["", "---", ""])
        lines.extend([f"## {heading}：未来6—12个月催化核验", ""])
        catalysts = [row for row in item.get("catalysts") or [] if isinstance(row, dict)]
        retrieved = item.get("retrieved_evidence") if isinstance(item.get("retrieved_evidence"), dict) else {}
        formal_windows = [
            row
            for row in retrieved.get("formal_documents") or []
            if isinstance(row, dict) and row.get("time_window") and row.get("excerpt")
        ]
        if catalysts and item.get("passed") is True:
            lines.append(f"**结论：核验到 {len(catalysts)} 项满足时间窗与来源约束的催化。**")
        elif catalysts:
            lines.append(f"**结论：提取到 {len(catalysts)} 项有来源的事件线索，但尚未达到严格催化通过条件。**")
        elif formal_windows:
            lines.append(
                f"**结论：已从正式报告正文核验到 {len(formal_windows)} 项未来经营节点；"
                "本轮模型语义归类未完成，不会将其误报为“没有催化”。**"
            )
        else:
            lines.append("**结论：本轮未核验到同时具备明确时间窗和可回查来源的催化事件。**")
        verdict = clean(item.get("verdict"), 600)
        if formal_windows and not catalysts and ("评估失败" in verdict or "AllModelsFailedError" in verdict):
            verdict = (
                "模型语义归类暂时不可用；以下先按正式报告原文列出未来经营节点，" "不把模型故障解释为公司没有催化。"
            )
        if verdict:
            lines.extend(["", verdict])

        if catalysts:
            lines.extend(["", "### 事件与证据", ""])
            event_type_labels = {
                "company_milestone": "公司里程碑",
                "financial_validation": "财务核验",
                "sector_mapping": "板块映射",
                "conditional_watch": "条件观察",
            }
            for index, catalyst in enumerate(catalysts, 1):
                event = clean(catalyst.get("event"), 220) or "未命名事件"
                window = clean(catalyst.get("time_window"), 80) or "时间窗缺失"
                status = clean(catalyst.get("verification_status"), 60) or "证据状态未标明"
                confidence = clean(catalyst.get("confidence"), 20)
                event_type = event_type_labels.get(
                    clean(catalyst.get("event_type"), 40),
                    "事件待分类",
                )
                lines.append(
                    f"{index}. **{event}**（{event_type}；{window}；{status}"
                    + (f"；置信度{confidence}" if confidence else "")
                    + "）"
                )
                why = clean(catalyst.get("why_it_matters"), 500)
                if why:
                    lines.append(f"   - 影响：{why}")
                sources = [row for row in catalyst.get("sources") or [] if isinstance(row, dict)]
                for source in sources:
                    evidence_id = clean(source.get("evidence_id"), 10)
                    title = clean(source.get("title"), 220) or "来源标题缺失"
                    source_name = clean(source.get("source"), 80) or "来源未标明"
                    source_date = clean(source.get("date"), 40) or "日期未标明"
                    url = str(source.get("url") or "").strip()
                    linked_title = f"[{title}]({url})" if re.match(r"^https?://", url) else title
                    lines.append(f"   - 证据 {evidence_id}：{linked_title}（{source_name}，{source_date}）")
                    excerpt = clean(source.get("excerpt"), 420)
                    if excerpt:
                        lines.append(f"     - 正文：{excerpt}")
        else:
            clue_rows: List[Dict[str, Any]] = []
            for dimension, label in (
                ("formal_documents", "正式报告正文"),
                ("report_schedule", "财报预约"),
                ("research", "研报"),
                ("news", "公司新闻"),
                ("announcements", "公司公告"),
            ):
                for source in retrieved.get(dimension) or []:
                    if not isinstance(source, dict) or not source.get("title"):
                        continue
                    clue_rows.append({**source, "dimension_label": label})
                    if len(clue_rows) >= 6:
                        break
                if len(clue_rows) >= 6:
                    break
            if clue_rows:
                lines.extend(["", "### 尚缺明确时间窗的原始线索，或尚未升级为严格催化的证据", ""])
                for source in clue_rows:
                    evidence_id = clean(source.get("evidence_id"), 10)
                    title = clean(source.get("title"), 220)
                    source_name = clean(
                        source.get("source")
                        or source.get("org")
                        or source.get("label")
                        or source.get("dimension_label"),
                        80,
                    )
                    source_date = clean(source.get("date") or source.get("time"), 40) or "日期未标明"
                    url = str(source.get("url") or "").strip()
                    linked_title = f"[{title}]({url})" if re.match(r"^https?://", url) else title
                    lines.append(
                        f"- {evidence_id} · {source.get('dimension_label')}：{linked_title}"
                        f"（{source_name}，{source_date}）"
                    )
                lines.append(
                    "\n以上会保留给用户查看；正式报告中的时间窗必须继续解释其经营含义，财报预约只作为核验节点，"
                    "板块事件也不会冒充公司订单；证据不足时不升级为已核验催化。"
                )

        cited_ids = {
            clean(evidence_id, 12) for catalyst in catalysts for evidence_id in catalyst.get("evidence_ids") or []
        }
        uncategorized_formal_windows = [
            row for row in formal_windows if clean(row.get("evidence_id"), 12) not in cited_ids
        ]
        if uncategorized_formal_windows:
            lines.extend(["", "### 公司正式披露的未来经营节点（原文列示）", ""])
            for row in uncategorized_formal_windows[:8]:
                evidence_id = clean(row.get("evidence_id"), 12)
                window = clean(row.get("time_window"), 80)
                excerpt = clean(row.get("excerpt"), 700)
                title = clean(row.get("title"), 180) or "公司定期报告"
                url = str(row.get("url") or "").strip()
                linked_title = f"[{title}]({url})" if re.match(r"^https?://", url) else title
                lines.append(f"- **{window} · {evidence_id}**：{excerpt}")
                lines.append(f"  - 来源：{linked_title}")
            lines.append(
                "- 上述节点来自正式报告正文；模型暂时不可用时先保留原文事实，不额外推断订单金额、收入或利润贡献。"
            )
        verification_windows = [
            row
            for row in retrieved.get("report_schedule") or []
            if isinstance(row, dict) and clean(row.get("evidence_id"), 12) not in cited_ids
        ]
        if verification_windows:
            lines.extend(["", "### 已知财务核验窗口（不自动等于利好）", ""])
            for row in verification_windows[:4]:
                evidence_id = clean(row.get("evidence_id"), 12)
                title = clean(row.get("title"), 160) or "定期报告预约披露"
                window = clean(row.get("time_window"), 40) or "日期未标明"
                url = str(row.get("url") or "").strip()
                linked_title = f"[{title}]({url})" if re.match(r"^https?://", url) else title
                lines.append(f"- {window} · {evidence_id}：{linked_title}")
            lines.append("- 该日期只说明何时验证收入、毛利率、现金流和新业务兑现，不因预约披露本身判定为正向催化。")

        coverage = item.get("source_coverage") if isinstance(item.get("source_coverage"), dict) else {}
        available = coverage.get("available_count")
        required = coverage.get("required_count")
        if available is not None and required is not None:
            lines.extend(
                ["", f"> 证据源覆盖：{available}/{required}（公告目录、正式报告正文、财报预约、公司新闻、券商研报）。"]
            )
        missing = [clean(value, 180) for value in item.get("missing_evidence") or [] if clean(value, 180)]
        if missing:
            lines.append("> 仍需核验：" + "；".join(missing[:6]))

    lines.extend(
        [
            "",
            "### 判断边界",
            "",
            "- 这里只回答未来催化，不等于现在可以买入；估值、利好是否已被股价反映、买入位置和风险收益比仍需单独核验。",
            "- 没有明确日历时间窗或无法绑定本轮真实来源的线索，不会被列为已核验催化。",
            "- 板块映射、财务核验和公司里程碑会分开标注；行业大会或关键客户事件不会被改写成公司订单。",
            f"- 分析时间：{max(data_times) if data_times else '未标明'}；来源：内部同步公告目录、正式定期报告正文、财报预约、公司新闻、券商研报。",
        ]
    )
    if warnings or errors:
        lines.append("- 数据边界：" + "；".join([*warnings, *errors][:8]))
    return "\n".join(lines)



__all__ = ["_build_workflow_evidence_fallback", "_build_staged_news_search_answer", "_build_catalyst_analysis_answer"]
