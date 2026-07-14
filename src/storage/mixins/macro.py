# -*- coding: utf-8 -*-
"""Mixin: macro, bond, fundamental snapshot, and market mainline operations."""
import json
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select, and_, desc
from sqlalchemy.orm import Session

from src.storage.models import MacroIndexDaily, BondYieldDaily, MacroIndicator, FundamentalSnapshot, MarketMainlineReport

logger = logging.getLogger(__name__)


class MacroMixin:
    """宏观经济、国债收益率、基本面快照、市场主线研判相关数据库操作 mixin."""

    def save_fundamental_snapshot(
        self,
        query_id: str,
        code: str,
        payload: Optional[Dict[str, Any]],
        source_chain: Optional[Any] = None,
        coverage: Optional[Any] = None,
    ) -> int:
        """
        保存基本面快照（P0 write-only）。失败不抛异常，返回写入条数 0/1。
        """
        if not query_id or not code or payload is None:
            return 0

        try:
            def _write(session: Session) -> int:
                session.add(
                    FundamentalSnapshot(
                        query_id=query_id,
                        code=code,
                        payload=self._safe_json_dumps(payload),
                        source_chain=self._safe_json_dumps(source_chain or []),
                        coverage=self._safe_json_dumps(coverage or {}),
                    )
                )
                return 1
            return self._run_write_transaction(
                f"save_fundamental_snapshot[{query_id}:{code}]",
                _write,
            )
        except Exception as e:
            logger.debug(
                "基本面快照写入失败（fail-open）: query_id=%s code=%s err=%s",
                query_id,
                code,
                e,
            )
            return 0

    def get_latest_fundamental_snapshot(
        self,
        query_id: str,
        code: str,
    ) -> Optional[Dict[str, Any]]:
        """
        获取指定 query_id + code 的最新基本面快照 payload。

        读取失败或不存在时返回 None（fail-open）。
        """
        if not query_id or not code:
            return None

        with self.get_session() as session:
            try:
                row = session.execute(
                    select(FundamentalSnapshot)
                    .where(
                        and_(
                            FundamentalSnapshot.query_id == query_id,
                            FundamentalSnapshot.code == code,
                        )
                    )
                    .order_by(desc(FundamentalSnapshot.created_at))
                    .limit(1)
                ).scalar_one_or_none()
            except Exception as e:
                logger.debug(
                    "基本面快照读取失败（fail-open）: query_id=%s code=%s err=%s",
                    query_id,
                    code,
                    e,
                )
                return None

            if row is None:
                return None
            try:
                payload = json.loads(row.payload or "{}")
                return payload if isinstance(payload, dict) else None
            except Exception:
                logger.warning("[Storage] 快照 payload JSON 解析失败", exc_info=True)
                return None

    def save_market_mainline_report(
        self,
        *,
        report_key: str,
        as_of_date: str,
        mode: str,
        payload: Dict[str, Any],
        raw_response: Optional[str],
        model_used: Optional[str],
    ) -> int:
        """保存市场主线结构化研判报告。"""
        try:
            payload_json = self._safe_json_dumps(payload)
            market_stage = payload.get("market_stage") if isinstance(payload, dict) else {}
            overview = payload.get("overview") if isinstance(payload, dict) else None

            def _write(session: Session) -> int:
                session.add(
                    MarketMainlineReport(
                        report_key=report_key,
                        as_of_date=as_of_date,
                        mode=mode,
                        model_used=model_used,
                        overview=str(overview or ""),
                        market_stage_label=str((market_stage or {}).get("label") or ""),
                        market_stage_description=str((market_stage or {}).get("description") or ""),
                        raw_response=raw_response,
                        payload=payload_json,
                        created_at=datetime.now(),
                    )
                )
                return 1

            return self._run_write_transaction(
                f"save_market_mainline_report[{report_key}:{mode}:{as_of_date}]",
                _write,
            )
        except Exception:
            logger.exception("保存市场主线研判报告失败")
            return 0

    def get_latest_market_mainline_report(
        self,
        *,
        report_key: str,
        mode: str = "llm",
        as_of_date: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """获取最新市场主线结构化研判报告。"""
        try:
            with self.get_session() as session:
                stmt = select(MarketMainlineReport).where(
                    and_(
                        MarketMainlineReport.report_key == report_key,
                        MarketMainlineReport.mode == mode,
                    )
                )
                if as_of_date:
                    stmt = stmt.where(MarketMainlineReport.as_of_date == as_of_date)
                row = session.execute(
                    stmt.order_by(desc(MarketMainlineReport.created_at)).limit(1)
                ).scalar_one_or_none()
                return row.to_dict() if row else None
        except Exception:
            logger.exception("读取市场主线研判报告失败")
            return None

    # ========================================================================
    # Macro data persistence
    # ========================================================================

    def save_macro_index_daily(
        self,
        index_code: str,
        records: list[dict],
        data_source: str = "新浪",
    ) -> int:
        """批量保存指数日线数据到数据库（UPSERT by (index_code, date)）"""
        if not records:
            return 0
        now = datetime.now()

        def _write(session: Session) -> int:
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            rows = []
            for rec in records:
                d = self._normalize_daily_date(rec.get("date"))
                if d is None:
                    continue
                rows.append({
                    "index_code": index_code,
                    "date": d,
                    "open": rec.get("open"),
                    "high": rec.get("high"),
                    "low": rec.get("low"),
                    "close": rec.get("close"),
                    "volume": rec.get("volume"),
                    "amount": rec.get("amount"),
                    "pct_chg": rec.get("pct_chg"),
                    "change_amount": rec.get("change_amount"),
                    "data_source": data_source,
                    "created_at": now,
                    "updated_at": now,
                })
            if not rows:
                return 0
            stmt = sqlite_insert(MacroIndexDaily).values(rows)
            excluded = stmt.excluded
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["index_code", "date"],
                    set_={
                        "open": excluded.open,
                        "high": excluded.high,
                        "low": excluded.low,
                        "close": excluded.close,
                        "volume": excluded.volume,
                        "amount": excluded.amount,
                        "pct_chg": excluded.pct_chg,
                        "change_amount": excluded.change_amount,
                        "data_source": excluded.data_source,
                        "updated_at": excluded.updated_at,
                    },
                )
            )
            session.flush()
            return len(rows)

        return self._run_write_transaction(f"save_macro_index_daily[{index_code}]", _write)

    def get_macro_index_daily(
        self,
        index_code: str,
        limit: int = 50,
    ) -> list[dict] | None:
        """获取指数日线历史数据"""
        try:
            with self.get_session() as session:
                rows = session.execute(
                    select(MacroIndexDaily)
                    .where(MacroIndexDaily.index_code == index_code)
                    .order_by(desc(MacroIndexDaily.date))
                    .limit(limit)
                ).scalars().all()
                if rows:
                    return [
                        {
                            "date": str(r.date),
                            "open": r.open,
                            "high": r.high,
                            "low": r.low,
                            "close": r.close,
                            "volume": r.volume,
                            "amount": r.amount,
                            "pct_chg": r.pct_chg,
                            "change_amount": r.change_amount,
                        }
                        for r in rows
                    ]
        except Exception:
            logger.debug("指数日线读取失败", exc_info=True)
        return None

    def get_trading_days(self, start_date: date, end_date: date) -> set[date] | None:
        """返回 [start_date, end_date] 区间内的交易日集合。

        以上证指数（000001）的日线 date 为交易日历来源：上证指数每个交易日都
        有数据，其 date 集合即 A 股交易日集合，能精确识别停牌/节假日缺失。

        Returns:
            交易日 date 集合；若该区间内上证指数本地无数据（可能尚未同步），
            返回 None —— 调用方据此回退到粗略的“首尾对齐”校验，而非误判为
            完整/不完整。
        """
        try:
            with self.get_session() as session:
                rows = session.execute(
                    select(MacroIndexDaily.date)
                    .where(MacroIndexDaily.index_code == "000001")
                    .where(MacroIndexDaily.date >= start_date)
                    .where(MacroIndexDaily.date <= end_date)
                ).scalars().all()
            if not rows:
                return None
            return set(rows)
        except Exception:
            logger.debug("交易日集合读取失败", exc_info=True)
            return None

    def save_bond_yield_daily(
        self,
        country: str,
        term: str,
        records: list[dict],
    ) -> int:
        """批量保存国债收益率数据（UPSERT by (country, term, date)）"""
        if not records:
            return 0
        now = datetime.now()

        def _write(session: Session) -> int:
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            rows = []
            for rec in records:
                d = self._normalize_daily_date(rec.get("date"))
                if d is None:
                    continue
                rows.append({
                    "country": country,
                    "term": term,
                    "date": d,
                    "yield_value": rec.get("value"),
                    "created_at": now,
                    "updated_at": now,
                })
            if not rows:
                return 0
            stmt = sqlite_insert(BondYieldDaily).values(rows)
            excluded = stmt.excluded
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["country", "term", "date"],
                    set_={
                        "yield_value": excluded.yield_value,
                        "updated_at": excluded.updated_at,
                    },
                )
            )
            session.flush()
            return len(rows)

        return self._run_write_transaction(f"save_bond_yield_daily[{country}:{term}]", _write)

    def get_bond_yield_daily(
        self,
        country: str,
        term: str,
        limit: int = 30,
    ) -> list[dict] | None:
        """获取国债收益率历史数据"""
        try:
            with self.get_session() as session:
                rows = session.execute(
                    select(BondYieldDaily)
                    .where(BondYieldDaily.country == country, BondYieldDaily.term == term)
                    .order_by(desc(BondYieldDaily.date))
                    .limit(limit)
                ).scalars().all()
                if rows:
                    return [
                        {"date": str(r.date), "value": r.yield_value} for r in rows
                    ]
        except Exception:
            logger.debug("国债收益率读取失败", exc_info=True)
        return None

    def save_macro_indicator(
        self,
        indicator: str,
        records: list[dict],
    ) -> int:
        """批量保存宏观经济指标数据（UPSERT by (indicator, period)）"""
        if not records:
            return 0
        now = datetime.now()

        def _write(session: Session) -> int:
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert

            rows = []
            for rec in records:
                period = rec.get("period", "")
                if not period:
                    continue
                extra_json = None
                if rec.get("extra"):
                    extra_json = json.dumps(rec["extra"], ensure_ascii=False)
                rows.append({
                    "indicator": indicator,
                    "period": period,
                    "value": rec.get("value"),
                    "yoy": rec.get("yoy"),
                    "mom": rec.get("mom"),
                    "extra_json": extra_json,
                    "created_at": now,
                    "updated_at": now,
                })
            if not rows:
                return 0
            stmt = sqlite_insert(MacroIndicator).values(rows)
            excluded = stmt.excluded
            session.execute(
                stmt.on_conflict_do_update(
                    index_elements=["indicator", "period"],
                    set_={
                        "value": excluded.value,
                        "yoy": excluded.yoy,
                        "mom": excluded.mom,
                        "extra_json": excluded.extra_json,
                        "updated_at": excluded.updated_at,
                    },
                )
            )
            session.flush()
            return len(rows)

        return self._run_write_transaction(f"save_macro_indicator[{indicator}]", _write)

    def get_macro_indicator(
        self,
        indicator: str,
        limit: int = 120,
    ) -> list[dict] | None:
        """获取宏观经济指标历史数据"""
        try:
            with self.get_session() as session:
                rows = session.execute(
                    select(MacroIndicator)
                    .where(MacroIndicator.indicator == indicator)
                    .order_by(desc(MacroIndicator.period))
                    .limit(limit)
                ).scalars().all()
                if rows:
                    result = []
                    for r in rows:
                        rec = {
                            "period": r.period,
                            "value": r.value,
                            "yoy": r.yoy,
                            "mom": r.mom,
                        }
                        if r.extra_json:
                            try:
                                rec["extra"] = json.loads(r.extra_json)
                            except Exception:
                                logger.debug("extra_json 解析失败", exc_info=True)
                        result.append(rec)
                    return result
        except Exception:
            logger.debug("宏观指标读取失败", exc_info=True)
        return None