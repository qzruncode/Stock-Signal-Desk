"""Deterministic summaries for RSS source, feed, item, and document reads."""

from __future__ import annotations

from typing import Any, Optional


def build_rss_resource_answer(
    evidence: list[dict[str, Any]],
) -> Optional[str]:
    """Describe completed RSS retrieval without asking a model to rewrite it."""

    results: dict[str, dict[str, Any]] = {}
    for packet in evidence:
        if not isinstance(packet, dict):
            continue
        tool_name = str(packet.get("tool") or "")
        result = packet.get("result")
        if tool_name in {
            "discover_rss_sources",
            "inspect_rss_source",
            "read_rss_feed",
            "read_rss_item",
            "read_text_document",
        } and isinstance(result, dict):
            results[tool_name] = result
    if not results:
        return None

    article = results.get("read_rss_item")
    document = results.get("read_text_document")
    feed = results.get("read_rss_feed")
    source = results.get("inspect_rss_source") or results.get(
        "discover_rss_sources"
    )
    lines = ["## RSS 读取完成", ""]

    route_path = ""
    for value in (article, feed, source):
        if not isinstance(value, dict):
            continue
        ref = value.get("item_ref") or value.get("source_ref") or value.get("route")
        if isinstance(ref, dict):
            route_path = str(ref.get("route_path") or "").strip()
        route_path = route_path or str(value.get("route_path") or "").strip()
        if route_path:
            break
    if route_path:
        lines.append(f"- 路由：`{route_path}`")

    if source:
        source_items = source.get("items")
        if isinstance(source_items, list) and source_items:
            lines.extend(
                [
                    "",
                    "### 来源目录",
                    "",
                    "| 来源 | 路由 | 健康状态 | 需配置 | 自动使用 | 用途 |",
                    "|---|---|---|---|---|---|",
                ]
            )
            for item in source_items:
                if not isinstance(item, dict):
                    continue
                source_name = str(
                    item.get("namespace_name")
                    or item.get("name")
                    or item.get("namespace")
                    or "未知来源"
                )
                item_name = str(item.get("name") or "").strip()
                display_name = (
                    f"{source_name} · {item_name}"
                    if item_name and item_name != source_name
                    else source_name
                )
                item_route = str(item.get("route_path") or "")
                readiness = str(item.get("readiness") or "unknown")
                configuration = "是" if item.get("requires_configuration") else "否"
                auto_use = "是" if item.get("auto_recommended") else "否"
                purpose = str(
                    item.get("description")
                    or item.get("name")
                    or "未说明"
                )
                cells = [
                    display_name,
                    f"`{item_route}`",
                    readiness,
                    configuration,
                    auto_use,
                    purpose,
                ]
                lines.append(
                    "| "
                    + " | ".join(cell.replace("|", "\\|") for cell in cells)
                    + " |"
                )

    if feed:
        items = feed.get("items")
        item_count = (
            len(items)
            if isinstance(items, list)
            else int(feed.get("item_count") or 0)
        )
        lines.append(f"- Feed：已读取 {item_count} 条")

    if article:
        title = str(article.get("title") or "未命名条目")
        content_length = int(article.get("content_length") or 0)
        content_chunks = article.get("content_chunks")
        chunk_count = len(content_chunks) if isinstance(content_chunks, list) else 0
        lines.append(f"- 已精确绑定条目：{title}")
        lines.append(
            f"- 应用内全文：{content_length} 个字符，{chunk_count} 个文本片段"
        )
        resources = article.get("resources")
        if isinstance(resources, list) and resources:
            lines.append("- 原始文本文件：")
            for resource in resources:
                if not isinstance(resource, dict):
                    continue
                filename = str(
                    resource.get("filename")
                    or resource.get("resource_id")
                    or "未命名文件"
                )
                status = str(resource.get("extraction_status") or "unknown")
                chunk_total = int(resource.get("chunk_count") or 0)
                lines.append(
                    f"  - {filename}（提取状态：{status}，文本片段：{chunk_total}）"
                )
        else:
            lines.append("- 原始文本文件：该条目未返回可持久化的文本型附件")
        errors = article.get("errors")
        if isinstance(errors, list) and errors:
            lines.append("- 部分失败：" + "；".join(str(item) for item in errors))
    elif document:
        resource = document.get("resource")
        filename = (
            str(resource.get("filename") or "")
            if isinstance(resource, dict)
            else ""
        )
        lines.append(f"- 已继续读取会话文件：{filename or '已绑定文件'}")

    lines.extend(
        [
            "",
            "列表、全文和原文件卡片均来自同一条资源链；未按标题重新搜索。",
        ]
    )
    return "\n".join(lines)


__all__ = ["build_rss_resource_answer"]
