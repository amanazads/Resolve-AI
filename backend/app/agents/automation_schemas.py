"""
Structured outputs for the autonomous automation workflow.

Every step of the workflow produces one of these models. Two consequences that
matter more than the type-safety:

  * The LLM's output is parsed into a schema or discarded. It cannot smuggle a
    free-text claim like "the emails were sent" into the state.
  * The fields the LLM is allowed to fill are exactly the interpretive ones --
    what the user meant, which audience, what tone. Counts, provider responses
    and authorization verdicts live on models the LLM never populates; those are
    filled from the database and from the campaign worker.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.automation.models import utc_now


# ==========================================================================
# Workflow phases
# ==========================================================================


class AutomationPhase(str, Enum):
    """
    Where a run has got to. Persisted, and used to resume a run in the right
    place after a restart.
    """

    USER_REQUEST = "USER_REQUEST"
    UNDERSTAND_GOAL = "UNDERSTAND_GOAL"
    CHECK_AVAILABLE_DATA = "CHECK_AVAILABLE_DATA"
    CREATE_PLAN = "CREATE_PLAN"
    VALIDATE_PLAN = "VALIDATE_PLAN"
    PERSONALIZE = "PERSONALIZE"
    CHECK_AUTHORIZATION = "CHECK_AUTHORIZATION"
    CREATE_JOBS = "CREATE_JOBS"
    EXECUTE = "EXECUTE"
    VERIFY_RESULT = "VERIFY_RESULT"
    PERSIST_STATE = "PERSIST_STATE"
    COMPLETE = "COMPLETE"
    AWAITING_COMPLETION = "AWAITING_COMPLETION"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class WorkflowDecision(str, Enum):
    """The terminal routing decision after verification."""

    CONTINUE = "CONTINUE"    # work is still in flight; resume this run later
    RETRY = "RETRY"          # something recoverable failed; run execution again
    COMPLETE = "COMPLETE"    # every job reached a terminal state
    BLOCKED = "BLOCKED"      # cannot proceed without a human


# ==========================================================================
# 1. UNDERSTAND_GOAL  (LLM-interpreted)
# ==========================================================================


class GoalAnalysis(BaseModel):
    """
    What the user asked for, as understood by the LLM.

    Interpretation only. Nothing here is treated as fact about the system's
    data, its authorization, or what has been sent.
    """

    is_automation_request: bool = Field(
        default=False,
        description="True when the user is asking for a bulk outreach campaign.",
    )
    automation_type: str = Field(default="outreach")
    campaign_type: str = Field(
        default="CUSTOM_OUTREACH",
        description="INVESTOR_OUTREACH | JOB_OUTREACH | INTERNSHIP_OUTREACH | CUSTOM_OUTREACH",
    )
    objective: str = Field(default="", description="One sentence describing the goal.")
    audience: List[str] = Field(
        default_factory=list,
        description="Contact types to target, e.g. ['INVESTOR', 'VC'].",
    )
    channel: str = Field(default="EMAIL")
    dataset_hint: Optional[str] = Field(
        default=None, description="Dataset the user named, if any."
    )
    requested_dry_run: bool = Field(
        default=False, description="True when the user asked for a preview or test run."
    )
    tone: str = Field(default="professional")
    constraints: Dict[str, Any] = Field(
        default_factory=dict, description="e.g. {'rate_per_minute': 30}"
    )
    ambiguities: List[str] = Field(
        default_factory=list,
        description="Things the user left unclear that a human should confirm.",
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning: str = ""


# ==========================================================================
# 2. CHECK_AVAILABLE_DATA  (deterministic; counts come from the database)
# ==========================================================================


class DatasetAssessment(BaseModel):
    """
    What data actually exists. Every number here is counted from MongoDB.

    The LLM never fills this in: an invented recipient count would flow straight
    into how many messages get sent.
    """

    dataset_id: Optional[str] = None
    dataset_found: bool = False
    total_contacts: int = 0
    valid_contacts: int = 0
    matched_contacts: int = 0
    audience_breakdown: Dict[str, int] = Field(default_factory=dict)
    contacts_missing_email: int = 0
    data_gaps: List[str] = Field(default_factory=list)
    sufficient: bool = False
    reason: str = ""


# ==========================================================================
# 3. CREATE_PLAN  (LLM-drafted)
# ==========================================================================


class PlannedStep(BaseModel):
    order: int = 1
    action: str = Field(description="Must name an approved action; anything else is rejected.")
    description: str = ""
    requires_authorization: bool = True


class CampaignPlanDraft(BaseModel):
    """The LLM's proposed plan. Nothing acts on it until it has been validated."""

    campaign_type: str = "CUSTOM_OUTREACH"
    objective: str = ""
    audience: List[str] = Field(default_factory=list)
    channel: str = "EMAIL"
    message_strategy: str = ""
    tone: str = "professional"
    personalization_fields: List[str] = Field(default_factory=list)
    suggested_subject_line: Optional[str] = None
    call_to_action: Optional[str] = None
    steps: List[PlannedStep] = Field(default_factory=list)
    estimated_recipients: int = 0
    rate_per_minute: float = 60.0
    max_attempts: int = 3
    requires_human_approval: bool = True
    notes: str = ""


