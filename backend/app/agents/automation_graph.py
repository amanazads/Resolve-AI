"""
The autonomous automation workflow, as a LangGraph.

    USER_REQUEST
        v
    UNDERSTAND_GOAL        goal_analysis_node
        v
    CHECK_AVAILABLE_DATA   dataset_analysis_node
        v
    CREATE_PLAN /          campaign_planning_node   (draft, then validate)
    VALIDATE_PLAN
        v
    PERSONALIZE            personalization_node     (settings + one real sample)
        v
    CHECK_AUTHORIZATION    authorization_node
        v
    CREATE_JOBS            job_creation_node
        v
    EXECUTE                execution_node           (hands off to the worker)
        v
    VERIFY_RESULT          verification_node        (provider confirmations)
        v
    PERSIST_STATE          persist_state_node
        v
    CONTINUE / RETRY / COMPLETE

Two properties are worth stating plainly, because they are what the design is
for:

  * The graph never sends anything. `execution_node` hands the campaign to the
    persistent worker and returns; recipients are queue jobs with leases,
    retries and rate limiting. A 1,000-contact campaign is not one invocation.

  * The graph is resumable. Every node's output is persisted to MongoDB by
    `persist_state_node` and at each phase boundary, so a run can be reloaded in
    a different process after a restart and continued from the phase it reached.
    `resume_automation_run` does exactly that, entering the graph at the right
    node rather than starting over.
"""

import logging
import uuid
from typing import Any, Dict, List, Optional

# pyrefly: ignore [missing-import]
from langgraph.graph import END, StateGraph

from app.agents.automation_nodes import (
    authorization_node,
    campaign_planning_node,
    dataset_analysis_node,
    execution_node,
    goal_analysis_node,
    job_creation_node,
    personalization_node,
    verification_node,
)
from app.agents.automation_schemas import (
    AutomationPhase,
    AutomationRunRecord,
    WorkflowDecision,
)
from app.agents.state import AutomationState
from app.database.mongodb import db_manager

logger = logging.getLogger(__name__)


# ==========================================================================
# Persistence
# ==========================================================================


def _state_to_record(state: AutomationState) -> Dict[str, Any]:
    record = AutomationRunRecord(
        run_id=state.get("run_id", ""),
        session_id=state.get("session_id"),
        user_id=state.get("user_id"),
        user_message=state.get("user_message", ""),
        phase=state.get("phase", AutomationPhase.USER_REQUEST.value),
        decision=state.get("decision"),
        campaign_id=state.get("campaign_id"),
        goal_analysis=state.get("goal_analysis"),
        dataset_assessment=state.get("dataset_assessment"),
        plan_draft=state.get("plan_draft"),
        plan_validation=state.get("plan_validation"),
        personalization=state.get("personalization"),
        authorization=state.get("authorization"),
        job_creation=state.get("job_creation"),
        execution=state.get("execution"),
        verification=state.get("verification"),
        authorization_scope=state.get("authorization_scope") or {},
        retry_count=state.get("retry_count", 0),
        errors=list(state.get("errors", [])),
        response=state.get("response", ""),
    )
    return record.model_dump(mode="json")


async def _persist(state: AutomationState) -> None:
    if state.get("run_id"):
        await db_manager.save_automation_run(_state_to_record(state))


async def persist_state_node(state: AutomationState) -> Dict[str, Any]:
    """
    Writes the run to MongoDB and composes the human-readable summary.

    This is the durable checkpoint the workflow resumes from. It runs before the
    CONTINUE / RETRY / COMPLETE decision so that a crash immediately after
    execution still leaves a run that can be picked up.
    """
    summary = _summarize(state)
    persisted = dict(state)
    persisted["response"] = summary
    persisted["phase"] = AutomationPhase.PERSIST_STATE.value
    await _persist(persisted)
    return {"phase": AutomationPhase.PERSIST_STATE.value, "response": summary}


# ==========================================================================
# Guard nodes for the paths that stop early
# ==========================================================================


