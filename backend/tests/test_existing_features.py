import sys
from pathlib import Path
import pytest

# Ensure backend is on sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.agents.graph import run_support_agent
from app.agents.state import AgentState
from app.tools.order_tools import get_order_status, cancel_order
from app.tools.code_tools import execute_python_calc
from app.tools.communication_tools import send_email, make_phone_call


def test_agent_state_extended_fields():
    """Verify that AgentState supports both support and automation fields without breaking."""
    state: AgentState = {
        "messages": [],
        "user_id": "user123",
        "session_id": "sess_1",
        "user_message": "Hello",
        "intent": "GENERAL",
        "confidence": 1.0,
        "reasoning": "greeting",
        "retrieved_context": "",
        "sources": [],
        "tool_name": None,
        "tool_args": None,
        "tool_result": None,
        "response": "Hello!",
        "escalation_required": False,
        # Automation fields
        "user_goal": "Goal test",
        "automation_type": "email_outreach",
        "campaign_id": "camp_1",
        "job_id": "job_1",
        "retry_count": 0,
        "execution_status": "READY"
    }
    assert state["session_id"] == "sess_1"
    assert state["campaign_id"] == "camp_1"
    assert state["execution_status"] == "READY"


def test_order_status_tool():
    res = get_order_status("ORD123")
    assert res["success"] is True
    assert res["order_id"] == "ORD123"
    assert res["status"] == "Shipped"


def test_order_cancel_tool():
    res = cancel_order("ORD456")
    assert res["success"] is True
    assert res["status"] == "Cancelled"


def test_code_execution_math_tool():
    res = execute_python_calc("150 * 0.20")
    assert res["success"] is True
    assert res["result"] == 30.0


def test_communication_tools():
    email_res = send_email("client@example.com", "Test Subject", "Test Body")
    assert email_res["success"] is True
    assert email_res["to_email"] == "client@example.com"

    call_res = make_phone_call("+15551234567", "Hello from support")
    assert call_res["success"] is True
    assert call_res["phone_number"] == "+15551234567"


def test_existing_support_agent_workflow():
    """Verify support graph workflow with LangGraph for various queries."""
    # 1. Order status query
    res = run_support_agent(
        session_id="test_sess_order",
        user_id="user123",
        user_message="Where is my order ORD123?"
    )
    assert res["intent"] == "ORDER_STATUS"
    assert res["escalation_required"] is False
    assert "ORD123" in res["response"]

    # 2. Math query
    res_math = run_support_agent(
        session_id="test_sess_math",
        user_id="user123",
        user_message="Calculate 25 * 4"
    )
    assert res_math["intent"] == "CODE_EXEC"
    assert res_math["escalation_required"] is False

    # 3. Human escalation query
    res_esc = run_support_agent(
        session_id="test_sess_esc",
        user_id="user123",
        user_message="I want to speak to a human representative right now"
    )
    assert res_esc["intent"] == "HUMAN_ESCALATION"
    assert res_esc["escalation_required"] is True
