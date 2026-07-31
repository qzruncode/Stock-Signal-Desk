"""Gate ①: verify that the requested industry direction is a current mainline."""

from __future__ import annotations

import logging
import re
from typing import Any

from src.services.buy_criteria.base import BaseCriterionEvaluator, CriterionEvidence
from src.services.buy_criteria.data_service import DataService
from src.services.buy_criteria.mainline_policy import (
    MainlineStrategyProfile,
    mainline_strategy_label,
    normalize_mainline_strategy,
)
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


def _bare_symbol(value: Any) -> str:
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", str(value or ""))
    return match.group(1) if match else str(value or "").strip()


def _compact_mainline(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": item.get("name"),
        "rank": item.get("rank"),
        "lifecycle": item.get("lifecycle"),
        "stage": item.get("stage") or item.get("stage_hint"),
        "branches": item.get("branches") or [],
        "expected_horizon": item.get("expected_horizon"),
        "evidence_axes": item.get("evidence_axes") or [],
        "reason": item.get("reason"),
        "focus": item.get("focus"),
        "evidence": item.get("evidence") or [],
        "triggers": item.get("triggers") or [],
        "trigger_assessments": item.get("trigger_assessments") or [],
    }


def _format_mainline_rows(
    title: str,
    rows: list[dict[str, Any]],
    *,
    candidate: bool = False,
) -> list[str]:
    lines = [f"### {title}"]
    if not rows:
        return [*lines, "- 缺失"]
    for index, item in enumerate(rows, start=1):
        prefix = "候选" if candidate else "当前"
        lines.append(f"{index}. {prefix}主线：{_text(item.get('name'))}" f"；阶段：{_text(item.get('stage'), '-')}")
        branches = item.get("branches") or []
        if branches:
            lines.append(f"   - 标准板块映射：" f"{'、'.join(str(branch) for branch in branches[:8])}")
        reason = _text(item.get("reason"), "")
        if reason:
            lines.append(f"   - 中期叙事依据：{reason[:320]}")
        expected_horizon = _text(item.get("expected_horizon"), "")
        if expected_horizon:
            lines.append(f"   - 预期窗口：{expected_horizon}")
        evidence_axes = _list_of_dicts(item.get("evidence_axes"))
        if evidence_axes:
            lines.append(
                "   - 已绑定证据维度："
                + "、".join(
                    str(axis.get("axis") or "") for axis in evidence_axes if str(axis.get("axis") or "").strip()
                )
            )
        evidence = item.get("evidence") or []
        if evidence:
            lines.append(f"   - 可回查证据：" f"{'；'.join(str(piece) for piece in evidence[:5])[:480]}")
        triggers = item.get("triggers") or []
        if triggers:
            lines.append(f"   - 后续触发：" f"{'；'.join(str(trigger) for trigger in triggers[:4])[:280]}")
        trigger_assessments = _list_of_dicts(item.get("trigger_assessments"))
        if trigger_assessments:
            lines.append(
                "   - 触发进度："
                + "；".join(
                    (f"{_text(trigger.get('description'))}" f"[{_text(trigger.get('status'), 'unknown')}]")
                    for trigger in trigger_assessments[:4]
                )[:420]
            )
    return lines


