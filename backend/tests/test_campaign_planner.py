import pytest
from pathlib import Path
import sys

# Ensure backend is on sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.campaigns.models import (
    Campaign,
    CampaignPlan,
    CampaignType,
    CommunicationChannel,
    CampaignStatus
)
from app.campaigns.planner import CampaignPlanner
from app.campaigns.service import campaign_service
from app.contacts.schema import Contact, ContactType
from app.database.mongodb import db_manager


@pytest.fixture(autouse=True)
def reset_db():
    """Reset in-memory storage for clean test isolation."""
    db_manager._memory_campaigns.clear()
    db_manager._memory_contacts.clear()
    db_manager._memory_datasets.clear()


# =========================================================================
# 1. Natural Language Planning Tests
# =========================================================================

def test_plan_investor_outreach():
    goal = (
        "Use the uploaded contacts. Send a personalized fundraising email to investors "
        "explaining my startup and asking for an investment conversation."
    )
    plan = CampaignPlanner.plan_from_goal(goal)

    assert isinstance(plan, CampaignPlan)
    assert plan.campaign_type == CampaignType.INVESTOR_OUTREACH
    assert "INVESTOR" in plan.audience or "VC" in plan.audience
    assert plan.channel == CommunicationChannel.EMAIL
    assert len(plan.personalization_fields) > 0
    assert any(f in plan.personalization_fields for f in ["first_name", "company", "investment_focus"])
    assert len(plan.objective) > 0
    assert len(plan.message_strategy) > 0


def test_plan_job_outreach():
    goal = (
        "Contact founders and HR professionals from this sheet and send personalized "
        "messages asking about open software engineering career and job opportunities."
    )
    plan = CampaignPlanner.plan_from_goal(goal)

    assert isinstance(plan, CampaignPlan)
    assert plan.campaign_type == CampaignType.JOB_OUTREACH
    assert any(role in plan.audience for role in ["FOUNDER", "HR", "RECRUITER"])
    assert plan.channel == CommunicationChannel.EMAIL
    assert "first_name" in plan.personalization_fields


def test_plan_internship_outreach():
    goal = (
        "Contact founders and HR professionals from this sheet and send personalized "
        "messages asking about software engineering internship and student opportunities."
    )
    plan = CampaignPlanner.plan_from_goal(goal)

    assert isinstance(plan, CampaignPlan)
    assert plan.campaign_type == CampaignType.INTERNSHIP_OUTREACH
    assert any(role in plan.audience for role in ["FOUNDER", "HR", "RECRUITER"])
    assert plan.channel == CommunicationChannel.EMAIL


def test_plan_custom_outreach():
    goal = "Reach out to university researchers to explore potential joint research collaboration and partnerships."
    plan = CampaignPlanner.plan_from_goal(goal)

    assert isinstance(plan, CampaignPlan)
    assert plan.campaign_type == CampaignType.CUSTOM_OUTREACH
    assert plan.channel == CommunicationChannel.EMAIL
    assert len(plan.personalization_fields) > 0


def test_plan_linkedin_channel_architecture():
    goal = "Connect with venture capitalists on LinkedIn and send a personalized direct message about our seed round."
    plan = CampaignPlanner.plan_from_goal(goal)

    assert isinstance(plan, CampaignPlan)
    assert plan.channel == CommunicationChannel.LINKEDIN
    assert plan.campaign_type == CampaignType.INVESTOR_OUTREACH


# =========================================================================
# 2. Campaign Service & Dataset Audience Matching Tests
# =========================================================================

@pytest.mark.asyncio
async def test_campaign_with_dataset_audience_matching():
    dataset_id = "ds_sample_target"

    # Insert contacts with different types into database
    test_contacts = [
        Contact(
            dataset_id=dataset_id,
            full_name="Alice Angel",
            email="alice@angel.com",
            contact_type=ContactType.INVESTOR,
            is_valid=True
        ).model_dump(),
        Contact(
            dataset_id=dataset_id,
            full_name="Bob VC",
            email="bob@venture.com",
            contact_type=ContactType.VC,
            is_valid=True
        ).model_dump(),
        Contact(
            dataset_id=dataset_id,
            full_name="Charlie Partner",
            email="charlie@fund.com",
            contact_type=ContactType.VC,
            is_valid=True
        ).model_dump(),
        Contact(
            dataset_id=dataset_id,
            full_name="Dave Founder",
            email="dave@startup.com",
            contact_type=ContactType.FOUNDER,
            is_valid=True
        ).model_dump(),
        Contact(
            dataset_id=dataset_id,
            full_name="Eve HR",
            email="eve@bigco.com",
            contact_type=ContactType.HR,
            is_valid=True
        ).model_dump()
    ]

    await db_manager.save_contacts_batch(test_contacts)

    goal = "Send a personalized fundraising email to investors and VCs explaining our startup traction."
    plan, campaign = await campaign_service.create_campaign_plan(
        goal=goal,
        dataset_id=dataset_id,
        name="Q3 Seed Fundraising"
    )

    assert campaign.name == "Q3 Seed Fundraising"
    assert campaign.dataset_id == dataset_id
    assert campaign.status == CampaignStatus.READY
    # INVESTOR + VC = 1 + 2 = 3 contacts
    assert campaign.total_contacts == 3
    assert campaign.pending == 3
    assert campaign.plan is not None

    # Retrieve from database
    fetched = await campaign_service.get_campaign(campaign.campaign_id)
    assert fetched is not None
    assert fetched.campaign_id == campaign.campaign_id
    assert fetched.total_contacts == 3


# =========================================================================
# 3. REST API Routes Tests
# =========================================================================

@pytest.mark.asyncio
async def test_campaign_api_routes():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)

    # 1. POST /api/campaigns/plan
    request_data = {
        "goal": "Send personalized fundraising email to investors asking for an investment meeting.",
        "name": "Investor Outreach 2026",
        "owner_id": "user_42"
    }

    resp = client.post("/api/campaigns/plan", json=request_data)
    assert resp.status_code == 200
    data = resp.json()

    assert "plan" in data
    assert "campaign" in data
    assert data["plan"]["campaign_type"] == "INVESTOR_OUTREACH"
    assert data["campaign"]["name"] == "Investor Outreach 2026"
    assert data["campaign"]["owner_id"] == "user_42"

    camp_id = data["campaign"]["campaign_id"]

    # 2. GET /api/campaigns
    list_resp = client.get("/api/campaigns")
    assert list_resp.status_code == 200
    all_camps = list_resp.json()
    assert any(c["campaign_id"] == camp_id for c in all_camps)

    # 3. GET /api/campaigns/{campaign_id}
    detail_resp = client.get(f"/api/campaigns/{camp_id}")
    assert detail_resp.status_code == 200
    detail_data = detail_resp.json()
    assert detail_data["campaign_id"] == camp_id
    assert detail_data["status"] == "READY"

    # 4. GET /api/campaigns/non_existent
    not_found = client.get("/api/campaigns/non_existent_12345")
    assert not_found.status_code == 404


# =========================================================================
# 4. Existing Support Chat Functionality Regression Test
# =========================================================================

@pytest.mark.asyncio
async def test_preserve_existing_support_chat():
    from app.services.chat_service import process_chat_message

    # Verify existing chat pipeline remains intact
    res = await process_chat_message(
        session_id="session_campaign_regression",
        user_id="test_user",
        user_message="Hello, what are your customer support operating hours?"
    )

    assert res.response is not None
    assert res.intent in ["GREETING", "FAQ", "GENERAL"]
    assert len(res.response) > 0
