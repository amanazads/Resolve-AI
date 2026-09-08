"""
Real-Time Campaign Monitoring and Activity Events Tests.

Validates:
1. All 12 requested campaign activity events and telemetry types.
2. The asynchronous CampaignEventBus (pub/sub, filtering, SSE formatting, unsubscribe).
3. MongoDB persistence and pagination for campaign-level and workspace-wide activity logs.
4. HTTP and WebSocket streaming endpoints (/activity, /events, /ws).
5. End-to-end telemetry emission during planner, executor, worker, and service operations.
"""

import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Ensure backend directory is in sys.path
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
from app.automation.rate_limiter import RateLimiterRegistry
from app.automation.retry_service import CampaignRetryPolicy
from app.automation.worker import CampaignExecutionService, CampaignWorker
from app.campaigns.events import (
    CampaignActivityEvent,
    CampaignEventBus,
    CampaignEventType,
    campaign_event_bus,
)
from app.campaigns.models import (
    Campaign,
    CampaignPlan,
    CampaignType,
    CommunicationChannel,
)
from app.campaigns.routes import router as campaigns_router
from app.campaigns.service import CampaignService
from app.database.mongodb import db_manager
from app.integrations.base import (
    ConnectionState,
    ConnectionStatus,
    EmailProvider,
    SendResult,
    SendStatus,
)
from app.personalization.validator import PersonalizedMessage, ValidationStatus


# =========================================================================
# Scriptable Test Provider & Generator
# =========================================================================


class ScriptedProvider(EmailProvider):
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


class StubGenerator:
    """Deterministic message generation for offline unit and telemetry testing."""

    def generate_single(self, **kwargs):
        recipient = kwargs.get("recipient", {})
        email = recipient.get("email", "")
        return PersonalizedMessage(
            recipient_email=email,
            recipient_name=recipient.get("first_name", "Valued Partner"),
            subject=f"Hello {recipient.get('first_name', 'there')}",
            body=f"Hi {recipient.get('first_name', 'there')}, this is our automated outreach.",
            personalization_used=["first_name"],
            confidence=0.95,
            validation_status=ValidationStatus.VALID,
        )


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture(autouse=True)
def clean_test_state():
    """Ensure in-memory stores and bus subscribers are clean for each test."""
    db_manager._memory_campaigns.clear()
    db_manager._memory_contacts.clear()
    db_manager._memory_campaign_jobs.clear()
    db_manager._memory_campaign_progress.clear()
    db_manager._memory_campaign_activities.clear()
    db_manager._memory_idempotency.clear()
    db_manager._memory_audit.clear()
    db_manager._memory_suppressions.clear()

    CampaignExecutionService._tasks.clear()
    CampaignExecutionService._workers.clear()
    campaign_event_bus._campaign_subscribers.clear()
    campaign_event_bus._global_subscribers.clear()
    yield
    CampaignExecutionService._tasks.clear()
    CampaignExecutionService._workers.clear()
    campaign_event_bus._campaign_subscribers.clear()
    campaign_event_bus._global_subscribers.clear()


@pytest.fixture
def test_app():
    app = FastAPI()
    app.include_router(campaigns_router, prefix="/api")
    return app


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


# =========================================================================
# 1. Event Types and Model Tests
# =========================================================================


def test_all_twelve_campaign_activity_event_types():
    """Verify that all 12 specified events exist in CampaignEventType."""
    required_events = [
        "CAMPAIGN_CREATED",
        "PLAN_GENERATED",
        "DRY_RUN_COMPLETED",
        "CAMPAIGN_STARTED",
        "MESSAGE_GENERATED",
        "MESSAGE_SENT",
        "MESSAGE_FAILED",
        "MESSAGE_RETRIED",
        "CAMPAIGN_PAUSED",
        "CAMPAIGN_RESUMED",
        "CAMPAIGN_CANCELLED",
        "CAMPAIGN_COMPLETED",
    ]
    for evt_name in required_events:
        assert hasattr(CampaignEventType, evt_name), f"Missing {evt_name} in CampaignEventType"
        val = getattr(CampaignEventType, evt_name).value
        assert val == evt_name

    assert CampaignEventType.PROGRESS_UPDATED.value == "PROGRESS_UPDATED"
    assert CampaignEventType.WORKER_STATUS.value == "WORKER_STATUS"


