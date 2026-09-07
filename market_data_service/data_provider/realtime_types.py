# -*- coding: utf-8 -*-
"""
实时行情统一类型定义

UnifiedRealtimeQuote — akshare 返回的实时行情数据结构
ChipDistribution — 筹码分布数据结构
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


# ============================================
# 通用类型转换工具函数
# ============================================


def safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    """安全转换为浮点数，处理 None/NaN/空字符串。"""
    try:
        if val is None:
            return default
        if isinstance(val, str):
            val = val.strip()
            if val in ("", "-", "--"):
                return default
        import math

        try:
            if math.isnan(float(val)):
                return default
        except (ValueError, TypeError):
            pass
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_int(val: Any, default: Optional[int] = None) -> Optional[int]:
    """安全转换为整数。"""
    f_val = safe_float(val, default=None)
    if f_val is not None:
        return int(f_val)
    return default


class RealtimeSource(Enum):
    """实时行情数据源"""

    AKSHARE_EM = "akshare_em"  # 东方财富（akshare库，全量拉取）
    EASTMONEY_PUSH = "eastmoney_push"  # 东方财富 push API（单股查询，快）
    XUEQIU = "xueqiu"  # 雪球
    AKSHARE_SINA = "akshare_sina"  # 新浪财经
    AKSHARE_TENCENT = "akshare_tencent"  # 腾讯财经


@dataclass
class UnifiedRealtimeQuote:
    """统一实时行情数据结构"""

    code: str
    name: str = ""
    source: RealtimeSource = RealtimeSource.AKSHARE_EM
    trade_time: Optional[str] = None

    # 核心价格数据
    price: Optional[float] = None
    change_pct: Optional[float] = None
    change_amount: Optional[float] = None

    # 量价指标
    volume: Optional[int] = None
    amount: Optional[float] = None
    volume_ratio: Optional[float] = None
    turnover_rate: Optional[float] = None
    amplitude: Optional[float] = None

    # 价格区间
    open_price: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    pre_close: Optional[float] = None

    # 估值指标（仅东财等全量接口有）
    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    total_mv: Optional[float] = None
    circ_mv: Optional[float] = None

    # 其他指标
    change_60d: Optional[float] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        """转换为字典（过滤 None 值）"""
        result = {
            "code": self.code,
            "name": self.name,
            "source": self.source.value,
            "volume_unit": "股",
            "amount_unit": "元",
            "market_value_unit": "元",
        }
        for f in [
            "trade_time",
            "price",
            "change_pct",
            "change_amount",
            "volume",
            "amount",
            "volume_ratio",
            "turnover_rate",
            "amplitude",
            "open_price",
            "high",
            "low",
            "pre_close",
            "pe_ratio",
            "pb_ratio",
            "total_mv",
            "circ_mv",
            "change_60d",
            "high_52w",
            "low_52w",
        ]:
            val = getattr(self, f, None)
            if val is not None:
                result[f] = val
        return result

    def has_basic_data(self) -> bool:
        """检查是否有基本的价格数据"""
        return self.price is not None and self.price > 0

    def has_volume_data(self) -> bool:
        """检查是否有量价数据"""
        return self.volume_ratio is not None or self.turnover_rate is not None


@dataclass
class ChipDistribution:
    """筹码分布数据"""

    code: str
    date: str = ""
    source: str = "akshare"

    profit_ratio: float = 0.0
    avg_cost: float = 0.0
    cost_90_low: float = 0.0
    cost_90_high: float = 0.0
    concentration_90: float = 0.0
    cost_70_low: float = 0.0
    cost_70_high: float = 0.0
    concentration_70: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "date": self.date,
            "source": self.source,
            "profit_ratio": self.profit_ratio,
            "avg_cost": self.avg_cost,
            "cost_90_low": self.cost_90_low,
            "cost_90_high": self.cost_90_high,
            "concentration_90": self.concentration_90,
            "concentration_70": self.concentration_70,
        }
