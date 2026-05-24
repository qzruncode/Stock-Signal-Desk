from fastapi import APIRouter

router = APIRouter()


@router.get("/backtest")
async def list_backtests():
    return {"backtests": []}
