"""
Integrations registry and environment diagnostics HTTP surface.

    GET /api/integrations             -> list of available/connected agent integrations
    GET /api/integrations/diagnostics -> system health and configuration status check
"""

import logging
from typing import Any, Dict, List
from fastapi import APIRouter

from app.config import settings
from app.database.mongodb import db_manager
from app.integrations.gmail.service import gmail_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["Integrations"])


@router.get("", response_model=Dict[str, Any])
async def list_integrations():
    """
    Returns the current status of all external tool and service integrations.
    """
    gmail_status = await gmail_service.get_status()
    has_send_scope = any("gmail.send" in s.lower() for s in (gmail_status.scopes or []))
    
    if gmail_status.connected and has_send_scope:
        gmail_state = "CONNECTED"
    elif gmail_status.connected and not has_send_scope:
        gmail_state = "NEEDS_PERMISSION"
    else:
        gmail_state = "DISCONNECTED"

    items: List[Dict[str, Any]] = [
        {
            "id": "gmail",
            "name": "Gmail",
            "category": "communication",
            "description": "Send and read email through your authenticated Google workspace or personal account.",
            "connected": bool(gmail_status.connected and has_send_scope),
            "status": gmail_state,
            "account_id": gmail_status.account_id,
            "email_address": gmail_status.email_address,
            "scopes": gmail_status.scopes or [],
            "has_send_scope": has_send_scope,
            "configured": bool(getattr(settings, "GOOGLE_CLIENT_ID", None) and getattr(settings, "GOOGLE_CLIENT_SECRET", None)),
            "detail": (
                f"Connected as {gmail_status.email_address} with active send permissions."
                if (gmail_status.connected and has_send_scope)
                else (
                    f"Connected as {gmail_status.email_address} but missing 'gmail.send' scope. Reconnect to grant send permissions."
                    if gmail_status.connected
                    else "Resolve AI cannot send real emails until you connect a Gmail account."
                )
            ),
        },
        {
            "id": "web_search",
            "name": "Web Search",
            "category": "research",
            "description": "Live web search for market research, company information, and founder background.",
            "connected": True,
            "status": "AVAILABLE",
            "provider": "DuckDuckGo / Live Search",
        },
        {
            "id": "file_intelligence",
            "name": "File Intelligence",
            "category": "document_reasoning",
            "description": "Deep parsing and context ingestion for multi-file autonomous reasoning.",
            "connected": True,
            "status": "AVAILABLE",
            "supported_extensions": list(settings.ALLOWED_FILE_EXTENSIONS),
            "max_file_size_mb": settings.MAX_FILE_SIZE_BYTES // (1024 * 1024),
        },
    ]

    return {
        "integrations": items,
        "total": len(items),
    }


@router.get("/diagnostics", response_model=Dict[str, Any])
async def get_system_diagnostics():
    """
    Returns environment and component health status:
    Database, LLM, Gmail OAuth, Gmail Account, Worker.
    """
    gmail_status = await gmail_service.get_status()
    has_send_scope = any("gmail.send" in s.lower() for s in (gmail_status.scopes or []))
    oauth_configured = bool(getattr(settings, "GOOGLE_CLIENT_ID", None) and getattr(settings, "GOOGLE_CLIENT_SECRET", None))
    llm_configured = bool(getattr(settings, "GEMINI_API_KEY", None))
    db_connected = bool(db_manager.is_connected and db_manager.db is not None)

    return {
        "database": {
            "name": "MongoDB Atlas" if db_connected else "In-Memory / Disconnected",
            "connected": db_connected,
            "healthy": db_connected,
        },
        "llm": {
            "provider": "Google Gemini",
            "model": settings.LLM_MODEL,
            "configured": llm_configured,
            "healthy": llm_configured,
        },
        "gmail_oauth": {
            "configured": oauth_configured,
            "client_id": f"{settings.GOOGLE_CLIENT_ID[:12]}..." if oauth_configured else None,
            "redirect_uri": settings.GOOGLE_OAUTH_REDIRECT_URI,
            "healthy": oauth_configured,
        },
        "gmail_account": {
            "connected": bool(gmail_status.connected),
            "email_address": gmail_status.email_address,
            "has_send_scope": has_send_scope,
            "healthy": bool(gmail_status.connected and has_send_scope),
            "status": "CONNECTED" if (gmail_status.connected and has_send_scope) else ("NEEDS_REAUTH" if gmail_status.connected else "NOT_CONNECTED"),
        },
        "worker": {
            "status": "active",
            "healthy": True,
        },
    }
