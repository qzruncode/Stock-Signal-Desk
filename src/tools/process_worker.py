"""One-shot tool worker used to contain native-library crashes."""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import NoReturn


RESULT_PREFIX = "__DSA_TOOL_RESULT__="


def main() -> int:
    try:
        request = json.loads(sys.stdin.read() or "{}")
        from src.tools.registry import ToolRegistry

        result = ToolRegistry().execute(
            str(request.get("name") or ""),
            request.get("arguments") or {},
        )
        payload = {"ok": True, "result": result}
    except Exception as exc:
        payload = {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(limit=8),
        }
    sys.stdout.write("\n" + RESULT_PREFIX + json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    sys.stdout.flush()
    return 0 if payload.get("ok") else 1


def _exit_after_result(exit_code: int) -> NoReturn:
    """End the one-shot worker without waiting on imported background threads.

    Some data providers initialize process-global helper threads while the tool
    runs.  The typed result is already fully serialized and flushed at this
    point, so normal interpreter finalization would only make the parent wait
    indefinitely for threads that do not belong to the one-shot worker
    contract.
    """
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        os._exit(exit_code)


if __name__ == "__main__":
    _exit_after_result(main())
