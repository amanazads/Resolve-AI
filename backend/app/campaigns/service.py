import logging
from datetime import datetime, timezone
from typing import Optional, List, Tuple, Dict, Any

from app.campaigns.models import (
    Campaign,
    CampaignPlan,
    CampaignStatus,
    CommunicationChannel
)
from app.campaigns.planner import CampaignPlanner
from app.database.mongodb import db_manager

logger = logging.getLogger(__name__)

class CampaignService:
    """
    Orchestrates campaign plan generation, dataset audience matching,
    and state persistence.
    """

    @classmethod
    async def create_campaign_plan(
        cls,
        goal: str,
        dataset_id: Optional[str] = None,
        name: Optional[str] = None,
        owner_id: Optional[str] = "default_user",
        channel: Optional[CommunicationChannel] = None
    ) -> Tuple[CampaignPlan, Campaign]:
        """
        Creates a CampaignPlan from natural language goal, links dataset contacts if specified,
        and saves Campaign entity in MongoDB.
        """
        dataset_context = ""
        total_contacts = 0

        # 1. Fetch dataset context if provided
        if dataset_id:
            dataset_record = await db_manager.get_dataset(dataset_id)
            if dataset_record:
                stats = dataset_record.get("statistics", {})
                dataset_context = (
                    f"Filename: {dataset_record.get('filename')}\n"
                    f"Total contacts: {stats.get('valid_contacts', 0)}\n"
                    f"Investors: {stats.get('investors', 0)}, "
                    f"Founders: {stats.get('founders', 0)}, "
                    f"HR/Recruiters: {stats.get('hr', 0)}"
                )

        # 2. Generate Plan
        plan = CampaignPlanner.plan_from_goal(goal, dataset_context)
        if channel:
            plan.channel = channel

        # 3. Calculate audience matching if dataset linked
        if dataset_id:
            try:
                audience_breakdown: Dict[str, int] = {}
                matched = 0
                # Normalise audience types to uppercase to handle any casing variation
                normalised_audience = [a.strip().upper() for a in plan.audience if a]
                plan.audience = normalised_audience  # always store canonical form
                for audience_type in normalised_audience:
                    c_count = await db_manager.count_contacts({
                        "dataset_id": dataset_id,
                        "contact_type": audience_type,
                        "is_valid": True
                    })
                    audience_breakdown[audience_type] = c_count
                    matched += c_count
                total_contacts = matched
                logger.info(
                    "Audience matching for dataset '%s': requested=%s breakdown=%s total=%d",
                    dataset_id, normalised_audience, audience_breakdown, total_contacts,
                )

            except Exception as e:
                logger.warning("Failed to calculate audience count from dataset: %s", e)

        # 4. Determine Campaign Name
        camp_name = name
        if not camp_name:
            type_title = plan.campaign_type.replace("_", " ").title()
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
            camp_name = f"{type_title} - {date_str}"

        # 5. Instantiate and persist Campaign
        campaign = Campaign(
            owner_id=owner_id or "default_user",
            name=camp_name,
            objective=plan.objective,
            audience=plan.audience,
            dataset_id=dataset_id,
            communication_channel=plan.channel,
            message_strategy=plan.message_strategy,
            personalization_fields=plan.personalization_fields,
            plan=plan,
            status=CampaignStatus.READY,
            total_contacts=total_contacts,
            pending=total_contacts
        )

        await db_manager.save_campaign(campaign.model_dump())
        logger.info(f"Created and persisted campaign '{campaign.campaign_id}' with name '{campaign.name}'")

        try:
            from app.campaigns.events import campaign_event_bus, CampaignEventType
            await campaign_event_bus.emit(
                event_type=CampaignEventType.PLAN_GENERATED,
                campaign_id=campaign.campaign_id,
                details=f"Campaign plan generated for goal: '{plan.objective}'",
                metadata={"plan": plan.model_dump()},
            )
            await campaign_event_bus.emit(
                event_type=CampaignEventType.CAMPAIGN_CREATED,
                campaign_id=campaign.campaign_id,
                details=f"Campaign '{campaign.name}' created with {total_contacts} matched recipient(s).",
                progress={
                    "total": total_contacts,
                    "completed": 0,
                    "sent": 0,
                    "failed": 0,
                    "retry_pending": 0,
                    "percent_complete": 0.0,
                    "status": CampaignStatus.READY.value,
                    "worker_status": "idle",
                },
                metadata={"audience": plan.audience, "channel": plan.channel.value},
            )
        except Exception as exc:
            logger.warning("Failed to emit campaign creation events: %s", exc)

        return plan, campaign

    @classmethod
    async def get_campaign(cls, campaign_id: str) -> Optional[Campaign]:
        camp_data = await db_manager.get_campaign(campaign_id)
        if not camp_data:
            return None
        return Campaign(**camp_data)

    @classmethod
    async def list_campaigns(cls, owner_id: Optional[str] = None) -> List[Campaign]:
        all_camps = await db_manager.list_campaigns()
        results: List[Campaign] = []
        for c in all_camps:
            try:
                # Filter by owner if specified
                if owner_id and c.get("owner_id") != owner_id:
                    continue
                # If campaign record has campaign_id or id
                if "campaign_id" not in c and "id" in c:
                    c["campaign_id"] = c["id"]
                if "objective" not in c:
                    c["objective"] = c.get("goal", "")
                if "message_strategy" not in c:
                    c["message_strategy"] = "Automated Campaign"
                results.append(Campaign(**c))
            except Exception as e:
                logger.debug(f"Skipping incompatible campaign record: {e}")
        return results

campaign_service = CampaignService()
