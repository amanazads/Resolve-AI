import os
import sys
import logging
from pathlib import Path

# Ensure root directory and backend directory are in sys.path
root_dir = str(Path(__file__).resolve().parent.parent.parent)
backend_dir = str(Path(__file__).resolve().parent.parent)
for p in [root_dir, backend_dir]:
    if p not in sys.path:
        sys.path.insert(0, p)

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database.mongodb import db_manager
from app.api.routes import chat, health, automation, contacts
from app.campaigns import router as campaigns_router
from app.tasks import tasks_router
from app.integrations.routes import router as integrations_router
from app.integrations.gmail.routes import router as gmail_router
from app.permissions.routes import router as permissions_router
from app.automation.campaign_service import campaign_service
from app.automation.worker import campaign_execution_service

# Configure Logging
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing application resources...")
    await db_manager.connect()
    # Recover any stale or in-flight jobs from previous server restarts
    try:
        await campaign_service.recover_from_restart()
    except Exception as e:
        logger.warning(f"Error during startup crash recovery: {e}")
    # Reclaim campaign jobs orphaned by a worker that died, and resume any
    # campaign that was still RUNNING when this process last stopped.
    try:
        await campaign_execution_service.recover_on_startup()
    except Exception as e:
        logger.warning(f"Error during campaign execution recovery: {e}")
    yield
    logger.info("Shutting down application resources...")
    try:
        await campaign_execution_service.shutdown()
    except Exception as e:
        logger.warning(f"Error stopping campaign workers: {e}")
    await db_manager.close()

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    lifespan=lifespan
)

# Enable secure CORS with configured allowed origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API Routers
app.include_router(health.router, prefix=settings.API_PREFIX, tags=["Health"])
app.include_router(chat.router, prefix=settings.API_PREFIX, tags=["Chat"])
app.include_router(automation.router, prefix=settings.API_PREFIX, tags=["Automation"])
app.include_router(contacts.router, prefix=settings.API_PREFIX, tags=["Contacts"])
app.include_router(campaigns_router, prefix=settings.API_PREFIX, tags=["Campaigns"])
app.include_router(tasks_router, prefix=settings.API_PREFIX, tags=["Tasks"])
app.include_router(integrations_router, prefix=settings.API_PREFIX, tags=["Integrations"])
app.include_router(gmail_router, prefix=settings.API_PREFIX)
app.include_router(permissions_router, prefix=settings.API_PREFIX, tags=["Permissions & Audit"])

@app.get("/")
async def root():
    return {
        "message": f"Welcome to {settings.PROJECT_NAME} API",
        "docs": "/docs",
        "health": f"{settings.API_PREFIX}/health"
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)

