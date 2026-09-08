"""
Campaign execution engine tests.

The engine is exercised against the in-memory MongoDB fallback and a scriptable
email provider, so every path -- success, transient failure, permanent failure,
retry, duplicate worker, restart, cancellation, rate limiting and idempotency --
runs deterministically and offline.
"""

import asyncio
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# Ensure backend is on sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.automation.executor import CampaignJobExecutor
from app.automation.models import (
    AutomationStatus,
    CampaignJob,
    JobStatus,
    utc_now,
)
from app.automation.queue import CampaignJobQueue
from app.automation.rate_limiter import (
    RateLimiterRegistry,
    TokenBucketRateLimiter,
)
from app.automation.retry_service import CampaignRetryPolicy
from app.automation.worker import CampaignExecutionService, CampaignWorker
from app.database.mongodb import db_manager
from app.integrations.base import ConnectionState, ConnectionStatus, EmailProvider, SendResult, SendStatus

CAMPAIGN_ID = "camp_exec_test"


# =========================================================================
# Fixtures and fakes
# =========================================================================


@pytest.fixture(autouse=True)
def clean_db():
    db_manager._memory_campaigns.clear()
    db_manager._memory_contacts.clear()
    db_manager._memory_campaign_jobs.clear()
    db_manager._memory_campaign_progress.clear()
    db_manager._memory_idempotency.clear()
    db_manager._memory_permissions.clear()
    db_manager._memory_audit.clear()
    CampaignExecutionService._tasks.clear()
    CampaignExecutionService._workers.clear()
    yield
    db_manager._memory_campaign_jobs.clear()
    db_manager._memory_idempotency.clear()
    CampaignExecutionService._tasks.clear()
    CampaignExecutionService._workers.clear()


class ScriptedProvider(EmailProvider):
    """
    Email provider whose outcome is scripted per call or per recipient.

    Records every call, so a test can assert not just the final status but how
    many times a given recipient was actually contacted.
    """

    name = "scripted"

    def __init__(
        self,
        results: Optional[List[SendResult]] = None,
        per_email: Optional[Dict[str, List[SendResult]]] = None,
        default_status: SendStatus = SendStatus.SENT,
    ):
        self.results = list(results or [])
        self.per_email = {k: list(v) for k, v in (per_email or {}).items()}
        self.default_status = default_status
        self.calls: List[Dict[str, Any]] = []

    def _next_result(self, to_email: str, subject: str) -> SendResult:
        queue = self.per_email.get(to_email)
        if queue:
            return queue.pop(0)
        if self.results:
            return self.results.pop(0)
        if self.default_status == SendStatus.SENT:
            return SendResult(
                status=SendStatus.SENT,
                provider=self.name,
                to_email=to_email,
                subject=subject,
                message_id=f"msg-{len(self.calls)}",
                thread_id=f"thr-{len(self.calls)}",
                sent_at=utc_now(),
            )
        return SendResult(
            status=self.default_status,
            provider=self.name,
            to_email=to_email,
            subject=subject,
            error=f"scripted {self.default_status.value}",
        )

    async def send_email(self, to_email, subject, body, **kwargs) -> SendResult:
        self.calls.append({"to_email": to_email, "subject": subject, "body": body})
        result = self._next_result(to_email, subject)
        result.to_email = to_email
        result.subject = subject
        return result

    async def get_connection_status(self, account_id=None) -> ConnectionStatus:
        return ConnectionStatus(
            provider=self.name, state=ConnectionState.CONNECTED, connected=True
        )

    def calls_to(self, email: str) -> int:
        return sum(1 for call in self.calls if call["to_email"] == email)


def failure(status: SendStatus, error: str = "boom", **kwargs) -> SendResult:
    return SendResult(
        status=status, provider="scripted", to_email="", subject="", error=error, **kwargs
    )


class StubGenerator:
    """Deterministic message generation, so tests assert on the engine only."""

    def __init__(self, fail_for: Optional[List[str]] = None):
        self.fail_for = set(fail_for or [])
        self.calls = 0

    def generate_single(self, **kwargs):
        from app.personalization.validator import PersonalizedMessage, ValidationStatus

        self.calls += 1
        recipient = kwargs.get("recipient", {})
        email = recipient.get("email", "")
        if email in self.fail_for:
            raise RuntimeError("generator exploded")
        return PersonalizedMessage(
            recipient_email=email,
            recipient_name=recipient.get("full_name", ""),
            subject=f"Hello {recipient.get('first_name', 'there')}",
            body=f"Hi {recipient.get('first_name', 'there')}, this is a test message body.",
            personalization_used=["first_name"],
            confidence=0.9,
            validation_status=ValidationStatus.VALID,
        )


