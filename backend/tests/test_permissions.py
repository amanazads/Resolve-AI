"""
Permission and audit tests.

The behaviour under test is the trade the whole feature makes: one explicit,
scoped approval covers every recipient of that campaign, and nothing else.
"""

import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List

import pytest

# Ensure backend is on sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.automation.worker import CampaignExecutionService, campaign_execution_service
from app.database.mongodb import db_manager
from app.integrations.mock_provider import MockEmailProvider
from app.integrations.registry import reset_provider_cache, set_email_provider
from app.permissions.middleware import (
    IntegrationNotAuthorized,
    PermissionDenied,
    PermissionEnforcer,
    enforcer,
)
from app.permissions.models import (
    AuditEvent,
    AuditEventType,
    GrantPermissionRequest,
    PermissionGrant,
    PermissionGrantView,
    PermissionRequest,
    PermissionScope,
    redact,
    utc_now,
)
from app.permissions.service import PermissionService, permission_service

USER = "aman@resolve.ai"
CAMPAIGN = "camp_x"
DATASET = "ds_y"


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture(autouse=True)
def clean_state():
    for store in (
        db_manager._memory_permissions,
        db_manager._memory_campaigns,
        db_manager._memory_contacts,
        db_manager._memory_datasets,
        db_manager._memory_campaign_jobs,
        db_manager._memory_campaign_progress,
        db_manager._memory_idempotency,
        db_manager._memory_automation_runs,
    ):
        store.clear()
    db_manager._memory_audit.clear()
    CampaignExecutionService._tasks.clear()
    CampaignExecutionService._workers.clear()
    reset_provider_cache()
    set_email_provider(None)
    yield
    db_manager._memory_permissions.clear()
    db_manager._memory_audit.clear()
    CampaignExecutionService._tasks.clear()
    CampaignExecutionService._workers.clear()
    set_email_provider(None)


def campaign_grant(**overrides) -> GrantPermissionRequest:
    """The worked example: send for Campaign X to Dataset Y via connected Gmail."""
    payload = {
        "user_id": USER,
        "scopes": [PermissionScope.CAMPAIGN_EXECUTE, PermissionScope.EMAIL_SEND],
        "integration": "mock",
        "campaign_id": CAMPAIGN,
        "dataset_id": DATASET,
        "audience": ["INVESTOR"],
        "max_recipients": 100,
        "granted_by": USER,
        "note": "Approved in the UI.",
    }
    payload.update(overrides)
    return GrantPermissionRequest(**payload)


def send_request(**overrides) -> PermissionRequest:
    payload = {
        "user_id": USER,
        "scope": PermissionScope.EMAIL_SEND,
        "integration": "mock",
        "campaign_id": CAMPAIGN,
        "dataset_id": DATASET,
        "audience": ["INVESTOR"],
        "recipient_count": 25,
    }
    payload.update(overrides)
    return PermissionRequest(**payload)


async def audit_events(**filters) -> List[Dict[str, Any]]:
    return await permission_service.list_audit_events(limit=1000, **filters)


async def event_types(**filters) -> List[str]:
    return [e["event"] for e in await audit_events(**filters)]


async def seed_campaign(contact_count: int = 5, owner: str = USER) -> None:
    await db_manager.save_dataset({"dataset_id": DATASET, "filename": "investors.csv"})
    await db_manager.save_campaign(
        {
            "id": CAMPAIGN,
            "campaign_id": CAMPAIGN,
            "owner_id": owner,
            "name": "Seed round outreach",
            "objective": "Introduce Resolve AI",
            "goal": "Introduce Resolve AI",
            "message_strategy": "Short and direct",
            "audience": ["INVESTOR"],
            "dataset_id": DATASET,
            "status": "READY",
            "personalization_fields": ["first_name"],
            "total_contacts": contact_count,
        }
    )
    await db_manager.save_contacts_batch(
        [
            {
                "contact_id": f"cnt_{i}",
                "dataset_id": DATASET,
                "email": f"investor{i}@fund.com",
                "first_name": f"Investor{i}",
                "company": f"Fund {i}",
                "contact_type": "INVESTOR",
                "is_valid": True,
            }
            for i in range(contact_count)
        ]
    )


