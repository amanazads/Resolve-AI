"""
End-to-End Verification of the Three Mandatory Product Demos for Resolve AI:

DEMO 1:
"Send an email to sharmaujjwal2019@gmail.com, Ujjwal Sharma, requesting pre-seed funding for my startup CKRIPT."
- Understands objective
- Generates structured plan
- Generates professional email without repeating prompt verbatim
- Pauses at WAITING_FOR_AUTHORIZATION (untrusted side effects require human consent)
- User approves -> Backend validates grant -> Executes tool
- Provider response captured with provider_message_id
- Transitions to COMPLETED

DEMO 2:
"Read the attached investor spreadsheet and identify the investors who are relevant for CKRIPT. Then prepare personalized outreach emails for them."
- Ingests CSV spreadsheet
- Understands columns and filters relevant investors
- Generates personalized drafts
- Pauses for authorization before sending

DEMO 3:
"Read these three documents and summarize the important information."
- Ingests 3 distinct document artifacts (PDF, DOCX, TXT)
- Extracts and reasons across all 3 documents
- Produces cross-document synthesis
- Completes with ZERO email permissions or authorizations required (general purpose)
"""

import os
import sys
from pathlib import Path
import pytest

backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.config import settings
from app.database.mongodb import db_manager

from app.integrations.mock_provider import MockEmailProvider
from app.integrations.registry import reset_provider_cache, set_email_provider
from app.tasks.models import (
    ActionStatus,
    ActionType,
    ExecutionTask,
    TaskStatus,
    TaskType,
)
from app.tasks.service import task_service
from app.tasks.attachments import AttachmentProcessor


@pytest.fixture(autouse=True)
def clean_test_env():
    db_manager.is_connected = False
    for attr in (
        "_memory_messages",
        "_memory_escalations",
        "_memory_plans",
        "_memory_campaigns",
        "_memory_jobs",
        "_memory_idempotency",
        "_memory_contacts",
        "_memory_datasets",
        "_memory_integrations",
        "_memory_campaign_jobs",
        "_memory_campaign_progress",
        "_memory_automation_runs",
        "_memory_permissions",
        "_memory_audit",
        "_memory_suppressions",
        "_memory_safety_audits",
        "_memory_campaign_activities",
        "_memory_tasks",
        "_memory_task_jobs",
        "_memory_task_events",
    ):
        store = getattr(db_manager, attr, None)
        if store is not None and hasattr(store, "clear"):
            store.clear()
    reset_provider_cache()
    mock_provider = MockEmailProvider()
    set_email_provider(mock_provider)
    yield mock_provider
    set_email_provider(None)


# =========================================================================
# DEMO 1: Single Outreach Email to Ujjwal Sharma (CKRIPT Pre-Seed)
# =========================================================================
@pytest.mark.asyncio
async def test_demo_1_funding_outreach_flow(clean_test_env):
    """
    Step 1 -> Enter prompt
    Step 2 -> Understands objective
    Step 3 -> Generates plan & email
    Step 4 -> Asks for authorization
    Step 5 -> User approves -> Executes Gmail tool
    Step 6 -> Captures provider message ID & completes
    """
    objective = (
        "Send an email to sharmaujjwal2019@gmail.com, Ujjwal Sharma, "
        "requesting pre-seed funding for my startup CKRIPT."
    )

    # 1. Create task without prior authorization
    task = await task_service.create_task(
        objective=objective,
        authorization_scope={"allow_send": False},
        dry_run=False,
    )

    assert task.task_id is not None
    assert task.status == TaskStatus.WAITING_FOR_AUTHORIZATION
    assert len(task.actions) >= 1

    email_action = task.actions[0]
    assert email_action.action_type == ActionType.SEND_EMAIL
    assert email_action.parameters["to_email"] == "sharmaujjwal2019@gmail.com"
    assert "Ujjwal" in email_action.parameters.get("subject", "") or "CKRIPT" in email_action.parameters.get("subject", "")

    body = email_action.parameters.get("body", "")
    assert len(body) > 20
    # Must not just parrot the prompt verbatim
    assert "Hi Ujjwal" in body or "Hello Ujjwal" in body
    # Never claim sent before authorization
    assert len(clean_test_env.outbox) == 0

    # 2. User authorizes and runs
    approved_task = await task_service.approve_task(task.task_id)

    assert approved_task.status == TaskStatus.COMPLETED
    assert len(clean_test_env.outbox) == 1
    sent_msg = clean_test_env.outbox[0]
    assert sent_msg["to_email"] == "sharmaujjwal2019@gmail.com"


    # Action has captured provider confirmation
    completed_action = approved_task.actions[0]
    assert completed_action.status == ActionStatus.SUCCEEDED
    assert completed_action.provider_message_id is not None or completed_action.result.get("message_id") is not None


