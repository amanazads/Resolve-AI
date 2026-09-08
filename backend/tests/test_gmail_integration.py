"""
Gmail integration tests.

Every Google and Gmail endpoint is served by an httpx MockTransport, so the
suite exercises the real request construction, response parsing and error
mapping without touching the network or needing real credentials.
"""

import base64
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import pytest

# Ensure backend is on sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.database.mongodb import db_manager
from app.integrations.base import ConnectionState, SendStatus
from app.integrations.mock_provider import MockEmailProvider
from app.integrations.registry import (
    get_email_provider,
    reset_provider_cache,
    set_email_provider,
)
from app.integrations.gmail.client import GmailApiClient, build_raw_message
from app.integrations.gmail.models import (
    GmailAuthError,
    GmailNotConfiguredError,
    GmailNotConnectedError,
    GmailStateError,
    OAuthTokenBundle,
)
from app.integrations.gmail.oauth import GmailOAuthClient, OAuthStateStore
from app.integrations.gmail.service import (
    GmailProvider,
    GmailService,
    GmailTokenStore,
    TokenCipher,
)

FERNET_KEY = "eZ8Yq3nP5cJk1mVx7bR2tW9sL4dG6hA0uZ3fN8pQyXc="  # test-only key
TEST_ACCOUNT = "test-account"


# =========================================================================
# Fixtures and fakes
# =========================================================================


@pytest.fixture(autouse=True)
def clean_state():
    """Isolate the in-memory integration store and the provider registry."""
    db_manager._memory_integrations.clear()
    reset_provider_cache()
    set_email_provider(None)
    yield
    db_manager._memory_integrations.clear()
    reset_provider_cache()
    set_email_provider(None)


class FakeGoogle:
    """
    Scriptable stand-in for Google's OAuth endpoints and the Gmail API.

    Each queue holds the responses to serve, in order; the last entry repeats
    once the queue is exhausted so a test only scripts what it cares about.
    """

    def __init__(self):
        self.token_responses: List[httpx.Response] = []
        self.send_responses: List[httpx.Response] = []
        self.metadata_responses: List[httpx.Response] = []
        self.userinfo_response = httpx.Response(200, json={"email": "founder@example.com"})
        self.revoke_response = httpx.Response(200)
        self.requests: List[httpx.Request] = []

    # -- scripting helpers -------------------------------------------------

    def token(self, payload: Dict[str, Any], status: int = 200) -> "FakeGoogle":
        self.token_responses.append(httpx.Response(status, json=payload))
        return self

    def send_ok(self, message_id: str = "msg-1", thread_id: str = "thr-1") -> "FakeGoogle":
        self.send_responses.append(
            httpx.Response(200, json={"id": message_id, "threadId": thread_id, "labelIds": ["SENT"]})
        )
        return self

    def send_error(
        self,
        status: int,
        reason: str = "",
        message: str = "boom",
        headers: Optional[Dict[str, str]] = None,
    ) -> "FakeGoogle":
        body = {
            "error": {
                "code": status,
                "message": message,
                "errors": [{"reason": reason}] if reason else [],
            }
        }
        self.send_responses.append(httpx.Response(status, json=body, headers=headers or {}))
        return self

    def metadata(self, payload: Dict[str, Any], status: int = 200) -> "FakeGoogle":
        self.metadata_responses.append(httpx.Response(status, json=payload))
        return self

    # -- transport ---------------------------------------------------------

    @staticmethod
    def _next(queue: List[httpx.Response], default: httpx.Response) -> httpx.Response:
        if not queue:
            return default
        response = queue[0] if len(queue) == 1 else queue.pop(0)
        # Responses are reused across calls; hand back a fresh copy each time.
        return httpx.Response(
            response.status_code, content=response.content, headers=response.headers
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)

        if "oauth2.googleapis.com/token" in url:
            return self._next(
                self.token_responses,
                httpx.Response(400, json={"error": "invalid_request"}),
            )
        if "oauth2.googleapis.com/revoke" in url:
            return httpx.Response(self.revoke_response.status_code)
        if "userinfo" in url:
            return httpx.Response(
                self.userinfo_response.status_code, content=self.userinfo_response.content
            )
        if "/messages/send" in url:
            return self._next(
                self.send_responses, httpx.Response(500, json={"error": {"message": "unscripted"}})
            )
        if "/messages/" in url:
            return self._next(self.metadata_responses, httpx.Response(403, json={}))
        if "/profile" in url:
            return httpx.Response(200, json={"emailAddress": "founder@example.com"})

        return httpx.Response(404, json={"error": {"message": f"unrouted: {url}"}})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def token_grants(self) -> List[str]:
        """grant_type of every token-endpoint call, in order."""
        grants = []
        for request in self.requests:
            if "oauth2.googleapis.com/token" in str(request.url):
                body = request.content.decode()
                for pair in body.split("&"):
                    if pair.startswith("grant_type="):
                        grants.append(pair.split("=", 1)[1])
        return grants