class MainlinePositionEvaluator(BaseCriterionEvaluator):
    criterion_id = "mainline_position"
    criterion_name = "当前市场主线"
    index = 0

    def collect_data(
        self,
        symbol: str,
        stock_info: dict[str, Any],
        pre_fetched_data: dict[str, Any] | None = None,
    ) -> CriterionEvidence:
        ds = DataService()
        scope_only = bool(stock_info.get("_scope_only"))
        strategy_profile = normalize_mainline_strategy(stock_info.get("_mainline_strategy"))
        raw: dict[str, Any] = {
            "market_subject": {
                "scope_only": scope_only,
                "symbol": (None if scope_only else stock_info.get("symbol") or symbol),
                "name": (
                    "本轮结构化产业方向" if scope_only else stock_info.get("name") or stock_info.get("short_name")
                ),
                "investment_thesis": stock_info.get("_investment_thesis"),
                "fallback_industry_direction": (None if scope_only else stock_info.get("industry")),
                "mainline_strategy": strategy_profile.value,
            },
        }

        thesis_context = stock_info.get("_investment_thesis_context")
        if thesis_context:
            if scope_only:
                domains = [item for item in thesis_context.get("domains") or [] if isinstance(item, dict)]
                raw["thesis_membership"] = {
                    "requested_domains": [
                        str(item.get("label") or "").strip() for item in domains if str(item.get("label") or "").strip()
                    ],
                    "matched_domains": [],
                    "lookup_themes": list(
                        dict.fromkeys(
                            str(query).strip()
                            for item in domains
                            for query in item.get("board_queries") or []
                            if str(query).strip()
                        )
                    ),
                    "boards": list(
                        dict.fromkeys(
                            str(query).strip()
                            for item in domains
                            for query in item.get("board_queries") or []
                            if str(query).strip()
                        )
                    ),
                    "sources": ["typed_investment_thesis_context"],
                    "coverage_complete": all(
                        item.get("mapping_type") == "catalog_binding" and bool(item.get("board_queries"))
                        for item in domains
                    ),
                    "decision_boundary": ("仅判断结构化产业方向，不判断任何候选公司。"),
                    "warnings": [],
                }
            else:
                try:
                    candidates = ds.get_investment_thesis_candidates(thesis_context)
                    target = _bare_symbol(stock_info.get("symbol") or symbol)
                    matched = next(
                        (
                            item
                            for item in candidates.get("items") or []
                            if isinstance(item, dict) and _bare_symbol(item.get("symbol")) == target
                        ),
                        None,
                    )
                    raw["thesis_membership"] = {
                        "requested_domains": candidates.get("requested_domains") or [],
                        "matched_domains": (matched or {}).get("matched_domains") or [],
                        "lookup_themes": (matched or {}).get("lookup_themes") or [],
                        "boards": (matched or {}).get("boards") or [],
                        "sources": (matched or {}).get("sources") or [],
                        "coverage_complete": not bool(candidates.get("partial")),
                        "decision_boundary": candidates.get("decision_boundary"),
                        "warnings": candidates.get("warnings") or [],
                    }
                except Exception as exc:
                    logger.warning("[mainline] thesis mapping failed: %s", exc)
                    raw["thesis_membership_error"] = str(exc)

        try:
            frozen_snapshot = (
                pre_fetched_data.get("market_mainline_snapshot") if isinstance(pre_fetched_data, dict) else None
            )
            if frozen_snapshot is not None:
                if not isinstance(frozen_snapshot, dict):
                    raise TypeError("冻结市场主线快照必须是对象")
                market_report = frozen_snapshot
                raw["market_mainline_snapshot_source"] = "frozen_batch_snapshot"
                raw["market_mainline_snapshot_id"] = frozen_snapshot.get("snapshot_id")
            else:
                market_report = ds.get_market_mainline_report()
                raw["market_mainline_snapshot_source"] = "database_read"
            raw["market_mainline_report"] = {
                "report_pending": bool(market_report.get("report_pending")),
                "as_of_date": market_report.get("as_of_date"),
                "overview": market_report.get("overview"),
                "market_stage": market_report.get("market_stage"),
                "current_mainlines": [
                    _compact_mainline(item) for item in _list_of_dicts(market_report.get("current_mainlines"))[:5]
                ],
                "future_mainlines": [
                    _compact_mainline(item)
                    for item in _list_of_dicts(
                        market_report.get("candidate_mainlines") or market_report.get("future_mainlines")
                    )[:5]
                ],
            }
        except Exception as exc:
            logger.warning("[mainline] market report failed: %s", exc)
            raw["market_mainline_report_error"] = str(exc)

        membership = raw.get("thesis_membership") or {}
        exact_names = {
            str(value).strip().casefold()
            for key in ("requested_domains", "lookup_themes", "boards")
            for value in membership.get(key) or []
            if str(value).strip()
        }
        fallback_industry = "" if scope_only else str(stock_info.get("industry") or "").strip()
        if fallback_industry:
            exact_names.add(fallback_industry.casefold())

        board_catalog: dict[str, Any] = {}
        for sector_type in ("industry", "concept"):
            try:
                payload = ds.get_sector_list(sector_type)
                all_items = [item for item in payload.get("items") or [] if isinstance(item, dict) and item.get("name")]
                board_catalog[sector_type] = {
                    "data_time": payload.get("data_time"),
                    "universe_count": len(all_items),
                    "matched_items": [
                        {
                            "name": item.get("name"),
                            "code": item.get("code"),
                            "data_source": item.get("data_source"),
                        }
                        for item in all_items
                        if str(item.get("name") or "").strip().casefold() in exact_names
                    ],
                }
            except Exception as exc:
                logger.warning(
                    "[mainline] %s board catalog failed: %s",
                    sector_type,
                    exc,
                )
                board_catalog[sector_type] = {
                    "data_time": None,
                    "universe_count": 0,
                    "matched_items": [],
                    "errors": [str(exc)],
                }
        raw["board_catalog"] = board_catalog

        subject = raw["market_subject"]
        report = raw.get("market_mainline_report") or {}
        market_stage = report.get("market_stage") or {}
        lines = [
            "## 本关判断对象",
            *(
                ["- 判断层级：本批次共享的结构化产业方向；不含任何公司。"]
                if scope_only
                else [f"- 观察标的：{_text(subject.get('name'))} " f"({_text(subject.get('symbol') or symbol)})"]
            ),
            f"- 本轮产业方向：" f"{_text(subject.get('investment_thesis'), '未指定；使用所属行业作为退化方向')}",
            f"- 主线投资口径：{mainline_strategy_label(strategy_profile)}" f"（{strategy_profile.value}）",
            *([] if scope_only else [f"- 退化行业方向：" f"{_text(subject.get('fallback_industry_direction'))}"]),
            "- 本关只判断该产业方向是否属于未来1—6个月A股主导叙事；" "不判断公司真实受益、短期交易热度、买点或催化。",
            "",
            "## 最新中期市场主线报告",
            f"- 报告日期：{_text(report.get('as_of_date'))}",
            f"- 报告状态："
            f"{'获取失败' if raw.get('market_mainline_report_error') else ('生成中/不可用' if report.get('report_pending') else '可用')}",
            f"- 市场阶段：{_text(market_stage.get('label'))}",
            f"- 总览：{_text(report.get('overview'))}",
            "",
            *_format_mainline_rows(
                "当前主线",
                _list_of_dicts(report.get("current_mainlines")),
            ),
            "",
            *_format_mainline_rows(
                (
                    "候选主线（需满足前瞻准入条件）"
                    if strategy_profile == MainlineStrategyProfile.EARLY_POSITIONING
                    else "候选主线（确认型口径不按当前主线通过）"
                ),
                _list_of_dicts(report.get("future_mainlines")),
                candidate=True,
            ),
            "",
            "## 结构化方向与标准板块映射",
            "- 板块目录只用于名称映射，不是主线证据；" "目录中的涨跌、排名和资金字段均未进入本关。",
        ]

        if membership:
            lines.extend(
                [
                    f"- 请求方向："
                    f"{'、'.join(str(item) for item in membership.get('requested_domains') or []) or '缺失'}",
                    f"- 查询板块：" f"{'、'.join(str(item) for item in membership.get('lookup_themes') or []) or '无'}",
                    f"- 标准板块：" f"{'、'.join(str(item) for item in membership.get('boards') or []) or '无'}",
                ]
            )
        elif raw.get("thesis_membership_error"):
            lines.append(f"- 方向映射失败：{raw['thesis_membership_error']}")

        for sector_type, title in (
            ("industry", "行业目录"),
            ("concept", "概念目录"),
        ):
            payload = board_catalog.get(sector_type) or {}
            matched_items = _list_of_dicts(payload.get("matched_items"))
            lines.append(
                f"- {title}命中："
                + ("、".join(str(item.get("name")) for item in matched_items) if matched_items else "无精确同名项")
            )

        lines.extend(
            [
                "",
                "## 判断约束",
                "- 当前市场主线专指未来1—6个月的主导产业叙事，"
                "必须由机构策略、政策落地、产业供需、技术路线、资本开支或"
                "持续景气证据建立。",
                "- 1—3年结构性趋势只能作为背景，不能单独证明它是当前主线。",
                "- 资金流、涨跌幅、成交排名、均线、技术指标和个股走势"
                "完全不属于本关证据，既不能建立主线，也不能否定主线。",
                "- 板块目录和候选成员关系只用于方向映射，不能证明公司真实受益；"
                "公司产品、订单、收入和竞争力由第二维独立核验。",
                "- 公司事件与未来催化由第六维判断，不能把单家公司事件包装成市场主线。",
                "- 主线报告不可用时，只能用本轮取得的机构策略、政策与产业证据"
                "独立判断；这些来源也不足时必须返回 insufficient。",
                *(
                    [
                        "- 当前使用确认型主线口径：候选主线不能按当前主线通过；"
                        "结论应写成“未通过确认型主线门槛”，不得写成产业方向不存在。"
                    ]
                    if strategy_profile == MainlineStrategyProfile.CONFIRMED_MAINLINE
                    else [
                        "- 当前使用前瞻布局型口径：候选方向只有同时满足结构化分支关系、"
                        "未来1—6个月窗口、至少两类独立中期证据以及至少一个已满足或部分满足"
                        "的触发条件，才具备第一关通过资格；仅有长期趋势或主题名称仍须失败。"
                    ]
                ),
            ]
        )
        return CriterionEvidence(
            raw_data=raw,
            data_summary="\n".join(lines),
        )

    def get_rubric(self) -> str:
        return MAINLINE_POSITION

    def evidence_failure_reason(
        self,
        evidence: CriterionEvidence,
    ) -> str | None:
        raw = evidence.raw_data
        subject = raw.get("market_subject") or {}
        membership = raw.get("thesis_membership") or {}
        has_direction = bool(
            subject.get("investment_thesis")
            or membership.get("requested_domains")
            or subject.get("fallback_industry_direction")
        )
        if not has_direction:
            return "本轮没有结构化产业方向，且公司所属行业也不可用，" "无法确定市场主线判断对象"
        return None
