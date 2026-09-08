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
    BATCH_ACTION = "BATCH_ACTION"
    MULTI_FILE_REASONING = "MULTI_FILE_REASONING"
    RECURRING_ACTION = "RECURRING_ACTION"
    LONG_RUNNING_TASK = "LONG_RUNNING_TASK"
    CUSTOMER_SUPPORT = "CUSTOMER_SUPPORT"
    GENERAL = "GENERAL"


class TaskStatus(str, Enum):
    UNDERSTANDING = "UNDERSTANDING"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    PLANNING = "PLANNING"
    WAITING_FOR_AUTHORIZATION = "WAITING_FOR_AUTHORIZATION"
    READY = "READY"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ActionType(str, Enum):
    SEND_EMAIL = "SEND_EMAIL"
    READ_FILE = "READ_FILE"
    SEARCH_CONTACTS = "SEARCH_CONTACTS"
    FILTER_CONTACTS = "FILTER_CONTACTS"
    GENERATE_MESSAGES = "GENERATE_MESSAGES"
    WEB_SEARCH = "WEB_SEARCH"
    CODE_EXEC = "CODE_EXEC"
    RAG_QUERY = "RAG_QUERY"
    CUSTOMER_QUERY = "CUSTOMER_QUERY"
    ORDER_QUERY = "ORDER_QUERY"


class ActionStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    RETRY_PENDING = "RETRY_PENDING"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"


class TaskAction(BaseModel):
    action_id: str = Field(default_factory=lambda: f"act_{uuid.uuid4().hex[:8]}")
    action_type: ActionType
    description: str = ""
    parameters: Dict[str, Any] = Field(default_factory=dict)
    requires_authorization: bool = False
    status: ActionStatus = ActionStatus.PENDING
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    idempotency_key: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: Optional[datetime] = None


class TaskAttachment(BaseModel):
    attachment_id: str = Field(default_factory=lambda: f"att_{uuid.uuid4().hex[:8]}")
    filename: str
    file_type: str  # csv, xlsx, pdf, docx, txt, json, etc.
    size_bytes: int = 0
    extracted_text: Optional[str] = None
    parsed_data: Optional[Any] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    uploaded_at: datetime = Field(default_factory=utc_now)


class ExecutionPlanStep(BaseModel):
    step_number: int
    action_type: str
    description: str
    requires_authorization: bool = False
    completed: bool = False


class ExecutionPlan(BaseModel):
    summary: str
    steps: List[ExecutionPlanStep] = Field(default_factory=list)
    required_integrations: List[str] = Field(default_factory=list)
    estimated_actions: int = 0
    requires_user_approval: bool = False


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
    attempt_count: int = 0
    max_attempts: int = 3
    idempotency_key: str
    lease_owner: Optional[str] = None
    lease_expires_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: Optional[datetime] = None


class TaskEventType(str, Enum):
    TASK_CREATED = "TASK_CREATED"
    FILES_INSPECTED = "FILES_INSPECTED"
    PLAN_CREATED = "PLAN_CREATED"
    CLARIFICATION_REQUESTED = "CLARIFICATION_REQUESTED"
    USER_RESPONSE_RECEIVED = "USER_RESPONSE_RECEIVED"
    AUTHORIZATION_REQUESTED = "AUTHORIZATION_REQUESTED"
    AUTHORIZATION_GRANTED = "AUTHORIZATION_GRANTED"
    ACTION_CREATED = "ACTION_CREATED"
    ACTION_STARTED = "ACTION_STARTED"
    ACTION_SUCCEEDED = "ACTION_SUCCEEDED"
    ACTION_FAILED = "ACTION_FAILED"
    ACTION_RETRIED = "ACTION_RETRIED"
    TASK_PAUSED = "TASK_PAUSED"
    TASK_RESUMED = "TASK_RESUMED"
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
    user_id: str = "default_user"
    objective: str
    status: TaskStatus = TaskStatus.UNDERSTANDING
    task_type: TaskType = TaskType.GENERAL

    # Rich contextual payload
    context: Dict[str, Any] = Field(default_factory=dict)
    attachments: List[TaskAttachment] = Field(default_factory=list)

    # Clarification dialogue & interactive flow
    required_information: List[str] = Field(default_factory=list)
    clarification_questions: List[str] = Field(default_factory=list)
    clarification_options: Optional[List[Dict[str, Any]]] = None

    # Planning & actions
    execution_plan: Optional[ExecutionPlan] = None
    current_step: int = 0
    actions: List[TaskAction] = Field(default_factory=list)
    results: List[Dict[str, Any]] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)

    # Multi-turn conversation persistence
    conversation_history: List[Dict[str, Any]] = Field(default_factory=list)

    # Scoped authorization & dry run flags
    authorization_scope: Dict[str, Any] = Field(default_factory=dict)
    dry_run: bool = False

    # Optional linkage to legacy bulk campaign
    campaign_id: Optional[str] = None

    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
