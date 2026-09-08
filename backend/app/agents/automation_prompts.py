"""
Prompts for the automation workflow's LLM steps.

Each prompt states the schema it must answer in and, just as importantly, what
the model is not permitted to do. The prompts are the first line of that; the
guardrails module is the line that actually holds, because a prompt is a request
and a validator is a rule.
"""

import json
from typing import Any, Dict, List

from app.agents.guardrails import (
    APPROVED_ACTIONS,
    APPROVED_AUDIENCE_TYPES,
    APPROVED_CAMPAIGN_TYPES,
    APPROVED_CHANNELS,
    APPROVED_PERSONALIZATION_FIELDS,
)

COMMON_RULES = """
HARD RULES -- these are enforced in code after you answer, so breaking them only
gets your answer discarded:
- Never state that anything was sent, delivered or completed. You do not perform
  actions and you cannot observe their results.
- Never invent counts, recipients, datasets, message ids or provider responses.
  If you do not know a number, omit the field.
- Never include an API key, token, password, connection string or any other
  credential.
- Never include a URL, hostname or endpoint.
- Only choose actions from the approved list. Do not describe new capabilities.
- Return ONLY a single JSON object. No prose, no markdown fences, no commentary.
"""


def goal_analysis_prompt(user_message: str, available_datasets: List[Dict[str, Any]]) -> str:
    dataset_summary = [
        {
            "dataset_id": d.get("dataset_id"),
            "filename": d.get("filename"),
            "valid_contacts": (d.get("statistics") or {}).get("valid_contacts", 0),
        }
        for d in available_datasets
    ]
    return f"""You are the goal-analysis step of an outreach automation system.
Read the user's request and describe what they are asking for.

{COMMON_RULES}

Approved campaign types: {sorted(APPROVED_CAMPAIGN_TYPES)}
Approved audience types: {sorted(APPROVED_AUDIENCE_TYPES)}
Approved channels: {sorted(APPROVED_CHANNELS)}

Datasets that exist in the system (do not invent others):
{json.dumps(dataset_summary, indent=2)}

User request:
{user_message}

Answer with this JSON object:
{{
  "is_automation_request": bool,
  "automation_type": "outreach" | "support" | "other",
  "campaign_type": one of the approved campaign types,
  "objective": "one sentence",
  "audience": [approved audience types],
  "channel": "EMAIL",
  "dataset_hint": dataset_id the user referred to, or null,
  "requested_dry_run": true when the user asked for a preview, test or dry run,
  "tone": "professional" | "friendly" | "direct",
  "constraints": {{}},
  "ambiguities": ["anything a human should confirm"],
  "confidence": 0.0-1.0,
  "reasoning": "short explanation"
}}"""


def campaign_planning_prompt(
    goal: Dict[str, Any], assessment: Dict[str, Any], user_message: str
) -> str:
    return f"""You are the planning step of an outreach automation system.
Draft a campaign plan for the goal below, using only the data described.

{COMMON_RULES}

Approved actions (choose from these only):
{json.dumps(APPROVED_ACTIONS, indent=2)}

Approved personalization fields: {sorted(APPROVED_PERSONALIZATION_FIELDS)}
Approved campaign types: {sorted(APPROVED_CAMPAIGN_TYPES)}

Understood goal:
{json.dumps(goal, indent=2)}

Data actually available (these counts are measured, do not change them):
{json.dumps(assessment, indent=2)}

Original request:
{user_message}

Answer with this JSON object:
{{
  "campaign_type": approved campaign type,
  "objective": "one sentence",
  "audience": [approved audience types],
  "channel": "EMAIL",
  "message_strategy": "how the message should be framed",
  "tone": "professional" | "friendly" | "direct",
  "personalization_fields": [approved personalization fields],
  "suggested_subject_line": "short subject" or null,
  "call_to_action": "the single ask" or null,
  "steps": [{{"order": 1, "action": approved action, "description": "...", "requires_authorization": bool}}],
  "estimated_recipients": integer taken from the measured counts above,
  "rate_per_minute": number between 1 and 600,
  "max_attempts": integer between 1 and 10,
  "requires_human_approval": bool,
  "notes": "anything the approver should know"
}}"""
