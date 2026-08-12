"""Detached worker for full-market and missing-code K-line synchronization."""

from __future__ import annotations

import sys
import threading

from api.v1.endpoints.stocks import sync as sync_endpoint
from src.services.data_maintenance import _update_job

_HEARTBEAT_SECONDS = 10.0


def main() -> int:
    if len(sys.argv) not in {3, 4} or not sys.argv[1].strip() or not sys.argv[2].strip():
        return 2
    job_id = sys.argv[1].strip()
    mode = sys.argv[2].strip()
    raw_codes = sys.argv[3] if len(sys.argv) == 4 else ""
    codes = [code.strip() for code in raw_codes.split(",") if code.strip()]
    if mode not in {"all", "missing"} or (mode == "missing" and not codes):
        return 2

    stopped = threading.Event()

    def heartbeat() -> None:
        while not stopped.wait(_HEARTBEAT_SECONDS):
            _update_job(job_id, updated_at=sync_endpoint.datetime.now())

    if mode == "missing":
        sync_endpoint._set_missing_kline_state(job_id=job_id)
    else:
        sync_endpoint._set_kline_state(job_id=job_id)
    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
    heartbeat_thread.start()
    try:
        if mode == "missing":
            sync_endpoint._run_missing_kline_sync(codes)
            state = sync_endpoint._get_missing_kline_state_copy()
        else:
            sync_endpoint._run_kline_sync()
            state = sync_endpoint._get_kline_state_copy()
    except Exception as exc:
        if mode == "missing":
            sync_endpoint._set_missing_kline_state(
                status="failed",
                error=str(exc)[:300],
                message="K线同步任务未完成",
                finished_at=sync_endpoint._utc_now_iso(),
            )
        else:
            sync_endpoint._set_kline_state(
                status="failed",
                error=str(exc)[:300],
                message="K线同步任务未完成",
                finished_at=sync_endpoint._utc_now_iso(),
            )
        return 1
    finally:
        stopped.set()
        heartbeat_thread.join(timeout=0.2)
    return 0 if state.get("status") == "success" else 1


if __name__ == "__main__":
    raise SystemExit(main())
