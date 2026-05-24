from fastapi import APIRouter

router = APIRouter()


@router.get("/portfolio")
async def get_portfolio():
    return {"portfolio": {}}
