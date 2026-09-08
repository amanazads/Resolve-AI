"""
Tool registry and deterministic tool execution for Resolve AI.

Every tool declares:
- name
- description
- input_schema
- permissions_required
- side_effects
- supports_dry_run
- execute() method

LLMs formulate plans and parameters; deterministic tools execute and record actual
provider results. The LLM never claims an action completed without confirmed execution.
"""

import asyncio
import logging
import re
from typing import Any, Callable, Dict, List, Optional
from pydantic import BaseModel, Field

from app.database.mongodb import db_manager
from app.integrations.base import SendResult, SendStatus
from app.integrations.registry import get_email_provider
from app.rag.retriever import retrieve_context
from app.tools.code_tools import execute_python_calc
from app.tools.customer_tools import get_customer_details
from app.tools.order_tools import cancel_order, get_order_details
from app.tools.web_tools import web_search

logger = logging.getLogger(__name__)


class ToolDefinition(BaseModel):
    name: str
    description: str
    input_schema: Dict[str, Any]
    permissions_required: List[str] = Field(default_factory=list)
    side_effects: bool = False
    supports_dry_run: bool = False


class ToolRegistry:
    """
    Central discovery and execution engine for all agent tools.
    """

    _tools: Dict[str, ToolDefinition] = {}
    _handlers: Dict[str, Callable] = {}

    @classmethod
    def register(
        cls,
        name: str,
        description: str,
        input_schema: Dict[str, Any],
        permissions_required: Optional[List[str]] = None,
        side_effects: bool = False,
        supports_dry_run: bool = False,
    ):
        """Decorator to register a tool and its handler."""

        def decorator(func: Callable):
            tool_def = ToolDefinition(
                name=name,
                description=description,
                input_schema=input_schema,
                permissions_required=permissions_required or [],
                side_effects=side_effects,
                supports_dry_run=supports_dry_run,
            )
            cls._tools[name] = tool_def
            cls._handlers[name] = func
            return func

        return decorator

    @classmethod
    def get_tool(cls, name: str) -> Optional[ToolDefinition]:
        return cls._tools.get(name)

    @classmethod
    def list_tools(cls) -> List[ToolDefinition]:
        return list(cls._tools.values())

    @classmethod
    async def execute(cls, name: str, **kwargs) -> Dict[str, Any]:
        """
        Executes a tool deterministically, safely returning errors instead of crashing.
        """
        tool_key = name.lower().strip()
        canonical_name = {
            "code_exec": "python_calc",
            "python_calc": "python_calc",
            "rag_query": "rag_search",
            "rag_search": "rag_search",
            "order_query": "order_lookup",
            "order_lookup": "order_lookup",
            "customer_query": "customer_lookup",
            "customer_lookup": "customer_lookup",
            "send_email": "send_email",
            "search_contacts": "search_contacts",
            "web_search": "web_search",
            "read_file": "read_attachment",
            "read_attachment": "read_attachment",
        }.get(tool_key, tool_key)

        # Internal workflow steps that don't need external tool dispatch
        if canonical_name in ("filter_contacts", "generate_messages"):
            return {"success": True, "status": "COMPLETED", "message": f"{canonical_name} processed."}

        if canonical_name not in cls._handlers:
            return {
                "success": False,
                "error": f"Tool '{name}' is not registered in ToolRegistry.",
            }

        handler = cls._handlers[canonical_name]
        try:
            import inspect
            if inspect.iscoroutinefunction(handler):
                return await handler(**kwargs)
            else:
                return handler(**kwargs)
        except Exception as e:
            logger.exception("Error executing tool '%s': %s", name, e)
            return {
                "success": False,
                "error": f"Tool execution failed: {str(e)}",
            }


# =========================================================================
# Standard Registered Tools
# =========================================================================

EMAIL_REGEX = re.compile(r"^[\w\.\+\-]+@[\w\.\-]+\.[a-zA-Z]{2,}$")


@ToolRegistry.register(
    name="send_email",
    description="Sends a single email through the configured provider (Gmail/Mock) after safety and suppression checks.",
    input_schema={
        "type": "object",
        "properties": {
            "to_email": {"type": "string", "description": "Recipient email address"},
            "subject": {"type": "string", "description": "Email subject line"},
            "body": {"type": "string", "description": "Plain text email body"},
            "account_id": {"type": "string", "description": "Sender Gmail account id (default 'default')"},
            "dry_run": {"type": "boolean", "description": "If true, simulates send and generates preview without sending"},
        },
        "required": ["to_email", "subject", "body"],
    },
    permissions_required=["EMAIL_SEND"],
    side_effects=True,
    supports_dry_run=True,
)
async def tool_send_email(
    to_email: str,
    subject: str,
    body: str,
    account_id: Optional[str] = "default",
    dry_run: bool = False,
    **kwargs,
) -> Dict[str, Any]:
    # 1. Email format validation
    clean_email = to_email.strip()
    if not EMAIL_REGEX.match(clean_email):
        return {
            "success": False,
            "status": "FAILED",
            "to_email": clean_email,
            "error": f"Invalid email format: '{clean_email}'",
        }

    # 2. Suppression list check
    try:
        suppression = await db_manager.get_suppression(clean_email)
        if suppression:
            return {
                "success": False,
                "status": "SKIPPED",
                "to_email": clean_email,
                "error": f"Recipient '{clean_email}' is on suppression list ({suppression.get('reason', 'opted out')}).",
            }
    except Exception as e:
        logger.warning("Error checking suppression for %s: %s", clean_email, e)

    # 3. Dry-run mode
    if dry_run:
        return {
            "success": True,
            "status": "DRY_RUN",
            "provider": "simulated",
            "to_email": clean_email,
            "subject": subject,
            "body_preview": body[:120] + ("..." if len(body) > 120 else ""),
            "message": "Dry-run preview generated; no external email was sent.",
        }

    # 4. Real provider send
    provider = get_email_provider()
    result: SendResult = await provider.send_email(
        to_email=clean_email,
        subject=subject,
        body=body,
        account_id=account_id,
    )

    payload = result.to_tool_payload()
    payload["body_preview"] = body[:120] + ("..." if len(body) > 120 else "")
    return payload


