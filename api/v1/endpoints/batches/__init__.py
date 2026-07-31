# -*- coding: utf-8 -*-
"""Batch run endpoints — route registration."""

from fastapi import APIRouter

router = APIRouter(tags=["batch"])

# Import submodules to register routes on the shared router
from api.v1.endpoints.batches import run  # noqa: E402, F401
from api.v1.endpoints.batches import schedule  # noqa: E402, F401
from api.v1.endpoints.batches import helpers  # noqa: E402, F401
