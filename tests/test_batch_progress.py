import threading

from api.v1.endpoints import batch
from src.batch_runner import BatchRunState, _get_batch_max_concurrent


def test_batch_progress_callback_can_snapshot_without_deadlock():
    state = BatchRunState("run-1", total=2)
    snapshots = []
    done = threading.Event()

    def callback(current_state):
        snapshots.append(current_state.to_dict())
        done.set()

    state.add_progress_callback(callback)

    worker = threading.Thread(
        target=lambda: state.add_result("600519", True, "ok", "test-model"),
        daemon=True,
    )
    worker.start()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert done.wait(timeout=0.1)
    assert snapshots[-1]["completed"] == 1
    assert snapshots[-1]["success"] == 1
    assert snapshots[-1]["failed"] == 0


def test_batch_progress_counts_failures_as_completed_work():
    state = BatchRunState("run-1", total=2)

    state.add_result("600519", True, "ok", "test-model")
    state.add_result("000001", False, "failed", "")

    snapshot = state.to_dict()
    assert snapshot["completed"] == 2
    assert snapshot["success"] == 1
    assert snapshot["failed"] == 1


def test_batch_abort_marks_all_work_completed_and_failed():
    state = BatchRunState("run-1", total=3)

    state.abort_all("LLM 未配置，无法执行跑批")

    snapshot = state.to_dict()
    assert snapshot["completed"] == 3
    assert snapshot["success"] == 0
    assert snapshot["failed"] == 3
    assert snapshot["current_message"] == "LLM 未配置，无法执行跑批"


def test_current_batch_route_is_registered_before_dynamic_run_detail_route():
    paths = [route.path for route in batch.router.routes]

    assert paths.index("/runs/current") < paths.index("/runs/{run_id}")


def test_batch_max_concurrent_is_configurable_with_limit(monkeypatch):
    monkeypatch.setenv("BATCH_MAX_CONCURRENT", "8")
    assert _get_batch_max_concurrent() == 8

    monkeypatch.setenv("BATCH_MAX_CONCURRENT", "99")
    assert _get_batch_max_concurrent() == 10
