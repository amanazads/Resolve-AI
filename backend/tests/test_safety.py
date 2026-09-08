"""
Test Suite for Outreach Safety & Compliance Layer.

Covers:
- opt-out / unsubscribe processing & suppression
- duplicate recipient detection & blocking
- invalid/malformed email validation
- suppressed recipient rejection (OPT_OUT, UNSUBSCRIBE, DO_NOT_CONTACT, BOUNCE_PERMANENT)
- campaign-level limits and provider ceiling enforcement
- rate limiting and domain clustering protections
- disconnected provider rejection
- unauthorized direct execution (mandatory dry-run enforcement for new integrations)
- audit logging and sensitive credential redaction
- real-time execution suppression and auto-bounce suppression
"""

import sys
from pathlib import Path
import pytest
import time
from datetime import datetime, timezone

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.safety.policy import (
    CampaignLimits,
    ProviderSafetyCeilings,
    SafetyViolationError,
    enforce_dry_run_policy,
)
from app.safety.suppression import (
    SuppressionManager,
    SuppressionReason,
    SuppressionRecord,
    suppression_manager,
)
from app.safety.rate_limits import (
    RateLimiter,
    RateLimitExceededError,
    rate_limiter,
)
from app.safety.audit import (
    SafetyAuditLogger,
    SafetyAuditEventType,
    safety_audit_logger,
)
from app.safety.validator import (
    CampaignSafetyValidator,
    campaign_safety_validator,
)
from app.database.mongodb import db_manager
from app.automation.worker import CampaignExecutionService
from app.automation.queue import CampaignJobQueue
from app.automation.executor import CampaignJobExecutor
from app.automation.models import CampaignJob, JobStatus
from app.integrations.mock_provider import MockEmailProvider
from app.integrations.base import SendStatus


@pytest.fixture(autouse=True)
def clean_stores():
    """Cleans in-memory stores between tests."""
    db_manager._memory_suppressions.clear()
    db_manager._memory_safety_audits.clear()
    rate_limiter.reset()


# =========================================================================
# 1. Opt-out and Suppression Tests
# =========================================================================

@pytest.mark.asyncio
async def test_opt_out_and_suppression():
    """Test adding an opt-out, verifying it is stored, and blocking subsequent checks."""
    email = "investor@sequoia.com"
    record = await suppression_manager.add_suppression(
        email=email,
        reason=SuppressionReason.OPT_OUT,
        campaign_id="camp_101",
        details="Recipient replied 'Please do not email me again'",
    )

    assert record.email == email
    assert record.reason == SuppressionReason.OPT_OUT

    # Verify is_suppressed check
    is_sup, sup_rec = await suppression_manager.is_suppressed(email)
    assert is_sup is True
    assert sup_rec is not None
    assert sup_rec.reason == SuppressionReason.OPT_OUT

    # Case-insensitive check
    is_sup_upper, _ = await suppression_manager.is_suppressed("INVESTOR@sequoia.com")
    assert is_sup_upper is True

    # Check non-suppressed email
    is_sup_other, _ = await suppression_manager.is_suppressed("other@sequoia.com")
    assert is_sup_other is False


@pytest.mark.asyncio
async def test_suppression_reasons_all():
    """Verify all 4 suppression reasons are supported and properly checked."""
    emails_and_reasons = [
        ("optout@example.com", SuppressionReason.OPT_OUT),
        ("unsub@example.com", SuppressionReason.UNSUBSCRIBE),
        ("dnc@example.com", SuppressionReason.DO_NOT_CONTACT),
        ("bounce@example.com", SuppressionReason.BOUNCE_PERMANENT),
    ]

    for email, reason in emails_and_reasons:
        await suppression_manager.add_suppression(email, reason)

    for email, reason in emails_and_reasons:
        is_sup, rec = await suppression_manager.is_suppressed(email)
        assert is_sup is True
        assert rec.reason == reason

    # Filter batch
    allowed, suppressed = await suppression_manager.filter_suppressed(
        ["optout@example.com", "clean@example.com", "bounce@example.com"]
    )
    assert allowed == ["clean@example.com"]
    assert len(suppressed) == 2


@pytest.mark.asyncio
async def test_suppression_removal_admin_override():
    """Test that an admin can manually remove an email from suppression if requested."""
    email = "restored@example.com"
    await suppression_manager.add_suppression(email, SuppressionReason.DO_NOT_CONTACT)
    assert (await suppression_manager.is_suppressed(email))[0] is True

    removed = await suppression_manager.remove_suppression(email)
    assert removed is True
    assert (await suppression_manager.is_suppressed(email))[0] is False


