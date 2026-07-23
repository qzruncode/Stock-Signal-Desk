"""Gate ①: verify that the referenced direction is currently tradable."""
from __future__ import annotations

import logging
import re
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.prompts.rubrics import MAINLINE_POSITION

logger = logging.getLogger(__name__)


def _text(value: Any, default: str = "缺失") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _compact_mainline(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": item.get("name"),
        "rank": item.get("rank"),
        "stage": item.get("stage") or item.get("stage_hint"),
        "branches": item.get("branches") or [],
        "reason": item.get("reason"),
        "focus": item.get("focus"),
        "evidence": item.get("evidence") or [],
        "triggers": item.get("triggers") or [],
    }


def _bare_symbol(value: Any) -> str:
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", str(value or ""))
    return match.group(1) if match else str(value or "").strip()


def _compact_sector_rows(payload: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(payload.get("items") or [], start=1):
        if not isinstance(item, dict) or not item.get("name"):
            continue
        rows.append({
            "name": item.get("name"),
            "rank": item.get("rank") or index,
            "flow_rank": item.get("flow_rank"),
            "change_pct": item.get("change_pct") if item.get("change_pct") is not None else item.get("pct_chg"),
            "net_flow": item.get("net_flow") if item.get("net_flow") is not None else item.get("main_net_inflow"),
            "total_amount": item.get("total_amount"),
        })
        if len(rows) >= limit:
            break
    return rows


def _format_mainline_rows(title: str, rows: list[dict[str, Any]], *, candidate: bool = False) -> list[str]:
    lines = [f"### {title}"]
    if not rows:
        lines.append("- 缺失")
        return lines
    for index, item in enumerate(rows, start=1):
        prefix = "候选" if candidate else "当前"
        lines.append(
            f"{index}. {prefix}主线：{_text(item.get('name'))}"
            f"；排名：{_text(item.get('rank'), '-')}"
            f"；阶段：{_text(item.get('stage'), '-')}"
        )
        branches = item.get("branches") or []
        if branches:
            lines.append(f"   - 分支：{'、'.join(str(branch) for branch in branches[:6])}")
        reason = _text(item.get("reason"), "")
        if reason:
            lines.append(f"   - 主线理由：{reason[:260]}")
        focus = _text(item.get("focus"), "")
        if focus:
            lines.append(f"   - 观察重点：{focus[:180]}")
        evidence = item.get("evidence") or []
        if evidence:
            lines.append(f"   - 主线证据：{'；'.join(str(piece) for piece in evidence[:4])[:360]}")
        triggers = item.get("triggers") or []
        if triggers:
            lines.append(f"   - 升级触发：{'；'.join(str(trigger) for trigger in triggers[:4])[:240]}")
    return lines


class MainlinePositionEvaluator(BaseCriterionEvaluator):
    criterion_id = "mainline_position"
    criterion_name = "市场环境与主线强度"
    index = 0

    def collect_data(self, symbol: str, stock_info: dict[str, Any], pre_fetched_data: dict[str, Any] | None = None) -> CriterionEvidence:
        ds = DataService()
        raw: dict[str, Any] = {}

        raw["market_subject"] = {
            "symbol": stock_info.get("symbol") or symbol,
            "name": stock_info.get("name") or stock_info.get("short_name"),
            "short_name": stock_info.get("short_name"),
            "fallback_industry_direction": stock_info.get("industry"),
            "investment_thesis": stock_info.get("_investment_thesis"),
        }

        thesis = str(stock_info.get("_investment_thesis") or "").strip()
        thesis_context = stock_info.get("_investment_thesis_context")
        if thesis_context:
            try:
                candidates = ds.get_investment_thesis_candidates(thesis_context)
                target = _bare_symbol(stock_info.get("symbol") or symbol)
                matched = next(
                    (
                        item for item in candidates.get("items") or []
                        if isinstance(item, dict) and _bare_symbol(item.get("symbol")) == target
                    ),
                    None,
                )
                raw["thesis_membership"] = {
                    "requested_domains": candidates.get("requested_domains") or [],
                    "context_themes": candidates.get("inferred_context_themes") or [],
                    "company_matched": bool(matched),
                    "matched_domains": (matched or {}).get("matched_domains") or [],
                    "lookup_themes": (matched or {}).get("lookup_themes") or [],
                    "boards": (matched or {}).get("boards") or [],
                    "sources": (matched or {}).get("sources") or [],
                    "coverage_complete": not bool(candidates.get("partial")),
                    "decision_boundary": candidates.get("decision_boundary"),
                    "warnings": candidates.get("warnings") or [],
                }
            except Exception as exc:
                logger.warning("[mainline] thesis membership failed: %s", exc)
                raw["thesis_membership_error"] = str(exc)

        try:
            market_report = ds.get_market_mainline_report()
            raw["market_mainline_report"] = {
                "report_pending": bool(market_report.get("report_pending")),
                "as_of_date": market_report.get("as_of_date"),
                "overview": market_report.get("overview"),
                "market_stage": market_report.get("market_stage"),
                "current_mainlines": [
                    _compact_mainline(item)
                    for item in _list_of_dicts(market_report.get("current_mainlines"))[:5]
                ],
                "future_mainlines": [
                    _compact_mainline(item)
                    for item in _list_of_dicts(market_report.get("future_mainlines"))[:5]
                ],
            }
        except Exception as exc:
            logger.warning("[mainline] market_mainline_report failed: %s", exc)
            raw["market_mainline_report_error"] = str(exc)

        # Market leadership is a multi-horizon judgement.  Keep current/five/ten-day
        # capital evidence and stock-relative-strength confirmation separate
        # from any one headline or one board rank.
        for period in ("today", "5d", "10d"):
            try:
                payload = ds.get_sector_flow("concept", period)
                all_records = [
                    item for item in payload.get("records") or []
                    if isinstance(item, dict)
                ]
                membership = raw.get("thesis_membership") or {}
                exact_names = {
                    str(value).strip().lower()
                    for key in ("requested_domains", "context_themes", "lookup_themes", "boards")
                    for value in membership.get(key) or []
                    if str(value).strip()
                }
                retained_records = all_records[:30]
                retained_names = {
                    str(item.get("name") or "").strip().lower()
                    for item in retained_records
                }
                retained_records.extend(
                    item for item in all_records
                    if str(item.get("name") or "").strip().lower() in exact_names
                    and str(item.get("name") or "").strip().lower() not in retained_names
                )
                raw[f"concept_flow_{period}"] = {
                    "records": [
                        {
                            "name": item.get("name"),
                            "pct_chg": item.get("pct_chg"),
                            "main_net_inflow": item.get("main_net_inflow"),
                            "main_net_inflow_pct": item.get("main_net_inflow_pct"),
                            "main_flow_rank": item.get("main_flow_rank"),
                        }
                        for item in retained_records
                    ],
                    "sector_count": payload.get("sector_count"),
                    "data_time": payload.get("data_time"),
                    "is_stale": payload.get("is_stale"),
                }
            except Exception as exc:
                raw[f"concept_flow_{period}_error"] = str(exc)
        for key, collect in (
            ("relative_quote", lambda: ds.get_realtime_quote(symbol)),
            ("relative_technical", lambda: ds.get_technical_indicators(symbol, count=120)),
        ):
            try:
                raw[key] = collect()
            except Exception as exc:
                raw[f"{key}_error"] = str(exc)
        try:
            news = ds.search_news(symbol, days=30)
            raw["recent_company_news"] = {
                "items": [
                    {
                        "publish_time": item.get("publish_time") or item.get("date"),
                        "source": item.get("source"),
                        "title": item.get("title"),
                        "summary": str(item.get("summary") or "")[:360],
                        "url": item.get("url") or item.get("link"),
                    }
                    for item in _list_of_dicts(news.get("items"))[:12]
                ],
                "data_time": news.get("data_time"),
                "is_stale": news.get("is_stale"),
            }
        except Exception as exc:
            raw["recent_company_news_error"] = str(exc)

        # Preserve the uncompressed current board layer.  The market report is
        # an executive summary and cannot enumerate every valid branch.
        for sector_type, limit in (("concept", 20), ("industry", 15)):
            try:
                sectors = ds.get_sector_list(sector_type)
                full_items = [
                    item for item in sectors.get("items") or [] if isinstance(item, dict)
                ]
                flow_names = [
                    str(item.get("name") or "").strip()
                    for item in sorted(
                        full_items,
                        key=lambda row: (
                            row.get("net_flow") if row.get("net_flow") is not None else row.get("main_net_inflow")
                        ) or float("-inf"),
                        reverse=True,
                    )
                    if item.get("name") and (
                        item.get("net_flow") is not None or item.get("main_net_inflow") is not None
                    )
                ]
                flow_rank_by_name = {
                    name: index for index, name in enumerate(dict.fromkeys(flow_names), start=1)
                }
                full_items = [
                    {
                        **item,
                        "rank": item.get("rank") or index,
                        "flow_rank": flow_rank_by_name.get(str(item.get("name") or "").strip()),
                    }
                    for index, item in enumerate(full_items, start=1)
                ]
                raw[f"{sector_type}_sectors"] = {
                    "items": _compact_sector_rows({"items": full_items}, limit=limit),
                    "data_time": sectors.get("data_time"),
                    "universe_count": len(full_items),
                }
                if sector_type == "concept":
                    membership = raw.get("thesis_membership") or {}
                    exact_names = {
                        str(value).strip().lower()
                        for key in ("requested_domains", "context_themes", "lookup_themes", "boards")
                        for value in membership.get(key) or []
                        if str(value).strip()
                    }
                    related: list[dict[str, Any]] = []
                    for index, item in enumerate(full_items, start=1):
                        if str(item.get("name") or "").strip().lower() not in exact_names:
                            continue
                        related.extend(_compact_sector_rows({"items": [{**item, "rank": item.get("rank") or index}]}, limit=1))
                    raw["concept_sectors"]["thesis_related_items"] = related
                if sector_type == "industry":
                    industry = stock_info.get("industry", "")
                    for index, item in enumerate(full_items, start=1):
                        if industry and isinstance(item, dict) and item.get("name") == industry:
                            raw["target_industry_rank"] = {
                                "name": item.get("name"),
                                "rank": item.get("rank") or index,
                                "change_pct": item.get("change_pct"),
                                "net_flow": item.get("net_flow") if item.get("net_flow") is not None else item.get("main_net_inflow"),
                                "total_amount": item.get("total_amount"),
                            }
                            break
            except Exception as exc:
                logger.warning("[mainline] %s sector_list failed: %s", sector_type, exc)
                raw[f"{sector_type}_sectors_error"] = str(exc)

        # Build summary
        subject = raw["market_subject"]
        report = raw.get("market_mainline_report") or {}
        market_stage = report.get("market_stage") or {}
        current_mainlines = _list_of_dicts(report.get("current_mainlines"))
        future_mainlines = _list_of_dicts(report.get("future_mainlines"))
        lines = [
            "## 本关判断对象",
            f"- 观察标的：{_text(subject.get('name'))} ({_text(subject.get('symbol') or symbol)})",
            f"- 本轮产业方向：{_text(subject.get('investment_thesis'), '未指定；使用所属行业作为退化方向')}",
            f"- 退化行业方向：{_text(subject.get('fallback_industry_direction'))}",
            "- 本关只判断该方向当前是否具有主线、活跃分支或事件驱动交易条件；不判断公司主营、订单、收入和竞争力。",
            "",
            "## 最新市场主线报告",
            f"- 报告日期：{_text(report.get('as_of_date'))}",
            f"- 报告状态：{'获取失败' if raw.get('market_mainline_report_error') else ('生成中/不可用' if report.get('report_pending') else '可用')}",
            f"- 市场阶段：{_text(market_stage.get('label'))}",
            f"- 总览：{_text(report.get('overview'))}",
            "",
            *_format_mainline_rows("当前主线/分支主线", current_mainlines),
            "",
            *_format_mainline_rows("候选主线（仅作观察，不等同于当前主线）", future_mainlines, candidate=True),
            "",
            "## 未压缩的当前板块证据",
        ]
        for title, key in (("概念板块", "concept_sectors"), ("行业板块", "industry_sectors")):
            payload = raw.get(key) or {}
            rows = _list_of_dicts(payload.get("items"))
            lines.append(f"### {title}（{_text(payload.get('data_time'))}）")
            related_rows = _list_of_dicts(payload.get("thesis_related_items"))
            if related_rows:
                lines.append("- 本轮产业方向对应的当前板块（从全量列表精确定位，不受Top N截断）：")
                for item in related_rows:
                    lines.append(
                        f"  - {item.get('name')}：涨幅排名{_text(item.get('rank'), '?')}/{_text(payload.get('universe_count'), '?')}，"
                        f"涨跌幅{_text(item.get('change_pct'), '?')}%；"
                        "资金金额统一以下方板块资金流接口为准"
                    )
            if not rows:
                lines.append("- 缺失")
                continue
            for item in rows:
                lines.append(
                    f"- 第{_text(item.get('rank'), '?')}名 {item.get('name')}："
                    f"涨跌幅{_text(item.get('change_pct'), '?')}%"
                )

        membership = raw.get("thesis_membership") or {}
        lines.extend(["", "## 结构化产业方向成员关系（仅证明映射，不证明业绩）"])
        if membership:
            lines.append(f"- 请求方向：{'、'.join(str(item) for item in membership.get('requested_domains') or []) or '缺失'}")
            lines.append(f"- 上位产业主题：{'、'.join(str(item) for item in membership.get('context_themes') or []) or '无'}")
            lines.append(f"- 公司是否命中对应板块成分：{'是' if membership.get('company_matched') else '否'}")
            lines.append(f"- 候选召回覆盖方向：{'、'.join(str(item) for item in membership.get('matched_domains') or []) or '无'}（不可据此声称公司拥有对应产品）")
            lines.append(f"- 查询板块：{'、'.join(str(item) for item in membership.get('lookup_themes') or []) or '无'}")
            lines.append(f"- 实际成分板块：{'、'.join(str(item) for item in membership.get('boards') or []) or '无'}")
            for source in _list_of_dicts(membership.get("sources"))[:6]:
                lines.append(
                    f"- 来源：{_text(source.get('name'))} / {_text(source.get('board'))} / "
                    f"{_text(source.get('date'))} / {_text(source.get('url'))}"
                )
        elif raw.get("thesis_membership_error"):
            lines.append(f"- 获取失败：{raw['thesis_membership_error']}")
        else:
            lines.append("- 本轮未指定产业方向")

        exact_names = {
            str(value).strip().lower()
            for key in ("requested_domains", "context_themes", "lookup_themes", "boards")
            for value in membership.get(key) or []
            if str(value).strip()
        }
        lines.extend(["", "## 本轮方向的当日/5日/10日资金与涨幅确认"])
        for period in ("today", "5d", "10d"):
            payload = raw.get(f"concept_flow_{period}") or {}
            matched = [
                item
                for item in payload.get("records") or []
                if str(item.get("name") or "").strip().lower() in exact_names
            ]
            if not matched:
                lines.append(
                    f"- {period}：未取得与结构化方向同名的板块记录"
                )
                continue
            for item in matched:
                lines.append(
                    f"- {period} {item.get('name')}：涨跌幅{_text(item.get('pct_chg'), '?')}%，"
                    f"主力净流入{_text(item.get('main_net_inflow'), '?')}，"
                    f"资金排名{_text(item.get('main_flow_rank'), '?')}/"
                    f"{_text(payload.get('sector_count'), '?')}"
                )

        quote = raw.get("relative_quote") or {}
        indicators = (raw.get("relative_technical") or {}).get("indicators") or {}
        lines.extend([
            "",
            "## 个股相对强度与近期催化确认",
            f"- 现价：{_text(quote.get('price'))}；当日涨跌："
            f"{_text(quote.get('change_pct') if quote.get('change_pct') is not None else quote.get('pct_chg'))}%；"
            f"成交额：{_text(quote.get('amount'))}；数据时间：{_text(quote.get('data_time'))}",
            f"- MA20/MA60：{_text(indicators.get('ma20'))}/{_text(indicators.get('ma60'))}；"
            f"RSI14：{_text(indicators.get('rsi14'))}；20日高点：{_text(indicators.get('high_20d'))}；"
            f"5/20/60日收益：{_text(indicators.get('return_5d_pct'))}%/"
            f"{_text(indicators.get('return_20d_pct'))}%/{_text(indicators.get('return_60d_pct'))}%；"
            f"量能/前5日：{_text(indicators.get('volume_vs_prev5d'))}",
        ])
        recent_news = _list_of_dicts((raw.get("recent_company_news") or {}).get("items"))
        if recent_news:
            for item in recent_news[:8]:
                lines.append(
                    f"- {item.get('publish_time') or item.get('date') or '未知日期'} "
                    f"{item.get('source') or '公开资讯'}：{item.get('title') or '无标题'}；"
                    f"{str(item.get('summary') or '')[:240]}"
                )
        else:
            lines.append("- 近30日公司级催化/相对强度资讯缺失")

        lines.extend(["", "## 其他辅助数据（不能单独作为通过依据）"])
        auxiliary_added = False
        if "target_industry_rank" in raw:
            r = raw["target_industry_rank"]
            lines.append(f"- 行业[{r['name']}]板块排名第{r.get('rank', '?')}名，涨跌幅{r.get('change_pct', '?')}%")
            auxiliary_added = True
        if not auxiliary_added:
            lines.append("- 缺失")
        lines.extend([
            "",
            "## 判断约束",
            "- 严禁用公司旧主营简介来否定或证明市场主线；公司是否真实受益由第二关用正式披露独立判断。",
            "- 最新市场主线报告是高层摘要，不是封闭白名单；没有逐字列出的细分分支，仍可由未压缩板块证据和结构化产业方向证据证明属于当前主线。",
            "- 单日板块涨跌、资金流排名或概念板块成员关系都不能单独判为通过或不通过；至少核对多周期板块、资金、成交、个股相对强度和事件催化。",
            "- 概念板块池包含数百个行业、风格和重复口径，不能用某一分支的绝对涨幅序号直接否定其主线属性；还必须检查净流入排名、上位主题共振及公司产业位置。",
            "- 本项只判断市场环境、方向强度与个股交易确认，不在这里判定公司的订单、收入或产业竞争力；这些由下一维度核验。",
            "- 单日板块回撤不等于中期主线结束，必须结合5日/10日资金、上位主题、个股相对强度和近期可回查催化。",
            "- 若板块中期数据偏弱，但标的存在近期可回查事件、显著放量和独立相对强度，可判为事件驱动的活跃分支；必须明确它不是板块普涨主线。",
            "- 公司级新闻只用于确认市场正在交易什么逻辑，不能单独证明业务收入或竞争优势。",
            "- 请求方向、候选召回覆盖方向和板块别名只是市场方向映射；verdict 不得把它们改写成公司主营或收入。",
            "- 候选主线只能作为观察方向，不能判为通过。",
            "- 高层报告暂不可用时，使用未压缩板块、5日/10日资金、上位主题和个股相对强度交叉核验；关键数据整体缺失时必须标为证据不足，不能写成方向明确不成立。",
        ])

        summary = "\n".join(lines)
        return CriterionEvidence(raw_data=raw, data_summary=summary)

    def get_rubric(self) -> str:
        return MAINLINE_POSITION

    def evidence_failure_reason(self, evidence: CriterionEvidence) -> str | None:
        raw = evidence.raw_data
        report = raw.get("market_mainline_report") or {}
        has_report = bool(
            not report.get("report_pending")
            and report.get("as_of_date")
            and report.get("current_mainlines")
        )
        concept = raw.get("concept_sectors") or {}
        industry = raw.get("industry_sectors") or {}
        membership = raw.get("thesis_membership") or {}
        has_direct_branch_evidence = bool(
            concept.get("data_time")
            and concept.get("items")
            and membership.get("company_matched")
            and membership.get("requested_domains")
        )
        has_market_context = bool(
            industry.get("data_time")
            and industry.get("items")
            and (raw.get("market_subject") or {}).get("fallback_industry_direction")
        )
        has_multi_period_flow = any(
            (raw.get(f"concept_flow_{period}") or {}).get("records")
            for period in ("today", "5d", "10d")
        )
        if not has_report and not has_direct_branch_evidence and not has_market_context and not has_multi_period_flow:
            return "主线报告、板块、资金和交易确认数据均获取失败或不可用，当前市场主线维度无法判断"
        return None
