"""HTTP adapter for the Agent-owned index tool."""

from fastapi import APIRouter, Query

router = APIRouter()


@router.get("/index", summary="获取大盘指数数据")
def get_index_data(
    index_code: str = Query("000001", description="指数代码"),
    days: int = Query(20, ge=5, le=250, description="最近交易日数量"),
):
    from src.tools.get_index_data import get_index_data as tool_get_index_data

    return tool_get_index_data(
        index_code if isinstance(index_code, str) else "000001",
        days if isinstance(days, int) else 20,
    )