async def seed_campaign(
    campaign_id: str = CAMPAIGN_ID,
    contact_count: int = 3,
    status: str = AutomationStatus.READY.value,
) -> List[Dict[str, Any]]:
    """Creates a campaign document and its contacts in the in-memory store."""
    await db_manager.save_campaign(
        {
            "id": campaign_id,
            "campaign_id": campaign_id,
            "name": "Execution test campaign",
            "objective": "Introduce the product",
            "goal": "Introduce the product",
            "message_strategy": "Short and direct",
            "audience": [],
            "dataset_id": "ds_test",
            "status": status,
            "personalization_fields": ["first_name"],
        }
    )
    contacts = [
        {
            "contact_id": f"cnt_{i}",
            "dataset_id": "ds_test",
            "email": f"lead{i}@example.com",
            "first_name": f"Lead{i}",
            "full_name": f"Lead {i}",
            "company": f"Company {i}",
            "contact_type": "INVESTOR",
            "is_valid": True,
        }
        for i in range(contact_count)
    ]
    await db_manager.save_contacts_batch(contacts)
    return contacts


def build_engine(
    provider: Optional[EmailProvider] = None,
    generator: Optional[StubGenerator] = None,
    retry_policy: Optional[CampaignRetryPolicy] = None,
    limiters: Optional[RateLimiterRegistry] = None,
):
    """A queue + executor + service wired to scriptable collaborators."""
    queue = CampaignJobQueue()
    executor = CampaignJobExecutor(
        queue=queue,
        provider=provider or ScriptedProvider(),
        retry_policy=retry_policy or CampaignRetryPolicy(
            initial_delay_seconds=0.01, jitter_ratio=0.0, max_delay_seconds=0.05
        ),
        limiters=limiters or RateLimiterRegistry(default_rate_per_minute=100000),
        generator=generator or StubGenerator(),
    )
    service = CampaignExecutionService(queue=queue, executor=executor)
    return queue, executor, service


async def run_campaign(service, campaign_id: str = CAMPAIGN_ID, **kwargs):
    """Starts a campaign and waits for its worker, without background tasks."""
    return await service.start_campaign(
        campaign_id=campaign_id, run_in_background=False, **kwargs
    )


# =========================================================================
# 1. Job construction and idempotency keys
# =========================================================================


def test_idempotency_key_is_campaign_contact_channel_action():
    key = CampaignJob.build_idempotency_key("camp_1", "cnt_9", "EMAIL", "SEND_MESSAGE")
    assert key == "camp_1:cnt_9:EMAIL:SEND_MESSAGE"

    # Changing any component changes the identity of the action.
    variants = {
        CampaignJob.build_idempotency_key("camp_2", "cnt_9", "EMAIL", "SEND_MESSAGE"),
        CampaignJob.build_idempotency_key("camp_1", "cnt_8", "EMAIL", "SEND_MESSAGE"),
        CampaignJob.build_idempotency_key("camp_1", "cnt_9", "LINKEDIN", "SEND_MESSAGE"),
        CampaignJob.build_idempotency_key("camp_1", "cnt_9", "EMAIL", "FOLLOW_UP"),
    }
    assert key not in variants
    assert len(variants) == 4


@pytest.mark.asyncio
async def test_one_job_per_recipient_and_contacts_without_email_are_skipped():
    queue = CampaignJobQueue()
    contacts = [
        {"contact_id": "cnt_1", "email": "a@example.com"},
        {"contact_id": "cnt_2", "email": ""},
    ]
    jobs = queue.build_jobs(CAMPAIGN_ID, contacts)

    assert len(jobs) == 2
    assert jobs[0].status == JobStatus.PENDING
    assert jobs[1].status == JobStatus.SKIPPED
    assert jobs[1].failure_code == "missing_email"


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_across_repeated_calls():
    await seed_campaign(contact_count=4)
    _, _, service = build_engine()

    first = await service.enqueue_campaign(CAMPAIGN_ID)
    second = await service.enqueue_campaign(CAMPAIGN_ID)

    assert first == 4
    assert second == 0, "a repeated enqueue must not duplicate jobs"
    assert len(db_manager._memory_campaign_jobs) == 4


# =========================================================================
# 2. Successful sending
# =========================================================================


