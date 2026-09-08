import sys
import os
import asyncio
from pathlib import Path
import pytest

# Ensure backend is on sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.automation.models import (
    AutomationPlan,
    AutomationTask,
    AutomationJob,
    Campaign,
    AutomationStatus,
    IdempotencyRecord
)
from app.automation.planner import AutomationPlanner
from app.automation.executor import DeterministicExecutor
from app.automation.retry_service import RetryService
from app.automation.job_queue import JobQueue
from app.automation.campaign_service import CampaignService
from app.database.mongodb import db_manager


@pytest.fixture(autouse=True)
def reset_in_memory_db():
    """Resets in-memory databases before each test to guarantee test isolation."""
    db_manager._memory_plans.clear()
    db_manager._memory_campaigns.clear()
    db_manager._memory_jobs.clear()
    db_manager._memory_idempotency.clear()


# =========================================================================
# 1. Plan Creation Tests
# =========================================================================

@pytest.mark.asyncio
async def test_plan_creation():
    planner = AutomationPlanner()
    goal = "Send onboarding outreach sequence to newly registered customers"
    automation_type = "customer_onboarding"
    datasets = [
        {"id": "user_101", "email": "alice@example.com", "name": "Alice"},
        {"id": "user_102", "email": "bob@example.com", "name": "Bob"}
    ]
    constraints = {"rate_limit_per_minute": 100, "max_retries": 3, "timeout_seconds": 15}
    personalization = "Use friendly greeting and highlight standard tier benefits."
    scope = {"requires_manual_approval": False, "dry_run": True, "authorized_roles": ["admin"]}

    plan: AutomationPlan = await planner.create_plan(
        goal=goal,
        automation_type=automation_type,
        input_datasets=datasets,
        execution_constraints=constraints,
        personalization_instructions=personalization,
        approval_authorization_scope=scope,
        persist=True
    )

    assert plan.id is not None
    assert plan.goal == goal
    assert plan.automation_type == automation_type
    assert len(plan.tasks) >= 1
    assert len(plan.required_integrations) >= 1
    assert plan.input_datasets == datasets
    assert plan.estimated_number_of_actions >= len(datasets)
    assert plan.execution_constraints["max_retries"] == 3
    assert plan.personalization_instructions == personalization
    assert plan.approval_authorization_scope["dry_run"] is True
    assert plan.status in [AutomationStatus.READY, AutomationStatus.PLANNING]

    # Verify each task structure
    for task in plan.tasks:
        assert task.id is not None
        assert task.plan_id == plan.id
        assert task.action is not None
        assert task.step_number >= 1

    # Verify persistence
    saved = await db_manager.get_plan(plan.id)
    assert saved is not None
    assert saved["id"] == plan.id
    assert saved["goal"] == goal


# =========================================================================
# 2. Job Creation & Idempotency Key Tests
# =========================================================================

@pytest.mark.asyncio
async def test_job_creation():
    service = CampaignService()
    datasets = [
        {"id": "cust_1", "email": "user1@example.com"},
        {"id": "cust_2", "email": "user2@example.com"},
        {"id": "cust_3", "email": "user3@example.com"}
    ]

    campaign = await service.create_campaign(
        goal="Notify customers of scheduled maintenance window",
        automation_type="maintenance_notice",
        input_datasets=datasets,
        name="Maintenance Notice"
    )

    assert campaign.id is not None
    assert campaign.status == AutomationStatus.READY
    assert campaign.total_jobs >= 3
    assert campaign.pending_jobs == campaign.total_jobs

    jobs = await db_manager.get_jobs_by_campaign(campaign.id)
    assert len(jobs) == campaign.total_jobs

    job_ids = set()
    idempotency_keys = set()

    for job_dict in jobs:
        job = AutomationJob(**job_dict)
        assert job.id not in job_ids, "Each job must have a unique ID"
        job_ids.add(job.id)

        assert job.campaign_id == campaign.id
        assert job.recipient_id in ["cust_1", "cust_2", "cust_3"]
        assert job.status == AutomationStatus.READY
        assert job.attempt_count == 0
        assert job.created_at is not None
        assert job.updated_at is not None
        assert job.result is None
        assert job.error is None
        assert job.idempotency_key is not None
        assert job.idempotency_key.startswith(campaign.id)

        assert job.idempotency_key not in idempotency_keys, "Idempotency keys must be unique per recipient-action pair"
        idempotency_keys.add(job.idempotency_key)