# =========================================================================
# 2. Duplicate Detection Tests
# =========================================================================

@pytest.mark.asyncio
async def test_duplicate_recipients_rejected():
    """Campaign with duplicate recipient emails should be flagged and rejected."""
    recipients = [
        {"email": "alice@startup.co", "name": "Alice"},
        {"email": "bob@startup.co", "name": "Bob"},
        {"email": "Alice@startup.co", "name": "Alice Duplicate"},  # Case variation
    ]

    with pytest.raises(SafetyViolationError) as exc_info:
        await campaign_safety_validator.validate_campaign(
            campaign_id="camp_dup",
            recipients=recipients,
            campaign_purpose="Engineering hiring outreach",
            sender_profile={"name": "Recruiter Jane", "email": "jane@tech.co"},
            provider="mock",
            integration_status={"connected": True},
            strict=True,
        )

    assert "Duplicate recipients detected" in str(exc_info.value)
    assert "alice@startup.co" in str(exc_info.value)


# =========================================================================
# 3. Invalid Email Tests
# =========================================================================

@pytest.mark.asyncio
async def test_invalid_email_formats_rejected():
    """Malformed and non-RFC email addresses must fail validation."""
    malformed_recipients = [
        {"email": "not-an-email", "name": "Bad1"},
        {"email": "user@missing-tld", "name": "Bad2"},
        {"email": "@domain.com", "name": "Bad3"},
        {"email": "user name@domain.com", "name": "Bad4"},
    ]

    with pytest.raises(SafetyViolationError) as exc_info:
        await campaign_safety_validator.validate_campaign(
            campaign_id="camp_malformed",
            recipients=malformed_recipients,
            campaign_purpose="Partnership outreach",
            sender_profile={"name": "Alice", "email": "alice@company.com"},
            provider="mock",
            integration_status={"connected": True},
            strict=True,
        )

    assert "Malformed/invalid recipient email addresses detected" in str(exc_info.value)


# =========================================================================
# 4. Suppressed Recipient Pre-Campaign Validation
# =========================================================================

@pytest.mark.asyncio
async def test_suppressed_recipient_blocks_campaign():
    """A campaign containing suppressed recipients must be blocked pre-run."""
    await suppression_manager.add_suppression(
        email="blocked@vc.com",
        reason=SuppressionReason.OPT_OUT,
        details="Requested unsubscribe",
    )

    recipients = [
        {"email": "valid@vc.com", "name": "Valid VC"},
        {"email": "blocked@vc.com", "name": "Blocked VC"},
    ]

    with pytest.raises(SafetyViolationError) as exc_info:
        await campaign_safety_validator.validate_campaign(
            campaign_id="camp_sup",
            recipients=recipients,
            campaign_purpose="Seed fundraising introduction",
            sender_profile={"name": "Founder Bob", "email": "bob@mystartup.com"},
            provider="mock",
            integration_status={"connected": True},
            strict=True,
        )

    assert "Suppression violation" in str(exc_info.value)
    assert "blocked@vc.com" in str(exc_info.value)


# =========================================================================
# 5. Campaign Limit and Rate Limit Tests
# =========================================================================

@pytest.mark.asyncio
async def test_campaign_recipient_limits_enforced():
    """Campaigns exceeding configured max_recipients_per_campaign must be blocked."""
    limits = CampaignLimits(max_recipients_per_campaign=5)
    recipients = [
        {"email": f"user{i}@example.com", "name": f"User {i}"} for i in range(6)
    ]

    with pytest.raises(SafetyViolationError) as exc_info:
        await campaign_safety_validator.validate_campaign(
            campaign_id="camp_limit",
            recipients=recipients,
            campaign_purpose="Beta testing invitation",
            sender_profile={"name": "Founder", "email": "founder@app.com"},
            provider="mock",
            integration_status={"connected": True},
            limits=limits,
            strict=True,
        )

    assert "exceeds configured campaign limit of 5" in str(exc_info.value)


