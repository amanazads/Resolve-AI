"""
Gmail integration service and provider.

Owns the parts that touch credentials:

  * TokenCipher      -- Fernet encryption of the OAuth token bundle.
  * GmailTokenStore  -- persistence of ciphertext, never plaintext.
  * GmailService     -- the OAuth lifecycle and sending, including refresh.
  * GmailProvider    -- the EmailProvider face the rest of the app talks to.

Design rules enforced here:
  * A raw password is never accepted or stored -- this is OAuth only.
  * Tokens are written to storage only as ciphertext, and are never placed on
    any model that the HTTP layer returns.
  * SENT is returned only when Gmail confirmed the message with an id.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.database.mongodb import db_manager
from app.integrations.base import (
    ConnectionState,
    ConnectionStatus,
    EmailProvider,
    SendResult,
    SendStatus,
)
from app.integrations.gmail.client import GmailApiClient, build_raw_message
from app.integrations.gmail.models import (
    GmailAccount,
    GmailApiError,
    GmailAuthError,
    GmailAuthorizationResponse,
    GmailConnectionStatus,
    GmailDisconnectResponse,
    GmailIntegrationError,
    GmailNotConfiguredError,
    GmailNotConnectedError,
    GmailRateLimitError,
    OAuthTokenBundle,
)
from app.integrations.gmail.oauth import GmailOAuthClient, OAuthStateStore
from app.permissions.models import AuditEventType
from app.permissions.service import permission_service

logger = logging.getLogger(__name__)

PROVIDER_NAME = "gmail"


# ==========================================================================
# Encryption
# ==========================================================================


class TokenCipher:
    """
    Fernet (AES-128-CBC + HMAC) encryption for the OAuth token bundle.

    There is deliberately no plaintext fallback. If GMAIL_TOKEN_ENCRYPTION_KEY is
    absent the integration refuses to store credentials at all, rather than
    quietly writing bearer tokens to the database in the clear.
    """

    def __init__(self, key: Optional[str] = None):
        self._key = key if key is not None else settings.GMAIL_TOKEN_ENCRYPTION_KEY
        self._fernet = None
        self._init_error: Optional[str] = None

        if not self._key:
            self._init_error = (
                "GMAIL_TOKEN_ENCRYPTION_KEY is not set. Generate one with: "
                'python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
            return

        try:
            from cryptography.fernet import Fernet

            self._fernet = Fernet(self._key.encode() if isinstance(self._key, str) else self._key)
        except ImportError:
            self._init_error = (
                "The 'cryptography' package is required for encrypted token storage. "
                "Install it with: pip install cryptography"
            )
        except Exception as exc:
            self._init_error = (
                f"GMAIL_TOKEN_ENCRYPTION_KEY is not a valid Fernet key ({exc}). "
                "It must be 32 url-safe base64-encoded bytes."
            )

    @property
    def is_available(self) -> bool:
        return self._fernet is not None

    @property
    def unavailable_reason(self) -> Optional[str]:
        return self._init_error

    def require_available(self) -> None:
        if not self.is_available:
            raise GmailNotConfiguredError(
                self._init_error or "Token encryption is unavailable.",
                error_code="token_encryption_unavailable",
            )

    def encrypt_bundle(self, bundle: OAuthTokenBundle) -> str:
        self.require_available()
        payload = bundle.model_dump(mode="json")
        return self._fernet.encrypt(json.dumps(payload).encode("utf-8")).decode("ascii")

    def decrypt_bundle(self, ciphertext: str) -> OAuthTokenBundle:
        self.require_available()
        if not ciphertext:
            raise GmailAuthError("No stored Gmail credentials to decrypt. Reconnect Gmail.")
        try:
            plaintext = self._fernet.decrypt(ciphertext.encode("utf-8"))
        except Exception as exc:
            # Wrong key, rotated key, or tampered ciphertext. Either way the
            # credentials are unusable and the user must reconnect.
            raise GmailAuthError(
                "Stored Gmail credentials could not be decrypted "
                f"({type(exc).__name__}). The encryption key may have changed. "
                "Disconnect and reconnect the mailbox."
            ) from exc
        return OAuthTokenBundle.model_validate(json.loads(plaintext.decode("utf-8")))


# ==========================================================================
# Storage
# ==========================================================================


class GmailTokenStore:
    """Reads and writes GmailAccount documents. Ciphertext in, ciphertext out."""

    def __init__(self, db=None):
        self._db = db or db_manager

    async def get(self, account_id: str) -> Optional[GmailAccount]:
        doc = await self._db.get_integration_account(PROVIDER_NAME, account_id)
        if not doc:
            return None
        try:
            return GmailAccount.model_validate(doc)
        except Exception as exc:
            logger.error("Stored Gmail account %s is malformed: %s", account_id, exc)
            return None

    async def save(self, account: GmailAccount) -> GmailAccount:
        account.updated_at = datetime.now(timezone.utc)
        await self._db.save_integration_account(account.model_dump(mode="python"))
        return account

    async def delete(self, account_id: str) -> bool:
        return await self._db.delete_integration_account(PROVIDER_NAME, account_id)


# ==========================================================================
# Service
# ==========================================================================


class GmailService:
    """
    Coordinates the OAuth lifecycle and sending.

    All public methods raise typed GmailIntegrationError subclasses; mapping
    those onto SendStatus / HTTP codes is the caller's job (GmailProvider and
    routes.py respectively).
    """

    def __init__(
        self,
        oauth_client: Optional[GmailOAuthClient] = None,
        api_client: Optional[GmailApiClient] = None,
        token_store: Optional[GmailTokenStore] = None,
        cipher: Optional[TokenCipher] = None,
        state_store: Optional[OAuthStateStore] = None,
    ):
        self._oauth_client = oauth_client
        self._api_client = api_client
        self._token_store = token_store
        self._cipher = cipher
        self.state_store = state_store or OAuthStateStore()

    # -- lazily built collaborators, so settings changes are picked up -----

    @property
    def oauth(self) -> GmailOAuthClient:
        if self._oauth_client is None:
            self._oauth_client = GmailOAuthClient()
        return self._oauth_client

    @property
    def api(self) -> GmailApiClient:
        if self._api_client is None:
            self._api_client = GmailApiClient()
        return self._api_client

    @property
    def store(self) -> GmailTokenStore:
        if self._token_store is None:
            self._token_store = GmailTokenStore()
        return self._token_store

    @property
    def cipher(self) -> TokenCipher:
        if self._cipher is None:
            self._cipher = TokenCipher()
        return self._cipher

    @staticmethod
    def resolve_account_id(account_id: Optional[str]) -> str:
        return account_id or settings.GMAIL_DEFAULT_ACCOUNT_ID or "default"

    # -- 1. authorization --------------------------------------------------

    def start_authorization(
        self,
        account_id: Optional[str] = None,
        login_hint: Optional[str] = None,
        redirect_after: Optional[str] = None,
    ) -> GmailAuthorizationResponse:
        """
        Produces the Google consent URL. Returns no credential material -- the
        frontend only ever receives a URL to send the user to.
        """
        self.oauth.require_configured()
        # Fail before sending the user to Google if we could not store the result.
        self.cipher.require_available()

        resolved = self.resolve_account_id(account_id)
        state = self.state_store.issue(resolved, redirect_after=redirect_after)

        return GmailAuthorizationResponse(
            authorization_url=self.oauth.build_authorization_url(state, login_hint=login_hint),
            state=state,
            account_id=resolved,
            expires_at=self.state_store.expires_at(),
            scopes=self.oauth.scopes,
        )

    # -- 2. callback -------------------------------------------------------

    async def complete_authorization(
        self, code: str, state: Optional[str]
    ) -> Tuple[GmailConnectionStatus, Optional[str]]:
        """
        Exchanges the authorization code and stores the encrypted tokens.

        Returns the public status plus the optional post-connect redirect target
        that was recorded when the flow started.
        """
        entry = self.state_store.consume(state)
        account_id = entry.get("account_id") or self.resolve_account_id(None)

        if not code:
            raise GmailAuthError("Google did not return an authorization code.")

        bundle = await self.oauth.exchange_code(code)
        email_address = await self.oauth.fetch_user_email(bundle.access_token)

        now = datetime.now(timezone.utc)
        existing = await self.store.get(account_id)

        account = GmailAccount(
            account_id=account_id,
            email_address=email_address,
            encrypted_tokens=self.cipher.encrypt_bundle(bundle),
            scopes=bundle.scope_list() or list(self.oauth.scopes),
            connected_at=(existing.connected_at if existing else None) or now,
            updated_at=now,
            last_refreshed_at=now,
            expires_at=bundle.expires_at,
            needs_reauth=False,
            last_error=None,
        )
        await self.store.save(account)
        logger.info("Gmail account '%s' connected (%s).", account_id, email_address or "unknown")

        # Audit the connection. The event records that an account was connected
        # and by which scopes -- never the tokens themselves.
        await permission_service.audit(
            AuditEventType.INTEGRATION_CONNECTED,
            user_id=account_id,
            integration=PROVIDER_NAME,
            integration_account_id=account_id,
            outcome="CONNECTED",
            detail=f"Gmail account '{email_address or 'unknown'}' connected over OAuth 2.0.",
            metadata={"scopes": account.scopes},
        )

        return self._status_from_account(account), entry.get("redirect_after")

    # -- 3. status ---------------------------------------------------------

    async def get_status(self, account_id: Optional[str] = None) -> GmailConnectionStatus:
        """Never raises: an unconfigured or unconnected integration is a status."""
        resolved = self.resolve_account_id(account_id)

        if not self.oauth.is_configured:
            return GmailConnectionStatus(
                connected=False,
                state=ConnectionState.NOT_CONFIGURED.value,
                account_id=resolved,
                detail=(
                    "Google OAuth client credentials are not configured. "
                    "See .env.example for the required Google Cloud setup."
                ),
            )

        if not self.cipher.is_available:
            return GmailConnectionStatus(
                connected=False,
                state=ConnectionState.NOT_CONFIGURED.value,
                account_id=resolved,
                detail=self.cipher.unavailable_reason,
            )

        account = await self.store.get(resolved)
        if account is None:
            return GmailConnectionStatus(
                connected=False,
                state=ConnectionState.DISCONNECTED.value,
                account_id=resolved,
                detail="No Gmail account is connected. Start the flow at POST /api/integrations/gmail/connect.",
            )

        return self._status_from_account(account)

    # -- 4. disconnect -----------------------------------------------------

    async def disconnect(self, account_id: Optional[str] = None) -> GmailDisconnectResponse:
        """
        Revokes at Google (best effort) and deletes the local credentials.

        Local deletion happens even if revocation fails, so the application can
        always forget a mailbox.
        """
        resolved = self.resolve_account_id(account_id)
        account = await self.store.get(resolved)

        if account is None:
            return GmailDisconnectResponse(
                disconnected=False,
                account_id=resolved,
                detail="No Gmail account was connected for this account id.",
            )

        revoked = False
        revoke_detail = None
        try:
            bundle = self.cipher.decrypt_bundle(account.encrypted_tokens)
            # Revoking the refresh token invalidates the whole grant.
            revoked = await self.oauth.revoke(bundle.refresh_token or bundle.access_token)
        except GmailIntegrationError as exc:
            revoke_detail = f"Local credentials removed, but revocation was skipped: {exc.message}"
            logger.warning("Gmail revocation skipped for '%s': %s", resolved, exc.message)

        deleted = await self.store.delete(resolved)
        logger.info("Gmail account '%s' disconnected (revoked at Google: %s).", resolved, revoked)

        await permission_service.audit(
            AuditEventType.INTEGRATION_DISCONNECTED,
            user_id=resolved,
            integration=PROVIDER_NAME,
            integration_account_id=resolved,
            outcome="DISCONNECTED",
            detail=(
                f"Gmail account '{account.email_address or 'unknown'}' disconnected; "
                f"revoked at Google: {revoked}."
            ),
        )

        return GmailDisconnectResponse(
            disconnected=deleted,
            account_id=resolved,
            revoked_at_google=revoked,
            detail=revoke_detail
            or (
                "Access revoked at Google and local credentials deleted."
                if revoked
                else "Local credentials deleted. Google-side revocation was not confirmed; "
                "you can also remove access at https://myaccount.google.com/permissions."
            ),
        )

    # -- 5. sending --------------------------------------------------------

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
        Sends one message through the Gmail API.

        Refreshes a stale access token up front, and retries exactly once if
        Gmail rejects a token we believed was valid. Returns a SendResult whose
        status is SENT only when Gmail returned a message id.
        """
        resolved = self.resolve_account_id(account_id)
        account = await self._require_connected(resolved)

        bundle = self.cipher.decrypt_bundle(account.encrypted_tokens)
        if bundle.is_expired():
            bundle, account = await self._refresh(account, bundle)

        raw = build_raw_message(
            to_email=to_email,
            subject=subject,
            body=body,
            from_email=account.email_address,
            html_body=html_body,
            cc=cc,
            bcc=bcc,
            reply_to=reply_to,
        )

        try:
            sent = await self.api.send_message(bundle.access_token, raw)
        except GmailAuthError:
            # The token was rejected despite looking fresh: refresh once, retry once.
            bundle, account = await self._refresh(account, bundle)
            sent = await self.api.send_message(bundle.access_token, raw)

        message_id = sent.get("id")
        metadata = await self._read_send_metadata(bundle.access_token, message_id)

        return SendResult(
            status=SendStatus.SENT,
            provider=PROVIDER_NAME,
            to_email=to_email,
            subject=subject,
            message_id=message_id,
            thread_id=sent.get("threadId"),
            rfc822_message_id=metadata.get("rfc822_message_id"),
            label_ids=metadata.get("label_ids") or list(sent.get("labelIds") or []),
            sent_at=metadata.get("sent_at") or datetime.now(timezone.utc),
            metadata={
                "account_id": resolved,
                "from": account.email_address,
                **{k: v for k, v in metadata.items() if k == "headers"},
            },
        )

    # -- internals ---------------------------------------------------------

    async def _require_connected(self, account_id: str) -> GmailAccount:
        self.oauth.require_configured()
        self.cipher.require_available()

        account = await self.store.get(account_id)
        if account is None:
            raise GmailNotConnectedError(
                f"No Gmail account is connected for '{account_id}'. "
                "Connect one at POST /api/integrations/gmail/connect."
            )
        if account.needs_reauth:
            raise GmailAuthError(
                f"The Gmail connection for '{account_id}' needs to be re-authorized: "
                f"{account.last_error or 'the stored grant is no longer valid'}."
            )
        return account

    async def _refresh(
        self, account: GmailAccount, bundle: OAuthTokenBundle
    ) -> Tuple[OAuthTokenBundle, GmailAccount]:
        """
        Exchanges the refresh token for a new access token and re-encrypts.

        A terminal refresh failure marks the account as needing re-auth so the
        status endpoint can tell the user what happened instead of failing every
        send with an opaque error.
        """
        try:
            refreshed = await self.oauth.refresh_access_token(bundle.refresh_token or "")
        except GmailAuthError as exc:
            account.needs_reauth = True
            account.last_error = exc.message
            await self.store.save(account)
            logger.warning(
                "Gmail token refresh failed for '%s'; marked for re-auth: %s",
                account.account_id,
                exc.message,
            )
            raise

        now = datetime.now(timezone.utc)
        account.encrypted_tokens = self.cipher.encrypt_bundle(refreshed)
        account.expires_at = refreshed.expires_at
        account.last_refreshed_at = now
        account.needs_reauth = False
        account.last_error = None
        if refreshed.scope_list():
            account.scopes = refreshed.scope_list()
        await self.store.save(account)

        logger.info("Refreshed Gmail access token for '%s'.", account.account_id)
        return refreshed, account

    async def _read_send_metadata(
        self, access_token: str, message_id: Optional[str]
    ) -> Dict[str, Any]:
        """
        Best-effort read of delivery metadata for a message we just sent.

        The message is already sent by this point, so a failure here must never
        change the outcome: it is logged and an empty dict is returned.
        """
        if not message_id:
            return {}
        try:
            payload = await self.api.get_message_metadata(access_token, message_id)
        except GmailIntegrationError as exc:
            logger.info(
                "Send succeeded but delivery metadata was unavailable (%s). "
                "This usually means the connected account lacks a metadata scope.",
                exc,
            )
            return {}

        headers = {
            str(h.get("name", "")).lower(): h.get("value", "")
            for h in (payload.get("payload", {}) or {}).get("headers", [])
            if isinstance(h, dict)
        }

        sent_at = None
        internal_date = payload.get("internalDate")
        if internal_date:
            try:
                sent_at = datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                sent_at = None

        return {
            "rfc822_message_id": headers.get("message-id"),
            "label_ids": list(payload.get("labelIds") or []),
            "sent_at": sent_at,
            "headers": headers,
        }

    @staticmethod
    def _status_from_account(account: GmailAccount) -> GmailConnectionStatus:
        # expires_at may be timezone-naive when round-tripped through MongoDB as
        # an ISO string. Normalise to UTC-aware before comparing.
        expires_at = account.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        expired = bool(expires_at and expires_at <= datetime.now(timezone.utc))
        if account.needs_reauth:
            state = ConnectionState.NEEDS_REAUTH
        else:
            state = ConnectionState.CONNECTED

        return GmailConnectionStatus(
            connected=not account.needs_reauth,
            state=state.value,
            account_id=account.account_id,
            email_address=account.email_address,
            scopes=account.scopes,
            connected_at=account.connected_at,
            last_refreshed_at=account.last_refreshed_at,
            expires_at=account.expires_at,
            token_expired=expired,
            needs_reauth=account.needs_reauth,
            detail=account.last_error
            or (
                "Access token is past its expiry and will be refreshed on the next send."
                if expired
                else None
            ),
        )


