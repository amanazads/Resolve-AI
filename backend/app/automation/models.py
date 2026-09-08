from datetime import datetime, timezone
from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
import uuid

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class AutomationStatus(str, Enum):
    PLANNING = "PLANNING"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

class AutomationTask(BaseModel):
    id: str = Field(default_factory=lambda: f"task_{uuid.uuid4().hex[:8]}")
    plan_id: Optional[str] = None
    name: str
    action: str
    step_number: int = 1
    parameters: Dict[str, Any] = Field(default_factory=dict)
    dependencies: List[str] = Field(default_factory=list)
    status: AutomationStatus = AutomationStatus.READY
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

class AutomationPlan(BaseModel):
    id: str = Field(default_factory=lambda: f"plan_{uuid.uuid4().hex[:8]}")
    goal: str
    automation_type: str = "general"
    tasks: List[AutomationTask] = Field(default_factory=list)
    required_integrations: List[str] = Field(default_factory=list)
    input_datasets: List[Dict[str, Any]] = Field(default_factory=list)
    estimated_number_of_actions: int = 0
    execution_constraints: Dict[str, Any] = Field(default_factory=dict)
    personalization_instructions: Optional[str] = None
    approval_authorization_scope: Dict[str, Any] = Field(default_factory=dict)
    status: AutomationStatus = AutomationStatus.PLANNING
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

class AutomationJob(BaseModel):
    id: str = Field(default_factory=lambda: f"job_{uuid.uuid4().hex[:8]}")
    campaign_id: str
    task_id: Optional[str] = None
    recipient_id: Optional[str] = None
    action: str
    status: AutomationStatus = AutomationStatus.READY
    attempt_count: int = 0
    max_retries: int = 3
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    idempotency_key: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    lease_owner: Optional[str] = None
    lease_expires_at: Optional[datetime] = None

class Campaign(BaseModel):
    id: str = Field(default_factory=lambda: f"camp_{uuid.uuid4().hex[:8]}")
    name: str
    goal: str
    automation_type: str = "general"
    plan_id: str
    status: AutomationStatus = AutomationStatus.READY
    total_jobs: int = 0
    completed_jobs: int = 0
    failed_jobs: int = 0
    pending_jobs: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    metadata: Dict[str, Any] = Field(default_factory=dict)

class IdempotencyRecord(BaseModel):
    idempotency_key: str
    campaign_id: str
    recipient_id: Optional[str] = None
    action: str
    status: str = "COMPLETED"
    result: Optional[Dict[str, Any]] = None
    created_at: datetime = Field(default_factory=utc_now)


# ==========================================================================
# Campaign execution engine
#
# The models above drive the generic automation planner. The models below drive
# the campaign execution engine, which has its own lifecycle: one job is one
# recipient action, and its states describe an outbound message rather than a
# generic task.
# ==========================================================================


class JobStatus(str, Enum):
    """
    Lifecycle of a single recipient action.

        PENDING       enqueued, not yet picked up
        GENERATING    a worker holds it and is composing the message
        READY         message generated and validated, waiting to be sent
        SENDING       a worker holds it and the provider call is in flight
        SENT          provider confirmed acceptance (terminal)
        FAILED        permanently failed (terminal)
        RETRY_PENDING transient failure, waiting for next_attempt_at
        SKIPPED       deliberately not sent, e.g. no address (terminal)
        CANCELLED     campaign cancelled before this job ran (terminal)
    """

    PENDING = "PENDING"
    GENERATING = "GENERATING"
    READY = "READY"
    SENDING = "SENDING"
    SENT = "SENT"
    FAILED = "FAILED"
    RETRY_PENDING = "RETRY_PENDING"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


#: Once a job reaches one of these it is never claimed again.
TERMINAL_JOB_STATUSES = frozenset(
    {JobStatus.SENT, JobStatus.FAILED, JobStatus.SKIPPED, JobStatus.CANCELLED}
)

#: States a worker may claim from.
CLAIMABLE_JOB_STATUSES = frozenset(
    {JobStatus.PENDING, JobStatus.READY, JobStatus.RETRY_PENDING}
)

#: States that mean a worker is actively holding the job.
IN_FLIGHT_JOB_STATUSES = frozenset({JobStatus.GENERATING, JobStatus.SENDING})


class JobAttempt(BaseModel):
    """One recorded attempt, kept as an audit trail on the job."""

    attempt: int
    status: str
    at: datetime = Field(default_factory=utc_now)
    worker_id: Optional[str] = None
    provider: Optional[str] = None
    provider_status: Optional[str] = None
    message_id: Optional[str] = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    retry_after_seconds: Optional[int] = None


class CampaignJob(BaseModel):
    """
    One recipient action. The unit of work the execution engine schedules,
    leases, retries and reports on.

    `idempotency_key` is campaign_id + contact_id + channel + action, which is
    what makes a recipient action unique across enqueue, retry and restart.
    """

    id: str = Field(default_factory=lambda: f"cjob_{uuid.uuid4().hex[:12]}")
    campaign_id: str
    contact_id: str
    channel: str = "EMAIL"
    action: str = "SEND_MESSAGE"
    idempotency_key: str

    status: JobStatus = JobStatus.PENDING

    # Recipient snapshot, taken at enqueue time so a later contact edit cannot
    # silently change what an in-flight campaign sends.
    recipient: Dict[str, Any] = Field(default_factory=dict)
    to_email: Optional[str] = None

    # Requirement 14: the generated message.
    generated_subject: Optional[str] = None
    generated_body: Optional[str] = None
    generation_metadata: Dict[str, Any] = Field(default_factory=dict)

    # Requirement 14: provider response, message id, attempt count, failure reason.
    provider: Optional[str] = None
    provider_status: Optional[str] = None
    provider_response: Dict[str, Any] = Field(default_factory=dict)
    message_id: Optional[str] = None
    thread_id: Optional[str] = None

    attempt_count: int = 0
    max_attempts: int = 3
    failure_reason: Optional[str] = None
    failure_code: Optional[str] = None
    permanent_failure: bool = False
    attempts: List[JobAttempt] = Field(default_factory=list)

    # Scheduling and concurrency control.
    next_attempt_at: Optional[datetime] = None
    lease_owner: Optional[str] = None
    lease_expires_at: Optional[datetime] = None

    dry_run: bool = False
    #: Set when a job cannot be safely retried automatically (see the worker's
    #: interrupted-send recovery) and a human should decide.
    requires_manual_review: bool = False

    # Requirement 14: timestamps.
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    generated_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    @staticmethod
    def build_idempotency_key(
        campaign_id: str, contact_id: str, channel: str, action: str
    ) -> str:
        """campaign_id + contact_id + channel + action uniquely identifies an action."""
        return f"{campaign_id}:{contact_id}:{channel}:{action}"

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_JOB_STATUSES


class CampaignProgress(BaseModel):
    """Persisted, queryable progress for one campaign."""

    campaign_id: str
    status: str
    dry_run: bool = False
    total: int = 0
    pending: int = 0
    generating: int = 0
    ready: int = 0
    sending: int = 0
    sent: int = 0
    failed: int = 0
    retry_pending: int = 0
    skipped: int = 0
    cancelled: int = 0
    requires_manual_review: int = 0
    in_flight: int = 0
    completed: int = 0
    remaining: int = 0
    percent_complete: float = 0.0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=utc_now)
    last_error: Optional[str] = None
