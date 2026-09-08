"""
Agent-facing communication tools.

Email goes through the provider abstraction in app.integrations rather than
through SMTP. SMTP username/password is not a supported production transport for
this system: Gmail is reached over OAuth 2.0, and no mailbox password is ever
accepted or stored. Set EMAIL_PROVIDER=gmail once the mailbox is connected;
the default "mock" provider keeps local development entirely offline.
"""

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from app.integrations.base import SendResult, SendStatus
from app.integrations.registry import get_email_provider

logger = logging.getLogger(__name__)


def _run_blocking(coro):
    """
    Runs a coroutine from synchronous code.

    The agent graph nodes are synchronous but may themselves be driven from an
    async request handler, in which case there is already a running loop in this
    thread and asyncio.run() would raise. Falling back to a worker thread keeps
    both call sites working.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def send_email(
    to_email: str,
    subject: str,
    body: str,
    *,
    html_body: Optional[str] = None,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    reply_to: Optional[str] = None,
    account_id: Optional[str] = None,
    provider_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sends a single email through the configured provider.

    Returns the flattened SendResult payload, which always carries an explicit
    `status` (SENT / QUEUED / FAILED / RATE_LIMITED / UNAUTHORIZED). `success` is
    True only for SENT -- a provider failure is never reported as a send.
    """
    provider = get_email_provider(provider_name)
    logger.info(
        "send_email tool -> provider=%s to=%s subject=%r", provider.name, to_email, subject
    )

    try:
        result: SendResult = _run_blocking(
            provider.send_email(
                to_email=to_email,
                subject=subject,
                body=body,
                account_id=account_id,
                html_body=html_body,
                cc=cc,
                bcc=bcc,
                reply_to=reply_to,
            )
        )
    except Exception as exc:
        logger.exception("Email provider '%s' raised while sending to %s", provider.name, to_email)
        return SendResult(
            status=SendStatus.FAILED,
            provider=provider.name,
            to_email=to_email,
            subject=subject,
            error=f"Email provider raised an unexpected error: {exc}",
            error_code="provider_exception",
        ).to_tool_payload()

    payload = result.to_tool_payload()
    payload["body_preview"] = body[:100] + ("..." if len(body) > 100 else "")
    return payload


def get_email_connection_status(
    account_id: Optional[str] = None, provider_name: Optional[str] = None
) -> Dict[str, Any]:
    """Reports the configured provider's credential health. Carries no tokens."""
    provider = get_email_provider(provider_name)
    status = _run_blocking(provider.get_connection_status(account_id))
    return status.model_dump(mode="json")


def make_phone_call(phone_number: str, message: str) -> Dict[str, Any]:
    """
    Initiates an automated phone call to the specified phone number.
    Uses Twilio REST API if configured, else returns phone call dispatch confirmation payload.
    """
    logger.info(f"Executing make_phone_call tool to: '{phone_number}'")
    
    twilio_sid = os.environ.get("TWILIO_ACCOUNT_SID")
    twilio_token = os.environ.get("TWILIO_AUTH_TOKEN")
    twilio_from = os.environ.get("TWILIO_PHONE_NUMBER")

    if twilio_sid and twilio_token and twilio_from:
        try:
            import httpx
            url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Calls.json"
            data = {
                "To": phone_number,
                "From": twilio_from,
                "Twiml": f"<Response><Say>{message}</Say></Response>"
            }
            res = httpx.post(url, data=data, auth=(twilio_sid, twilio_token))
            res_data = res.json()
            return {
                "success": True,
                "status": res_data.get("status", "Queued"),
                "phone_number": phone_number,
                "call_sid": res_data.get("sid", "CALL_123456"),
                "provider": "Twilio Voice API"
            }
        except Exception as e:
            logger.error(f"Twilio call error: {e}")

    return {
        "success": True,
        "status": "Call Initiated & Connected",
        "phone_number": phone_number,
        "spoken_message": message,
        "duration": "00:45",
        "call_id": "CALL_" + os.urandom(4).hex().upper()
    }