@pytest.fixture
def inline_worker(monkeypatch):
    original = CampaignExecutionService._spawn_worker

    async def spawn(self, campaign_id, rate_per_minute, concurrency, run_in_background=True):
        await original(
            self, campaign_id, rate_per_minute=rate_per_minute,
            concurrency=concurrency, run_in_background=False,
        )

    monkeypatch.setattr(CampaignExecutionService, "_spawn_worker", spawn)
    monkeypatch.setattr("app.personalization.generator.invoke_llm", lambda prompt: "")


# =========================================================================
# 1. Scope matching
# =========================================================================


@pytest.mark.asyncio
async def test_a_matching_grant_allows_the_action():
    grant = await permission_service.grant(campaign_grant())

    check = await permission_service.check(send_request())

    assert check.allowed is True
    assert check.grant_id == grant.grant_id
    assert check.requires_authorization is False


@pytest.mark.asyncio
async def test_a_grant_for_a_different_campaign_does_not_carry_over():
    """The headline rule: one campaign's approval is not the next one's."""
    await permission_service.grant(campaign_grant())

    check = await permission_service.check(send_request(campaign_id="camp_totally_different"))

    assert check.allowed is False
    assert check.requires_authorization is True
    assert any("limited to campaign 'camp_x'" in r for r in json.dumps(check.near_misses).split('"'))


@pytest.mark.asyncio
async def test_a_grant_for_a_different_integration_does_not_carry_over():
    await permission_service.grant(campaign_grant(integration="gmail"))

    check = await permission_service.check(send_request(integration="linkedin"))

    assert check.allowed is False
    assert "gmail" in json.dumps(check.near_misses)


@pytest.mark.asyncio
async def test_a_grant_does_not_cover_an_audience_it_was_not_given():
    await permission_service.grant(campaign_grant(audience=["INVESTOR"]))

    same_audience = await permission_service.check(send_request(audience=["INVESTOR"]))
    wider = await permission_service.check(send_request(audience=["INVESTOR", "RECRUITER"]))
    unrestricted = await permission_service.check(send_request(audience=[]))

    assert same_audience.allowed is True
    assert wider.allowed is False
    assert unrestricted.allowed is False, "an unrestricted audience is not a subset"


@pytest.mark.asyncio
async def test_a_grant_does_not_cover_more_recipients_than_it_authorized():
    await permission_service.grant(campaign_grant(max_recipients=50))

    assert (await permission_service.check(send_request(recipient_count=50))).allowed is True

    over = await permission_service.check(send_request(recipient_count=51))
    assert over.allowed is False
    assert "above" in json.dumps(over.near_misses)


@pytest.mark.asyncio
async def test_a_grant_does_not_cover_a_scope_it_was_not_given():
    await permission_service.grant(
        campaign_grant(scopes=[PermissionScope.CONTACT_READ], integration=None)
    )

    check = await permission_service.check(send_request())
    assert check.allowed is False
    # Not even a near miss: the scope is absent entirely.
    assert check.near_misses == []


@pytest.mark.asyncio
async def test_a_grant_does_not_cover_a_different_user():
    await permission_service.grant(campaign_grant())
    check = await permission_service.check(send_request(user_id="someone-else@example.com"))
    assert check.allowed is False


@pytest.mark.asyncio
async def test_a_standing_grant_covers_more_than_one_campaign():
    """A user who wants an ongoing permission grants one without a campaign."""
    grant = await permission_service.grant(campaign_grant(campaign_id=None))
    assert grant.is_standing is True

    for campaign_id in ("camp_a", "camp_b", "camp_c"):
        check = await permission_service.check(send_request(campaign_id=campaign_id))
        assert check.allowed is True, campaign_id

    # ...but it is still bound by its other narrowing fields.
    assert (await permission_service.check(send_request(dataset_id="ds_other"))).allowed is False
    assert (await permission_service.check(send_request(audience=["HR"]))).allowed is False


@pytest.mark.asyncio
async def test_an_integration_bound_scope_must_name_its_integration():
    with pytest.raises(ValueError, match="must name the integration"):
        await permission_service.grant(campaign_grant(integration=None))


# =========================================================================
# 2. Expiry and revocation
# =========================================================================


@pytest.mark.asyncio
async def test_an_expired_grant_stops_working():
    grant = await permission_service.grant(campaign_grant(expires_in_days=1))
    assert (await permission_service.check(send_request())).allowed is True

    grant.expires_at = utc_now() - timedelta(seconds=1)
    await db_manager.save_permission_grant(grant.model_dump(mode="json"))

    check = await permission_service.check(send_request())
    assert check.allowed is False
    assert "expired" in json.dumps(check.near_misses).lower()


