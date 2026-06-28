# -*- coding: utf-8 -*-
"""Market-regime inference, stage judgment, rule-based themes and policy analysis."""

from __future__ import annotations

from typing import Any

from ._utils import (
    RANK_LABELS,
    STAGE_DESCRIPTIONS,
    STRATEGIC_NARRATIVES,
    infer_stage,
    normalize_texts,
    pick_theme_profile,
    safe_float,
    shorten,
    strip_html,
)


def summarize_market_regime(market_status: dict[str, Any], breadth: dict[str, Any]) -> str:
    regime_bits = []
    if (market_status.get("north_flow") or 0) > 0:
        regime_bits.append("北向资金偏正")
    if (breadth.get("advance_decline_ratio") or 0) >= 1.2:
        regime_bits.append("风险偏好扩散")
    if (breadth.get("limit_down_count") or 0) >= (breadth.get("limit_up_count") or 0):
        regime_bits.append("高低切或防御倾向")
    if (breadth.get("advance_decline_ratio") or 0) < 0.5 and (market_status.get("limit_up_count") or 0) >= 50:
        return "普跌环境下的少数主线抱团"
    if regime_bits:
        return " / ".join(regime_bits)
    return "结构性轮动"


def build_market_stage(market_status: dict[str, Any], breadth: dict[str, Any], themes: list[dict[str, Any]]) -> dict[str, str]:
    leading_stage = themes[0]["stage"] if themes else "预热期"
    adv_ratio = breadth.get("advance_decline_ratio") or 0
    if adv_ratio < 0.5 and leading_stage in ("发酵期", "加速期", "分歧期"):
        return {
            "label": "结构牛中后段 / 主线抱团期",
            "description": "大盘并不是全面普涨，而是少数强主线在普跌环境里抱团推进，市场已经进入强弱分化很明显的阶段。",
        }
    if adv_ratio >= 1 and leading_stage in ("发酵期", "加速期"):
        return {
            "label": "主线扩散期",
            "description": "主线不再只是个别品种活跃，而是开始向分支扩散，市场正在从方向确认走向板块共振。",
        }
    return {
        "label": "业绩验证期",
        "description": "市场已经过了单纯拔估值阶段，接下来更看重产业趋势、订单和盈利兑现是否跟得上。",
    }


def build_trade_action(theme: dict[str, Any]) -> str:
    stage = str(theme.get("stage") or "")
    name = str(theme.get("name") or "该主线")
    if stage == "加速期":
        return f"{name} 已经进入一致性很强的阶段，更适合做核心分支和龙头，而不是追边缘题材。"
    if stage == "分歧期":
        return f"{name} 已经出现高位分化，后续只能看兑现能力更强的细分，不能再按普涨思路交易。"
    if stage == "发酵期":
        return f"{name} 还在板块扩散阶段，适合沿着产业趋势和资金共振去找中军与低位补涨。"
    return f"{name} 仍在方向确认阶段，更适合观察是否有进一步政策和产业验证。"


def build_deep_summary(market_stage: dict[str, Any], lifecycle_notes: list[dict[str, Any]], future_outlook: list[dict[str, Any]]) -> str:
    current = "、".join(f"{item['theme']}处于{item['stage']}" for item in lifecycle_notes[:3]) or "当前主线仍不够清晰"
    future = "、".join(item["name"] for item in future_outlook[:3]) or "暂时没有特别明确的接棒方向"
    return f"整体来看，市场处在{market_stage.get('label')}，当前最值得关注的是{current}；未来更有预期差的方向主要看{future}。"


def collect_headlines(rss_snapshot: dict[str, Any]) -> list[str]:
    headlines: list[str] = []
    for feed in rss_snapshot.values():
        for item in (feed.get("items") or [])[:6]:
            title = str(item.get("title") or "").strip()
            summary = strip_html(str(item.get("summary") or ""))
            if title:
                headlines.append(f"{title} {summary}".strip())
    return normalize_texts(headlines)


