"""Sector catalog HTTP façade; acquisition and storage belong to the data service."""

from fastapi import APIRouter, Query
from typing import Literal
from src.services.market_data_client import read_source

router = APIRouter()


@router.get("/sectors", summary="获取行业/概念板块列表")
def get_sector_list(
    type: Literal["industry", "concept"] = Query("industry"), force: bool = Query(False)
):
    return read_source(
        "market.sectors",
        {
            "type": type if isinstance(type, str) else "industry",
            "force": force if isinstance(force, bool) else False,
        },
    )