def test_rate_limiter_daily_and_domain_throttling():
    """Test rate limiter pacing, daily quota enforcement, and per-domain limits."""
    limiter = RateLimiter()
    limits = CampaignLimits(
        max_daily_sends=3,
        max_hourly_sends=3,
        max_sends_per_domain=2,
        min_delay_seconds=0.5,
    )

    t0 = 1000.0
    # Send 1 to domain target.com
    allowed, reason, delay = limiter.check_rate_limit("mock", "ceo@target.com", limits=limits, now=t0)
    assert allowed is True
    limiter.record_send("mock", "ceo@target.com", now=t0)

    # Fast follow-up should trigger pacing delay
    allowed, reason, delay = limiter.check_rate_limit("mock", "cto@target.com", limits=limits, now=t0 + 0.1)
    assert allowed is False
    assert "Pacing delay active" in reason

    # After pacing delay, send 2 to domain target.com
    t1 = t0 + 0.6
    allowed, reason, _ = limiter.check_rate_limit("mock", "cto@target.com", limits=limits, now=t1)
    assert allowed is True
    limiter.record_send("mock", "cto@target.com", now=t1)

    # 3rd send to target.com should hit domain limit of 2
    t2 = t1 + 0.6
    allowed, reason, _ = limiter.check_rate_limit("mock", "vp@target.com", limits=limits, now=t2)
    assert allowed is False
    assert "Domain sending limit reached for '@target.com'" in reason

    # Different domain should be allowed
    allowed, reason, _ = limiter.check_rate_limit("mock", "lead@other.com", limits=limits, now=t2)
    assert allowed is True
    limiter.record_send("mock", "lead@other.com", now=t2)

    # 4th total send hits max_daily_sends of 3
    t3 = t2 + 0.6
    allowed, reason, _ = limiter.check_rate_limit("mock", "lead@other2.com", limits=limits, now=t3)
    assert allowed is False
    assert "Campaign daily send limit reached" in reason


# =========================================================================
# 6. Disconnected Provider Tests
# =========================================================================

@pytest.mark.asyncio
async def test_disconnected_provider_blocks_campaign():
    """Attempting to start a campaign when the provider is disconnected must fail."""
    recipients = [{"email": "investor@firm.com", "name": "Investor"}]

    with pytest.raises(SafetyViolationError) as exc_info:
        await campaign_safety_validator.validate_campaign(
            campaign_id="camp_disc",
            recipients=recipients,
            campaign_purpose="Series A fundraising outreach",
            sender_profile={"name": "Alice", "email": "alice@startup.com"},
            provider="gmail",
            integration_status={
                "connected": False,
                "state": "DISCONNECTED",
                "detail": "OAuth token revoked or expired.",
            },
            strict=True,
        )

    assert "Provider integration 'gmail' is not ready (DISCONNECTED)" in str(exc_info.value)


# =========================================================================
# 7. Unauthorized Action & Mandatory Dry-Run Tests
# =========================================================================

def test_mandatory_dry_run_for_new_integration():
    """Dry-run is mandatory on first campaign for a newly connected integration unless explicitly overridden."""
    # 1. New integration requesting direct send without override -> must raise SafetyViolationError
    with pytest.raises(SafetyViolationError) as exc_info:
        enforce_dry_run_policy(
            is_new_integration=True,
            requested_dry_run=False,
            allow_direct_execution=False,
        )
    assert "Dry-run is mandatory for the first campaign on a newly connected integration" in str(exc_info.value)

    # 2. New integration requesting dry-run -> permitted
    res = enforce_dry_run_policy(
        is_new_integration=True,
        requested_dry_run=True,
        allow_direct_execution=False,
    )
    assert res is True

    # 3. New integration with explicit user override -> permitted direct send
    res = enforce_dry_run_policy(
        is_new_integration=True,
        requested_dry_run=False,
        allow_direct_execution=True,
    )
    assert res is False

    # 4. Established integration -> permitted direct send without override
    res = enforce_dry_run_policy(
        is_new_integration=False,
        requested_dry_run=False,
        allow_direct_execution=False,
    )
    assert res is False


@pytest.mark.asyncio
async def test_missing_or_abusive_purpose_rejected():
    """Empty, short, or phishing-like purposes must fail validation."""
    recipients = [{"email": "target@firm.com", "name": "Target"}]
    sender = {"name": "Sender", "email": "sender@firm.com"}

    # Too short
    with pytest.raises(SafetyViolationError) as exc_info:
        await campaign_safety_validator.validate_campaign(
            campaign_id="camp_p1",
            recipients=recipients,
            campaign_purpose="hi",
            sender_profile=sender,
            provider="mock",
            integration_status={"connected": True},
        )
    assert "Campaign purpose/objective is missing or too short" in str(exc_info.value)

    # Prohibited phishing pattern
    with pytest.raises(SafetyViolationError) as exc_info:
        await campaign_safety_validator.validate_campaign(
            campaign_id="camp_p2",
            recipients=recipients,
            campaign_purpose="Urgent transfer of wire funds to claim your crypto giveaway",
            sender_profile=sender,
            provider="mock",
            integration_status={"connected": True},
        )
    assert "violates acceptable use and anti-phishing/anti-scam safety policy" in str(exc_info.value)


