"""
Flexible task planner for Resolve AI.

Parses natural language objectives into structured TaskPlans.
Supports:
- Single action (e.g. 1 email)
- Multi action (e.g. 2-3 emails)
- Batch action (e.g. CSV with 10-1000 contacts)
- Customer support / RAG queries (e.g. refund policy, order lookup)
- Web search and calculations
- Multi-file reasoning (e.g. resume + recruiter list)
"""

import json
import logging
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.llm.client import invoke_llm
from app.tasks.models import ActionType, TaskType

logger = logging.getLogger(__name__)


class PlannedActionSpec(BaseModel):
    action_type: str
    description: str
    recipient_name: Optional[str] = None
    recipient_email: Optional[str] = None
    subject: Optional[str] = None
    body_prompt: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)


class TaskPlanDraft(BaseModel):
    task_type: str
    objective: str
    reasoning: str
    identified_recipients: List[str] = Field(default_factory=list)
    actions: List[PlannedActionSpec] = Field(default_factory=list)
    requires_clarification: bool = False
    clarification_question: Optional[str] = None
    missing_fields: List[str] = Field(default_factory=list)
    requires_user_approval: bool = True
    suggested_integrations: List[str] = Field(default_factory=list)


TASK_PLANNER_SYSTEM_PROMPT = """You are the core planning brain of Resolve AI, an autonomous AI execution agent.
Your mission is to understand ANY natural language user objective and turn it into an actionable plan.

Resolve AI is NOT just a bulk campaign tool. It can perform:
- SINGLE_ACTION (e.g. "Send an email to Aman")
- MULTI_ACTION (e.g. "Send emails to Aman and Ujjwal")
- BATCH_ACTION (e.g. "Process this CSV and email all founders")
- CUSTOMER_SUPPORT (e.g. "What is your refund policy?", "Check status of order 123")
- WEB_TASK / RESEARCH (e.g. "Search web for latest news about...")
- CODE_TASK / MATH (e.g. "Calculate 15% tip on $85")
- MULTI_FILE REASONING (e.g. "Read my resume and find relevant recruiters in this CSV")

You must respond ONLY with a JSON object matching this schema:
{
  "task_type": "SINGLE_ACTION" | "MULTI_ACTION" | "BATCH_ACTION" | "CUSTOMER_SUPPORT" | "GENERAL",
  "objective": "Clear one-sentence summary of the task",
  "reasoning": "Short explanation of your plan",
  "identified_recipients": ["Aman", "Ujjwal"],
  "actions": [
    {
      "action_type": "SEND_EMAIL" | "READ_FILE" | "RAG_QUERY" | "WEB_SEARCH" | "CODE_EXEC" | "ORDER_QUERY",
      "description": "Short description of this action",
      "recipient_name": "Aman",
      "recipient_email": "aman@example.com (or null if not specified)",
      "subject": "Suggested subject line",
      "body_prompt": "What the message should say"
    }
  ],
  "requires_clarification": false,
  "clarification_question": null,
  "missing_fields": [],
  "requires_user_approval": true,
  "suggested_integrations": ["gmail"]
}

CRITICAL RULES:
- Never say "this is not a bulk outreach request".
- If the user asks to email 1 person, that is a SINGLE_ACTION.
- If the user asks to email 2 or 3 people, that is a MULTI_ACTION with 2 or 3 actions.
- If the user asks a question about policies, refunds, or support, task_type is CUSTOMER_SUPPORT with action RAG_QUERY.
- Do not invent emails if not provided or known.
"""


