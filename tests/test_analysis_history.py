# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 分析历史存储单元测试
===================================

职责：
1. 验证分析历史保存逻辑
2. 验证上下文快照保存开关
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Keep this test runnable when optional LLM runtime deps are not installed.
try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    sys.modules["litellm"] = MagicMock()

try:
    from fastapi.testclient import TestClient
    from api.app import create_app
    from api.v1.endpoints.history import get_history_detail
except ModuleNotFoundError:
    TestClient = None
    create_app = None
    get_history_detail = None

from src.config import Config
from src.storage import DatabaseManager, AnalysisHistory, BacktestResult
from src.analyzer import AnalysisResult
from src.services.history_service import HistoryService
import src.auth as auth



"""Shared fixtures for the focused test slices."""


"""Shared fixtures for the focused test slices."""
