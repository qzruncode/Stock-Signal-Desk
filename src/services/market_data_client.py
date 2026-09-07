"""The business application's sole boundary to current market data.

HTTPX owns pooling and timeouts. There is deliberately no database or provider
fallback: an unavailable/stale data service must not silently become fresh data.
"""

from __future__ import annotations

import atexit
import os
from functools import lru_cache
from typing import Any

import httpx


class MarketDataError(RuntimeError):
    def __init__(self, message: str, *, status: int = 503, detail: Any = None):
        super().__init__(message)
        self.status = status
        self.detail = detail


class DataNotReady(MarketDataError):
    def __init__(self, detail):
        super().__init__(
            "数据正在更新，尚未达到最新要求，请稍后重试", status=503, detail=detail
        )


class MarketDataClient:
    def __init__(
        self, base_url: str | None = None, token: str | None = None, *, transport=None
    ):
        self.base_url = (
            base_url or os.getenv("MARKET_DATA_SERVICE_URL", "http://127.0.0.1:8010")
        ).rstrip("/")
        token = (
            token if token is not None else os.getenv("MARKET_DATA_SERVICE_TOKEN", "")
        )
        self.http = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {token}"} if token else {},
            timeout=httpx.Timeout(35, connect=3),
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
            transport=transport,
            trust_env=False,
        )

    def close(self):
        self.http.close()

    def request(self, method, path, **kwargs):
        try:
            response = self.http.request(method, path, **kwargs)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            try:
                detail = exc.response.json().get("detail")
            except ValueError:
                detail = None
            # Service credentials are server configuration, not the end user's login.
            status = (
                exc.response.status_code
                if exc.response.status_code in {404, 409, 422}
                else 503
            )
            raise MarketDataError(
                str(detail or "数据服务暂不可用"), status=status, detail=detail
            ) from exc
        except httpx.RequestError as exc:
            raise MarketDataError(
                "无法连接独立数据服务，请检查数据服务及采集进程"
            ) from exc
        return response

    def get(self, path, **params):
        return self.request(
            "GET",
            path,
            params={key: value for key, value in params.items() if value is not None},
        ).json()

    def post(self, path, body=None):
        return self.request("POST", path, json=body or {}).json()

    def _read(self, path, body, *, wait=25):
        # One bounded HTTP request. The data service waits for committed events;
        # neither the business process nor the service repeatedly polls SQL.
        response = self.request(
            "POST", path, json={**body, "max_wait_seconds": min(30, max(0, wait))}
        )
        result = response.json()
        if response.status_code == 202:
            raise DataNotReady(result)
        return result

    def source(self, operation, arguments=None, *, freshness="latest", wait=25):
        return self._read(
            "/v1/observations",
            {
                "operation": operation,
                "arguments": arguments or {},
                "freshness": freshness,
            },
            wait=wait,
        )

    def snapshot(
        self,
        symbols,
        datasets=None,
        *,
        freshness="latest",
        count=250,
        wait=25,
        **ranges,
    ):
        if not symbols:
            return {"items": {}, "freshness": {}, "partial": False}
        return self._read(
            "/v1/snapshots",
            {
                "symbols": list(dict.fromkeys(symbols)),
                "datasets": datasets or ["securities", "financials", "kline"],
                "freshness": freshness,
                "count": count,
                **ranges,
            },
            wait=wait,
        )

    def securities(self, *, require_fresh=True, **params):
        result = self.get("/v1/securities", **params)
        if require_fresh and result.get("freshness") != "fresh":
            job = self.post("/v1/jobs", {"dataset": "securities", "mode": "stale"})
            raise DataNotReady(
                {
                    "dataset": "securities",
                    "job": job,
                    "freshness": result.get("freshness"),
                }
            )
        return result

    def calendar(self):
        result = self.get("/v1/calendar")
        if not result.get("ready"):
            self.post("/v1/jobs", {"dataset": "calendar", "mode": "stale"})
            raise DataNotReady({"dataset": "calendar"})
        return result["days"]

    def wait_job(self, job, *, timeout=30, on_progress=None):
        from httpx_sse import connect_sse

        if job["status"] in {"queued", "running"}:
            try:
                with connect_sse(
                    self.http,
                    "GET",
                    f"/v1/jobs/{job['id']}/events",
                    params={"timeout": min(300, max(0, timeout))},
                ) as stream:
                    stream.response.raise_for_status()
                    for event in stream.iter_sse():
                        if event.event != "job":
                            continue
                        job = event.json()
                        if on_progress:
                            on_progress(job["progress"], job["total"], job["message"])
                        if job["status"] not in {"queued", "running"}:
                            break
            except httpx.HTTPError as exc:
                raise MarketDataError(
                    "任务状态连接中断；后台任务仍会继续执行", detail={"job": job}
                ) from exc
        if job["status"] in {"queued", "running"}:
            raise DataNotReady({"job": job})
        if job["status"] != "success":
            raise MarketDataError(
                job.get("error") or job.get("message") or "同步未完成", detail=job
            )
        return job


@lru_cache
def get_market_data_client():
    client = MarketDataClient()
    atexit.register(client.close)
    return client


def read_source(operation: str, arguments: dict | None = None, **options):
    return get_market_data_client().source(operation, arguments, **options)
