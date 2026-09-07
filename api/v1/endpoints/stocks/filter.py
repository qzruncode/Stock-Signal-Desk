"""Fundamental filtering consumes a coherent, freshness-checked snapshot."""

from pydantic import BaseModel, Field
from api.v1.endpoints.stocks import router
from src.services.market_data_client import get_market_data_client


class FundamentalFilterRequest(BaseModel):
    codes: list[str] = Field(default_factory=list, max_length=200)


@router.post("/fundamental-filter", summary="获取达标财务数据用于筛选")
def fundamental_filter(body: FundamentalFilterRequest):
    if not body.codes:
        return {"data": {}, "freshness": {}, "partial": False}
    response = get_market_data_client().snapshot(body.codes, ["financials"])
    return {
        "data": {
            code: item["financials"] or {} for code, item in response["items"].items()
        },
        "freshness": response["freshness"],
        "partial": response["partial"],
    }
