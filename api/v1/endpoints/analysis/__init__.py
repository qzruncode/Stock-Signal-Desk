# -*- coding: utf-8 -*-
"""Stock analysis endpoints — route registration."""

from fastapi import APIRouter

router = APIRouter()

# Import submodules to register routes on the shared router
from api.v1.endpoints.analysis import trigger  # noqa: E402, F401
from api.v1.endpoints.analysis import task  # noqa: E402, F401
from api.v1.endpoints.analysis import report  # noqa: E402, F401