# =========================================================================
# 3. State Transitions Tests
# =========================================================================

@pytest.mark.asyncio
async def test_state_transitions():
    service = CampaignService()
    datasets = [{"id": "user_a", "email": "a@example.com"}]

    # 1. PLANNING -> READY upon campaign creation
    campaign = await service.create_campaign(
        goal="Send automated product release alert",
        automation_type="product_alert",
        input_datasets=datasets
    )
    assert campaign.status == AutomationStatus.READY

    # 2. READY -> RUNNING upon start
    started = await service.start_campaign(campaign.id, run_in_background=False)
    assert started.status in [AutomationStatus.RUNNING, AutomationStatus.COMPLETED]

    # Check that jobs transitioned to COMPLETED
    jobs = await db_manager.get_jobs_by_campaign(campaign.id)
    for j in jobs:
        assert j["status"] == AutomationStatus.COMPLETED.value

    # Campaign status synced to COMPLETED
    status_data = await service.get_campaign_status(campaign.id)
    assert status_data["campaign"]["status"] == AutomationStatus.COMPLETED.value
    assert status_data["campaign"]["completed_jobs"] == len(jobs)
    assert status_data["campaign"]["pending_jobs"] == 0

    # 3. Test PAUSED and RESUMED transitions
    camp_pause = await service.create_campaign(
        goal="Long running survey dispatch",
        automation_type="survey",
        input_datasets=[{"id": f"rec_{i}"} for i in range(10)]
    )
    assert camp_pause.status == AutomationStatus.READY

    paused = await service.pause_campaign(camp_pause.id)
    assert paused.status == AutomationStatus.PAUSED

    resumed = await service.resume_campaign(camp_pause.id, run_in_background=False)
    assert resumed.status in [AutomationStatus.RUNNING, AutomationStatus.COMPLETED]


# =========================================================================
# 4. Retry Mechanism Tests
# =========================================================================

@pytest.mark.asyncio
async def test_retry_service():
    retry = RetryService(initial_interval_seconds=1.0, backoff_factor=2.0, max_interval_seconds=10.0, default_max_retries=3)

    # Exponential backoff verification
    assert retry.calculate_backoff_delay(0) == 1.0
    assert retry.calculate_backoff_delay(1) == 2.0
    assert retry.calculate_backoff_delay(2) == 4.0
    assert retry.calculate_backoff_delay(3) == 8.0
    assert retry.calculate_backoff_delay(4) == 10.0  # Capped at max_interval

    # Retry eligibility
    job = AutomationJob(
        campaign_id="camp_test",
        action="mock_send_email",
        idempotency_key="key_retry_1",
        attempt_count=1,
        max_retries=3
    )
    assert retry.should_retry(job, "Connection timeout") is True

    # When max retries exceeded
    job.attempt_count = 3
    assert retry.should_retry(job, "Connection timeout") is False

    # Process failure to READY vs FAILED
    job_retrying = AutomationJob(
        campaign_id="camp_test",
        action="mock_send_email",
        idempotency_key="key_retry_2",
        attempt_count=1,
        max_retries=3
    )
    updated = retry.process_failure(job_retrying, "Temporary network timeout")
    assert updated.status == AutomationStatus.READY
    assert updated.error == "Temporary network timeout"

    job_exhausted = AutomationJob(
        campaign_id="camp_test",
        action="mock_send_email",
        idempotency_key="key_retry_3",
        attempt_count=3,
        max_retries=3
    )
    failed = retry.process_failure(job_exhausted, "Connection refused")
    assert failed.status == AutomationStatus.FAILED
    assert failed.completed_at is not None