def token_payload(access_token: str = "access-1", expires_in: int = 3600, **extra) -> Dict[str, Any]:
    payload = {
        "access_token": access_token,
        "refresh_token": "refresh-1",
        "token_type": "Bearer",
        "expires_in": expires_in,
        "scope": "https://www.googleapis.com/auth/gmail.send https://www.googleapis.com/auth/gmail.metadata",
    }
    payload.update(extra)
    return payload


def build_service(google: FakeGoogle, key: str = FERNET_KEY) -> GmailService:
    """A GmailService wired to the fake transport and a test encryption key."""
    return GmailService(
        oauth_client=GmailOAuthClient(
            client_id="test-client-id",
            client_secret="test-client-secret",
            redirect_uri="http://localhost:8000/api/integrations/gmail/callback",
            transport=google.transport,
        ),
        api_client=GmailApiClient(transport=google.transport),
        token_store=GmailTokenStore(),
        cipher=TokenCipher(key),
        state_store=OAuthStateStore(ttl_seconds=600),
    )


async def connect(service: GmailService, account_id: str = TEST_ACCOUNT):
    """Drives a full authorize -> callback handshake."""
    auth = service.start_authorization(account_id=account_id)
    status, _ = await service.complete_authorization("auth-code-123", auth.state)
    return auth, status


# =========================================================================
# 1. Token encryption
# =========================================================================


def test_token_cipher_roundtrip_and_ciphertext_hides_tokens():
    cipher = TokenCipher(FERNET_KEY)
    assert cipher.is_available

    bundle = OAuthTokenBundle(
        access_token="ya29.super-secret-access",
        refresh_token="1//super-secret-refresh",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        scope="https://www.googleapis.com/auth/gmail.send",
    )
    ciphertext = cipher.encrypt_bundle(bundle)

    assert "ya29.super-secret-access" not in ciphertext
    assert "1//super-secret-refresh" not in ciphertext

    restored = cipher.decrypt_bundle(ciphertext)
    assert restored.access_token == bundle.access_token
    assert restored.refresh_token == bundle.refresh_token


def test_token_cipher_refuses_to_operate_without_a_key():
    """No key must mean no storage -- never a silent plaintext fallback."""
    cipher = TokenCipher("")
    assert cipher.is_available is False
    assert "GMAIL_TOKEN_ENCRYPTION_KEY" in (cipher.unavailable_reason or "")

    with pytest.raises(GmailNotConfiguredError):
        cipher.encrypt_bundle(OAuthTokenBundle(access_token="a"))


def test_token_cipher_rejects_ciphertext_from_a_different_key():
    original = TokenCipher(FERNET_KEY).encrypt_bundle(OAuthTokenBundle(access_token="a"))
    other_key = "pBem2yTPfWoNOF97RLXkicflN6RwVjtte0v_5uuA3kc="

    with pytest.raises(GmailAuthError):
        TokenCipher(other_key).decrypt_bundle(original)


