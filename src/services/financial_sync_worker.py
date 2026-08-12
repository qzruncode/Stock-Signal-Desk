"""Detached worker for the settings-page financial synchronization task."""

from __future__ import annotations

import sys
import threading

from api.v1.endpoints.stocks import sync as sync_endpoint
from api.v1.endpoints.stocks import _financials_sync
from src.services.data_maintenance import _update_job

_HEARTBEAT_SECONDS = 10.0


def main() -> int:
    if len(sys.argv) not in {3, 4} or not sys.argv[1].strip() or not sys.argv[2].strip():
        return 2
    job_id = sys.argv[1].strip()
    period = sys.argv[2].strip()
    active_codes = (
        [code.strip() for code in sys.argv[3].split(",") if code.strip()]
        if len(sys.argv) == 4
        else None
    )
    state = sync_endpoint._initial_state()
    state["job_id"] = job_id
    state_lock = threading.Lock()

    def set_state(**updates) -> None:
        with state_lock:
            state.update(updates)

    _financials_sync.attach_state(
        state=state,
        lock=state_lock,
        set_state=set_state,
        initial_state=sync_endpoint._initial_state,
        utc_now_iso=sync_endpoint._utc_now_iso,
    )
    stopped = threading.Event()

    def heartbeat() -> None:
        while not stopped.wait(_HEARTBEAT_SECONDS):
            _update_job(job_id, updated_at=sync_endpoint.datetime.now())

    heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
    heartbeat_thread.start()
    try:
        _financials_sync.run_financial_sync(period, active_codes, job_id=job_id)
    except Exception:
        return 1
    finally:
        stopped.set()
        heartbeat_thread.join(timeout=0.2)
    with state_lock:
        return 0 if state.get("status") in {"success", "partial"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