async def blocked_node(state: AutomationState) -> Dict[str, Any]:
    """
    Terminal state for a run that cannot proceed: not an automation request, no
    usable data, a plan that failed validation, or authorization withheld.
    """
    summary = _summarize(state)
    blocked = dict(state)
    blocked["phase"] = AutomationPhase.BLOCKED.value
    blocked["response"] = summary
    blocked["decision"] = WorkflowDecision.BLOCKED.value
    await _persist(blocked)
    return {
        "phase": AutomationPhase.BLOCKED.value,
        "decision": WorkflowDecision.BLOCKED.value,
        "response": summary,
    }


# ==========================================================================
# Routing
# ==========================================================================


def route_after_goal(state: AutomationState) -> str:
    goal = state.get("goal_analysis") or {}
    if not goal.get("is_automation_request"):
        logger.info("Not an automation request; stopping the automation workflow.")
        return "blocked"
    return "dataset_analysis"


def route_after_data(state: AutomationState) -> str:
    assessment = state.get("dataset_assessment") or {}
    if not assessment.get("sufficient"):
        return "blocked"
    return "campaign_planning"


def route_after_validation(state: AutomationState) -> str:
    validation = state.get("plan_validation") or {}
    if not validation.get("is_valid"):
        return "blocked"
    return "personalization"


def route_after_authorization(state: AutomationState) -> str:
    """
    The single gate before anything is created or sent.

    There is no other edge into job creation, and `job_creation_node` re-checks
    the decision itself, so an unauthorized run cannot reach the queue by any
    route.
    """
    authorization = state.get("authorization") or {}
    if not authorization.get("authorized"):
        return "blocked"
    return "job_creation"


def route_after_verification(state: AutomationState) -> str:
    """CONTINUE / RETRY / COMPLETE, as decided by verification_node."""
    decision = state.get("decision")
    if decision == WorkflowDecision.RETRY.value:
        if state.get("retry_count", 0) >= 2:
            return "persist"
        return "retry"
    return "persist"


async def retry_node(state: AutomationState) -> Dict[str, Any]:
    """Bumps the retry counter and sends the run back through execution."""
    count = state.get("retry_count", 0) + 1
    logger.info("Retrying execution for run '%s' (attempt %d).", state.get("run_id"), count)
    return {"retry_count": count}


# ==========================================================================
# Summary
# ==========================================================================


def _summarize(state: AutomationState) -> str:
    """A short, factual account of the run. Every number comes from the state."""
    phase = state.get("phase")
    goal = state.get("goal_analysis") or {}
    assessment = state.get("dataset_assessment") or {}
    validation = state.get("plan_validation") or {}
    authorization = state.get("authorization") or {}
    verification = state.get("verification") or {}

    if not goal.get("is_automation_request", False):
        return (
            "This does not look like a bulk outreach request, so no campaign was created. "
            + (goal.get("reasoning") or "The request could not be processed as an outreach campaign.")
        ).strip()

    if not assessment.get("sufficient", False):
        return (
            "No campaign was created: "
            + (assessment.get("reason") or "there is no usable contact data.")
            + (" " + " ".join(assessment.get("data_gaps", [])) if assessment.get("data_gaps") else "")
        )

    if validation and not validation.get("is_valid", False):
        return "The generated plan did not pass validation: " + "; ".join(
            validation.get("errors", [])
        )

    if authorization and not authorization.get("authorized", False):
        return (
            "The campaign is planned and ready but not authorized, so nothing has been sent. "
            + " ".join(authorization.get("reasons", []))
        )

    if verification:
        parts = [
            f"Campaign '{verification.get('campaign_id')}': "
            f"{verification.get('provider_confirmed', 0)} of {verification.get('total', 0)} "
            "message(s) confirmed by the provider"
        ]
        if verification.get("dry_run"):
            parts = [
                f"Dry run for campaign '{verification.get('campaign_id')}': "
                f"{verification.get('skipped', 0)} message(s) generated and previewed, "
                "nothing sent"
            ]
        if verification.get("failed"):
            parts.append(f"{verification['failed']} failed")
        if verification.get("remaining"):
            parts.append(f"{verification['remaining']} still in progress")
        if verification.get("requires_manual_review"):
            parts.append(
                f"{verification['requires_manual_review']} need a human decision"
            )
        return ", ".join(parts) + "."

    if authorization.get("authorized"):
        return "The campaign is authorized and queued for the worker."

    return f"Automation run is at phase {phase}."


