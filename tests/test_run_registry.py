# -*- coding: utf-8 -*-
"""RunBroadcaster / ActiveRunRegistry 单测 —— 后台保活运行时核心行为。

覆盖:
- 多订阅者收到相同 chunk、unsubscribe 后不再收
- mark_done 投 None 哨兵,订阅 generator 自然结束
- 慢订阅者 queue 满 drop oldest
- is_active / start_or_get 复用
- 断连不杀:订阅者中途断开(cancel 消费),后台 task 仍跑完并落库回调
"""

from __future__ import annotations

import asyncio
from typing import List

import pytest

import src.agent.run_registry as run_registry_module
from src.agent.run_registry import (
    ActiveRun,
    ActiveRunRegistry,
    RunBroadcaster,
    RunCapacityExceeded,
)


@pytest.fixture(autouse=True)
def reset_registry():
    """每个测试用独立 registry,避免模块级单例跨测试残留。"""
    registry = ActiveRunRegistry()
    yield registry
    # 清理可能残留的后台 task
    for run in list(registry._runs.values()):
        if run.task is not None and not run.task.done():
            run.task.cancel()


def _drain(queue: asyncio.Queue, count: int, timeout: float = 1.0):
    """同步收集 queue 中最多 count 个 chunk(测试辅助)。"""
    async def _collect():
        out = []
        for _ in range(count):
            try:
                out.append(await asyncio.wait_for(queue.get(), timeout=timeout))
            except asyncio.TimeoutError:
                break
        return out
    return asyncio.get_event_loop().run_until_complete(_collect())


def test_broadcast_to_multiple_subscribers():
    """两个订阅者都收到相同的 emit chunk。"""
    async def run():
        b = RunBroadcaster()
        q1 = b.subscribe()
        q2 = b.subscribe()
        b.append_text("hello")
        b.append_text("world")
        b.mark_finished()
        c1 = [q1.get_nowait() for _ in range(3)]
        c2 = [q2.get_nowait() for _ in range(3)]
        assert c1[0].text_delta == "hello"
        assert c1[1].text_delta == "world"
        assert c1[2] is None  # None 哨兵
        assert [c.text_delta for c in c2[:2]] == ["hello", "world"]
        assert c2[2] is None

    asyncio.new_event_loop().run_until_complete(run())


def test_unsubscribe_stops_receiving():
    """unsubscribe 后不再收到后续 chunk(但不影响已 emit 的)。"""
    async def run():
        b = RunBroadcaster()
        q = b.subscribe()
        b.append_text("a")
        b.unsubscribe(q)
        b.append_text("b")  # 不应进 q
        assert q.get_nowait().text_delta == "a"
        # q 里只有 "a",没有 "b"
        assert q.empty()

    asyncio.new_event_loop().run_until_complete(run())


def test_drop_oldest_when_queue_full():
    """订阅者 queue 满(容量受限)时,drop oldest,不阻塞生成。"""
    async def run():
        b = RunBroadcaster()
        q = b.subscribe()
        # 模拟满 queue:capacity 是模块常量,这里发足够多 chunk 触发 drop。
        for i in range(300):
            b.append_text(f"t{i}")
        # queue 不会无限增长(drop oldest),put_nowait 不抛 QueueFull
        collected: List[str] = []
        while not q.empty():
            c = q.get_nowait()
            if c is not None:
                collected.append(c.text_delta)
        # 容量 256,故最多 256 条;最早的被丢弃
        assert len(collected) <= 256
        assert collected[-1] == "t299"

    asyncio.new_event_loop().run_until_complete(run())


def test_replay_subscriber_keeps_full_history_beyond_live_queue_limit():
    """续流 replay 不能沿用 live queue 的 drop-oldest,否则回答会从中间开始。"""
    async def run():
        b = RunBroadcaster()
        for i in range(300):
            b.append_text(f"t{i}")
        b.mark_finished()

        q = b.subscribe(replay_from=0)
        collected: List[str] = []
        while True:
            c = await asyncio.wait_for(q.get(), timeout=1.0)
            if c is None:
                break
            collected.append(c.text_delta)

        assert len(collected) == 300
        assert collected[0] == "t0"
        assert collected[-1] == "t299"

    asyncio.new_event_loop().run_until_complete(run())


def test_trimmed_history_keeps_monotonic_resume_cursor(monkeypatch):
    """裁剪旧 chunk 后游标仍是全局递增值，续流只回放保留窗口。"""
    async def run():
        monkeypatch.setattr(run_registry_module, "_RUN_HISTORY_MAX_CHUNKS", 3)
        b = RunBroadcaster()
        for i in range(5):
            b.append_text(f"t{i}")
        b.mark_finished()

        assert b.history_length == 5
        q = b.subscribe(replay_from=0)
        collected: List[str] = []
        while True:
            chunk = await asyncio.wait_for(q.get(), timeout=1.0)
            if chunk is None:
                break
            collected.append(chunk.text_delta)
        assert collected == ["t2", "t3", "t4"]

        at_end = b.subscribe(replay_from=5)
        assert await asyncio.wait_for(at_end.get(), timeout=1.0) is None

    asyncio.new_event_loop().run_until_complete(run())