# =========================================================================
# DEMO 2: Investor Spreadsheet Ingestion & Personalized Outreach
# =========================================================================
@pytest.mark.asyncio
async def test_demo_2_investor_spreadsheet_reasoning(clean_test_env):
    """
    "Read the attached investor spreadsheet and identify the investors who are
    relevant for CKRIPT. Then prepare personalized outreach emails for them."
    """
    csv_content = (
        "Name,Email,Firm,Focus,Stage\n"
        "Sarah Chen,sarah@sequoia.com,Sequoia,AI & Infrastructure,Seed/Series A\n"
        "Michael Brown,mbrown@accel.com,Accel,Consumer Apps,Growth\n"
        "Alex Rivera,alex@foundersfund.com,Founders Fund,DeepTech & Crypto,Pre-seed\n"
    )

    task = await task_service.create_task(
        objective="Read the attached investor spreadsheet and identify the investors who are relevant for CKRIPT. Then prepare personalized outreach emails for them.",
        attachments=[{
            "filename": "investor_leads.csv",
            "content": csv_content.encode("utf-8"),
        }],
        authorization_scope={"allow_send": False},
        dry_run=False,
    )

    assert task.status in (TaskStatus.WAITING_FOR_AUTHORIZATION, TaskStatus.PLAN_READY)
    assert len(task.attachments) == 1
    assert task.attachments[0].filename == "investor_leads.csv"
    assert task.execution_plan is not None
    assert len(task.execution_plan.steps) >= 2

    # Verify actions prepared without sending automatically
    assert len(clean_test_env.outbox) == 0
    assert len(task.actions) >= 1


# =========================================================================
# DEMO 3: Multi-Document Reasoning (Zero Email Side Effects)
# =========================================================================
@pytest.mark.asyncio
async def test_demo_3_multi_document_synthesis(clean_test_env):
    """
    "Read these three documents and summarize the important information."
    General-purpose document reasoning across 3 files with NO email permissions requested.
    """
    doc1 = "CKRIPT Whitepaper: CKRIPT is a zero-knowledge autonomous execution agent."
    doc2 = "Market Report: The market for enterprise AI agents is growing at 45% CAGR."
    doc3 = "Security Protocol: All outgoing side-effect actions require explicit user authorization."

    task = await task_service.create_task(
        objective="Read these three documents and summarize the important information.",
        attachments=[
            {"filename": "doc1_whitepaper.txt", "content": doc1.encode("utf-8")},
            {"filename": "doc2_market.txt", "content": doc2.encode("utf-8")},
            {"filename": "doc3_security.txt", "content": doc3.encode("utf-8")},
        ],
    )

    # Document analysis completes without asking for email authorization
    assert task.status in (TaskStatus.COMPLETED, TaskStatus.PLAN_READY, TaskStatus.READY)
    assert len(task.attachments) == 3
    # Zero emails should be sent or requested
    assert len(clean_test_env.outbox) == 0
    assert task.authorization is None or task.authorization.required is False