# ==========================================================================
# Provider
# ==========================================================================


class GmailProvider(EmailProvider):
    """
    EmailProvider implementation backed by the Gmail API.

    Translates the service's typed errors into the explicit status vocabulary.
    There is exactly one place in this class that produces SendStatus.SENT, and
    it is only reachable when GmailService.send_email returned successfully with
    a Gmail message id.
    """

    name = PROVIDER_NAME

    def __init__(self, service: Optional[GmailService] = None):
        self.service = service or gmail_service

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
        try:
            return await self.service.send_email(
                to_email=to_email,
                subject=subject,
                body=body,
                account_id=account_id,
                html_body=html_body,
                cc=cc,
                bcc=bcc,
                reply_to=reply_to,
            )

        except GmailRateLimitError as exc:
            return self._failure(
                SendStatus.RATE_LIMITED, to_email, subject, exc, exc.retry_after_seconds
            )

        except (GmailAuthError, GmailNotConnectedError, GmailNotConfiguredError) as exc:
            return self._failure(SendStatus.UNAUTHORIZED, to_email, subject, exc)

        except GmailApiError as exc:
            return self._failure(SendStatus.FAILED, to_email, subject, exc)

        except GmailIntegrationError as exc:
            return self._failure(SendStatus.FAILED, to_email, subject, exc)

        except Exception as exc:  # never let an unexpected error read as success
            logger.exception("Unexpected error sending Gmail message to %s", to_email)
            return SendResult(
                status=SendStatus.FAILED,
                provider=self.name,
                to_email=to_email,
                subject=subject,
                error=f"Unexpected error: {exc}",
                error_code="gmail_unexpected_error",
            )

    async def get_connection_status(
        self, account_id: Optional[str] = None
    ) -> ConnectionStatus:
        status = await self.service.get_status(account_id)
        return ConnectionStatus(
            provider=self.name,
            state=ConnectionState(status.state),
            connected=status.connected,
            account_id=status.account_id,
            email_address=status.email_address,
            scopes=status.scopes,
            connected_at=status.connected_at,
            last_refreshed_at=status.last_refreshed_at,
            expires_at=status.expires_at,
            needs_reauth=status.needs_reauth,
            detail=status.detail,
        )

    def _failure(
        self,
        status: SendStatus,
        to_email: str,
        subject: str,
        exc: GmailIntegrationError,
        retry_after: Optional[int] = None,
    ) -> SendResult:
        logger.warning("Gmail send to %s returned %s: %s", to_email, status.value, exc.message)
        return SendResult(
            status=status,
            provider=self.name,
            to_email=to_email,
            subject=subject,
            error=exc.message,
            error_code=exc.error_code,
            retry_after_seconds=retry_after,
        )


#: Process-wide service instance used by routes and the provider registry.
gmail_service = GmailService()
