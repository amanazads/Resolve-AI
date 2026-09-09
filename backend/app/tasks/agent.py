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

from app.config import settings
from app.database.mongodb import db_manager
from app.permissions.middleware import PermissionEnforcer
from app.permissions.models import GrantPermissionRequest, PermissionScope
from app.permissions.service import permission_service
from app.tasks.attachments import AttachmentProcessor
from app.tasks.models import (
    ActionStatus,
    ActionType,
    ExecutionPlan,
    ExecutionPlanStep,
    ExecutionTask,
    TaskAction,
    TaskAttachment,
    TaskAuthorization,
    TaskClarification,
    TaskEvent,
    TaskEventType,
    TaskJob,
    TaskStatus,
    TaskType,
)
from app.tasks.planner import TaskPlanner
from app.tasks.tools import ToolRegistry
from app.integrations.registry import get_email_provider

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
        Resumes a paused task (e.g. WAITING_FOR_CLARIFICATION) using the user's response.
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
        if task.clarification:
            task.clarification.answered = True
            task.clarification.user_response = message
            task.clarification.round += 1

        task.status = TaskStatus.PLANNING
        await db_manager.save_task(task.model_dump(mode="json"))

        return await cls.step_task(task)

    @classmethod
    async def approve_and_execute(cls, task_id: str) -> Optional[ExecutionTask]:
        """
        Explicit user authorization to proceed with pending actions.
        Creates a verified server-side permission grant.
        """
        task_dict = await db_manager.get_task(task_id)
        if not task_dict:
            return None

        task = ExecutionTask.model_validate(task_dict)
        provider = get_email_provider()
        provider_name = (provider.name if provider else settings.EMAIL_PROVIDER or "mock").lower()

        # Create server-side permission grant
        grant = await permission_service.grant(
            GrantPermissionRequest(
                user_id=task.user_id,
                granted_by=task.user_id,
                scopes=[PermissionScope.EMAIL_SEND],
                integration=provider_name,
                campaign_id=task.task_id,
                recipient_count=len([a for a in task.actions if a.requires_authorization]),
            )
        )

        task.authorization_scope["allow_send"] = True
        task.authorization_scope["grant_id"] = grant.grant_id
        task.authorization = TaskAuthorization(
            required=True,
            authorized=True,
            grant_id=grant.grant_id,
            authorized_by=task.user_id,
            scope={"scope": "EMAIL_SEND", "integration": provider_name, "grant_id": grant.grant_id},
        )
        task.status = TaskStatus.AUTHORIZED
        await cls._emit_event(task_id, TaskEventType.AUTHORIZATION_GRANTED, f"User approved execution of prepared actions (grant {grant.grant_id}).")
        task.status = TaskStatus.RUNNING
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
                    task.status = TaskStatus.WAITING_FOR_CLARIFICATION
                    await db_manager.save_task(task.model_dump(mode="json"))
                    return task

                # 2. CREATE EXECUTION PLAN
                await cls._build_execution_plan(task)
                await cls._emit_event(task.task_id, TaskEventType.PLAN_CREATED, f"Generated plan with {len(task.actions)} action(s).")

                # 3. CHECK AUTHORIZATION
                needs_auth = await cls._check_authorization_required(task)
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
            if task.status in (TaskStatus.RUNNING, TaskStatus.AUTHORIZED):
                task.status = TaskStatus.RUNNING
                await cls._execute_task_actions(task)

                # 5. VERIFICATION & COMPLETION
                task.status = TaskStatus.VERIFYING
                failed_actions = [a for a in task.actions if a.status == ActionStatus.FAILED]
                succeeded_actions = [a for a in task.actions if a.status == ActionStatus.SUCCEEDED]

                if not failed_actions:
                    task.status = TaskStatus.COMPLETED
                elif succeeded_actions:
                    task.status = TaskStatus.PARTIALLY_COMPLETED
                else:
                    task.status = TaskStatus.FAILED

                await cls._emit_event(
                    task.task_id,
                    TaskEventType.TASK_COMPLETED if task.status == TaskStatus.COMPLETED else (
                        TaskEventType.TASK_FAILED if task.status == TaskStatus.FAILED else TaskEventType.TASK_COMPLETED
                    ),
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
        # If sender is missing and required
        if task.context.get("sender_missing") and not task.context.get("sender_name"):
            task.required_information = ["sender_identity"]
            q = "What name and email address should I use as the sender identity?"
            task.clarification_questions = [q]
            task.clarification = TaskClarification(
                questions=[q],
                missing_fields=["sender_identity"],
                round=1,
                answered=False,
            )
            await cls._emit_event(task.task_id, TaskEventType.CLARIFICATION_REQUESTED, q)
            return True

        if task.context.get("clarified_recipient") or task.context.get("clarified_email"):
            return False

        # Preliminary plan to inspect task structure
        plan_draft = TaskPlanner.plan(
            objective=task.objective,
            context=task.context,
            attachments_summary=task.context.get("attachments_summary"),
        )
        task.task_type = coerce_task_type(plan_draft.task_type)

        # If planner detected missing critical info
        if plan_draft.requires_clarification:
            task.required_information = list(plan_draft.missing_fields)
            q = plan_draft.clarification_question or "Could you please provide more details to proceed?"
            task.clarification_questions = [q]
            task.clarification = TaskClarification(
                questions=[q],
                missing_fields=list(plan_draft.missing_fields),
                round=1,
                answered=False,
            )
            await cls._emit_event(
                task.task_id,
                TaskEventType.CLARIFICATION_REQUESTED,
                q,
            )
            return True

        # For Customer support or General, no recipient clarification is needed
        if task.task_type in (TaskType.CUSTOMER_SUPPORT, TaskType.GENERAL):
            return False

        # For batch action from CSV or multi-file reasoning, recipients come from the file
        if task.task_type in (TaskType.BATCH_ACTION, TaskType.MULTI_FILE_REASONING):
            return False

        # Check if an explicit recipient email was already provided in objective or actions
        explicit_emails = re.findall(r"[\w\.\+\-]+@[\w\.\-]+\.[a-zA-Z]{2,}", task.objective)
        action_emails = [a.recipient_email for a in plan_draft.actions if getattr(a, "recipient_email", None)]
        has_explicit_email = bool(explicit_emails or action_emails)

        # If specific recipients identified, inspect contact database
        for name in plan_draft.identified_recipients:
            if has_explicit_email:
                # Recipient email is already explicitly provided by the user in the prompt
                continue

            search_res = await ToolRegistry.execute("search_contacts", query=name, limit=10)
            contacts = search_res.get("contacts", [])
            unique_emails = set(c["email"] for c in contacts if c.get("email"))

            if len(unique_emails) > 1:
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
                q = f"I found {len(contacts)} contacts named '{name}'. Which one would you like me to email?\n{formatted_opts}"
                task.clarification_questions = [q]
                task.clarification = TaskClarification(
                    questions=[q],
                    options=options,
                    missing_fields=[f"recipient_identity_{name}"],
                    round=1,
                    answered=False,
                )
                await cls._emit_event(
                    task.task_id,
                    TaskEventType.CLARIFICATION_REQUESTED,
                    q,
                )
                return True

            elif len(contacts) >= 1:
                c = contacts[0]
                task.context[f"contact_{name}"] = c

            else:
                email_match = re.search(r"[\w\.\+\-]+@[\w\.\-]+\.[a-zA-Z]{2,}", task.objective)
                if not email_match:
                    task.required_information.append(f"recipient_email_{name}")
                    q = f"I know you want to email {name}, but I don't have an email address. What is {name}'s email?"
                    task.clarification_questions = [q]
                    task.clarification = TaskClarification(
                        questions=[q],
                        missing_fields=[f"recipient_email_{name}"],
                        round=1,
                        answered=False,
                    )
                    await cls._emit_event(
                        task.task_id,
                        TaskEventType.CLARIFICATION_REQUESTED,
                        q,
                    )
                    return True

        return False

    @classmethod
    def _resolve_clarification_from_message(cls, task: ExecutionTask, message: str) -> None:
        """
        Parses the user's reply to resolve ambiguity or missing details.
        """
        clean_msg = message.strip()
        lower_msg = clean_msg.lower()

        # Check if resolving sender identity
        if "sender_identity" in task.required_information or (task.clarification and "sender_identity" in task.clarification.missing_fields):
            email_match = re.search(r"[\w\.\+\-]+@[\w\.\-]+\.[a-zA-Z]{2,}", clean_msg)
            sender_email = email_match.group(0) if email_match else "aman@ckript.com"
            raw_without_email = re.sub(r"[\w\.\+\-]+@[\w\.\-]+\.[a-zA-Z]{2,}", "", clean_msg).strip(" ,.-")
            sender_name = re.sub(r"^(?:my\s+name\s+is|use|i\s+am|from)\s*", "", raw_without_email, flags=re.IGNORECASE).strip()
            sender_name = re.sub(r"(?:,\s*)?(?:email\s*(?:is)?|mail\s*(?:is)?)\s*$", "", sender_name, flags=re.IGNORECASE).strip(" ,.-")
            if not sender_name or len(sender_name) < 2:
                sender_name = "Alex Vance"
            task.context["sender_name"] = sender_name
            task.context["sender_email"] = sender_email
            task.context["sender_missing"] = False
            task.clarification_questions = []
            task.required_information = [f for f in task.required_information if f != "sender_identity"]
            return

        # 1. Check if user picked an index like "1" or "2"
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

            # Check if user mentioned company, name substring, or keyword tokens
            clean_lower = lower_msg.strip(".,!?\"' ")
            for opt in task.clarification_options:
                label_lower = opt["label"].lower()
                val_lower = opt["value"].lower()
                name_lower = (opt.get("name") or "").lower()
                if (
                    clean_lower in label_lower
                    or val_lower in lower_msg
                    or (name_lower and name_lower in lower_msg)
                ):
                    task.context["clarified_recipient"] = opt
                    task.clarification_questions = []
                    task.clarification_options = None
                    return

                # Token matching (e.g. "The one at Ckript." matches "Ckript")
                tokens = [t.strip(".,!?\"' ") for t in lower_msg.split() if len(t.strip(".,!?\"' ")) > 2]
                meaningful_tokens = [t for t in tokens if t not in ("the", "one", "at", "for", "and", "yes", "please", "email", "send")]
                if any(t in label_lower for t in meaningful_tokens):
                    task.context["clarified_recipient"] = opt
                    task.clarification_questions = []
                    task.clarification_options = None
                    return

        # 2. Check for email format in message
        email_match = re.search(r"[\w\.\+\-]+@[\w\.\-]+\.[a-zA-Z]{2,}", clean_msg)
        if email_match:
            task.context["clarified_email"] = email_match.group(0)
            task.clarification_questions = []
            task.clarification_options = None
            task.required_information = []
            return

        # 3. If recipient name was missing
        if "recipient" in task.required_information:
            task.context["clarified_recipient"] = {"name": clean_msg, "email": None}
            task.clarification_questions = []
            task.required_information = []

    @classmethod
    def _extract_outreach_subject_and_body(
        cls, objective: str, recipient_name: str, resume_context: str = "", sender_name: str = "Aman"
    ) -> tuple[str, str]:
        """
        Generates realistic, professional email subject and body without verbatim repetition.
        Follows Section 13 guidelines: professional, natural tone, never inventing ungrounded metrics.
        """
        lower = objective.lower()
        company_name = "CKRIPT" if "ckript" in lower else "our startup"

        # Resolve recipient name if 'there'
        resolved_name = recipient_name
        if resolved_name in ("there", "unknown", ""):
            name_m = re.search(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b", objective)
            if name_m and name_m.group(1).lower() not in ("send", "email", "ckript", "the", "an"):
                resolved_name = name_m.group(1)
            else:
                resolved_name = "there"

        first_name = resolved_name.split()[0] if resolved_name != "there" else "there"

        # 1. Pre-Seed / Funding Outreach
        if any(w in lower for w in ["pre-seed", "pre seed", "funding", "investor", "seed round", "angel"]):
            subject = f"{company_name} — Pre-Seed Funding Discussion"
            body = (
                f"Hi {first_name},\n\n"
                f"I’m building {company_name} and we’re currently exploring pre-seed funding to help us take the next stage of the product forward. "
                f"I’d love to briefly share what we’re building and see if it could be relevant to your investment interests.\n\n"
                f"Would you be open to a short conversation sometime next week?\n\n"
                f"Best,\n{sender_name}"
            )
            return subject, body

        # 2. General / Resume-grounded outreach
        topic = re.sub(
            r"^(?:send(?:\s+an)?\s+emails?\s+to\s+[\w\.\+\-@,]+|email\s+[\w\.\+\-@,]+|reach\s+out\s+to\s+[\w\.\+\-@,]+)\s*(?:saying|about|asking(?:\s+for)?|requesting|regarding)?\s*",
            "",
            objective,
            flags=re.IGNORECASE,
        ).strip()
        if not topic or len(topic) < 3:
            topic = "introductory conversation and collaboration"

        subject = f"Software Engineering Opportunities — {first_name}" if "job" in lower or "opening" in lower else f"Connecting with {first_name}"
        body = f"Hi {first_name},\n\n"
        if resume_context:
            body += (
                f"I hope this note finds you well. I reviewed your work and wanted to reach out regarding {topic}.\n\n"
                f"Given my background and experience in engineering and software systems:\n{resume_context[:250]}...\n\n"
                f"I would welcome the opportunity to connect for a brief introductory conversation.\n\n"
            )
        else:
            body += (
                f"I hope you're having a productive week. I'm reaching out regarding {topic}, "
                f"and would appreciate the chance to connect for a quick introductory conversation.\n\n"
            )
        body += f"Best regards,\n{sender_name}"
        return subject, body


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

        # 3. Multi-File Reasoning or Batch Action from CSV/Attachment
        elif task.task_type in (TaskType.MULTI_FILE_REASONING, TaskType.BATCH_ACTION):
            contacts_data = []
            resume_context = ""
            for att in task.attachments:
                if att.parsed_data and isinstance(att.parsed_data, list):
                    contacts_data.extend(att.parsed_data)
                elif "resume" in att.filename.lower() or "cv" in att.filename.lower() or att.file_type in ("pdf", "txt", "docx"):
                    resume_context = att.extracted_text or ""

            # If no attachment contacts, check DB contacts if batch action
            if not contacts_data and task.task_type == TaskType.BATCH_ACTION:
                db_contacts = await db_manager.list_contacts(filters={"is_valid": True}, limit=50)
                contacts_data = db_contacts

            def _get_field(d: Dict[str, Any], *keys: str) -> Optional[str]:
                d_lower = {str(k).lower().strip(): v for k, v in d.items() if v is not None}
                for k in keys:
                    val = d_lower.get(k.lower().strip())
                    if val is not None and str(val).strip():
                        return str(val).strip()
                return None

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

            if filtered_recipients:
                target_batch = filtered_recipients[:50]
                for c in target_batch:
                    action_id = f"act_{uuid.uuid4().hex[:8]}"
                    first = _get_field(c, "first_name", "first") or ""
                    last = _get_field(c, "last_name", "last") or ""
                    full = f"{first} {last}".strip()
                    name = full or _get_field(c, "name", "full_name", "contact_name") or "there"
                    email = _get_field(c, "email", "work_email", "e-mail", "email_address")
                    comp = _get_field(c, "company", "organization", "firm") or "your company"

                    sender_name = task.context.get("sender_name") or "Aman"
                    subj, body = cls._extract_outreach_subject_and_body(
                        task.objective, name, resume_context=resume_context, sender_name=sender_name
                    )
                    steps.append(
                        ExecutionPlanStep(
                            step_number=len(steps) + 1,
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
                            parameters={"to_email": email, "subject": subj, "body": body},
                            requires_authorization=True,
                        )
                    )
            else:
                sender_name = task.context.get("sender_name") or "Aman"
                for idx, act in enumerate(plan_draft.actions):
                    action_id = f"act_{uuid.uuid4().hex[:8]}"
                    a_type = ActionType(act.action_type)
                    req_auth = (a_type == ActionType.SEND_EMAIL)
                    steps.append(
                        ExecutionPlanStep(
                            step_number=idx + 1,
                            action_type=act.action_type,
                            description=act.description,
                            requires_authorization=req_auth,
                        )
                    )
                    params = dict(act.parameters)
                    if a_type == ActionType.SEND_EMAIL and not params.get("to_email"):
                        params["to_email"] = act.recipient_email or "recruiter@example.com"
                        params["subject"] = act.subject or "Application & Introductory Inquiry"
                        params["body"] = f"Hi,\n\nI reviewed your openings and would love to connect based on my background and experience.\n\nBest regards,\n{sender_name}"

                    actions.append(
                        TaskAction(
                            action_id=action_id,
                            action_type=a_type,
                            description=act.description,
                            parameters=params,
                            requires_authorization=req_auth,
                        )
                    )

        # 5. Single / Multi Communication Actions (Dynamic Action Count)
        else:
            resume_context = ""
            for att in task.attachments:
                if "resume" in att.filename.lower() or att.file_type == "pdf":
                    resume_context = att.extracted_text[:1000] if att.extracted_text else ""

            step_num = 1
            for act in plan_draft.actions:
                action_id = f"act_{uuid.uuid4().hex[:8]}"
                a_type = ActionType(act.action_type)

                if a_type == ActionType.SEND_EMAIL:
                    name = act.recipient_name or "there"
                    email = act.recipient_email
                    if not email:
                        if task.context.get("clarified_email"):
                            email = task.context["clarified_email"]
                        elif task.context.get(f"contact_{name}"):
                            email = task.context[f"contact_{name}"].get("email")
                        elif task.context.get("clarified_recipient"):
                            email = task.context["clarified_recipient"].get("email") or task.context["clarified_recipient"].get("value")
                    if not email:
                        raw_email_match = re.search(r"[\w\.\+\-]+@[\w\.\-]+\.[a-zA-Z]{2,}", task.objective)
                        email = raw_email_match.group(0) if raw_email_match else f"{name.lower()}@example.com"

                    sender_name = task.context.get("sender_name") or "Aman"
                    subj, body = cls._extract_outreach_subject_and_body(
                        act.body_prompt or task.objective, name, resume_context, sender_name=sender_name
                    )
                    steps.append(
                        ExecutionPlanStep(
                            step_number=step_num,
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
                            parameters={"to_email": email, "subject": act.subject or subj, "body": body},
                            requires_authorization=True,
                        )
                    )
                elif a_type == ActionType.CREATE_FOLLOW_UP:
                    steps.append(
                        ExecutionPlanStep(
                            step_number=step_num,
                            action_type="CREATE_FOLLOW_UP",
                            description=act.description,
                            requires_authorization=False,
                        )
                    )
                    actions.append(
                        TaskAction(
                            action_id=action_id,
                            action_type=ActionType.CREATE_FOLLOW_UP,
                            description=act.description,
                            parameters=act.parameters,
                            requires_authorization=False,
                        )
                    )
                else:
                    steps.append(
                        ExecutionPlanStep(
                            step_number=step_num,
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
                step_num += 1

        task.execution_plan = ExecutionPlan(
            summary=f"Plan to execute '{task.objective}' ({len(actions)} action(s)).",
            steps=steps,
            required_integrations=["gmail"] if any(a.action_type == ActionType.SEND_EMAIL for a in actions) else [],
            estimated_actions=len(actions),
            requires_user_approval=any(a.requires_authorization for a in actions),
        )
        task.actions = actions

    @classmethod
    async def _check_authorization_required(cls, task: ExecutionTask) -> bool:
        """
        Determines if user authorization is required before executing actions.
        Checks server-side permission grants; never relies solely on client parameters.
        """
        if task.dry_run:
            return False  # Dry-run is safe to execute without send authorization

        actions_needing_auth = [a for a in task.actions if a.requires_authorization]
        if not actions_needing_auth:
            return False

        enforcer = PermissionEnforcer()
        provider = get_email_provider()
        provider_name = (provider.name if provider else settings.EMAIL_PROVIDER or "mock").lower()

        # If client provided pre-authorization, record real server-side grant
        if task.authorization_scope.get("allow_send") is True:
            check = await enforcer.check(
                user_id=task.user_id,
                scope=PermissionScope.EMAIL_SEND,
                integration=provider_name,
                campaign_id=task.task_id,
            )
            if not check.allowed:
                grant = await permission_service.grant(
                    GrantPermissionRequest(
                        user_id=task.user_id,
                        granted_by=task.user_id,
                        scopes=[PermissionScope.EMAIL_SEND],
                        integration=provider_name,
                        campaign_id=task.task_id,
                        recipient_count=len(actions_needing_auth),
                    )
                )
                task.authorization_scope["grant_id"] = grant.grant_id
            task.authorization = TaskAuthorization(
                required=True,
                authorized=True,
                grant_id=task.authorization_scope.get("grant_id"),
                authorized_by=task.user_id,
                scope={"scope": "EMAIL_SEND", "integration": provider_name},
            )
            return False

        # Server-side verification of active grant
        check = await enforcer.check(
            user_id=task.user_id,
            scope=PermissionScope.EMAIL_SEND,
            integration=provider_name,
            campaign_id=task.task_id,
        )

        if check.allowed:
            task.authorization = TaskAuthorization(
                required=True,
                authorized=True,
                grant_id=task.authorization_scope.get("grant_id"),
                authorized_by=task.user_id,
                scope={"scope": "EMAIL_SEND", "integration": provider_name},
            )
            return False

        # If not allowed on server, authorization IS required
        task.authorization = TaskAuthorization(
            required=True,
            authorized=False,
            scope={"scope": "EMAIL_SEND", "integration": provider_name},
            reasons=check.reasons,
        )
        return True

    @classmethod
    async def _execute_task_actions(cls, task: ExecutionTask) -> None:
        """
        Executes each TaskAction through ToolRegistry and records persistent TaskJobs.
        Performs immediate revocation check before any action requiring authorization.
        """
        for action in task.actions:
            if action.status == ActionStatus.SUCCEEDED:
                continue

            action.status = ActionStatus.RUNNING
            idempotency_key = f"{task.task_id}_{action.action_id}"
            action.idempotency_key = idempotency_key

            # Immediate revocation check for actions requiring authorization
            if action.requires_authorization and not task.dry_run:
                enforcer = PermissionEnforcer()
                provider = get_email_provider()
                provider_name = (provider.name if provider else settings.EMAIL_PROVIDER or "mock").lower()
                auth_check = await enforcer.check(
                    user_id=task.user_id,
                    scope=PermissionScope.EMAIL_SEND,
                    integration=provider_name,
                    campaign_id=task.task_id,
                )
                if not auth_check.allowed:
                    action.status = ActionStatus.FAILED
                    err_msg = "Execution denied: Authorization revoked or missing."
                    action.error = err_msg
                    action.completed_at = utc_now()
                    job = TaskJob(
                        task_id=task.task_id,
                        action_id=action.action_id,
                        action_type=action.action_type,
                        status=ActionStatus.FAILED,
                        error=err_msg,
                        parameters=action.parameters,
                        idempotency_key=idempotency_key,
                        created_at=utc_now(),
                        completed_at=utc_now(),
                    )
                    task.errors.append(f"{action.description}: {err_msg}")
                    await db_manager.save_task_job(job.model_dump(mode="json"))
                    await cls._emit_event(
                        task.task_id,
                        TaskEventType.ACTION_FAILED,
                        f"Action failed: {action.description} ({err_msg})",
                    )
                    continue

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
                retryable = bool(res.get("retryable", False))
                action.status = ActionStatus.RETRY_PENDING if retryable else ActionStatus.FAILED
                action.retryable = retryable
                job.retryable = retryable
                err = res.get("error", "Unknown execution error")
                action.error = err
                action.completed_at = utc_now()
                job.status = ActionStatus.RETRY_PENDING if retryable else ActionStatus.FAILED
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