# =========================================================================
# 2. Authorization URL and CSRF state
# =========================================================================


def test_authorization_url_requests_offline_access_and_carries_state():
    google = FakeGoogle()
    service = build_service(google)

    auth = service.start_authorization(account_id=TEST_ACCOUNT, login_hint="founder@example.com")

    assert auth.authorization_url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    # Offline + consent is what makes Google issue a refresh token.
    assert "access_type=offline" in auth.authorization_url
    assert "prompt=consent" in auth.authorization_url
    assert "response_type=code" in auth.authorization_url
    assert "gmail.send" in auth.authorization_url
    assert f"state={auth.state}" in auth.authorization_url
    assert "login_hint=founder%40example.com" in auth.authorization_url

    # The client secret must never appear in anything the browser receives.
    assert "test-client-secret" not in auth.authorization_url


def test_start_authorization_requires_configuration():
    google = FakeGoogle()
    unconfigured = GmailService(
        oauth_client=GmailOAuthClient(client_id="", client_secret="", redirect_uri=""),
        api_client=GmailApiClient(transport=google.transport),
        cipher=TokenCipher(FERNET_KEY),
    )
    with pytest.raises(GmailNotConfiguredError):
        unconfigured.start_authorization()


def test_start_authorization_requires_an_encryption_key():
    """Refuse before sending the user to Google if the result cannot be stored."""
    google = FakeGoogle()
    service = build_service(google, key="")
    with pytest.raises(GmailNotConfiguredError):
        service.start_authorization()


def test_oauth_state_is_single_use_and_rejects_forgeries():
    store = OAuthStateStore(ttl_seconds=600)
    state = store.issue(TEST_ACCOUNT)

    assert store.consume(state)["account_id"] == TEST_ACCOUNT

    with pytest.raises(GmailStateError):
        store.consume(state)          # replay
    with pytest.raises(GmailStateError):
        store.consume("forged-state") # never issued
    with pytest.raises(GmailStateError):
        store.consume(None)           # absent


@pytest.mark.asyncio
async def test_callback_with_bad_state_is_rejected():
    google = FakeGoogle().token(token_payload())
    service = build_service(google)
    service.start_authorization(account_id=TEST_ACCOUNT)

    with pytest.raises(GmailStateError):
        await service.complete_authorization("code", "not-the-issued-state")


# =========================================================================
# 3. Callback, secure storage, status
# =========================================================================


@pytest.mark.asyncio
async def test_callback_stores_tokens_encrypted_and_never_in_plaintext():
    google = FakeGoogle().token(token_payload(access_token="ya29.plain-access"))
    service = build_service(google)

    _, status = await connect(service)

    assert status.connected is True
    assert status.email_address == "founder@example.com"

    stored = db_manager._memory_integrations["gmail:test-account"]
    serialized = json.dumps(stored, default=str)
    assert "ya29.plain-access" not in serialized
    assert "refresh-1" not in serialized
    assert stored["encrypted_tokens"]

    # And the ciphertext really does decrypt back to the tokens.
    account = await service.store.get(TEST_ACCOUNT)
    bundle = service.cipher.decrypt_bundle(account.encrypted_tokens)
    assert bundle.access_token == "ya29.plain-access"
    assert bundle.refresh_token == "refresh-1"


@pytest.mark.asyncio
async def test_callback_requires_a_refresh_token():
    """An access-token-only grant cannot survive an hour, so refuse it up front."""
    payload = token_payload()
    payload.pop("refresh_token")
    google = FakeGoogle().token(payload)
    service = build_service(google)

    auth = service.start_authorization(account_id=TEST_ACCOUNT)
    with pytest.raises(GmailAuthError, match="refresh token"):
        await service.complete_authorization("code", auth.state)