# ==========================================================================
# Graph
# ==========================================================================

#: Phase -> the node that should run next when a persisted run is resumed.
RESUME_ENTRY_POINTS: Dict[str, str] = {
    AutomationPhase.USER_REQUEST.value: "goal_analysis",
    AutomationPhase.UNDERSTAND_GOAL.value: "dataset_analysis",
    AutomationPhase.CHECK_AVAILABLE_DATA.value: "campaign_planning",
    AutomationPhase.CREATE_PLAN.value: "personalization",
    AutomationPhase.VALIDATE_PLAN.value: "personalization",
    AutomationPhase.PERSONALIZE.value: "authorization",
    AutomationPhase.CHECK_AUTHORIZATION.value: "job_creation",
    AutomationPhase.CREATE_JOBS.value: "execution",
    AutomationPhase.EXECUTE.value: "verification",
    AutomationPhase.VERIFY_RESULT.value: "persist",
    AutomationPhase.PERSIST_STATE.value: "verification",
    AutomationPhase.AWAITING_COMPLETION.value: "verification",
}


async def dispatch_node(state: AutomationState) -> Dict[str, Any]:
    """Entry node. Exists so the graph can be entered at any phase on resume."""
    return {}


def route_from_dispatch(state: AutomationState) -> str:
    """
    Picks the entry node for this invocation.

    A fresh run starts at goal analysis. A resumed run re-enters at the node
    after the last phase it completed, which is what makes the workflow
    restartable without redoing work or, worse, re-creating jobs.
    """
    decision = state.get("decision")
    if decision in (WorkflowDecision.COMPLETE.value, WorkflowDecision.BLOCKED.value):
        # A finished run is re-persisted rather than re-executed, so resuming it
        # by mistake cannot create jobs or contact anyone a second time.
        logger.info("Run already reached decision '%s'; nothing to do.", decision)
        return "persist"

    phase = state.get("phase") or AutomationPhase.USER_REQUEST.value
    entry = RESUME_ENTRY_POINTS.get(phase)
    if entry is None:
        logger.info("Run is at terminal phase '%s'; nothing to resume.", phase)
        return "persist"
    return entry


def build_automation_graph():
    """Compiles the automation workflow."""
    workflow = StateGraph(AutomationState)

    workflow.add_node("dispatch", dispatch_node)
    workflow.add_node("goal_analysis", goal_analysis_node)
    workflow.add_node("dataset_analysis", dataset_analysis_node)
    workflow.add_node("campaign_planning", campaign_planning_node)
    workflow.add_node("personalization", personalization_node)
    workflow.add_node("authorization", authorization_node)
    workflow.add_node("job_creation", job_creation_node)
    workflow.add_node("execution", execution_node)
    workflow.add_node("verification", verification_node)
    workflow.add_node("persist", persist_state_node)
    workflow.add_node("retry", retry_node)
    workflow.add_node("blocked", blocked_node)

    workflow.set_entry_point("dispatch")
    workflow.add_conditional_edges(
        "dispatch",
        route_from_dispatch,
        {
            "goal_analysis": "goal_analysis",
            "dataset_analysis": "dataset_analysis",
            "campaign_planning": "campaign_planning",
            "personalization": "personalization",
            "authorization": "authorization",
            "job_creation": "job_creation",
            "execution": "execution",
            "verification": "verification",
            "persist": "persist",
        },
    )

    workflow.add_conditional_edges(
        "goal_analysis",
        route_after_goal,
        {"dataset_analysis": "dataset_analysis", "blocked": "blocked"},
    )
    workflow.add_conditional_edges(
        "dataset_analysis",
        route_after_data,
        {"campaign_planning": "campaign_planning", "blocked": "blocked"},
    )
    workflow.add_conditional_edges(
        "campaign_planning",
        route_after_validation,
        {"personalization": "personalization", "blocked": "blocked"},
    )
    workflow.add_edge("personalization", "authorization")
    workflow.add_conditional_edges(
        "authorization",
        route_after_authorization,
        {"job_creation": "job_creation", "blocked": "blocked"},
    )
    workflow.add_edge("job_creation", "execution")
    workflow.add_edge("execution", "verification")
    workflow.add_conditional_edges(
        "verification",
        route_after_verification,
        {"retry": "retry", "persist": "persist"},
    )
    workflow.add_edge("retry", "execution")
    workflow.add_edge("persist", END)
    workflow.add_edge("blocked", END)

    return workflow.compile()


