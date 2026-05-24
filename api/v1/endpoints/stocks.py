from fastapi import APIRouter

router = APIRouter()


@router.get("/stocks")
async def list_stocks():
    return {"stocks": []}