@pytest.mark.asyncio
async def test_status_reports_disconnected_then_connected_without_leaking_tokens():
    google = FakeGoogle().token(token_payload())
    service = build_service(google)

    before = await service.get_status(TEST_ACCOUNT)
    assert before.connected is False
    assert before.state == ConnectionState.DISCONNECTED.value

    await connect(service)

    after = await service.get_status(TEST_ACCOUNT)
    assert after.connected is True
    assert after.state == ConnectionState.CONNECTED.value
    assert after.email_address == "founder@example.com"
    assert "https://www.googleapis.com/auth/gmail.send" in after.scopes

    # The status model is returned verbatim by the API: it must be token-free.
    serialized = after.model_dump()
    for forbidden in ("access_token", "refresh_token", "encrypted_tokens", "id_token"):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_status_reports_not_configured_without_credentials():
    google = FakeGoogle()
    service = GmailService(
        oauth_client=GmailOAuthClient(client_id="", client_secret="", redirect_uri=""),
        api_client=GmailApiClient(transport=google.transport),
        cipher=TokenCipher(FERNET_KEY),
    )
    status = await service.get_status()
    assert status.connected is False
    assert status.state == ConnectionState.NOT_CONFIGURED.value


# =========================================================================
# 4. Disconnect
# =========================================================================


@pytest.mark.asyncio
async def test_disconnect_revokes_at_google_and_deletes_local_credentials():
    google = FakeGoogle().token(token_payload())
    service = build_service(google)
    await connect(service)

    result = await service.disconnect(TEST_ACCOUNT)

    assert result.disconnected is True
    assert result.revoked_at_google is True
    assert "gmail:test-account" not in db_manager._memory_integrations
    assert any("revoke" in str(r.url) for r in google.requests)

    status = await service.get_status(TEST_ACCOUNT)
    assert status.connected is False


@pytest.mark.asyncio
async def test_disconnect_deletes_locally_even_if_google_revocation_fails():
    google = FakeGoogle().token(token_payload())
    google.revoke_response = httpx.Response(400)
    service = build_service(google)
    await connect(service)

    result = await service.disconnect(TEST_ACCOUNT)

    assert result.disconnected is True
    assert result.revoked_at_google is False
    assert "gmail:test-account" not in db_manager._memory_integrations


@pytest.mark.asyncio
async def test_disconnect_is_idempotent():
    google = FakeGoogle()
    service = build_service(google)
    result = await service.disconnect("never-connected")
    assert result.disconnected is False


# =========================================================================
# 5. Sending: the happy path and its metadata
# =========================================================================


def test_raw_message_is_base64url_and_wellformed():
    raw = build_raw_message(
        "to@example.com", "Subject line", "Body text", from_email="me@example.com"
    )
    decoded = base64.urlsafe_b64decode(raw.encode()).decode()

    assert "To: to@example.com" in decoded
    assert "Subject: Subject line" in decoded
    assert "From: me@example.com" in decoded
    assert "Body text" in decoded
    # URL-safe alphabet only: standard base64 padding chars would break Gmail.
    assert "+" not in raw and "/" not in raw


@pytest.mark.asyncio
async def test_send_returns_sent_with_gmail_ids_and_delivery_metadata():
    google = FakeGoogle().token(token_payload()).send_ok("m-100", "t-200")
    google.metadata(
        {
            "id": "m-100",
            "threadId": "t-200",
            "labelIds": ["SENT", "IMPORTANT"],
            "internalDate": "1757000000000",
            "payload": {
                "headers": [
                    {"name": "Message-ID", "value": "<abc123@mail.gmail.com>"},
                    {"name": "To", "value": "lead@example.com"},
                    {"name": "Subject", "value": "Hello"},
                ]
            },
        }
    )
    service = build_service(google)
    await connect(service)

    result = await service.send_email("lead@example.com", "Hello", "Body", account_id=TEST_ACCOUNT)

    assert result.status == SendStatus.SENT
    assert result.message_id == "m-100"
    assert result.thread_id == "t-200"
    assert result.rfc822_message_id == "<abc123@mail.gmail.com>"
    assert result.label_ids == ["SENT", "IMPORTANT"]
    assert result.sent_at is not None
    assert result.success is True


