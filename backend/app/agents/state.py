from typing import TypedDict, List, Dict, Any, Optional
from langchain_core.messages import BaseMessage

class AgentState(TypedDict):
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
