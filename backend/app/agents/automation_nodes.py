"""
Specialized nodes for the autonomous automation workflow.

The division of labour is the point of this module:

  LLM decides           code decides
  -----------           ------------
  what the user meant   what data exists (counted from MongoDB)
  which audience        whether the plan is allowed (guardrails)
  the plan's shape      whether the run may send (caller's scope)
  the message wording   whether a message is valid (validator)
  which approved tool   what actually happened (provider confirmations)

Nothing a model returns is treated as an observation. Every number that governs
behaviour -- recipient counts, jobs created, messages confirmed -- is read from
the database or from the campaign worker.
"""

import logging
import uuid
from typing import Any, Dict, List

from app.agents.automation_prompts import campaign_planning_prompt, goal_analysis_prompt
from app.agents.automation_schemas import (
    AuthorizationDecision,
    AutomationPhase,
    CampaignPlanDraft,
    DatasetAssessment,
    ExecutionHandoff,
    GoalAnalysis,
    JobCreationResult,
    PersonalizationSpec,
    PlannedStep,
    PlanValidation,
    VerificationResult,
    WorkflowDecision,
)
from app.agents.guardrails import (
    APPROVED_PERSONALIZATION_FIELDS,
    GuardrailViolation,
    assert_no_credentials,
    evaluate_authorization,
    parse_structured,
    sanitize_plan,
    validate_plan,
)
from app.agents.state import AutomationState
from app.automation.models import JobStatus
from app.automation.queue import campaign_job_queue
from app.automation.worker import campaign_execution_service
from app.campaigns.models import CampaignStatus
from app.database.mongodb import db_manager
from app.config import settings
from app.llm.client import invoke_llm
from app.permissions.middleware import PermissionDenied, enforcer
from app.permissions.models import (
    GrantPermissionRequest,
    PermissionScope,
)
from app.permissions.service import permission_service
from app.personalization.generator import PersonalizationGenerator
from app.personalization.validator import ValidationStatus

logger = logging.getLogger(__name__)

#: How many times execution may be retried before the run is left for a human.
MAX_EXECUTION_RETRIES = 2


def _get(state: AutomationState, key: str, model):
    """Rehydrates a persisted step output back into its model."""
    raw = state.get(key)
    return model.model_validate(raw) if raw else None


# ==========================================================================
# 1. UNDERSTAND_GOAL
# ==========================================================================


async def goal_analysis_node(state: AutomationState) -> Dict[str, Any]:
    """
    Interprets the user's request into a structured goal.

    The model is given the datasets that actually exist so it can point at one,
    and its answer is parsed into GoalAnalysis or discarded. A discarded answer
    falls back to a conservative default rather than a guess.
    """
    user_message = state.get("user_message", "")
    logger.info("--- goal_analysis_node --- %r", user_message[:120])

    datasets = await db_manager.list_datasets(skip=0, limit=20)
    raw = invoke_llm(goal_analysis_prompt(user_message, datasets))
    analysis = parse_structured(raw, GoalAnalysis)

    if analysis is None:
        logger.warning("Goal analysis could not be parsed; attempting safe heuristic recovery.")
        from app.campaigns.planner import CampaignPlanner
        heuristic = CampaignPlanner._heuristic_campaign_plan(user_message)
        analysis = GoalAnalysis(
            is_automation_request=True,
            automation_type="outreach",
            campaign_type=heuristic.campaign_type.value,
            objective=user_message[:200],
            audience=[a.upper() for a in heuristic.audience],
            channel="EMAIL",
            confidence=0.75,
            reasoning="Recovered structured goal via deterministic task planner.",
            ambiguities=[],
        )

    # The model may only point at a dataset that exists.
    known_ids = {d.get("dataset_id") for d in datasets}
    if analysis.dataset_hint and analysis.dataset_hint not in known_ids:
        analysis.ambiguities.append(
            f"The named dataset '{analysis.dataset_hint}' does not exist; it was ignored."
        )
        analysis.dataset_hint = None

    return {
        "phase": AutomationPhase.UNDERSTAND_GOAL.value,
        "goal_analysis": analysis.model_dump(mode="json"),
    }


# ==========================================================================
# 2. CHECK_AVAILABLE_DATA
# ==========================================================================


