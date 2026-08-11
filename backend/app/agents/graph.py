import logging
from typing import Dict, Any
# pyrefly: ignore [missing-import]
from langgraph.graph import StateGraph, END
from app.agents.state import AgentState
from app.agents.nodes import (
    intent_detection_node,
    rag_node,
    tool_node,
    escalation_node,
    general_node,
    web_search_node,
    communication_node,
    code_node
)
from app.agents.router import route_intent

logger = logging.getLogger(__name__)

def build_support_graph() -> StateGraph:
    workflow = StateGraph(AgentState)

    workflow.add_node("intent_detection", intent_detection_node)
    workflow.add_node("rag_node", rag_node)
    workflow.add_node("tool_node", tool_node)
    workflow.add_node("escalation_node", escalation_node)
    workflow.add_node("general_node", general_node)
    workflow.add_node("web_search_node", web_search_node)
    workflow.add_node("communication_node", communication_node)
    workflow.add_node("code_node", code_node)

    workflow.set_entry_point("intent_detection")

    workflow.add_conditional_edges(
        "intent_detection",
        route_intent,
        {
            "rag_node": "rag_node",
            "tool_node": "tool_node",
            "escalation_node": "escalation_node",
            "general_node": "general_node",
            "web_search_node": "web_search_node",
            "communication_node": "communication_node",
            "code_node": "code_node"
        }
    )

    workflow.add_edge("rag_node", END)
    workflow.add_edge("tool_node", END)
    workflow.add_edge("escalation_node", END)
    workflow.add_edge("general_node", END)
    workflow.add_edge("web_search_node", END)
    workflow.add_edge("communication_node", END)
    workflow.add_edge("code_node", END)

    return workflow.compile()


support_graph = build_support_graph()

def run_support_agent(session_id: str, user_id: str, user_message: str, history: list = None) -> Dict[str, Any]:
    initial_state: AgentState = {
        "messages": history or [],
        "user_id": user_id,
        "session_id": session_id,
        "user_message": user_message,
        "intent": "GENERAL",
        "confidence": 1.0,
        "reasoning": "",
        "retrieved_context": "",
        "sources": [],
        "tool_name": None,
        "tool_args": None,
        "tool_result": None,
        "response": "",
        "escalation_required": False
    }

    logger.info(f"Running LangGraph Support Workflow for Session: {session_id}")
    final_state = support_graph.invoke(initial_state)
    return final_state
