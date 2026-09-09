"""
Task-first execution models for Resolve AI.

Resolve AI is an autonomous AI agent capable of turning natural-language objectives
into executable workflows. Tasks represent any user-directed unit of work, whether:
- Single email ("Send an email to Aman")
- Multiple emails ("Send emails to Aman and Ujjwal")
- Batch data processing ("Read this CSV and email the founders")
- Multi-file reasoning ("Read my resume and the job listings...")
- Clarification dialogues ("Which Aman do you want to contact?")
- Support inquiries ("What is your refund policy?")
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TaskType(str, Enum):
    SINGLE_ACTION = "SINGLE_ACTION"
    MULTI_ACTION = "MULTI_ACTION"
    DOCUMENT_ANALYSIS = "DOCUMENT_ANALYSIS"
    OUTREACH = "OUTREACH"
    GENERAL_AUTOMATION = "GENERAL_AUTOMATION"
    CUSTOMER_SUPPORT = "CUSTOMER_SUPPORT"
    CAMPAIGN = "CAMPAIGN"
    BATCH_ACTION = "BATCH_ACTION"
    MULTI_FILE_REASONING = "MULTI_FILE_REASONING"
    RECURRING_ACTION = "RECURRING_ACTION"
    LONG_RUNNING_TASK = "LONG_RUNNING_TASK"
    GENERAL = "GENERAL"


class TaskStatus(str, Enum):
    DRAFT = "DRAFT"
    UNDERSTANDING = "UNDERSTANDING"
    INSPECTING = "INSPECTING"
    WAITING_FOR_CLARIFICATION = "WAITING_FOR_CLARIFICATION"
    WAITING_FOR_USER = "WAITING_FOR_CLARIFICATION"  # True alias
    PLANNING = "PLANNING"
    PLAN_READY = "PLAN_READY"
    WAITING_FOR_AUTHORIZATION = "WAITING_FOR_AUTHORIZATION"
    AUTHORIZED = "AUTHORIZED"
    QUEUED = "QUEUED"
    READY = "READY"
    RUNNING = "RUNNING"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    PAUSED = "PAUSED"
    VERIFYING = "VERIFYING"
    CANCELLING = "CANCELLING"
    CANCELLED = "CANCELLED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"



class ActionType(str, Enum):
    SEND_EMAIL = "SEND_EMAIL"
    READ_FILE = "READ_FILE"
    READ_DOCUMENT = "READ_DOCUMENT"
    READ_DATASET = "READ_DATASET"
    SEARCH_CONTACTS = "SEARCH_CONTACTS"
    FILTER_CONTACTS = "FILTER_CONTACTS"
    SELECT_CONTACT = "SELECT_CONTACT"
    GENERATE_MESSAGES = "GENERATE_MESSAGES"
    GENERATE_MESSAGE = "GENERATE_MESSAGE"
    WEB_SEARCH = "WEB_SEARCH"
    CODE_EXEC = "CODE_EXEC"
    RAG_QUERY = "RAG_QUERY"
    CUSTOMER_QUERY = "CUSTOMER_QUERY"
    ORDER_QUERY = "ORDER_QUERY"
    CREATE_FOLLOW_UP = "CREATE_FOLLOW_UP"


class ActionStatus(str, Enum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRY_PENDING = "RETRY_PENDING"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class TaskAction(BaseModel):
    action_id: str = Field(default_factory=lambda: f"act_{uuid.uuid4().hex[:8]}")
    task_id: Optional[str] = None
    action_type: ActionType
    description: str = ""
    parameters: Dict[str, Any] = Field(default_factory=dict)
    normalized_target: Optional[Dict[str, Any]] = None
    generated_content: Optional[Dict[str, Any]] = None
    requires_authorization: bool = False
    has_side_effects: bool = False
    retryable: bool = False
    dependencies: List[str] = Field(default_factory=list)
    status: ActionStatus = ActionStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    idempotency_key: Optional[str] = None
    provider: Optional[str] = None
    provider_message_id: Optional[str] = None
    attempts: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: Optional[datetime] = None


class TaskArtifact(BaseModel):
    artifact_id: str = Field(default_factory=lambda: f"art_{uuid.uuid4().hex[:8]}")
    attachment_id: Optional[str] = None  # Backward compatibility alias
    filename: str
    file_type: str  # csv, xlsx, pdf, docx, txt, json, md, etc.
    mime_type: Optional[str] = None
    size_bytes: int = 0
    sha256_hash: Optional[str] = None
    extracted_text: Optional[str] = None
    structured_data: Optional[Any] = None
    parsed_data: Optional[Any] = None  # Backward compatibility alias
    source: str = "upload"
    task_id: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    uploaded_at: datetime = Field(default_factory=utc_now)
    created_at: datetime = Field(default_factory=utc_now)

    def model_post_init(self, __context: Any) -> None:
        if not self.attachment_id:
            self.attachment_id = self.artifact_id
        if self.parsed_data is not None and self.structured_data is None:
            self.structured_data = self.parsed_data
        elif self.structured_data is not None and self.parsed_data is None:
            self.parsed_data = self.structured_data


# Backward-compatible alias
TaskAttachment = TaskArtifact


class TaskClarification(BaseModel):
    questions: List[str] = Field(default_factory=list)
    options: Optional[List[Dict[str, Any]]] = None
    missing_fields: List[str] = Field(default_factory=list)
    round: int = 1
    answered: bool = False
    user_response: Optional[str] = None


class TaskAuthorization(BaseModel):
    required: bool = False
    authorized: bool = False
    scope: Dict[str, Any] = Field(default_factory=dict)
    grant_id: Optional[str] = None
    authorized_by: Optional[str] = None
    reasons: List[str] = Field(default_factory=list)


class ExecutionResult(BaseModel):
    success: bool = True
    action_id: str
    provider: Optional[str] = None
    provider_message_id: Optional[str] = None
    status: str = "SUCCEEDED"
    data: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    timestamp: datetime = Field(default_factory=utc_now)


class ExecutionPlanStep(BaseModel):
    step_number: int
    action_type: str
    description: str
    requires_authorization: bool = False
    completed: bool = False


class ExecutionPlan(BaseModel):
    summary: str
    steps: List[ExecutionPlanStep] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    required_tools: List[str] = Field(default_factory=list)
    required_integrations: List[str] = Field(default_factory=list)
    required_information: List[str] = Field(default_factory=list)
    authorization_requirements: List[str] = Field(default_factory=list)
    estimated_actions: int = 0
    requires_user_approval: bool = False


# Alias TaskPlan to ExecutionPlan
TaskPlan = ExecutionPlan


class TaskJob(BaseModel):
    """
    Granular action job underneath an ExecutionTask.
    Ensures idempotency, auditability, and retry support.
    """
    job_id: str = Field(default_factory=lambda: f"tjob_{uuid.uuid4().hex[:8]}")
    task_id: str
    action_id: str
    action_type: ActionType
    status: ActionStatus = ActionStatus.PENDING
    parameters: Dict[str, Any] = Field(default_factory=dict)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    retryable: bool = False
    attempt_count: int = 0
    max_attempts: int = 3
    idempotency_key: str
    provider: Optional[str] = None
    provider_message_id: Optional[str] = None
    lease_owner: Optional[str] = None
    lease_expires_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: Optional[datetime] = None


# Alias ExecutionJob to TaskJob
ExecutionJob = TaskJob


class TaskEventType(str, Enum):
    TASK_CREATED = "TASK_CREATED"
    FILES_INSPECTED = "FILES_INSPECTED"
    FILE_ATTACHED = "FILE_ATTACHED"
    FILE_PARSED = "FILE_PARSED"
    GOAL_UNDERSTOOD = "GOAL_UNDERSTOOD"
    PLAN_CREATED = "PLAN_CREATED"
    PLAN_VALIDATED = "PLAN_VALIDATED"
    CLARIFICATION_REQUESTED = "CLARIFICATION_REQUESTED"
    CLARIFICATION_RECEIVED = "CLARIFICATION_RECEIVED"
    USER_RESPONSE_RECEIVED = "USER_RESPONSE_RECEIVED"
    AUTHORIZATION_REQUESTED = "AUTHORIZATION_REQUESTED"
    AUTHORIZATION_GRANTED = "AUTHORIZATION_GRANTED"
    AUTHORIZATION_REVOKED = "AUTHORIZATION_REVOKED"
    ACTION_CREATED = "ACTION_CREATED"
    ACTION_QUEUED = "ACTION_QUEUED"
    ACTION_STARTED = "ACTION_STARTED"
    MESSAGE_GENERATED = "MESSAGE_GENERATED"
    MESSAGE_SENT = "MESSAGE_SENT"
    MESSAGE_FAILED = "MESSAGE_FAILED"
    ACTION_SUCCEEDED = "ACTION_SUCCEEDED"
    ACTION_FAILED = "ACTION_FAILED"
    ACTION_RETRIED = "ACTION_RETRIED"
    TASK_PAUSED = "TASK_PAUSED"
    TASK_RESUMED = "TASK_RESUMED"
    TASK_CANCELLED = "TASK_CANCELLED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"


class TaskEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:8]}")
    task_id: str
    event_type: TaskEventType
    message: str
    data: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=utc_now)


class ExecutionTask(BaseModel):
    """
    The universal root task abstraction for Resolve AI.
    """
    task_id: str = Field(default_factory=lambda: f"task_{uuid.uuid4().hex[:10]}")
    id: Optional[str] = None  # Primary ID alias
    user_id: str = "default_user"
    objective: str
    status: TaskStatus = TaskStatus.UNDERSTANDING
    task_type: TaskType = TaskType.GENERAL

    # Rich contextual payload & artifacts
    context: Dict[str, Any] = Field(default_factory=dict)
    attachments: List[TaskArtifact] = Field(default_factory=list)
    artifacts: List[TaskArtifact] = Field(default_factory=list)

    # Clarification dialogue & interactive flow
    required_information: List[str] = Field(default_factory=list)
    clarification_questions: List[str] = Field(default_factory=list)
    clarification_options: Optional[List[Dict[str, Any]]] = None
    clarification: Optional[TaskClarification] = None

    # Planning & actions
    execution_plan: Optional[ExecutionPlan] = None
    plan: Optional[ExecutionPlan] = None  # Plan alias
    current_step: int = 0
    actions: List[TaskAction] = Field(default_factory=list)
    results: List[Dict[str, Any]] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)

    # Multi-turn conversation persistence
    conversation_history: List[Dict[str, Any]] = Field(default_factory=list)

    # Scoped authorization & dry run flags
    authorization_scope: Dict[str, Any] = Field(default_factory=dict)
    authorization: Optional[TaskAuthorization] = None
    authorizations: Optional[List[Dict[str, Any]]] = None  # Authorizations alias
    execution_state: Optional[Dict[str, Any]] = None
    dry_run: bool = False

    # Event audit trail
    events: List[Dict[str, Any]] = Field(default_factory=list)

    # Optional linkage to legacy bulk campaign
    campaign_id: Optional[str] = None

    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: Optional[datetime] = None

    def model_post_init(self, __context: Any) -> None:
        if not self.id:
            self.id = self.task_id
        if self.execution_plan and not self.plan:
            self.plan = self.execution_plan
        elif self.plan and not self.execution_plan:
            self.execution_plan = self.plan
        if self.authorization and not self.authorizations:
            self.authorizations = [self.authorization.model_dump(mode="json")]
        # Keep attachments and artifacts synchronized
        if self.attachments and not self.artifacts:
            self.artifacts = list(self.attachments)
        elif self.artifacts and not self.attachments:
            self.attachments = list(self.artifacts)
        if self.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED) and not self.completed_at:
            self.completed_at = self.updated_at



# Alias Task to ExecutionTask
Task = ExecutionTask
