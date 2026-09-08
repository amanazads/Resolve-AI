"""
Google OAuth 2.0 authorization-code flow for Gmail.

Implemented directly against Google's documented endpoints with httpx (already a
project dependency) rather than pulling in google-api-python-client, which would
add a substantial amount of weight to a 512MB deployment target.

Nothing in this module persists anything: it turns codes into tokens and hands
them to the service layer, which is responsible for encrypting them.
"""

import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.integrations.gmail.models import (
    DEFAULT_GMAIL_SCOPES,
    GOOGLE_AUTH_ENDPOINT,
    GOOGLE_REVOKE_ENDPOINT,
    GOOGLE_TOKEN_ENDPOINT,
    GOOGLE_USERINFO_ENDPOINT,
    GmailAuthError,
    GmailNotConfiguredError,
    GmailStateError,
    OAuthTokenBundle,
)

logger = logging.getLogger(__name__)

#: Google token-endpoint errors that mean the grant is gone for good.
_TERMINAL_GRANT_ERRORS = {
    "invalid_grant",
    "unauthorized_client",
    "invalid_client",
    "access_denied",
}


def configured_scopes() -> List[str]:
    """Scopes from settings, falling back to the documented default set."""
    raw = (settings.GMAIL_SCOPES or "").replace(",", " ").split()
    return raw or list(DEFAULT_GMAIL_SCOPES)


class OAuthStateStore:
    """
    Single-use, expiring CSRF `state` values for the authorization flow.

    Kept in process memory on purpose: a state value is only meaningful for the
    few minutes between redirecting the user to Google and Google redirecting
    them back, and it must not outlive a deploy.
    """

    def __init__(self, ttl_seconds: Optional[int] = None):
        self.ttl_seconds = ttl_seconds or settings.GMAIL_OAUTH_STATE_TTL_SECONDS
        self._states: Dict[str, Dict[str, Any]] = {}

    def issue(self, account_id: str, redirect_after: Optional[str] = None) -> str:
        self._purge_expired()
        state = secrets.token_urlsafe(32)
        self._states[state] = {
            "account_id": account_id,
            "redirect_after": redirect_after,
            "expires_at": time.time() + self.ttl_seconds,
        }
        return state

    def consume(self, state: Optional[str]) -> Dict[str, Any]:
        """
        Validates and burns a state value.

        Raises GmailStateError if the value is absent, unknown, already used or
        expired -- all of which indicate a forged or stale callback.
        """
        self._purge_expired()
        if not state:
            raise GmailStateError("Missing OAuth state parameter.")

        entry = self._states.pop(state, None)
        if entry is None:
            raise GmailStateError("Unknown, expired or already-used OAuth state parameter.")
        if entry["expires_at"] < time.time():
            raise GmailStateError("OAuth state parameter has expired. Restart the connection flow.")
        return entry

    def expires_at(self) -> datetime:
        return datetime.now(timezone.utc) + timedelta(seconds=self.ttl_seconds)

    def clear(self) -> None:
        self._states.clear()

    def _purge_expired(self) -> None:
        now = time.time()
        for key in [k for k, v in self._states.items() if v["expires_at"] < now]:
            self._states.pop(key, None)


