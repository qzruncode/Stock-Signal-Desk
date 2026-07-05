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