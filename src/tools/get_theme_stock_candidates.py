# -*- coding: utf-8 -*-
"""Recall A-share theme candidates before company-level evidence grading.

The synchronized ``stock_meta`` table is the authoritative security universe,
but it does not contain concept-board membership.  This tool therefore uses
public concept constituents for recall and intersects every result with the
local universe.  Concept membership is deliberately labelled L1: it is a
candidate signal, never proof of orders, customers or revenue.
"""

from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from io import StringIO
from typing import Any

from src.tools.base import ToolSpec, object_schema


_RELATED_THEME_ALIASES: dict[str, tuple[str, ...]] = {
    "人形机器人": ("人形机器人", "机器人概念", "机器人"),
    "具身智能": ("具身智能", "人形机器人", "机器人概念", "机器人"),
    "机器人": ("机器人概念", "人形机器人", "机器人"),
}


def _canonical_theme(theme: str) -> str:
    """Collapse conversational tool arguments to the actual board topic.

    Models occasionally pass a phrase such as ``只梳理精确的人形机器人主题A股候选``
    instead of the bare topic.  Treating that entire phrase as the requested
    board makes every real board look approximate and re-introduces broad alias
    boards.  Prefer the longest known theme embedded in the phrase.
    """
    normalized = str(theme or "").strip()
    known = sorted(
        {
            *(_RELATED_THEME_ALIASES.keys()),
            *(alias for aliases in _RELATED_THEME_ALIASES.values() for alias in aliases),
        },
        key=len,
        reverse=True,
    )
    if normalized in known:
        return normalized

    # Only collapse an embedded known topic when the argument is visibly a
    # conversational wrapper.  Product-level concepts such as ``机器人执行器``
    # and ``机器人减速器`` legitimately contain the generic word ``机器人``;
    # the old substring rule silently widened both to the 700+ member generic
    # robot board.
    wrapper_markers = (
        "请", "只", "梳理", "查找", "寻找", "完整", "精确", "相关",
        "主题", "候选", "股票", "个股", "公司", "标的", "a股",
    )
    if any(marker in normalized.lower() for marker in wrapper_markers):
        return next((candidate for candidate in known if candidate in normalized), normalized)
    return normalized


def _compact(value: Any) -> str:
    return re.sub(r"[\s·•（）()\-_/]+", "", str(value or "")).lower()


def _theme_aliases(theme: str) -> list[str]:
    normalized = _canonical_theme(theme)
    aliases = list(_RELATED_THEME_ALIASES.get(normalized, (normalized,)))
    if normalized.endswith("产业链"):
        aliases.append(normalized[:-3])
    if "人形机器人" in normalized:
        aliases.extend(_RELATED_THEME_ALIASES["人形机器人"])
    return list(dict.fromkeys(alias for alias in aliases if alias))


def _load_local_universe() -> dict[str, dict[str, Any]]:
    from src.storage import DatabaseManager, StockMeta

    db = DatabaseManager.get_instance()
    with db.get_session() as session:
        rows = session.query(
            StockMeta.code,
            StockMeta.name,
            StockMeta.sector,
            StockMeta.revenue_latest,
            StockMeta.net_profit_latest,
            StockMeta.report_date,
        ).filter(StockMeta.status == "active").all()
    return {
        str(code).strip().zfill(6): {
            "symbol": str(code).strip().zfill(6),
            "name": str(name or "").strip(),
            "sector": str(sector or "").strip(),
            "revenue_latest": revenue,
            "net_profit_latest": profit,
            "report_date": str(report_date or "").strip() or None,
        }
        for code, name, sector, revenue, profit, report_date in rows
        if str(code or "").strip() and str(name or "").strip()
    }


def _board_match_score(board_name: str, aliases: list[str]) -> int:
    board = _compact(board_name)
    score = 0
    for index, alias in enumerate(aliases):
        candidate = _compact(alias)
        if not candidate:
            continue
        if board == candidate:
            score = max(score, 100 - index)
        elif len(candidate) >= 2 and (candidate in board or board in candidate):
            score = max(score, 60 - index)
    return score


