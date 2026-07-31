"""Render structured domain and theme evidence results.

The chat endpoint owns request orchestration; this module owns the presentation
of domain-scoped result contracts.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


def _format_source_label(name: Any, url: Any, default: str) -> str:
    source_name = str(name or default)
    source_url = str(url or "").strip()
    return f"[{source_name}]({source_url})" if re.match(r"^https?://", source_url) else source_name


def _format_evidence_quote(value: Any, *, limit: int = 180) -> str:
    quote = re.sub(r"\s+", " ", str(value or "")).strip().replace("|", "｜")
    return quote if len(quote) <= limit else quote[: limit - 3] + "..."


def build_domain_candidate_answer(
    evidence: Optional[List[Dict[str, Any]]],
) -> Optional[str]:
    """Render multi-domain structured candidates without another model pass."""
    result = next(
        (
            packet.get("result")
            for packet in evidence or []
            if isinstance(packet, dict)
            and packet.get("tool") == "get_domain_stock_candidates"
            and isinstance(packet.get("result"), dict)
        ),
        None,
    )
    if not isinstance(result, dict):
        return None

    domain_results = [item for item in result.get("domain_results") or [] if isinstance(item, dict)]
    if not domain_results:
        return "## 领域股票候选未完成\n\n" "本轮没有取得任何结构化板块结果，因此没有使用网页名单或模型记忆补股票。"

    union: Dict[str, Dict[str, Any]] = {}
    coverage_lines: List[str] = []
    inherited_mapping_parts: List[str] = []
    failed_domains: List[str] = []
    for domain_result in domain_results:
        domain = str(domain_result.get("domain") or "未命名领域")
        themes = [str(value) for value in domain_result.get("lookup_themes") or [] if value]
        boards = list(
            dict.fromkeys(
                str(board.get("name") or "")
                for board in domain_result.get("matched_boards") or []
                if isinstance(board, dict) and board.get("name")
            )
        )
        mapping_type = str(domain_result.get("mapping_type") or "")
        basis = {
            "catalog_binding": "当前实时目录语义绑定板块",
            "unresolved": "当前目录未解析",
        }.get(mapping_type, "结构化板块")
        coverage = "完整" if domain_result.get("coverage_complete") else "部分"
        count = int(domain_result.get("candidate_count") or 0)
        rationale = str(domain_result.get("mapping_rationale") or "").strip()
        unresolved_parts = [str(value) for value in domain_result.get("unresolved_parts") or [] if value]
        coverage_lines.append(
            f"- **{domain}**：{basis} `{ '、'.join(themes) or '未匹配' }`；"
            f"实际板块 { '、'.join(boards) or '未取得' }；{coverage}覆盖，候选 **{count} 只**。"
            + (f" 映射说明：{rationale}" if rationale else "")
            + (f" 未覆盖子领域：{'、'.join(unresolved_parts)}。" if unresolved_parts else "")
        )
        if themes:
            inherited_mapping_parts.append(f"{domain}→{'、'.join(themes)}")
        if not domain_result.get("success"):
            failed_domains.append(domain)
        for item in domain_result.get("items") or []:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "")
            name = str(item.get("name") or "").strip()
            if not re.fullmatch(r"\d{6}", symbol) or not name:
                continue
            merged = union.setdefault(
                symbol,
                {
                    "symbol": symbol,
                    "name": name,
                    "domains": [],
                    "boards": [],
                },
            )
            for value in item.get("matched_domains") or [domain]:
                text = str(value or "").strip()
                if text and text not in merged["domains"]:
                    merged["domains"].append(text)
            for value in item.get("boards") or boards:
                text = str(value or "").strip()
                if text and text not in merged["boards"]:
                    merged["boards"].append(text)

    rows = sorted(
        union.values(),
        key=lambda item: (-len(item["domains"]), item["symbol"]),
    )
    if rows:
        table_lines = [
            "| 公司/代码 | 匹配领域 | 结构化板块依据 | 证据级别 |",
            "|---|---|---|---|",
        ]
        table_lines.extend(
            f"| {item['name']} ({item['symbol']}) | {'、'.join(item['domains'])} | "
            f"{'、'.join(item['boards']) or '板块名称缺失'} | L1 候选 |"
            for item in rows
        )
        candidate_text = "\n".join(table_lines)
    else:
        candidate_text = "没有取得任何本地证券库可核验的候选股票。"

    failure_text = ""
    if failed_domains:
        failure_text = "\n\n> 未完成领域：" + "、".join(failed_domains) + "。这些领域没有改用网页搜索或模型记忆补名单。"
    inherited_mapping_text = ""
    if inherited_mapping_parts:
        inherited_mapping_text = (
            "\n\n> 结构化板块映射（后续追问继续沿用）：" + "；".join(inherited_mapping_parts) + "。"
        )
    return (
        "## 按领域匹配的 A 股候选\n\n"
        f"已与本地 **{result.get('local_universe_count') or '全量'} 只**有效证券交叉核验，"
        f"合并去重后共 **{len(rows)} 只**。\n\n"
        "### 领域与板块映射\n\n"
        + "\n".join(coverage_lines)
        + "\n\n### 候选股票\n\n"
        + candidate_text
        + inherited_mapping_text
        + failure_text
        + "\n\n> 口径：以上只证明结构化概念板块成员关系和证券身份有效。"
        "它不是订单、客户验证、收入兑现或买入建议；本轮没有使用通用网页搜索生成候选。"
    )


def processor_result(
    evidence: Optional[List[Dict[str, Any]]],
    processor_name: str,
) -> Optional[Dict[str, Any]]:
    return next(
        (
            packet.get("result")
            for packet in evidence or []
            if isinstance(packet, dict)
            and packet.get("processor") == processor_name
            and isinstance(packet.get("result"), dict)
        ),
        None,
    )


def build_ranked_domain_answer(
    evidence: Optional[List[Dict[str, Any]]],
) -> Optional[str]:
    result = processor_result(evidence, "ranked_domain_selection")
    if not isinstance(result, dict):
        return None
    if (
        result.get("success") is not True
        or result.get("coverage_complete") is False
        or result.get("ranking_complete") is False
    ):
        catalog_total = int(result.get("catalog_total") or result.get("catalog_count") or 0)
        catalog_supplied = int(result.get("catalog_supplied") or 0)
        errors = [str(value).strip() for value in result.get("errors") or [] if str(value).strip()]
        lines = [
            "## 产业受益领域排序未完成",
            "",
            (
                f"本轮取得 **{catalog_total} 个**实时板块，" f"有限集合选择器实际收到 **{catalog_supplied} 个**。"
                if catalog_total
                else "本轮没有取得可执行的项目实时板块目录。"
            ),
            "",
            (
                "板块 ID 与资源契约没有全部通过校验，因此本轮没有展示"
                "部分梯队或其他部分结果，也没有发布可供后续找股使用的领域集合。"
            ),
        ]
        error_code = str(result.get("error_code") or "").strip()
        public_error = {
            "planner_schema_invalid": ("目录选择模型没有返回完整的结构化结果，单次定点修复仍未通过。"),
            "synthesis_failed": ("上游模型调用失败，未形成可校验的板块集合。"),
            "resource_unavailable": ("实时板块目录不可用，无法形成可校验的板块集合。"),
        }.get(error_code)
        if public_error:
            lines.extend(
                [
                    "",
                    f"执行信息：{public_error}（错误代码：`{error_code}`）",
                ]
            )
        elif errors:
            lines.extend(["", "执行信息：" + "；".join(errors)])
        return "\n".join(lines)
    items = [item for item in result.get("items") or [] if isinstance(item, dict) and item.get("label")]
    if not items:
        return "## 产业受益领域排序未完成\n\n" "本轮没有形成通过结构校验的领域排序，因此没有输出或保存梯队。"

    project_catalog = result.get("source_scope") == "project_live_board_catalog"
    selection = result.get("result_selection") if isinstance(result.get("result_selection"), dict) else {}
    selection_mode = str(selection.get("mode") or "all_relevant")
    single_result = selection_mode == "best_one"
    top_k_result = selection_mode == "top_k"
    project_summary = (
        f"有限集合选择器读取项目当前完整的 "
        f"**{int(result.get('catalog_count') or 0)} 个**实时概念板块，"
        "模型只返回紧凑板块 ID，程序随后完成目录成员、角色和数量校验"
    )
    if single_result:
        heading = "## 项目实时板块最受益方向" if project_catalog else "## 最受益方向"
        summary = (
            project_summary + "，并按本轮结果约束只保留最优的一个方向。"
            if project_catalog
            else "项目板块目录无法覆盖该主题，本轮使用公开来源兜底，并只保留最优的一个方向。"
        )
    elif top_k_result:
        heading = "## 项目实时板块最受益方向排序" if project_catalog else "## 最受益方向排序"
        summary = (
            project_summary + f"，并按本轮结果约束保留前 **{len(items)} 个**方向。"
            if project_catalog
            else ("项目板块目录无法覆盖该主题，本轮使用公开来源兜底，" f"并按结果约束保留前 **{len(items)} 个**方向。")
        )
    else:
        heading = "## 项目实时板块受益梯队" if project_catalog else "## 受益领域梯队"
        summary = (
            project_summary + "；以下名称都可直接用于后续板块成分股查询。"
            if project_catalog
            else "项目板块目录无法覆盖该主题，本轮使用公开来源兜底；以下排序仍保存为结构化领域产物。"
        )
    lines = [
        heading,
        "",
        summary,
    ]
    artifacts = [
        artifact
        for artifact in result.get("semantic_artifacts") or []
        if isinstance(artifact, dict) and artifact.get("type") == "domain_collection_v2"
    ]
    assumptions = artifacts[0].get("assumptions") or [] if artifacts else []
    if assumptions:
        lines.extend(
            [
                "",
                "执行口径："
                + "；".join(
                    str(item.get("reason") or "").strip()
                    for item in assumptions
                    if isinstance(item, dict) and str(item.get("reason") or "").strip()
                ),
            ]
        )

    def append_item(item: Dict[str, Any], prefix: str) -> None:
        label = str(item.get("label") or "")
        rationale = str(item.get("rationale") or "").strip()
        if project_catalog:
            board_code = str(item.get("board_code") or "").strip()
            code_text = f"（{board_code}）" if board_code else ""
            lines.append(f"{prefix} **{label}**{code_text}：{rationale}")
            return
        quote = re.sub(r"\s+", " ", str(item.get("support_quote") or "")).strip()
        source_name = str(item.get("source_name") or "公开资料")
        source_date = str(item.get("source_date") or "日期未标明")
        source_url = str(item.get("source_url") or "").strip()
        source = f"[{source_name}]({source_url})" if re.match(r"^https?://", source_url) else source_name
        lines.append(f"{prefix} **{label}**：{rationale}")
        lines.append(f"   来源原文：{quote}（{source}，{source_date}）")

    if single_result:
        lines.append("")
        append_item(items[0], "-")
    elif top_k_result:
        lines.append("")
        for index, item in enumerate(items, start=1):
            append_item(item, f"{index}.")
    else:
        for tier in sorted({int(item.get("tier") or 0) for item in items if int(item.get("tier") or 0) > 0}):
            lines.extend(["", f"### 第{tier}梯队", ""])
            for item in items:
                if int(item.get("tier") or 0) == tier:
                    append_item(item, "-")
    lines.extend(
        [
            "",
            (
                "> 后续提到“这个方向”时，Planner 读取的是本轮只保留一个结果的结构化领域集合，"
                if single_result
                else "> 后续提到“这些方向”或“第一梯队”时，Planner 读取的是本轮保存的结构化领域集合，"
            )
            + "不是重新解析这段 Markdown。"
            + (" 后续找股将直接查询这些真实板块的项目成分股数据。" if project_catalog else ""),
        ]
    )
    return "\n".join(lines)


def build_per_security_theme_answer(result: Dict[str, Any]) -> str:
    candidate_count = int(result.get("candidate_count") or 0)
    analyzed_count = int(result.get("analyzed_candidate_count") or 0)
    coverage_complete = bool(result.get("candidate_coverage_complete"))
    counts = result.get("verdict_counts") if isinstance(result.get("verdict_counts"), dict) else {}
    passed = int(counts.get("pass") or 0)
    failed = int(counts.get("fail") or 0)
    insufficient = int(counts.get("insufficient") or 0)
    errored = int(counts.get("error") or 0)
    items = [item for item in result.get("items") or [] if isinstance(item, dict)]
    lines = [
        "## 候选公司逐股主题分析",
        "",
        (
            f"项目板块候选池共有 **{candidate_count} 家**，已为其中 "
            f"**{analyzed_count} 家**分别建立公司资料、主营构成、公告、"
            "个股新闻和个股研报证据档案，并逐家公司完成独立判断。"
        ),
        "",
        (
            f"- 通过：**{passed} 家**"
            f"\n- 不符合：**{failed} 家**"
            f"\n- 证据不足：**{insufficient} 家**"
            f"\n- 分析错误：**{errored} 家**"
        ),
        "",
        ("本轮逐股覆盖完整。" if coverage_complete else "本轮逐股覆盖不完整，不能把当前通过名单描述成完整筛选结果。"),
        "",
    ]
    if not items:
        lines.append("没有公司同时通过上位产业、具体子领域、发展强度和逐字证据校验。")
    else:
        lines.extend(
            [
                "| 公司/代码 | 匹配领域 | 已证实阶段 | 独立判断 | 已核验证据 |",
                "|---|---|---|---|---|",
            ]
        )
        level_labels = {
            "layout": "产品/技术布局",
            "investment": "研发或战略投入",
            "customer_validation": "客户验证/定点",
            "order": "订单",
            "mass_production": "量产/批量交付",
            "revenue": "相关业务收入",
            "none": "无",
        }
        for item in items:
            references = [reference for reference in item.get("evidence") or [] if isinstance(reference, dict)]
            reference = references[0] if references else {}
            quote = _format_evidence_quote(reference.get("support_quote"))
            source_date = str(reference.get("source_date") or "日期未标明")
            source = _format_source_label(reference.get("source_name"), reference.get("source_url"), "项目数据源")
            evidence_text = f"{quote}（{source}，{source_date}）" if quote else "无通过校验的引用"
            lines.append(
                f"| {item.get('company_name') or ''} ({item.get('symbol') or ''})"
                f" | {'、'.join(item.get('matched_domains') or [])}"
                f" | {level_labels.get(str(item.get('development_level') or ''), '未分级')}"
                f" | {str(item.get('reason') or '').replace('|', '｜')}"
                f" | {evidence_text} |"
            )
    lines.extend(
        [
            "",
            "> 每家公司均有独立终态；不符合、证据不足和执行错误不会被静默丢弃。"
            "网络搜索只会在该公司所有项目数据源均未形成可分析资料时逐股兜底。"
            "以上不构成买入建议。",
        ]
    )
    return "\n".join(lines)


def build_theme_business_evidence_answer(
    evidence: Optional[List[Dict[str, Any]]],
) -> Optional[str]:
    result = processor_result(evidence, "company_evidence_binding")
    if not isinstance(result, dict):
        return None
    if result.get("screening_mode") == "per_security_full_analysis":
        return build_per_security_theme_answer(result)
    items = [item for item in result.get("items") or [] if isinstance(item, dict)]
    domain_results = [item for item in result.get("domain_results") or [] if isinstance(item, dict)]
    candidate_scope = str(result.get("candidate_scope") or "")
    candidate_count = int(result.get("candidate_count") or 0)
    source_observed_count = int(result.get("source_observed_candidate_count") or 0)
    not_observed_count = int(
        result.get("not_observed_candidate_count")
        if result.get("not_observed_candidate_count") is not None
        else max(0, candidate_count - source_observed_count)
    )
    coverage_complete = bool(result.get("candidate_coverage_complete"))
    scope_description = (
        (
            f"项目结构化板块候选池共有 **{candidate_count} 家**；本轮公开来源材料实际提及其中 "
            f"**{source_observed_count} 家**，仍有 **{not_observed_count} 家**未被本轮来源覆盖。"
            "下面是候选池内的公开证据命中名单，不代表完整筛选后只剩这些公司；"
            "新闻和研报也不能向候选池外补股票。"
            if not coverage_complete
            else f"项目结构化板块候选池共有 **{candidate_count} 家**，本轮来源已覆盖全部候选；"
            "新闻和研报不能向候选池外补股票。"
        )
        if candidate_scope == "candidate_collection"
        else "项目实时板块目录无法覆盖这些领域，本轮才使用公开来源发现并核验 A 股公司。"
    )
    lines = [
        "## 按领域命中公开业务证据的 A 股公司",
        "",
        scope_description,
        "",
    ]
    if items:
        lines.extend(
            [
                "| 领域 | 公司/代码 | 进展层级 | 已核验原文 | 来源 |",
                "|---|---|---|---|---|",
            ]
        )
        stage_labels = {
            "L3": "L3 已有收入/订单/量产交付",
            "L2": "L2 客户验证/定点",
            "L1": "L1 产品或技术布局",
        }
        for item in sorted(
            items,
            key=lambda value: (
                str(value.get("domain") or ""),
                -{"L3": 3, "L2": 2, "L1": 1}.get(str(value.get("stage") or ""), 0),
                str(value.get("symbol") or ""),
            ),
        ):
            domain = str(item.get("domain") or "")
            name = str(item.get("company_name") or "")
            symbol = str(item.get("symbol") or "")
            stage = stage_labels.get(
                str(item.get("stage") or ""),
                str(item.get("stage") or "未分级"),
            )
            quote = _format_evidence_quote(item.get("support_quote"))
            source_date = str(item.get("source_date") or "日期未标明")
            source = _format_source_label(item.get("source_name"), item.get("source_url"), "公开资料")
            lines.append(f"| {domain} | {name} ({symbol}) | {stage} | {quote} | " f"{source}，{source_date} |")
    else:
        lines.append(
            "本轮来源里没有找到通过公司、领域和原文三重校验的正向事实，"
            "因此没有用概念板块、网页名单或模型记忆补股票。"
        )

    if domain_results:
        lines.extend(["", "### 来源覆盖", ""])
        for domain in domain_results:
            label = str(domain.get("domain") or "未命名领域")
            count = int(domain.get("company_count") or 0)
            source_count = int(domain.get("source_item_count") or 0)
            observed_count = int(domain.get("source_observed_candidate_count") or 0)
            rejected = int(domain.get("rejected_outside_candidate_count") or 0)
            status = "已处理" if domain.get("success") else "处理失败"
            detail = "；".join(str(value) for value in domain.get("errors") or [] if value)
            lines.append(
                f"- **{label}**：{status} {source_count} 条来源材料，"
                f"其中逐字提及候选池内 {observed_count} 家，"
                f"通过主题与原文校验 {count} 家。"
                + (f" 另有 {rejected} 条集合外公司事实被程序拒绝。" if rejected else "")
                + (f" {detail}" if detail else "")
            )
    lines.extend(
        [
            "",
            "> 层级口径：L3 才表示收入、订单、量产或批量交付；L2 是客户验证或定点；"
            "L1 只证明产品、技术或商业应用布局。以上均不等于买入建议。",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "build_domain_candidate_answer",
    "build_ranked_domain_answer",
    "build_per_security_theme_answer",
    "build_theme_business_evidence_answer",
    "processor_result",
]
