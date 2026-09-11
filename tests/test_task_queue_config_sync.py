# -*- coding: utf-8 -*-
"""Unit tests for task queue MAX_WORKERS runtime synchronization."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.services.task_queue import AnalysisTaskQueue, TaskInfo, TaskStatus, get_task_queue


class TaskQueueConfigSyncTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._original_instance = AnalysisTaskQueue._instance
        AnalysisTaskQueue._instance = None

    def tearDown(self) -> None:
        queue = AnalysisTaskQueue._instance
        if queue is not None and queue is not self._original_instance:
            executor = getattr(queue, "_executor", None)
            if executor is not None and hasattr(executor, "shutdown"):
                executor.shutdown(wait=False)
        AnalysisTaskQueue._instance = self._original_instance

    def test_sync_max_workers_applies_when_idle(self) -> None:
        queue = AnalysisTaskQueue(max_workers=3)
        shutdown_wait_args = []

        class ExecutorStub:
            def shutdown(self, wait=True, cancel_futures=False):
                shutdown_wait_args.append(wait)

        queue._executor = ExecutorStub()

        result = queue.sync_max_workers(1)
        self.assertEqual(result, "applied")
        self.assertEqual(queue.max_workers, 1)
        self.assertIsNone(queue._executor)
        self.assertEqual(shutdown_wait_args, [False])

    def test_sync_max_workers_deferred_when_busy(self) -> None:
        queue = AnalysisTaskQueue(max_workers=3)
        queue._tasks["task1"] = TaskInfo(
            task_id="task1",
            stock_code="600519",
            status=TaskStatus.PROCESSING,
        )

        result = queue.sync_max_workers(1)
        self.assertEqual(result, "deferred_busy")
        self.assertEqual(queue.max_workers, 3)

    def test_get_task_queue_uses_runtime_configured_max_workers(self) -> None:
        with patch("src.config.get_config", return_value=SimpleNamespace(max_workers=1)):
            queue = get_task_queue()

        self.assertEqual(queue.max_workers, 1)

    def test_get_task_queue_keeps_singleton_identity_after_sync(self) -> None:
        with patch("src.config.get_config", return_value=SimpleNamespace(max_workers=3)):
            first = get_task_queue()
        with patch("src.config.get_config", return_value=SimpleNamespace(max_workers=1)):
            second = get_task_queue()

        self.assertIs(first, second)
        self.assertEqual(second.max_workers, 1)

    def test_get_task_queue_supports_string_max_workers(self) -> None:
        with patch("src.config.get_config", return_value=SimpleNamespace(max_workers="2")):
            queue = get_task_queue()

        self.assertEqual(queue.max_workers, 2)

    def test_get_task_queue_defers_sync_when_busy(self) -> None:
        queue = AnalysisTaskQueue(max_workers=3)
        queue._tasks["task1"] = TaskInfo(
            task_id="task1",
            stock_code="600519",
            status=TaskStatus.PROCESSING,
        )

        with patch("src.config.get_config", return_value=SimpleNamespace(max_workers=1)):
            synced = get_task_queue()

        self.assertIs(synced, queue)
        self.assertEqual(synced.max_workers, 3)

    def test_terminal_update_ignores_missing_task(self) -> None:
        queue = AnalysisTaskQueue(max_workers=3)

        self.assertIsNone(queue._mark_task_completed_locked("missing", {"stock_name": "贵州茅台"}))
        self.assertIsNone(queue._mark_task_failed_locked("missing", "boom"))

    def test_terminal_update_marks_the_task_failed(self) -> None:
        queue = AnalysisTaskQueue(max_workers=3)
        stale_task = TaskInfo(task_id="old-task", stock_code="600519", status=TaskStatus.PROCESSING)
        queue._tasks[stale_task.task_id] = stale_task

        snapshot = queue._mark_task_failed_locked(stale_task.task_id, "boom")

        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.status, TaskStatus.FAILED)


if __name__ == "__main__":
    unittest.main()
