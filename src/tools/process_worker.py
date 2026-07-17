"""One-shot tool worker used to contain native-library crashes."""

from __future__ import annotations

import json
import sys
import traceback


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


if __name__ == "__main__":
    raise SystemExit(main())