def test_registry_is_active_and_reuse(reset_registry):
    """start_or_get 复用 running run;mark_done 后 is_active=False。"""
    registry = reset_registry

    async def factory(broadcaster: RunBroadcaster):
        async def _noop():
            await asyncio.sleep(0.05)
            await registry.mark_done("c1", "completed", final_text="done")
        return asyncio.create_task(_noop())

    async def run():
        run1 = await registry.start_or_get("c1")
        assert registry.is_active("c1")
        await run1.start(factory)
        run2 = await registry.start_or_get("c1")  # 复用
        assert run2 is run1
        # 等 task 完成
        await run1.task
        assert not registry.is_active("c1")

    asyncio.new_event_loop().run_until_complete(run())


def test_cancel_stops_running_task_and_removes_run(reset_registry):
    """显式 cancel 会取消后台 task 并从 registry 移除。"""
    registry = reset_registry

    async def factory(broadcaster: RunBroadcaster):
        async def _wait_forever():
            try:
                await asyncio.Event().wait()
            finally:
                broadcaster.append_text("cancelled")
        return asyncio.create_task(_wait_forever())

    async def run():
        run_obj = await registry.start_or_get("c-cancel")
        await run_obj.start(factory)
        assert registry.is_active("c-cancel")
        cancelled = await registry.cancel("c-cancel")
        assert cancelled is True
        assert registry.get("c-cancel") is None
        assert run_obj.task is not None
        try:
            await run_obj.task
        except asyncio.CancelledError:
            pass
        assert run_obj.task.cancelled()

    asyncio.new_event_loop().run_until_complete(run())


def test_subscriber_disconnect_does_not_kill_generation(reset_registry):
    """核心:订阅者中途断开,后台 task 仍跑完并调用 mark_done。"""
    registry = reset_registry
    completion_log: List[str] = []

    async def factory(broadcaster: RunBroadcaster):
        async def _generate():
            try:
                for i in range(5):
                    broadcaster.append_text(f"chunk{i}")
                    await asyncio.sleep(0.01)
                completion_log.append("generation_done")
            finally:
                await registry.mark_done("c2", "completed", final_text="ok")
        return asyncio.create_task(_generate())

    async def run():
        run_obj = await registry.start_or_get("c2")
        q = run_obj.broadcaster.subscribe()
        await run_obj.start(factory)
        # 消费第一个 chunk 后"断开"(不再消费,模拟客户端断连)
        first = await asyncio.wait_for(q.get(), timeout=1.0)
        assert first.text_delta == "chunk0"
        run_obj.broadcaster.unsubscribe(q)
        # 后台 task 不应被影响,继续跑完
        await run_obj.task
        assert completion_log == ["generation_done"]
        assert not registry.is_active("c2")

    asyncio.new_event_loop().run_until_complete(run())


def test_registry_enforces_process_wide_capacity(reset_registry):
    registry = reset_registry

    async def run():
        first = await registry.try_claim("capacity-1", max_active_runs=1)
        assert first is not None
        with pytest.raises(RunCapacityExceeded):
            await registry.try_claim("capacity-2", max_active_runs=1)
        await registry.cancel("capacity-1")

    asyncio.new_event_loop().run_until_complete(run())


def test_old_retention_timer_cannot_delete_replacement_run(reset_registry, monkeypatch):
    """A completed run's cleanup timer must be scoped to that exact run id."""
    import src.agent.run_registry as run_registry_module

    monkeypatch.setattr(run_registry_module, "_RUN_RETENTION_SECONDS", 0.01)
    registry = reset_registry

    async def run():
        first = await registry.try_claim("same-conversation")
        assert first is not None
        await registry.mark_done("same-conversation", "completed", final_text="old")

        replacement = await registry.try_claim("same-conversation")
        assert replacement is not None
        assert replacement.run_id != first.run_id
        await asyncio.sleep(0.03)

        assert registry.get("same-conversation") is replacement
        await registry.cancel("same-conversation")
        await registry.shutdown()

    asyncio.new_event_loop().run_until_complete(run())


def test_cancel_does_not_rewrite_completed_status(reset_registry):
    registry = reset_registry

    async def run():
        completed = await registry.try_claim("completed")
        assert completed is not None
        await registry.mark_done("completed", "completed", final_text="done")

        assert await registry.cancel("completed") is False
        assert completed.status == "completed"
        assert registry.stats()["terminal"]["completed"] == 1
        await registry.shutdown()

    asyncio.new_event_loop().run_until_complete(run())


def test_partial_run_is_a_distinct_terminal_status(reset_registry):
    registry = reset_registry

    async def run():
        partial = await registry.try_claim("partial")
        assert partial is not None
        await registry.mark_done(
            "partial",
            "partial",
            final_text="部分任务未完成",
            error="coverage_incomplete",
        )

        assert partial.is_running is False
        assert partial.status == "partial"
        assert registry.stats()["terminal"]["partial"] == 1
        await registry.shutdown()

    asyncio.new_event_loop().run_until_complete(run())