@pytest.mark.asyncio
async def test_campaign_sends_to_every_recipient_exactly_once():
    await seed_campaign(contact_count=5)
    provider = ScriptedProvider()
    queue, _, service = build_engine(provider=provider)

    progress = await run_campaign(service)

    assert progress.total == 5
    assert progress.sent == 5
    assert progress.failed == 0
    assert progress.remaining == 0
    assert progress.percent_complete == 100.0
    assert len(provider.calls) == 5
    for i in range(5):
        assert provider.calls_to(f"lead{i}@example.com") == 1

    campaign = await db_manager.get_campaign(CAMPAIGN_ID)
    assert campaign["status"] == AutomationStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_sent_job_records_message_provider_response_and_timestamps():
    await seed_campaign(contact_count=1)
    queue, _, service = build_engine()

    await run_campaign(service)
    job = (await queue.list_jobs(CAMPAIGN_ID))[0]

    assert job.status == JobStatus.SENT
    assert job.generated_subject and job.generated_body       # generated message
    assert job.provider == "scripted"                          # provider
    assert job.provider_status == SendStatus.SENT.value
    assert job.provider_response                               # provider response
    assert job.message_id                                      # message id
    assert job.attempt_count == 1                              # attempt count
    assert job.failure_reason is None                          # failure reason
    assert job.generated_at and job.sent_at and job.completed_at  # timestamps
    assert job.attempts and job.attempts[-1].status == JobStatus.SENT.value


# =========================================================================
# 3. Transient failure, retry and backoff
# =========================================================================


@pytest.mark.asyncio
async def test_transient_failure_moves_job_to_retry_pending_then_succeeds():
    await seed_campaign(contact_count=1)
    provider = ScriptedProvider(
        per_email={
            "lead0@example.com": [
                failure(SendStatus.FAILED, "upstream timeout"),
                SendResult(
                    status=SendStatus.SENT,
                    provider="scripted",
                    to_email="lead0@example.com",
                    subject="Hello",
                    message_id="msg-retry-ok",
                ),
            ]
        }
    )
    queue, _, service = build_engine(provider=provider)

    await run_campaign(service)
    job = (await queue.list_jobs(CAMPAIGN_ID))[0]

    assert job.status == JobStatus.SENT
    assert job.message_id == "msg-retry-ok"
    assert job.attempt_count == 2
    assert provider.calls_to("lead0@example.com") == 2
    # The retry is recorded, so the history shows what happened.
    assert any(a.status == JobStatus.RETRY_PENDING.value for a in job.attempts)


@pytest.mark.asyncio
async def test_retry_exhaustion_fails_the_job_after_max_attempts():
    await seed_campaign(contact_count=1)
    provider = ScriptedProvider(default_status=SendStatus.FAILED)
    queue, _, service = build_engine(provider=provider)

    await run_campaign(service, max_attempts=3)
    job = (await queue.list_jobs(CAMPAIGN_ID))[0]

    assert job.status == JobStatus.FAILED
    assert job.attempt_count == 3
    assert provider.calls_to("lead0@example.com") == 3
    assert "gave up after 3" in (job.failure_reason or "")


def test_backoff_is_exponential_and_capped():
    policy = CampaignRetryPolicy(
        initial_delay_seconds=2.0, backoff_factor=2.0, max_delay_seconds=30.0, jitter_ratio=0.0
    )
    assert [policy.base_delay(n) for n in range(1, 6)] == [2.0, 4.0, 8.0, 16.0, 30.0]


def test_backoff_jitter_spreads_retries():
    policy = CampaignRetryPolicy(initial_delay_seconds=10.0, jitter_ratio=0.5)
    delays = {round(policy.delay_with_jitter(1), 6) for _ in range(50)}
    assert len(delays) > 1, "jitter must not produce a single synchronized delay"
    assert all(5.0 <= d <= 15.0 for d in delays)


@pytest.mark.asyncio
async def test_retry_pending_job_is_not_claimable_before_its_backoff_elapses():
    await seed_campaign(contact_count=1)
    queue = CampaignJobQueue()
    await queue.enqueue(queue.build_jobs(CAMPAIGN_ID, [{"contact_id": "c1", "email": "a@b.com"}]))

    job = await queue.claim_next("w1", CAMPAIGN_ID)
    job = await queue.mark_generated(job, "w1", "s", "b")
    job = await queue.mark_sending(job, "w1")
    await queue.mark_retry_pending(
        job, "w1", "transient", next_attempt_at=utc_now() + timedelta(minutes=5)
    )

    assert await queue.claim_next("w2", CAMPAIGN_ID) is None

    # Once the backoff has elapsed, it becomes claimable again.
    await db_manager.update_campaign_job(
        job.id, {"next_attempt_at": (utc_now() - timedelta(seconds=1)).isoformat()}
    )
    assert await queue.claim_next("w2", CAMPAIGN_ID) is not None


# =========================================================================
# 4. Permanent failures
# =========================================================================