def build_rule_themes(snapshot: dict[str, Any], headlines: list[str]) -> list[dict[str, Any]]:
    candidates = (
        list(snapshot["concept_flow"]["inflow_top"])[:12]
        + list(snapshot["industry_flow"]["inflow_top"])[:12]
        + list(snapshot["concept_sectors"])[:12]
        + list(snapshot["industry_sectors"])[:12]
    )
    grouped: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for item in candidates:
        name = str(item.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        matched = [h for h in headlines if any(key.lower() in h.lower() for key in [name, name.replace("概念", ""), name.replace("行业", "")])][:3]
        profile = pick_theme_profile(name, matched)
        key = profile["title"]
        bucket = grouped.setdefault(key, {
            "title": key,
            "profile": profile,
            "items": [],
            "headlines": [],
            "score": 0.0,
            "flow": 0.0,
            "pct_values": [],
        })
        pct = safe_float(item.get("change_pct") or item.get("pct_chg"))
        flow = safe_float(item.get("main_net_inflow") or item.get("net_flow"))
        bucket["items"].append(item)
        bucket["headlines"].extend(matched)
        bucket["pct_values"].append(pct or 0.0)
        bucket["flow"] += flow or 0.0
        bucket["score"] += (pct or 0.0) * 8 + (1.5 if (flow or 0.0) > 0 else 0) + len(matched) * 2

    narratives: list[dict[str, Any]] = []
    for narrative in STRATEGIC_NARRATIVES:
        matched_buckets: list[dict[str, Any]] = []
        for title, bucket in grouped.items():
            component_names = [str(entry.get("name") or "").strip() for entry in bucket["items"] if entry.get("name")]
            haystack = " ".join([title, *component_names, *bucket["headlines"]]).lower()
            if title in narrative["titles"] or any(keyword.lower() in haystack for keyword in narrative["keywords"]):
                matched_buckets.append(bucket)

        if not matched_buckets:
            continue
        all_items = []
        all_components: list[str] = []
        all_headlines: list[str] = []
        total_flow = 0.0
        total_score = 0.0
        pct_values: list[float] = []
        for bucket in matched_buckets:
            all_items.extend(bucket["items"])
            all_components.extend(str(entry.get("name") or "").strip() for entry in bucket["items"] if entry.get("name"))
            all_headlines.extend(bucket["headlines"])
            total_flow += bucket["flow"]
            total_score += bucket["score"]
            pct_values.extend(bucket["pct_values"])

        dedup_components = list(dict.fromkeys([name for name in all_components if name]))[:6]
        headline_hits = normalize_texts(all_headlines)[:5]
        avg_change = sum(pct_values) / max(len(pct_values), 1)
        stage, stage_reason = infer_stage(avg_change, total_flow, len(headline_hits))
        thesis = (
            f"{narrative['name']} 当前能进入主线梯队，核心不是单个板块异动，而是 "
            f"{' / '.join(dedup_components[:4]) or narrative['name']} 这些分支在同一叙事里形成共振。"
        )
        evidence = [
            f"分支共振：{' / '.join(dedup_components) if dedup_components else '--'}",
            f"板块均值涨幅：{avg_change:.2f}%",
            f"资金净流入：{total_flow:.0f}",
        ]
        evidence.extend(shorten(headline, 90) for headline in headline_hits[:2])
        narratives.append({
            "name": narrative["name"],
            "stage": stage,
            "thesis": thesis,
            "policy_signal": narrative["policy"],
            "industry_trend": narrative["industry"],
            "valuation_view": f"{narrative['valuation']} {build_valuation_basis(narrative['name'], narrative['name'])}",
            "expectation_view": "后续重点看政策细则、订单兑现、盈利上修三者里至少有一项是否继续强化。",
            "risks": [
                "如果只有短线资金博弈、没有中期产业或盈利验证，主线持续性会明显下降。",
                "如果估值走在基本面前面太多，后续很容易提前进入分歧期。",
            ],
            "evidence": evidence[:5],
            "stage_note": STAGE_DESCRIPTIONS.get(stage, ""),
            "stage_reason": f"{stage_reason} 当前主要由 {' / '.join(dedup_components[:4]) or narrative['name']} 贡献强度。",
            "components": dedup_components,
            "_score": total_score + len(dedup_components) * 2,
        })
    narratives.sort(key=lambda item: item.get("_score", 0), reverse=True)
    themes: list[dict[str, Any]] = []
    for index, item in enumerate(narratives[:3]):
        item["rank_label"] = RANK_LABELS[index] if index < len(RANK_LABELS) else f"主线{index + 1}"
        item.pop("_score", None)
        themes.append(item)
    return themes


def build_next_themes(snapshot: dict[str, Any], headlines: list[str]) -> list[dict[str, str]]:
    current_titles = {theme["name"] for theme in build_rule_themes(snapshot, headlines)}
    candidates: list[dict[str, Any]] = []
    for narrative in STRATEGIC_NARRATIVES:
        title = narrative["name"]
        if title in current_titles:
            continue
        count = sum(1 for headline in headlines if any(keyword.lower() in headline.lower() for keyword in narrative["keywords"]))
        if count <= 0:
            continue
        candidates.append({
            "name": title,
            "score": count,
            "why_now": f"最近公开信息里已经出现与“{title}”相关的政策、产业或研究线索，但还没形成足够强的资金与板块共振，因此更适合放进候选池而不是直接定义成主线。",
            "trigger": "需要再看到更明确的政策落地、订单/招标验证，或者龙头盈利预期上修，才有机会升级成下一阶段主线。",
        })
    candidates.sort(key=lambda item: item["score"], reverse=True)
    results = [{"name": item["name"], "why_now": item["why_now"], "trigger": item["trigger"]} for item in candidates[:4]]
    if results:
        return results

    first_concepts = [item["name"] for item in STRATEGIC_NARRATIVES if item["name"] not in current_titles][:3]
    return [
        {
            "name": name,
            "why_now": "当前板块热度已经抬升，但还需要政策和产业证据进一步确认。",
            "trigger": "观察是否出现连续催化、龙头超预期表现或行业基本面改善。",
        }
        for name in first_concepts
    ]


def build_policy_watchlist(headlines: list[str]) -> list[str]:
    watchlist: list[str] = []
    samples = [
        ("政策表述是否从方向性鼓励升级到可执行细则", ["政策", "会议", "方案", "意见", "支持"]),
        ("产业订单、招标、资本开支是否开始验证主线逻辑", ["订单", "招标", "扩产", "资本开支", "投资"]),
        ("龙头公司业绩指引是否带来未来两个季度的盈利上修", ["业绩", "预告", "财报", "超预期", "指引"]),
        ("估值修复是否已经透支未来预期", ["估值", "涨停", "新高", "大涨"]),
    ]
    haystack = " ".join(headlines).lower()
    for text, keys in samples:
        if any(key.lower() in haystack for key in keys):
            watchlist.append(text)
    if not watchlist:
        watchlist.extend([
            "后续政策是否有更明确的执行细则",
            "行业需求和订单是否开始验证景气改善",
            "龙头公司估值是否已经提前透支未来预期",
        ])
    return watchlist[:4]


def build_valuation_basis(theme_name: str, theme_profile_title: str) -> str:
    if theme_profile_title in ("金融地产", "资源周期"):
        return "当前更适合结合行业 PB、股息率或资产重估逻辑评估估值安全边际。"
    if theme_profile_title in ("人工智能", "半导体", "医药创新", "军工"):
        return "当前更适合结合龙头 PE/PS 与盈利兑现速度，判断预期是否透支。"
    if "新能源" in theme_name or theme_profile_title == "新能源":
        return "当前更适合结合行业出清进度、单位盈利和龙头估值分位判断修复空间。"
    return "建议继续补充龙头公司估值分位和盈利预测变化，确认性价比。"