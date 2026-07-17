from datetime import date, datetime

from src.tools._trading_calendar import expected_trade_day, is_trading_time


def test_trading_time_requires_official_trade_date() -> None:
    holiday = datetime.fromisoformat("2026-10-05T10:00:00+08:00")

    assert is_trading_time(holiday, calendar=[date(2026, 9, 30), date(2026, 10, 9)]) is False


def test_trading_time_accepts_morning_and_afternoon_sessions_only() -> None:
    calendar = [date(2026, 7, 16)]

    assert is_trading_time(datetime.fromisoformat("2026-07-16T10:00:00+08:00"), calendar) is True
    assert is_trading_time(datetime.fromisoformat("2026-07-16T12:00:00+08:00"), calendar) is False
    assert is_trading_time(datetime.fromisoformat("2026-07-16T14:00:00+08:00"), calendar) is True


def test_expected_trade_day_switches_at_call_auction() -> None:
    calendar = [date(2026, 7, 15), date(2026, 7, 16)]

    assert expected_trade_day(datetime(2026, 7, 16, 9, 14), calendar) == date(2026, 7, 15)
    assert expected_trade_day(datetime(2026, 7, 16, 9, 15), calendar) == date(2026, 7, 16)