@pytest.mark.asyncio
async def test_permanent_failure_is_not_retried():
    await seed_campaign(contact_count=1)
    provider = ScriptedProvider(default_status=SendStatus.FAILED)
    provider.results = [failure(SendStatus.FAILED, "Invalid recipient address")]
    queue, _, service = build_engine(provider=provider)

    await run_campaign(service, max_attempts=5)
    job = (await queue.list_jobs(CAMPAIGN_ID))[0]

    assert job.status == JobStatus.FAILED
    assert job.permanent_failure is True
    assert job.attempt_count == 1, "a permanent failure must not be retried"
    assert provider.calls_to("lead0@example.com") == 1


@pytest.mark.asyncio
async def test_unauthorized_fails_the_job_and_pauses_the_campaign():
    """Bad credentials would fail every remaining job identically, so stop."""
    await seed_campaign(contact_count=5)
    provider = ScriptedProvider(default_status=SendStatus.UNAUTHORIZED)
    queue, _, service = build_engine(provider=provider)

    await run_campaign(service, concurrency=1)

    campaign = await db_manager.get_campaign(CAMPAIGN_ID)
    assert campaign["status"] == AutomationStatus.PAUSED.value
    assert "Paused automatically" in campaign["execution"]["pause_reason"]

    # It stopped early rather than burning through all five recipients.
    assert len(provider.calls) < 5


@pytest.mark.asyncio
async def test_generation_failure_fails_the_job_without_contacting_the_provider():
    await seed_campaign(contact_count=1)
    provider = ScriptedProvider()
    queue, _, service = build_engine(
        provider=provider, generator=StubGenerator(fail_for=["lead0@example.com"])
    )

    await run_campaign(service)
    job = (await queue.list_jobs(CAMPAIGN_ID))[0]

    assert job.status == JobStatus.FAILED
    assert job.failure_code == "generation_error"
    assert provider.calls == []


# =========================================================================
# 5. Idempotency and duplicate protection
# =========================================================================


@pytest.mark.asyncio
async def test_restarting_a_finished_campaign_does_not_resend():
    await seed_campaign(contact_count=3)
    provider = ScriptedProvider()
    queue, _, service = build_engine(provider=provider)

    await run_campaign(service)
    assert len(provider.calls) == 3

    # Start it again: the jobs are terminal, so nothing is claimable.
    await db_manager.update_campaign(CAMPAIGN_ID, {"status": AutomationStatus.READY.value})
    await run_campaign(service)

    assert len(provider.calls) == 3, "a re-start must not re-send to anyone"


@pytest.mark.asyncio
async def test_completed_idempotency_record_blocks_a_second_send():
    """
    Even if a job is somehow re-queued, a completed record for its key means the
    recipient action already happened and must not be repeated.
    """
    await seed_campaign(contact_count=1)
    provider = ScriptedProvider()
    queue, executor, service = build_engine(provider=provider)

    await run_campaign(service)
    job = (await queue.list_jobs(CAMPAIGN_ID))[0]
    assert provider.calls_to("lead0@example.com") == 1

    # Force the job back into the queue as if something had reset it.
    await db_manager.update_campaign_job(
        job.id,
        {
            "status": JobStatus.PENDING.value,
            "lease_owner": None,
            "lease_expires_at": None,
            "completed_at": None,
        },
    )
    await db_manager.update_campaign(CAMPAIGN_ID, {"status": AutomationStatus.RUNNING.value})

    worker = CampaignWorker(
        CAMPAIGN_ID, queue=queue, executor=executor, concurrency=1, max_idle_seconds=0
    )
    await worker.run(await CampaignWorker.load_campaign_context(CAMPAIGN_ID))

    reloaded = await queue.get(job.id)
    assert reloaded.status == JobStatus.SENT
    assert reloaded.provider_response.get("deduplicated") is True
    assert reloaded.attempt_count == 1, "reconciling a duplicate is not a new attempt"
    assert provider.calls_to("lead0@example.com") == 1, "the provider must not be called twice"


# =========================================================================
# 6. Concurrency: two workers, one job
# =========================================================================


@pytest.mark.asyncio
async def test_a_job_can_only_be_claimed_by_one_worker():
    await seed_campaign(contact_count=1)
    queue = CampaignJobQueue()
    await queue.enqueue(queue.build_jobs(CAMPAIGN_ID, [{"contact_id": "c1", "email": "a@b.com"}]))

    first = await queue.claim_next("worker-a", CAMPAIGN_ID)
    second = await queue.claim_next("worker-b", CAMPAIGN_ID)

    assert first is not None
    assert second is None, "a leased job must not be claimable by a second worker"
    assert first.lease_owner == "worker-a"