async def dataset_analysis_node(state: AutomationState) -> Dict[str, Any]:
    """
    Counts the audience. Entirely deterministic.

    An invented recipient count would decide how many people get emailed, so no
    part of this comes from the model -- only the *choice* of audience types
    does, and those were validated against the classifier's vocabulary.
    """
    goal = _get(state, "goal_analysis", GoalAnalysis) or GoalAnalysis()
    dataset_id = state.get("dataset_id") or goal.dataset_hint
    logger.info("--- dataset_analysis_node --- dataset=%s", dataset_id)

    if not dataset_id:
        datasets = await db_manager.list_datasets(skip=0, limit=1)
        dataset_id = datasets[0].get("dataset_id") if datasets else None

    if not dataset_id:
        assessment = DatasetAssessment(
            dataset_found=False,
            sufficient=False,
            reason="No contact dataset has been uploaded yet.",
            data_gaps=["Upload a CSV or XLSX of contacts before running a campaign."],
        )
        return {
            "phase": AutomationPhase.CHECK_AVAILABLE_DATA.value,
            "dataset_assessment": assessment.model_dump(mode="json"),
        }

    record = await db_manager.get_dataset(dataset_id)
    total = await db_manager.count_contacts({"dataset_id": dataset_id})
    valid = await db_manager.count_contacts({"dataset_id": dataset_id, "is_valid": True})

    breakdown: Dict[str, int] = {}
    matched = 0
    for audience_type in goal.audience or []:
        # Normalise to uppercase so "Founder" == "FOUNDER" in the DB query
        normalised_type = audience_type.strip().upper()
        count = await db_manager.count_contacts(
            {"dataset_id": dataset_id, "contact_type": normalised_type, "is_valid": True}
        )
        breakdown[normalised_type] = count
        matched += count

    # An empty audience means everyone valid in the dataset.
    if not goal.audience:
        matched = valid
        breakdown["ALL"] = valid

    contacts = await db_manager.query_contacts(
        {"dataset_id": dataset_id, "is_valid": True}, skip=0, limit=500
    )
    missing_email = sum(1 for c in contacts if not (c.get("email") or "").strip())

    gaps: List[str] = []
    if missing_email:
        gaps.append(f"{missing_email} contact(s) in the sample have no email address.")
    if goal.audience and matched == 0:
        gaps.append(
            f"No contacts match the requested audience {goal.audience} in this dataset."
        )

    assessment = DatasetAssessment(
        dataset_id=dataset_id,
        dataset_found=record is not None,
        total_contacts=total,
        valid_contacts=valid,
        matched_contacts=matched,
        audience_breakdown=breakdown,
        contacts_missing_email=missing_email,
        data_gaps=gaps,
        sufficient=matched > 0,
        reason=(
            f"{matched} contact(s) match the requested audience."
            if matched > 0
            else "No contacts match the requested audience, so there is nobody to contact."
        ),
    )

    return {
        "phase": AutomationPhase.CHECK_AVAILABLE_DATA.value,
        "dataset_id": dataset_id,
        "dataset_assessment": assessment.model_dump(mode="json"),
    }


# ==========================================================================
# 3. CREATE_PLAN  +  4. VALIDATE_PLAN
# ==========================================================================


