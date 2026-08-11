from fastapi import APIRouter
from app.config import settings
from app.database.mongodb import db_manager

router = APIRouter()

@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "project": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "mongodb_connected": db_manager.is_connected,
        "database_mode": "MongoDB" if db_manager.is_connected else "In-Memory Fallback"
    }