def test_campaign_activity_event_model_creation():
    """Validate CampaignActivityEvent model serialization and defaults."""
    event = CampaignActivityEvent(
        campaign_id="camp_101",
        event_type=CampaignEventType.MESSAGE_SENT,
        recipient="founder@example.com",
        job_id="job_202",
        worker_id="worker_303",
        progress={"processed": 5, "sent": 4, "failed": 1},
        details="Email successfully delivered via Gmail API",
        metadata={"latency_ms": 120},
    )

    assert event.event_id.startswith("evt_")
    assert event.campaign_id == "camp_101"
    assert event.event_type == CampaignEventType.MESSAGE_SENT
    assert event.recipient == "founder@example.com"
    assert event.recipient_email == "founder@example.com"
    assert event.job_id == "job_202"
    assert event.progress["sent"] == 4

    d = event.to_dict()
    assert d["campaign_id"] == "camp_101"
    assert d["event_type"] == "MESSAGE_SENT"
    assert isinstance(d["timestamp"], str)


# =========================================================================
# 2. Event Bus Pub/Sub & SSE Tests
# =========================================================================


@pytest.mark.asyncio
async def test_event_bus_publish_and_subscribe():
    """Verify targeted and global subscribers receive matching events."""
    bus = CampaignEventBus()

    camp1_sub = await bus.subscribe("camp_alpha")
    camp2_sub = await bus.subscribe("camp_beta")
    global_sub = await bus.subscribe(None)

    evt_alpha = CampaignActivityEvent(
        campaign_id="camp_alpha",
        event_type=CampaignEventType.CAMPAIGN_STARTED,
        details="Alpha started",
    )
    evt_beta = CampaignActivityEvent(
        campaign_id="camp_beta",
        event_type=CampaignEventType.MESSAGE_SENT,
        recipient="beta@example.com",
    )

    await bus.publish(evt_alpha, persist=False)
    await bus.publish(evt_beta, persist=False)

    rec1 = await asyncio.wait_for(camp1_sub.get(), timeout=1.0)
    assert rec1.campaign_id == "camp_alpha"
    assert rec1.event_type == CampaignEventType.CAMPAIGN_STARTED
    assert camp1_sub.empty()

    rec2 = await asyncio.wait_for(camp2_sub.get(), timeout=1.0)
    assert rec2.campaign_id == "camp_beta"
    assert rec2.event_type == CampaignEventType.MESSAGE_SENT
    assert camp2_sub.empty()

    g1 = await asyncio.wait_for(global_sub.get(), timeout=1.0)
    g2 = await asyncio.wait_for(global_sub.get(), timeout=1.0)
    assert {g1.campaign_id, g2.campaign_id} == {"camp_alpha", "camp_beta"}

    await bus.unsubscribe(camp1_sub, "camp_alpha")
    await bus.unsubscribe(camp2_sub, "camp_beta")
    await bus.unsubscribe(global_sub, None)


def test_event_bus_sse_message_formatting():
    """Verify standard SSE wire format: event: ...\\ndata: {...}\\n\\n."""
    bus = CampaignEventBus()
    event = CampaignActivityEvent(
        campaign_id="camp_sse",
        event_type=CampaignEventType.MESSAGE_SENT,
        recipient="test@example.com",
        details="Delivered",
    )
    sse_text = bus.to_sse_message(event)

    assert sse_text.startswith(f"id: {event.event_id}\n")
    assert f"event: {event.event_type.value}\n" in sse_text
    assert "data: {" in sse_text
    assert sse_text.endswith("\n\n")

    lines = sse_text.strip().split("\n")
    data_line = [line for line in lines if line.startswith("data: ")][0]
    payload = json.loads(data_line[len("data: ") :])
    assert payload["campaign_id"] == "camp_sse"
    assert payload["recipient"] == "test@example.com"


# =========================================================================
# 3. Database Persistence & Activity Log Tests
# =========================================================================