class GmailOAuthClient:
    """Thin, testable wrapper around Google's OAuth 2.0 endpoints."""

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        redirect_uri: Optional[str] = None,
        scopes: Optional[List[str]] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        timeout: Optional[float] = None,
    ):
        self.client_id = client_id if client_id is not None else settings.GOOGLE_CLIENT_ID
        self.client_secret = (
            client_secret if client_secret is not None else settings.GOOGLE_CLIENT_SECRET
        )
        self.redirect_uri = (
            redirect_uri if redirect_uri is not None else settings.GOOGLE_OAUTH_REDIRECT_URI
        )
        self.scopes = scopes or configured_scopes()
        self._transport = transport
        self._timeout = timeout or settings.GMAIL_HTTP_TIMEOUT_SECONDS

    # -- configuration -----------------------------------------------------

    @property
    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri)

    def require_configured(self) -> None:
        if not self.is_configured:
            raise GmailNotConfiguredError(
                "Google OAuth is not configured. Set GOOGLE_CLIENT_ID, "
                "GOOGLE_CLIENT_SECRET and GOOGLE_OAUTH_REDIRECT_URI. "
                "See .env.example for the full Google Cloud setup."
            )

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=self._timeout)

    # -- step 1: authorization URL ----------------------------------------

    def build_authorization_url(self, state: str, login_hint: Optional[str] = None) -> str:
        """
        Builds the Google consent URL.

        access_type=offline plus prompt=consent is what makes Google return a
        refresh token; without both, a re-connect yields an access token only and
        the integration cannot survive its first hour.
        """
        self.require_configured()
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(self.scopes),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": state,
        }
        if login_hint:
            params["login_hint"] = login_hint
        return f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"

    # -- step 2: code -> tokens -------------------------------------------

    async def exchange_code(self, code: str) -> OAuthTokenBundle:
        self.require_configured()
        payload = {
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "grant_type": "authorization_code",
        }
        data = await self._post_token(payload, "authorization code exchange")
        bundle = OAuthTokenBundle.from_token_response(data)

        if not bundle.access_token:
            raise GmailAuthError("Google returned no access token for this authorization code.")
        if not bundle.refresh_token:
            # Without a refresh token the connection dies at the first expiry.
            raise GmailAuthError(
                "Google returned no refresh token. Revoke the app's access at "
                "https://myaccount.google.com/permissions and reconnect so that "
                "the consent screen is shown again."
            )
        return bundle

    # -- step 3: refresh ---------------------------------------------------

    async def refresh_access_token(self, refresh_token: str) -> OAuthTokenBundle:
        self.require_configured()
        if not refresh_token:
            raise GmailAuthError("No refresh token stored for this account. Reconnect Gmail.")

        payload = {
            "refresh_token": refresh_token,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "refresh_token",
        }
        data = await self._post_token(payload, "token refresh")
        return OAuthTokenBundle.from_token_response(data, previous_refresh_token=refresh_token)

    # -- teardown ----------------------------------------------------------

    async def revoke(self, token: str) -> bool:
        """
        Best-effort revocation at Google. Returns True when Google confirms.

        A failure here is not fatal: the caller still deletes local credentials,
        so the application forgets the mailbox either way.
        """
        if not token:
            return False
        try:
            async with self._client() as client:
                response = await client.post(
                    GOOGLE_REVOKE_ENDPOINT,
                    data={"token": token},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            if response.status_code == 200:
                return True
            logger.warning(
                "Google token revocation returned %s: %s",
                response.status_code,
                response.text[:200],
            )
            return False
        except httpx.HTTPError as exc:
            logger.warning("Google token revocation failed: %s", exc)
            return False

    # -- identity ----------------------------------------------------------

    async def fetch_user_email(self, access_token: str) -> Optional[str]:
        """
        Resolves the mailbox address for display. Best effort: the integration
        works without it, so a failure is logged and swallowed.
        """
        try:
            async with self._client() as client:
                response = await client.get(
                    GOOGLE_USERINFO_ENDPOINT,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
            if response.status_code == 200:
                return response.json().get("email")
            logger.info(
                "Could not resolve Google account email (%s). Continuing without it.",
                response.status_code,
            )
        except (httpx.HTTPError, ValueError) as exc:
            logger.info("Could not resolve Google account email: %s", exc)
        return None

    # -- internals ---------------------------------------------------------

    async def _post_token(self, payload: Dict[str, str], operation: str) -> Dict[str, Any]:
        try:
            async with self._client() as client:
                response = await client.post(
                    GOOGLE_TOKEN_ENDPOINT,
                    data=payload,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
        except httpx.HTTPError as exc:
            # Network-level problem: not an auth failure, and worth retrying.
            raise GmailAuthError(
                f"Could not reach Google's token endpoint during {operation}: {exc}"
            ) from exc

        if response.status_code == 200:
            try:
                return response.json()
            except ValueError as exc:
                raise GmailAuthError(
                    f"Google returned a malformed token response during {operation}."
                ) from exc

        detail = self._error_detail(response)
        error_key = detail.get("error", "")
        message = (
            f"Google rejected the {operation} "
            f"({response.status_code} {error_key or 'error'}): "
            f"{detail.get('error_description') or response.text[:200]}"
        )

        if error_key in _TERMINAL_GRANT_ERRORS or response.status_code in (400, 401):
            raise GmailAuthError(message, error_code=f"google_{error_key or 'auth_error'}")
        raise GmailAuthError(message)

    @staticmethod
    def _error_detail(response: httpx.Response) -> Dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            return {}
        return body if isinstance(body, dict) else {}
