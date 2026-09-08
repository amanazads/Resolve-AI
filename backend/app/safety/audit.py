"""
Safety & Compliance Audit Logging.

Maintains an immutable, persistent audit log of all safety checks,
validation results, suppression events, policy violations, and rate-limiting
blocks. Redacts sensitive credentials or secret tokens automatically.
"""

import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

from app.database.mongodb import db_manager

logger = logging.getLogger(__name__)


class SafetyAuditEventType(str, Enum):
    CAMPAIGN_VALIDATION_PASSED = "CAMPAIGN_VALIDATION_PASSED"
    CAMPAIGN_VALIDATION_FAILED = "CAMPAIGN_VALIDATION_FAILED"
    RECIPIENT_SUPPRESSED_BLOCKED = "RECIPIENT_SUPPRESSED_BLOCKED"
    OPT_OUT_RECORDED = "OPT_OUT_RECORDED"
    UNSUBSCRIBE_RECORDED = "UNSUBSCRIBE_RECORDED"
    PERMANENT_BOUNCE_RECORDED = "PERMANENT_BOUNCE_RECORDED"
    RATE_LIMIT_ENFORCED = "RATE_LIMIT_ENFORCED"
    DRY_RUN_ENFORCED = "DRY_RUN_ENFORCED"
    UNAUTHORIZED_ACTION_BLOCKED = "UNAUTHORIZED_ACTION_BLOCKED"


class SafetyAuditEvent(BaseModel):
    event_type: SafetyAuditEventType
    campaign_id: Optional[str] = None
    actor_id: Optional[str] = "system"
    target_email: Optional[str] = None
    details: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SafetyAuditLogger:
    """
    Records compliance & safety audit events.
    Guarantees credential redaction before persistence.
    """

    _SENSITIVE_KEYS = frozenset(
        {
            "token",
            "access_token",
            "refresh_token",
            "client_secret",
            "secret",
            "password",
            "api_key",
            "authorization",
        }
    )

    def __init__(self, db=None):
        self._db = db or db_manager

    @classmethod
    def redact_sensitive(cls, data: Any) -> Any:
        """Recursively replaces credential material with '[REDACTED]'."""
        if isinstance(data, dict):
            redacted = {}
            for k, v in data.items():
                if any(sens in k.lower() for sens in cls._SENSITIVE_KEYS):
                    redacted[k] = "[REDACTED]"
                else:
                    redacted[k] = cls.redact_sensitive(v)
            return redacted
        elif isinstance(data, list):
            return [cls.redact_sensitive(item) for item in data]
        return data

    async def log_event(
        self,
        event_type: SafetyAuditEventType,
        details: str,
        campaign_id: Optional[str] = None,
        actor_id: Optional[str] = "system",
        target_email: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SafetyAuditEvent:
        """Constructs, redacts, and persists a safety audit event."""
        safe_meta = self.redact_sensitive(metadata or {})
        event = SafetyAuditEvent(
            event_type=event_type,
            campaign_id=campaign_id,
            actor_id=actor_id,
            target_email=target_email,
            details=details,
            metadata=safe_meta,
            timestamp=datetime.now(timezone.utc),
        )

        await self._db.save_safety_audit(event.model_dump(mode="json"))
        logger.info(
            "[SAFETY AUDIT] [%s] campaign=%s target=%s: %s",
            event_type.value,
            campaign_id or "N/A",
            target_email or "N/A",
            details,
        )
        return event

    async def list_events(
        self,
        campaign_id: Optional[str] = None,
        event_type: Optional[SafetyAuditEventType] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[SafetyAuditEvent]:
        docs = await self._db.list_safety_audits(
            campaign_id=campaign_id,
            event_type=event_type.value if event_type else None,
            skip=skip,
            limit=limit,
        )
        return [SafetyAuditEvent(**d) for d in docs]


safety_audit_logger = SafetyAuditLogger()
