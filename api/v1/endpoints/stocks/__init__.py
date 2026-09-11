# -*- coding: utf-8 -*-
"""A-share stock endpoints — route registration."""

from fastapi import APIRouter

router = APIRouter()

# Import submodules to register routes on the shared router
from api.v1.endpoints.stocks import list  # noqa: E402, F401