@pytest.mark.asyncio
async def test_send_still_succeeds_when_metadata_is_unavailable():
    """A metadata scope refusal must not turn a delivered message into a failure."""
    google = FakeGoogle().token(token_payload()).send_ok("m-101")
    google.metadata({"error": {"code": 403, "message": "Metadata scope missing"}}, status=403)
    service = build_service(google)
    await connect(service)

    result = await service.send_email("lead@example.com", "Hello", "Body", account_id=TEST_ACCOUNT)

    assert result.status == SendStatus.SENT
    assert result.message_id == "m-101"
    assert result.rfc822_message_id is None
    assert result.label_ids == ["SENT"]  # falls back to the send response


@pytest.mark.asyncio
async def test_send_without_a_message_id_is_not_reported_as_sent():
    google = FakeGoogle().token(token_payload())
    google.send_responses.append(httpx.Response(200, json={"threadId": "t-1"}))
    service = build_service(google)
    await connect(service)

    provider = GmailProvider(service=service)
    result = await provider.send_email("lead@example.com", "Hello", "Body", account_id=TEST_ACCOUNT)

    assert result.status == SendStatus.FAILED
    assert result.success is False


# =========================================================================
# 6. Token refresh
# =========================================================================


@pytest.mark.asyncio
async def test_expired_access_token_is_refreshed_before_sending():
    google = FakeGoogle()
    google.token(token_payload(access_token="access-old", expires_in=3600))
    service = build_service(google)
    await connect(service)

    # Age the stored token past its expiry.
    account = await service.store.get(TEST_ACCOUNT)
    bundle = service.cipher.decrypt_bundle(account.encrypted_tokens)
    bundle.expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    account.encrypted_tokens = service.cipher.encrypt_bundle(bundle)
    await service.store.save(account)

    google.token_responses.clear()
    google.token(token_payload(access_token="access-new"))
    google.send_ok("m-refreshed").metadata({"labelIds": ["SENT"], "payload": {"headers": []}})

    result = await service.send_email("lead@example.com", "Hello", "Body", account_id=TEST_ACCOUNT)

    assert result.status == SendStatus.SENT
    assert "refresh_token" in google.token_grants()

    # The new access token was persisted, still encrypted.
    refreshed = service.cipher.decrypt_bundle(
        (await service.store.get(TEST_ACCOUNT)).encrypted_tokens
    )
    assert refreshed.access_token == "access-new"
    assert refreshed.refresh_token == "refresh-1"  # carried over, Google omits it


@pytest.mark.asyncio
async def test_rejected_token_triggers_one_refresh_and_retry():
    google = FakeGoogle().token(token_payload(access_token="access-old"))
    service = build_service(google)
    await connect(service)

    google.token_responses.clear()
    google.token(token_payload(access_token="access-new"))
    google.send_error(401, message="Invalid Credentials")
    google.send_ok("m-retried")
    google.metadata({"labelIds": ["SENT"], "payload": {"headers": []}})

    result = await service.send_email("lead@example.com", "Hello", "Body", account_id=TEST_ACCOUNT)

    assert result.status == SendStatus.SENT
    assert result.message_id == "m-retried"