@pytest.mark.asyncio
async def test_a_worker_that_lost_its_lease_cannot_overwrite_the_job():
    await seed_campaign(contact_count=1)
    queue = CampaignJobQueue()
    await queue.enqueue(queue.build_jobs(CAMPAIGN_ID, [{"contact_id": "c1", "email": "a@b.com"}]))

    job = await queue.claim_next("worker-a", CAMPAIGN_ID)
    # Worker B steals the lease (as would happen after A's lease expired).
    await db_manager.update_campaign_job(job.id, {"lease_owner": "worker-b"})

    rejected = await queue.mark_generated(job, "worker-a", "subject", "body")
    assert rejected is None, "a stale worker's write must be rejected"

    current = await queue.get(job.id)
    assert current.generated_subject is None


@pytest.mark.asyncio
async def test_two_concurrent_workers_never_double_send():
    await seed_campaign(contact_count=12)
    provider = ScriptedProvider()
    queue, executor, service = build_engine(provider=provider)
    await service.enqueue_campaign(CAMPAIGN_ID)
    await db_manager.update_campaign(CAMPAIGN_ID, {"status": AutomationStatus.RUNNING.value})

    context = await CampaignWorker.load_campaign_context(CAMPAIGN_ID)
    workers = [
        CampaignWorker(
            CAMPAIGN_ID,
            worker_id=f"worker-{n}",
            queue=queue,
            executor=executor,
            concurrency=3,
            max_idle_seconds=0,
        )
        for n in range(3)
    ]
    await asyncio.gather(*(w.run(context) for w in workers))

    jobs = await queue.list_jobs(CAMPAIGN_ID, limit=100)
    assert len(jobs) == 12
    assert all(job.status == JobStatus.SENT for job in jobs)
    assert len(provider.calls) == 12
    for i in range(12):
        assert provider.calls_to(f"lead{i}@example.com") == 1


# =========================================================================
# 7. Restart recovery
# =========================================================================


@pytest.mark.asyncio
async def test_restart_requeues_a_job_interrupted_during_generation():
    await seed_campaign(contact_count=1)
    queue = CampaignJobQueue()
    await queue.enqueue(queue.build_jobs(CAMPAIGN_ID, [{"contact_id": "c1", "email": "a@b.com"}]))

    job = await queue.claim_next("dead-worker", CAMPAIGN_ID)
    assert job.status == JobStatus.GENERATING
    # The worker dies: its lease lapses.
    await db_manager.update_campaign_job(
        job.id, {"lease_expires_at": (utc_now() - timedelta(minutes=1)).isoformat()}
    )

    requeued, flagged = await queue.recover_expired_leases()

    assert (requeued, flagged) == (1, 0)
    recovered = await queue.get(job.id)
    assert recovered.status == JobStatus.PENDING
    assert recovered.lease_owner is None
    assert await queue.claim_next("new-worker", CAMPAIGN_ID) is not None


@pytest.mark.asyncio
async def test_restart_does_not_resend_a_job_interrupted_mid_send():
    """
    The provider may already have accepted the message. A duplicate email is
    worse than a missing one, so the job is failed for review, never retried.
    """
    await seed_campaign(contact_count=1)
    queue = CampaignJobQueue()
    await queue.enqueue(queue.build_jobs(CAMPAIGN_ID, [{"contact_id": "c1", "email": "a@b.com"}]))

    job = await queue.claim_next("dead-worker", CAMPAIGN_ID)
    job = await queue.mark_generated(job, "dead-worker", "subject", "body")
    job = await queue.mark_sending(job, "dead-worker")
    await db_manager.update_campaign_job(
        job.id, {"lease_expires_at": (utc_now() - timedelta(minutes=1)).isoformat()}
    )

    requeued, flagged = await queue.recover_expired_leases()

    assert (requeued, flagged) == (0, 1)
    recovered = await queue.get(job.id)
    assert recovered.status == JobStatus.FAILED
    assert recovered.requires_manual_review is True
    assert recovered.failure_code == "interrupted_in_flight"
    assert await queue.claim_next("new-worker", CAMPAIGN_ID) is None


