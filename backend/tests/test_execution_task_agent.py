"""
Comprehensive verification tests for Resolve AI's general-purpose autonomous execution agent.

Tests all 10 target scenarios required by the architectural specification:
1. One email ("Send an email to Aman saying hello")
2. Two emails ("Send emails to Aman and Ujjwal")
3. Three emails ("Send personalized emails to Aman, Ujjwal and Rahul")
4. CSV ("Read this CSV and send an email to every founder")
5. Multiple files ("Use my resume and the attached recruiter CSV...")
6. Missing information ("Send an email to Aman" with 2 Amans -> WAITING_FOR_USER)
7. Continue after clarification (User answers -> same task_id resumes and completes)
8. Existing support functionality ("What is the refund policy?" -> RAG response)
9. Tool action (Web search / Math calculation)
10. Dry run (Simulation mode without external send)
"""

import asyncio
import os
import sys
from pathlib import Path
import pytest

# Ensure backend directory is in sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.database.mongodb import db_manager
from app.integrations.mock_provider import MockEmailProvider
from app.integrations.registry import reset_provider_cache, set_email_provider
from app.tasks.agent import TaskAgent
from app.tasks.models import (
    ActionStatus,
    ActionType,
    ExecutionTask,
    TaskStatus,
    TaskType,
)
from app.tasks.service import task_service
from app.tasks.tools import ToolRegistry


@pytest.fixture(autouse=True)
def clean_environment():
    """Ensures in-memory database and mock provider are fresh for each test."""
    for store in (
        db_manager._memory_campaigns,
        db_manager._memory_contacts,
        db_manager._memory_datasets,
        db_manager._memory_tasks,
        db_manager._memory_task_jobs,
        db_manager._memory_task_events,
        db_manager._memory_suppressions,
        db_manager._memory_idempotency,
    ):
        store.clear()
    reset_provider_cache()
    set_email_provider(MockEmailProvider())
    yield
    set_email_provider(None)


@pytest.mark.asyncio
async def test_scenario_1_single_email_with_authorization():
    """
    Test 1: Single email task
    'Send an email to Aman saying hello.'
    Expected: Resolves Aman from contacts, plans 1 action, waits for authorization,
    then executes upon approval and confirms provider send.
    """
    # Seed single contact Aman
    await db_manager.save_contact({
        "contact_id": "c_aman",
        "first_name": "Aman",
        "last_name": "Azad",
        "email": "amanazad@ckript.com",
        "company": "Ckript",
        "role_title": "Founder",
        "contact_type": "FOUNDER",
        "is_valid": True,
    })

    task = await task_service.create_task(
        objective="Send an email to Aman saying hello.",
        authorization_scope={"allow_send": False}
    )

    # Agent plans and pauses for authorization
    assert task.status == TaskStatus.WAITING_FOR_AUTHORIZATION
    assert task.task_type == TaskType.SINGLE_ACTION
    assert len(task.actions) == 1
    assert task.actions[0].action_type == ActionType.SEND_EMAIL
    assert task.actions[0].parameters["to_email"] == "amanazad@ckript.com"

    # User authorizes execution
    completed_task = await task_service.approve_task(task.task_id)
    assert completed_task.status == TaskStatus.COMPLETED
    assert completed_task.actions[0].status == ActionStatus.SUCCEEDED
    assert completed_task.actions[0].result["success"] is True
    assert completed_task.actions[0].result["message_id"] is not None


@pytest.mark.asyncio
async def test_scenario_2_two_emails_multi_action():
    """
    Test 2: Two emails
    'Send emails to Aman and Ujjwal.'
    Expected: 2 actions, no campaign required.
    """
    await db_manager.save_contact({
        "contact_id": "c_aman",
        "first_name": "Aman",
        "last_name": "Azad",
        "email": "amanazad@ckript.com",
        "company": "Ckript",
        "is_valid": True,
    })
    await db_manager.save_contact({
        "contact_id": "c_ujjwal",
        "first_name": "Ujjwal",
        "last_name": "Sharma",
        "email": "ujjwal@zipride.com",
        "company": "ZipRide",
        "is_valid": True,
    })

    task = await task_service.create_task(
        objective="Send emails to Aman and Ujjwal.",
        authorization_scope={"allow_send": True}  # Pre-authorized
    )

    assert task.status == TaskStatus.COMPLETED
    assert task.task_type == TaskType.MULTI_ACTION
    assert len(task.actions) == 2
    recipients = {a.parameters["to_email"] for a in task.actions}
    assert recipients == {"amanazad@ckript.com", "ujjwal@zipride.com"}
    assert all(a.status == ActionStatus.SUCCEEDED for a in task.actions)


