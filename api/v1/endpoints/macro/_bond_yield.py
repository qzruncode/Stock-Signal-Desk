"""HTTP adapter for the Agent-owned sovereign-yield tool."""

from fastapi import APIRouter, Query

router = APIRouter()


@router.get("/bond-yield", summary="获取国债收益率")
def get_bond_yield(
    country: str = Query("cn", description="国家: cn | us"),
    term: str = Query("10y", description="期限: 2y | 5y | 10y | 30y"),
    days: int = Query(30, ge=5, le=250, description="最近交易日数量"),
):
    from src.tools.get_bond_yield import get_bond_yield as tool_get_bond_yield

    return tool_get_bond_yield(
        country if isinstance(country, str) else "cn",
        term if isinstance(term, str) else "10y",
        days if isinstance(days, int) else 30,
    )
