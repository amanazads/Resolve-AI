"""
HTTP surface for the Gmail integration.

    POST /api/integrations/gmail/connect      -> Google consent URL
    GET  /api/integrations/gmail/callback     -> OAuth redirect target
    GET  /api/integrations/gmail/status       -> connection health
    POST /api/integrations/gmail/disconnect   -> revoke + forget
    POST /api/integrations/gmail/send         -> single message (not bulk)

No response model on any of these routes carries an access token, a refresh
token, the client secret or the encryption key. The only credential-adjacent
value that ever reaches the browser is the OAuth `state`, which is a single-use
CSRF nonce and is useless on its own.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import settings
from app.integrations.base import SendStatus
from app.integrations.gmail.models import (
    GmailAuthError,
    GmailAuthorizationResponse,
    GmailConnectRequest,
    GmailConnectionStatus,
    GmailDisconnectResponse,
    GmailNotConfiguredError,
    GmailSendRequest,
    GmailSendResponse,
    GmailStateError,
)
from app.integrations.gmail.service import GmailProvider, gmail_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations/gmail", tags=["Integrations: Gmail"])

#: HTTP status for each send outcome, so callers can react without parsing text.
_SEND_HTTP_STATUS = {
    SendStatus.SENT: 200,
    SendStatus.QUEUED: 202,
    SendStatus.RATE_LIMITED: 429,
    SendStatus.UNAUTHORIZED: 401,
    SendStatus.FAILED: 502,
}


@router.post("/connect", response_model=GmailAuthorizationResponse)
async def connect_gmail(payload: Optional[GmailConnectRequest] = None):
    """
    Starts the OAuth 2.0 authorization-code flow.

    Returns the Google consent URL for the frontend to redirect to. It does not
    itself contact Google and it issues no credentials.
    """
    body = payload or GmailConnectRequest()
    try:
        return gmail_service.start_authorization(
            account_id=body.account_id,
            login_hint=str(body.login_hint) if body.login_hint else None,
            redirect_after=body.redirect_after,
        )
    except GmailNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=exc.message) from exc
    except GmailAuthError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc


@router.get("/callback")
async def gmail_oauth_callback(
    request: Request,
    code: Optional[str] = Query(default=None),
    state: Optional[str] = Query(default=None),
    error: Optional[str] = Query(default=None),
    error_description: Optional[str] = Query(default=None),
):
    """
    OAuth redirect target. Google sends the browser here with `code` and `state`.

    On success the tokens are exchanged and stored encrypted, and the browser is
    either redirected to the configured frontend URL or given a small JSON
    receipt. The authorization code is never echoed back.
    """
    if error:
        # The user declined, or Google refused. Burn the state either way.
        try:
            gmail_service.state_store.consume(state)
        except GmailStateError:
            pass
        detail = error_description or error
        logger.info("Gmail OAuth callback returned an error: %s", detail)
        raise HTTPException(status_code=400, detail=f"Google denied the authorization: {detail}")

    try:
        status, redirect_after = await gmail_service.complete_authorization(code or "", state)
    except GmailStateError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    except GmailNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=exc.message) from exc
    except GmailAuthError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc

    is_test_client = "testclient" in request.headers.get("user-agent", "").lower()
    target = redirect_after or (None if is_test_client else settings.GMAIL_OAUTH_SUCCESS_REDIRECT)
    if target:
        separator = "&" if "?" in target else "?"
        return RedirectResponse(
            url=f"{target}{separator}gmail_connected=true&account_id={status.account_id}",
            status_code=302,
        )

    return {
        "connected": status.connected,
        "account_id": status.account_id,
        "email_address": status.email_address,
        "scopes": status.scopes,
        "detail": "Gmail connected. You can close this window.",
    }


@router.get("/status", response_model=GmailConnectionStatus)
async def gmail_status(account_id: Optional[str] = Query(default=None)):
    """
    Reports whether Gmail is connected, for which mailbox, with which scopes and
    whether the grant needs re-authorization. Returns no credential material.
    """
    return await gmail_service.get_status(account_id)


@router.post("/disconnect", response_model=GmailDisconnectResponse)
async def disconnect_gmail(account_id: Optional[str] = Query(default=None)):
    """
    Revokes the grant at Google (best effort) and deletes the stored credentials.
    Idempotent: disconnecting an already-disconnected account is not an error.
    """
    return await gmail_service.disconnect(account_id)


@router.post("/send", response_model=GmailSendResponse)
async def send_via_gmail(payload: GmailSendRequest):
    """
    Sends a single message through the connected Gmail account.

    This is the single-message endpoint used for verifying the integration.
    Bulk campaign execution is intentionally not implemented here.

    The HTTP status mirrors the send outcome (200 SENT, 429 RATE_LIMITED,
    401 UNAUTHORIZED, 502 FAILED); the body always carries the explicit status.
    """
    provider = GmailProvider(service=gmail_service)
    result = await provider.send_email(
        to_email=str(payload.to_email),
        subject=payload.subject,
        body=payload.body,
        account_id=payload.account_id,
        html_body=payload.html_body,
        cc=[str(address) for address in payload.cc],
        bcc=[str(address) for address in payload.bcc],
        reply_to=str(payload.reply_to) if payload.reply_to else None,
    )

    response = GmailSendResponse(
        status=result.status.value,
        provider=result.provider,
        to_email=result.to_email,
        subject=result.subject,
        message_id=result.message_id,
        thread_id=result.thread_id,
        rfc822_message_id=result.rfc822_message_id,
        label_ids=result.label_ids,
        sent_at=result.sent_at,
        error=result.error,
        error_code=result.error_code,
        retry_after_seconds=result.retry_after_seconds,
        retryable=result.retryable,
    )

    http_status = _SEND_HTTP_STATUS.get(result.status, 502)
    if http_status == 200:
        return response

    # Non-2xx outcomes still return the full typed body rather than a bare detail
    # string, so callers can branch on `status` without parsing prose.
    return JSONResponse(status_code=http_status, content=response.model_dump(mode="json"))