@pytest.mark.asyncio
async def test_executor_with_retry_and_recovery():
    queue = JobQueue()
    executor = DeterministicExecutor()

    # Track execution attempts
    call_counts = {"count": 0}

    async def flaky_tool(recipient_id: str, payload: dict):
        call_counts["count"] += 1
        if call_counts["count"] < 3:
            raise RuntimeError("Transient upstream service timeout")
        return {"success": True, "action": "flaky_tool", "data": "recovered"}

    executor.register_tool("flaky_tool", flaky_tool)

    job = AutomationJob(
        campaign_id="camp_flaky",
        action="flaky_tool",
        recipient_id="rec_flaky",
        idempotency_key="flaky_key_1",
        max_retries=4
    )
    await queue.enqueue(job)

    # Attempt 1: fails, triggers nack -> sets back to READY for retry
    leased_1 = await queue.dequeue(worker_id="w1", campaign_id="camp_flaky")
    assert leased_1 is not None
    assert leased_1.attempt_count == 1
    res1 = await executor.execute_job(leased_1)
    assert res1["success"] is False

    j1 = await db_manager.get_job(job.id)
    assert j1["status"] == AutomationStatus.READY.value

    # Attempt 2: fails again, increments attempt
    leased_2 = await queue.dequeue(worker_id="w2", campaign_id="camp_flaky")
    assert leased_2 is not None
    assert leased_2.attempt_count == 2
    res2 = await executor.execute_job(leased_2)
    assert res2["success"] is False

    j2 = await db_manager.get_job(job.id)
    assert j2["status"] == AutomationStatus.READY.value

    # Attempt 3: succeeds!
    leased_3 = await queue.dequeue(worker_id="w3", campaign_id="camp_flaky")
    assert leased_3 is not None
    assert leased_3.attempt_count == 3
    res3 = await executor.execute_job(leased_3)
    assert res3["success"] is True

    j3 = await db_manager.get_job(job.id)
    assert j3["status"] == AutomationStatus.COMPLETED.value
    assert j3["result"]["data"] == "recovered"


# =========================================================================
# 5. Idempotency Protection Tests
# =========================================================================

@pytest.mark.asyncio
async def test_idempotency_protection():
    queue = JobQueue()
    executor = DeterministicExecutor()

    send_counter = {"dispatches": 0}

    async def counting_sender(recipient: str, payload: dict):
        send_counter["dispatches"] += 1
        return {
            "success": True,
            "action": "counting_sender",
            "recipient": recipient,
            "dispatch_id": f"DISPATCH_{send_counter['dispatches']}"
        }

    executor.register_tool("counting_sender", counting_sender)

    job_1 = AutomationJob(
        campaign_id="camp_idemp",
        action="counting_sender",
        recipient_id="user_target@example.com",
        idempotency_key="camp_idemp:counting_sender:user_target@example.com"
    )
    await queue.enqueue(job_1)

    # First execution: tool is actually invoked
    leased = await queue.dequeue(worker_id="worker_1", campaign_id="camp_idemp")
    result_1 = await executor.execute_job(leased)

    assert result_1["success"] is True
    assert result_1["idempotent"] is False
    assert result_1["cached"] is False
    assert result_1["result"]["dispatch_id"] == "DISPATCH_1"
    assert send_counter["dispatches"] == 1

    # Simulate duplicate queue delivery / worker retry with identical idempotency key
    job_duplicate = AutomationJob(
        campaign_id="camp_idemp",
        action="counting_sender",
        recipient_id="user_target@example.com",
        idempotency_key="camp_idemp:counting_sender:user_target@example.com"
    )
    await queue.enqueue(job_duplicate)

    leased_dup = await queue.dequeue(worker_id="worker_2", campaign_id="camp_idemp")
    result_2 = await executor.execute_job(leased_dup)

    # Idempotency triggered! Tool was NOT executed again
    assert result_2["success"] is True
    assert result_2["idempotent"] is True
    assert result_2["cached"] is True
    assert result_2["result"]["dispatch_id"] == "DISPATCH_1"
    assert send_counter["dispatches"] == 1, "Crucial: Recipient must NEVER receive the same action twice!"


