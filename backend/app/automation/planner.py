import json
import logging
import re
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from app.automation.models import (
    AutomationPlan,
    AutomationTask,
    AutomationStatus
)
from app.database.mongodb import db_manager
from app.llm.client import invoke_llm

logger = logging.getLogger(__name__)

PLANNER_SYSTEM_PROMPT = """You are an Autonomous AI Automation Planning Engine.
Your responsibility is ONLY to PLAN and DECIDE tasks. You must NEVER execute external actions directly.

Given a user goal and automation parameters, decompose the goal into a deterministic sequence of tasks.
Available deterministic tool actions:
- "mock_send_email": Send an email notification/campaign (simulation/dry-run)
- "mock_phone_call": Initiate simulated voice call
- "mock_send_sms": Send simulated text message
- "mock_data_fetch": Query customer, contact, or order records
- "mock_order_action": Query or update order status
- "dry_run_action": Generic simulated deterministic action

Respond ONLY with a valid JSON object with NO markdown formatting:
{
  "goal": "<user goal>",
  "automation_type": "<automation type>",
  "required_integrations": ["<integration1>", "<integration2>"],
  "tasks": [
    {
      "name": "<task name>",
      "action": "<one of available actions>",
      "step_number": 1,
      "parameters": {},
      "dependencies": []
    }
  ],
  "estimated_number_of_actions": <int>,
  "execution_constraints": {
    "rate_limit_per_minute": 60,
    "max_retries": 3,
    "timeout_seconds": 30
  },
  "personalization_instructions": "<instructions>",
  "approval_authorization_scope": {
    "requires_manual_approval": false,
    "dry_run": true,
    "authorized_roles": ["admin", "automation_agent"]
  }
}
"""

