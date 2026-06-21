# -*- coding: utf-8 -*-
"""Watchlist (自选股列表) management endpoints."""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_system_config_service
from api.v1.schemas.common import ErrorResponse
from src.storage import DatabaseManager, WatchlistGroupNameConflict
from src.services.system_config_service import (
    ConfigConflictError,
    ConfigValidationError,
    SystemConfigService,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class WatchlistGroupUpsertRequest(BaseModel):
    name: str
    codes: List[str] = []
    source: Optional[str] = "manual"


class WatchlistGroupPatchRequest(BaseModel):
    name: Optional[str] = None
    codes: Optional[List[str]] = None


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
) -> tuple[List[str], str]:
    """Persist STOCK_LIST. Returns the updated list and new config version."""
    value = ",".join(codes)
    result = service.update(
        config_version=config_version,
        items=[{"key": "STOCK_LIST", "value": value}],
        mask_token=mask_token,
        reload_now=True,
    )
    updated_codes = [c.strip() for c in value.split(",") if c.strip()]
    return updated_codes, result.get("config_version", config_version)


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
        updated, new_config_version = _save_stock_list(service, current, config_version, mask_token)
        return {
            "codes": updated,
            "count": len(updated),
            "added": added,
            "message": f"Added {len(added)} stock(s)",
            "configVersion": new_config_version,
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
        updated, new_config_version = _save_stock_list(service, new_list, config_version, mask_token)
        return {
            "codes": updated,
            "count": len(updated),
            "removed": removed,
            "message": f"Removed {len(removed)} stock(s)",
            "configVersion": new_config_version,
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


# ── Watchlist Groups（自选股自定义分组）──────────────────────────────────


@router.get(
    "/groups",
    summary="List custom watchlist groups",
    responses={500: {"model": ErrorResponse}},
)
def list_watchlist_groups():
    """Return all custom watchlist groups (excludes the default STOCK_LIST group)."""
    try:
        db = DatabaseManager.get_instance()
        return {"groups": db.list_watchlist_groups()}
    except Exception as exc:
        logger.error("Failed to list watchlist groups: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "Failed to list watchlist groups"},
        )


@router.post(
    "/groups",
    summary="Create or update a watchlist group (upsert by name)",
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def upsert_watchlist_group(body: WatchlistGroupUpsertRequest):
    """Create a group, or replace an existing group's codes when the name matches."""
    try:
        db = DatabaseManager.get_instance()
        return db.upsert_watchlist_group(body.name, body.codes, body.source or "manual")
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": str(exc)},
        )
    except Exception as exc:
        logger.error("Failed to upsert watchlist group: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "Failed to save watchlist group"},
        )


@router.patch(
    "/groups/{group_id}",
    summary="Update a watchlist group's name or codes",
    responses={
        400: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
)
def patch_watchlist_group(group_id: str, body: WatchlistGroupPatchRequest):
    """Rename a group and/or replace its codes."""
    try:
        db = DatabaseManager.get_instance()
        group = db.update_watchlist_group(group_id, name=body.name, codes=body.codes)
        if group is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "not_found", "message": "Watchlist group not found"},
            )
        return group
    except WatchlistGroupNameConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "name_conflict", "message": f"分组名称已存在: {exc}"},
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": str(exc)},
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to update watchlist group: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "Failed to update watchlist group"},
        )


@router.delete(
    "/groups/{group_id}",
    summary="Delete a watchlist group",
    responses={404: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
def delete_watchlist_group(group_id: str):
    """Delete a custom watchlist group by id."""
    try:
        db = DatabaseManager.get_instance()
        deleted = db.delete_watchlist_group(group_id)
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail={"error": "not_found", "message": "Watchlist group not found"},
            )
        return {"deleted": True}
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to delete watchlist group: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": "internal_error", "message": "Failed to delete watchlist group"},
        )