automation_graph = build_automation_graph()


# ==========================================================================
# Public API
# ==========================================================================


def new_run_id() -> str:
    return f"arun_{uuid.uuid4().hex[:12]}"


async def run_automation_agent(
    user_message: str,
    user_id: str = "default_user",
    session_id: Optional[str] = None,
    authorization_scope: Optional[Dict[str, Any]] = None,
    dataset_id: Optional[str] = None,
    sender_profile: Optional[Dict[str, Any]] = None,
    startup_info: Optional[Dict[str, Any]] = None,
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Runs the automation workflow once, from the user's request.

    Returns when the campaign has been handed to the worker and verified once --
    typically with decision CONTINUE, because the worker is still sending. Call
    `resume_automation_run` to advance the run; nothing is lost in between,
    because the state is persisted.
    """
    state: AutomationState = {
        "run_id": run_id or new_run_id(),
        "session_id": session_id,
        "user_id": user_id,
        "user_message": user_message,
        "phase": AutomationPhase.USER_REQUEST.value,
        "retry_count": 0,
        "errors": [],
        "response": "",
        "authorization_scope": dict(authorization_scope or {}),
        "dataset_id": dataset_id,
        "sender_profile": sender_profile or {},
        "startup_info": startup_info or {},
    }

    await _persist(state)
    logger.info("Automation run '%s' starting.", state["run_id"])
    final_state = await automation_graph.ainvoke(state)
    await _persist(final_state)
    return dict(final_state)


async def resume_automation_run(run_id: str) -> Optional[Dict[str, Any]]:
    """
    Continues a persisted run from the phase it reached.

    This is how the workflow survives a restart: the run's state lives in
    MongoDB, so a different process -- or the same one after a crash -- can pick
    it up. Because job creation is idempotent and completed jobs are terminal,
    resuming never re-creates work or re-sends to anyone.
    """
    record = await db_manager.get_automation_run(run_id)
    if not record:
        logger.warning("Cannot resume automation run '%s': not found.", run_id)
        return None

    state: AutomationState = {
        "run_id": record.get("run_id"),
        "session_id": record.get("session_id"),
        "user_id": record.get("user_id"),
        "user_message": record.get("user_message", ""),
        "phase": record.get("phase", AutomationPhase.USER_REQUEST.value),
        "decision": record.get("decision"),
        "retry_count": record.get("retry_count", 0),
        "errors": list(record.get("errors") or []),
        "response": record.get("response", ""),
        "authorization_scope": dict(record.get("authorization_scope") or {}),
        "campaign_id": record.get("campaign_id"),
        "goal_analysis": record.get("goal_analysis"),
        "dataset_assessment": record.get("dataset_assessment"),
        "plan_draft": record.get("plan_draft"),
        "plan_validation": record.get("plan_validation"),
        "personalization": record.get("personalization"),
        "authorization": record.get("authorization"),
        "job_creation": record.get("job_creation"),
        "execution": record.get("execution"),
        "verification": record.get("verification"),
        "dataset_id": (record.get("dataset_assessment") or {}).get("dataset_id"),
        "sender_profile": {},
        "startup_info": {},
    }

    logger.info("Resuming automation run '%s' from phase '%s'.", run_id, state["phase"])
    final_state = await automation_graph.ainvoke(state)
    await _persist(final_state)
    return dict(final_state)


async def get_automation_run(run_id: str) -> Optional[Dict[str, Any]]:
    return await db_manager.get_automation_run(run_id)


async def list_automation_runs(
    phase: Optional[str] = None, skip: int = 0, limit: int = 50
) -> List[Dict[str, Any]]:
    return await db_manager.list_automation_runs(phase=phase, skip=skip, limit=limit)
