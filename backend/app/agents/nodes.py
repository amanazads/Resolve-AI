import re
import json
import logging
from typing import Dict, Any
from app.agents.state import AgentState
from app.llm.client import invoke_llm
from app.llm.prompts import INTENT_CLASSIFICATION_PROMPT, RAG_SYSTEM_PROMPT, TOOL_RESPONSE_PROMPT
from app.rag.retriever import retrieve_context
from app.tools.order_tools import get_order_status, get_order_details, cancel_order
from app.tools.customer_tools import get_customer_details
from app.tools.web_tools import web_search
from app.tools.communication_tools import send_email, make_phone_call
from app.tools.code_tools import execute_python_calc

logger = logging.getLogger(__name__)

def web_search_node(state: AgentState) -> Dict[str, Any]:
    user_msg = state.get("user_message", "")
    logger.info(f"--- Web Search Node for: '{user_msg}' ---")

    search_data = web_search(user_msg)
    results = search_data.get("results", [])

    results_text = "\n".join([f"- Title: {r.get('title')}\n  Snippet: {r.get('snippet')}\n  URL: {r.get('url')}" for r in results])
    
    from app.llm.prompts import WEB_SEARCH_PROMPT
    prompt = WEB_SEARCH_PROMPT.format(search_results=results_text, query=user_msg)
    response = invoke_llm(prompt)

    return {
        "tool_name": "web_search",
        "tool_result": search_data,
        "response": response,
        "sources": [r.get("url") for r in results if r.get("url")],
        "escalation_required": False
    }

def communication_node(state: AgentState) -> Dict[str, Any]:
    user_msg = state.get("user_message", "")
    intent = state.get("intent", "")
    logger.info(f"--- Communication Node (Intent: {intent}) for: '{user_msg}' ---")

    tool_used = None
    tool_result = None

    if intent == "SEND_EMAIL":
        tool_used = "send_email"
        # Extract email or use default
        email_match = re.search(r"[\w\.-]+@[\w\.-]+", user_msg)
        to_email = email_match.group(0) if email_match else "customer@example.com"
        subject = "Support Assistance & Information Request"
        body = f"Hello,\n\nIn response to your query: '{user_msg}', we have processed your request.\n\nBest regards,\nAI Support Team"
        tool_result = send_email(to_email, subject, body)
    else:
        tool_used = "make_phone_call"
        phone_match = re.search(r"\+?\d[\d\s-]{7,14}\d", user_msg)
        phone = phone_match.group(0) if phone_match else "+1-800-555-0199"
        msg_text = f"Hello, this is an automated support update regarding your request: {user_msg}"
        tool_result = make_phone_call(phone, msg_text)

    prompt = TOOL_RESPONSE_PROMPT.format(
        tool_result=json.dumps(tool_result, indent=2),
        query=user_msg
    )
    response = invoke_llm(prompt)

    return {
        "tool_name": tool_used,
        "tool_result": tool_result,
        "response": response,
        "sources": [],
        "escalation_required": False
    }

def code_node(state: AgentState) -> Dict[str, Any]:
    user_msg = state.get("user_message", "")
    logger.info(f"--- Code Execution / Math Node for: '{user_msg}' ---")

    # Extract math expression from query
    expr_match = re.search(r"[\d\.\s\+\-\*\/\(\)\^%]+", user_msg)
    expr = expr_match.group(0).strip() if expr_match else "100 * 0.15"

    tool_result = execute_python_calc(expr)

    prompt = TOOL_RESPONSE_PROMPT.format(
        tool_result=json.dumps(tool_result, indent=2),
        query=user_msg
    )
    response = invoke_llm(prompt)

    return {
        "tool_name": "execute_python_calc",
        "tool_result": tool_result,
        "response": response,
        "sources": [],
        "escalation_required": False
    }