@pytest.mark.asyncio
async def test_mongodb_activity_events_crud():
    """Verify database manager persists, filters, and paginates activity events."""
    camp_id = "camp_db_test"

    events = [
        CampaignActivityEvent(
            campaign_id=camp_id,
            event_type=CampaignEventType.CAMPAIGN_CREATED,
            details="Created",
        ),
        CampaignActivityEvent(
            campaign_id=camp_id,
            event_type=CampaignEventType.CAMPAIGN_STARTED,
            details="Started",
        ),
        CampaignActivityEvent(
            campaign_id=camp_id,
            event_type=CampaignEventType.MESSAGE_SENT,
            recipient="alice@test.com",
        ),
        CampaignActivityEvent(
            campaign_id=camp_id,
            event_type=CampaignEventType.MESSAGE_FAILED,
            recipient="bob@test.com",
            details="Bounced",
        ),
        CampaignActivityEvent(
            campaign_id=camp_id,
            event_type=CampaignEventType.CAMPAIGN_COMPLETED,
            details="Completed",
        ),
    ]

    for ev in events:
        saved = await db_manager.save_campaign_activity_event(ev.to_dict())
        assert saved is not None
        assert saved["event_id"] == ev.event_id

    all_events = await db_manager.list_campaign_activity_events(campaign_id=camp_id)
    assert len(all_events) == 5

    sent_events = await db_manager.list_campaign_activity_events(
        campaign_id=camp_id,
        event_type="MESSAGE_SENT",
    )
    assert len(sent_events) == 1
    assert sent_events[0]["recipient_email"] == "alice@test.com"

    p1 = await db_manager.list_campaign_activity_events(
        campaign_id=camp_id, skip=0, limit=2
    )
    p2 = await db_manager.list_campaign_activity_events(
        campaign_id=camp_id, skip=2, limit=2
    )
    assert len(p1) == 2
    assert len(p2) == 2
    assert p1[0]["event_id"] != p2[0]["event_id"]

    total = await db_manager.count_campaign_activity_events(campaign_id=camp_id)
    assert total == 5

    failed_count = await db_manager.count_campaign_activity_events(
        campaign_id=camp_id, event_type="MESSAGE_FAILED"
    )
    assert failed_count == 1


# =========================================================================
# 4. HTTP and WebSocket API Endpoints Tests
# =========================================================================


@pytest.mark.asyncio
async def test_api_campaign_activity_log(client):
    """Test GET /api/campaigns/{campaign_id}/activity and /api/campaigns/activity/log."""
    camp_id = "camp_api_log"

    await db_manager.save_campaign(
        {
            "campaign_id": camp_id,
            "name": "API Log Test",
            "objective": "Test objective",
            "message_strategy": "Test strategy",
            "status": "RUNNING",
        }
    )

    e1 = CampaignActivityEvent(
        campaign_id=camp_id,
        event_type=CampaignEventType.CAMPAIGN_STARTED,
        details="Running",
    )
    e2 = CampaignActivityEvent(
        campaign_id=camp_id,
        event_type=CampaignEventType.MESSAGE_SENT,
        recipient="inv@vc.com",
    )
    await db_manager.save_campaign_activity_event(e1.to_dict())
    await db_manager.save_campaign_activity_event(e2.to_dict())

    res = client.get(f"/api/campaigns/{camp_id}/activity")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
    assert data["items"][0]["campaign_id"] == camp_id

    res_filtered = client.get(
        f"/api/campaigns/{camp_id}/activity", params={"event_type": "MESSAGE_SENT"}
    )
    assert res_filtered.status_code == 200
    data_filtered = res_filtered.json()
    assert data_filtered["total"] == 1
    assert data_filtered["items"][0]["event_type"] == "MESSAGE_SENT"

    res_global = client.get("/api/campaigns/activity/log")
    assert res_global.status_code == 200
    data_global = res_global.json()
    assert data_global["total"] >= 2


def test_api_campaign_websocket(client):
    """Test WebSocket connection to /api/campaigns/{campaign_id}/ws."""
    camp_id = "camp_ws_test"

    with client.websocket_connect(f"/api/campaigns/{camp_id}/ws") as ws:
        initial = ws.receive_json()
        assert initial["type"] == "CONNECTION_ESTABLISHED"
        assert initial["campaign_id"] == camp_id

        evt = CampaignActivityEvent(
            campaign_id=camp_id,
            event_type=CampaignEventType.MESSAGE_SENT,
            recipient="ws_recipient@domain.com",
            details="Sent via WS test",
        )
        asyncio.run(campaign_event_bus.publish(evt, persist=False))

        # Stream receives events; verify MESSAGE_SENT arrives
        received_types = []
        for _ in range(5):
            msg = ws.receive_json()
            received_types.append(msg.get("event_type"))
            if msg.get("event_type") == "MESSAGE_SENT":
                assert msg["recipient"] == "ws_recipient@domain.com"
                break
        assert "MESSAGE_SENT" in received_types


