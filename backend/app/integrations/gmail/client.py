"""
Gmail REST API client.

Responsible for one thing: turning an access token plus a message into an HTTP
call, and turning Gmail's response into either data or a typed error. It holds
no credentials of its own and performs no refresh -- the service layer owns that.
"""

import base64
import logging
from email.message import EmailMessage
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings
from app.integrations.gmail.models import (
    GMAIL_API_BASE,
    GmailApiError,
    GmailAuthError,
    GmailRateLimitError,
)

logger = logging.getLogger(__name__)

#: Gmail reports quota problems as 403 with one of these reasons, not only as 429.
_RATE_LIMIT_REASONS = {
    "rateLimitExceeded",
    "userRateLimitExceeded",
    "quotaExceeded",
    "dailyLimitExceeded",
    "backendError",
}

#: Metadata headers worth pulling back after a send.
_METADATA_HEADERS = ["Message-ID", "Date", "To", "From", "Subject"]


def build_raw_message(
    to_email: str,
    subject: str,
    body: str,
    *,
    from_email: Optional[str] = None,
    html_body: Optional[str] = None,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    reply_to: Optional[str] = None,
) -> str:
    """
    Builds an RFC 2822 message and base64url-encodes it as the Gmail API's
    `raw` field expects (URL-safe alphabet, which is not the same as standard
    base64 -- using the wrong one produces a silent 400).
    """
    message = EmailMessage()
    message["To"] = to_email
    message["Subject"] = subject
    if from_email:
        message["From"] = from_email
    if cc:
        message["Cc"] = ", ".join(cc)
    if bcc:
        message["Bcc"] = ", ".join(bcc)
    if reply_to:
        message["Reply-To"] = reply_to

    message.set_content(body)
    if html_body:
        message.add_alternative(html_body, subtype="html")

    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")


class GmailApiClient:
    """Async Gmail API wrapper. `transport` exists so tests can stub the network."""

    def __init__(
        self,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        timeout: Optional[float] = None,
        base_url: str = GMAIL_API_BASE,
    ):
        self._transport = transport
        self._timeout = timeout or settings.GMAIL_HTTP_TIMEOUT_SECONDS
        self.base_url = base_url.rstrip("/")

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=self._timeout)

    # -- operations --------------------------------------------------------

    async def send_message(self, access_token: str, raw_message: str) -> Dict[str, Any]:
        """
        POSTs a message to Gmail.

        Returns Gmail's response body ({id, threadId, labelIds}) on success and
        raises a typed error otherwise. It never returns a partial success.
        """
        response = await self._request(
            "POST",
            f"{self.base_url}/users/me/messages/send",
            access_token,
            json={"raw": raw_message},
        )
        payload = self._json(response)
        if not payload.get("id"):
            raise GmailApiError(
                "Gmail accepted the request but returned no message id; "
                "treating the send as failed.",
                status_code=response.status_code,
            )
        return payload

    async def get_message_metadata(
        self, access_token: str, message_id: str
    ) -> Dict[str, Any]:
        """
        Reads back labels and headers for a sent message.

        Requires a metadata-capable scope; a scope-related refusal is surfaced to
        the caller, which treats missing metadata as non-fatal.
        """
        params: List[tuple] = [("format", "metadata")]
        params += [("metadataHeaders", header) for header in _METADATA_HEADERS]

        response = await self._request(
            "GET",
            f"{self.base_url}/users/me/messages/{message_id}",
            access_token,
            params=params,
        )
        return self._json(response)

    async def get_profile(self, access_token: str) -> Dict[str, Any]:
        response = await self._request(
            "GET", f"{self.base_url}/users/me/profile", access_token
        )
        return self._json(response)

    # -- internals ---------------------------------------------------------

    async def _request(
        self,
        method: str,
        url: str,
        access_token: str,
        **kwargs: Any,
    ) -> httpx.Response:
        if not access_token:
            raise GmailAuthError("No Gmail access token available for this request.")

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }
        try:
            async with self._client() as client:
                response = await client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            # The request never reached Gmail, so the message was definitely not sent.
            raise GmailApiError(
                f"Could not reach the Gmail API: {exc}",
                retryable=True,
                error_code="gmail_network_error",
            ) from exc

        if 200 <= response.status_code < 300:
            return response

        self._raise_for_status(response)
        raise GmailApiError(  # pragma: no cover - _raise_for_status always raises
            f"Unhandled Gmail API response: {response.status_code}",
            status_code=response.status_code,
        )

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        detail = self._error_detail(response)
        reason = detail.get("reason", "")
        message = detail.get("message") or response.text[:300]

        if status == 401:
            raise GmailAuthError(
                f"Gmail rejected the access token (401): {message}",
                error_code="gmail_token_rejected",
            )

        if status == 429 or (status == 403 and reason in _RATE_LIMIT_REASONS):
            raise GmailRateLimitError(
                f"Gmail rate limit reached ({status} {reason or 'rateLimitExceeded'}): {message}",
                retry_after_seconds=self._retry_after(response),
            )

        if status == 403:
            # A 403 that is not a quota problem is a scope/permission problem,
            # which needs a re-consent rather than a retry.
            raise GmailAuthError(
                f"Gmail refused the request (403 {reason or 'forbidden'}): {message}. "
                "The connected account may be missing a required scope.",
                error_code="gmail_insufficient_permissions",
            )

        raise GmailApiError(
            f"Gmail API error ({status}): {message}",
            status_code=status,
            retryable=status >= 500,
        )

    @staticmethod
    def _retry_after(response: httpx.Response) -> Optional[int]:
        raw = response.headers.get("Retry-After")
        if not raw:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _error_detail(response: httpx.Response) -> Dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            return {}
        if not isinstance(body, dict):
            return {}
        error = body.get("error")
        if isinstance(error, dict):
            errors = error.get("errors")
            reason = ""
            if isinstance(errors, list) and errors and isinstance(errors[0], dict):
                reason = errors[0].get("reason", "")
            return {
                "message": error.get("message", ""),
                "status": error.get("status", ""),
                "reason": reason or error.get("status", ""),
            }
        return {"message": str(error) if error else ""}

    @staticmethod
    def _json(response: httpx.Response) -> Dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise GmailApiError(
                "Gmail returned a non-JSON response.", status_code=response.status_code
            ) from exc
        return payload if isinstance(payload, dict) else {}