@pytest.mark.asyncio
async def test_revocation_takes_effect_immediately_and_is_recorded():
    grant = await permission_service.grant(campaign_grant())
    assert (await permission_service.check(send_request())).allowed is True

    revoked = await permission_service.revoke(
        grant.grant_id, revoked_by=USER, reason="Changed my mind."
    )

    assert revoked.revoked is True
    assert revoked.revoked_at is not None
    assert revoked.revoked_by == USER
    assert (await permission_service.check(send_request())).allowed is False

    events = await event_types(grant_id=grant.grant_id)
    assert AuditEventType.PERMISSION_REVOKED.value in events


@pytest.mark.asyncio
async def test_revocation_is_idempotent_and_keeps_the_record():
    grant = await permission_service.grant(campaign_grant())
    await permission_service.revoke(grant.grant_id, revoked_by=USER)
    again = await permission_service.revoke(grant.grant_id, revoked_by=USER)

    assert again.revoked is True
    # The record is kept, not deleted -- an audit trail that forgets is not one.
    assert await permission_service.get(grant.grant_id) is not None
    assert await permission_service.list_grants(user_id=USER) == []
    assert len(await permission_service.list_grants(user_id=USER, include_revoked=True)) == 1


@pytest.mark.asyncio
async def test_revoking_an_unknown_grant_returns_none():
    assert await permission_service.revoke("perm_nope") is None


# =========================================================================
# 3. Timestamps, usage and auditability
# =========================================================================


@pytest.mark.asyncio
async def test_a_grant_is_timestamped_and_tracks_its_use():
    grant = await permission_service.grant(campaign_grant())
    assert grant.granted_at is not None
    assert grant.granted_by == USER
    assert grant.usage_count == 0

    await permission_service.check(send_request())
    await permission_service.check(send_request())

    reloaded = await permission_service.get(grant.grant_id)
    assert reloaded.usage_count == 2
    assert reloaded.last_used_at is not None


@pytest.mark.asyncio
async def test_grants_and_denials_are_both_audited():
    grant = await permission_service.grant(campaign_grant())
    await permission_service.check(send_request())                       # allowed
    await permission_service.check(send_request(campaign_id="other"))    # denied

    events = await event_types(user_id=USER)
    assert AuditEventType.PERMISSION_GRANTED.value in events
    assert AuditEventType.PERMISSION_USED.value in events
    assert AuditEventType.PERMISSION_DENIED.value in events

    used = [e for e in await audit_events(user_id=USER) if e["event"] == "PERMISSION_USED"]
    assert used[0]["grant_id"] == grant.grant_id


def test_audit_events_redact_credential_shaped_content():
    event = AuditEvent(
        event=AuditEventType.INTEGRATION_CONNECTED,
        detail="stored token ya29.super-secret-value for the mailbox",
        metadata={
            "refresh_token": "1//totally-secret",
            "note": "api_key=sk-abcdefghijklmnopqrstuv",
            "nested": {"client_secret": "hunter2", "safe": "keep me"},
        },
    )

    safe = event.sanitized()

    serialized = json.dumps(safe.model_dump(mode="json"))
    assert "ya29.super-secret-value" not in serialized
    assert "1//totally-secret" not in serialized
    assert "sk-abcdefghijklmnopqrstuv" not in serialized
    assert "hunter2" not in serialized
    assert "keep me" in serialized, "non-secret content must survive"


def test_redact_leaves_ordinary_values_alone():
    assert redact("Sent to investor@fund.com") == "Sent to investor@fund.com"
    assert redact({"count": 12, "audience": ["INVESTOR"]}) == {
        "count": 12,
        "audience": ["INVESTOR"],
    }


# =========================================================================
# 4. OAuth is not bypassable
# =========================================================================