def _fetch_sina_constituents(theme: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    import akshare as ak

    aliases = _theme_aliases(theme)
    board_frame = ak.stock_sector_spot(indicator="概念")
    matched: list[tuple[int, str, str]] = []
    for _, row in board_frame.iterrows():
        name = str(row.get("板块") or "").strip()
        label = str(row.get("label") or "").strip()
        score = _board_match_score(name, aliases)
        if score and label:
            matched.append((score, name, label))
    matched.sort(key=lambda item: (-item[0], item[1]))
    # A precise board such as “人形机器人” must not be widened to the much
    # broader “机器人概念” board.  The latter contains hundreds of industrial,
    # medical and consumer names that are not evidence for the requested
    # humanoid-robot chain.
    exact_matches = [item for item in matched if item[0] >= 100]
    matched = exact_matches or matched[:3]

    items: list[dict[str, Any]] = []
    boards: list[dict[str, Any]] = []
    errors: list[str] = []
    for score, board_name, label in matched[:3]:
        try:
            frame = ak.stock_sector_detail(sector=label)
            boards.append({
                "name": board_name,
                "source": "新浪概念板块",
                "constituent_count": int(len(frame)),
                "primary_theme": _compact(board_name) == _compact(theme),
                "coverage": "full",
                "url": f"http://vip.stock.finance.sina.com.cn/mkt/#{label}",
            })
            for _, row in frame.iterrows():
                items.append({
                    "symbol": str(row.get("code") or "").split(".")[0].strip().zfill(6),
                    "source_name": str(row.get("name") or "").strip(),
                    "board": board_name,
                    "board_score": score,
                    "primary_theme": _compact(board_name) == _compact(theme),
                    "source": "新浪概念板块",
                    "source_url": f"http://vip.stock.finance.sina.com.cn/mkt/#{label}",
                })
        except Exception as exc:
            errors.append(f"新浪板块 {board_name} 成分股获取失败: {type(exc).__name__}: {exc}")
    return items, boards, errors


def _ths_board_map() -> dict[str, str]:
    import requests
    from bs4 import BeautifulSoup

    url = "http://q.10jqka.com.cn/gn/detail/code/307822/"
    response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, features="lxml")
    result: dict[str, str] = {}
    for anchor in soup.select("a[href*='/gn/detail/code/']"):
        match = re.search(r"/gn/detail/code/(\d+)/", str(anchor.get("href") or ""))
        name = anchor.get_text(strip=True)
        if match and name:
            result[name] = match.group(1)
    return result


