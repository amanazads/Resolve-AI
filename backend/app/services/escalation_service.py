import logging
from typing import List, Dict, Any
try:
    from app.database.mongodb import db_manager
    from app.database.models import EscalationRecord
except ImportError:
    from ..database.mongodb import db_manager
    from ..database.models import EscalationRecord

logger = logging.getLogger(__name__)

async def create_escalation(session_id: str, user_id: str, reason: str) -> EscalationRecord:
    record = EscalationRecord(
        session_id=session_id,
        user_id=user_id,
        reason=reason,
        status="PENDING"
    )
    await db_manager.save_escalation(record.dict())
    return record

async def list_escalations() -> List[Dict[str, Any]]:
    return await db_manager.get_escalations()
