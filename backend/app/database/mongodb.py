import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
import motor.motor_asyncio
try:
    from app.config import settings
except ImportError:
    from ..config import settings

logger = logging.getLogger(__name__)

class MongoDBManager:
    """
    Manages MongoDB persistence for chat sessions, messages, and escalations.
    Includes an in-memory fallback store if MongoDB server is unreachable.
    """
    def __init__(self):
        self.client = None
        self.db = None
        self.is_connected = False
        
        # In-memory fallbacks
        self._memory_messages: Dict[str, List[Dict[str, Any]]] = {}
        self._memory_escalations: List[Dict[str, Any]] = []

    async def connect(self):
        try:
            self.client = motor.motor_asyncio.AsyncIOMotorClient(
                settings.MONGODB_URI,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=5000
            )
            # Test connection
            await self.client.admin.command('ping')
            self.db = self.client[settings.MONGODB_DB_NAME]
            self.is_connected = True
            logger.info(f"Connected to MongoDB Atlas database '{settings.MONGODB_DB_NAME}' successfully.")
        except Exception as e:
            self.is_connected = False
            logger.warning(f"MongoDB not reachable ({e}). Operating with in-memory persistence fallback.")

    async def close(self):
        if self.client:
            self.client.close()
            logger.info("Closed MongoDB client connection.")

    async def save_message(self, session_id: str, message: Dict[str, Any]):
        message["timestamp"] = message.get("timestamp") or datetime.utcnow().isoformat()
        if self.is_connected and self.db is not None:
            try:
                await self.db.messages.insert_one({"session_id": session_id, **message})
                return
            except Exception as e:
                logger.error(f"Error saving message to MongoDB: {e}")
        
        # Fallback in-memory
        if session_id not in self._memory_messages:
            self._memory_messages[session_id] = []
        self._memory_messages[session_id].append(message)

    async def get_history(self, session_id: str) -> List[Dict[str, Any]]:
        if self.is_connected and self.db is not None:
            try:
                cursor = self.db.messages.find({"session_id": session_id}).sort("timestamp", 1)
                messages = await cursor.to_list(length=100)
                for m in messages:
                    m.pop("_id", None)
                return messages
            except Exception as e:
                logger.error(f"Error reading history from MongoDB: {e}")

        # Fallback in-memory
        return self._memory_messages.get(session_id, [])

    async def save_escalation(self, record: Dict[str, Any]):
        record["timestamp"] = record.get("timestamp") or datetime.utcnow().isoformat()
        if self.is_connected and self.db is not None:
            try:
                await self.db.escalations.insert_one(record)
                return
            except Exception as e:
                logger.error(f"Error saving escalation to MongoDB: {e}")
        
        self._memory_escalations.append(record)

    async def get_escalations(self) -> List[Dict[str, Any]]:
        if self.is_connected and self.db is not None:
            try:
                cursor = self.db.escalations.find().sort("timestamp", -1)
                records = await cursor.to_list(length=100)
                for r in records:
                    r.pop("_id", None)
                return records
            except Exception as e:
                logger.error(f"Error fetching escalations from MongoDB: {e}")

        return self._memory_escalations

db_manager = MongoDBManager()