def _fetch_eastmoney_constituents(theme: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Fetch the complete Eastmoney concept board through the reachable delay host."""
    import requests

    endpoint = "https://push2delay.eastmoney.com/api/qt/clist/get"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
    common = {
        "po": "1", "np": "1", "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2", "invt": "2", "fid": "f12", "fields": "f12,f14",
    }

    def fetch_page(fs: str, page: int) -> tuple[int, list[dict[str, Any]]]:
        response = requests.get(
            endpoint,
            params={**common, "pn": str(page), "pz": "100", "fs": fs},
            headers=headers,
            timeout=12,
        )
        response.raise_for_status()
        data = response.json().get("data") or {}
        return int(data.get("total") or 0), [item for item in data.get("diff") or [] if isinstance(item, dict)]

    aliases = _theme_aliases(theme)
    board_records: list[dict[str, Any]] = []
    total, first = fetch_page("m:90 t:3 f:!50", 1)
    board_records.extend(first)
    for page in range(2, math.ceil(total / 100) + 1):
        _, records = fetch_page("m:90 t:3 f:!50", page)
        board_records.extend(records)

    matched = sorted(
        (
            (_board_match_score(str(item.get("f14") or ""), aliases), str(item.get("f14") or ""), str(item.get("f12") or ""))
            for item in board_records
            if _board_match_score(str(item.get("f14") or ""), aliases)
        ),
        key=lambda item: (-item[0], item[1]),
    )
    exact_matches = [item for item in matched if item[0] >= 100]
    matched = exact_matches or matched[:3]

    items: list[dict[str, Any]] = []
    boards: list[dict[str, Any]] = []
    errors: list[str] = []
    for score, board_name, board_code in matched:
        source_url = f"https://quote.eastmoney.com/center/boardlist.html#boards-{board_code}"
        records: list[dict[str, Any]] = []
        try:
            constituent_total, first_page = fetch_page(f"b:{board_code} f:!50", 1)
            records.extend(first_page)
            failed_pages: list[int] = []
            for page in range(2, math.ceil(constituent_total / 100) + 1):
                try:
                    _, page_records = fetch_page(f"b:{board_code} f:!50", page)
                    records.extend(page_records)
                except Exception as exc:
                    failed_pages.append(page)
                    errors.append(
                        f"东方财富板块 {board_name} 第 {page} 页获取失败: {type(exc).__name__}: {exc}"
                    )
            boards.append({
                "name": board_name,
                "source": "东方财富概念板块",
                "constituent_count": constituent_total,
                "returned_count": len(records),
                "page_count": max(1, math.ceil(constituent_total / 100)),
                "failed_pages": failed_pages,
                "primary_theme": _compact(board_name) == _compact(theme),
                "coverage": "full" if not failed_pages and len(records) >= constituent_total else "partial_pages",
                "url": source_url,
            })
            for row in records:
                items.append({
                    "symbol": str(row.get("f12") or "").strip().zfill(6),
                    "source_name": str(row.get("f14") or "").strip(),
                    "board": board_name,
                    "board_score": score,
                    "primary_theme": _compact(board_name) == _compact(theme),
                    "source": "东方财富概念板块",
                    "source_url": source_url,
                })
        except Exception as exc:
            errors.append(f"东方财富板块 {board_name} 成分股获取失败: {type(exc).__name__}: {exc}")
    return items, boards, errors


def _fetch_ths_constituents(theme: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    import pandas as pd
    import requests
    from bs4 import BeautifulSoup

    aliases = _theme_aliases(theme)
    board_map = _ths_board_map()
    matched = sorted(
        (
            (_board_match_score(name, aliases), name, code)
            for name, code in board_map.items()
            if _board_match_score(name, aliases)
        ),
        key=lambda item: (-item[0], item[1]),
    )
    # 精确主题存在时只抓取精确板块。把“人形机器人”继续扩成 100 多页的
    # 泛“机器人概念”会引入大量工业自动化公司，反而降低候选池精度；没有
    # 精确板块时才退回前三个近似板块。
    exact_matches = [item for item in matched if item[0] >= 100]
    matched = exact_matches or matched[:3]
    items: list[dict[str, Any]] = []
    boards: list[dict[str, Any]] = []
    errors: list[str] = []
    for score, board_name, board_code in matched:
        source_url = f"http://q.10jqka.com.cn/gn/detail/code/{board_code}/"
        try:
            response = requests.get(source_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, features="lxml")
            page_info = soup.select_one(".page_info")
            page_count = 1
            if page_info:
                match = re.search(r"/(\d+)", page_info.get_text(" ", strip=True))
                if match:
                    page_count = int(match.group(1))
            page_frames: dict[int, Any] = {1: pd.read_html(StringIO(response.text))[0]}
            failed_pages: list[int] = []

            def fetch_page(page: int) -> tuple[int, Any]:
                page_url = f"http://q.10jqka.com.cn/gn/detail/page/{page}/code/{board_code}/"
                page_response = requests.get(
                    page_url,
                    headers={"User-Agent": "Mozilla/5.0", "Referer": source_url},
                    timeout=12,
                )
                page_response.raise_for_status()
                frames = pd.read_html(StringIO(page_response.text))
                if not frames:
                    raise RuntimeError("页面没有成分股表格")
                return page, frames[0]

            # 同花顺页面每页仅 10 家。旧实现只读第 1 页，却把它当作主题候选池，
            # 是名单严重不全的直接原因。并发读取剩余页面，同时逐页记录失败，绝不
            # 把部分抓取伪装成完整覆盖。
            # 未登录的公开页面从第 6 页开始会跳转登录页。与其并发制造几十条
            # 相同错误，只抓公开可读的 1-5 页，并把覆盖限制作为一条结构化告警；
            # 精确主题的全量覆盖由东方财富全分页源承担。
            public_page_count = min(page_count, 5)
            if public_page_count > 1:
                workers = min(4, public_page_count - 1)
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = {pool.submit(fetch_page, page): page for page in range(2, public_page_count + 1)}
                    for future in as_completed(futures):
                        page = futures[future]
                        try:
                            fetched_page, frame = future.result()
                            page_frames[fetched_page] = frame
                        except Exception as exc:
                            failed_pages.append(page)
                            errors.append(
                                f"同花顺板块 {board_name} 第 {page} 页获取失败: "
                                f"{type(exc).__name__}: {exc}"
                            )
            if page_count > public_page_count:
                failed_pages.extend(range(public_page_count + 1, page_count + 1))
                errors.append(
                    f"同花顺板块 {board_name} 公开访问仅覆盖前 {public_page_count}/{page_count} 页；"
                    "其余页面需要登录，已使用其他全分页概念源补足候选召回。"
                )

            frames = [page_frames[page] for page in sorted(page_frames)]
            constituent_count = sum(len(frame) for frame in frames)
            boards.append({
                "name": board_name,
                "source": "同花顺概念板块",
                "constituent_count": constituent_count,
                "returned_count": constituent_count,
                "page_count": page_count,
                "fetched_page_count": len(page_frames),
                "failed_pages": sorted(failed_pages),
                "primary_theme": _compact(board_name) == _compact(theme),
                "coverage": "full" if not failed_pages else "partial_pages",
                "url": source_url,
            })
            for frame in frames:
                for _, row in frame.iterrows():
                    items.append({
                        "symbol": str(row.get("代码") or "").split(".")[0].strip().zfill(6),
                        "source_name": str(row.get("名称") or "").strip(),
                        "board": board_name,
                        "board_score": score,
                        "primary_theme": _compact(board_name) == _compact(theme),
                        "source": "同花顺概念板块",
                        "source_url": source_url,
                    })
        except Exception as exc:
            errors.append(f"同花顺板块 {board_name} 成分股获取失败: {type(exc).__name__}: {exc}")
    return items, boards, errors


def _financial_scale(value: Any) -> float:
    try:
        number = abs(float(value))
    except (TypeError, ValueError):
        return 0.0
    return math.log10(number + 1.0)


def get_theme_stock_candidates(
    theme: str,
    limit: int = 500,
    *,
    local_universe: dict[str, dict[str, Any]] | None = None,
    maintenance_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    topic = _canonical_theme(theme)
    if not topic:
        raise ValueError("theme 不能为空")
    bounded_limit = max(20, min(int(limit or 500), 1000))
    from src.services.data_maintenance import ensure_stock_universe

    maintenance = maintenance_result or ensure_stock_universe(trigger="agent_theme_candidates")
    local = local_universe if local_universe is not None else _load_local_universe()
    warnings: list[str] = [maintenance["warning"]] if maintenance.get("warning") else []
    raw_items: list[dict[str, Any]] = []
    boards: list[dict[str, Any]] = []

    for fetcher in (_fetch_eastmoney_constituents, _fetch_sina_constituents, _fetch_ths_constituents):
        try:
            fetched, fetched_boards, errors = fetcher(topic)
            raw_items.extend(fetched)
            boards.extend(fetched_boards)
            warnings.extend(errors)
        except Exception as exc:
            fetcher_name = getattr(fetcher, "__name__", type(fetcher).__name__)
            warnings.append(f"{fetcher_name} 失败: {type(exc).__name__}: {exc}")

    coverage_complete = any(
        isinstance(board, dict)
        and board.get("coverage") == "full"
        and board.get("primary_theme") is True
        for board in boards
    )
    if not coverage_complete:
        warnings.append(
            "没有任何精确主题源完成全分页抓取，本轮候选池不是主题全量成分股；"
            "已保留逐来源覆盖范围，不能把 returned_count 写成全市场主题公司总数。"
        )
    else:
        # Once at least one source has fully covered the exact requested board,
        # only exact-board records belong in the returned candidate inventory.
        # Approximate aliases remain useful solely as a fallback when no exact
        # board can be completed.
        raw_items = [item for item in raw_items if item.get("primary_theme") is True]
        boards = [board for board in boards if board.get("primary_theme") is True]

    merged: dict[str, dict[str, Any]] = {}
    for raw in raw_items:
        symbol = str(raw.get("symbol") or "").zfill(6)
        authoritative = local.get(symbol)
        if authoritative is None:
            continue
        item = merged.setdefault(symbol, {
            **authoritative,
            "boards": [],
            "sources": [],
            "primary_theme_membership": False,
        })
        board_name = str(raw.get("board") or "")
        if board_name and board_name not in item["boards"]:
            item["boards"].append(board_name)
        source = {
            "name": raw.get("source"),
            "board": board_name,
            "url": raw.get("source_url"),
            "date": date.today().isoformat(),
        }
        if source not in item["sources"]:
            item["sources"].append(source)
        if raw.get("primary_theme") is True:
            item["primary_theme_membership"] = True

    for item in merged.values():
        item["board_count"] = len(item["boards"])
        item["evidence_level"] = "L1"
        item["evidence_basis"] = (
            "主题板块成分股且代码已由本地 stock_meta 核验；"
            "仅用于候选召回，不证明相关订单、客户验证或收入。"
        )
        item["company_evidence_required"] = True

    ranked = sorted(
        merged.values(),
        key=lambda item: (
            not bool(item.get("primary_theme_membership")),
            -int(item.get("board_count") or 0),
            -_financial_scale(item.get("revenue_latest")),
            str(item.get("symbol") or ""),
        ),
    )
    returned = ranked[:bounded_limit]
    success = bool(returned)
    if not success:
        warnings.append("概念板块成分股未能与本地 stock_meta 形成有效交集")
    return {
        "success": success,
        "partial": success and bool(warnings),
        "theme": topic,
        "local_universe_count": len(local),
        "raw_constituent_records": len(raw_items),
        "candidate_count": len(ranked),
        "returned_count": len(returned),
        "omitted_count": max(0, len(ranked) - len(returned)),
        "coverage_complete": coverage_complete,
        "items": returned,
        "matched_boards": boards,
        "maintenance": maintenance,
        "source_scope": "public_concept_constituents_intersected_with_local_stock_meta",
        "decision_boundary": (
            "items 是完整性优先的候选召回，不是受益公司定论。回答必须同时给出完整候选索引，"
            "并把公告、财报、主营构成、订单或客户验证形成的公司级证据单独分层。"
        ),
        "data_time": date.today().isoformat(),
        "is_stale": False,
        "freshness_unknown": False,
        "warnings": warnings,
        "errors": [] if success else list(warnings),
    }


TOOL = ToolSpec(
    name="get_theme_stock_candidates",
    description=(
        "从公开概念板块成分股召回产业主题候选公司，并与本地 stock_meta 全量证券库交叉核验。"
        "用于主题到A股公司的候选发现；板块成员只算L1概念证据，不代表订单或收入兑现。"
    ),
    parameters=object_schema({
        "theme": {"type": "string", "description": "产业主题，例如人形机器人、低空经济"},
        "limit": {"type": "integer", "minimum": 20, "maximum": 1000, "default": 500},
    }, required=("theme",)),
    executor=get_theme_stock_candidates,
    category="research",
)


__all__ = ["TOOL", "get_theme_stock_candidates"]
