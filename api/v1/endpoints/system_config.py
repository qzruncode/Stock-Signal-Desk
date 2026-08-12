"""System configuration endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_system_config_service
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.system_config import (
    SystemConfigConflictResponse,
    SystemConfigResponse,
    SystemConfigSchemaResponse,
    SetupStatusResponse,
    SystemConfigValidationErrorResponse,
    TestNotificationChannelRequest,
    TestNotificationChannelResponse,
    UpdateSystemConfigRequest,
    UpdateSystemConfigResponse,
)
from src.services.system_config_service import (
    ConfigConflictError,
    ConfigValidationError,
    SystemConfigService,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/config",
    response_model=SystemConfigResponse,
    responses={
        200: {"description": "Configuration loaded"},
        401: {"description": "Unauthorized", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Get system configuration",
    description="Read current configuration from .env; sensitive values are masked server-side.",
)
def get_system_config(
    include_schema: bool = Query(True, description="Whether to include schema metadata"),
    service: SystemConfigService = Depends(get_system_config_service),
) -> SystemConfigResponse:
    try:
        payload = service.get_config(include_schema=include_schema)
        return SystemConfigResponse.model_validate(payload)
    except Exception as exc:
        logger.error("Failed to load system configuration: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "Failed to load system configuration"},
        )


@router.get(
    "/config/setup/status",
    response_model=SetupStatusResponse,
    responses={
        200: {"description": "Setup status loaded"},
        401: {"description": "Unauthorized", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Get first-run setup status",
    description="Read a side-effect-free setup readiness summary from saved and runtime configuration.",
)
def get_setup_status(
    service: SystemConfigService = Depends(get_system_config_service),
) -> SetupStatusResponse:
    try:
        payload = service.get_setup_status()
        return SetupStatusResponse.model_validate(payload)
    except Exception as exc:
        logger.error("Failed to load setup status: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "Failed to load setup status"},
        )


@router.put(
    "/config",
    response_model=UpdateSystemConfigResponse,
    responses={
        200: {"description": "Configuration updated"},
        400: {"description": "Validation failed", "model": SystemConfigValidationErrorResponse},
        409: {"description": "Version conflict", "model": SystemConfigConflictResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Update system configuration",
    description="Update key-value pairs in .env. Mask token preserves existing secret values.",
)
def update_system_config(
    request: UpdateSystemConfigRequest,
    service: SystemConfigService = Depends(get_system_config_service),
) -> UpdateSystemConfigResponse:
    try:
        payload = service.update(
            config_version=request.config_version,
            items=[item.model_dump() for item in request.items],
            mask_token=request.mask_token,
            reload_now=request.reload_now,
        )
        return UpdateSystemConfigResponse.model_validate(payload)
    except ConfigValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "validation_failed",
                "message": "System configuration validation failed",
                "issues": exc.issues,
            },
        )
    except ConfigConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "config_version_conflict",
                "message": "Configuration has changed, please reload and retry",
                "current_config_version": exc.current_version,
            },
        )
    except Exception as exc:
        logger.error("Failed to update system configuration: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "Failed to update system configuration"},
        )


@router.post(
    "/config/notification/test-channel",
    response_model=TestNotificationChannelResponse,
    responses={
        200: {"description": "Notification channel test completed"},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Test one notification channel",
    description="Send a short test notification using unsaved or saved notification configuration.",
)
def test_notification_channel(
    request: TestNotificationChannelRequest,
    service: SystemConfigService = Depends(get_system_config_service),
) -> TestNotificationChannelResponse:
    try:
        payload = service.test_notification_channel(
            channel=request.channel,
            items=[item.model_dump() for item in request.items],
            mask_token=request.mask_token,
            title=request.title,
            content=request.content,
            timeout_seconds=request.timeout_seconds,
        )
        return TestNotificationChannelResponse.model_validate(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail={"error": "validation_error", "message": str(exc)})
    except Exception as exc:
        logger.error("Failed to test notification channel: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": "internal_error", "message": "Failed to test notification channel"}
        )


@router.get(
    "/config/schema",
    response_model=SystemConfigSchemaResponse,
    responses={
        200: {"description": "Schema loaded"},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Get system configuration schema",
    description="Return categorized field metadata used for dynamic settings form rendering.",
)
def get_system_config_schema(
    service: SystemConfigService = Depends(get_system_config_service),
) -> SystemConfigSchemaResponse:
    try:
        payload = service.get_schema()
        return SystemConfigSchemaResponse.model_validate(payload)
    except Exception as exc:
        logger.error("Failed to load system configuration schema: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500, detail={"error": "internal_error", "message": "Failed to load system configuration schema"}
        )
