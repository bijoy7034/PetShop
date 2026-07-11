from fastapi import APIRouter
from config.db import mongo_manager
router = APIRouter(prefix="/health", tags=["health"])


@router.get("")
async def health():
    if not mongo_manager.ping():
        return {"status": "error", "message": "MongoDB connection failed"}
    return {"status": "ok"}