class AutomationPlanner:
    """
    Autonomous AI Automation Planner.
    Adheres strictly to the architectural boundary:
    - The LLM should PLAN and DECIDE.
    - Deterministic tools should EXECUTE.
    - Does NOT invoke external tools directly.
    """

    async def create_plan(
        self,
        goal: str,
        automation_type: str = "general",
        input_datasets: Optional[List[Dict[str, Any]]] = None,
        execution_constraints: Optional[Dict[str, Any]] = None,
        personalization_instructions: Optional[str] = None,
        approval_authorization_scope: Optional[Dict[str, Any]] = None,
        persist: bool = True
    ) -> AutomationPlan:
        logger.info(f"AutomationPlanner generating plan for goal: '{goal}', type: '{automation_type}'")
        input_datasets = input_datasets or []
        dataset_size = len(input_datasets)

        prompt = (
            f"{PLANNER_SYSTEM_PROMPT}\n"
            f"User Goal: {goal}\n"
            f"Automation Type: {automation_type}\n"
            f"Input Dataset Size: {dataset_size} items\n"
            f"Personalization Instructions: {personalization_instructions or 'Standard templating'}\n"
        )

        raw_output = invoke_llm(prompt)
        plan_dict = self._parse_llm_output(raw_output, goal, automation_type, dataset_size)

        # Merge supplied constraints/scope overrides
        if execution_constraints:
            plan_dict["execution_constraints"].update(execution_constraints)
        if approval_authorization_scope:
            plan_dict["approval_authorization_scope"].update(approval_authorization_scope)
        if personalization_instructions:
            plan_dict["personalization_instructions"] = personalization_instructions

        # Construct tasks
        tasks: List[AutomationTask] = []
        for i, t_data in enumerate(plan_dict.get("tasks", [])):
            task = AutomationTask(
                name=t_data.get("name", f"Task {i+1}"),
                action=t_data.get("action", "dry_run_action"),
                step_number=t_data.get("step_number", i + 1),
                parameters=t_data.get("parameters", {}),
                dependencies=t_data.get("dependencies", []),
                status=AutomationStatus.READY
            )
            tasks.append(task)

        # Calculate estimated number of actions
        estimated_actions = plan_dict.get("estimated_number_of_actions", 0)
        if estimated_actions <= 0:
            task_count = len(tasks) if tasks else 1
            estimated_actions = max(1, dataset_size) * task_count

        plan = AutomationPlan(
            goal=goal,
            automation_type=automation_type,
            tasks=tasks,
            required_integrations=plan_dict.get("required_integrations", ["email"]),
            input_datasets=input_datasets,
            estimated_number_of_actions=estimated_actions,
            execution_constraints=plan_dict.get("execution_constraints", {
                "rate_limit_per_minute": 60,
                "max_retries": 3,
                "timeout_seconds": 30
            }),
            personalization_instructions=personalization_instructions,
            approval_authorization_scope=plan_dict.get("approval_authorization_scope", {
                "requires_manual_approval": False,
                "dry_run": True,
                "authorized_roles": ["admin", "automation_agent"]
            }),
            status=AutomationStatus.READY,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc)
        )

        for task in plan.tasks:
            task.plan_id = plan.id

        if persist:
            await db_manager.save_plan(plan.model_dump())
            logger.info(f"Persisted AutomationPlan '{plan.id}' with {len(plan.tasks)} tasks.")

        return plan

    def _parse_llm_output(
        self,
        output: str,
        goal: str,
        automation_type: str,
        dataset_size: int
    ) -> Dict[str, Any]:
        """Parses LLM output JSON or builds deterministic structured fallback."""
        try:
            cleaned = output.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned.split("```json")[1].split("```")[0].strip()
            elif cleaned.startswith("```"):
                cleaned = cleaned.split("```")[1].split("```")[0].strip()

            parsed = json.loads(cleaned)
            if isinstance(parsed, dict) and "tasks" in parsed:
                return parsed
        except Exception as e:
            logger.debug(f"Could not parse LLM output as JSON ({e}). Falling back to heuristic planning.")

        # Heuristic deterministic decomposition
        goal_lower = goal.lower()
        tasks = []
        required_integrations = []

        if any(w in goal_lower for w in ["email", "outreach", "newsletter", "campaign", "welcome"]):
            required_integrations.append("email")
            tasks.append({
                "name": "Prepare recipient records and personalized content",
                "action": "mock_data_fetch",
                "step_number": 1,
                "parameters": {"operation": "enrich_recipients"},
                "dependencies": []
            })
            tasks.append({
                "name": "Deliver email communication to recipient",
                "action": "mock_send_email",
                "step_number": 2,
                "parameters": {"dry_run": True},
                "dependencies": ["mock_data_fetch"]
            })
        elif any(w in goal_lower for w in ["call", "phone", "voice"]):
            required_integrations.append("telephony")
            tasks.append({
                "name": "Initiate outbound phone call",
                "action": "mock_phone_call",
                "step_number": 1,
                "parameters": {"dry_run": True},
                "dependencies": []
            })
        elif any(w in goal_lower for w in ["order", "shipping", "tracking"]):
            required_integrations.append("order_management")
            tasks.append({
                "name": "Query and verify order status",
                "action": "mock_order_action",
                "step_number": 1,
                "parameters": {"operation": "check_status"},
                "dependencies": []
            })
        else:
            required_integrations.append("general_tasks")
            tasks.append({
                "name": "Execute automated task action",
                "action": "dry_run_action",
                "step_number": 1,
                "parameters": {"goal": goal},
                "dependencies": []
            })

        return {
            "goal": goal,
            "automation_type": automation_type,
            "required_integrations": required_integrations,
            "tasks": tasks,
            "estimated_number_of_actions": max(1, dataset_size) * len(tasks),
            "execution_constraints": {
                "rate_limit_per_minute": 60,
                "max_retries": 3,
                "timeout_seconds": 30
            },
            "approval_authorization_scope": {
                "requires_manual_approval": False,
                "dry_run": True,
                "authorized_roles": ["admin", "automation_agent"]
            }
        }