async def campaign_planning_node(state: AutomationState) -> Dict[str, Any]:
    """
    Drafts the plan with the LLM, then validates it deterministically.

    Planning and validation live in one node because a draft that has not been
    validated is not usable for anything, and keeping them together removes any
    path where an unvalidated plan reaches the next step.
    """
    goal = _get(state, "goal_analysis", GoalAnalysis) or GoalAnalysis()
    assessment = _get(state, "dataset_assessment", DatasetAssessment) or DatasetAssessment()
    logger.info("--- campaign_planning_node ---")

    raw = invoke_llm(
        campaign_planning_prompt(
            goal.model_dump(mode="json"),
            assessment.model_dump(mode="json"),
            state.get("user_message", ""),
        )
    )
    draft = parse_structured(raw, CampaignPlanDraft)

    if draft is None:
        # A deterministic plan from the understood goal, rather than nothing.
        logger.warning("Plan could not be parsed; falling back to a deterministic draft.")
        draft = CampaignPlanDraft(
            campaign_type=goal.campaign_type,
            objective=goal.objective or state.get("user_message", "")[:200],
            audience=goal.audience,
            channel=goal.channel,
            message_strategy="Concise, factual outreach based on the recipient's profile.",
            tone=goal.tone,
            personalization_fields=["first_name", "company"],
            steps=[
                PlannedStep(order=1, action="GENERATE_MESSAGES", requires_authorization=False),
                PlannedStep(order=2, action="SEND_EMAIL", requires_authorization=True),
                PlannedStep(order=3, action="VERIFY_DELIVERY", requires_authorization=False),
            ],
            notes="Generated deterministically because the model's plan could not be parsed.",
        )

    draft = sanitize_plan(draft)

    # The measured audience size always wins over the model's estimate.
    draft.estimated_recipients = assessment.matched_contacts

    try:
        assert_no_credentials(draft.model_dump(mode="json"), where="campaign plan")
    except GuardrailViolation as exc:
        validation = PlanValidation(is_valid=False, errors=[str(exc)])
        return {
            "phase": AutomationPhase.VALIDATE_PLAN.value,
            "plan_draft": draft.model_dump(mode="json"),
            "plan_validation": validation.model_dump(mode="json"),
            "errors": list(state.get("errors", [])) + [str(exc)],
        }

    validation = validate_plan(draft, assessment)
    if validation.warnings:
        logger.info("Plan validation warnings: %s", validation.warnings)
    if not validation.is_valid:
        logger.warning("Plan rejected by guardrails: %s", validation.errors)

    return {
        "phase": AutomationPhase.VALIDATE_PLAN.value,
        "plan_draft": draft.model_dump(mode="json"),
        "plan_validation": validation.model_dump(mode="json"),
    }


# ==========================================================================
# 5. PERSONALIZE
# ==========================================================================


async def personalization_node(state: AutomationState) -> Dict[str, Any]:
    """
    Fixes the message settings and generates one real sample.

    The sample matters: whoever authorizes this run should be approving an
    actual message, not a description of one. Bulk generation deliberately does
    not happen here -- that is one job at a time in the campaign worker.
    """
    plan = _get(state, "plan_draft", CampaignPlanDraft) or CampaignPlanDraft()
    assessment = _get(state, "dataset_assessment", DatasetAssessment) or DatasetAssessment()
    logger.info("--- personalization_node ---")

    allowed = [f for f in plan.personalization_fields if f in APPROVED_PERSONALIZATION_FIELDS]
    spec = PersonalizationSpec(
        allowed_fields=allowed or ["first_name", "company"],
        tone=plan.tone,
        instructions=plan.message_strategy,
    )

    sample_contacts = (
        await db_manager.query_contacts(
            {"dataset_id": assessment.dataset_id, "is_valid": True}, skip=0, limit=1
        )
        if assessment.dataset_id
        else []
    )
    if not sample_contacts:
        spec.warnings.append("No contact was available to generate a sample message from.")
        return {
            "phase": AutomationPhase.PERSONALIZE.value,
            "personalization": spec.model_dump(mode="json"),
        }

    sample = sample_contacts[0]
    try:
        message = PersonalizationGenerator().generate_single(
            campaign_objective=plan.objective,
            campaign_type=plan.campaign_type,
            recipient=sample,
            sender_profile=state.get("sender_profile") or {},
            startup_info=state.get("startup_info") or {},
            campaign_instructions=plan.message_strategy,
            allowed_fields=spec.allowed_fields,
            tone=plan.tone,
        )
    except Exception as exc:
        logger.exception("Sample message generation failed")
        spec.warnings.append(f"Sample generation failed: {exc}")
        return {
            "phase": AutomationPhase.PERSONALIZE.value,
            "personalization": spec.model_dump(mode="json"),
        }

    spec.sample_recipient = {
        "contact_id": sample.get("contact_id"),
        "email": sample.get("email"),
        "company": sample.get("company"),
    }
    spec.sample_subject = message.subject
    spec.sample_body = message.body
    spec.validation_status = message.validation_status.value
    spec.warnings.extend(message.warnings)
    spec.generated = message.validation_status != ValidationStatus.INVALID

    return {
        "phase": AutomationPhase.PERSONALIZE.value,
        "personalization": spec.model_dump(mode="json"),
    }


# ==========================================================================
# 6. CHECK_AUTHORIZATION
# ==========================================================================


