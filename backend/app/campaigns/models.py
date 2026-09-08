from datetime import datetime, timezone
from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, model_validator
import uuid

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class CampaignType(str, Enum):
    INVESTOR_OUTREACH = "INVESTOR_OUTREACH"
    JOB_OUTREACH = "JOB_OUTREACH"
    INTERNSHIP_OUTREACH = "INTERNSHIP_OUTREACH"
    CUSTOM_OUTREACH = "CUSTOM_OUTREACH"

class CommunicationChannel(str, Enum):
    EMAIL = "EMAIL"
    LINKEDIN = "LINKEDIN"

class CampaignStatus(str, Enum):
    DRAFT = "DRAFT"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

class CampaignPlan(BaseModel):
    campaign_type: CampaignType
    audience: List[str] = Field(default_factory=list, description="Target contact roles e.g. ['INVESTOR', 'VC']")
    channel: CommunicationChannel = CommunicationChannel.EMAIL
    objective: str
    message_strategy: str
    personalization_fields: List[str] = Field(default_factory=lambda: ["first_name", "company"])
    suggested_subject_line: Optional[str] = None
    tone: Optional[str] = "professional"
    call_to_action: Optional[str] = None

class Campaign(BaseModel):
    campaign_id: str = Field(default_factory=lambda: f"camp_{uuid.uuid4().hex[:8]}")
    id: Optional[str] = None  # Alias for compatibility with db_manager
    owner_id: str = "default_user"
    name: str
    objective: str
    audience: List[str] = Field(default_factory=list)
    dataset_id: Optional[str] = None
    communication_channel: CommunicationChannel = CommunicationChannel.EMAIL
    message_strategy: str
    personalization_fields: List[str] = Field(default_factory=list)
    plan: Optional[CampaignPlan] = None
    status: CampaignStatus = CampaignStatus.READY
    total_contacts: int = 0
    pending: int = 0
    generated: int = 0
    queued: int = 0
    sent: int = 0
    failed: int = 0
    replied: int = 0
    #: Execution settings and run bookkeeping written by the execution engine
    #: (dry_run, rate_per_minute, concurrency, sender_profile, startup_info,
    #: started_at, pause_reason). Kept as a free-form dict so the engine can
    #: evolve without a schema migration on existing campaign documents.
    execution: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def sync_id(self) -> "Campaign":
        if not self.id:
            self.id = self.campaign_id
        return self

class PlanCampaignRequest(BaseModel):
    goal: str
    dataset_id: Optional[str] = None
    name: Optional[str] = None
    owner_id: Optional[str] = "default_user"
    channel: Optional[CommunicationChannel] = CommunicationChannel.EMAIL

class PlanCampaignResponse(BaseModel):
    plan: CampaignPlan
    campaign: Campaign


# ==========================================================================
# Execution API models
# ==========================================================================


class StartCampaignRequest(BaseModel):
    """
    Options for one campaign run.

    `dry_run` generates every message and records exactly what would have been
    sent, without contacting the provider.

    `user_id` identifies the principal the run acts for. When present, the run
    is checked against that user's stored permission grants once, before any job
    is created. In a deployment with authentication this would come from the
    session rather than the request body.
    """

    user_id: Optional[str] = None
    dry_run: bool = False
    rate_per_minute: float = Field(default=60.0, gt=0, le=10000)
    concurrency: int = Field(default=4, ge=1, le=64)
    max_attempts: int = Field(default=3, ge=1, le=10)
    sender_profile: Optional[Dict[str, Any]] = None
    startup_info: Optional[Dict[str, Any]] = None
    allow_direct_execution: bool = False
    campaign_limits: Optional[Dict[str, Any]] = None


class ResumeCampaignRequest(BaseModel):
    user_id: Optional[str] = None
    rate_per_minute: Optional[float] = Field(default=None, gt=0, le=10000)
    concurrency: Optional[int] = Field(default=None, ge=1, le=64)


class CampaignJobView(BaseModel):
    """
    A job as returned by the API.

    Carries the generated message, the provider response, the attempt count and
    the failure reason, so an operator can see exactly what happened to one
    recipient without reading the database.
    """

    id: str
    campaign_id: str
    contact_id: str
    channel: str
    action: str
    idempotency_key: str
    status: str
    to_email: Optional[str] = None
    #: A few display fields lifted from the recipient snapshot, so a job row can
    #: show who the message is for without a second lookup. The rest of the
    #: snapshot stays server-side.
    recipient_name: Optional[str] = None
    recipient_company: Optional[str] = None
    generated_subject: Optional[str] = None
    generated_body: Optional[str] = None
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
    requires_manual_review: bool = False
    dry_run: bool = False
    next_attempt_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    generated_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class PaginatedCampaignJobs(BaseModel):
    items: List[CampaignJobView]
    campaign_id: str
    status_filter: Optional[str] = None
    page: int = 1
    page_size: int = 50
    returned: int = 0
