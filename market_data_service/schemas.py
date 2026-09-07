"""Versioned public HTTP contracts; no business database entities cross the boundary."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Dataset = Literal[
    "securities",
    "calendar",
    "kline",
    "financials",
    "quotes",
    "news",
    "announcements",
    "market",
    "macro",
    "rss",
]
DATASETS = tuple(Dataset.__args__)
DEFAULT_POLICIES = {
    "securities": (86400, 172800),
    "calendar": (86400, 604800),
    "kline": (3600, 86400),
    "financials": (21600, 43200),
    "quotes": (60, 120),
    "news": (900, 1800),
    "announcements": (1800, 3600),
    "market": (300, 900),
    "macro": (3600, 86400),
    "rss": (900, 1800),
}
DATASET_LABELS = {
    "securities": "证券主数据",
    "calendar": "交易日历",
    "kline": "日 K 线",
    "financials": "财务报表",
    "quotes": "实时行情",
    "news": "公司新闻",
    "announcements": "公司公告",
    "market": "市场与资金流",
    "macro": "指数与宏观",
    "rss": "资讯订阅",
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SyncRequest(StrictModel):
    dataset: Dataset
    mode: Literal["stale", "missing", "all"] = "stale"
    symbols: list[str] = Field(default_factory=list, max_length=10000)

    @field_validator("symbols")
    @classmethod
    def valid_symbols(cls, value):
        import re

        if any(not re.fullmatch(r"\d{6}", item) for item in value):
            raise ValueError("股票代码必须为 6 位数字")
        return sorted(set(value))


class PolicyUpdate(StrictModel):
    enabled: bool
    interval_seconds: int = Field(ge=30, le=604800)
    max_age_seconds: int = Field(ge=30, le=1209600)


class SourceRequest(StrictModel):
    operation: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any] = Field(default_factory=dict)
    freshness: Literal["latest", "allow_stale"] = "latest"
    max_wait_seconds: float = Field(default=0, ge=0, le=30)


class DateWindow(StrictModel):
    start_date: str | None = None
    end_date: str | None = None

    @field_validator("start_date", "end_date")
    @classmethod
    def valid_date(cls, value):
        from datetime import date

        return date.fromisoformat(value).isoformat() if value else None

    @model_validator(mode="after")
    def valid_dates(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("开始日期不能晚于结束日期")
        return self


class SnapshotRequest(DateWindow):
    max_wait_seconds: float = Field(default=0, ge=0, le=30)
    symbols: list[str] = Field(min_length=1, max_length=10000)
    datasets: list[Literal["securities", "financials", "kline", "news", "quotes"]] = (
        Field(
            default_factory=lambda: ["securities", "financials", "kline"], min_length=1
        )
    )
    freshness: Literal["latest", "allow_stale"] = "latest"
    count: int = Field(default=1, ge=1, le=5000)

    @field_validator("symbols")
    @classmethod
    def valid_symbols(cls, values):
        return SyncRequest.valid_symbols(values)

    @model_validator(mode="after")
    def valid_window(self):
        if "kline" in self.datasets and len(self.symbols) * self.count > 200000:
            raise ValueError("单次日线读取不能超过 20 万条，请分段读取")
        return self
