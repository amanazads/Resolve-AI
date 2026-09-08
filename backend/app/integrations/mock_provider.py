"""
In-process email provider for local development and tests.

Records every message it is given so tests can assert on what would have been
sent, and can be told to simulate any of the real failure modes.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.integrations.base import (
    ConnectionState,
    ConnectionStatus,
    EmailProvider,
    SendResult,
    SendStatus,
)

logger = logging.getLogger(__name__)


class MockEmailProvider(EmailProvider):
    """
    Never touches the network. Returns SENT by default; set force_status to
    rehearse RATE_LIMITED / UNAUTHORIZED / FAILED handling downstream.
    """

    name = "mock"

    def __init__(
        self,
        force_status: SendStatus = SendStatus.SENT,
        connected: bool = True,
        email_address: str = "dev-mailbox@localhost",
    ):
        self.force_status = force_status
        self.connected = connected
        self.email_address = email_address
        self.outbox: List[Dict[str, Any]] = []

    def reset(self) -> None:
        self.outbox.clear()

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
        record = {
            "to_email": to_email,
            "subject": subject,
            "body": body,
            "html_body": html_body,
            "cc": cc or [],
            "bcc": bcc or [],
            "reply_to": reply_to,
            "account_id": account_id,
            "queued_at": datetime.now(timezone.utc),
        }
        self.outbox.append(record)
        logger.info("[mock provider] captured message to %s (%s)", to_email, subject)

        if self.force_status != SendStatus.SENT:
            return SendResult(
                status=self.force_status,
                provider=self.name,
                to_email=to_email,
                subject=subject,
                error=f"Mock provider configured to return {self.force_status.value}.",
                error_code="mock_forced_status",
            )

        message_id = f"mock-{uuid.uuid4().hex[:16]}"
        return SendResult(
            status=SendStatus.SENT,
            provider=self.name,
            to_email=to_email,
            subject=subject,
            message_id=message_id,
            thread_id=message_id,
            rfc822_message_id=f"<{message_id}@localhost>",
            label_ids=["SENT"],
            sent_at=datetime.now(timezone.utc),
            metadata={"mock": True, "outbox_index": len(self.outbox) - 1},
        )

    async def get_connection_status(
        self, account_id: Optional[str] = None
    ) -> ConnectionStatus:
        return ConnectionStatus(
            provider=self.name,
            state=ConnectionState.CONNECTED if self.connected else ConnectionState.DISCONNECTED,
            connected=self.connected,
            account_id=account_id or "mock-account",
            email_address=self.email_address if self.connected else None,
            scopes=["mock.send"] if self.connected else [],
            detail="Local development provider. No message leaves this process.",
        )
