from fastapi import APIRouter, HTTPException
from app.database.models import ChatRequest, ChatResponse, EscalationRequest
from app.services.chat_service import process_chat_message, get_session_history
from app.services.escalation_service import create_escalation

router = APIRouter()

@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    try:
        response = await process_chat_message(
            session_id=request.session_id,
            user_id=request.user_id,
            user_message=request.message
        )
        return response
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing chat request: {str(e)}")

@router.get("/chat/history/{session_id}")
async def get_history_endpoint(session_id: str):
    try:
        history = await get_session_history(session_id)
        return {"session_id": session_id, "messages": history}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching chat history: {str(e)}")

@router.post("/escalate")
async def escalate_endpoint(request: EscalationRequest):
    try:
        record = await create_escalation(
            session_id=request.session_id,
            user_id=request.user_id,
            reason=request.reason
        )
        return {
            "success": True,
            "message": "Conversation manually escalated to human agent.",
            "escalation": record
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error escalating conversation: {str(e)}")