async def authorization_node(state: AutomationState) -> Dict[str, Any]:
    """
    Decides whether this run may contact anyone.

    Computed from the caller-supplied scope and the validation result. No model
    output reaches this decision, and there is no branch that skips it: the graph
    routes to job creation only when `authorized` is true.
    """
    plan = _get(state, "plan_draft", CampaignPlanDraft) or CampaignPlanDraft()
    validation = _get(state, "plan_validation", PlanValidation) or PlanValidation()
    assessment = _get(state, "dataset_assessment", DatasetAssessment) or DatasetAssessment()
    goal = _get(state, "goal_analysis", GoalAnalysis) or GoalAnalysis()
    logger.info("--- authorization_node ---")

    scope = dict(state.get("authorization_scope") or {})
    user_id = state.get("user_id") or "default_user"
    dry_run = goal.requested_dry_run or bool(scope.get("dry_run"))
    from app.integrations.registry import get_email_provider
    try:
        active_p = get_email_provider()
        integration = getattr(active_p, "name", None) or settings.EMAIL_PROVIDER
    except Exception:
        integration = settings.EMAIL_PROVIDER

    # 1. A permission the user granted earlier. This is what stops the agent
    #    asking again: an existing grant covering this dataset, audience and
    #    mailbox authorizes the run without a fresh approval in the request.
    if validation.is_valid and not dry_run:
        stored = await enforcer.check(
            user_id=user_id,
            scope=PermissionScope.EMAIL_SEND,
            integration=integration,
            dataset_id=assessment.dataset_id,
            audience=plan.audience,
            recipient_count=assessment.matched_contacts,
            verify_integration=False,
        )
        if stored.allowed:
            decision = AuthorizationDecision(
                authorized=True,
                decision="GRANTED",
                dry_run_only=False,
                granted_by=(stored.matched_grant.granted_by if stored.matched_grant else None),
                source="stored_grant",
                grant_id=stored.grant_id,
                scope=scope,
                reasons=stored.reasons,
                requires_human_approval=False,
            )
            logger.info(
                "Authorization satisfied by stored grant %s; not asking again.",
                stored.grant_id,
            )
            return {
                "phase": AutomationPhase.CHECK_AUTHORIZATION.value,
                "authorization": decision.model_dump(mode="json"),
            }

    # 2. Otherwise fall back to an approval supplied with this request.
    decision = evaluate_authorization(
        scope=scope,
        plan=plan,
        validation=validation,
        recipient_count=assessment.matched_contacts,
        requested_dry_run=dry_run,
    )

    logger.info(
        "Authorization decision: %s (dry_run_only=%s) %s",
        decision.decision,
        decision.dry_run_only,
        decision.reasons,
    )
    return {
        "phase": AutomationPhase.CHECK_AUTHORIZATION.value,
        "authorization": decision.model_dump(mode="json"),
    }


# ==========================================================================
# 7. CREATE_JOBS
# ==========================================================================


