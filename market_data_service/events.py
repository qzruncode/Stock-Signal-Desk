"""Redis Streams owns ordered delivery, replay, and blocking event waits."""

import hashlib
import json
import re
import time
from contextlib import asynccontextmanager

from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis.exceptions import RedisError

from market_data_service.settings import get_settings


def stream_key():
    # Multiple isolated databases may share a Redis instance without sharing events.
    namespace = hashlib.sha256(get_settings().database_url.encode()).hexdigest()[:20]
    return f"market-data:events:{namespace}"


def validate_cursor(value):
    if not re.fullmatch(r"\d{1,20}-\d{1,20}", value):
        raise ValueError("无效的数据事件游标")
    return value


def cursor_tuple(value):
    return tuple(map(int, value.split("-")))


def current_cursor():
    try:
        with Redis.from_url(
            get_settings().broker_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        ) as redis:
            rows = redis.xrevrange(stream_key(), count=1)
            return rows[0][0] if rows else "0-0"
    except RedisError:
        # The read model remains available during a notification outage.
        return "0-0"


def publish_event(payload):
    with Redis.from_url(
        get_settings().broker_url,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=5,
    ) as redis:
        return redis.xadd(
            stream_key(),
            {"payload": json.dumps(payload, ensure_ascii=False)},
            maxlen=get_settings().event_stream_length,
            approximate=True,
        )


@asynccontextmanager
async def event_reader():
    async with AsyncRedis.from_url(
        get_settings().broker_url,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=40,
    ) as redis:
        yield EventReader(redis)


class EventReader:
    def __init__(self, redis):
        self.redis = redis
        self.key = stream_key()

    async def tail(self):
        rows = await self.redis.xrevrange(self.key, count=1)
        return rows[0][0] if rows else "0-0"

    async def resume(self, cursor):
        validate_cursor(cursor)
        first = await self.redis.xrange(self.key, count=1)
        tail = await self.tail()
        invalid = cursor_tuple(cursor) > cursor_tuple(tail) or bool(
            first and cursor_tuple(cursor) < cursor_tuple(first[0][0])
        )
        return (tail, True) if invalid else (cursor, False)

    async def read(self, cursor, seconds=15):
        rows = await self.redis.xread(
            {self.key: cursor}, count=100, block=max(1, int(seconds * 1000))
        )
        return [
            (event_id, json.loads(fields["payload"]))
            for _, events in rows
            for event_id, fields in events
        ]

    async def wait(self, cursor, seconds, predicate):
        """No SQL and no sleeps: Redis wakes a waiter only when an event arrives."""
        deadline = time.monotonic() + seconds
        while (remaining := deadline - time.monotonic()) > 0:
            rows = await self.read(cursor, min(remaining, 15))
            for event_id, payload in rows:
                cursor = event_id
                if predicate(payload):
                    return cursor, payload
            if not rows:
                resumed, reset = await self.resume(cursor)
                if reset:
                    return resumed, {"reset": True}
        return cursor, None


def affects_data(payload, *, datasets, symbols=(), request_keys=()):
    if payload.get("reset"):
        return True
    for dataset in datasets:
        states = payload.get("states", {}).get(dataset, [])
        observations = payload.get("observations", {}).get(dataset, [])
        if "*" in states or "all" in states or set(states).intersection(symbols):
            return True
        if "*" in observations or set(observations).intersection(request_keys):
            return True
    return False
