# -*- coding: utf-8 -*-
"""LLM prompt builders for stock business analysis."""

from __future__ import annotations

from api.v1.endpoints.stock_info._data import _build_growth_text


def format_llm_input(system_prompt: str, user_prompt: str) -> str:
    return f"[系统提示词]\n{system_prompt}\n\n[用户输入]\n{user_prompt}"


def _build_environment_prompt(
    symbol: str,
    industry: str,
    main_business: str,
    peer_data: list[dict],
    macro_data: dict,
) -> tuple[str, str]:
    peer_lines = []
    for p in peer_data[:5]:
        peer_lines.append(f"  - {p.get('name', '-')} ({p.get('code', '-')})")
    peer_text = '\n'.join(peer_lines) if peer_lines else "暂无同业数据"

    def _fmt_macro(records: list[dict]) -> str:
        if not records:
            return "暂无数据"
        lines = []
        for r in records[-3:]:
            date = r.get("date", "") or r.get("end_date", "")
            value = r.get("value", "")
            lines.append(f"  - {date}: {value}")
        return "\n".join(lines)

    pmi_text = _fmt_macro(macro_data.get('pmi', []))
    cpi_text = _fmt_macro(macro_data.get('cpi', []))
    ppi_text = _fmt_macro(macro_data.get('ppi', []))

    system = (
        "你是一个股票基本面分析专家，擅长以下分析：\n"
        "1. 宏观环境分析：解读宏观经济指标对行业和个股的影响\n"
        "2. 竞争格局分析：分析公司在行业中的竞争地位\n"
        "3. 业务质量分析：评估公司护城河和经营质量\n"
        "请基于提供的数据进行分析，不要编造数据。"
    )

    user = (
        f"请分析以下股票的环境和竞争格局：\n\n"
        f"**{symbol} - {industry}**\n"
        f"主营业务：{main_business}\n\n"
        f"**宏观指标**\n"
        f"PMI:\n{pmi_text}\n\n"
        f"CPI:\n{cpi_text}\n\n"
        f"PPI:\n{ppi_text}\n\n"
        f"**同业公司**\n{peer_text}\n\n"
        f"请从以下方面分析：\n"
        f"1. 当前宏观环境对该行业的影响\n"
        f"2. 公司在行业中的竞争地位\n"
        f"3. 行业发展趋势和机会\n"
        f"4. 主要风险因素"
    )

    return system, user


def _build_track_quality_prompt(
    symbol: str,
    industry: str,
    main_business: str,
    composition: list[dict],
    financial_summary: dict,
    peer_data: list[dict],
) -> tuple[str, str]:
    comp_text = '\n'.join(
        f"  - {c['business_name']}: 收入占比{c['revenue_pct']:.1%}"
        for c in composition[:5]
        if c.get('revenue_pct') is not None
    ) if composition else "暂无业务构成数据"

    fin_text = _build_growth_text(financial_summary)

    peer_text = '\n'.join(
        f"  - {p.get('name', '-')}: 市值{p.get('market_cap', 'N/A')}亿"
        for p in peer_data[:3]
    ) if peer_data else "暂无同业数据"

    system = (
        "你是一个深度价值投资者，擅长评估企业护城河和经营质量。\n"
        "请基于数据给出客观评估。"
    )

    user = (
        f"请评估 {symbol} 的经营质量和护城河：\n\n"
        f"行业：{industry}\n"
        f"主营：{main_business}\n\n"
        f"**业务构成**\n{comp_text}\n\n"
        f"**增长数据**\n{fin_text}\n\n"
        f"**同业对比**\n{peer_text}\n\n"
        f"请评估：\n"
        f"1. 护城河评分 (0-10)，并说明理由\n"
        f"2. 经营质量评分 (0-10)，并说明理由\n"
        f"3. 核心竞争优势\n"
        f"4. 主要经营风险\n"
        f"5. 是否值得长期跟踪（是/否）"
    )

    return system, user