async def job_creation_node(state: AutomationState) -> Dict[str, Any]:
    """
    Creates the campaign and one persistent job per recipient.

    Refuses to do anything without an authorization record -- a second check
    behind the graph's routing, so that a future edge cannot accidentally reach
    this node unauthorized.
    """
    authorization = _get(state, "authorization", AuthorizationDecision)
    if authorization is None or not authorization.authorized:
        reason = "Job creation was reached without authorization and was refused."
        logger.error(reason)
        return {
            "phase": AutomationPhase.BLOCKED.value,
            "job_creation": JobCreationResult(detail=reason).model_dump(mode="json"),
            "errors": list(state.get("errors", [])) + [reason],
        }

    plan = _get(state, "plan_draft", CampaignPlanDraft) or CampaignPlanDraft()
    assessment = _get(state, "dataset_assessment", DatasetAssessment) or DatasetAssessment()
    dry_run = authorization.dry_run_only
    logger.info("--- job_creation_node --- dry_run=%s", dry_run)

    campaign_id = state.get("campaign_id")
    if not campaign_id:
        campaign_id = f"camp_{uuid.uuid4().hex[:8]}"
        await db_manager.save_campaign(
            {
                "id": campaign_id,
                "campaign_id": campaign_id,
                "owner_id": state.get("user_id") or "default_user",
                "name": (plan.objective or "Automation campaign")[:80],
                "objective": plan.objective,
                "goal": plan.objective,
                "audience": plan.audience,
                "dataset_id": assessment.dataset_id,
                "communication_channel": plan.channel,
                "message_strategy": plan.message_strategy,
                "personalization_fields": plan.personalization_fields,
                "status": CampaignStatus.READY.value,
                "plan": plan.model_dump(mode="json"),
                "execution": {
                    "dry_run": dry_run,
                    "rate_per_minute": plan.rate_per_minute,
                    "max_attempts": plan.max_attempts,
                    "sender_profile": state.get("sender_profile") or {},
                    "startup_info": state.get("startup_info") or {},
                    "authorized_by": authorization.granted_by,
                    "automation_run_id": state.get("run_id"),
                },
                "total_contacts": assessment.matched_contacts,
            }
        )

    before = await campaign_job_queue.status_counts(campaign_id)
    created = await campaign_execution_service.enqueue_campaign(
        campaign_id,
        max_attempts=plan.max_attempts,
        dry_run=dry_run,
    )
    after = await campaign_job_queue.status_counts(campaign_id)

    # Turn a one-off approval into a stored, scoped grant, so the rest of this
    # campaign -- every recipient, every retry, every resume after a restart --
    # proceeds without asking the user again. The grant is pinned to this
    # campaign, dataset, audience and mailbox, so it cannot carry over to a
    # different campaign or a different audience.
    grant_id = authorization.grant_id
    if authorization.source != "stored_grant":
        scopes = [PermissionScope.CAMPAIGN_EXECUTE]
        if not dry_run:
            scopes.append(PermissionScope.EMAIL_SEND)
        try:
            grant = await permission_service.grant(
                GrantPermissionRequest(
                    user_id=state.get("user_id") or "default_user",
                    scopes=scopes,
                    integration=settings.EMAIL_PROVIDER if not dry_run else None,
                    campaign_id=campaign_id,
                    dataset_id=assessment.dataset_id,
                    audience=plan.audience,
                    max_recipients=max(assessment.matched_contacts, 1),
                    granted_by=str(authorization.granted_by or "user"),
                    note=(
                        f"Recorded from the approval for automation run "
                        f"{state.get('run_id')}."
                    ),
                )
            )
            grant_id = grant.grant_id
        except ValueError as exc:
            logger.warning("Could not record a permission grant for this run: %s", exc)

    result = JobCreationResult(
        campaign_id=campaign_id,
        jobs_created=created,
        already_existing=sum(before.values()),
        total_recipients=sum(after.values()),
        dry_run=dry_run,
        detail=f"{created} job(s) created for campaign '{campaign_id}'."
        + (f" Authorization stored as {grant_id}." if grant_id else ""),
    )
    return {
        "phase": AutomationPhase.CREATE_JOBS.value,
        "campaign_id": campaign_id,
        "job_creation": result.model_dump(mode="json"),
    }


# ==========================================================================
# 8. EXECUTE
# ==========================================================================


async def execution_node(state: AutomationState) -> Dict[str, Any]:
    """
    Hands the campaign to the persistent worker and returns.

    This is the boundary the design turns on: LangGraph orchestrates the
    workflow, the worker executes the recipients. A thousand-contact campaign is
    a thousand queue jobs processed with leases, retries and rate limiting -- it
    is never a thousand steps inside one graph invocation.
    """
    authorization = _get(state, "authorization", AuthorizationDecision)
    campaign_id = state.get("campaign_id")

    if authorization is None or not authorization.authorized or not campaign_id:
        reason = "Execution was reached without an authorized campaign and was refused."
        logger.error(reason)
        return {
            "phase": AutomationPhase.BLOCKED.value,
            "execution": ExecutionHandoff(detail=reason).model_dump(mode="json"),
            "errors": list(state.get("errors", [])) + [reason],
        }

    plan = _get(state, "plan_draft", CampaignPlanDraft) or CampaignPlanDraft()
    dry_run = authorization.dry_run_only
    logger.info("--- execution_node --- campaign=%s dry_run=%s", campaign_id, dry_run)

    try:
        await campaign_execution_service.start_campaign(
            campaign_id=campaign_id,
            dry_run=dry_run,
            rate_per_minute=plan.rate_per_minute,
            max_attempts=plan.max_attempts,
            sender_profile=state.get("sender_profile"),
            startup_info=state.get("startup_info"),
            run_in_background=True,
            principal_user_id=state.get("user_id") or "default_user",
        )
    except PermissionDenied as exc:
        # The grant was revoked between authorization and execution.
        reason = "Execution refused by the permission system: " + " ".join(exc.reasons)
        logger.warning(reason)
        return {
            "phase": AutomationPhase.BLOCKED.value,
            "execution": ExecutionHandoff(
                campaign_id=campaign_id, handed_off=False, detail=reason
            ).model_dump(mode="json"),
            "errors": list(state.get("errors", [])) + [reason],
        }

    handoff = ExecutionHandoff(
        campaign_id=campaign_id,
        handed_off=True,
        dry_run=dry_run,
        rate_per_minute=plan.rate_per_minute,
        detail=(
            "Campaign handed to the persistent worker. Individual recipients are "
            "processed as queue jobs, not inside this workflow invocation."
        ),
    )
    return {
        "phase": AutomationPhase.EXECUTE.value,
        "execution": handoff.model_dump(mode="json"),
    }


