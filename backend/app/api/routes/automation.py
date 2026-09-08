import logging
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.automation.models import AutomationPlan, Campaign
from app.automation.planner import AutomationPlanner
from app.automation.campaign_service import campaign_service
from app.database.mongodb import db_manager

logger = logging.getLogger(__name__)

router = APIRouter()
planner = AutomationPlanner()

class PlanCreateRequest(BaseModel):
    goal: str = Field(..., description="High-level goal for the automation plan")
    automation_type: str = Field("general", description="Classification of automation")
    input_datasets: Optional[List[Dict[str, Any]]] = Field(default=None, description="Input dataset items")
    execution_constraints: Optional[Dict[str, Any]] = Field(default=None, description="Rate limits, concurrency")
    personalization_instructions: Optional[str] = Field(default=None, description="Prompt/template instructions")
    approval_authorization_scope: Optional[Dict[str, Any]] = Field(default=None, description="Scopes and approvals")

class CampaignCreateRequest(BaseModel):
    goal: str = Field(..., description="Campaign objective")
    automation_type: str = Field("general", description="Type of automation")
    name: Optional[str] = Field(default=None, description="Campaign human-readable name")
    input_datasets: Optional[List[Dict[str, Any]]] = Field(default=None, description="Recipients or input entities")
    execution_constraints: Optional[Dict[str, Any]] = Field(default=None, description="Execution parameters")
    personalization_instructions: Optional[str] = Field(default=None, description="Personalization rules")
    approval_authorization_scope: Optional[Dict[str, Any]] = Field(default=None, description="Authorization scope")

@router.post("/automation/plan", response_model=AutomationPlan)
async def create_plan_endpoint(request: PlanCreateRequest):
    try:
        plan = await planner.create_plan(
            goal=request.goal,
            automation_type=request.automation_type,
            input_datasets=request.input_datasets,
            execution_constraints=request.execution_constraints,
            personalization_instructions=request.personalization_instructions,
            approval_authorization_scope=request.approval_authorization_scope,
            persist=True
        )
        return plan
    except Exception as e:
        logger.error(f"Error creating automation plan: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create plan: {str(e)}")

@router.post("/automation/campaign", response_model=Campaign)
async def create_campaign_endpoint(request: CampaignCreateRequest):
    try:
        campaign = await campaign_service.create_campaign(
            goal=request.goal,
            automation_type=request.automation_type,
            name=request.name,
            input_datasets=request.input_datasets,
            execution_constraints=request.execution_constraints,
            personalization_instructions=request.personalization_instructions,
            approval_authorization_scope=request.approval_authorization_scope
        )
        return campaign
    except Exception as e:
        logger.error(f"Error creating campaign: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create campaign: {str(e)}")

@router.post("/automation/campaign/{campaign_id}/start")
async def start_campaign_endpoint(campaign_id: str):
    try:
        camp = await campaign_service.start_campaign(campaign_id, run_in_background=True)
        return {"success": True, "message": f"Campaign '{campaign_id}' started.", "campaign": camp}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start campaign: {str(e)}")

@router.post("/automation/campaign/{campaign_id}/pause")
async def pause_campaign_endpoint(campaign_id: str):
    try:
        camp = await campaign_service.pause_campaign(campaign_id)
        if not camp:
            raise HTTPException(status_code=404, detail="Campaign not found")
        return {"success": True, "message": f"Campaign '{campaign_id}' paused.", "campaign": camp}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to pause campaign: {str(e)}")

@router.post("/automation/campaign/{campaign_id}/resume")
async def resume_campaign_endpoint(campaign_id: str):
    try:
        camp = await campaign_service.resume_campaign(campaign_id, run_in_background=True)
        if not camp:
            raise HTTPException(status_code=404, detail="Campaign not found")
        return {"success": True, "message": f"Campaign '{campaign_id}' resumed.", "campaign": camp}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to resume campaign: {str(e)}")

@router.post("/automation/campaign/{campaign_id}/cancel")
async def cancel_campaign_endpoint(campaign_id: str):
    try:
        camp = await campaign_service.cancel_campaign(campaign_id)
        if not camp:
            raise HTTPException(status_code=404, detail="Campaign not found")
        return {"success": True, "message": f"Campaign '{campaign_id}' cancelled.", "campaign": camp}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to cancel campaign: {str(e)}")

