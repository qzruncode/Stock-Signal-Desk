# -*- coding: utf-8 -*-
"""Expose the project's complete live concept-board catalog to workflows."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.services.domain_board_catalog import (
    get_domain_board_catalog as _load_domain_board_catalog,
)
from src.tools.base import TypedToolResult


class GetDomainBoardCatalogArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DomainBoardCatalogItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sector_code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    main_flow_rank: int | None = None
    main_net_inflow: float | None = None
    main_net_inflow_pct: float | None = None
    pct_chg: float | None = None


class GetDomainBoardCatalogResult(TypedToolResult):
    boards: list[DomainBoardCatalogItem]
    board_names: list[str]
    board_count: int = Field(ge=0)
    catalog_snapshot_id: str = Field(min_length=1, max_length=64)
    source: str | None = None


def get_domain_board_catalog() -> dict[str, Any]:
    return _load_domain_board_catalog()


__all__ = [
    "DomainBoardCatalogItem",
    "GetDomainBoardCatalogArgs",
    "GetDomainBoardCatalogResult",
    "get_domain_board_catalog",
]