@pytest.mark.asyncio
async def test_terminal_refresh_failure_marks_the_account_for_reauth():
    google = FakeGoogle().token(token_payload(access_token="access-old"))
    service = build_service(google)
    await connect(service)

    google.token_responses.clear()
    google.token({"error": "invalid_grant", "error_description": "Token has been revoked."}, status=400)
    google.send_error(401, message="Invalid Credentials")

    provider = GmailProvider(service=service)
    result = await provider.send_email("lead@example.com", "Hello", "Body", account_id=TEST_ACCOUNT)

    assert result.status == SendStatus.UNAUTHORIZED
    assert result.success is False

    status = await service.get_status(TEST_ACCOUNT)
    assert status.needs_reauth is True
    assert status.connected is False
    assert status.state == ConnectionState.NEEDS_REAUTH.value


# =========================================================================
# 7. Failure mapping: never SENT when Gmail said no
# =========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status_code,reason,headers,expected",
    [
        (401, "", None, SendStatus.UNAUTHORIZED),
        (403, "insufficientPermissions", None, SendStatus.UNAUTHORIZED),
        (403, "rateLimitExceeded", None, SendStatus.RATE_LIMITED),
        (429, "", {"Retry-After": "30"}, SendStatus.RATE_LIMITED),
        (400, "invalidArgument", None, SendStatus.FAILED),
        (500, "", None, SendStatus.FAILED),
        (503, "", None, SendStatus.FAILED),
    ],
)
async def test_gmail_errors_map_to_explicit_statuses(status_code, reason, headers, expected):
    google = FakeGoogle().token(token_payload())
    service = build_service(google)
    await connect(service)

    google.send_error(status_code, reason=reason, headers=headers)
    # A 401 makes the service refresh and retry once; let that path fail too.
    google.token_responses.clear()
    google.token({"error": "invalid_grant"}, status=400)

    provider = GmailProvider(service=service)
    result = await provider.send_email("lead@example.com", "Hello", "Body", account_id=TEST_ACCOUNT)

    assert result.status == expected
    assert result.status != SendStatus.SENT
    assert result.success is False
    assert result.error
    assert result.message_id is None


@pytest.mark.asyncio
async def test_rate_limited_result_is_retryable_and_carries_retry_after():
    google = FakeGoogle().token(token_payload())
    service = build_service(google)
    await connect(service)
    google.send_error(429, headers={"Retry-After": "45"})

    result = await GmailProvider(service=service).send_email("lead@example.com", "Hi", "Body", account_id=TEST_ACCOUNT)

    assert result.status == SendStatus.RATE_LIMITED
    assert result.retry_after_seconds == 45
    assert result.retryable is True


@pytest.mark.asyncio
async def test_network_failure_is_failed_not_sent():
    def explode(request: httpx.Request) -> httpx.Response:
        if "oauth2.googleapis.com/token" in str(request.url):
            return httpx.Response(200, json=token_payload())
        if "userinfo" in str(request.url):
            return httpx.Response(200, json={"email": "founder@example.com"})
        raise httpx.ConnectError("connection refused")

    google = FakeGoogle()
    service = GmailService(
        oauth_client=GmailOAuthClient(
            client_id="id",
            client_secret="secret",
            redirect_uri="http://localhost/cb",
            transport=httpx.MockTransport(explode),
        ),
        api_client=GmailApiClient(transport=httpx.MockTransport(explode)),
        cipher=TokenCipher(FERNET_KEY),
        state_store=OAuthStateStore(),
    )
    await connect(service)

    result = await GmailProvider(service=service).send_email("lead@example.com", "Hi", "Body", account_id=TEST_ACCOUNT)

    assert result.status == SendStatus.FAILED
    assert result.success is False


@pytest.mark.asyncio
async def test_sending_without_a_connection_is_unauthorized_not_failed():
    google = FakeGoogle()
    service = build_service(google)

    provider = GmailProvider(service=service)
    result = await provider.send_email("lead@example.com", "Hello", "Body", account_id=TEST_ACCOUNT)

    assert result.status == SendStatus.UNAUTHORIZED
    assert result.error_code == "gmail_not_connected"

    with pytest.raises(GmailNotConnectedError):
        await service.send_email("lead@example.com", "Hello", "Body", account_id=TEST_ACCOUNT)