@pytest.mark.asyncio
async def test_invalid_sender_identity_rejected():
    """Anonymous or invalid sender profiles must fail validation."""
    recipients = [{"email": "target@firm.com", "name": "Target"}]

    with pytest.raises(SafetyViolationError) as exc_info:
        await campaign_safety_validator.validate_campaign(
            campaign_id="camp_sender",
            recipients=recipients,
            campaign_purpose="Recruiting senior engineer",
            sender_profile={"name": "", "email": "bad-sender-email"},
            provider="mock",
            integration_status={"connected": True},
        )
    assert "Sender email 'bad-sender-email' is invalid" in str(exc_info.value)


# =========================================================================
# 8. Unsubscribe Token and Compliance Mechanism Tests
# =========================================================================

def test_unsubscribe_token_and_headers():
    """Test generating secure unsubscribe tokens, URLs, and compliance headers."""
    email = "lead@enterprise.com"
    token = suppression_manager.generate_unsubscribe_token(email, campaign_id="camp_200")
    assert email in token

    # Verification
    verified = suppression_manager.verify_unsubscribe_token(token)
    assert verified is not None
    assert verified["email"] == email
    assert verified["campaign_id"] == "camp_200"

    # Tampered token fails
    tampered = token[:-2] + "xx"
    assert suppression_manager.verify_unsubscribe_token(tampered) is None

    # Footer and headers
    footer = suppression_manager.get_unsubscribe_footer(email, "camp_200")
    assert "unsubscribe" in footer
    assert token in footer

    headers = suppression_manager.get_compliance_headers(email, "camp_200")
    assert "List-Unsubscribe" in headers
    assert "List-Unsubscribe-Post" in headers
    assert "One-Click" in headers["List-Unsubscribe-Post"]


@pytest.mark.asyncio
async def test_process_unsubscribe_via_token():
    """Processing unsubscribe token immediately adds to suppression store."""
    email = "user-unsubscribe@test.com"
    token = suppression_manager.generate_unsubscribe_token(email, campaign_id="camp_300")

    rec = await suppression_manager.process_unsubscribe(token)
    assert rec.email == email
    assert rec.reason == SuppressionReason.UNSUBSCRIBE

    is_sup, _ = await suppression_manager.is_suppressed(email)
    assert is_sup is True


# =========================================================================
# 9. Audit Logging and Credential Redaction Tests
# =========================================================================

@pytest.mark.asyncio
async def test_audit_logging_and_credential_redaction():
    """Verify safety events are logged and credentials are redacted."""
    await safety_audit_logger.log_event(
        event_type=SafetyAuditEventType.OPT_OUT_RECORDED,
        details="User opted out via reply",
        campaign_id="camp_audit",
        target_email="test@audit.com",
        metadata={
            "api_key": "super_secret_key_123",
            "access_token": "bearer_oauth_token",
            "safe_param": "allowed_data",
        },
    )

    events = await safety_audit_logger.list_events(campaign_id="camp_audit")
    assert len(events) == 1
    event = events[0]
    assert event.event_type == SafetyAuditEventType.OPT_OUT_RECORDED
    assert event.target_email == "test@audit.com"
    # Verify sensitive redaction
    assert event.metadata["api_key"] == "[REDACTED]"
    assert event.metadata["access_token"] == "[REDACTED]"
    assert event.metadata["safe_param"] == "allowed_data"


# =========================================================================
# 10. Execution-Level Real-Time Suppression & Bounce Recording
# =========================================================================

@pytest.mark.asyncio
async def test_realtime_suppression_blocks_send_in_executor():
    """If a recipient is suppressed while a campaign is running, executor aborts send."""
    queue = CampaignJobQueue(db=db_manager)
    executor = CampaignJobExecutor(queue=queue, db=db_manager)

    email = "midrun-suppressed@test.com"
    job = CampaignJob(
        campaign_id="camp_midrun",
        contact_id="cnt_1",
        channel="EMAIL",
        action="SEND_MESSAGE",
        idempotency_key=CampaignJob.build_idempotency_key("camp_midrun", "cnt_1", "EMAIL", "SEND_MESSAGE"),
        status=JobStatus.READY,
        to_email=email,
        generated_subject="Hello",
        generated_body="World",
    )
    await queue.enqueue([job])

    # Claim the job so worker-test holds the active lease
    claimed = await queue.claim_next(worker_id="worker-test", campaign_id="camp_midrun")
    assert claimed is not None

    # Mark as suppressed before executor sends
    await suppression_manager.add_suppression(email, SuppressionReason.UNSUBSCRIBE)

    # Try to send
    updated, decision = await executor.send(claimed, worker_id="worker-test")
    assert updated is not None or decision is None

    # Fetch updated job from db
    saved_jobs = await queue.list_jobs("camp_midrun")
    assert len(saved_jobs) == 1
    assert saved_jobs[0].status == JobStatus.FAILED
    assert "recipient_suppressed" in saved_jobs[0].failure_code


