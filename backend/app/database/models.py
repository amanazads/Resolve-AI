from datetime import datetime
from enum import Enum
from typing import List, Optional, Any, Dict
from pydantic import BaseModel, Field

class IntentType(str, Enum):
    FAQ = "FAQ"
    PRODUCT = "PRODUCT"
    PRICING = "PRICING"
    REFUND = "REFUND"
    SHIPPING = "SHIPPING"
    TROUBLESHOOTING = "TROUBLESHOOTING"
    ORDER_STATUS = "ORDER_STATUS"
    CANCEL_ORDER = "CANCEL_ORDER"
    COMPLAINT = "COMPLAINT"
    HUMAN_ESCALATION = "HUMAN_ESCALATION"
    GENERAL = "GENERAL"

class IntentResult(BaseModel):
    intent: IntentType
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: Optional[str] = None

class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Unique session ID")
    user_id: str = Field(..., description="User ID")
    message: str = Field(..., description="User query text")

class ChatResponse(BaseModel):
    response: str
    intent: str
    confidence: float
    sources: List[str] = []
    tool_used: Optional[str] = None
    tool_result: Optional[Any] = None
    escalated: bool = False
    session_id: str
    user_id: str

class MessageRecord(BaseModel):
    role: str # "user" or "assistant" or "system"
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    intent: Optional[str] = None
    sources: Optional[List[str]] = None
    tool_used: Optional[str] = None
    escalated: bool = False

class EscalationRequest(BaseModel):
    session_id: str
    user_id: str
    reason: str

class EscalationRecord(BaseModel):
    session_id: str
    user_id: str
    reason: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    status: str = "PENDING" # PENDING, RESOLVED, IN_PROGRESS