# =========================================================================
# 8. Provider abstraction
# =========================================================================


@pytest.mark.asyncio
async def test_mock_provider_records_messages_and_reports_connected():
    provider = MockEmailProvider()

    result = await provider.send_email("dev@example.com", "Subject", "Body")
    assert result.status == SendStatus.SENT
    assert provider.outbox[0]["to_email"] == "dev@example.com"

    status = await provider.get_connection_status()
    assert status.connected is True
    assert status.state == ConnectionState.CONNECTED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forced", [SendStatus.FAILED, SendStatus.RATE_LIMITED, SendStatus.UNAUTHORIZED]
)
async def test_mock_provider_can_rehearse_failure_modes(forced):
    provider = MockEmailProvider(force_status=forced)
    result = await provider.send_email("dev@example.com", "Subject", "Body")
    assert result.status == forced
    assert result.success is False


def test_registry_defaults_to_mock_and_honours_overrides():
    assert get_email_provider().name == "mock"
    assert get_email_provider("gmail").name == "gmail"

    override = MockEmailProvider(email_address="override@localhost")
    set_email_provider(override)
    assert get_email_provider() is override

    set_email_provider(None)
    assert get_email_provider().name == "mock"


def test_gmail_provider_exposes_the_required_interface():
    provider = GmailProvider()
    assert provider.name == "gmail"
    assert callable(provider.send_email)
    assert callable(provider.get_connection_status)


# =========================================================================
# 9. Communication tools now go through the provider
# =========================================================================


def test_communication_tool_uses_the_configured_provider():
    from app.tools.communication_tools import send_email

    provider = MockEmailProvider()
    set_email_provider(provider)

    payload = send_email("client@example.com", "Test Subject", "Test Body")

    assert payload["success"] is True
    assert payload["status"] == SendStatus.SENT.value
    assert payload["provider"] == "mock"
    assert payload["to_email"] == "client@example.com"
    assert provider.outbox[0]["subject"] == "Test Subject"


@pytest.mark.parametrize(
    "forced", [SendStatus.FAILED, SendStatus.RATE_LIMITED, SendStatus.UNAUTHORIZED]
)
def test_communication_tool_never_reports_success_on_failure(forced):
    from app.tools.communication_tools import send_email

    set_email_provider(MockEmailProvider(force_status=forced))
    payload = send_email("client@example.com", "Subject", "Body")

    assert payload["success"] is False
    assert payload["status"] == forced.value


def test_communication_tool_no_longer_uses_smtp_credentials():
    """Regression guard: mailbox passwords must not re-enter this module."""
    source = (backend_dir / "app" / "tools" / "communication_tools.py").read_text()
    for forbidden in ["smtplib", "SMTP_PASS", "SMTP_USER", "starttls", "server.login"]:
        assert forbidden not in source, f"communication_tools.py still references {forbidden}"


def test_communication_tool_reports_connection_status():
    from app.tools.communication_tools import get_email_connection_status

    set_email_provider(MockEmailProvider())
    status = get_email_connection_status()

    assert status["connected"] is True
    assert "access_token" not in status


# =========================================================================
# 10. HTTP surface
# =========================================================================