@router.get("/automation/campaign/{campaign_id}")
async def get_campaign_status_endpoint(campaign_id: str):
    try:
        status_data = await campaign_service.get_campaign_status(campaign_id)
        if not status_data:
            raise HTTPException(status_code=404, detail="Campaign not found")
        return status_data
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch campaign: {str(e)}")

@router.get("/automation/campaigns")
async def list_campaigns_endpoint():
    try:
        campaigns = await db_manager.list_campaigns()
        return {"campaigns": campaigns}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to list campaigns: {str(e)}")


# ==========================================================================
# Autonomous automation agent (LangGraph workflow)
# ==========================================================================

from app.agents.automation_graph import (
    get_automation_run,
    list_automation_runs,
    resume_automation_run,
    run_automation_agent,
)


class AutomationAgentRequest(BaseModel):
    """
    A request to the autonomous automation workflow.

    `authorization_scope` is the only thing that can permit sending, and it is
    supplied here by the caller -- from an authenticated principal -- never by
    the model. Without `allow_send` and `authorized_by` the run plans the
    campaign and stops before contacting anyone.
    """

    message: str = Field(..., description="What the user wants, in natural language.")
    user_id: str = Field(default="default_user")
    session_id: Optional[str] = Field(default=None)
    dataset_id: Optional[str] = Field(
        default=None, description="Overrides the dataset the model inferred."
    )
    authorization_scope: Dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Permissions for this run: allow_send, authorized_by, max_recipients, "
            "allowed_actions, allow_dry_run."
        ),
    )
    sender_profile: Optional[Dict[str, Any]] = Field(default=None)
    startup_info: Optional[Dict[str, Any]] = Field(default=None)


@router.post("/automation/agent/run")
async def run_automation_agent_endpoint(request: AutomationAgentRequest):
    """
    Runs the automation workflow: understand, check data, plan, validate,
    personalize, authorize, create jobs, hand off, verify, persist.

    Returns once the campaign has been handed to the persistent worker and
    verified once -- usually with decision CONTINUE, because the worker is still
    sending. The individual recipients are never processed inside this request.
    """
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty.")

    try:
        state = await run_automation_agent(
            user_message=request.message,
            user_id=request.user_id,
            session_id=request.session_id,
            authorization_scope=request.authorization_scope,
            dataset_id=request.dataset_id,
            sender_profile=request.sender_profile,
            startup_info=request.startup_info,
        )
    except Exception as e:
        logger.exception("Automation agent run failed")
        raise HTTPException(status_code=500, detail=f"Automation run failed: {e}")

    return {
        "run_id": state.get("run_id"),
        "phase": state.get("phase"),
        "decision": state.get("decision"),
        "campaign_id": state.get("campaign_id"),
        "response": state.get("response"),
        "goal_analysis": state.get("goal_analysis"),
        "dataset_assessment": state.get("dataset_assessment"),
        "plan": state.get("plan_draft"),
        "plan_validation": state.get("plan_validation"),
        "personalization": state.get("personalization"),
        "authorization": state.get("authorization"),
        "job_creation": state.get("job_creation"),
        "execution": state.get("execution"),
        "verification": state.get("verification"),
        "errors": state.get("errors", []),
    }


@router.post("/automation/agent/{run_id}/resume")
async def resume_automation_agent_endpoint(run_id: str):
    """
    Continues a persisted run from the phase it reached.

    Safe to call repeatedly and after a restart: job creation is idempotent and
    completed jobs are terminal, so resuming never re-sends to anyone.
    """
    state = await resume_automation_run(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Automation run '{run_id}' not found.")
    return {
        "run_id": state.get("run_id"),
        "phase": state.get("phase"),
        "decision": state.get("decision"),
        "campaign_id": state.get("campaign_id"),
        "response": state.get("response"),
        "verification": state.get("verification"),
    }


@router.get("/automation/agent/{run_id}")
async def get_automation_agent_run(run_id: str):
    """The full persisted record of one run, including every structured step."""
    record = await get_automation_run(run_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Automation run '{run_id}' not found.")
    return record


@router.get("/automation/agent")
async def list_automation_agent_runs(
    phase: Optional[str] = None, skip: int = 0, limit: int = 50
):
    return await list_automation_runs(phase=phase, skip=skip, limit=min(limit, 200))
