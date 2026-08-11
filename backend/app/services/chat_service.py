import logging
from typing import Dict, Any, List
from datetime import datetime
try:
    from app.database.mongodb import db_manager
    from app.agents.graph import run_support_agent
    from app.database.models import ChatResponse, EscalationRecord
except ImportError:
    from ..database.mongodb import db_manager
    from ..agents.graph import run_support_agent
    from ..database.models import ChatResponse, EscalationRecord

logger = logging.getLogger(__name__)

async def process_chat_message(session_id: str, user_id: str, user_message: str) -> ChatResponse:
    """
    Processes an incoming user chat message:
    1. Fetches previous conversation history from MongoDB.
    2. Runs the LangGraph agent state graph.
    3. Persists both user and assistant messages into MongoDB memory.
    4. Handles human escalation logging if triggered.
    """
    # 1. Fetch History
    history = await db_manager.get_history(session_id)

    # 2. Run LangGraph Workflow
    result_state = run_support_agent(
        session_id=session_id,
        user_id=user_id,
        user_message=user_message,
        history=history
    )

    response_text = result_state.get("response", "")
    intent = result_state.get("intent", "GENERAL")
    confidence = result_state.get("confidence", 1.0)
    sources = result_state.get("sources", [])
    tool_used = result_state.get("tool_name")
    tool_result = result_state.get("tool_result")
    escalated = result_state.get("escalation_required", False)

    # 3. Persist Messages to MongoDB
    user_record = {
        "role": "user",
        "content": user_message,
        "timestamp": datetime.utcnow().isoformat()
    }
    await db_manager.save_message(session_id, user_record)

    assistant_record = {
        "role": "assistant",
        "content": response_text,
        "intent": intent,
        "confidence": confidence,
        "sources": sources,
        "tool_used": tool_used,
        "tool_result": tool_result,
        "escalated": escalated,
        "timestamp": datetime.utcnow().isoformat()
    }
    await db_manager.save_message(session_id, assistant_record)

    # 4. Save Escalation Record if Escalated
    if escalated:
        escalation_record = EscalationRecord(
            session_id=session_id,
            user_id=user_id,
            reason=result_state.get("reasoning", "Agent triggered human escalation"),
            status="PENDING"
        )
        await db_manager.save_escalation(escalation_record.dict())

    return ChatResponse(
        response=response_text,
        intent=intent,
        confidence=confidence,
        sources=sources,
        tool_used=tool_used,
        tool_result=tool_result,
        escalated=escalated,
        session_id=session_id,
        user_id=user_id
    )

async def get_session_history(session_id: str) -> List[Dict[str, Any]]:
    return await db_manager.get_history(session_id)
