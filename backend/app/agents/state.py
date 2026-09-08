from typing import TypedDict, List, Dict, Any, Optional
from langchain_core.messages import BaseMessage

class AgentState(TypedDict, total=False):
    # Core Support Agent Fields
    messages: List[Dict[str, Any]]
    user_id: str
    session_id: str
    user_message: str
    intent: str
    confidence: float
    reasoning: Optional[str]
    retrieved_context: str
    sources: List[str]
    tool_name: Optional[str]
    tool_args: Optional[Dict[str, Any]]
    tool_result: Optional[Dict[str, Any]]
    response: str
    escalation_required: bool

    # Automation Agent Fields
    user_goal: Optional[str]
    automation_type: Optional[str]
    execution_plan: Optional[Dict[str, Any]]
    current_step: Optional[int]
    execution_status: Optional[str]
    campaign_id: Optional[str]
    job_id: Optional[str]
    tool_calls: Optional[List[Dict[str, Any]]]
    errors: Optional[List[str]]
    retry_count: Optional[int]
    completion_status: Optional[str]


class AutomationState(TypedDict, total=False):
    """
    State for the autonomous automation workflow.

    Kept separate from AgentState because the two graphs have different shapes:
    the support graph answers one message, this one drives a campaign across
    several invocations and a restart. Every field below is JSON-serializable so
    the whole state can be persisted and reloaded to resume a run.
    """

    # Request
    run_id: str
    session_id: Optional[str]
    user_id: Optional[str]
    user_message: str

    # Where the run has got to, and how it should proceed.
    phase: str
    decision: Optional[str]
    retry_count: int
    errors: List[str]
    response: str

    # Structured step outputs (dumps of the models in automation_schemas).
    goal_analysis: Optional[Dict[str, Any]]
    dataset_assessment: Optional[Dict[str, Any]]
    plan_draft: Optional[Dict[str, Any]]
    plan_validation: Optional[Dict[str, Any]]
    personalization: Optional[Dict[str, Any]]
    authorization: Optional[Dict[str, Any]]
    job_creation: Optional[Dict[str, Any]]
    execution: Optional[Dict[str, Any]]
    verification: Optional[Dict[str, Any]]

    # Supplied by the caller, never by the model.
    authorization_scope: Dict[str, Any]
    dataset_id: Optional[str]
    sender_profile: Dict[str, Any]
    startup_info: Dict[str, Any]

    campaign_id: Optional[str]