# =========================================================================
# 5. Integration Telemetry: Planner, Executor, and Worker
# =========================================================================


@pytest.mark.asyncio
async def test_planner_emits_plan_generated_and_campaign_created():
    """Verify CampaignService emits PLAN_GENERATED and CAMPAIGN_CREATED."""
    service = CampaignService()
    sub = await campaign_event_bus.subscribe(None)

    contacts = [
        {"contact_id": "c1", "email": "vc1@fund.com", "first_name": "Val"},
        {"contact_id": "c2", "email": "vc2@fund.com", "first_name": "Vic"},
    ]
    await db_manager.save_contacts_batch(contacts)

    mock_plan = CampaignPlan(
        campaign_type=CampaignType.INVESTOR_OUTREACH,
        audience=["INVESTOR"],
        channel=CommunicationChannel.EMAIL,
        objective="Raise seed capital",
        message_strategy="Personalized investment note",
        personalization_fields=["first_name", "firm"],
    )

    with patch(
        "app.campaigns.service.CampaignPlanner.plan_from_goal",
        return_value=mock_plan,
    ):
        plan, camp = await service.create_campaign_plan(
            goal="Reach out to angels for our seed round",
            owner_id="founder_1",
            dataset_id="ds_angels",
            channel=CommunicationChannel.EMAIL,
        )

        assert camp.campaign_id is not None
        assert plan.campaign_type == CampaignType.INVESTOR_OUTREACH

        events_received = []
        while not sub.empty():
            events_received.append(sub.get_nowait())

        types = [e.event_type for e in events_received]
        assert CampaignEventType.PLAN_GENERATED in types
        assert CampaignEventType.CAMPAIGN_CREATED in types

        activities = await db_manager.list_campaign_activity_events(
            campaign_id=camp.campaign_id
        )
        persisted_types = [a["event_type"] for a in activities]
        assert "PLAN_GENERATED" in persisted_types
        assert "CAMPAIGN_CREATED" in persisted_types

    await campaign_event_bus.unsubscribe(sub, None)


@pytest.mark.asyncio
async def test_executor_telemetry_dry_run_and_live_send():
    """Verify executor emits MESSAGE_GENERATED, DRY_RUN_COMPLETED, and MESSAGE_SENT."""
    camp_id = "camp_exec_telemetry"
    sub = await campaign_event_bus.subscribe(camp_id)

    provider = ScriptedProvider()
    queue = CampaignJobQueue()
    executor = CampaignJobExecutor(
        queue=queue,
        provider=provider,
        limiters=RateLimiterRegistry(),
        retry_policy=CampaignRetryPolicy(),
        generator=StubGenerator(),
    )

    contact_dry = {
        "contact_id": "ct_dry",
        "email": "investor@sequoia.com",
        "first_name": "Roelof",
        "company": "Sequoia",
    }
    contact_live = {
        "contact_id": "ct_live",
        "email": "partner@benchmark.com",
        "first_name": "Peter",
        "company": "Benchmark",
    }

    # 1. Execute Dry Run Job
    dry_jobs = queue.build_jobs(camp_id, [contact_dry], dry_run=True)
    await queue.enqueue(dry_jobs)
    claimed_dry = await queue.claim_next(worker_id="w1", campaign_id=camp_id)
    assert claimed_dry is not None

    gen_dry = await executor.generate(
        claimed_dry,
        worker_id="w1",
        campaign_context={"objective": "Pitch", "campaign_type": "INVESTOR_OUTREACH"},
    )
    assert gen_dry is not None
    await executor.send(gen_dry, worker_id="w1")

    dry_events = []
    while not sub.empty():
        dry_events.append(sub.get_nowait())

    dry_types = [e.event_type for e in dry_events]
    assert CampaignEventType.MESSAGE_GENERATED in dry_types
    assert CampaignEventType.DRY_RUN_COMPLETED in dry_types
    assert provider.calls == []

    # 2. Execute Live Send Job
    live_jobs = queue.build_jobs(camp_id, [contact_live], dry_run=False)
    await queue.enqueue(live_jobs)
    claimed_live = await queue.claim_next(worker_id="w1", campaign_id=camp_id)
    assert claimed_live is not None

    gen_live = await executor.generate(
        claimed_live,
        worker_id="w1",
        campaign_context={"objective": "Pitch", "campaign_type": "INVESTOR_OUTREACH"},
    )
    assert gen_live is not None
    await executor.send(gen_live, worker_id="w1")

    live_events = []
    while not sub.empty():
        live_events.append(sub.get_nowait())

    live_types = [e.event_type for e in live_events]
    assert CampaignEventType.MESSAGE_SENT in live_types
    assert len(provider.calls) == 1

    await campaign_event_bus.unsubscribe(sub, camp_id)


