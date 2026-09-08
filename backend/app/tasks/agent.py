"""
Autonomous Task Agent for Resolve AI.

Orchestrates the general-purpose task lifecycle:
1. UNDERSTAND_TASK
2. LOAD_CONTEXT & INSPECT_ATTACHMENTS
3. DETERMINE_REQUIREMENTS & CLARIFICATION (WAITING_FOR_USER)
4. PLAN_TASK
5. AUTHORIZATION CHECK (WAITING_FOR_AUTHORIZATION)
6. EXECUTE (via ToolRegistry)
7. VERIFY & COMPLETE

Supports seamless multi-turn continuation on the same task_id.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from app.database.mongodb import db_manager
from app.tasks.attachments import AttachmentProcessor
from app.tasks.models import (
    ActionStatus,
    ActionType,
    ExecutionPlan,
    ExecutionPlanStep,
    ExecutionTask,
    TaskAction,
    TaskAttachment,
    TaskEvent,
    TaskEventType,
    TaskJob,
    TaskStatus,
    TaskType,
)
from app.tasks.planner import TaskPlanner
from app.tasks.tools import ToolRegistry

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def coerce_task_type(raw: Any) -> TaskType:
    clean = str(raw).upper().strip().replace(" ", "_").replace("-", "_")
    try:
        return TaskType(clean)
    except ValueError:
        if "FILE" in clean:
            return TaskType.MULTI_FILE_REASONING
        if "BATCH" in clean:
            return TaskType.BATCH_ACTION
        if "MULTI" in clean:
            return TaskType.MULTI_ACTION
        if "SUPPORT" in clean or "RAG" in clean:
            return TaskType.CUSTOMER_SUPPORT
        return TaskType.GENERAL


class TaskAgent:
    """
    Stateful execution engine for general-purpose autonomous tasks.
    """

    @classmethod
    async def create_and_run_task(
        cls,
        objective: str,
        user_id: str = "default_user",
        attachments: Optional[List[Dict[str, Any]]] = None,
        authorization_scope: Optional[Dict[str, Any]] = None,
        dry_run: bool = False,
    ) -> ExecutionTask:
        """
        Creates a new ExecutionTask and initiates the agent lifecycle.
        """
        task_id = f"task_{uuid.uuid4().hex[:10]}"
        parsed_attachments: List[TaskAttachment] = []
        if attachments:
            for att in attachments:
                content = att.get("content") or b""
                if isinstance(content, str):
                    content = content.encode("utf-8")
                parsed = AttachmentProcessor.process_file(
                    content=content,
                    filename=att.get("filename", "uploaded_file"),
                    metadata=att.get("metadata", {}),
                )
                parsed_attachments.append(parsed)

        task = ExecutionTask(
            task_id=task_id,
            user_id=user_id,
            objective=objective,
            status=TaskStatus.UNDERSTANDING,
            attachments=parsed_attachments,
            authorization_scope=authorization_scope or {},
            dry_run=dry_run,
            conversation_history=[
                {"role": "user", "content": objective, "timestamp": utc_now().isoformat()}
            ],
        )

        await db_manager.save_task(task.model_dump(mode="json"))
        await cls._emit_event(task_id, TaskEventType.TASK_CREATED, f"Task created: '{objective[:80]}'")

        return await cls.step_task(task)

    @classmethod
    async def resume_with_message(cls, task_id: str, message: str) -> Optional[ExecutionTask]:
        """
        Resumes a paused task (e.g. WAITING_FOR_USER) using the user's response.
        Ensures continuation on the SAME task_id.
        """
        task_dict = await db_manager.get_task(task_id)
        if not task_dict:
            logger.warning("Task '%s' not found for resume", task_id)
            return None

        task = ExecutionTask.model_validate(task_dict)
        task.conversation_history.append(
            {"role": "user", "content": message, "timestamp": utc_now().isoformat()}
        )
        await cls._emit_event(
            task_id, TaskEventType.USER_RESPONSE_RECEIVED, f"User provided response: '{message}'"
        )

        # Resolve clarification using user's answer
        cls._resolve_clarification_from_message(task, message)
        task.status = TaskStatus.PLANNING
        await db_manager.save_task(task.model_dump(mode="json"))

        return await cls.step_task(task)

    @classmethod
    async def approve_and_execute(cls, task_id: str) -> Optional[ExecutionTask]:
        """
        Explicit user authorization to proceed with pending actions.
        """
        task_dict = await db_manager.get_task(task_id)
        if not task_dict:
            return None

        task = ExecutionTask.model_validate(task_dict)
        task.authorization_scope["allow_send"] = True
        task.status = TaskStatus.RUNNING
        await cls._emit_event(task_id, TaskEventType.AUTHORIZATION_GRANTED, "User approved execution of prepared actions.")
        await db_manager.save_task(task.model_dump(mode="json"))

        return await cls.step_task(task)

    @classmethod
    async def step_task(cls, task: ExecutionTask) -> ExecutionTask:
        """
        Advances the task state machine until a terminal state or waiting condition is reached.
        """
        try:
            # 1. UNDERSTANDING & CONTEXT INSPECTION
            if task.status in (TaskStatus.UNDERSTANDING, TaskStatus.PLANNING):
                await cls._inspect_context_and_attachments(task)
                has_clarification = await cls._check_and_request_clarification(task)
                if has_clarification:
                    task.status = TaskStatus.WAITING_FOR_USER
                    await db_manager.save_task(task.model_dump(mode="json"))
                    return task

                # 2. CREATE EXECUTION PLAN
                await cls._build_execution_plan(task)
                await cls._emit_event(task.task_id, TaskEventType.PLAN_CREATED, f"Generated plan with {len(task.actions)} action(s).")

                # 3. CHECK AUTHORIZATION
                needs_auth = cls._requires_authorization(task)
                if needs_auth:
                    task.status = TaskStatus.WAITING_FOR_AUTHORIZATION
                    await cls._emit_event(
                        task.task_id,
                        TaskEventType.AUTHORIZATION_REQUESTED,
                        f"Prepared {len(task.actions)} action(s). Awaiting authorization.",
                    )
                    await db_manager.save_task(task.model_dump(mode="json"))
                    return task

                task.status = TaskStatus.RUNNING

            # 4. EXECUTION
            if task.status == TaskStatus.RUNNING:
                await cls._execute_task_actions(task)

                # 5. VERIFICATION & COMPLETION
                task.status = TaskStatus.VERIFYING
                failed_actions = [a for a in task.actions if a.status == ActionStatus.FAILED]
                if failed_actions:
                    task.errors.extend([f"Action failed: {a.error}" for a in failed_actions if a.error])
                    if len(failed_actions) == len(task.actions):
                        task.status = TaskStatus.FAILED
                    else:
                        task.status = TaskStatus.COMPLETED
                else:
                    task.status = TaskStatus.COMPLETED

                await cls._emit_event(
                    task.task_id,
                    TaskEventType.TASK_COMPLETED if task.status == TaskStatus.COMPLETED else TaskEventType.TASK_FAILED,
                    f"Task {task.status.value}. {len(task.results)} result(s) recorded.",
                )

            task.updated_at = utc_now()
            await db_manager.save_task(task.model_dump(mode="json"))
            return task

        except Exception as e:
            logger.exception("Error stepping task '%s': %s", task.task_id, e)
            task.status = TaskStatus.FAILED
            task.errors.append(f"Unexpected error: {str(e)}")
            task.updated_at = utc_now()
            await db_manager.save_task(task.model_dump(mode="json"))
            return task

    # =====================================================================
    # Lifecycle Step Implementations
    # =====================================================================

    @classmethod
    async def _inspect_context_and_attachments(cls, task: ExecutionTask) -> None:
        """
        Inspects available attachments, checks contact database, and gathers available tool context.
        """
        # Formulate attachment summary
        att_summaries = []
        for att in task.attachments:
            att_summaries.append(
                f"- File: {att.filename} ({att.file_type}, {att.size_bytes} bytes)\n"
                f"  Preview:\n  {att.extracted_text[:400] if att.extracted_text else '[empty]'}"
            )
        if att_summaries:
            task.context["attachments_summary"] = "\n".join(att_summaries)
            await cls._emit_event(task.task_id, TaskEventType.FILES_INSPECTED, f"Inspected {len(task.attachments)} attachment(s).")

    @classmethod
    async def _check_and_request_clarification(cls, task: ExecutionTask) -> bool:
        """
        Checks if required information is missing or ambiguous.
        If so, sets clarification question and returns True.
        """
        # If already clarified in context, don't ask again
        if task.context.get("clarified_recipient"):
            return False

        # Preliminary plan to identify potential recipients
        plan_draft = TaskPlanner.plan(
            objective=task.objective,
            context=task.context,
            attachments_summary=task.context.get("attachments_summary"),
        )
        task.task_type = coerce_task_type(plan_draft.task_type)

        # For Customer support or General, no recipient clarification is needed
        if task.task_type in (TaskType.CUSTOMER_SUPPORT, TaskType.GENERAL):
            return False

        # For batch action from CSV or multi-file reasoning, recipients come from the file
        if task.task_type in (TaskType.BATCH_ACTION, TaskType.MULTI_FILE_REASONING):
            return False

        # If specific recipients identified, inspect contact database
        for name in plan_draft.identified_recipients:
            search_res = await ToolRegistry.execute("search_contacts", query=name, limit=10)
            contacts = search_res.get("contacts", [])

            if len(contacts) > 1:
                # Ambiguity detected: multiple contacts found!
                task.required_information.append(f"recipient_identity_{name}")
                options = [
                    {
                        "label": f"{c['name']} ({c['email']} - {c.get('company', 'No Company')})",
                        "value": c["email"],
                        "name": c["name"],
                    }
                    for c in contacts
                ]
                task.clarification_options = options
                formatted_opts = "\n".join([f"{i+1}. {opt['label']}" for i, opt in enumerate(options)])
                task.clarification_questions = [
                    f"I found {len(contacts)} contacts named '{name}'. Which one would you like me to email?\n{formatted_opts}"
                ]
                await cls._emit_event(
                    task.task_id,
                    TaskEventType.CLARIFICATION_REQUESTED,
                    task.clarification_questions[0],
                )
                return True

            elif len(contacts) == 1:
                # Exactly one matching contact: store in context
                c = contacts[0]
                task.context[f"contact_{name}"] = c

            else:
                # 0 contacts found and no email in objective
                email_match = re.search(r"[\w\.\+\-]+@[\w\.\-]+\.[a-zA-Z]{2,}", task.objective)
                if not email_match:
                    task.required_information.append(f"email_for_{name}")
                    task.clarification_questions = [
                        f"I couldn't find a contact named '{name}' in your database. What email address should I use for {name}?"
                    ]
                    await cls._emit_event(
                        task.task_id,
                        TaskEventType.CLARIFICATION_REQUESTED,
                        task.clarification_questions[0],
                    )
                    return True

        return False

    @classmethod
    def _resolve_clarification_from_message(cls, task: ExecutionTask, message: str) -> None:
        """
        Parses the user's reply to resolve the ambiguity.
        """
        clean_msg = message.strip().lower()

        # Check if user picked an index like "1" or "2"
        if task.clarification_options:
            idx_match = re.search(r"\b(\d+)\b", clean_msg)
            if idx_match:
                idx = int(idx_match.group(1)) - 1
                if 0 <= idx < len(task.clarification_options):
                    chosen = task.clarification_options[idx]
                    task.context["clarified_recipient"] = chosen
                    task.clarification_questions = []
                    task.clarification_options = None
                    return

            # Check if user mentioned company or name substring
            for opt in task.clarification_options:
                if (
                    clean_msg in opt["label"].lower()
                    or opt["value"].lower() in clean_msg
                    or (opt.get("name") and opt["name"].lower() in clean_msg)
                ):
                    task.context["clarified_recipient"] = opt
                    task.clarification_questions = []
                    task.clarification_options = None
                    return

        # Check for email format in message
        email_match = re.search(r"[\w\.\+\-]+@[\w\.\-]+\.[a-zA-Z]{2,}", message)
        if email_match:
            task.context["clarified_email"] = email_match.group(0)
            task.clarification_questions = []
            task.clarification_options = None

    @classmethod
    async def _build_execution_plan(cls, task: ExecutionTask) -> None:
        """
        Builds the concrete execution plan and concrete actions.
        """
        plan_draft = TaskPlanner.plan(
            objective=task.objective,
            context=task.context,
            attachments_summary=task.context.get("attachments_summary"),
        )
        task.task_type = coerce_task_type(plan_draft.task_type)

        steps: List[ExecutionPlanStep] = []
        actions: List[TaskAction] = []

        # 1. Customer Support
        if task.task_type == TaskType.CUSTOMER_SUPPORT:
            action_id = f"act_{uuid.uuid4().hex[:8]}"
            steps.append(
                ExecutionPlanStep(
                    step_number=1,
                    action_type="RAG_QUERY",
                    description=f"Query knowledge base for: '{task.objective}'",
                    requires_authorization=False,
                )
            )
            actions.append(
                TaskAction(
                    action_id=action_id,
                    action_type=ActionType.RAG_QUERY,
                    description=f"Query knowledge base for: '{task.objective}'",
                    parameters={"query": task.objective},
                    requires_authorization=False,
                )
            )

        # 2. General / Web Search / Code Task
        elif task.task_type == TaskType.GENERAL:
            for idx, act in enumerate(plan_draft.actions):
                action_id = f"act_{uuid.uuid4().hex[:8]}"
                a_type = ActionType(act.action_type)
                steps.append(
                    ExecutionPlanStep(
                        step_number=idx + 1,
                        action_type=act.action_type,
                        description=act.description,
                        requires_authorization=False,
                    )
                )
                actions.append(
                    TaskAction(
                        action_id=action_id,
                        action_type=a_type,
                        description=act.description,
                        parameters=act.parameters,
                        requires_authorization=False,
                    )
                )

        # 3. Batch Action from CSV/Attachment or Multi-File Reasoning
        elif task.task_type in (TaskType.BATCH_ACTION, TaskType.MULTI_FILE_REASONING):
            contacts_data = []
            # Check attachments for CSV/XLSX
            for att in task.attachments:
                if att.parsed_data and isinstance(att.parsed_data, list):
                    contacts_data.extend(att.parsed_data)

            # If no attachment contacts, check if user specified dataset in context or DB
            if not contacts_data:
                db_contacts = await db_manager.list_contacts(filters={"is_valid": True}, limit=50)
                contacts_data = db_contacts

            def _get_field(d: Dict[str, Any], *keys: str) -> Optional[str]:
                d_lower = {str(k).lower().strip(): v for k, v in d.items() if v is not None}
                for k in keys:
                    val = d_lower.get(k.lower().strip())
                    if val is not None and str(val).strip():
                        return str(val).strip()
                return None

            # Filter founders if objective asks for founders
            is_founder_filter = "founder" in task.objective.lower()
            filtered_recipients = []
            for c in contacts_data:
                role = (_get_field(c, "role", "role_title", "title", "contact_type", "position") or "").lower()
                c_type = (_get_field(c, "contact_type") or "").upper()
                email = _get_field(c, "email", "work_email", "e-mail", "email_address")
                if not email or "@" not in email:
                    continue
                if is_founder_filter:
                    if "founder" in role or "co-founder" in role or c_type == "FOUNDER":
                        filtered_recipients.append(c)
                else:
                    filtered_recipients.append(c)

            # Limit batch size for safety (e.g. 50)
            target_batch = filtered_recipients[:50]
            steps.append(
                ExecutionPlanStep(
                    step_number=1,
                    action_type="FILTER_CONTACTS",
                    description=f"Filtered {len(target_batch)} contact(s) from dataset.",
                    completed=True,
                )
            )
            steps.append(
                ExecutionPlanStep(
                    step_number=2,
                    action_type="SEND_EMAIL",
                    description=f"Send personalized emails to {len(target_batch)} recipient(s).",
                    requires_authorization=True,
                )
            )

            # Build resume context if resume attached
            resume_context = ""
            for att in task.attachments:
                if "resume" in att.filename.lower() or att.file_type == "pdf":
                    resume_context = att.extracted_text[:1500] if att.extracted_text else ""

            for c in target_batch:
                action_id = f"act_{uuid.uuid4().hex[:8]}"
                first = _get_field(c, "first_name", "first") or ""
                last = _get_field(c, "last_name", "last") or ""
                full = f"{first} {last}".strip()
                name = full or _get_field(c, "name", "full_name", "contact_name") or "there"
                email = _get_field(c, "email", "work_email", "e-mail", "email_address")
                comp = _get_field(c, "company", "organization", "firm") or "your company"

                body = (
                    f"Hi {name},\n\n"
                    f"I came across your work at {comp} and was deeply impressed by what your team is building. "
                )
                if resume_context:
                    body += f"Based on my background in engineering and systems, I would love to connect to discuss potential opportunities.\n\nRelevant background:\n{resume_context[:250]}...\n\n"
                else:
                    body += "I would appreciate the opportunity to connect for a quick introductory conversation.\n\n"
                body += "Best regards,\nResolve AI User"

                actions.append(
                    TaskAction(
                        action_id=action_id,
                        action_type=ActionType.SEND_EMAIL,
                        description=f"Send email to {name} <{email}>",
                        parameters={"to_email": email, "subject": f"Connecting regarding {comp}", "body": body},
                        requires_authorization=True,
                    )
                )

        # 4. Single / Multi Communication Actions
        else:
            recipients = []
            # Check if clarified recipient exists
            if task.context.get("clarified_recipient"):
                recipients.append(task.context["clarified_recipient"])
            elif task.context.get("clarified_email"):
                recipients.append({"name": "Recipient", "email": task.context["clarified_email"]})
            else:
                for name in plan_draft.identified_recipients:
                    c = task.context.get(f"contact_{name}")
                    if c:
                        recipients.append(c)
                    else:
                        # Email directly in prompt
                        m = re.search(rf"{name}.*?([\w\.\+\-]+@[\w\.\-]+\.[a-zA-Z]{{2,}})", task.objective, re.I)
                        if m:
                            recipients.append({"name": name, "email": m.group(1)})

            # If no recipients resolved yet, check prompt for any raw emails
            if not recipients:
                raw_emails = re.findall(r"[\w\.\+\-]+@[\w\.\-]+\.[a-zA-Z]{2,}", task.objective)
                for em in raw_emails:
                    recipients.append({"name": "Recipient", "email": em})

            # Check resume context
            resume_context = ""
            for att in task.attachments:
                if "resume" in att.filename.lower():
                    resume_context = att.extracted_text[:1000] if att.extracted_text else ""

            for idx, r in enumerate(recipients):
                action_id = f"act_{uuid.uuid4().hex[:8]}"
                name = r.get("name") or r.get("first_name") or "there"
                email = r.get("email") or r.get("value")
                company = r.get("company") or ""

                subject = f"Connecting with {name}"
                body = f"Hello {name},\n\nI hope this message finds you well. "
                if resume_context:
                    body += f"I reviewed your role at {company} and would love to connect based on my experience:\n\n{resume_context[:200]}...\n\n"
                else:
                    body += f"I am reaching out regarding: {task.objective}.\n\n"
                body += "Best regards,\nResolve AI User"

                steps.append(
                    ExecutionPlanStep(
                        step_number=idx + 1,
                        action_type="SEND_EMAIL",
                        description=f"Send email to {name} <{email}>",
                        requires_authorization=True,
                    )
                )
                actions.append(
                    TaskAction(
                        action_id=action_id,
                        action_type=ActionType.SEND_EMAIL,
                        description=f"Send email to {name} <{email}>",
                        parameters={"to_email": email, "subject": subject, "body": body},
                        requires_authorization=True,
                    )
                )

        task.execution_plan = ExecutionPlan(
            summary=f"Plan to execute '{task.objective}' ({len(actions)} action(s)).",
            steps=steps,
            required_integrations=["gmail"] if any(a.action_type == ActionType.SEND_EMAIL for a in actions) else [],
            estimated_actions=len(actions),
            requires_user_approval=any(a.requires_authorization for a in actions),
        )
        task.actions = actions

    @classmethod
    def _requires_authorization(cls, task: ExecutionTask) -> bool:
        """
        Determines if user authorization is required before executing actions.
        """
        if task.dry_run:
            return False  # Dry-run is safe to execute without send authorization

        if task.authorization_scope.get("allow_send") is True:
            return False  # User already granted authorization

        # If any action has side-effects (e.g. SEND_EMAIL)
        return any(a.requires_authorization for a in task.actions)

    @classmethod
    async def _execute_task_actions(cls, task: ExecutionTask) -> None:
        """
        Executes each TaskAction through ToolRegistry and records persistent TaskJobs.
        """
        for action in task.actions:
            if action.status == ActionStatus.SUCCEEDED:
                continue

            action.status = ActionStatus.RUNNING
            idempotency_key = f"{task.task_id}_{action.action_id}"
            action.idempotency_key = idempotency_key

            # Record TaskJob
            job = TaskJob(
                task_id=task.task_id,
                action_id=action.action_id,
                action_type=action.action_type,
                status=ActionStatus.RUNNING,
                parameters=action.parameters,
                idempotency_key=idempotency_key,
                created_at=utc_now(),
            )
            await db_manager.save_task_job(job.model_dump(mode="json"))
            await cls._emit_event(task.task_id, TaskEventType.ACTION_STARTED, f"Executing {action.action_type.value}: {action.description}")

            # Pass dry_run flag to parameters if task is in dry_run mode
            exec_params = dict(action.parameters)
            if task.dry_run:
                exec_params["dry_run"] = True

            # Deterministic tool execution
            tool_name = action.action_type.value.lower()
            res = await ToolRegistry.execute(tool_name, **exec_params)

            is_success = res.get("success", False)
            if is_success:
                action.status = ActionStatus.SUCCEEDED
                action.result = res
                action.completed_at = utc_now()
                job.status = ActionStatus.SUCCEEDED
                job.result = res
                job.completed_at = utc_now()
                task.results.append({"action_id": action.action_id, "result": res})
                await cls._emit_event(
                    task.task_id,
                    TaskEventType.ACTION_SUCCEEDED,
                    f"Action succeeded: {action.description}",
                    data=res,
                )
            else:
                action.status = ActionStatus.FAILED
                err = res.get("error", "Unknown execution error")
                action.error = err
                action.completed_at = utc_now()
                job.status = ActionStatus.FAILED
                job.error = err
                job.completed_at = utc_now()
                task.errors.append(f"{action.description}: {err}")
                await cls._emit_event(
                    task.task_id,
                    TaskEventType.ACTION_FAILED,
                    f"Action failed: {action.description} ({err})",
                )

            await db_manager.save_task_job(job.model_dump(mode="json"))

    @classmethod
    async def _emit_event(
        cls, task_id: str, event_type: TaskEventType, message: str, data: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Creates and persists a TaskEvent.
        """
        evt = TaskEvent(
            task_id=task_id,
            event_type=event_type,
            message=message,
            data=data or {},
            timestamp=utc_now(),
        )
        await db_manager.save_task_event(evt.model_dump(mode="json"))