@pytest.mark.asyncio
async def test_a_grant_does_not_substitute_for_the_oauth_connection():
    """
    The user has authorized us to send from Gmail. Gmail itself has not been
    connected. The send is still refused, and the message says why.
    """
    await permission_service.grant(campaign_grant(integration="gmail"))

    check = await enforcer.check(
        user_id=USER,
        scope=PermissionScope.EMAIL_SEND,
        integration="gmail",
        campaign_id=CAMPAIGN,
        dataset_id=DATASET,
        audience=["INVESTOR"],
        recipient_count=10,
    )

    assert check.allowed is False
    assert any("does not replace the provider" in r for r in check.reasons)

    # The permission record itself is untouched and still valid on its own terms.
    assert (await permission_service.check(send_request(integration="gmail"))).allowed is True


@pytest.mark.asyncio
async def test_require_integration_authorization_raises_for_a_disconnected_account():
    with pytest.raises(IntegrationNotAuthorized) as exc:
        await enforcer.require_integration_authorization("gmail")
    assert "not authorized" in str(exc.value)


@pytest.mark.asyncio
async def test_a_connected_integration_passes_the_provider_check():
    set_email_provider(MockEmailProvider())
    await permission_service.grant(campaign_grant(integration="mock"))

    check = await enforcer.check(
        user_id=USER,
        scope=PermissionScope.EMAIL_SEND,
        integration="mock",
        campaign_id=CAMPAIGN,
        dataset_id=DATASET,
        audience=["INVESTOR"],
        recipient_count=10,
    )
    assert check.allowed is True


# =========================================================================
# 5. One approval, many recipients
# =========================================================================


@pytest.mark.asyncio
async def test_one_grant_covers_every_recipient_of_the_campaign(inline_worker):
    """
    The point of the feature: the user approves once, and all five recipients go
    out without another approval -- while each message is still audited.
    """
    await seed_campaign(contact_count=5)
    provider = MockEmailProvider()
    set_email_provider(provider)
    await permission_service.grant(campaign_grant(max_recipients=10))

    progress = await campaign_execution_service.start_campaign(
        campaign_id=CAMPAIGN,
        rate_per_minute=6000,
        run_in_background=False,
        principal_user_id=USER,
    )

    assert progress.sent == 5
    assert len(provider.outbox) == 5

    events = await audit_events(user_id=USER)
    kinds = [e["event"] for e in events]

    # One campaign-level authorization decision, not one per recipient.
    assert kinds.count(AuditEventType.PERMISSION_USED.value) == 2  # CAMPAIGN_EXECUTE + EMAIL_SEND
    assert kinds.count(AuditEventType.CAMPAIGN_STARTED.value) == 1
    # ...but every individual message is still on the record.
    assert kinds.count(AuditEventType.MESSAGE_SENT.value) == 5

    sent = [e for e in events if e["event"] == AuditEventType.MESSAGE_SENT.value]
    assert {e["recipient"] for e in sent} == {f"investor{i}@fund.com" for i in range(5)}
    assert all(e["message_id"] for e in sent)


@pytest.mark.asyncio
async def test_starting_a_campaign_without_a_grant_is_refused_before_any_job_exists(
    inline_worker,
):
    await seed_campaign(contact_count=5)
    provider = MockEmailProvider()
    set_email_provider(provider)

    with pytest.raises(PermissionDenied) as exc:
        await campaign_execution_service.start_campaign(
            campaign_id=CAMPAIGN, run_in_background=False, principal_user_id=USER
        )

    assert "CAMPAIGN_EXECUTE" in " ".join(exc.value.reasons)
    assert provider.outbox == []
    assert db_manager._memory_campaign_jobs == {}, "no jobs are created on a refusal"
    assert AuditEventType.PERMISSION_DENIED.value in await event_types(user_id=USER)


@pytest.mark.asyncio
async def test_a_dry_run_needs_campaign_execute_but_not_send_permission(inline_worker):
    await seed_campaign(contact_count=3)
    provider = MockEmailProvider()
    set_email_provider(provider)
    await permission_service.grant(
        campaign_grant(scopes=[PermissionScope.CAMPAIGN_EXECUTE], integration=None)
    )

    progress = await campaign_execution_service.start_campaign(
        campaign_id=CAMPAIGN,
        dry_run=True,
        rate_per_minute=6000,
        run_in_background=False,
        principal_user_id=USER,
    )

    assert progress.skipped == 3
    assert provider.outbox == []
    assert AuditEventType.CAMPAIGN_STARTED.value in await event_types(user_id=USER)