@pytest.mark.asyncio
async def test_restart_resumes_unfinished_jobs_of_a_running_campaign():
    await seed_campaign(contact_count=6, status=AutomationStatus.RUNNING.value)
    provider = ScriptedProvider()
    queue, executor, service = build_engine(provider=provider)
    await service.enqueue_campaign(CAMPAIGN_ID)

    # Simulate a partial run: two already sent, one orphaned mid-generation.
    jobs = await queue.list_jobs(CAMPAIGN_ID, limit=100)
    for job in jobs[:2]:
        await db_manager.update_campaign_job(
            job.id,
            {
                "status": JobStatus.SENT.value,
                "message_id": "pre-existing",
                "completed_at": utc_now().isoformat(),
            },
        )
    await db_manager.update_campaign_job(
        jobs[2].id,
        {
            "status": JobStatus.GENERATING.value,
            "lease_owner": "dead-worker",
            "lease_expires_at": (utc_now() - timedelta(minutes=5)).isoformat(),
        },
    )

    summary = await service.recover_on_startup(resume_running=False)
    assert summary["requeued"] == 1

    # Now finish the run.
    await service.resume_campaign(CAMPAIGN_ID, run_in_background=False)
    progress = await service.get_progress(CAMPAIGN_ID)

    assert progress.sent == 6
    assert progress.remaining == 0
    # Only the four unfinished recipients were actually contacted.
    assert len(provider.calls) == 4


# =========================================================================
# 8. Pause, resume, cancel
# =========================================================================


@pytest.mark.asyncio
async def test_pause_stops_claiming_and_resume_finishes_the_rest():
    await seed_campaign(contact_count=6)
    provider = ScriptedProvider()
    queue, executor, service = build_engine(provider=provider)
    await service.enqueue_campaign(CAMPAIGN_ID)
    await db_manager.update_campaign(CAMPAIGN_ID, {"status": AutomationStatus.PAUSED.value})

    worker = CampaignWorker(
        CAMPAIGN_ID, queue=queue, executor=executor, concurrency=1, max_idle_seconds=0
    )
    await worker.run(await CampaignWorker.load_campaign_context(CAMPAIGN_ID))
    assert provider.calls == [], "a paused campaign must not send"

    progress = await service.resume_campaign(CAMPAIGN_ID, run_in_background=False)
    assert progress.sent == 6
    assert len(provider.calls) == 6


@pytest.mark.asyncio
async def test_cancel_stops_pending_jobs_but_keeps_what_was_already_sent():
    await seed_campaign(contact_count=5)
    provider = ScriptedProvider()
    queue, executor, service = build_engine(provider=provider)
    await service.enqueue_campaign(CAMPAIGN_ID)

    jobs = await queue.list_jobs(CAMPAIGN_ID, limit=100)
    await db_manager.update_campaign_job(
        jobs[0].id, {"status": JobStatus.SENT.value, "message_id": "already-sent"}
    )

    progress = await service.cancel_campaign(CAMPAIGN_ID)

    assert progress.cancelled == 4
    assert progress.sent == 1
    campaign = await db_manager.get_campaign(CAMPAIGN_ID)
    assert campaign["status"] == AutomationStatus.CANCELLED.value

    # A cancelled campaign runs nothing further.
    worker = CampaignWorker(
        CAMPAIGN_ID, queue=queue, executor=executor, concurrency=1, max_idle_seconds=0
    )
    await worker.run(await CampaignWorker.load_campaign_context(CAMPAIGN_ID))
    assert provider.calls == []

    still_sent = await queue.get(jobs[0].id)
    assert still_sent.status == JobStatus.SENT


@pytest.mark.asyncio
async def test_cancelled_campaign_cannot_be_started_or_resumed():
    await seed_campaign(contact_count=1)
    _, _, service = build_engine()
    await service.cancel_campaign(CAMPAIGN_ID)

    with pytest.raises(ValueError):
        await service.start_campaign(CAMPAIGN_ID, run_in_background=False)
    with pytest.raises(ValueError):
        await service.resume_campaign(CAMPAIGN_ID, run_in_background=False)


# =========================================================================
# 9. Rate limiting
# =========================================================================


def test_token_bucket_allows_a_burst_then_paces():
    clock = {"now": 0.0}
    limiter = TokenBucketRateLimiter(
        rate_per_minute=60, burst=5, time_source=lambda: clock["now"]
    )

    assert [limiter.try_acquire() for _ in range(5)] == [True] * 5
    assert limiter.try_acquire() is False, "the burst is exhausted"

    clock["now"] += 1.0  # 60/min == 1 token per second
    assert limiter.try_acquire() is True
    assert limiter.try_acquire() is False


@pytest.mark.asyncio
async def test_token_bucket_acquire_waits_for_capacity():
    clock = {"now": 0.0}
    slept: List[float] = []

    async def fake_sleep(seconds: float):
        slept.append(seconds)
        clock["now"] += seconds

    limiter = TokenBucketRateLimiter(
        rate_per_minute=60, burst=1, time_source=lambda: clock["now"], sleep=fake_sleep
    )

    assert await limiter.acquire() == 0.0    # the first is free
    waited = await limiter.acquire()          # the second must wait ~1s

    assert waited == pytest.approx(1.0, abs=0.05)
    assert slept, "acquire must yield rather than spin"


