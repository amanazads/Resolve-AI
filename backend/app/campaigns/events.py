"""
Real-Time Campaign Monitoring & Activity Events.

Defines the 12 core campaign activity events, event schemas,
and an in-memory asynchronous EventBus supporting Server-Sent Events (SSE)
and WebSocket streams with MongoDB persistence.
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Any, List, Optional, Set, AsyncIterator
from pydantic import BaseModel, Field, model_validator

from app.database.mongodb import db_manager

logger = logging.getLogger(__name__)


class CampaignEventType(str, Enum):
    # Core 12 lifecycle activity events
    CAMPAIGN_CREATED = "CAMPAIGN_CREATED"
    PLAN_GENERATED = "PLAN_GENERATED"
    DRY_RUN_COMPLETED = "DRY_RUN_COMPLETED"
    CAMPAIGN_STARTED = "CAMPAIGN_STARTED"
    MESSAGE_GENERATED = "MESSAGE_GENERATED"
    MESSAGE_SENT = "MESSAGE_SENT"
    MESSAGE_FAILED = "MESSAGE_FAILED"
    MESSAGE_RETRIED = "MESSAGE_RETRIED"
    CAMPAIGN_PAUSED = "CAMPAIGN_PAUSED"
    CAMPAIGN_RESUMED = "CAMPAIGN_RESUMED"
    CAMPAIGN_CANCELLED = "CAMPAIGN_CANCELLED"
    CAMPAIGN_COMPLETED = "CAMPAIGN_COMPLETED"

    # Progress & worker telemetry events
    PROGRESS_UPDATED = "PROGRESS_UPDATED"
    WORKER_STATUS = "WORKER_STATUS"


class CampaignActivityEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")
    campaign_id: str
    event_type: CampaignEventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    details: str = ""
    recipient_email: Optional[str] = None
    recipient: Optional[str] = None
    job_id: Optional[str] = None
    worker_id: Optional[str] = None
    # Live progress snapshot: {total, completed, sent, failed, retry_pending, percent_complete, status, worker_status}
    progress: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    def populate_recipient(cls, values):
        if isinstance(values, dict):
            if "recipient" in values and not values.get("recipient_email"):
                values["recipient_email"] = values["recipient"]
            elif "recipient_email" in values and not values.get("recipient"):
                values["recipient"] = values["recipient_email"]
        return values

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")


class CampaignEventBus:
    """
    In-memory PubSub broadcasting campaign events to SSE and WebSocket listeners.
    Persists important events into MongoDB.
    """

    def __init__(self, db=None):
        self._db = db or db_manager
        # campaign_id -> set of asyncio.Queue
        self._campaign_subscribers: Dict[str, Set[asyncio.Queue]] = {}
        # global subscribers ("*")
        self._global_subscribers: Set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self, campaign_id: Optional[str] = None) -> asyncio.Queue:
        """Subscribes an async queue to receive real-time events for a campaign or globally."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        async with self._lock:
            if campaign_id:
                if campaign_id not in self._campaign_subscribers:
                    self._campaign_subscribers[campaign_id] = set()
                self._campaign_subscribers[campaign_id].add(queue)
            else:
                self._global_subscribers.add(queue)
        logger.debug("New subscriber registered for campaign '%s'", campaign_id or "*")
        return queue

    async def unsubscribe(self, queue: asyncio.Queue, campaign_id: Optional[str] = None) -> None:
        """Removes a subscriber queue."""
        async with self._lock:
            if campaign_id and campaign_id in self._campaign_subscribers:
                self._campaign_subscribers[campaign_id].discard(queue)
                if not self._campaign_subscribers[campaign_id]:
                    self._campaign_subscribers.pop(campaign_id, None)
            else:
                self._global_subscribers.discard(queue)
        logger.debug("Subscriber removed for campaign '%s'", campaign_id or "*")

    async def publish(
        self,
        event: CampaignActivityEvent,
        persist: bool = True,
    ) -> CampaignActivityEvent:
        """
        Persists and distributes a CampaignActivityEvent across subscribers.
        """
        if persist:
            try:
                await self._db.save_campaign_activity_event(event.model_dump(mode="json"))
            except Exception as e:
                logger.error("Failed to persist campaign activity event: %s", e)

        async with self._lock:
            targets: List[asyncio.Queue] = []
            if event.campaign_id in self._campaign_subscribers:
                targets.extend(self._campaign_subscribers[event.campaign_id])
            targets.extend(self._global_subscribers)

        for q in targets:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("Subscriber queue full; dropping event %s", event.event_id)
            except Exception as exc:
                logger.error("Error dispatching event to subscriber queue: %s", exc)

        logger.info(
            "[EVENT] [%s] camp=%s email=%s: %s",
            event.event_type.value,
            event.campaign_id,
            event.recipient_email or event.recipient or "N/A",
            event.details,
        )
        return event

    async def emit(
        self,
        event_type: CampaignEventType,
        campaign_id: str,
        details: str = "",
        recipient_email: Optional[str] = None,
        job_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        progress: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        persist: bool = True,
    ) -> CampaignActivityEvent:
        """
        Creates, logs, persists, and distributes a campaign activity event.
        """
        event = CampaignActivityEvent(
            campaign_id=campaign_id,
            event_type=event_type,
            timestamp=datetime.now(timezone.utc),
            details=details,
            recipient_email=recipient_email,
            recipient=recipient_email,
            job_id=job_id,
            worker_id=worker_id,
            progress=progress,
            metadata=metadata or {},
        )
        return await self.publish(event, persist=persist)

    @staticmethod
    def format_sse(event: CampaignActivityEvent) -> str:
        """Formats an event for Server-Sent Events (SSE) wire protocol."""
        data_json = event.model_dump_json()
        return f"id: {event.event_id}\nevent: {event.event_type.value}\ndata: {data_json}\n\n"

    to_sse_message = format_sse

    async def event_stream(
        self,
        campaign_id: Optional[str] = None,
        heartbeat_interval: float = 15.0,
    ) -> AsyncIterator[str]:
        """
        Yields continuous SSE chunks for a subscriber queue, with periodic keepalive heartbeats.
        """
        queue = await self.subscribe(campaign_id)
        try:
            while True:
                try:
                    event: CampaignActivityEvent = await asyncio.wait_for(
                        queue.get(), timeout=heartbeat_interval
                    )
                    yield self.format_sse(event)
                except asyncio.TimeoutError:
                    # Send standard SSE keepalive comment to avoid proxy timeouts
                    yield f": keepalive {datetime.now(timezone.utc).isoformat()}\n\n"
        finally:
            await self.unsubscribe(queue, campaign_id)


campaign_event_bus = CampaignEventBus()