@pytest.mark.asyncio
async def test_scenario_3_three_emails_multi_action():
    """
    Test 3: Three emails
    'Send personalized emails to Aman, Ujjwal and Rahul.'
    Expected: 3 actions created and executed.
    """
    await db_manager.save_contact({
        "contact_id": "c_aman",
        "first_name": "Aman",
        "email": "aman@example.com",
        "is_valid": True,
    })
    await db_manager.save_contact({
        "contact_id": "c_ujjwal",
        "first_name": "Ujjwal",
        "email": "ujjwal@example.com",
        "is_valid": True,
    })
    await db_manager.save_contact({
        "contact_id": "c_rahul",
        "first_name": "Rahul",
        "email": "rahul@example.com",
        "is_valid": True,
    })

    task = await task_service.create_task(
        objective="Send personalized emails to Aman, Ujjwal and Rahul.",
        authorization_scope={"allow_send": True}
    )

    assert task.status == TaskStatus.COMPLETED
    assert task.task_type == TaskType.MULTI_ACTION
    assert len(task.actions) == 3
    recipients = {a.parameters["to_email"] for a in task.actions}
    assert recipients == {"aman@example.com", "ujjwal@example.com", "rahul@example.com"}


@pytest.mark.asyncio
async def test_scenario_4_csv_attachment_founder_filtering():
    """
    Test 4: CSV batch processing
    'Read this CSV and send an email to every founder.'
    Expected: Reads CSV, identifies founders, filters non-founders, creates email actions.
    """
    csv_content = (
        "Name,Email,Company,Role\n"
        "Alice Founder,alice@startup.com,Alpha,Co-Founder\n"
        "Bob Engineer,bob@tech.com,Beta,Senior Developer\n"
        "Carol Founder,carol@venture.io,Gamma,Founder & CEO\n"
    )

    task = await task_service.create_task(
        objective="Read this CSV and send an email to every founder.",
        attachments=[{
            "filename": "founders_and_devs.csv",
            "content": csv_content.encode("utf-8")
        }],
        authorization_scope={"allow_send": True}
    )

    assert task.status == TaskStatus.COMPLETED
    assert task.task_type == TaskType.BATCH_ACTION
    # Bob (Senior Developer) must be excluded; Alice and Carol (Founders) included
    recipients = [a.parameters["to_email"] for a in task.actions]
    assert "alice@startup.com" in recipients
    assert "carol@venture.io" in recipients
    assert "bob@tech.com" not in recipients
    assert len(recipients) == 2


@pytest.mark.asyncio
async def test_scenario_5_multiple_files_reasoning():
    """
    Test 5: Multi-file reasoning
    'Use my resume and the attached recruiter CSV to contact relevant recruiters.'
    Expected: Reads both resume and CSV, reasons across both, generates tailored outreach.
    """
    resume_text = (
        "John Doe - Senior AI Engineer\n"
        "Experience with Python, LangGraph, LLMs, and distributed systems."
    )
    recruiter_csv = (
        "Name,Email,Company,Role\n"
        "Sarah Recruiter,sarah@hiring.com,TechCorp,Tech Recruiter\n"
        "David Talent,david@ventures.com,VenturesInc,Talent Lead\n"
    )

    task = await task_service.create_task(
        objective="Use my resume and the attached recruiter CSV to contact relevant recruiters.",
        attachments=[
            {"filename": "resume.txt", "content": resume_text.encode("utf-8")},
            {"filename": "recruiters.csv", "content": recruiter_csv.encode("utf-8")},
        ],
        authorization_scope={"allow_send": True}
    )

    assert task.status == TaskStatus.COMPLETED
    assert len(task.actions) >= 2
    # Verify resume skills were integrated into the message body
    for action in task.actions:
        body = action.parameters.get("body", "")
        assert "experience" in body.lower() or "background" in body.lower()