# ==========================================================================
# 9. VERIFY_RESULT
# ==========================================================================


async def verification_node(state: AutomationState) -> Dict[str, Any]:
    """
    Reads back what actually happened, and decides CONTINUE / RETRY / COMPLETE.

    `provider_confirmed` counts jobs that are SENT *and* carry a provider
    message id. That is the only evidence this system accepts that a message
    reached anyone; nothing here consults the model.
    """
    campaign_id = state.get("campaign_id")
    if not campaign_id:
        result = VerificationResult(
            verdict=WorkflowDecision.BLOCKED,
            reasons=["There is no campaign to verify."],
        )
        return {
            "phase": AutomationPhase.VERIFY_RESULT.value,
            "verification": result.model_dump(mode="json"),
            "decision": result.verdict.value,
        }

    progress = await campaign_execution_service.get_progress(campaign_id)

    # Count provider confirmations directly from the job records.
    confirmed = 0
    sent_jobs = await campaign_job_queue.list_jobs(
        campaign_id, status=JobStatus.SENT.value, limit=1000
    )
    for job in sent_jobs:
        if job.message_id or job.provider_response.get("message_id"):
            confirmed += 1

    reasons: List[str] = []
    if progress.sent != confirmed:
        reasons.append(
            f"{progress.sent - confirmed} job(s) are marked SENT without a provider "
            "message id and are not counted as confirmed."
        )

    verdict = WorkflowDecision.CONTINUE
    if progress.remaining == 0:
        if progress.retry_pending == 0:
            verdict = WorkflowDecision.COMPLETE
            reasons.append("Every job has reached a terminal state.")
    elif progress.retry_pending > 0 and progress.in_flight == 0:
        # Work is waiting on a backoff: another execution pass will pick it up.
        verdict = WorkflowDecision.RETRY
        reasons.append(f"{progress.retry_pending} job(s) are waiting to be retried.")
    else:
        reasons.append(f"{progress.remaining} job(s) are still in progress.")

    if progress.requires_manual_review:
        reasons.append(
            f"{progress.requires_manual_review} job(s) need a human decision "
            "(interrupted while sending)."
        )

    if state.get("retry_count", 0) >= MAX_EXECUTION_RETRIES and verdict == WorkflowDecision.RETRY:
        verdict = WorkflowDecision.BLOCKED
        reasons.append(
            f"Execution has already been retried {MAX_EXECUTION_RETRIES} time(s); "
            "leaving the remaining jobs for a human."
        )

    result = VerificationResult(
        campaign_id=campaign_id,
        total=progress.total,
        sent=progress.sent,
        provider_confirmed=confirmed,
        failed=progress.failed,
        skipped=progress.skipped,
        cancelled=progress.cancelled,
        remaining=progress.remaining,
        requires_manual_review=progress.requires_manual_review,
        percent_complete=progress.percent_complete,
        dry_run=progress.dry_run,
        verdict=verdict,
        reasons=reasons,
    )
    logger.info("--- verification_node --- %s: %s", verdict.value, reasons)

    return {
        "phase": AutomationPhase.VERIFY_RESULT.value,
        "verification": result.model_dump(mode="json"),
        "decision": verdict.value,
    }
