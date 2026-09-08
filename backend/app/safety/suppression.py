"""
Suppression list management and unsubscribe compliance.

Implements multi-reason suppression tracking:
- OPT_OUT: recipient explicitly opted out
- UNSUBSCRIBE: recipient used unsubscribe link / header
- DO_NOT_CONTACT: requested no contact / manual block
- BOUNCE_PERMANENT: permanent 5xx delivery failure

Guarantees that once suppressed, no further campaign email is sent to the recipient.
"""

import hmac
import hashlib
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Any, List, Optional, Tuple
from pydantic import BaseModel, Field

from app.database.mongodb import db_manager

logger = logging.getLogger(__name__)

UNSUBSCRIBE_SECRET = "resolve-ai-outreach-safety-secret-key-2026"


class SuppressionReason(str, Enum):
    OPT_OUT = "OPT_OUT"
    UNSUBSCRIBE = "UNSUBSCRIBE"
    DO_NOT_CONTACT = "DO_NOT_CONTACT"
    BOUNCE_PERMANENT = "BOUNCE_PERMANENT"


class SuppressionRecord(BaseModel):
    email: str
    reason: SuppressionReason
    campaign_id: Optional[str] = None
    details: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SuppressionManager:
    """
    Centralized suppression list service.
    Persists suppressions via db_manager and provides fast in-memory query capabilities.
    """

    def __init__(self, db=None, secret: str = UNSUBSCRIBE_SECRET):
        self._db = db or db_manager
        self._secret = secret.encode("utf-8")

    @staticmethod
    def normalize_email(email: str) -> str:
        return (email or "").strip().lower()

    async def add_suppression(
        self,
        email: str,
        reason: SuppressionReason,
        campaign_id: Optional[str] = None,
        details: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SuppressionRecord:
        """Adds an email to the global suppression list."""
        norm_email = self.normalize_email(email)
        if not norm_email or "@" not in norm_email:
            raise ValueError(f"Invalid email '{email}' cannot be added to suppression list.")

        record = SuppressionRecord(
            email=norm_email,
            reason=reason,
            campaign_id=campaign_id,
            details=details,
            created_at=datetime.now(timezone.utc),
            metadata=metadata or {},
        )

        await self._db.save_suppression(record.model_dump(mode="json"))
        logger.info(
            "Suppression recorded for email '%s' with reason '%s' (campaign: %s).",
            norm_email,
            reason.value,
            campaign_id,
        )
        return record

    async def remove_suppression(self, email: str) -> bool:
        """Removes an email from the suppression list (admin manual override)."""
        norm_email = self.normalize_email(email)
        deleted = await self._db.delete_suppression(norm_email)
        if deleted:
            logger.info("Suppression removed for email '%s'.", norm_email)
        return deleted

    async def is_suppressed(self, email: str) -> Tuple[bool, Optional[SuppressionRecord]]:
        """
        Checks if an email is suppressed.
        Returns:
            Tuple[bool, Optional[SuppressionRecord]]: (is_suppressed, record_if_any)
        """
        norm_email = self.normalize_email(email)
        if not norm_email:
            return False, None

        doc = await self._db.get_suppression(norm_email)
        if doc:
            try:
                rec = SuppressionRecord(**doc)
                return True, rec
            except Exception:
                return True, None
        return False, None

    async def filter_suppressed(
        self, emails: List[str]
    ) -> Tuple[List[str], List[Dict[str, Any]]]:
        """
        Partitions an email list into allowed emails and suppressed records.
        """
        allowed: List[str] = []
        suppressed: List[Dict[str, Any]] = []

        for email in emails:
            is_sup, rec = await self.is_suppressed(email)
            if is_sup:
                suppressed.append(
                    rec.model_dump(mode="json")
                    if rec
                    else {"email": self.normalize_email(email), "reason": "UNKNOWN"}
                )
            else:
                allowed.append(email)

        return allowed, suppressed

    async def list_all(self, skip: int = 0, limit: int = 100) -> List[SuppressionRecord]:
        docs = await self._db.list_suppressions(skip=skip, limit=limit)
        return [SuppressionRecord(**d) for d in docs]

    async def count(self) -> int:
        return await self._db.count_suppressions()

    # ----------------------------------------------------------------------
    # Unsubscribe mechanism for outreach campaigns
    # ----------------------------------------------------------------------

    def generate_unsubscribe_token(self, email: str, campaign_id: Optional[str] = None) -> str:
        """Generates a tamper-proof HMAC token for unsubscribe links."""
        norm_email = self.normalize_email(email)
        payload = f"{norm_email}|{campaign_id or ''}"
        sig = hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
        # Format: email_b64-like or plain concatenated
        return f"{norm_email}:{campaign_id or 'global'}:{sig}"

    def verify_unsubscribe_token(self, token: str) -> Optional[Dict[str, str]]:
        """Verifies an unsubscribe token. Returns dict with email & campaign_id if valid."""
        parts = token.split(":")
        if len(parts) != 3:
            return None
        email, camp_id, sig = parts
        norm_email = self.normalize_email(email)
        expected_camp = "" if camp_id == "global" else camp_id
        payload = f"{norm_email}|{expected_camp}"
        expected_sig = hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()[:24]

        if hmac.compare_digest(sig, expected_sig):
            return {"email": norm_email, "campaign_id": expected_camp}
        return None

    async def process_unsubscribe(
        self,
        token_or_email: str,
        reason: SuppressionReason = SuppressionReason.UNSUBSCRIBE,
        details: Optional[str] = None,
    ) -> SuppressionRecord:
        """
        Processes an unsubscribe request either from an unsubscribe token or direct email.
        """
        email = token_or_email
        camp_id = None
        if ":" in token_or_email:
            verified = self.verify_unsubscribe_token(token_or_email)
            if verified:
                email = verified["email"]
                camp_id = verified["campaign_id"] or None
            else:
                raise ValueError("Invalid or tampered unsubscribe token.")

        return await self.add_suppression(
            email=email,
            reason=reason,
            campaign_id=camp_id,
            details=details or "Processed via outreach unsubscribe mechanism.",
        )

    def get_unsubscribe_footer(
        self,
        email: str,
        campaign_id: Optional[str] = None,
        base_url: str = "https://resolve.ai",
    ) -> str:
        """Generates an appropriate, compliant unsubscribe footer for outreach campaigns."""
        token = self.generate_unsubscribe_token(email, campaign_id)
        opt_out_url = f"{base_url.rstrip('/')}/unsubscribe?token={token}"
        return (
            "\n\n---\n"
            "If you do not wish to receive further communications, you can unsubscribe here:\n"
            f"{opt_out_url}\n"
            "Or simply reply with 'unsubscribe' and we will immediately honor your request."
        )

    def get_compliance_headers(
        self,
        email: str,
        campaign_id: Optional[str] = None,
        base_url: str = "https://resolve.ai",
    ) -> Dict[str, str]:
        """Returns standard RFC 2369 / RFC 8058 compliance headers for email outreach."""
        token = self.generate_unsubscribe_token(email, campaign_id)
        opt_out_url = f"{base_url.rstrip('/')}/unsubscribe?token={token}"
        return {
            "List-Unsubscribe": f"<{opt_out_url}>, <mailto:unsubscribe@resolve.ai?subject=unsubscribe_{token}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        }


suppression_manager = SuppressionManager()
