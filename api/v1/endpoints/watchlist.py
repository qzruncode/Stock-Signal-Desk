# -*- coding: utf-8 -*-
"""Watchlist (自选股列表) management endpoints."""

from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_system_config_service
from api.v1.schemas.common import ErrorResponse
from src.services.system_config_service import (
    ConfigConflictError,
    ConfigValidationError,
    SystemConfigService,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _load_stock_list(service: SystemConfigService) -> tuple[List[str], str, str]:
    """Read current STOCK_LIST from config. Returns (codes, config_version, value)."""
    cfg = service.get_config(include_schema=False, mask_token="")
    items = cfg.get("items", [])
    config_version = cfg.get("config_version", "")
    mask_token = cfg.get("mask_token", "")
    for item in items:
        if item.get("key") == "STOCK_LIST":
            raw = item.get("value", "")
            codes = [c.strip() for c in raw.split(",") if c.strip()]
            return codes, config_version, mask_token
    return [], config_version, mask_token


def _save_stock_list(
    service: SystemConfigService,
    codes: List[str],
    config_version: str,
    mask_token: str,
) -> List[str]:
    """Persist STOCK_LIST. Returns the updated list."""
    value = ",".join(codes)
    result = service.update(
        config_version=config_version,
        items=[{"key": "STOCK_LIST", "value": value}],
        mask_token=mask_token,
        reload_now=True,
    )
    updated_codes = [c.strip() for c in value.split(",") if c.strip()]
    return updated_codes


@router.get(
    "",
    summary="Get watchlist",
    responses={500: {"model": ErrorResponse}},
)
def get_watchlist(
    service: SystemConfigService = Depends(get_system_config_service),
):
    """Return the current self-selected stock list."""
    try:
        codes, config_version, _ = _load_stock_list(service)
        return {
            "codes": codes,
            "count": len(codes),
            "configVersion": config_version,
        }
    except Exception as exc:
        logger.error("Failed to get watchlist: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "Failed to get watchlist"},
        )


@router.post(
    "/add",
    summary="Add stocks to watchlist",
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def add_to_watchlist(
    codes: List[str],
    service: SystemConfigService = Depends(get_system_config_service),
):
    """Add one or more stock codes to the watchlist. Duplicates are skipped."""
    try:
        current, config_version, mask_token = _load_stock_list(service)
        added: List[str] = []
        for code in codes:
            code = code.strip().upper()
            if not code:
                continue
            if code not in current:
                current.append(code)
                added.append(code)
        if not added:
            return {
                "codes": current,
                "count": len(current),
                "added": [],
                "message": "No new stocks added (all already in list)",
                "configVersion": config_version,
            }
        updated = _save_stock_list(service, current, config_version, mask_token)
        return {
            "codes": updated,
            "count": len(updated),
            "added": added,
            "message": f"Added {len(added)} stock(s)",
            "configVersion": config_version,
        }
    except ConfigValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": str(exc)},
        )
    except ConfigConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "config_version_conflict", "message": str(exc)},
        )
    except Exception as exc:
        logger.error("Failed to add to watchlist: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "Failed to add stocks to watchlist"},
        )


@router.post(
    "/remove",
    summary="Remove stocks from watchlist",
    responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def remove_from_watchlist(
    codes: List[str],
    service: SystemConfigService = Depends(get_system_config_service),
):
    """Remove one or more stock codes from the watchlist."""
    try:
        current, config_version, mask_token = _load_stock_list(service)
        remove_set = {c.strip().upper() for c in codes if c.strip()}
        removed: List[str] = []
        new_list: List[str] = []
        for code in current:
            if code in remove_set:
                removed.append(code)
            else:
                new_list.append(code)
        if not removed:
            return {
                "codes": current,
                "count": len(current),
                "removed": [],
                "message": "No matching stocks found to remove",
                "configVersion": config_version,
            }
        updated = _save_stock_list(service, new_list, config_version, mask_token)
        return {
            "codes": updated,
            "count": len(updated),
            "removed": removed,
            "message": f"Removed {len(removed)} stock(s)",
            "configVersion": config_version,
        }
    except ConfigValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": str(exc)},
        )
    except ConfigConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "config_version_conflict", "message": str(exc)},
        )
    except Exception as exc:
        logger.error("Failed to remove from watchlist: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "Failed to remove stocks from watchlist"},
        )