@pytest.mark.asyncio
async def test_campaign_run_is_paced_by_the_rate_limiter():
    await seed_campaign(contact_count=4)
    clock = {"now": 0.0}
    slept: List[float] = []

    async def fake_sleep(seconds: float):
        slept.append(seconds)
        clock["now"] += seconds

    limiters = RateLimiterRegistry(
        default_rate_per_minute=60, time_source=lambda: clock["now"], sleep=fake_sleep
    )
    limiters.for_campaign(CAMPAIGN_ID, rate_per_minute=60, burst=1)

    provider = ScriptedProvider()
    queue, _, service = build_engine(provider=provider, limiters=limiters)

    await run_campaign(service, concurrency=1, rate_per_minute=60)

    assert len(provider.calls) == 4
    # One free send, then one second of pacing before each of the other three.
    assert sum(slept) == pytest.approx(3.0, abs=0.1)


@pytest.mark.asyncio
async def test_provider_rate_limit_retries_after_the_requested_delay():
    await seed_campaign(contact_count=1)
    provider = ScriptedProvider(
        per_email={
            "lead0@example.com": [
                failure(SendStatus.RATE_LIMITED, "slow down", retry_after_seconds=1),
                SendResult(
                    status=SendStatus.SENT,
                    provider="scripted",
                    to_email="lead0@example.com",
                    subject="Hello",
                    message_id="msg-after-limit",
                ),
            ]
        }
    )
    queue, _, service = build_engine(provider=provider)

    await run_campaign(service)
    job = (await queue.list_jobs(CAMPAIGN_ID))[0]

    assert job.status == JobStatus.SENT
    assert job.attempt_count == 2
    assert any(a.retry_after_seconds == 1 for a in job.attempts)


# =========================================================================
# 10. Dry run
# =========================================================================


@pytest.mark.asyncio
async def test_dry_run_generates_everything_and_sends_nothing():
    await seed_campaign(contact_count=3)
    provider = ScriptedProvider()
    queue, _, service = build_engine(provider=provider)

    progress = await run_campaign(service, dry_run=True)

    assert provider.calls == [], "a dry run must not contact the provider"
    assert progress.dry_run is True
    assert progress.sent == 0, "a dry run must never be counted as delivery"
    assert progress.skipped == 3
    assert progress.remaining == 0

    for job in await queue.list_jobs(CAMPAIGN_ID):
        assert job.status == JobStatus.SKIPPED
        assert job.provider_status == "DRY_RUN"
        assert job.attempt_count == 0, "a dry run attempts nothing"
        assert job.generated_subject and job.generated_body
        preview = job.provider_response
        assert preview["dry_run"] is True
        assert preview["would_send_to"] == job.to_email
        assert preview["subject"] == job.generated_subject
        assert preview["body"] == job.generated_body

    # A dry run leaves no idempotency records, so the real run is unaffected.
    assert db_manager._memory_idempotency == {}


# =========================================================================
# 11. Progress persistence and scale
# =========================================================================


@pytest.mark.asyncio
async def test_progress_is_persisted_and_readable_without_the_worker():
    await seed_campaign(contact_count=4)
    provider = ScriptedProvider(default_status=SendStatus.FAILED)
    provider.results = [failure(SendStatus.FAILED, "Invalid recipient address")] * 4
    queue, _, service = build_engine(provider=provider)

    await run_campaign(service, max_attempts=1)

    stored = await db_manager.get_campaign_progress(CAMPAIGN_ID)
    assert stored is not None
    assert stored["failed"] == 4
    assert stored["sent"] == 0
    assert stored["total"] == 4

    # A fresh service instance reads the same numbers from storage.
    fresh = CampaignExecutionService(queue=CampaignJobQueue())
    progress = await fresh.get_progress(CAMPAIGN_ID)
    assert progress.failed == 4
    assert progress.percent_complete == 100.0


@pytest.mark.asyncio
async def test_large_campaign_creates_one_job_per_recipient():
    await seed_campaign(contact_count=0)
    contacts = [
        {"contact_id": f"cnt_{i}", "email": f"bulk{i}@example.com", "first_name": f"B{i}"}
        for i in range(1000)
    ]
    _, _, service = build_engine()

    enqueued = await service.enqueue_campaign(CAMPAIGN_ID, contacts=contacts)

    assert enqueued == 1000
    counts = await service.queue.status_counts(CAMPAIGN_ID)
    assert counts[JobStatus.PENDING.value] == 1000

    keys = {j["idempotency_key"] for j in db_manager._memory_campaign_jobs.values()}
    assert len(keys) == 1000, "every recipient action must have a unique key"