class TaskPlanner:
    """
    Translates user objectives and contextual data into structured TaskPlanDrafts.
    """

    @classmethod
    def plan(
        cls,
        objective: str,
        context: Optional[Dict[str, Any]] = None,
        attachments_summary: Optional[str] = None,
    ) -> TaskPlanDraft:
        """
        Attempts LLM planning with deterministic fallback.
        """
        prompt = f"{TASK_PLANNER_SYSTEM_PROMPT}\n\nObjective: {objective}\n"
        if context:
            prompt += f"Available Context:\n{json.dumps(context, indent=2)[:3000]}\n"
        if attachments_summary:
            prompt += f"Attached Files Summary:\n{attachments_summary[:3000]}\n"

        try:
            raw = invoke_llm(prompt)
            clean = raw.strip()
            if "```json" in clean:
                clean = clean.split("```json")[1].split("```")[0].strip()
            elif "```" in clean:
                clean = clean.split("```")[1].split("```")[0].strip()

            start = clean.find("{")
            end = clean.rfind("}")
            if start != -1 and end != -1:
                data = json.loads(clean[start : end + 1])
                return TaskPlanDraft.model_validate(data)
        except Exception as e:
            logger.warning("LLM task planner fallback triggered: %s", e)

        # Robust deterministic heuristic planner
        return cls._heuristic_plan(objective, context, attachments_summary)

    @classmethod
    def _heuristic_plan(
        cls,
        objective: str,
        context: Optional[Dict[str, Any]] = None,
        attachments_summary: Optional[str] = None,
    ) -> TaskPlanDraft:
        """
        Deterministic planning ensuring 100% availability even if LLM is offline or rate-limited.
        """
        text = objective.strip()
        lower = text.lower()

        # 1. Customer support / RAG queries
        if any(w in lower for w in ["refund", "policy", "return", "shipping", "hours", "discount", "pricing", "cost"]):
            return TaskPlanDraft(
                task_type=TaskType.CUSTOMER_SUPPORT.value,
                objective=text,
                reasoning="Detected customer support inquiry; retrieving knowledge base context via RAG.",
                actions=[
                    PlannedActionSpec(
                        action_type=ActionType.RAG_QUERY.value,
                        description=f"Retrieve knowledge base context for: '{text}'",
                        parameters={"query": text},
                    )
                ],
                requires_user_approval=False,
            )

        # 2. Web search
        if any(w in lower for w in ["search the web", "search web", "google", "latest news", "weather in"]):
            q = re.sub(r"^(search the web for|search web for|search for|google)\s*", "", text, flags=re.IGNORECASE)
            return TaskPlanDraft(
                task_type=TaskType.GENERAL.value,
                objective=text,
                reasoning="Web search query detected.",
                actions=[
                    PlannedActionSpec(
                        action_type=ActionType.WEB_SEARCH.value,
                        description=f"Search web for '{q}'",
                        parameters={"query": q},
                    )
                ],
                requires_user_approval=False,
            )

        # 3. Math / Python calc
        if any(w in lower for w in ["calculate", "math", "percent", "tip"]) and re.search(r"\d", text):
            expr_match = re.search(r"[\d\.\s\+\-\*\/\(\)\^%]+", text)
            expr = expr_match.group(0).strip() if expr_match else "100 * 0.15"
            return TaskPlanDraft(
                task_type=TaskType.GENERAL.value,
                objective=text,
                reasoning="Mathematical computation detected.",
                actions=[
                    PlannedActionSpec(
                        action_type=ActionType.CODE_EXEC.value,
                        description=f"Calculate expression: '{expr}'",
                        parameters={"expression": expr},
                    )
                ],
                requires_user_approval=False,
            )

        # 4. Email / Outreach communication
        STOP_WORDS = {
            "saying", "asking", "these", "the", "that", "people", "everyone", "all",
            "every", "each", "founder", "founders", "recruiter", "recruiters",
            "investor", "investors", "candidate", "candidates", "lead", "leads",
            "contact", "contacts", "someone", "anyone", "them", "him", "her",
            "personalized", "relevant", "attached", "startup", "information",
        }

        def _is_real_name(token: str) -> bool:
            clean = token.strip().lower()
            if not clean or len(clean) < 2:
                return False
            words = clean.split()
            if any(w in STOP_WORDS for w in words):
                return False
            return True

        # Extract potential names mentioned: "email to Aman and Ujjwal", "send personalized emails to Aman, Ujjwal and Rahul"
        names: List[str] = []
        name_patterns = [
            r"(?:send\s+)?(?:personalized\s+)?(?:an\s+)?emails?\s+to\s+([A-Za-z\s,]+?)(?:\.|$|\s+saying|\s+about|\s+using|\s+with)",
            r"(?:contact|reach\s+out\s+to)\s+([A-Za-z\s,]+?)(?:\.|$|\s+saying|\s+about|\s+using|\s+with)",
            r"email\s+([A-Za-z\s,]+?)(?:\.|$|\s+saying|\s+about|\s+using|\s+with)",
        ]
        for pattern in name_patterns:
            m = re.search(pattern, text, flags=re.IGNORECASE)
            if m:
                raw_names = m.group(1)
                tokens = re.split(r",|\band\b", raw_names, flags=re.IGNORECASE)
                for t in tokens:
                    clean_t = t.strip()
                    if _is_real_name(clean_t):
                        names.append(clean_t.capitalize())
                if names:
                    break

        # Check for explicit emails in prompt
        email_matches = re.findall(r"[\w\.\+\-]+@[\w\.\-]+\.[a-zA-Z]{2,}", text)

        # Check for CSV/spreadsheet batch
        is_csv_batch = (
            "csv" in lower
            or "sheet" in lower
            or "spreadsheet" in lower
            or "founders" in lower
            or "recruiters" in lower
            or (attachments_summary and ("csv" in attachments_summary.lower() or "excel" in attachments_summary.lower()))
        )

        if is_csv_batch and not names:
            return TaskPlanDraft(
                task_type=TaskType.BATCH_ACTION.value,
                objective=text,
                reasoning="Batch communication task detected using uploaded/linked dataset or spreadsheet.",
                actions=[
                    PlannedActionSpec(
                        action_type=ActionType.READ_FILE.value,
                        description="Read and extract contacts from dataset/CSV file",
                    ),
                    PlannedActionSpec(
                        action_type=ActionType.FILTER_CONTACTS.value,
                        description="Filter contacts matching objective audience criteria",
                    ),
                    PlannedActionSpec(
                        action_type=ActionType.GENERATE_MESSAGES.value,
                        description="Generate tailored, personalized outreach messages",
                    ),
                    PlannedActionSpec(
                        action_type=ActionType.SEND_EMAIL.value,
                        description="Send validated emails through verified email provider",
                    ),
                ],
                requires_user_approval=True,
                suggested_integrations=["gmail"],
            )

        if names or email_matches:
            targets = names if names else email_matches
            task_type = TaskType.SINGLE_ACTION.value if len(targets) == 1 else TaskType.MULTI_ACTION.value
            actions = []
            for target in targets:
                is_email = "@" in target
                actions.append(
                    PlannedActionSpec(
                        action_type=ActionType.SEND_EMAIL.value,
                        description=f"Send personalized email to {target}",
                        recipient_name=None if is_email else target,
                        recipient_email=target if is_email else None,
                        subject=f"Update regarding: {text[:40]}",
                        body_prompt=text,
                    )
                )

            return TaskPlanDraft(
                task_type=task_type,
                objective=text,
                reasoning=f"Prepared communication action(s) for {len(targets)} recipient(s).",
                identified_recipients=names,
                actions=actions,
                requires_user_approval=True,
                suggested_integrations=["gmail"],
            )

        # General fallback task
        return TaskPlanDraft(
            task_type=TaskType.GENERAL.value,
            objective=text,
            reasoning="General autonomous execution objective.",
            actions=[
                PlannedActionSpec(
                    action_type=ActionType.WEB_SEARCH.value if "search" in lower else ActionType.RAG_QUERY.value,
                    description=f"Process objective: '{text}'",
                    parameters={"query": text},
                )
            ],
            requires_user_approval=False,
        )
