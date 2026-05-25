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
    _extract_structured_decision,
    _get_batch_max_concurrent,
    _save_batch_run_progress,
    _with_batch_decision_schema,
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
            "605118": {"success": True, "text": "筛选通过：建议买入\n理由：基本面改善且趋势向上", "model": "model-a"},
            "000001": {"success": True, "text": "最终结论：不买\n原因：买点不足", "model": "model-b"},
            "300750": {"success": False, "text": "failed", "model": ""},
        },
    )

    content = _build_batch_notification_content(
        "run-1",
        state,
        "行业+预期差",
        "/tmp/batch.md",
    )

    assert "跑批筛选汇总" in content
    assert "成功率" in content
    assert "| 605118 |" in content
    assert "建议买入" in content
    assert "000001" not in content
    assert "300750" in content


def test_batch_summary_respects_final_no_buy_over_section_pass():
    state = BatchRunState(
        "run-1",
        total=2,
        existing_results={
            "603876": {
                "success": True,
                "text": (
                    "### 主线属性判定\n"
                    "结论：通过\n\n"
                    "### 行业β判定\n"
                    "结论：不通过\n\n"
                    "### 最终结论\n"
                    "**不买**\n"
                    "最核心的否定原因：行业催化不足。"
                ),
                "model": "model-a",
            },
            "605118": {
                "success": True,
                "text": "### 最终结论：建议买入\n理由：业绩改善且趋势向上",
                "model": "model-a",
            },
        },
    )

    content = _build_batch_notification_content(
        "run-1",
        state,
        "行业+预期差",
        "/tmp/batch.md",
    )

    assert "| 605118 |" in content
    assert "| 603876 |" not in content
    assert "筛选通过: **1**" in content


def test_batch_summary_accepts_buy_variants_and_rejects_no_buy_phrase():
    state = BatchRunState(
        "run-1",
        total=4,
        existing_results={
            "000001": {"success": True, "text": "最终结论：可买入\n原因：赔率较好", "model": "model-a"},
            "000002": {"success": True, "text": "操作建议：建议买入\n理由：催化明确", "model": "model-a"},
            "000003": {"success": True, "text": "最终结论：不买\n原因：没有买点", "model": "model-a"},
            "000004": {"success": True, "text": "综合结论：不建议买入\n原因：估值偏贵", "model": "model-a"},
        },
    )

    content = _build_batch_notification_content(
        "run-1",
        state,
        "行业+预期差",
        "/tmp/batch.md",
    )

    assert "| 000001 | 可买入 |" in content
    assert "| 000002 | 建议买入 |" in content
    assert "| 000003 |" not in content
    assert "| 000004 |" not in content
    assert "筛选通过: **2**" in content


def test_batch_summary_prefers_leading_no_buy_over_future_may_buy():
    state = BatchRunState(
        "run-1",
        total=1,
        existing_results={
            "301357": {
                "success": True,
                "text": (
                    "不买\n\n"
                    "最核心的否定原因：行业β不满足且存在风险否决项。\n\n"
                    "最关键验证点：若后续订单实质性落地，可能转为可买。\n"
                    "核心逻辑溯源：不符合主线属性与行业β。"
                ),
                "model": "model-a",
            },
        },
    )

    content = _build_batch_notification_content(
        "run-1",
        state,
        "行业+预期差",
        "/tmp/batch.md",
    )

    assert "| 301357 |" not in content
    assert "筛选通过: **0**" in content
    assert "排除: **1**" in content


def test_batch_structured_decision_overrides_unfamiliar_words():
    text = """
    这只股票的自然语言结论用了一个系统没见过的新词：火速上车。

    BATCH_DECISION_JSON
    ```json
    {
      "decision": "buy",
      "decision_label": "火速上车",
      "reason": "结构化字段明确给出 buy"
    }
    ```
    """
    state = BatchRunState(
        "run-1",
        total=1,
        existing_results={
            "000001": {"success": True, "text": text, "model": "model-a"},
        },
    )

    content = _build_batch_notification_content(
        "run-1",
        state,
        "行业+预期差",
        "/tmp/batch.md",
    )

    assert "| 000001 | 火速上车 | 结构化字段明确给出 buy |" in content
    assert "筛选通过: **1**" in content


def test_batch_unrecognized_legacy_words_go_to_unknown_not_passed():
    state = BatchRunState(
        "run-1",
        total=1,
        existing_results={
            "000001": {"success": True, "text": "最终结论：火速上车\n原因：新表达未配置", "model": "model-a"},
        },
    )

    content = _build_batch_notification_content(
        "run-1",
        state,
        "行业+预期差",
        "/tmp/batch.md",
    )

    assert "| 000001 |" in content
    assert "待确认: **1**" in content
    assert "筛选通过: **0**" in content


def test_batch_decision_schema_is_injected_once():
    prompt = _with_batch_decision_schema("原始模板")

    assert "BATCH_DECISION_JSON" in prompt
    assert _with_batch_decision_schema(prompt) == prompt


def test_structured_decision_parser_accepts_json_tail():
    parsed = _extract_structured_decision(
        '正文\nBATCH_DECISION_JSON\n```json\n{"decision":"reject","decision_label":"暂避","reason":"结构化否决"}\n```'
    )

    assert parsed == {
        "decision": "reject",
        "decision_label": "暂避",
        "decision_reason": "结构化否决",
        "decision_source": "structured",
    }
