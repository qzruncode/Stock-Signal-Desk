# -*- coding: utf-8 -*-
"""Prompt-injection and trace privacy contracts."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from cryptography.fernet import Fernet
from starlette.requests import Request
from starlette.responses import Response

from api.middlewares.auth import AuthMiddleware
from src.storage.mixins.agent_run_trace import (
    encode_agent_trace_json,
    redact_agent_trace,
)


def test_trace_redaction_covers_nested_secrets_and_common_pii():
    redacted = redact_agent_trace(
        {
            "api_key": "secret-value",
            "nested": {
                "access_token": "token-value",
                "text": ("contact foo@example.com or 13800138000; " "Authorization: Bearer abcdefghijklmnop"),
            },
        }
    )

    assert redacted["api_key"] == "[redacted]"
    assert redacted["nested"]["access_token"] == "[redacted]"
    text = redacted["nested"]["text"]
    assert "foo@example.com" not in text
    assert "13800138000" not in text
    assert "abcdefghijklmnop" not in text


def test_trace_encryption_is_authenticated_and_contains_only_redacted_data(
    monkeypatch,
):
    key = Fernet.generate_key()
    monkeypatch.setenv("AGENT_TRACE_ENCRYPTION_KEY", key.decode("ascii"))

    encoded = encode_agent_trace_json(
        {"authorization": "Bearer top-secret", "value": "safe"},
        encrypt=True,
    )

    assert encoded.startswith("enc:v1:")
    decrypted = Fernet(key).decrypt(encoded.removeprefix("enc:v1:").encode("ascii")).decode("utf-8")
    payload = json.loads(decrypted)
    assert payload == {"authorization": "[redacted]", "value": "safe"}


def _request(path: str, headers: list[tuple[bytes, bytes]] = ()) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": headers,
            "query_string": b"",
            "scheme": "https",
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 443),
            "root_path": "",
        }
    )


def test_identity_headers_require_the_proxy_shared_secret(monkeypatch):
    secret = "s" * 40
    monkeypatch.setenv("TRUSTED_IDENTITY_HEADERS", "true")
    monkeypatch.setenv("TRUSTED_IDENTITY_SHARED_SECRET", secret)
    monkeypatch.setattr(
        "api.middlewares.auth.is_auth_enabled",
        lambda: False,
    )
    middleware = AuthMiddleware(app=MagicMock())
    call_next = AsyncMock(return_value=Response(status_code=200))

    rejected = asyncio.run(
        middleware.dispatch(
            _request(
                "/api/v1/agent/conversations",
                [
                    (b"x-dsa-tenant-id", b"tenant-a"),
                    (b"x-dsa-user-id", b"alice"),
                ],
            ),
            call_next,
        )
    )
    accepted_request = _request(
        "/api/v1/agent/conversations",
        [
            (b"x-dsa-tenant-id", b"tenant-a"),
            (b"x-dsa-user-id", b"alice"),
            (b"x-dsa-identity-secret", secret.encode("ascii")),
        ],
    )
    accepted = asyncio.run(
        middleware.dispatch(
            accepted_request,
            call_next,
        )
    )

    assert rejected.status_code == 401
    assert accepted.status_code == 200
    assert accepted_request.state.tenant_id == "tenant-a"
    assert accepted_request.state.owner_id == "alice"
    call_next.assert_awaited_once()


def test_liveness_stays_independent_of_identity_proxy_headers(monkeypatch):
    monkeypatch.setenv("TRUSTED_IDENTITY_HEADERS", "true")
    monkeypatch.delenv("TRUSTED_IDENTITY_SHARED_SECRET", raising=False)
    monkeypatch.setattr(
        "api.middlewares.auth.is_auth_enabled",
        lambda: True,
    )
    middleware = AuthMiddleware(app=MagicMock())
    call_next = AsyncMock(return_value=Response(status_code=200))

    response = asyncio.run(
        middleware.dispatch(
            _request("/api/health"),
            call_next,
        )
    )

    assert response.status_code == 200
    call_next.assert_awaited_once()
