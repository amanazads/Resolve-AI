"""
Guardrails for the autonomous automation workflow.

The workflow lets an LLM interpret instructions, classify contacts, draft plans,
personalize messages and choose among approved tools. This module is what stops
it doing anything else. Concretely it enforces:

  * Only actions in APPROVED_ACTIONS may appear in a plan. An unknown action is
    rejected, not "interpreted".
  * No plan may carry a credential, secret or token.
  * No plan may direct the system at an arbitrary URL.
  * A model response that does not parse into its declared schema is discarded
    rather than partially believed.
  * Authorization is computed from the caller's scope. There is no code path by
    which model output can grant it.
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from app.agents.automation_schemas import (
    AuthorizationDecision,
    CampaignPlanDraft,
    DatasetAssessment,
    PlanValidation,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# ==========================================================================
# Approved action registry
# ==========================================================================

#: The only actions a generated plan may contain. Each maps to code that already
#: exists and is already constrained; the model chooses among them, it does not
#: describe new capabilities into being.
APPROVED_ACTIONS: Dict[str, str] = {
    "ANALYZE_AUDIENCE": "Count and segment contacts already in the database.",
    "GENERATE_MESSAGES": "Generate personalized messages through the personalization engine.",
    "SEND_EMAIL": "Send one email per recipient through the configured email provider.",
    "DRY_RUN_PREVIEW": "Generate messages and record what would be sent, without sending.",
    "VERIFY_DELIVERY": "Read back provider confirmations for the campaign's jobs.",
}

#: Actions that actually contact a person. These require authorization.
SIDE_EFFECTING_ACTIONS = frozenset({"SEND_EMAIL"})

#: Channels the engine can execute today.
APPROVED_CHANNELS = frozenset({"EMAIL"})

#: Campaign types the personalization engine has templates and prompts for.
APPROVED_CAMPAIGN_TYPES = frozenset(
    {"INVESTOR_OUTREACH", "JOB_OUTREACH", "INTERNSHIP_OUTREACH", "CUSTOM_OUTREACH"}
)

#: Contact types the classifier produces.
APPROVED_AUDIENCE_TYPES = frozenset(
    {"INVESTOR", "VC", "FOUNDER", "HR", "RECRUITER", "OTHER", "UNKNOWN"}
)

#: Recipient fields the personalization engine is allowed to use.
APPROVED_PERSONALIZATION_FIELDS = frozenset(
    {"first_name", "last_name", "full_name", "company", "role", "investment_focus", "location"}
)

#: Anything that looks like a credential must never reach a plan or a prompt.
CREDENTIAL_PATTERNS = [
    re.compile(r"\b(?:api[_-]?key|secret|password|passwd|client[_-]?secret)\b", re.IGNORECASE),
    re.compile(r"\b(?:access[_-]?token|refresh[_-]?token|bearer\s+[A-Za-z0-9._-]{12,})\b", re.IGNORECASE),
    re.compile(r"\bya29\.[A-Za-z0-9._-]+"),                 # Google access token
    re.compile(r"\bsk-[A-Za-z0-9]{16,}"),                   # OpenAI-style key
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                    # AWS access key id
    re.compile(r"\bmongodb(?:\+srv)?://[^\s]+"),            # connection string
    re.compile(r"GMAIL_TOKEN_ENCRYPTION_KEY|GOOGLE_CLIENT_SECRET|GEMINI_API_KEY"),
]

#: A plan is a description of work over data we already hold. It has no business
#: naming an endpoint; the only network calls in this system are made by vetted
#: integration code against fixed provider endpoints.
URL_PATTERN = re.compile(r"\b(?:https?://|ftp://|file://)\S+", re.IGNORECASE)

#: Field limits, so a model cannot plan a campaign at an absurd rate.
MAX_RATE_PER_MINUTE = 600.0
MAX_ATTEMPTS = 10


class GuardrailViolation(Exception):
    """Raised when model-supplied content breaks a hard rule."""


# ==========================================================================
# Structured output parsing
# ==========================================================================


def extract_json(raw: str) -> Optional[Any]:
    """Pulls the first JSON object or array out of a model response."""
    if not raw:
        return None

    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text)
        text = re.sub(r"```\s*$", "", text).strip()

    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except (json.JSONDecodeError, ValueError):
                continue
    return None


def parse_structured(raw: str, model: Type[T]) -> Optional[T]:
    """
    Parses a model response into `model`, or returns None.

    None means the caller falls back to a deterministic default. A partially
    understood response is never accepted: half-parsed model output is how an
    invented field ends up looking like a fact.
    """
    payload = extract_json(raw)
    if payload is None:
        logger.warning("LLM response contained no parseable JSON for %s.", model.__name__)
        return None
    if not isinstance(payload, dict):
        logger.warning("LLM response for %s was not a JSON object.", model.__name__)
        return None

    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        logger.warning("LLM response failed %s validation: %s", model.__name__, exc)
        return None


# ==========================================================================
# Content guards
# ==========================================================================


def find_credentials(text: str) -> List[str]:
    """Returns the names of credential patterns present in `text`."""
    hits = []
    for pattern in CREDENTIAL_PATTERNS:
        match = pattern.search(text or "")
        if match:
            hits.append(match.group(0)[:40])
    return hits


def find_urls(text: str) -> List[str]:
    return URL_PATTERN.findall(text or "")


def assert_no_credentials(payload: Any, where: str = "plan") -> None:
    """Raises GuardrailViolation if anything credential-shaped is present."""
    serialized = json.dumps(payload, default=str)
    hits = find_credentials(serialized)
    if hits:
        raise GuardrailViolation(
            f"Credential-like content found in the {where} and rejected: {hits}"
        )


# ==========================================================================
# Plan validation
# ==========================================================================


def validate_plan(
    draft: CampaignPlanDraft,
    assessment: Optional[DatasetAssessment] = None,
) -> PlanValidation:
    """
    Checks a drafted plan against the guardrails.

    Deterministic on purpose: this is the gate the model's plan has to pass, so
    it cannot itself be something the model influences.
    """
    errors: List[str] = []
    warnings: List[str] = []
    approved: List[str] = []
    rejected: List[str] = []

    serialized = draft.model_dump(mode="json")

    # 1. No credentials, anywhere.
    credential_hits = find_credentials(json.dumps(serialized, default=str))
    if credential_hits:
        errors.append(
            f"Plan contains credential-like content and was rejected: {credential_hits}"
        )

    # 2. No URLs. The plan describes work over stored data; it does not get to
    #    point the system at an address.
    url_hits = find_urls(json.dumps(serialized, default=str))
    if url_hits:
        errors.append(
            f"Plan references URLs, which are not permitted in a generated plan: {url_hits}"
        )

    # 3. Every action must be on the approved list.
    if not draft.steps:
        errors.append("Plan contains no steps.")
    for step in draft.steps:
        action = (step.action or "").strip().upper()
        if action in APPROVED_ACTIONS:
            approved.append(action)
        else:
            rejected.append(step.action)
            errors.append(
                f"Action '{step.action}' is not an approved action. "
                f"Approved actions: {sorted(APPROVED_ACTIONS)}"
            )

    # 4. Channel, campaign type, audience and personalization fields.
    if (draft.channel or "").upper() not in APPROVED_CHANNELS:
        errors.append(
            f"Channel '{draft.channel}' is not supported. Supported: {sorted(APPROVED_CHANNELS)}"
        )

    if (draft.campaign_type or "").upper() not in APPROVED_CAMPAIGN_TYPES:
        errors.append(f"Campaign type '{draft.campaign_type}' is not recognised.")

    unknown_audience = [
        a for a in draft.audience if (a or "").upper() not in APPROVED_AUDIENCE_TYPES
    ]
    if unknown_audience:
        errors.append(f"Unknown audience type(s): {unknown_audience}")

    unknown_fields = [
        f for f in draft.personalization_fields if f not in APPROVED_PERSONALIZATION_FIELDS
    ]
    if unknown_fields:
        # Not fatal: drop them rather than refusing the whole plan.
        warnings.append(
            f"Dropping personalization field(s) the engine cannot use: {unknown_fields}"
        )

    # 5. Execution limits.
    if draft.rate_per_minute <= 0 or draft.rate_per_minute > MAX_RATE_PER_MINUTE:
        errors.append(
            f"rate_per_minute must be between 1 and {MAX_RATE_PER_MINUTE:.0f}, "
            f"got {draft.rate_per_minute}."
        )
    if draft.max_attempts < 1 or draft.max_attempts > MAX_ATTEMPTS:
        errors.append(f"max_attempts must be between 1 and {MAX_ATTEMPTS}.")

    # 6. The plan must match the data that actually exists.
    if assessment is not None:
        if not assessment.sufficient:
            errors.append(f"Insufficient data for this plan: {assessment.reason}")
        elif draft.estimated_recipients > assessment.matched_contacts:
            warnings.append(
                f"Plan estimates {draft.estimated_recipients} recipients but only "
                f"{assessment.matched_contacts} contacts match. The real audience size wins."
            )

    return PlanValidation(
        is_valid=not errors,
        errors=errors,
        warnings=warnings,
        approved_actions=sorted(set(approved)),
        rejected_actions=rejected,
    )


def sanitize_plan(draft: CampaignPlanDraft) -> CampaignPlanDraft:
    """Drops unusable personalization fields and normalises casing."""
    cleaned = draft.model_copy(deep=True)
    cleaned.channel = (cleaned.channel or "EMAIL").upper()
    cleaned.campaign_type = (cleaned.campaign_type or "CUSTOM_OUTREACH").upper()
    cleaned.audience = [a.upper() for a in cleaned.audience if a]
    cleaned.personalization_fields = [
        f for f in cleaned.personalization_fields if f in APPROVED_PERSONALIZATION_FIELDS
    ] or ["first_name", "company"]
    for step in cleaned.steps:
        step.action = (step.action or "").strip().upper()
    return cleaned


# ==========================================================================
# Authorization
# ==========================================================================


def evaluate_authorization(
    scope: Dict[str, Any],
    plan: CampaignPlanDraft,
    validation: PlanValidation,
    recipient_count: int,
    requested_dry_run: bool = False,
) -> AuthorizationDecision:
    """
    Decides whether this run may send, from the caller-supplied scope.

    `scope` arrives with the API request, from an authenticated principal. No
    model output reaches this function, and there is no argument by which a
    model can widen the scope it is given.

    Recognised scope keys:
        authorized_by          -- identity of the approver (required to send)
        allow_send             -- explicit permission to contact real people
        max_recipients         -- ceiling on this run's audience
        allowed_actions        -- optional narrowing of the approved actions
        allow_dry_run          -- permission to preview (defaults to True)
    """
    reasons: List[str] = []
    scope = dict(scope or {})

    # A plan that failed validation can never be authorized.
    if not validation.is_valid:
        return AuthorizationDecision(
            authorized=False,
            decision="DENIED",
            scope=scope,
            reasons=["The plan did not pass validation, so it cannot be authorized."]
            + validation.errors,
            requires_human_approval=True,
        )

    granted_by = scope.get("authorized_by")
    allow_send = bool(scope.get("allow_send", False))
    allow_dry_run = bool(scope.get("allow_dry_run", True))
    max_recipients = scope.get("max_recipients")

    needs_send = any(
        step.action in SIDE_EFFECTING_ACTIONS for step in plan.steps
    ) and not requested_dry_run

    # Dry runs contact nobody, so they need only the dry-run permission.
    if not needs_send:
        if not allow_dry_run:
            return AuthorizationDecision(
                authorized=False,
                decision="DENIED",
                dry_run_only=True,
                scope=scope,
                reasons=["Dry runs are not permitted by the supplied authorization scope."],
                requires_human_approval=True,
            )
        return AuthorizationDecision(
            authorized=True,
            decision="GRANTED",
            dry_run_only=True,
            granted_by=granted_by,
            scope=scope,
            reasons=["Dry run approved: messages are generated but nothing is sent."],
            requires_human_approval=False,
        )

    if not allow_send:
        reasons.append(
            "The authorization scope does not permit sending. A human must approve "
            "this campaign (set allow_send and authorized_by) before it can contact anyone."
        )
    if not granted_by:
        reasons.append("No approver identity was supplied (authorized_by).")

    if max_recipients is not None:
        try:
            ceiling = int(max_recipients)
        except (TypeError, ValueError):
            reasons.append(f"max_recipients is not a number: {max_recipients!r}")
            ceiling = 0
        if recipient_count > ceiling:
            reasons.append(
                f"This run would contact {recipient_count} recipients, above the "
                f"authorized ceiling of {ceiling}."
            )

    allowed_actions = scope.get("allowed_actions")
    if allowed_actions:
        allowed = {str(a).upper() for a in allowed_actions}
        outside = [a for a in validation.approved_actions if a not in allowed]
        if outside:
            reasons.append(f"Action(s) outside the authorized scope: {outside}")

    if reasons:
        return AuthorizationDecision(
            authorized=False,
            decision="PENDING_HUMAN_APPROVAL" if not allow_send else "DENIED",
            scope=scope,
            reasons=reasons,
            requires_human_approval=True,
        )

    return AuthorizationDecision(
        authorized=True,
        decision="GRANTED",
        dry_run_only=False,
        granted_by=str(granted_by),
        scope=scope,
        reasons=[f"Sending approved by '{granted_by}' for up to {recipient_count} recipients."],
        requires_human_approval=False,
    )
