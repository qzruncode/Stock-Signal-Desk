# -*- coding: utf-8 -*-
"""SSE event formatting for stock business analysis streaming."""

from __future__ import annotations

import json


def _format_business_sse_event(event_type: str, data) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event_type}\ndata: {payload}\n\n"