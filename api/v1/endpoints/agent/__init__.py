# -*- coding: utf-8 -*-
"""Agent chat endpoints — route registration."""

from fastapi import APIRouter

router = APIRouter()

# Import submodules to register routes on the shared router
from api.v1.endpoints.agent import chat  # noqa: E402, F401
from api.v1.endpoints.agent import conversations  # noqa: E402, F401
from api.v1.endpoints.agent import approvals  # noqa: E402, F401
from api.v1.endpoints.agent import tools  # noqa: E402, F401
from api.v1.endpoints.agent import health  # noqa: E402, F401
from api.v1.endpoints.agent import tool_registry_meta  # noqa: E402, F401
from api.v1.endpoints.agent import prompts  # noqa: E402, F401
from api.v1.endpoints.agent import exports  # noqa: E402, F401
from api.v1.endpoints.agent import quality  # noqa: E402, F401
from api.v1.endpoints.agent import financial_lifecycle  # noqa: E402, F401
from api.v1.endpoints.agent import research_alerts  # noqa: E402, F401
from api.v1.endpoints.agent import run_explorer  # noqa: E402, F401
from api.v1.endpoints.agent import resources  # noqa: E402, F401
from api.v1.endpoints.agent import checkpoints  # noqa: E402, F401

# The LangGraph tool catalog is exposed for metadata/readiness endpoints.
from api.v1.endpoints.agent.chat import _registry  # noqa: E402, F401
from api.v1.endpoints.agent.tools import (  # noqa: E402, F401
    _compact_tool_result,
    _format_result,
    _maybe_attach_search_fallback,
)
from api.v1.endpoints.agent.health import _assess_tool_data_health  # noqa: E402, F401
