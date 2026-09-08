import json
from typing import Dict, Any, List

PERSONALIZATION_SYSTEM_PROMPT = """You are an expert AI Personalization Copywriter for Resolve AI.
Your goal is to generate unique, highly effective, and strictly factual outreach emails for each recipient based on their structured profile.

CRITICAL ANTI-HALLUCINATION & FACTUALITY RULES:
1. NEVER INVENT or FABRICATE:
   - Investments or prior check sizes (e.g. NEVER say "I loved your investment in Stripe/Uber/etc." unless explicitly stated in their profile).
   - Portfolio companies or fund performance.
   - Employment history, degrees, or shared alumni networks ("as a fellow Stanford alum").
   - Personal relationships or mutual acquaintances ("our mutual friend suggested I reach out").
   - Achievements, awards, or recent news not in the prompt.
   - False facts about their company.
2. MISSING INFORMATION OMISSION:
   - If an attribute (such as investment_focus, role, or company) is missing or blank, completely OMIT the claim.
   - NEVER insert unrendered placeholders like [Insert Company], {company}, <Name>, or TODO.
3. CONCISENESS & TONE:
   - Keep emails concise, professional, and respectful of the recipient's time.
   - Respect the specified maximum length and tone.
4. STRICT JSON OUTPUT:
   - Return ONLY a valid JSON array of personalized message objects.
   - NO markdown fences, NO explanatory chatter.

JSON Output Schema:
[
  {
    "recipient_email": "string",
    "recipient_name": "string",
    "subject": "string",
    "body": "string",
    "personalization_used": ["first_name", "company", ...],
    "confidence": float (0.0 to 1.0)
  }
]
"""

def format_batch_personalization_prompt(
    campaign_objective: str,
    campaign_type: str,
    sender_profile: Dict[str, Any],
    startup_info: Dict[str, Any],
    recipients: List[Dict[str, Any]],
    allowed_fields: List[str],
    campaign_instructions: str = "",
    tone: str = "professional",
    max_length: int = 1500
) -> str:
    """
    Formats the batch personalization prompt for LLM execution.
    """
    recipients_data = []
    for r in recipients:
        recipients_data.append({
            "email": r.get("email"),
            "full_name": r.get("full_name") or f"{r.get('first_name', '')} {r.get('last_name', '')}".strip(),
            "first_name": r.get("first_name"),
            "company": r.get("company") or r.get("firm"),
            "role": r.get("role") or r.get("designation"),
            "investment_focus": r.get("investment_focus"),
            "location": r.get("location"),
            "notes": r.get("notes")
        })

    prompt = (
        f"Campaign Type: {campaign_type}\n"
        f"Campaign Objective: {campaign_objective}\n"
        f"Tone: {tone}\n"
        f"Max Length (words/chars): {max_length}\n"
        f"Allowed Personalization Fields: {allowed_fields}\n\n"
        f"Sender Profile:\n{json.dumps(sender_profile, indent=2)}\n\n"
        f"Startup / Company Information:\n{json.dumps(startup_info, indent=2)}\n\n"
    )

    if campaign_instructions:
        prompt += f"Special Campaign Instructions:\n{campaign_instructions}\n\n"

    # Strategy-specific guidelines
    ctype = (campaign_type or "").upper()
    if "INVESTOR" in ctype:
        prompt += (
            "Strategy: INVESTOR_OUTREACH\n"
            "- Personalize using: recipient first name, firm, verified investment focus (if present), and startup relevance.\n"
            "- Pitch the startup's core thesis and traction succinctly. Ask for a brief introductory call.\n"
            "- Do NOT assume what portfolio companies they backed.\n\n"
        )
    elif "JOB" in ctype:
        prompt += (
            "Strategy: JOB_OUTREACH\n"
            "- Personalize using: recipient name, company, their role, and company industry.\n"
            "- Highlight sender technical background, skills, and interest in contributing to open roles.\n\n"
        )
    elif "INTERN" in ctype:
        prompt += (
            "Strategy: INTERNSHIP_OUTREACH\n"
            "- Personalize using: recipient name, company, their role, and engineering background.\n"
            "- Inquire about internship/co-op opportunities for the upcoming term with relevant project highlights.\n\n"
        )

    prompt += f"Recipients to Personalize ({len(recipients_data)} total):\n{json.dumps(recipients_data, indent=2)}\n\n"
    prompt += "Generate the JSON array of personalized messages now:"

    return prompt
