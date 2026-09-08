"""
Provider-agnostic email sending abstraction.

Every concrete provider (Gmail, mock, future SES/Postmark) returns the same
explicit status vocabulary so callers never have to guess whether a message
actually left the building.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SendStatus(str, Enum):
    """
    Outcome of a single send attempt.

    SENT is reserved for a confirmed accept by the upstream provider. It is
    never returned optimistically, and never as a fallback for an error.
    """

    QUEUED = "QUEUED"           # Accepted locally, not yet confirmed upstream.
    SENT = "SENT"               # Provider confirmed acceptance (has a message id).
    FAILED = "FAILED"           # Permanent or unclassified failure.
    RATE_LIMITED = "RATE_LIMITED"   # Provider throttled us; retry later.
    UNAUTHORIZED = "UNAUTHORIZED"   # Credentials missing, revoked or expired.


#: Statuses for which a later retry is meaningful.
RETRYABLE_STATUSES = frozenset({SendStatus.QUEUED, SendStatus.RATE_LIMITED})


class ConnectionState(str, Enum):
    """Health of a provider's credentials."""

    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    NEEDS_REAUTH = "NEEDS_REAUTH"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    ERROR = "ERROR"


class SendResult(BaseModel):
    """
    Result of one send attempt.

    Deliberately carries no credential material: it is safe to log, persist and
    return to the frontend as-is.
    """

    status: SendStatus
    provider: str
    to_email: str
    subject: str = ""

    # Provider-side identifiers and delivery metadata, when the provider exposes them.
    message_id: Optional[str] = None
    thread_id: Optional[str] = None
    rfc822_message_id: Optional[str] = None
    label_ids: List[str] = Field(default_factory=list)
    sent_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    # Failure detail.
    error: Optional[str] = None
    error_code: Optional[str] = None
    retry_after_seconds: Optional[int] = None
    attempted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def success(self) -> bool:
        return self.status == SendStatus.SENT

    @property
    def retryable(self) -> bool:
        return self.status in RETRYABLE_STATUSES

    def to_tool_payload(self) -> Dict[str, Any]:
        """Flattened dict shape used by the agent tool layer."""
        payload: Dict[str, Any] = {
            "success": self.success,
            "status": self.status.value,
            "provider": self.provider,
            "to_email": self.to_email,
            "subject": self.subject,
            "retryable": self.retryable,
        }
        if self.message_id:
            payload["message_id"] = self.message_id
        if self.thread_id:
            payload["thread_id"] = self.thread_id
        if self.rfc822_message_id:
            payload["rfc822_message_id"] = self.rfc822_message_id
        if self.label_ids:
            payload["label_ids"] = self.label_ids
        if self.sent_at:
            payload["sent_at"] = self.sent_at.isoformat()
        if self.error:
            payload["error"] = self.error
        if self.error_code:
            payload["error_code"] = self.error_code
        if self.retry_after_seconds is not None:
            payload["retry_after_seconds"] = self.retry_after_seconds
        return payload


class ConnectionStatus(BaseModel):
    """
    Credential health for a provider account.

    NOTE: this model is returned by the public API. It must never gain a field
    that carries an access token, refresh token, client secret or encryption key.
    """

    provider: str
    state: ConnectionState
    connected: bool = False
    account_id: Optional[str] = None
    email_address: Optional[str] = None
    scopes: List[str] = Field(default_factory=list)
    connected_at: Optional[datetime] = None
    last_refreshed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    needs_reauth: bool = False
    detail: Optional[str] = None


class EmailProvider(ABC):
    """Interface every email provider implements."""

    #: Stable machine name, e.g. "gmail" or "mock".
    name: str = "base"

    @abstractmethod
    async def send_email(
        self,
        to_email: str,
        subject: str,
        body: str,
        *,
        account_id: Optional[str] = None,
        html_body: Optional[str] = None,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        reply_to: Optional[str] = None,
    ) -> SendResult:
        """
        Sends a single message.

        Implementations must return a SendResult rather than raising for
        expected provider failures, and must never report SENT unless the
        provider confirmed acceptance.
        """

    @abstractmethod
    async def get_connection_status(
        self, account_id: Optional[str] = None
    ) -> ConnectionStatus:
        """Reports credential health without exposing credential material."""
