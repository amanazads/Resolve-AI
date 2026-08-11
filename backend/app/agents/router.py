import logging
from app.agents.state import AgentState

logger = logging.getLogger(__name__)

def route_intent(state: AgentState) -> str:
    """
    Decides the node execution path based on detected intent and confidence level.
    Returns destination node name: 'rag_node', 'tool_node', 'escalation_node', or 'general_node'.
    """
    intent = state.get("intent", "GENERAL").upper()
    confidence = state.get("confidence", 1.0)
    user_msg = state.get("user_message", "").lower()

    logger.info(f"Router Evaluating -> Intent: '{intent}', Confidence: {confidence}")

    # Check for explicit human escalation request or low confidence
    if intent == "HUMAN_ESCALATION" or confidence < 0.6 or "talk to human" in user_msg or "speak to human" in user_msg or "human agent" in user_msg:
        logger.info("Routing -> escalation_node")
        return "escalation_node"

    # Web Search intent
    if intent == "WEB_SEARCH":
        logger.info("Routing -> web_search_node")
        return "web_search_node"

    # Communication tools intent (email & calls)
    if intent in ["SEND_EMAIL", "MAKE_CALL"]:
        logger.info("Routing -> communication_node")
        return "communication_node"

    # Python math / code evaluation intent
    if intent == "CODE_EXEC":
        logger.info("Routing -> code_node")
        return "code_node"

    # Business API tool intents
    if intent in ["ORDER_STATUS", "CANCEL_ORDER"]:
        logger.info("Routing -> tool_node")
        return "tool_node"

    # Severe complaints
    if intent == "COMPLAINT":
        logger.info("Routing -> escalation_node")
        return "escalation_node"

    # Knowledge base RAG intents
    rag_intents = ["FAQ", "PRODUCT", "PRICING", "REFUND", "SHIPPING", "TROUBLESHOOTING"]
    if intent in rag_intents:
        logger.info("Routing -> rag_node")
        return "rag_node"

    logger.info("Routing -> general_node")
    return "general_node"

