"""
Integrations registry HTTP surface.

    GET /api/integrations -> list of available/connected agent integrations
"""

import logging
from typing import Any, Dict, List
from fastapi import APIRouter

from app.config import settings
from app.integrations.gmail.service import gmail_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["Integrations"])


@router.get("", response_model=Dict[str, Any])
async def list_integrations():
    """
    Returns the current status of all external tool and service integrations.
    """
    gmail_status = await gmail_service.get_status()
    
    items: List[Dict[str, Any]] = [
        {
            "id": "gmail",
            "name": "Gmail",
            "category": "communication",
            "description": "Send and read email through your authenticated Google workspace or personal account.",
            "connected": bool(gmail_status.connected),
            "status": "CONNECTED" if gmail_status.connected else "DISCONNECTED",
            "account_id": gmail_status.account_id,
            "email_address": gmail_status.email_address,
            "scopes": gmail_status.scopes or [],
            "configured": bool(getattr(settings, "GOOGLE_CLIENT_ID", None) and getattr(settings, "GOOGLE_CLIENT_SECRET", None)),

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