@pytest.mark.asyncio
async def test_scenario_6_missing_information_triggers_clarification():
    """
    Test 6: Missing information / Ambiguity
    'Send an email to Aman.'
    If 2 Amans exist in contacts, agent MUST enter WAITING_FOR_USER with clarification options.
    """
    await db_manager.save_contact({
        "contact_id": "c_aman1",
        "first_name": "Aman",
        "last_name": "Azad",
        "email": "amanazad@ckript.com",
        "company": "Ckript",
        "is_valid": True,
    })
    await db_manager.save_contact({
        "contact_id": "c_aman2",
        "first_name": "Aman",
        "last_name": "Singh",
        "email": "amansingh@xyz.com",
        "company": "XYZ Corp",
        "is_valid": True,
    })

    task = await task_service.create_task(objective="Send an email to Aman.")

    assert task.status == TaskStatus.WAITING_FOR_USER
    assert len(task.clarification_questions) > 0
    assert "Aman" in task.clarification_questions[0]
    assert task.clarification_options is not None
    assert len(task.clarification_options) == 2


@pytest.mark.asyncio
async def test_scenario_7_continue_after_clarification_on_same_task_id():
    """
    Test 7: Task continuation after user response
    User responds '1' -> resumes existing task_id, completes plan and executes.
    """
    await db_manager.save_contact({
        "contact_id": "c_aman1",
        "first_name": "Aman",
        "last_name": "Azad",
        "email": "amanazad@ckript.com",
        "company": "Ckript",
        "is_valid": True,
    })
    await db_manager.save_contact({
        "contact_id": "c_aman2",
        "first_name": "Aman",
        "last_name": "Singh",
        "email": "amansingh@xyz.com",
        "company": "XYZ Corp",
        "is_valid": True,
    })

    # Step 1: Initial ambiguous request
    initial_task = await task_service.create_task(
        objective="Send an email to Aman saying we are ready.",
        authorization_scope={"allow_send": True}
    )
    assert initial_task.status == TaskStatus.WAITING_FOR_USER
    original_task_id = initial_task.task_id

    # Step 2: User responds "1" to pick the first Aman
    resumed_task = await task_service.resume_task(original_task_id, message="1")

    # MUST be the exact same task_id
    assert resumed_task.task_id == original_task_id
    assert resumed_task.status == TaskStatus.COMPLETED
    assert len(resumed_task.actions) == 1
    assert resumed_task.actions[0].parameters["to_email"] == "amanazad@ckript.com"
    assert resumed_task.actions[0].status == ActionStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_scenario_8_customer_support_rag():
    """
    Test 8: Existing support functionality
    'What is the refund policy?'
    Expected: Routes to RAG search, returns grounded answer.
    """
    task = await task_service.create_task(objective="What is your refund policy?")

    assert task.task_type == TaskType.CUSTOMER_SUPPORT
    assert task.status == TaskStatus.COMPLETED
    assert len(task.actions) == 1
    assert task.actions[0].action_type == ActionType.RAG_QUERY
    assert task.actions[0].status == ActionStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_scenario_9_deterministic_tool_action():
    """
    Test 9: Tool actions (e.g. Python math calculation)
    'Calculate 25 * 4'
    Expected: Executes code tool deterministically.
    """
    task = await task_service.create_task(objective="Calculate 25 * 4")

    assert task.status == TaskStatus.COMPLETED
    assert len(task.actions) == 1
    assert task.actions[0].action_type == ActionType.CODE_EXEC
    assert task.actions[0].status == ActionStatus.SUCCEEDED
    assert task.actions[0].result["result"] == 100


@pytest.mark.asyncio
async def test_scenario_10_dry_run_simulation():
    """
    Test 10: Dry run
    Validates and generates preview, but provider is not contacted.
    """
    await db_manager.save_contact({
        "contact_id": "c_aman",
        "first_name": "Aman",
        "email": "aman@ckript.com",
        "is_valid": True,
    })

    task = await task_service.create_task(
        objective="Send an email to Aman.",
        dry_run=True
    )

    assert task.status == TaskStatus.COMPLETED
    assert len(task.actions) == 1
    action_res = task.actions[0].result
    assert action_res["status"] == "DRY_RUN"
    assert "Dry-run preview" in action_res["message"]
    # Verify no message_id from real provider was created
    assert "message_id" not in action_res