@pytest.mark.asyncio
async def test_failed_messages_are_audited_too(inline_worker):
    from app.integrations.base import SendStatus

    await seed_campaign(contact_count=2)
    set_email_provider(MockEmailProvider(force_status=SendStatus.FAILED))
    await permission_service.grant(campaign_grant())

    await campaign_execution_service.start_campaign(
        campaign_id=CAMPAIGN,
        rate_per_minute=6000,
        max_attempts=1,
        run_in_background=False,
        principal_user_id=USER,
    )

    kinds = await event_types(user_id=USER)
    assert kinds.count(AuditEventType.MESSAGE_FAILED.value) == 2
    assert AuditEventType.MESSAGE_SENT.value not in kinds


@pytest.mark.asyncio
async def test_revoking_mid_campaign_stops_the_resume(inline_worker):
    await seed_campaign(contact_count=4)
    set_email_provider(MockEmailProvider())
    grant = await permission_service.grant(campaign_grant())

    await campaign_execution_service.start_campaign(
        campaign_id=CAMPAIGN, rate_per_minute=6000,
        run_in_background=False, principal_user_id=USER,
    )
    await campaign_execution_service.pause_campaign(CAMPAIGN)
    await permission_service.revoke(grant.grant_id, revoked_by=USER, reason="Stop.")

    with pytest.raises(PermissionDenied):
        await campaign_execution_service.resume_campaign(
            CAMPAIGN, run_in_background=False, principal_user_id=USER
        )


@pytest.mark.asyncio
async def test_a_campaign_started_without_a_principal_is_still_audited(inline_worker):
    """Internal/system starts are not gated, but they are on the record."""
    await seed_campaign(contact_count=2)
    set_email_provider(MockEmailProvider())

    await campaign_execution_service.start_campaign(
        campaign_id=CAMPAIGN, rate_per_minute=6000, run_in_background=False
    )

    started = [e for e in await audit_events() if e["event"] == "CAMPAIGN_STARTED"]
    assert len(started) == 1
    assert started[0]["actor"] == "system"
    assert started[0]["user_id"] == USER  # attributed to the campaign's owner


# =========================================================================
# 6. Integration connect / disconnect auditing
# =========================================================================


@pytest.mark.asyncio
async def test_integration_disconnect_is_audited():
    from app.integrations.gmail.service import gmail_service

    # Nothing connected: disconnect is a no-op and writes no event.
    await gmail_service.disconnect("no-such-account")
    assert AuditEventType.INTEGRATION_DISCONNECTED.value not in await event_types()


@pytest.mark.asyncio
async def test_gmail_connect_and_disconnect_write_audit_events(monkeypatch):
    """Uses the real Gmail service against a stubbed Google."""
    import httpx
    from app.integrations.gmail.client import GmailApiClient
    from app.integrations.gmail.oauth import GmailOAuthClient, OAuthStateStore
    from app.integrations.gmail.service import (
        GmailService,
        GmailTokenStore,
        TokenCipher,
    )

    key = "eZ8Yq3nP5cJk1mVx7bR2tW9sL4dG6hA0uZ3fN8pQyXc="

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "token" in url:
            return httpx.Response(
                200,
                json={
                    "access_token": "ya29.secret",
                    "refresh_token": "1//secret",
                    "expires_in": 3600,
                    "scope": "https://www.googleapis.com/auth/gmail.send",
                },
            )
        if "userinfo" in url:
            return httpx.Response(200, json={"email": "founder@example.com"})
        if "revoke" in url:
            return httpx.Response(200)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    service = GmailService(
        oauth_client=GmailOAuthClient(
            client_id="id", client_secret="secret",
            redirect_uri="http://localhost/cb", transport=transport,
        ),
        api_client=GmailApiClient(transport=transport),
        token_store=GmailTokenStore(),
        cipher=TokenCipher(key),
        state_store=OAuthStateStore(),
    )

    auth = service.start_authorization(account_id="mailbox-1")
    await service.complete_authorization("code", auth.state)
    await service.disconnect("mailbox-1")

    kinds = await event_types()
    assert AuditEventType.INTEGRATION_CONNECTED.value in kinds
    assert AuditEventType.INTEGRATION_DISCONNECTED.value in kinds

    # The audit trail records the connection, never the credentials.
    serialized = json.dumps(await audit_events())
    assert "ya29.secret" not in serialized
    assert "1//secret" not in serialized