# =========================================================================
# 6. Cancellation Tests
# =========================================================================

@pytest.mark.asyncio
async def test_cancellation():
    service = CampaignService()
    datasets = [{"id": f"cust_{i}"} for i in range(5)]

    campaign = await service.create_campaign(
        goal="Send promotional discount announcement",
        automation_type="promo_broadcast",
        input_datasets=datasets
    )
    assert campaign.status == AutomationStatus.READY

    # Cancel campaign before processing
    cancelled = await service.cancel_campaign(campaign.id)
    assert cancelled is not None
    assert cancelled.status == AutomationStatus.CANCELLED

    # Verify all jobs in DB are marked CANCELLED
    jobs = await db_manager.get_jobs_by_campaign(campaign.id)
    for j in jobs:
        assert j["status"] == AutomationStatus.CANCELLED.value

    # Queue should return None for this campaign
    queue = JobQueue()
    leased = await queue.dequeue(worker_id="w_cancel", campaign_id=campaign.id)
    assert leased is None


# =========================================================================
# 7. Restart and Resume Behavior Tests
# =========================================================================

@pytest.mark.asyncio
async def test_restart_resume_resilience():
    service = CampaignService()
    datasets = [
        {"id": "customer_1", "email": "c1@example.com"},
        {"id": "customer_2", "email": "c2@example.com"},
        {"id": "customer_3", "email": "c3@example.com"}
    ]

    campaign = await service.create_campaign(
        goal="Sync account statuses",
        automation_type="sync_accounts",
        input_datasets=datasets
    )

    queue = JobQueue()

    # Worker 1 leases job 1
    job_1 = await queue.dequeue(worker_id="worker_crashed", lease_duration_seconds=0, campaign_id=campaign.id)
    assert job_1 is not None
    assert job_1.status == AutomationStatus.RUNNING

    # Worker 1 finishes job 1
    from app.automation.executor import executor
    await executor.execute_job(job_1)
    j1_db = await db_manager.get_job(job_1.id)
    assert j1_db["status"] == AutomationStatus.COMPLETED.value

    # Worker 2 leases job 2, but worker 2 CRASHES mid-execution!
    job_2 = await queue.dequeue(worker_id="worker_crashed_2", lease_duration_seconds=0, campaign_id=campaign.id)
    assert job_2 is not None
    assert job_2.status == AutomationStatus.RUNNING
    # Note: lease_duration_seconds was 0, so lease is already expired!

    # Simulate backend/worker crash & restart!
    # On startup, recover_from_restart is called
    recovered_count = await service.recover_from_restart()
    assert recovered_count >= 1

    # The orphaned job_2 was restored to READY
    j2_db = await db_manager.get_job(job_2.id)
    assert j2_db["status"] == AutomationStatus.READY.value

    # Now resume campaign processing
    resumed_camp = await service.resume_campaign(campaign.id, run_in_background=False)
    assert resumed_camp.status in [AutomationStatus.RUNNING, AutomationStatus.COMPLETED]

    # Verify all jobs are now COMPLETED
    all_jobs = await db_manager.get_jobs_by_campaign(campaign.id)
    for j in all_jobs:
        assert j["status"] == AutomationStatus.COMPLETED.value

    status_data = await service.get_campaign_status(campaign.id)
    assert status_data["campaign"]["status"] == AutomationStatus.COMPLETED.value
    assert status_data["campaign"]["completed_jobs"] == len(all_jobs)
    assert status_data["campaign"]["pending_jobs"] == 0