@pytest.mark.asyncio
async def test_executor_telemetry_retry_and_failure():
    """Verify executor emits MESSAGE_RETRIED and MESSAGE_FAILED."""
    camp_id = "camp_fail_telemetry"
    sub = await campaign_event_bus.subscribe(camp_id)

    queue = CampaignJobQueue()
    contact_r = {"contact_id": "ct_r", "email": "bounce@domain.com"}
    contact_f = {"contact_id": "ct_f", "email": "fail@domain.com"}

    # 1. Rate-limited / retryable failure -> should emit MESSAGE_RETRIED
    provider_retry = ScriptedProvider(default_status=SendStatus.RATE_LIMITED)
    executor_retry = CampaignJobExecutor(
        queue=queue,
        provider=provider_retry,
        limiters=RateLimiterRegistry(),
        retry_policy=CampaignRetryPolicy(max_attempts=2, initial_delay_seconds=0.01),
        generator=StubGenerator(),
    )
    jobs_r = queue.build_jobs(camp_id, [contact_r])
    await queue.enqueue(jobs_r)
    claimed_r = await queue.claim_next(worker_id="w1", campaign_id=camp_id)
    assert claimed_r is not None
    gen_r = await executor_retry.generate(claimed_r, worker_id="w1", campaign_context={})
    await executor_retry.send(gen_r, worker_id="w1")

    events1 = []
    while not sub.empty():
        events1.append(sub.get_nowait())
    assert CampaignEventType.MESSAGE_RETRIED in [e.event_type for e in events1]

    # 2. Permanent failure -> should emit MESSAGE_FAILED
    provider_perm = ScriptedProvider(
        default_status=SendStatus.FAILED,
        results=[
            SendResult(
                status=SendStatus.FAILED,
                provider="scripted",
                to_email="fail@domain.com",
                subject="Test",
                error="recipient address rejected: invalid recipient",
            )
        ],
    )
    executor_perm = CampaignJobExecutor(
        queue=queue,
        provider=provider_perm,
        limiters=RateLimiterRegistry(),
        retry_policy=CampaignRetryPolicy(max_attempts=2, initial_delay_seconds=0.01),
        generator=StubGenerator(),
    )
    jobs_f = queue.build_jobs(camp_id, [contact_f])
    await queue.enqueue(jobs_f)
    claimed_f = await queue.claim_next(worker_id="w1", campaign_id=camp_id)
    assert claimed_f is not None
    gen_f = await executor_perm.generate(claimed_f, worker_id="w1", campaign_context={})
    await executor_perm.send(gen_f, worker_id="w1")

    events2 = []
    while not sub.empty():
        events2.append(sub.get_nowait())
    assert CampaignEventType.MESSAGE_FAILED in [e.event_type for e in events2]

    await campaign_event_bus.unsubscribe(sub, camp_id)


