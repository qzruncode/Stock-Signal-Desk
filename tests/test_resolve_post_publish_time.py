# -*- coding: utf-8 -*-
"""Tests for cross-year/clock-skew handling in guba post timestamp parser."""

from __future__ import annotations

import unittest
from datetime import datetime

from market_data_service.providers.sentiment_support._analysis import (
    _resolve_post_publish_time,
)


class ResolvePostPublishTimeTestCase(unittest.TestCase):
    def test_normal_same_year(self) -> None:
        now = datetime(2026, 6, 15, 12, 0)
        result = _resolve_post_publish_time("06-01 08:00", now=now)
        assert result == datetime(2026, 6, 1, 8, 0)

    def test_genuine_cross_year_rollback(self) -> None:
        # In early January, a "12-31" post truly belongs to the previous year.
        now = datetime(2026, 1, 5, 10, 0)
        result = _resolve_post_publish_time("12-31 22:00", now=now)
        assert result == datetime(2025, 12, 31, 22, 0)

    def test_clock_skew_minor_future_keeps_current_year(self) -> None:
        # Server clock drift puts the parsed time a few minutes in the future.
        now = datetime(2026, 6, 15, 9, 0)
        result = _resolve_post_publish_time("06-15 09:05", now=now)
        # Within the 12h tolerance — must stay in 2026, not roll back to 2025.
        assert result == datetime(2026, 6, 15, 9, 5)

    def test_clock_skew_hours_future_keeps_current_year(self) -> None:
        now = datetime(2026, 6, 15, 9, 0)
        result = _resolve_post_publish_time("06-15 18:00", now=now)
        # 9-hour offset still within tolerance.
        assert result == datetime(2026, 6, 15, 18, 0)

    def test_future_beyond_tolerance_rolls_back(self) -> None:
        # 13h future: treat as last year.
        now = datetime(2026, 6, 15, 9, 0)
        result = _resolve_post_publish_time("06-15 23:00", now=now)
        assert result == datetime(2025, 6, 15, 23, 0)

    def test_invalid_format_falls_back_to_parse_date(self) -> None:
        result = _resolve_post_publish_time("not a date", now=datetime(2026, 6, 15))
        assert result is None


if __name__ == "__main__":
    unittest.main()
