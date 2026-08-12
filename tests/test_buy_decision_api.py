"""Boundary tests for the buy-decision HTTP adapter."""

from __future__ import annotations

import asyncio
import base64

import pytest
from fastapi import HTTPException

from api.v1.endpoints.buy_decision import analyze_buy_criteria


def test_pre_fetched_payload_must_be_a_json_object() -> None:
    encoded = base64.urlsafe_b64encode(b"[]").decode("ascii").rstrip("=")

    with pytest.raises(HTTPException) as error:
        asyncio.run(analyze_buy_criteria("600519", encoded))

    assert error.value.status_code == 400
    assert error.value.detail == "Invalid pre_fetched data"


def test_pre_fetched_payload_rejects_invalid_base64() -> None:
    with pytest.raises(HTTPException) as error:
        asyncio.run(analyze_buy_criteria("600519", "not-base64"))

    assert error.value.status_code == 400
    assert error.value.detail == "Invalid pre_fetched data"