# =========================================================================
# 7. The agent stops asking once a permission is stored
# =========================================================================


@pytest.mark.asyncio
async def test_the_agent_reuses_a_stored_grant_instead_of_asking_again(monkeypatch):
    """
    A standing grant means a later run needs no approval in the request at all.
    """
    from app.agents.automation_graph import run_automation_agent

    monkeypatch.setattr("app.personalization.generator.invoke_llm", lambda prompt: "")

    goal = {
        "is_automation_request": True,
        "campaign_type": "INVESTOR_OUTREACH",
        "objective": "Introduce Resolve AI",
        "audience": ["INVESTOR"],
        "channel": "EMAIL",
        "dataset_hint": DATASET,
        "confidence": 0.9,
    }
    plan = {
        "campaign_type": "INVESTOR_OUTREACH",
        "objective": "Introduce Resolve AI",
        "audience": ["INVESTOR"],
        "channel": "EMAIL",
        "message_strategy": "Short and direct.",
        "personalization_fields": ["first_name", "company"],
        "steps": [{"order": 1, "action": "SEND_EMAIL", "requires_authorization": True}],
        "rate_per_minute": 600,
        "max_attempts": 1,
    }

    def fake_llm(prompt: str) -> str:
        if "planning step" in prompt:
            return json.dumps(plan)
        if "goal-analysis step" in prompt:
            return json.dumps(goal)
        return ""

    monkeypatch.setattr("app.agents.automation_nodes.invoke_llm", fake_llm)

    original = CampaignExecutionService._spawn_worker

    async def spawn(self, campaign_id, rate_per_minute, concurrency, run_in_background=True):
        await original(
            self, campaign_id, rate_per_minute=rate_per_minute,
            concurrency=concurrency, run_in_background=False,
        )

    monkeypatch.setattr(CampaignExecutionService, "_spawn_worker", spawn)

    await seed_campaign(contact_count=3, owner=USER)
    set_email_provider(MockEmailProvider())

    # A standing grant the user made earlier, for this dataset and audience.
    await permission_service.grant(
        campaign_grant(campaign_id=None, max_recipients=100)
    )

    state = await run_automation_agent(
        user_message="Email the investors.",
        user_id=USER,
        authorization_scope={},  # nothing supplied with the request
    )

    assert state["authorization"]["authorized"] is True
    assert state["authorization"]["source"] == "stored_grant"
    assert state["verification"]["provider_confirmed"] == 3


@pytest.mark.asyncio
async def test_a_stored_grant_does_not_cover_a_different_audience(monkeypatch):
    """The same standing grant does not authorize a campaign to recruiters."""
    await permission_service.grant(campaign_grant(campaign_id=None, audience=["INVESTOR"]))

    check = await enforcer.check(
        user_id=USER,
        scope=PermissionScope.EMAIL_SEND,
        integration="mock",
        dataset_id=DATASET,
        audience=["RECRUITER"],
        recipient_count=10,
        verify_integration=False,
    )
    assert check.allowed is False
    assert "RECRUITER" in json.dumps(check.near_misses)


# =========================================================================
# 8. API
# =========================================================================


@pytest.fixture
def api():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.permissions.routes import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return TestClient(app)


def test_grant_list_and_revoke_over_the_api(api):
    body = {
        "user_id": USER,
        "scopes": ["CAMPAIGN_EXECUTE", "EMAIL_SEND"],
        "integration": "gmail",
        "campaign_id": CAMPAIGN,
        "dataset_id": DATASET,
        "audience": ["INVESTOR"],
        "max_recipients": 500,
        "granted_by": USER,
    }

    created = api.post("/api/permissions", json=body)
    assert created.status_code == 201
    grant_id = created.json()["grant_id"]
    assert created.json()["active"] is True
    assert created.json()["standing"] is False
    assert "EMAIL_SEND" in created.json()["summary"]

    listed = api.get("/api/permissions", params={"user_id": USER})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["grant_id"] == grant_id

    revoked = api.delete(f"/api/permissions/{grant_id}", params={"revoked_by": USER})
    assert revoked.status_code == 200
    assert revoked.json()["revoked"] is True

    assert api.get("/api/permissions", params={"user_id": USER}).json()["total"] == 0
    with_revoked = api.get(
        "/api/permissions", params={"user_id": USER, "include_revoked": True}
    )
    assert with_revoked.json()["total"] == 1
    assert with_revoked.json()["items"][0]["revoked"] is True


