# -*- coding: utf-8 -*-
"""Shared stock code utilities and error types."""

from __future__ import annotations

from typing import Optional, Tuple

import pandas as pd
from src.data.stock_index_loader import get_index_stock_name
from src.data.stock_mapping import STOCK_NAME_MAP, is_meaningful_stock_name


# === 标准化列名定义 ===
STANDARD_COLUMNS = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']


def unwrap_exception(exc: Exception) -> Exception:
    """Follow chained exceptions and return the deepest non-cyclic cause."""
    current = exc
    visited = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        next_exc = current.__cause__ or current.__context__
        if next_exc is None:
            break
        current = next_exc
    return current


def summarize_exception(exc: Exception) -> Tuple[str, str]:
    """Build a stable summary for logs."""
    root = unwrap_exception(exc)
    error_type = type(root).__name__
    message = str(exc).strip() or str(root).strip() or error_type
    return error_type, " ".join(message.split())


def normalize_stock_code(stock_code: str) -> str:
    """Normalize stock code by stripping exchange prefixes/suffixes."""
    code = stock_code.strip()
    upper = code.upper()
    if upper.startswith('HK') and not upper.startswith('HK.'):
        candidate = upper[2:]
        if candidate.isdigit() and 1 <= len(candidate) <= 5:
            return f"HK{candidate.zfill(5)}"
    if upper.startswith(('SH', 'SZ')) and not upper.startswith('SH.') and not upper.startswith('SZ.'):
        candidate = code[2:]
        if candidate.isdigit() and len(candidate) in (5, 6):
            return candidate
    if upper.startswith('BJ') and not upper.startswith('BJ.'):
        candidate = code[2:]
        if candidate.isdigit() and len(candidate) == 6:
            return candidate
    if '.' in code:
        base, suffix = code.rsplit('.', 1)
        if suffix.upper() == 'HK' and base.isdigit() and 1 <= len(base) <= 5:
            return f"HK{base.zfill(5)}"
        if suffix.upper() in ('SH', 'SZ', 'SS', 'BJ') and base.isdigit():
            return base
    return code


def _is_hk_market(code: str) -> bool:
    """判定是否为港股代码。"""
    normalized = (code or "").strip().upper()
    if normalized.endswith(".HK"):
        base = normalized[:-3]
        return base.isdigit() and 1 <= len(base) <= 5
    if normalized.startswith("HK"):
        digits = normalized[2:]
        return digits.isdigit() and 1 <= len(digits) <= 5
    if normalized.isdigit() and len(normalized) == 5:
        return True
    return False


ETF_PREFIXES = ("51", "52", "56", "58", "15", "16", "18")


def _is_etf_code(code: str) -> bool:
    """判定 A 股 ETF 基金代码。"""
    normalized = normalize_stock_code(code)
    return normalized.isdigit() and len(normalized) == 6 and normalized.startswith(ETF_PREFIXES)


def is_bse_code(code: str) -> bool:
    """Check if the code is a Beijing Stock Exchange (BSE) A-share code."""
    c = (code or "").strip().split(".")[0]
    if len(c) != 6 or not c.isdigit():
        return False
    if c.startswith("900"):
        return False
    return c.startswith(("92", "43", "81", "82", "83", "87", "88"))


def is_st_stock(name: str) -> bool:
    """Check if the stock is an ST or *ST stock based on its name."""
    n = (name or "").upper()
    return 'ST' in n


def is_kc_cy_stock(code: str) -> bool:
    """Check if the stock is a STAR Market (科创板) or ChiNext (创业板) stock."""
    c = (code or "").strip().split(".")[0]
    return c.startswith("688") or c.startswith("30")


def canonical_stock_code(code: str) -> str:
    """Return the canonical (uppercase) form of a stock code."""
    return (code or "").strip().upper()


class DataFetchError(Exception):
    """数据获取异常基类"""
    pass


class RateLimitError(DataFetchError):
    """API 速率限制异常"""
    pass