@pytest.fixture
def api_client(monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from app.integrations.gmail import routes as gmail_routes

    google = FakeGoogle().token(token_payload())
    service = build_service(google)
    monkeypatch.setattr(gmail_routes, "gmail_service", service)

    app = FastAPI()
    app.include_router(gmail_routes.router, prefix="/api")
    return TestClient(app), service, google


def test_connect_endpoint_returns_a_url_and_no_secrets(api_client):
    client, _, _ = api_client
    response = client.post("/api/integrations/gmail/connect", json={"account_id": TEST_ACCOUNT})

    assert response.status_code == 200
    body = response.json()
    assert body["authorization_url"].startswith("https://accounts.google.com/")
    assert body["account_id"] == TEST_ACCOUNT

    serialized = json.dumps(body)
    for forbidden in ("test-client-secret", "access_token", "refresh_token", FERNET_KEY):
        assert forbidden not in serialized


def test_status_endpoint_before_and_after_connect(api_client):
    client, _, _ = api_client

    before = client.get("/api/integrations/gmail/status", params={"account_id": TEST_ACCOUNT})
    assert before.status_code == 200
    assert before.json()["connected"] is False

    connect_body = client.post(
        "/api/integrations/gmail/connect", json={"account_id": TEST_ACCOUNT}
    ).json()
    callback = client.get(
        "/api/integrations/gmail/callback",
        params={"code": "auth-code", "state": connect_body["state"]},
    )
    assert callback.status_code == 200
    assert callback.json()["connected"] is True

    after = client.get("/api/integrations/gmail/status", params={"account_id": TEST_ACCOUNT})
    body = after.json()
    assert body["connected"] is True
    assert body["email_address"] == "founder@example.com"
    assert "access_token" not in json.dumps(body)


def test_callback_rejects_a_forged_state(api_client):
    client, _, _ = api_client
    response = client.get(
        "/api/integrations/gmail/callback", params={"code": "auth-code", "state": "forged"}
    )
    assert response.status_code == 400


def test_callback_surfaces_user_denial(api_client):
    client, _, _ = api_client
    response = client.get(
        "/api/integrations/gmail/callback",
        params={"error": "access_denied", "error_description": "The user denied the request."},
    )
    assert response.status_code == 400
    assert "denied" in response.json()["detail"].lower()


def test_disconnect_endpoint(api_client):
    client, _, _ = api_client

    connect_body = client.post(
        "/api/integrations/gmail/connect", json={"account_id": TEST_ACCOUNT}
    ).json()
    client.get(
        "/api/integrations/gmail/callback",
        params={"code": "auth-code", "state": connect_body["state"]},
    )

    response = client.post(
        "/api/integrations/gmail/disconnect", params={"account_id": TEST_ACCOUNT}
    )
    assert response.status_code == 200
    assert response.json()["disconnected"] is True

    status = client.get("/api/integrations/gmail/status", params={"account_id": TEST_ACCOUNT})
    assert status.json()["connected"] is False


def test_send_endpoint_maps_status_to_http_code(api_client):
    client, _, google = api_client

    connect_body = client.post(
        "/api/integrations/gmail/connect", json={"account_id": TEST_ACCOUNT}
    ).json()
    client.get(
        "/api/integrations/gmail/callback",
        params={"code": "auth-code", "state": connect_body["state"]},
    )

    google.send_ok("m-http").metadata({"labelIds": ["SENT"], "payload": {"headers": []}})
    ok = client.post(
        "/api/integrations/gmail/send",
        json={
            "to_email": "lead@example.com",
            "subject": "Hello",
            "body": "Body",
            "account_id": TEST_ACCOUNT,
        },
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "SENT"
    assert ok.json()["message_id"] == "m-http"

    google.send_responses.clear()
    google.send_error(429, headers={"Retry-After": "60"})
    limited = client.post(
        "/api/integrations/gmail/send",
        json={
            "to_email": "lead@example.com",
            "subject": "Hello",
            "body": "Body",
            "account_id": TEST_ACCOUNT,
        },
    )
    assert limited.status_code == 429
    assert limited.json()["status"] == "RATE_LIMITED"


def test_send_endpoint_is_unauthorized_when_not_connected(api_client):
    client, _, _ = api_client
    response = client.post(
        "/api/integrations/gmail/send",
        json={"to_email": "lead@example.com", "subject": "Hello", "body": "Body"},
    )
    assert response.status_code == 401
    assert response.json()["status"] == "UNAUTHORIZED"