def test_api_rejects_an_integration_bound_scope_without_an_integration(api):
    response = api.post(
        "/api/permissions",
        json={"user_id": USER, "scopes": ["EMAIL_SEND"], "granted_by": USER},
    )
    assert response.status_code == 400
    assert "must name the integration" in response.json()["detail"]


def test_api_404s_for_unknown_grants(api):
    assert api.get("/api/permissions/perm_nope").status_code == 404
    assert api.delete("/api/permissions/perm_nope").status_code == 404


def test_check_endpoint_reports_what_is_missing(api):
    denied = api.post(
        "/api/permissions/check",
        json={
            "user_id": USER,
            "scope": "EMAIL_SEND",
            "integration": "mock",
            "campaign_id": CAMPAIGN,
            "recipient_count": 5,
        },
    )
    assert denied.status_code == 200
    assert denied.json()["allowed"] is False
    assert denied.json()["requires_authorization"] is True
    assert denied.json()["reasons"]


def test_audit_log_endpoint_is_filterable(api):
    api.post(
        "/api/permissions",
        json={
            "user_id": USER,
            "scopes": ["CAMPAIGN_EXECUTE"],
            "granted_by": USER,
            "campaign_id": CAMPAIGN,
        },
    )

    everything = api.get("/api/permissions/audit/log")
    assert everything.status_code == 200
    assert everything.json()["total"] >= 1

    filtered = api.get(
        "/api/permissions/audit/log", params={"event": "PERMISSION_GRANTED", "user_id": USER}
    )
    assert filtered.json()["total"] == 1
    assert filtered.json()["items"][0]["event"] == "PERMISSION_GRANTED"

    assert api.get(
        "/api/permissions/audit/log", params={"event": "MESSAGE_SENT"}
    ).json()["total"] == 0


def test_permission_view_never_exposes_credentials():
    grant = PermissionGrant(
        user_id=USER,
        scopes=[PermissionScope.EMAIL_SEND],
        integration="gmail",
        granted_by=USER,
        metadata={"access_token": "ya29.should-never-be-serialized"},
    )
    view = PermissionGrantView.from_grant(grant)
    serialized = json.dumps(view.model_dump(mode="json"))
    assert "ya29" not in serialized
    assert "access_token" not in serialized


@pytest.mark.asyncio
async def test_campaign_start_endpoint_returns_403_until_permission_is_granted(
    inline_worker, monkeypatch
):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.campaigns import routes as campaign_routes

    await seed_campaign(contact_count=3)
    set_email_provider(MockEmailProvider())

    app = FastAPI()
    app.include_router(campaign_routes.router, prefix="/api")
    client = TestClient(app)

    refused = client.post(
        f"/api/campaigns/{CAMPAIGN}/start",
        json={"user_id": USER, "rate_per_minute": 6000},
    )
    assert refused.status_code == 403
    assert refused.json()["detail"]["error"] == "permission_denied"
    assert refused.json()["detail"]["reasons"]
    assert "POST /api/permissions" in refused.json()["detail"]["how_to_fix"]

    await permission_service.grant(campaign_grant())

    allowed = client.post(
        f"/api/campaigns/{CAMPAIGN}/start",
        json={"user_id": USER, "rate_per_minute": 6000},
    )
    assert allowed.status_code == 200
    assert allowed.json()["sent"] == 3


# =========================================================================
# 9. Service isolation
# =========================================================================


@pytest.mark.asyncio
async def test_the_service_can_be_pointed_at_a_different_store():
    service = PermissionService(db=db_manager)
    grant = await service.grant(campaign_grant(user_id="other@example.com"))
    assert grant.user_id == "other@example.com"
    assert (await service.list_grants(user_id=USER)) == []


@pytest.mark.asyncio
async def test_bulk_revocation_clears_a_users_grants():
    await permission_service.grant(campaign_grant())
    await permission_service.grant(campaign_grant(campaign_id="camp_other"))

    revoked = await permission_service.revoke_all_for_user(USER, revoked_by="admin")

    assert revoked == 2
    assert await permission_service.list_grants(user_id=USER) == []


def test_enforcer_can_be_constructed_with_its_own_service():
    custom = PermissionEnforcer(service=PermissionService())
    assert custom.service is not permission_service