# =========================================================================
# 12. Campaign execution API
# =========================================================================


@pytest.fixture
def api(monkeypatch):
    """
    A TestClient wired to a scripted provider.

    The worker is spawned inline so each request completes deterministically;
    in production `_spawn_worker` creates a background task instead, which is
    what keeps a large campaign out of the request that started it.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.campaigns import routes as campaign_routes

    provider = ScriptedProvider()
    queue, executor, service = build_engine(provider=provider)

    original_spawn = service._spawn_worker

    async def inline_spawn(campaign_id, rate_per_minute, concurrency, run_in_background=True):
        await original_spawn(
            campaign_id,
            rate_per_minute=rate_per_minute,
            concurrency=concurrency,
            run_in_background=False,
        )

    monkeypatch.setattr(service, "_spawn_worker", inline_spawn)
    monkeypatch.setattr(campaign_routes, "campaign_execution_service", service)

    app = FastAPI()
    app.include_router(campaign_routes.router, prefix="/api")
    return TestClient(app), provider, queue, service


@pytest.mark.asyncio
async def test_api_start_progress_and_jobs(api):
    client, provider, _, _ = api
    await seed_campaign(contact_count=3)

    started = client.post(f"/api/campaigns/{CAMPAIGN_ID}/start", json={"rate_per_minute": 6000})
    assert started.status_code == 200
    assert started.json()["total"] == 3

    progress = client.get(f"/api/campaigns/{CAMPAIGN_ID}/progress")
    assert progress.status_code == 200
    body = progress.json()
    assert body["sent"] == 3
    assert body["percent_complete"] == 100.0

    jobs = client.get(f"/api/campaigns/{CAMPAIGN_ID}/jobs")
    assert jobs.status_code == 200
    payload = jobs.json()
    assert payload["returned"] == 3
    first = payload["items"][0]
    assert first["status"] == "SENT"
    assert first["generated_subject"]
    assert first["message_id"]
    assert first["attempt_count"] == 1
    assert first["idempotency_key"].startswith(f"{CAMPAIGN_ID}:")


@pytest.mark.asyncio
async def test_api_jobs_filter_and_validation(api):
    client, _, _, _ = api
    await seed_campaign(contact_count=2)
    client.post(f"/api/campaigns/{CAMPAIGN_ID}/start", json={"rate_per_minute": 6000})

    sent = client.get(f"/api/campaigns/{CAMPAIGN_ID}/jobs", params={"status": "SENT"})
    assert sent.json()["returned"] == 2

    failed = client.get(f"/api/campaigns/{CAMPAIGN_ID}/jobs", params={"status": "FAILED"})
    assert failed.json()["returned"] == 0

    bad = client.get(f"/api/campaigns/{CAMPAIGN_ID}/jobs", params={"status": "NONSENSE"})
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_api_pause_resume_cancel(api):
    client, provider, _, _ = api
    await seed_campaign(contact_count=4)

    # Pause before starting: nothing should go out.
    paused = client.post(f"/api/campaigns/{CAMPAIGN_ID}/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == AutomationStatus.PAUSED.value

    resumed = client.post(f"/api/campaigns/{CAMPAIGN_ID}/resume", json={"rate_per_minute": 6000})
    assert resumed.status_code == 200

    cancelled = client.post(f"/api/campaigns/{CAMPAIGN_ID}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == AutomationStatus.CANCELLED.value

    # Starting a cancelled campaign is a conflict, not a silent no-op.
    conflict = client.post(f"/api/campaigns/{CAMPAIGN_ID}/start")
    assert conflict.status_code == 409


@pytest.mark.asyncio
async def test_api_dry_run_reports_previews_without_sending(api):
    client, provider, _, _ = api
    await seed_campaign(contact_count=2)

    response = client.post(
        f"/api/campaigns/{CAMPAIGN_ID}/start", json={"dry_run": True, "rate_per_minute": 6000}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["dry_run"] is True
    assert body["sent"] == 0
    assert provider.calls == []

    jobs = client.get(f"/api/campaigns/{CAMPAIGN_ID}/jobs").json()["items"]
    assert all(job["provider_status"] == "DRY_RUN" for job in jobs)
    assert all(job["provider_response"]["would_send_to"] for job in jobs)


def test_api_unknown_campaign_is_404(api):
    client, _, _, _ = api
    assert client.get("/api/campaigns/does-not-exist/progress").status_code == 404
    assert client.post("/api/campaigns/does-not-exist/pause").status_code == 404
    assert client.post("/api/campaigns/does-not-exist/cancel").status_code == 404
    assert client.post("/api/campaigns/does-not-exist/start").status_code == 404
