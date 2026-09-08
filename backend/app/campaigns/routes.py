import json
import logging
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from app.campaigns.models import (
    Campaign,
    PlanCampaignRequest,
    PlanCampaignResponse
)
from app.campaigns.service import campaign_service
from app.campaigns.events import campaign_event_bus, CampaignActivityEvent, CampaignEventType
from app.database.mongodb import db_manager

logger = logging.getLogger(__name__)

router = APIRouter()

@router.post("/campaigns/plan", response_model=PlanCampaignResponse)
async def plan_campaign(request: PlanCampaignRequest):
    """
    Translates natural language prompt into a structured, validated CampaignPlan
    and persists the Campaign entity in MongoDB.
    """
    if not request.goal or not request.goal.strip():
        raise HTTPException(status_code=400, detail="Campaign goal description cannot be empty.")

    try:
        plan, campaign = await campaign_service.create_campaign_plan(
            goal=request.goal,
            dataset_id=request.dataset_id,
            name=request.name,
            owner_id=request.owner_id,
            channel=request.channel
        )
        return PlanCampaignResponse(plan=plan, campaign=campaign)
    except Exception as e:
        logger.error(f"Error generating campaign plan: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to plan campaign: {str(e)}")

@router.get("/campaigns", response_model=List[Campaign])
async def list_campaigns(owner_id: Optional[str] = Query(None, description="Filter by owner ID")):
    """
    Returns list of outreach campaigns.
    """
    try:
        return await campaign_service.list_campaigns(owner_id=owner_id)
    except Exception as e:
        logger.error(f"Error listing campaigns: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list campaigns: {str(e)}")

@router.get("/campaigns/{campaign_id}", response_model=Campaign)
async def get_campaign(campaign_id: str):
    """
    Retrieves details and status for a specific campaign.
    """
    campaign = await campaign_service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail=f"Campaign '{campaign_id}' not found.")
    return campaign


# ==========================================================================
# Campaign execution
#
# None of these endpoints processes a campaign. `start` and `resume` persist the
# jobs and hand the work to a background worker, then return the current
# progress; a campaign with thousands of recipients is executed by the worker,
# never inside the request that asked for it.
# ==========================================================================

from app.automation.models import CampaignProgress, JobStatus
from app.automation.worker import campaign_execution_service
from app.permissions.middleware import (
    IntegrationNotAuthorized,
    PermissionDenied,
    integration_error,
    permission_error,
)
from app.campaigns.models import (
    CampaignJobView,
    PaginatedCampaignJobs,
    ResumeCampaignRequest,
    StartCampaignRequest,
)
from app.safety import SafetyViolationError

_VALID_JOB_STATUSES = {status.value for status in JobStatus}


def _job_view(job) -> CampaignJobView:
    recipient = job.recipient or {}
    return CampaignJobView(
        **job.model_dump(mode="python"),
        recipient_name=(recipient.get("full_name") or recipient.get("first_name") or None),
        recipient_company=recipient.get("company") or None,
    )


@router.post("/campaigns/{campaign_id}/start", response_model=CampaignProgress)
async def start_campaign(campaign_id: str, request: Optional[StartCampaignRequest] = None):
    """
    Enqueues one persistent job per recipient and starts a background worker.

    Returns as soon as the jobs are durable. Poll `/progress` to follow the run.
    Re-starting a campaign is safe: jobs that already exist are not duplicated,
    and recipients already sent to are not sent to again.
    """
    options = request or StartCampaignRequest()
    try:
        return await campaign_execution_service.start_campaign(
            campaign_id=campaign_id,
            dry_run=options.dry_run,
            rate_per_minute=options.rate_per_minute,
            concurrency=options.concurrency,
            max_attempts=options.max_attempts,
            sender_profile=options.sender_profile,
            startup_info=options.startup_info,
            principal_user_id=options.user_id,
            allow_direct_execution=options.allow_direct_execution,
            campaign_limits=options.campaign_limits,
        )
    except SafetyViolationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except PermissionDenied as exc:
        raise permission_error(exc) from exc
    except IntegrationNotAuthorized as exc:
        raise integration_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc) else 409, detail=str(exc))
    except Exception as exc:
        logger.exception("Failed to start campaign '%s'", campaign_id)
        raise HTTPException(status_code=500, detail=f"Failed to start campaign: {exc}")


@router.post("/campaigns/{campaign_id}/pause", response_model=CampaignProgress)
async def pause_campaign(campaign_id: str):
    """
    Stops claiming new jobs. Work already handed to the provider is allowed to
    finish, because interrupting a send in flight is how duplicates happen.
    """
    campaign = await campaign_service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail=f"Campaign '{campaign_id}' not found.")
    return await campaign_execution_service.pause_campaign(campaign_id)


@router.post("/campaigns/{campaign_id}/resume", response_model=CampaignProgress)
async def resume_campaign(campaign_id: str, request: Optional[ResumeCampaignRequest] = None):
    """
    Picks up exactly where the campaign stopped. Recipients already sent to are
    never re-sent: their jobs are terminal and are not claimable.
    """
    options = request or ResumeCampaignRequest()
    try:
        return await campaign_execution_service.resume_campaign(
            campaign_id=campaign_id,
            rate_per_minute=options.rate_per_minute,
            concurrency=options.concurrency,
            principal_user_id=options.user_id,
        )
    except PermissionDenied as exc:
        raise permission_error(exc) from exc
    except IntegrationNotAuthorized as exc:
        raise integration_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc) else 409, detail=str(exc))


