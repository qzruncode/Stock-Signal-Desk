"""HTTP-only business boundary; no database or real upstream access."""

import httpx
import pytest

from src.services.market_data_client import (
    MarketDataClient,
    MarketDataError,
    DataNotReady,
)


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_service_failure_never_becomes_a_user_login_or_local_fallback(status):
    client = MarketDataClient(
        token="test-server-only-token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, json={"detail": "服务未就绪"})
        ),
    )
    try:
        with pytest.raises(MarketDataError) as error:
            client.get("/v1/datasets")
        assert error.value.status == 503
        assert "test-server-only-token" not in str(error.value)
    finally:
        client.close()


def test_not_ready_snapshot_is_explicit_and_wait_budget_is_bounded():
    seen = []

    def pending(request):
        seen.append(request)
        return httpx.Response(202, json={"not_ready": {"000001": ["kline"]}})

    client = MarketDataClient(transport=httpx.MockTransport(pending))
    try:
        with pytest.raises(DataNotReady) as error:
            client.snapshot(["000001"], ["kline"], wait=0)
        assert error.value.detail["not_ready"] == {"000001": ["kline"]}
        assert len(seen) == 1
    finally:
        client.close()


def test_rss_instance_verification_fetches_through_the_service(monkeypatch):
    from api.v1.endpoints import rss
    from unittest.mock import Mock

    monkeypatch.setattr(rss, "_xueqiu_cookies_configured", lambda: True)
    read = Mock(return_value={"items": [{"title": "test"}], "errors": []})
    monkeypatch.setattr(rss, "read_source", read)
    assert rss.test_xueqiu_cookie()["verified"] is True
    assert read.call_args.args[0] == "rss.ui.get_rss_feeds_by_spec"
    assert read.call_args.args[1]["body"]["force"] is True
