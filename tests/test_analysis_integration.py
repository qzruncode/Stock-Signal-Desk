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
from datetime import datetime
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from api.app import create_app
from src.services.task_queue import AnalysisTaskQueue, TaskStatus, TaskInfo
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
    with (
        patch("api.middlewares.auth.is_auth_enabled", return_value=False),
        patch("src.auth.is_auth_enabled", return_value=False),
    ):
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

    def test_trigger_analysis_dos_protection(self, client):
        """Test that excessive stock codes are rejected."""
        too_many_codes = [f"{i:06d}" for i in range(101)]
        response = client.post("/api/v1/analysis/analyze", json={"stock_codes": too_many_codes, "async_mode": True})

        assert response.status_code == 400
        assert "最多支持" in response.json()["message"]