@router.post("/campaigns/{campaign_id}/cancel", response_model=CampaignProgress)
async def cancel_campaign(campaign_id: str):
    """
    Cancels the campaign and every job that has not finished.

    Jobs already SENT stay SENT: cancelling stops future sends, it cannot unsend
    what has already gone out.
    """
    campaign = await campaign_service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail=f"Campaign '{campaign_id}' not found.")
    return await campaign_execution_service.cancel_campaign(campaign_id)


@router.get("/campaigns/{campaign_id}/progress", response_model=CampaignProgress)
async def campaign_progress(campaign_id: str):
    """
    Live progress, recomputed from the persisted jobs.

    Progress is stored, not held in memory, so it survives a restart and can be
    read by a process that is not the one running the campaign.
    """
    campaign = await campaign_service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail=f"Campaign '{campaign_id}' not found.")
    return await campaign_execution_service.get_progress(campaign_id)


@router.get("/campaigns/{campaign_id}/jobs", response_model=PaginatedCampaignJobs)
async def campaign_jobs(
    campaign_id: str,
    status: Optional[str] = Query(None, description="Filter by job status, e.g. SENT or FAILED"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
):
    """
    Per-recipient job records: generated message, provider response, attempt
    count, failure reason and message id.
    """
    if status and status.upper() not in _VALID_JOB_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown job status '{status}'. Expected one of {sorted(_VALID_JOB_STATUSES)}.",
        )

    jobs = await campaign_execution_service.list_jobs(
        campaign_id,
        status=status.upper() if status else None,
        skip=(page - 1) * page_size,
        limit=page_size,
    )
    return PaginatedCampaignJobs(
        items=[_job_view(job) for job in jobs],
        campaign_id=campaign_id,
        status_filter=status.upper() if status else None,
        page=page,
        page_size=page_size,
        returned=len(jobs),
    )


# ==========================================================================
# Real-Time Monitoring & Activity Log API
# ==========================================================================


from pydantic import BaseModel


class PaginatedCampaignActivity(BaseModel):
    items: List[CampaignActivityEvent]
    campaign_id: Optional[str] = None
    page: int = 1
    page_size: int = 50
    total: int = 0


@router.get("/campaigns/{campaign_id}/events")
async def stream_campaign_events(campaign_id: str):
    """
    Server-Sent Events (SSE) stream for real-time campaign progress,
    job status updates, and lifecycle activity events.
    """
    campaign = await campaign_service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail=f"Campaign '{campaign_id}' not found.")

    return StreamingResponse(
        campaign_event_bus.event_stream(campaign_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/campaigns/events")
async def stream_global_campaign_events():
    """
    Server-Sent Events (SSE) stream for workspace-wide campaign events.
    """
    return StreamingResponse(
        campaign_event_bus.event_stream(None),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.websocket("/campaigns/{campaign_id}/ws")
async def campaign_websocket(websocket: WebSocket, campaign_id: str):
    """
    WebSocket endpoint streaming live campaign progress and activity events.
    """
    await websocket.accept()
    queue = await campaign_event_bus.subscribe(campaign_id)
    try:
        # Send initial progress state
        progress = await campaign_execution_service.get_progress(campaign_id)
        await websocket.send_text(
            json.dumps(
                {
                    "type": "CONNECTION_ESTABLISHED",
                    "campaign_id": campaign_id,
                    "progress": progress.model_dump(mode="json"),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
        )
        while True:
            event = await queue.get()
            await websocket.send_text(event.model_dump_json())
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for campaign '%s'", campaign_id)
    except Exception as exc:
        logger.warning("WebSocket error for campaign '%s': %s", campaign_id, exc)
    finally:
        await campaign_event_bus.unsubscribe(queue, campaign_id)


@router.get("/campaigns/{campaign_id}/activity", response_model=PaginatedCampaignActivity)
async def get_campaign_activity(
    campaign_id: str,
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
):
    """
    Returns paginated activity log events for a specific campaign.
    """
    campaign = await campaign_service.get_campaign(campaign_id)
    if not campaign:
        raise HTTPException(status_code=404, detail=f"Campaign '{campaign_id}' not found.")

    events_data = await db_manager.list_campaign_activity_events(
        campaign_id=campaign_id,
        event_type=event_type,
        skip=(page - 1) * page_size,
        limit=page_size,
    )
    total = await db_manager.count_campaign_activity_events(
        campaign_id=campaign_id,
        event_type=event_type,
    )
    return PaginatedCampaignActivity(
        items=[CampaignActivityEvent(**e) for e in events_data],
        campaign_id=campaign_id,
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/campaigns/activity/log", response_model=PaginatedCampaignActivity)
async def get_global_activity(
    campaign_id: Optional[str] = Query(None, description="Optional campaign ID filter"),
    event_type: Optional[str] = Query(None, description="Filter by event type"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
):
    """
    Returns workspace-wide activity log events across all campaigns.
    """
    events_data = await db_manager.list_campaign_activity_events(
        campaign_id=campaign_id,
        event_type=event_type,
        skip=(page - 1) * page_size,
        limit=page_size,
    )
    total = await db_manager.count_campaign_activity_events(
        campaign_id=campaign_id,
        event_type=event_type,
    )
    return PaginatedCampaignActivity(
        items=[CampaignActivityEvent(**e) for e in events_data],
        campaign_id=campaign_id,
        page=page,
        page_size=page_size,
        total=total,
    )
