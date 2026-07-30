#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bounded black-box load probe for the durable Agent streaming endpoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import time

import httpx


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(
        len(ordered) - 1,
        max(0, math.ceil(percentile * len(ordered)) - 1),
    )
    return ordered[index]


async def _one_request(
    client: httpx.AsyncClient,
    *,
    index: int,
    prompt: str,
) -> dict[str, object]:
    started = time.monotonic()
    byte_count = 0
    try:
        async with client.stream(
            "POST",
            "/api/v1/agent/chat",
            json={
                "messages": [{
                    "role": "user",
                    "content": f"{prompt}\n[load-case:{index}]",
                }],
            },
        ) as response:
            async for chunk in response.aiter_bytes():
                byte_count += len(chunk)
            return {
                "ok": response.status_code == 200 and byte_count > 0,
                "status": response.status_code,
                "duration_seconds": time.monotonic() - started,
                "bytes": byte_count,
            }
    except Exception as exc:
        return {
            "ok": False,
            "status": 0,
            "duration_seconds": time.monotonic() - started,
            "bytes": byte_count,
            "error": f"{type(exc).__name__}: {exc}",
        }


async def _run(args) -> int:
    semaphore = asyncio.Semaphore(args.concurrency)
    headers = {}
    session_cookie = (os.getenv("DSA_SESSION_COOKIE") or "").strip()
    if session_cookie:
        headers["Cookie"] = f"dsa_session={session_cookie}"

    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"),
        headers=headers,
        timeout=httpx.Timeout(args.timeout),
    ) as client:
        async def bounded(index: int):
            async with semaphore:
                return await _one_request(
                    client,
                    index=index,
                    prompt=args.prompt,
                )

        results = await asyncio.gather(*[
            bounded(index)
            for index in range(args.requests)
        ])

    durations = [
        float(result["duration_seconds"])
        for result in results
    ]
    successes = sum(bool(result["ok"]) for result in results)
    summary = {
        "requests": len(results),
        "concurrency": args.concurrency,
        "successes": successes,
        "success_rate": successes / len(results) if results else 0.0,
        "latency_seconds": {
            "mean": statistics.fmean(durations) if durations else 0.0,
            "p50": _percentile(durations, 0.50),
            "p95": _percentile(durations, 0.95),
            "p99": _percentile(durations, 0.99),
        },
        "statuses": {
            str(status): sum(result["status"] == status for result in results)
            for status in sorted({int(result["status"]) for result in results})
        },
    }
    summary["release_gate_passed"] = (
        summary["success_rate"] >= args.min_success_rate
        and summary["latency_seconds"]["p95"] <= args.max_p95_seconds
    )
    print(json.dumps(
        {"summary": summary, "results": results},
        ensure_ascii=False,
        indent=2,
    ))
    return 0 if summary["release_gate_passed"] else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=1_500.0)
    parser.add_argument("--min-success-rate", type=float, default=0.99)
    parser.add_argument("--max-p95-seconds", type=float, default=120.0)
    parser.add_argument(
        "--prompt",
        default="用一句话说明你当前是否可用，不调用数据工具。",
    )
    args = parser.parse_args()
    if args.requests < 1 or args.concurrency < 1:
        parser.error("requests and concurrency must be positive")
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
