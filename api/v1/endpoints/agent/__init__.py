# -*- coding: utf-8 -*-
"""Agent chat endpoints — route registration."""

from fastapi import APIRouter

router = APIRouter()

# Import submodules to register routes on the shared router
from api.v1.endpoints.agent import chat  # noqa: E402, F401
from api.v1.endpoints.agent import conversations  # noqa: E402, F401
from api.v1.endpoints.agent import tools  # noqa: E402, F401
from api.v1.endpoints.agent import health  # noqa: E402, F401
from api.v1.endpoints.agent import tool_registry_meta  # noqa: E402, F401
from api.v1.endpoints.agent import prompts  # noqa: E402, F401

# Re-export internal helpers so tests can import/patch them from the package namespace.
from api.v1.endpoints.agent.chat import (  # noqa: E402, F401
    MAX_REACT_ITERATIONS,
    _registry,
    _run_react_loop,
    litellm,
)
from api.v1.endpoints.agent.tools import (  # noqa: E402, F401
    _compact_tool_result,
    _format_result,
    _maybe_attach_search_fallback,
)
from api.v1.endpoints.agent.health import _assess_tool_data_health  # noqa: E402, F401