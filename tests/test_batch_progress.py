import threading

from api.v1.endpoints import batch
from api.v1.endpoints.batch import (
    _build_partial_report_from_run,
    _resolve_auto_resume_stock_codes,
    _resolve_resume_stock_codes,
)
from src.batch_runner import (
    BatchRunState,
    _build_batch_notification_content,
    _get_batch_max_concurrent,
    _save_batch_run_progress,
)


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


def test_batch_state_can_resume_from_existing_results():
    state = BatchRunState(
        "run-1",
        total=3,
        existing_results={
            "600519": {"success": True, "text": "ok", "model": "test-model"},
            "000001": {"success": False, "text": "failed", "model": ""},
        },
    )

    snapshot = state.to_dict()
    assert snapshot["completed"] == 2
    assert snapshot["success"] == 1
    assert snapshot["failed"] == 1
    assert "已恢复 2/3" in snapshot["current_message"]


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


def test_batch_progress_is_persisted_incrementally(monkeypatch):
    class FakeRecord:
        success_count = 0
        fail_count = 0
        results_json = "[]"

    record = FakeRecord()

    class FakeQuery:
        def filter_by(self, run_id):
            return self

        def first(self):
            return record

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def query(self, model):
            return FakeQuery()

        def commit(self):
            pass

    class FakeDb:
        def get_session(self):
            return FakeSession()

    class FakeDatabaseManager:
        @staticmethod
        def get_instance():
            return FakeDb()

    monkeypatch.setattr("src.batch_runner.DatabaseManager", FakeDatabaseManager)
    state = BatchRunState("run-1", total=2)
    state.add_result("600519", True, "ok", "test-model")

    _save_batch_run_progress("run-1", state)

    assert record.success_count == 1
    assert record.fail_count == 0
    assert "600519" in record.results_json


def test_partial_report_can_be_built_from_persisted_results():
    report = _build_partial_report_from_run({
        "started_at": "2026-05-24T14:44:39",
        "template_name": "行业+预期差",
        "stock_count": 347,
        "success_count": 1,
        "fail_count": 0,
        "results_json": '{"600519":{"success":true,"text":"分析正文","model":"test-model"}}',
    })

    assert "批量分析报告（部分结果）" in report
    assert "600519" in report
    assert "分析正文" in report


def test_resume_stock_codes_prefers_persisted_original_list():
    codes = _resolve_resume_stock_codes(
        {"stock_codes_json": '["600519", "000001"]'},
        ["300750"],
    )

    assert codes == ["600519", "000001"]


def test_auto_resume_ignores_empty_incomplete_runs():
    codes = _resolve_auto_resume_stock_codes({
        "stock_codes_json": '["600519", "000001"]',
        "stock_count": 2,
        "results_json": "{}",
    })

    assert codes == []


def test_auto_resume_only_uses_partial_runs():
    codes = _resolve_auto_resume_stock_codes({
        "stock_codes_json": '["600519", "000001"]',
        "stock_count": 2,
        "results_json": '{"600519":{"success":true,"text":"ok","model":"test-model"}}',
    })

    assert codes == ["600519", "000001"]


def test_batch_notification_is_statistical_summary_not_raw_stock_list():
    state = BatchRunState(
        "run-1",
        total=3,
        existing_results={
            "605118": {"success": True, "text": "short", "model": "model-a"},
            "000001": {"success": True, "text": "long text" * 100, "model": "model-b"},
            "300750": {"success": False, "text": "failed", "model": ""},
        },
    )

    content = _build_batch_notification_content(
        "run-1",
        state,
        "行业+预期差",
        "/tmp/batch.md",
    )

    assert "批量分析统计" in content
    assert "成功率" in content
    assert "605118" not in content
    assert "000001" not in content
    assert "300750" in content