# ==========================================================================
# 4. VALIDATE_PLAN  (deterministic)
# ==========================================================================


class PlanValidation(BaseModel):
    """
    Result of running the plan through the guardrails. Produced by code, never
    by the model, because it is the thing that decides whether the model's plan
    is allowed to touch anything.
    """

    is_valid: bool = False
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    approved_actions: List[str] = Field(default_factory=list)
    rejected_actions: List[str] = Field(default_factory=list)


# ==========================================================================
# 5. PERSONALIZE  (LLM-generated content, deterministically validated)
# ==========================================================================


class PersonalizationSpec(BaseModel):
    """
    The message settings for the run, plus one real generated sample.

    The sample exists so that whoever authorizes the campaign is approving an
    actual message rather than a description of one. Bulk generation happens in
    the campaign worker, one job at a time.
    """

    allowed_fields: List[str] = Field(default_factory=list)
    tone: str = "professional"
    instructions: str = ""
    sample_recipient: Dict[str, Any] = Field(default_factory=dict)
    sample_subject: Optional[str] = None
    sample_body: Optional[str] = None
    validation_status: str = "UNKNOWN"
    warnings: List[str] = Field(default_factory=list)
    generated: bool = False


# ==========================================================================
# 6. CHECK_AUTHORIZATION  (deterministic; the LLM has no vote)
# ==========================================================================


class AuthorizationDecision(BaseModel):
    """
    Whether this run may send.

    Decided from the caller-supplied authorization scope -- which arrives with
    the request, from an authenticated principal -- and from the guardrails.
    The LLM cannot produce, influence or override this model.
    """

    authorized: bool = False
    decision: str = "DENIED"  # GRANTED | DENIED | PENDING_HUMAN_APPROVAL
    dry_run_only: bool = False
    granted_by: Optional[str] = None
    #: Where the authorization came from: a permission grant the user stored
    #: earlier, or an approval supplied with this request.
    source: str = "request_scope"
    grant_id: Optional[str] = None
    scope: Dict[str, Any] = Field(default_factory=dict)
    reasons: List[str] = Field(default_factory=list)
    requires_human_approval: bool = True
    decided_at: datetime = Field(default_factory=utc_now)


# ==========================================================================
# 7. CREATE_JOBS / 8. EXECUTE  (facts from the queue and the worker)
# ==========================================================================


class JobCreationResult(BaseModel):
    campaign_id: Optional[str] = None
    jobs_created: int = 0
    already_existing: int = 0
    total_recipients: int = 0
    dry_run: bool = False
    detail: str = ""


class ExecutionHandoff(BaseModel):
    """
    Records that the run handed the work to the persistent campaign worker.

    The graph does not send anything itself. A thousand-recipient campaign is a
    thousand worker jobs, not a thousand steps inside one graph invocation.
    """

    campaign_id: Optional[str] = None
    handed_off: bool = False
    dry_run: bool = False
    rate_per_minute: float = 60.0
    concurrency: int = 4
    detail: str = ""


# ==========================================================================
# 9. VERIFY_RESULT  (provider confirmations only)
# ==========================================================================


class VerificationResult(BaseModel):
    """
    What actually happened, read back from the persisted jobs.

    `provider_confirmed` counts only jobs the provider accepted with a message
    id. A job is never counted as delivered because a model said so.
    """

    campaign_id: Optional[str] = None
    total: int = 0
    sent: int = 0
    provider_confirmed: int = 0
    failed: int = 0
    skipped: int = 0
    cancelled: int = 0
    remaining: int = 0
    requires_manual_review: int = 0
    percent_complete: float = 0.0
    dry_run: bool = False
    verdict: WorkflowDecision = WorkflowDecision.CONTINUE
    reasons: List[str] = Field(default_factory=list)


# ==========================================================================
# Persisted run record
# ==========================================================================


class AutomationRunRecord(BaseModel):
    """
    The durable record of one automation run.

    This, not the in-process graph, is what makes the workflow resumable: a run
    can be reloaded in a different process after a restart and continued from
    the phase it reached.
    """

    run_id: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    user_message: str = ""
    phase: str = AutomationPhase.USER_REQUEST.value
    decision: Optional[str] = None
    campaign_id: Optional[str] = None

    goal_analysis: Optional[Dict[str, Any]] = None
    dataset_assessment: Optional[Dict[str, Any]] = None
    plan_draft: Optional[Dict[str, Any]] = None
    plan_validation: Optional[Dict[str, Any]] = None
    personalization: Optional[Dict[str, Any]] = None
    authorization: Optional[Dict[str, Any]] = None
    job_creation: Optional[Dict[str, Any]] = None
    execution: Optional[Dict[str, Any]] = None
    verification: Optional[Dict[str, Any]] = None

    authorization_scope: Dict[str, Any] = Field(default_factory=dict)
    retry_count: int = 0
    errors: List[str] = Field(default_factory=list)
    response: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
