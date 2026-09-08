"""
Data models and error types for the Gmail integration.

Two families of model live here and must not be confused:

  * Internal models (OAuthTokenBundle, GmailAccount) carry credential material
    and never leave the backend process except as encrypted ciphertext.
  * API models (GmailConnectionStatus, GmailAuthorizationResponse, ...) are what
    the HTTP layer returns. None of them has a token field, by design.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, EmailStr, Field

# --------------------------------------------------------------------------
# Google endpoints
# --------------------------------------------------------------------------

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
GOOGLE_USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"

#: Minimum scopes needed to send and to read back delivery metadata.
DEFAULT_GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.metadata",
    "openid",
    "email",
]

#: Refresh this many seconds before the access token actually expires.
TOKEN_EXPIRY_SKEW_SECONDS = 120


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class GmailIntegrationError(Exception):
    """Base class for every Gmail integration failure."""

    retryable = False
    error_code = "gmail_error"

    def __init__(self, message: str, *, error_code: Optional[str] = None):
        super().__init__(message)
        self.message = message
        if error_code:
            self.error_code = error_code


class GmailNotConfiguredError(GmailIntegrationError):
    """Google OAuth client credentials or the token encryption key are absent."""

    error_code = "gmail_not_configured"


class GmailNotConnectedError(GmailIntegrationError):
    """No Gmail account has completed the OAuth flow for this account id."""

    error_code = "gmail_not_connected"


class GmailAuthError(GmailIntegrationError):
    """Tokens are missing, expired beyond refresh, revoked or rejected."""

    error_code = "gmail_unauthorized"


class GmailRateLimitError(GmailIntegrationError):
    """Gmail throttled the request."""

    retryable = True
    error_code = "gmail_rate_limited"

    def __init__(self, message: str, retry_after_seconds: Optional[int] = None):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class GmailApiError(GmailIntegrationError):
    """Any other non-2xx response from the Gmail API."""

    error_code = "gmail_api_error"

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        retryable: bool = False,
        error_code: Optional[str] = None,
    ):
        super().__init__(message, error_code=error_code)
        self.status_code = status_code
        self.retryable = retryable


class GmailStateError(GmailIntegrationError):
    """The OAuth `state` parameter was missing, unknown, replayed or expired."""

    error_code = "gmail_invalid_state"


# --------------------------------------------------------------------------
# Internal credential models  (never serialized to an HTTP response)
# --------------------------------------------------------------------------


class OAuthTokenBundle(BaseModel):
    """
    The credential material returned by Google's token endpoint.

    Only ever persisted as Fernet ciphertext, and only ever held in memory for
    the duration of a request.
    """

    access_token: str
    refresh_token: Optional[str] = None
    token_type: str = "Bearer"
    expires_at: Optional[datetime] = None
    scope: str = ""
    id_token: Optional[str] = None

    @classmethod
    def from_token_response(
        cls, payload: Dict[str, Any], previous_refresh_token: Optional[str] = None
    ) -> "OAuthTokenBundle":
        expires_in = payload.get("expires_in")
        expires_at = None
        if expires_in is not None:
            try:
                expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))
            except (TypeError, ValueError):
                expires_at = None

        # Google omits refresh_token on refresh responses; keep the one we hold.
        refresh_token = payload.get("refresh_token") or previous_refresh_token

        return cls(
            access_token=payload.get("access_token") or "",
            refresh_token=refresh_token,
            token_type=payload.get("token_type") or "Bearer",
            expires_at=expires_at,
            scope=payload.get("scope") or "",
            id_token=payload.get("id_token"),
        )

    def is_expired(self, skew_seconds: int = TOKEN_EXPIRY_SKEW_SECONDS) -> bool:
        if self.expires_at is None:
            # Unknown expiry: treat as usable and let a 401 drive the refresh.
            return False
        deadline = self.expires_at - timedelta(seconds=skew_seconds)
        return datetime.now(timezone.utc) >= deadline

    def scope_list(self) -> List[str]:
        return [s for s in (self.scope or "").split() if s]


class GmailAccount(BaseModel):
    """
    A connected mailbox as persisted. `encrypted_tokens` is Fernet ciphertext of
    the JSON-encoded OAuthTokenBundle; the plaintext never touches the database.
    """

    account_id: str
    provider: str = "gmail"
    email_address: Optional[str] = None
    encrypted_tokens: str = ""
    scopes: List[str] = Field(default_factory=list)
    connected_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_refreshed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    needs_reauth: bool = False
    last_error: Optional[str] = None

    @classmethod
    def _utc(cls, dt: Optional[datetime]) -> Optional[datetime]:
        """Return dt with UTC tzinfo; pass-through if already aware or None."""
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    def model_post_init(self, __context: Any) -> None:
        """Normalise all stored datetime fields to UTC-aware after construction."""
        self.connected_at = self._utc(self.connected_at)
        self.updated_at = self._utc(self.updated_at)
        self.last_refreshed_at = self._utc(self.last_refreshed_at)
        self.expires_at = self._utc(self.expires_at)


# --------------------------------------------------------------------------
# API models  (safe to return to the frontend)
# --------------------------------------------------------------------------


class GmailConnectRequest(BaseModel):
    account_id: Optional[str] = None
    login_hint: Optional[EmailStr] = None
    #: Where the callback should bounce the browser once the flow completes.
    redirect_after: Optional[str] = None


class GmailAuthorizationResponse(BaseModel):
    """Returned by POST /connect. Contains no credentials -- only a Google URL."""

    authorization_url: str
    state: str
    account_id: str
    expires_at: datetime
    scopes: List[str]


class GmailConnectionStatus(BaseModel):
    """
    Returned by GET /status.

    Intentionally mirrors integrations.base.ConnectionStatus and, like it, must
    never grow a token field.
    """

    provider: str = "gmail"
    connected: bool = False
    state: str = "DISCONNECTED"
    account_id: Optional[str] = None
    email_address: Optional[str] = None
    scopes: List[str] = Field(default_factory=list)
    connected_at: Optional[datetime] = None
    last_refreshed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    token_expired: bool = False
    needs_reauth: bool = False
    detail: Optional[str] = None


class GmailDisconnectResponse(BaseModel):
    disconnected: bool
    account_id: str
    revoked_at_google: bool = False
    detail: Optional[str] = None


class GmailSendRequest(BaseModel):
    to_email: EmailStr
    subject: str = Field(min_length=1, max_length=998)
    body: str = Field(min_length=1)
    html_body: Optional[str] = None
    cc: List[EmailStr] = Field(default_factory=list)
    bcc: List[EmailStr] = Field(default_factory=list)
    reply_to: Optional[EmailStr] = None
    account_id: Optional[str] = None


class GmailSendResponse(BaseModel):
    """Send outcome. `status` is one of the SendStatus values."""

    status: str
    provider: str = "gmail"
    to_email: str
    subject: str = ""
    message_id: Optional[str] = None
    thread_id: Optional[str] = None
    rfc822_message_id: Optional[str] = None
    label_ids: List[str] = Field(default_factory=list)
    sent_at: Optional[datetime] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    retry_after_seconds: Optional[int] = None
    retryable: bool = False