def intent_detection_node(state: AgentState) -> Dict[str, Any]:
    user_msg = state.get("user_message", "").strip()
    user_msg_lower = user_msg.lower()
    logger.info(f"--- Intent Detection Node for: '{user_msg}' ---")

    # Fast-path instant pattern detection (<1ms) to eliminate redundant LLM intent roundtrips
    fast_intent = None
    if any(w in user_msg_lower for w in ["human", "representative", "speak to a person", "speak to a human", "real manager", "agent", "support team", "इंसान", "प्रतिनिधि"]):
        fast_intent = "HUMAN_ESCALATION"
    elif any(w in user_msg_lower for w in ["send an email", "send email", "write an email", "email to"]):
        fast_intent = "SEND_EMAIL"
    elif any(w in user_msg_lower for w in ["make a call", "make call", "call to", "dial"]):
        fast_intent = "MAKE_CALL"
    elif any(w in user_msg_lower for w in ["calculate", "math", "percent", "tip", "+", "*", "/"]) and re.search(r"\d", user_msg):
        fast_intent = "CODE_EXEC"
    elif any(w in user_msg_lower for w in ["search the web", "search web", "latest news", "weather in"]):
        fast_intent = "WEB_SEARCH"
    elif any(w in user_msg_lower for w in ["refund", "money back", "return policy", "eligible for a full refund"]):
        fast_intent = "REFUND"
    elif any(w in user_msg_lower for w in ["cancel my order", "cancel order", "please cancel", "cancel ord", "can i cancel"]):
        fast_intent = "CANCEL_ORDER"
    elif any(w in user_msg_lower for w in ["cost", "price of", "how much does"]):
        fast_intent = "PRICING"
    elif any(w in user_msg_lower for w in ["shipping", "delivery time", "ground shipping"]):
        fast_intent = "SHIPPING"
    elif any(w in user_msg_lower for w in ["operating hours", "location", "student discount", "military discount"]):
        fast_intent = "FAQ"
    elif any(w in user_msg_lower for w in ["battery life", "waterproof", "techpro", "smartfit"]):
        fast_intent = "PRODUCT"
    elif any(w in user_msg_lower for w in ["terrible", "angry", "horrible", "complaint"]):
        fast_intent = "COMPLAINT"

    if fast_intent:
        logger.info(f"Fast-path Detected Intent: {fast_intent} (Confidence: 0.95)")
        return {
            "intent": fast_intent,
            "confidence": 0.95,
            "reasoning": "Fast-path pattern match"
        }

    prompt = f"{INTENT_CLASSIFICATION_PROMPT}\nUser Query: {user_msg}"
    raw_output = invoke_llm(prompt)

    try:
        cleaned_output = raw_output.strip()
        if cleaned_output.startswith("```json"):
            cleaned_output = cleaned_output.split("```json")[1].split("```")[0].strip()
        elif cleaned_output.startswith("```"):
            cleaned_output = cleaned_output.split("```")[1].split("```")[0].strip()

        data = json.loads(cleaned_output)
        intent = data.get("intent", "GENERAL").upper()
        confidence = float(data.get("confidence", 0.8))
        reasoning = data.get("reasoning", "")
    except Exception as e:
        logger.warning(f"Error parsing intent JSON output ({e}). Falling back to HEURISTIC detection.")
        intent = "GENERAL"
        confidence = 0.8
        reasoning = "Fallback parser match"

    logger.info(f"Detected Intent: {intent} (Confidence: {confidence})")
    return {
        "intent": intent,
        "confidence": confidence,
        "reasoning": reasoning
    }

def rag_node(state: AgentState) -> Dict[str, Any]:
    user_msg = state.get("user_message", "")
    logger.info(f"--- RAG Node for query: '{user_msg}' ---")

    rag_result = retrieve_context(user_msg)
    retrieved_context = rag_result["context_text"]
    sources = rag_result["sources"]

    if not retrieved_context.strip():
        logger.warning("No context retrieved from RAG knowledge base.")
        return {
            "retrieved_context": "",
            "sources": [],
            "response": "I'm sorry, but that specific information is currently unavailable in our knowledge base.",
            "escalation_required": False
        }

    prompt = RAG_SYSTEM_PROMPT.format(context=retrieved_context, query=user_msg)
    response = invoke_llm(prompt)

    if sources and not any(src in response for src in sources):
        formatted_sources = ", ".join(sources)
        response += f"\n\nSources: {formatted_sources}"

    return {
        "retrieved_context": retrieved_context,
        "sources": sources,
        "response": response,
        "escalation_required": False
    }

def tool_node(state: AgentState) -> Dict[str, Any]:
    user_msg = state.get("user_message", "")
    intent = state.get("intent", "")
    user_id = state.get("user_id", "user123")
    logger.info(f"--- Tool Node (Intent: {intent}) ---")

    order_id_match = re.search(r"ORD\d+", user_msg, re.IGNORECASE)
    order_id = order_id_match.group(0).upper() if order_id_match else "ORD123"

    tool_used = None
    tool_result = None

    if intent == "CANCEL_ORDER":
        tool_used = "cancel_order"
        tool_result = cancel_order(order_id)
    elif intent == "ORDER_STATUS":
        tool_used = "get_order_status"
        tool_result = get_order_status(order_id)
    else:
        if "customer" in user_msg.lower() or "profile" in user_msg.lower():
            tool_used = "get_customer_details"
            tool_result = get_customer_details(user_id)
        else:
            tool_used = "get_order_details"
            tool_result = get_order_details(order_id)

    prompt = TOOL_RESPONSE_PROMPT.format(
        tool_result=json.dumps(tool_result, indent=2),
        query=user_msg
    )
    response = invoke_llm(prompt)

    return {
        "tool_name": tool_used,
        "tool_args": {"order_id": order_id},
        "tool_result": tool_result,
        "response": response,
        "sources": [],
        "escalation_required": False
    }

def escalation_node(state: AgentState) -> Dict[str, Any]:
    intent = state.get("intent", "HUMAN_ESCALATION")
    confidence = state.get("confidence", 1.0)
    logger.info(f"--- Escalation Node Triggered (Intent: {intent}, Conf: {confidence}) ---")

    reason = "User requested human support"
    if confidence < 0.6:
        reason = f"Low confidence ({confidence}) in intent classification"
    elif intent == "COMPLAINT":
        reason = "Severe customer complaint detected"

    escalation_message = (
        "I'm unable to resolve this query reliably with automated assistance. "
        "I have flagged your conversation and escalated this request to a human customer support specialist. "
        "A team member will review your message shortly."
    )

    return {
        "response": escalation_message,
        "escalation_required": True,
        "reasoning": reason,
        "sources": []
    }

def general_node(state: AgentState) -> Dict[str, Any]:
    user_msg = state.get("user_message", "")
    logger.info(f"--- General Node for query: '{user_msg}' ---")

    prompt = f"You are a helpful AI customer support agent. Answer politely and concisely: {user_msg}"
    response = invoke_llm(prompt)

    return {
        "response": response,
        "escalation_required": False,
        "sources": []
    }