@pytest.mark.asyncio
async def test_permanent_bounce_automatically_suppresses_recipient():
    """When a provider returns a permanent bounce (550), recipient is auto-added to suppression."""
    queue = CampaignJobQueue(db=db_manager)
    provider = MockEmailProvider(force_status=SendStatus.FAILED)
    executor = CampaignJobExecutor(queue=queue, provider=provider, db=db_manager)

    email = "nonexistent-mailbox@target.com"
    job = CampaignJob(
        campaign_id="camp_bounce",
        contact_id="cnt_bounce",
        channel="EMAIL",
        action="SEND_MESSAGE",
        idempotency_key=CampaignJob.build_idempotency_key("camp_bounce", "cnt_bounce", "EMAIL", "SEND_MESSAGE"),
        status=JobStatus.READY,
        to_email=email,
        generated_subject="Hello",
        generated_body="World",
    )
    await queue.enqueue([job])

    # Simulate mock provider returning permanent bounce 550
    provider.force_status = SendStatus.FAILED
    # Send attempt
    job.attempt_count = 1
    result = await provider.send_email(to_email=email, subject="Hi", body="Hi")
    result.error = "550 5.1.1 Mailbox does not exist or user unknown"
    result.error_code = "mailbox_not_found"

    await executor._handle_send_failure(job, "worker-test", result)

    # Check that recipient is now suppressed
    is_sup, sup_rec = await suppression_manager.is_suppressed(email)
    assert is_sup is True
    assert sup_rec.reason == SuppressionReason.BOUNCE_PERMANENT


# =========================================================================
# 11. Worker Integration & Mandatory Dry Run Enforcement
# =========================================================================

@pytest.mark.asyncio
async def test_worker_start_campaign_enforces_safety():
    """Starting a campaign via CampaignExecutionService executes full safety validation."""
    service = CampaignExecutionService(db=db_manager)

    # 1. Setup a campaign with a suppressed contact
    camp_id = "camp_worker_safety"
    await db_manager.save_campaign({
        "id": camp_id,
        "campaign_id": camp_id,
        "name": "Outreach Test",
        "objective": "Partnership discussion",
        "status": "READY",
        "total_contacts": 1,
    })

    suppressed_email = "already-opted-out@firm.com"
    await suppression_manager.add_suppression(suppressed_email, SuppressionReason.OPT_OUT)

    contacts = [
        {"contact_id": "c1", "email": suppressed_email, "name": "Partner", "is_valid": True}
    ]

    # Starting campaign must fail with SafetyViolationError
    with pytest.raises(SafetyViolationError) as exc_info:
        await service.start_campaign(
            campaign_id=camp_id,
            contacts=contacts,
            run_in_background=False,
            validate_safety=True,
        )

    assert "Suppression violation" in str(exc_info.value)


@pytest.mark.asyncio
async def test_worker_start_campaign_new_integration_mandatory_dry_run():
    """New integration requires dry_run=True unless allow_direct_execution=True."""
    from app.integrations.registry import set_email_provider
    from app.integrations.mock_provider import MockEmailProvider
    set_email_provider(MockEmailProvider())
    service = CampaignExecutionService(db=db_manager)
    camp_id = "camp_worker_new_integ"
    await db_manager.save_campaign({
        "id": camp_id,
        "campaign_id": camp_id,
        "name": "Outreach Test",
        "objective": "Partnership discussion",
        "status": "READY",
        "metadata": {"is_new_integration": True},
    })

    contacts = [
        {"contact_id": "c1", "email": "valid-partner@firm.com", "name": "Partner", "is_valid": True}
    ]

    # 1. Attempt direct execution without allow_direct_execution -> fails
    with pytest.raises(SafetyViolationError) as exc_info:
        await service.start_campaign(
            campaign_id=camp_id,
            dry_run=False,
            allow_direct_execution=False,
            contacts=contacts,
            run_in_background=False,
        )
    assert "Dry-run is mandatory for the first campaign on a newly connected integration" in str(exc_info.value)

    # 2. Start with allow_direct_execution=True -> passes
    progress = await service.start_campaign(
        campaign_id=camp_id,
        dry_run=False,
        allow_direct_execution=True,
        contacts=contacts,
        run_in_background=False,
    )
    assert progress is not None
