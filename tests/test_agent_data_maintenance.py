"""Automatic stock-universe maintenance and Agent capability contracts."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.services import data_maintenance
from src.storage import DataMaintenanceJob
from src.services.stock_universe_service import sync_stock_universe
from src.tools.get_data_health import get_data_health
from src.tools.manage_watchlist import manage_watchlist
from src.tools.registry import ToolRegistry


def test_fresh_stock_universe_skips_refresh() -> None:
    snapshot = {"total": 5200, "data_time": "2026-07-19T08:00:00", "is_stale": False}
    with (
        patch.object(data_maintenance, "_universe_snapshot", return_value=snapshot),
        patch.object(data_maintenance, "sync_stock_universe") as sync,
    ):
        result = data_maintenance.ensure_stock_universe()

    assert result["maintenance_status"] == "ready"
    assert result["refreshed"] is False
    sync.assert_not_called()


def test_force_refreshes_stale_stock_universe_and_records_job() -> None:
    stale = {"total": 5100, "data_time": "2026-07-18T08:00:00", "is_stale": True}
    fresh = {"total": 5200, "data_time": "2026-07-19T08:00:00", "is_stale": False}
    changes = {
        "total": 5200,
        "added": 100,
        "updated": 5100,
        "delisted": 0,
        "delisted_daily": 0,
        "data_time": fresh["data_time"],
    }
    with (
        patch.object(data_maintenance, "_universe_snapshot", side_effect=[stale, stale, fresh]),
        patch.object(data_maintenance, "_claim_job", return_value=("job-1", True)),
        patch.object(data_maintenance, "_update_job") as update,
        patch.object(data_maintenance, "sync_stock_universe", return_value=changes) as sync,
    ):
        result = data_maintenance.ensure_stock_universe(trigger="test", force=True)

    assert result["maintenance_status"] == "success"
    assert result["refreshed"] is True
    assert result["changes"] == changes
    sync.assert_called_once()
    assert update.call_args_list[-1].kwargs["status"] == "success"


def test_force_refresh_target_fits_maintenance_job_schema() -> None:
    stale = {"total": 5100, "data_time": "2026-07-18T08:00:00", "is_stale": True}
    with (
        patch.object(data_maintenance, "_universe_snapshot", side_effect=[stale, stale, stale]),
        patch.object(data_maintenance, "_claim_job", return_value=("job-1", True)) as claim,
        patch.object(data_maintenance, "run_stock_universe_maintenance_job", return_value={"total": 5100}),
    ):
        data_maintenance.ensure_stock_universe(trigger="test", force=True)

    target = claim.call_args.args[1]
    assert len(target) <= 32


def test_stale_maintenance_takeover_rotates_job_id_to_fence_old_worker() -> None:
    now = datetime.now()
    old_job = DataMaintenanceJob(
        id="old-maintenance-job",
        dataset="stock_universe",
        scope_key="all",
        target_data_time="2026-08-12",
        status="running",
        started_at=now - timedelta(minutes=5),
        updated_at=now - timedelta(minutes=2),
        created_at=now - timedelta(minutes=5),
    )
    db = MagicMock()
    session = MagicMock()
    query = MagicMock()
    query.filter.return_value = query
    query.one_or_none.return_value = old_job
    query.update.return_value = 1
    session.query.return_value = query
    db.get_session.return_value.__enter__.return_value = session

    with patch.object(data_maintenance, "DatabaseManager") as db_cls:
        db_cls.get_instance.return_value = db
        job_id, claimed = data_maintenance._claim_job(
            "stock_universe",
            "2026-08-12",
            "test",
        )

    assert claimed is True
    assert job_id != old_job.id
    assert len(job_id) == 36
    assert query.update.call_args.args[0]["id"] == job_id


def test_stale_stock_universe_returns_immediately_and_starts_background_job() -> None:
    stale = {"total": 5100, "data_time": "2026-07-18T08:00:00", "is_stale": True}
    with (
        patch.object(data_maintenance, "_universe_snapshot", side_effect=[stale, stale]),
        patch.object(data_maintenance, "_claim_job", return_value=("job-1", True)),
        patch.object(data_maintenance, "_get_job", return_value=MagicMock(status="running")),
        patch.object(data_maintenance, "_launch_stock_universe_worker") as launch,
        patch.object(data_maintenance, "sync_stock_universe") as sync,
    ):
        result = data_maintenance.ensure_stock_universe(trigger="test")

    assert result["maintenance_status"] == "refreshing"
    assert result["is_stale"] is True
    assert "后台更新" in result["warning"]
    launch.assert_called_once_with("job-1")
    sync.assert_not_called()


def test_background_launch_failure_uses_existing_stale_universe() -> None:
    stale = {"total": 5100, "data_time": "2026-07-18T08:00:00", "is_stale": True}
    failed_job = MagicMock(status="failed", error="source down")
    with (
        patch.object(data_maintenance, "_universe_snapshot", side_effect=[stale, stale]),
        patch.object(data_maintenance, "_claim_job", return_value=("job-1", True)),
        patch.object(data_maintenance, "_get_job", side_effect=[None, failed_job]),
        patch.object(data_maintenance, "_update_job"),
        patch.object(data_maintenance, "_launch_stock_universe_worker", side_effect=RuntimeError("source down")),
    ):
        result = data_maintenance.ensure_stock_universe(trigger="test")

    assert result["maintenance_status"] == "stale_fallback"
    assert "source down" in result["warning"]


def test_agent_registry_excludes_aggregate_maintenance_dashboard() -> None:
    names = set(ToolRegistry().get_tool_names())
    assert {"search_stocks", "list_watchlist", "add_watchlist_items"}.issubset(names)
    assert "get_data_health" not in names


def test_manage_watchlist_tool_delegates_explicit_action() -> None:
    with patch(
        "src.tools.manage_watchlist._manage",
        return_value={"codes": ["600519"], "count": 1, "changed": ["600519"]},
    ) as manage:
        result = manage_watchlist("add", "600519")

    assert result["success"] is True
    assert result["changed"] == ["600519"]
    manage.assert_called_once_with("add", ["600519"])


def test_manage_watchlist_list_ignores_symbols_field() -> None:
    with patch(
        "src.tools.manage_watchlist._manage",
        return_value={"codes": ["300850"], "count": 1, "changed": []},
    ) as manage:
        result = manage_watchlist("list", "新强联")

    assert result["success"] is True
    manage.assert_called_once_with("list", [])


def test_registry_list_watchlist_has_no_multiplexed_action_field() -> None:
    with patch(
        "src.tools.manage_watchlist._manage",
        return_value={"codes": ["300850"], "count": 1, "changed": []},
    ) as manage:
        result = ToolRegistry().execute(
            "list_watchlist",
            {},
        )

    assert result["success"] is True
    manage.assert_called_once_with("list", [])


def test_manage_watchlist_resolves_chinese_name_for_direct_tool_execution() -> None:
    with (
        patch(
            "src.tools.manage_watchlist.resolve_securities_csv",
            return_value=([{"input": "新强联", "symbol": "300850", "name": "新强联"}], []),
        ),
        patch(
            "src.tools.manage_watchlist._manage",
            return_value={"codes": ["300850"], "count": 1, "changed": ["300850"]},
        ) as manage,
    ):
        result = manage_watchlist("add", "新强联")

    assert result["changed"] == ["300850"]
    manage.assert_called_once_with("add", ["300850"])


def test_stale_fallback_hides_internal_repair_detail() -> None:
    stale = {"total": 5532, "data_time": "2026-07-04T21:03:14", "is_stale": True}
    job = MagicMock(status="failed", error="维护结果已失效：已恢复原 active 状态并启用覆盖率保护")
    with (
        patch.object(data_maintenance, "_universe_snapshot", side_effect=[stale, stale]),
        patch.object(data_maintenance, "_claim_job", return_value=("job-1", False)),
        patch.object(data_maintenance, "_get_job", return_value=job),
    ):
        result = data_maintenance.ensure_stock_universe(trigger="test")

    assert result["maintenance_status"] == "stale_fallback"
    assert "active 状态" not in result["warning"]
    assert "不完整的上游结果未写入数据库" in result["warning"]


def test_data_health_tool_marks_empty_universe_as_unknown_freshness() -> None:
    payload = {
        "stock_universe": {"total": 0, "data_time": None, "is_stale": True},
        "kline": {"covered_stocks": 0, "coverage_ratio": 0, "latest_trade_date": None},
        "financials": {
            "covered_stocks": 0,
            "coverage_ratio": 0,
            "latest_report_period": None,
            "last_fetched_at": None,
        },
        "recent_jobs": [],
    }
    with patch("src.tools.get_data_health._get_health", return_value=payload):
        result = get_data_health()

    assert result["success"] is True
    assert result["freshness_unknown"] is True
    assert result["is_stale"] is None
    assert result["warnings"]


def test_stock_universe_rejects_partial_source_before_writing() -> None:
    session = MagicMock()
    session.query.return_value.count.return_value = 5200
    db = MagicMock()
    db.get_session.return_value.__enter__.return_value = session
    partial = [{"code": f"{index:06d}", "name": f"公司{index}", "market": "sz"} for index in range(100)]
    with (
        patch(
            "src.services.stock_universe_service.DatabaseManager.get_instance",
            return_value=db,
        ),
        patch(
            "src.services.stock_universe_service.AkshareFetcher.get_all_a_stocks",
            return_value=partial,
        ),
    ):
        with pytest.raises(RuntimeError, match="安全要求"):
            sync_stock_universe()

    session.add.assert_not_called()
