# -*- coding: utf-8 -*-
"""
===================================
Analysis Integration Tests
===================================

Covers:
- API endpoint /analyze
- Name resolution to code
- Task queue submission
- Metadata persistence (original_query, selection_source)
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from api.app import create_app
from src.services.task_queue import AnalysisTaskQueue, TaskStatus
from src.config import Config
import src.auth as auth

@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


@pytest.fixture(autouse=True)
def disable_auth():
    """Keep analysis integration tests independent from local auth env state."""
    auth._auth_enabled = None
    with patch("api.middlewares.auth.is_auth_enabled", return_value=False), \
         patch("src.auth.is_auth_enabled", return_value=False):
        yield
    auth._auth_enabled = None

@pytest.fixture
def mock_task_queue():
    with patch("api.v1.endpoints.analysis.get_task_queue") as mock_get:
        queue = MagicMock(spec=AnalysisTaskQueue)
        mock_get.return_value = queue
        yield queue

class TestAnalysisIntegration:
    """End-to-end integration tests for the analysis flow."""

    def test_trigger_analysis_flow_manual_name(self, client, mock_task_queue):
        """Test flow: User enters stock name -> resolved to code -> task submitted."""
        # Setup mock behavior
        mock_task_queue.submit_tasks_batch.return_value = (
            [MagicMock(task_id="test_task_123", stock_code="600519")],
            []
        )

        # Trigger analysis with a stock name
        response = client.post(
            "/api/v1/analysis/analyze",
            json={
                "stock_code": "贵州茅台",
                "async_mode": True,
                "original_query": "贵州茅台",
                "selection_source": "manual"
            }
        )

        assert response.status_code == 202
        data = response.json()
        assert data["task_id"] == "test_task_123"
        assert data["status"] == "pending"

        # Verify task queue received the correct resolved code and metadata.
        # Use call_args so this integration test stays focused on analysis flow
        # semantics even if the queue API gains orthogonal optional flags.
        mock_task_queue.submit_tasks_batch.assert_called_once()
        _, kwargs = mock_task_queue.submit_tasks_batch.call_args
        assert kwargs["stock_codes"] == ["600519"]
        assert kwargs["stock_name"] is None
        assert kwargs["original_query"] == "贵州茅台"
        assert kwargs["selection_source"] == "manual"
        assert kwargs["report_type"] == "detailed"
        assert kwargs["force_refresh"] is False
        assert kwargs["notify"] is True
        assert kwargs["prompt_template_id"] is None

    def test_trigger_analysis_passes_prompt_template_id(self, client, mock_task_queue):
        """Selected prompt template must be passed into async task execution."""
        mock_task_queue.submit_tasks_batch.return_value = (
            [MagicMock(task_id="test_task_tpl", stock_code="600519")],
            []
        )

        response = client.post(
            "/api/v1/analysis/analyze",
            json={
                "stock_code": "600519",
                "async_mode": True,
                "prompt_template_id": "template-alpha",
            }
        )

        assert response.status_code == 202
        mock_task_queue.submit_tasks_batch.assert_called_once()
        _, kwargs = mock_task_queue.submit_tasks_batch.call_args
        assert kwargs["prompt_template_id"] == "template-alpha"

    def test_ai_caller_injects_realtime_data_policy(self):
        """All stock AI calls must include realtime freshness constraints."""
        from src.ai_caller import call_ai_for_stock

        analyzer = MagicMock()
        analyzer._call_litellm.return_value = ("ok", "test-model", {})

        with patch("src.ai_caller.persist_llm_usage"):
            call_ai_for_stock(
                analyzer,
                system_prompt="自定义模板",
                stock_code="600519",
                stock_name="贵州茅台",
            )

        args, kwargs = analyzer._call_litellm.call_args
        assert "自定义模板" in kwargs["system_prompt"]
        assert "【实时数据硬性要求】" in kwargs["system_prompt"]
        assert "禁止把过期行情" in kwargs["system_prompt"]
        assert "实时最新数据" in args[0]

    def test_trigger_analysis_batch_deduplication(self, client, mock_task_queue):
        """Test de-duplication across different formats (600519 and 600519.SH)."""
        mock_task_queue.submit_tasks_batch.return_value = ([], [])

        client.post(
            "/api/v1/analysis/analyze",
            json={
                "stock_codes": ["600519", "600519.SH"],
                "async_mode": True
            }
        )

        # Should only submit once after de-duplication
        mock_task_queue.submit_tasks_batch.assert_called_once()
        args, kwargs = mock_task_queue.submit_tasks_batch.call_args
        assert len(kwargs["stock_codes"]) == 1
        assert kwargs["stock_codes"] == ["600519"]

    def test_trigger_analysis_dos_protection(self, client):
        """Test that excessive stock codes are rejected."""
        too_many_codes = [f"{i:06d}" for i in range(101)]
        response = client.post(
            "/api/v1/analysis/analyze",
            json={
                "stock_codes": too_many_codes,
                "async_mode": True
            }
        )

        assert response.status_code == 400
        assert "最多支持" in response.json()["message"]

    def test_trigger_analysis_metadata_isolation_in_batch(self, client, mock_task_queue):
        """Test that single-stock metadata isn't applied to batch tasks."""
        mock_task_queue.submit_tasks_batch.return_value = ([], [])

        client.post(
            "/api/v1/analysis/analyze",
            json={
                "stock_codes": ["600519", "000001"],
                "stock_name": "贵州茅台",
                "original_query": "茅台",
                "async_mode": True
            }
        )

        # Batch request: metadata should be None
        mock_task_queue.submit_tasks_batch.assert_called_once()
        args, kwargs = mock_task_queue.submit_tasks_batch.call_args
        assert kwargs["stock_name"] is None
        assert kwargs["original_query"] is None
        assert kwargs["selection_source"] is None