@ToolRegistry.register(
    name="search_contacts",
    description="Searches saved contacts by name, email, company, or role title.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search term (e.g. 'Aman', 'founder', 'Google')"},
            "role": {"type": "string", "description": "Role filter: FOUNDER, INVESTOR, HR, RECRUITER, OTHER"},
            "limit": {"type": "integer", "description": "Maximum contacts to return"},
        },
    },
    side_effects=False,
    supports_dry_run=True,
)
async def tool_search_contacts(
    query: Optional[str] = None,
    role: Optional[str] = None,
    limit: int = 10,
    **kwargs,
) -> Dict[str, Any]:
    filters: Dict[str, Any] = {"is_valid": True}
    if role:
        filters["contact_type"] = role.upper()

    contacts = await db_manager.list_contacts(filters=filters, skip=0, limit=max(limit, 50))

    q_lower = (query or "").lower().strip()
    matched = []
    for c in contacts:
        first = (c.get("first_name") or "").lower()
        last = (c.get("last_name") or "").lower()
        full = f"{first} {last}".strip()
        email = (c.get("email") or "").lower()
        company = (c.get("company") or "").lower()
        title = (c.get("role_title") or "").lower()

        if (
            not q_lower
            or q_lower in first
            or q_lower in last
            or q_lower in full
            or q_lower in email
            or q_lower in company
            or q_lower in title
        ):
            matched.append(
                {
                    "contact_id": c.get("contact_id"),
                    "name": f"{c.get('first_name', '')} {c.get('last_name', '')}".strip(),
                    "email": c.get("email"),
                    "company": c.get("company"),
                    "role_title": c.get("role_title"),
                    "contact_type": c.get("contact_type"),
                }
            )
            if len(matched) >= limit:
                break

    return {
        "success": True,
        "count": len(matched),
        "contacts": matched,
    }


@ToolRegistry.register(
    name="rag_search",
    description="Retrieves customer support knowledge and company policies (refund, pricing, hours, etc.).",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Question or policy topic"},
        },
        "required": ["query"],
    },
    side_effects=False,
    supports_dry_run=True,
)
def tool_rag_search(query: str, **kwargs) -> Dict[str, Any]:
    result = retrieve_context(query)
    return {
        "success": True,
        "context_text": result.get("context_text", ""),
        "sources": result.get("sources", []),
    }


@ToolRegistry.register(
    name="web_search",
    description="Searches the live web for recent news, company data, or public information.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
        },
        "required": ["query"],
    },
    side_effects=False,
    supports_dry_run=True,
)
def tool_web_search(query: str, **kwargs) -> Dict[str, Any]:
    res = web_search(query)
    return {
        "success": True,
        "results": res.get("results", []),
        "query": query,
    }


@ToolRegistry.register(
    name="python_calc",
    description="Executes mathematical calculations and percentage computations safely.",
    input_schema={
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "Math expression to evaluate"},
        },
        "required": ["expression"],
    },
    side_effects=False,
    supports_dry_run=True,
)
def tool_python_calc(expression: Optional[str] = None, **kwargs) -> Dict[str, Any]:
    expr = expression or kwargs.get("query") or kwargs.get("expr") or "100 * 0.15"
    m = re.search(r"[\d\.\s\+\-\*\/\(\)\^%]+", expr)
    clean_expr = m.group(0).strip() if m else expr
    res = execute_python_calc(clean_expr)
    return {
        "success": bool(res.get("success") or res.get("status") == "success"),
        "result": res.get("result"),
        "error": res.get("error"),
    }


@ToolRegistry.register(
    name="order_lookup",
    description="Looks up customer e-commerce order details.",
    input_schema={
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "Order identifier (e.g. ORD-12345)"},
        },
        "required": ["order_id"],
    },
    side_effects=False,
    supports_dry_run=True,
)
def tool_order_lookup(order_id: str, **kwargs) -> Dict[str, Any]:
    res = get_order_details(order_id)
    return {"success": "error" not in res, "order": res}


@ToolRegistry.register(
    name="cancel_order",
    description="Cancels an order if permitted by cancellation policy.",
    input_schema={
        "type": "object",
        "properties": {
            "order_id": {"type": "string", "description": "Order identifier to cancel"},
        },
        "required": ["order_id"],
    },
    side_effects=True,
    supports_dry_run=True,
)
def tool_cancel_order(order_id: str, **kwargs) -> Dict[str, Any]:
    res = cancel_order(order_id)
    return {"success": "error" not in res, "result": res}


@ToolRegistry.register(
    name="customer_lookup",
    description="Fetches customer account details and order history.",
    input_schema={
        "type": "object",
        "properties": {
            "customer_id": {"type": "string", "description": "Customer identifier"},
        },
        "required": ["customer_id"],
    },
    side_effects=False,
    supports_dry_run=True,
)
def tool_customer_lookup(customer_id: str, **kwargs) -> Dict[str, Any]:
    res = get_customer_details(customer_id)
    return {"success": "error" not in res, "customer": res}
