import json
import re
import logging
from typing import Optional, Dict, Any

from app.campaigns.models import (
    CampaignPlan,
    CampaignType,
    CommunicationChannel
)
from app.campaigns.prompts import (
    CAMPAIGN_PLANNER_SYSTEM_PROMPT,
    format_planner_prompt
)
from app.llm.client import invoke_llm

logger = logging.getLogger(__name__)

class CampaignPlanner:
    """
    Translates natural language user objectives into structured, validated CampaignPlans.
    Strictly decides and plans without directly executing external actions.
    """

    @classmethod
    def plan_from_goal(cls, goal: str, dataset_context: Optional[str] = None) -> CampaignPlan:
        """
        Generates and validates a CampaignPlan using LLM with deterministic fallback.
        """
        logger.info(f"Planning campaign from natural language goal: '{goal}'")
        full_prompt = f"{CAMPAIGN_PLANNER_SYSTEM_PROMPT}\n\n{format_planner_prompt(goal, dataset_context or '')}"

        try:
            raw_response = invoke_llm(full_prompt)
            plan = cls._parse_and_validate_response(raw_response)
            if plan:
                logger.info(f"Successfully generated plan via LLM: type='{plan.campaign_type}', audience={plan.audience}")
                return plan
        except Exception as e:
            logger.warning(f"LLM plan generation encountered an issue ({e}). Using deterministic fallback planner.")

        # Fallback to deterministic planning
        fallback_plan = cls._heuristic_campaign_plan(goal)
        logger.info(f"Generated plan via heuristic fallback: type='{fallback_plan.campaign_type}', audience={fallback_plan.audience}")
        return fallback_plan

    @classmethod
    def _parse_and_validate_response(cls, response_text: str) -> Optional[CampaignPlan]:
        """Extracts and validates JSON from raw LLM response."""
        clean = response_text.strip()
        # Strip markdown fences if present
        if clean.startswith("```"):
            clean = re.sub(r"^```(?:json)?", "", clean)
            clean = re.sub(r"```$", "", clean)
            clean = clean.strip()

        # Find first { and last }
        start_idx = clean.find("{")
        end_idx = clean.rfind("}")
        if start_idx != -1 and end_idx != -1:
            json_str = clean[start_idx : end_idx + 1]
            data = json.loads(json_str)

            # Ensure channel is enum compatible
            if "channel" in data and isinstance(data["channel"], str):
                data["channel"] = data["channel"].upper()
            if "campaign_type" in data and isinstance(data["campaign_type"], str):
                data["campaign_type"] = data["campaign_type"].upper()

            # Pydantic validation
            return CampaignPlan(**data)

        return None

    @classmethod
    def _heuristic_campaign_plan(cls, goal: str) -> CampaignPlan:
        """
        Deterministic planning fallback ensuring 100% reliability and zero crash risk.
        """
        lower = goal.lower()

        # Channel detection (Email by default, LinkedIn if explicitly asked)
        channel = CommunicationChannel.LINKEDIN if "linkedin" in lower else CommunicationChannel.EMAIL

        # 1. Investor Outreach
        if any(w in lower for w in ["investor", "fundrais", "vc", "pitch", "angel", "check", "seed round", "series a"]):
            return CampaignPlan(
                campaign_type=CampaignType.INVESTOR_OUTREACH,
                audience=["INVESTOR", "VC"],
                channel=channel,
                objective="Request an introductory investment conversation and pitch startup mission",
                message_strategy="Executive introduction highlighting founder background, traction metrics, problem thesis, and investor alignment",
                personalization_fields=["first_name", "company", "investment_focus"],
                suggested_subject_line="Introduction & Investment Discussion",
                tone="professional",
                call_to_action="15-minute introductory call to share our investor deck"
            )

        # 2. Internship Outreach
        if any(w in lower for w in ["intern", "internship", "co-op", "summer analyst", "trainee"]):
            return CampaignPlan(
                campaign_type=CampaignType.INTERNSHIP_OUTREACH,
                audience=["FOUNDER", "HR", "RECRUITER"],
                channel=channel,
                objective="Inquire about software engineering internship and student opportunities",
                message_strategy="Direct tailored inquiry demonstrating passion for the company product, projects, and relevant engineering skills",
                personalization_fields=["first_name", "company", "role"],
                suggested_subject_line="Software Engineering Internship Inquiry",
                tone="enthusiastic and professional",
                call_to_action="Quick conversation regarding potential internship opportunities"
            )

        # 3. Job / Career Outreach
        if any(w in lower for w in ["job", "career", "hiring", "role", "full-time", "engineer", "employment"]):
            return CampaignPlan(
                campaign_type=CampaignType.JOB_OUTREACH,
                audience=["FOUNDER", "HR", "RECRUITER"],
                channel=channel,
                objective="Explore open software engineering and technical career opportunities",
                message_strategy="Concise value proposition detailing engineering experience, problem-solving skills, and interest in open roles",
                personalization_fields=["first_name", "company", "role"],
                suggested_subject_line="Software Engineer Opportunity Inquiry",
                tone="professional",
                call_to_action="Brief discussion about open opportunities on the team"
            )

        # 4. Custom Outreach
        return CampaignPlan(
            campaign_type=CampaignType.CUSTOM_OUTREACH,
            audience=["OTHER", "FOUNDER"],
            channel=channel,
            objective=goal[:150],
            message_strategy="Personalized networking and direct outreach tailored to contact background",
            personalization_fields=["first_name", "company"],
            suggested_subject_line="Connecting regarding collaboration",
            tone="friendly and professional",
            call_to_action="Brief conversation to explore potential collaboration"
        )
