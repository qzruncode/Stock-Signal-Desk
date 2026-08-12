"""Detached worker for the explicit stock-universe synchronization endpoint."""

from __future__ import annotations

import sys
import threading
from datetime import datetime

from src.services.data_maintenance import run_stock_universe_maintenance_job
from src.services.data_maintenance import _update_job


_HEARTBEAT_SECONDS = 10.0


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        return 2
    job_id = sys.argv[1].strip()
    stopped = threading.Event()

    def heartbeat() -> None:
        while not stopped.wait(_HEARTBEAT_SECONDS):
            _update_job(job_id, updated_at=datetime.now())

    heartbeat_thread = threading.Thread(target=heartbeat, name="stock-list-sync-heartbeat", daemon=True)
    heartbeat_thread.start()
    try:
        run_stock_universe_maintenance_job(job_id)
    except Exception:
        return 1
    finally:
        stopped.set()
        heartbeat_thread.join(timeout=0.2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