@pytest.mark.asyncio
async def test_service_and_worker_full_lifecycle_monitoring():
    """Verify execution service and worker emit CAMPAIGN_STARTED, WORKER_STATUS, PROGRESS_UPDATED, and CAMPAIGN_COMPLETED."""
    camp_id = "camp_worker_telemetry"
    sub = await campaign_event_bus.subscribe(camp_id)

    provider = ScriptedProvider()
    queue = CampaignJobQueue()
    executor = CampaignJobExecutor(
        queue=queue,
        provider=provider,
        limiters=RateLimiterRegistry(),
        retry_policy=CampaignRetryPolicy(),
        generator=StubGenerator(),
    )
    service = CampaignExecutionService(queue=queue, executor=executor)

    await db_manager.save_campaign(
        {
            "id": camp_id,
            "campaign_id": camp_id,
            "name": "Telemetry Lifecycle",
            "objective": "Lifecycle test",
            "message_strategy": "Direct outreach",
            "status": "READY",
            "total_contacts": 2,
        }
    )
    contacts = [
        {"contact_id": "c_0", "email": "lead_0@corp.com", "first_name": "Lead 0"},
        {"contact_id": "c_1", "email": "lead_1@corp.com", "first_name": "Lead 1"},
    ]
    await db_manager.save_contacts_batch(contacts)

    jobs = queue.build_jobs(camp_id, contacts)
    await queue.enqueue(jobs)

    # Start campaign synchronously via execution service
    await service.start_campaign(
        campaign_id=camp_id,
        contacts=contacts,
        rate_per_minute=6000,
        concurrency=1,
        run_in_background=False,
        validate_safety=False,
    )

    worker_events = []
    while not sub.empty():
        worker_events.append(sub.get_nowait())

    event_types = [e.event_type for e in worker_events]

    assert CampaignEventType.CAMPAIGN_STARTED in event_types
    assert CampaignEventType.WORKER_STATUS in event_types
    assert CampaignEventType.PROGRESS_UPDATED in event_types
    assert CampaignEventType.MESSAGE_SENT in event_types
    assert CampaignEventType.CAMPAIGN_COMPLETED in event_types

    comp_event = [
        e for e in worker_events if e.event_type == CampaignEventType.CAMPAIGN_COMPLETED
    ][0]
    assert comp_event.progress["sent"] == 2
    assert comp_event.progress["completed"] == 2
    assert comp_event.progress["failed"] == 0

    await campaign_event_bus.unsubscribe(sub, camp_id)


@pytest.mark.asyncio
async def test_service_pause_resume_cancel_telemetry():
    """Verify service emits CAMPAIGN_PAUSED, CAMPAIGN_RESUMED, and CAMPAIGN_CANCELLED."""
    camp_id = "camp_pause_cancel_telemetry"
    sub = await campaign_event_bus.subscribe(camp_id)

    queue = CampaignJobQueue()
    executor = CampaignJobExecutor(queue=queue, generator=StubGenerator())
    service = CampaignExecutionService(queue=queue, executor=executor)

    await db_manager.save_campaign(
        {
            "id": camp_id,
            "campaign_id": camp_id,
            "name": "Pause Test",
            "status": "RUNNING",
            "total_contacts": 5,
        }
    )

    # 1. Pause
    await service.pause_campaign(camp_id, reason="User review")
    # 2. Resume
    await service.resume_campaign(camp_id, run_in_background=True)
    # 3. Cancel
    await service.cancel_campaign(camp_id)

    events = []
    while not sub.empty():
        events.append(sub.get_nowait())

    event_types = [e.event_type for e in events]
    assert CampaignEventType.CAMPAIGN_PAUSED in event_types
    assert CampaignEventType.CAMPAIGN_RESUMED in event_types
    assert CampaignEventType.CAMPAIGN_CANCELLED in event_types

    await campaign_event_bus.unsubscribe(sub, camp_id)


@pytest.mark.asyncio
async def test_sse_events_generator_stream():
    """Verify campaign_event_bus.event_stream yields formatted SSE chunks."""
    camp_id = "camp_sse_gen"
    gen = campaign_event_bus.event_stream(camp_id, heartbeat_interval=5.0)

    evt = CampaignActivityEvent(
        campaign_id=camp_id,
        event_type=CampaignEventType.MESSAGE_SENT,
        recipient="streamed@test.com",
        details="Real-time transmission",
    )

    # Calling __anext__ initiates subscribe and waits for event
    task = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0.01)

    await campaign_event_bus.publish(evt, persist=False)
    chunk = await asyncio.wait_for(task, timeout=2.0)

    assert chunk.startswith(f"id: {evt.event_id}\n")
    assert "event: MESSAGE_SENT\n" in chunk
    assert "streamed@test.com" in chunk

    await gen.aclose()