def _build_catalyst_prompt(
    symbol: str,
    industry: str,
    events: dict,
    financial_summary: dict,
) -> tuple[str, str]:
    news_text = '\n'.join(
        f"  - [{n.get('time', '')}] [{n.get('source', '')}] {n.get('title', '')}"
        for n in (events.get('news') or [])[:8]
    )

    ann_text = '\n'.join(
        f"  - [{a.get('date', '')}] {a.get('title', '')}"
        for a in (events.get('announcements') or [])[:8]
    )

    fin_text = _build_growth_text(financial_summary)

    system = (
        "你是一个事件驱动分析专家，擅长识别和评估催化剂事件。"
    )

    user = (
        f"请分析 {symbol}({industry}) 近期催化因素：\n\n"
        f"**近期新闻**\n{news_text or '暂无'}\n\n"
        f"**近期公告**\n{ann_text or '暂无'}\n\n"
        f"**财务摘要**\n{fin_text}\n\n"
        f"请分析：\n"
        f"1. 短期催化剂（未来1-3个月）\n"
        f"2. 中长期催化剂（3-12个月）\n"
        f"3. 潜在风险事件"
    )

    return system, user


def _build_business_prompt(
    symbol: str,
    intro: dict,
    composition: list[dict],
    profit_forecast: list[dict],
    financial_summary: dict,
    events: dict,
) -> tuple[str, str, str]:
    announcements_text = '\n'.join(
        f"  - [{a['date']}] [{a['type']}] {a['title']}"
        for a in events.get('announcements', [])[:15]
    )
    news_text = '\n'.join(
        f"  - [{n['time']}] [{n['source']}] {n['title']}" + (f"\n    {n['content'][:120]}" if n.get('content') else "")
        for n in events.get('news', [])[:10]
    )

    composition_text = '\n'.join(
        f"  - {c['business_name']}: 收入占比{(c['revenue_pct']*100):.1f}%" + (f", 毛利率{(c['gross_margin']*100):.1f}%" if c.get('gross_margin') is not None else "")
        for c in composition
        if c.get('category_type') == '按产品分类' and c.get('revenue_pct') is not None
    )[:500]

    forecast_text = '\n'.join(
        f"  - {f['analyst']}({f['researcher']}): 2026E EPS={f['eps_2026']}, 2027E={f['eps_2027']}, 2028E={f['eps_2028']}"
        for f in profit_forecast[:8]
    ) if profit_forecast else '暂无预测数据'

    summary_text = ''
    if isinstance(financial_summary, list) and financial_summary:
        r = financial_summary[-1]
        summary_text = (
            f"营收: {r.get('revenue', 'N/A')} | 净利润: {r.get('net_profit', 'N/A')} | "
            f"每股收益: {r.get('eps', 'N/A')} | 净资产: {r.get('equity', 'N/A')}"
        )

    system = (
        "你是一个专业股票分析师，擅长通过多维度数据分析公司价值。"
        "请基于提供的信息给出客观、深入的分析。"
    )

    user = (
        f"请对 {symbol} 进行全面的业务分析：\n\n"
        f"**公司简介**\n{intro.get('main_business', '暂无')[:300] if isinstance(intro, dict) else '暂无'}\n\n"
        f"**业务构成**\n{composition_text or '暂无业务构成数据'}\n\n"
        f"**财务摘要**\n{summary_text or '暂无'}\n\n"
        f"**盈利预测**\n{forecast_text}\n\n"
        f"**近期公告**\n{announcements_text or '暂无'}\n\n"
        f"**新闻舆情**\n{news_text or '暂无'}\n\n"
        f"请从以下方面分析：\n"
        f"1. 业务概览：公司主要做什么，收入结构如何\n"
        f"2. 竞争优势：核心壁垒和护城河\n"
        f"3. 财务健康：关键财务指标分析\n"
        f"4. 成长驱动：未来增长的驱动力\n"
        f"5. 风险提示：需要关注的主要风险\n"
        f"6. 综合评分 (0-10)"
    )

    return system, user, format_llm_input(system, user)