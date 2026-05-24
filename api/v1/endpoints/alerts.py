from fastapi import APIRouter

router = APIRouter()


@router.get("/alerts")
async def list_alerts():
    return {"alerts": []}
