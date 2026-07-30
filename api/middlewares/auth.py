# -*- coding: utf-8 -*-
"""
Auth middleware: protect /api/v1/* when admin auth is enabled.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from src.auth import COOKIE_NAME, is_auth_enabled, verify_session

logger = logging.getLogger(__name__)
_PRINCIPAL_ID = re.compile(r"^[A-Za-z0-9_.:@-]{1,128}$")

EXEMPT_PATHS = frozenset({
    "/api/v1/auth/login",
    "/api/v1/auth/status",
    "/api/health",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
})


def _path_exempt(path: str) -> bool:
    """Check if path is exempt from auth."""
    normalized = path.rstrip("/") or "/"
    return normalized in EXEMPT_PATHS


class AuthMiddleware(BaseHTTPMiddleware):
    """Require valid session for /api/v1/* when auth is enabled."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ):
        path = request.url.path
        path_exempt = _path_exempt(path)
        tenant_id = (os.getenv("DSA_TENANT_ID") or "local").strip()
        owner_id = (os.getenv("DSA_OWNER_ID") or "admin").strip()
        if not path_exempt and str(
            os.getenv("TRUSTED_IDENTITY_HEADERS") or ""
        ).lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            expected_secret = (
                os.getenv("TRUSTED_IDENTITY_SHARED_SECRET") or ""
            ).strip()
            supplied_secret = (
                request.headers.get("x-dsa-identity-secret") or ""
            ).strip()
            if (
                len(expected_secret) < 32
                or not hmac.compare_digest(
                    supplied_secret.encode("utf-8"),
                    expected_secret.encode("utf-8"),
                )
            ):
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": "untrusted_identity_proxy",
                        "message": "Trusted proxy identity could not be verified",
                    },
                )
            tenant_id = (request.headers.get("x-dsa-tenant-id") or "").strip()
            owner_id = (request.headers.get("x-dsa-user-id") or "").strip()
            if (
                not _PRINCIPAL_ID.fullmatch(tenant_id)
                or not _PRINCIPAL_ID.fullmatch(owner_id)
            ):
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": "invalid_principal",
                        "message": "Trusted identity headers are missing or invalid",
                    },
                )
        request.state.tenant_id = tenant_id[:64] or "local"
        request.state.owner_id = owner_id[:128] or "admin"

        if not is_auth_enabled():
            return await call_next(request)

        if path_exempt:
            return await call_next(request)

        if not path.startswith("/api/v1/"):
            return await call_next(request)

        cookie_val = request.cookies.get(COOKIE_NAME)
        if not cookie_val or not verify_session(cookie_val):
            return JSONResponse(
                status_code=401,
                content={
                    "error": "unauthorized",
                    "message": "Login required",
                },
            )

        return await call_next(request)


def add_auth_middleware(app):
    """Add auth middleware to protect API routes.

    The middleware is always registered; whether auth is enforced is determined
    at request time by is_auth_enabled() so the decision stays consistent across
    any runtime configuration reload.
    """
    app.add_middleware(AuthMiddleware)
