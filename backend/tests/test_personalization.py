import json
import sys
from pathlib import Path

import pytest

# Ensure backend is on sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.personalization.validator import (
    PersonalizedMessage,
    PersonalizationValidator,
    ValidationStatus
)
from app.personalization.generator import PersonalizationGenerator
from app.personalization.templates import TemplateEngine


@pytest.fixture(autouse=True)
def deterministic_llm(monkeypatch):
    """
    Keeps the suite hermetic. Without this, every generate_* call would hit the
    real Gemini endpoint whenever GEMINI_API_KEY is present, making the tests
    slow, costly and non-deterministic. Returning an unusable response drives
    the engine down its deterministic TemplateEngine path; the tests that
    exercise the LLM path override this with their own canned response.
    """
    monkeypatch.setattr("app.personalization.generator.invoke_llm", lambda prompt: "")


@pytest.fixture
def sender_profile():
    return {
        "name": "Alex Rivera",
        "title": "Founder & CEO",
        "company": "Resolve AI",
        "skills": "Python, Distributed Systems, AI Agents",
        "bio": "Building autonomous AI agent architectures."
    }


@pytest.fixture
def startup_info():
    return {
        "name": "Resolve AI",
        "industry": "AI Automation",
        "one_liner": "we build autonomous agent infrastructure that eliminates manual business workflows.",
        "traction": "we have 10 enterprise pilots and 25k in MRR"
    }


# =========================================================================
# 1. Strategy Personalization Tests
# =========================================================================

def test_investor_outreach_personalization(sender_profile, startup_info):
    generator = PersonalizationGenerator()
    recipient = {
        "email": "sarah@apexventures.com",
        "first_name": "Sarah",
        "full_name": "Sarah Connor",
        "company": "Apex Ventures",
        "role": "General Partner",
        "investment_focus": "Enterprise B2B SaaS and AI"
    }

    msg = generator.generate_single(
        campaign_objective="Request a 15-minute intro meeting to share our investor deck",
        campaign_type="INVESTOR_OUTREACH",
        recipient=recipient,
        sender_profile=sender_profile,
        startup_info=startup_info
    )

    assert isinstance(msg, PersonalizedMessage)
    assert msg.recipient_email == "sarah@apexventures.com"
    assert msg.recipient_name == "Sarah Connor"
    assert "Sarah" in msg.body
    assert "Apex Ventures" in msg.body
    assert msg.validation_status == ValidationStatus.VALID
    assert len(msg.warnings) == 0
    assert len(msg.subject) > 5
    assert len(msg.body) > 50


def test_job_outreach_personalization(sender_profile, startup_info):
    generator = PersonalizationGenerator()
    recipient = {
        "email": "marcus@hightech.com",
        "first_name": "Marcus",
        "full_name": "Marcus Vance",
        "company": "HighTech Corp",
        "role": "VP of Engineering"
    }

    msg = generator.generate_single(
        campaign_objective="Explore open senior software engineering opportunities",
        campaign_type="JOB_OUTREACH",
        recipient=recipient,
        sender_profile=sender_profile,
        startup_info=startup_info
    )

    assert msg.validation_status == ValidationStatus.VALID
    assert "HighTech Corp" in msg.body or "HighTech Corp" in msg.subject
    assert "Marcus" in msg.body
    assert "engineering" in msg.body.lower()


def test_internship_outreach_personalization(sender_profile, startup_info):
    generator = PersonalizationGenerator()
    recipient = {
        "email": "charlotte@scaleai.com",
        "first_name": "Charlotte",
        "full_name": "Charlotte Bronte",
        "company": "ScaleAI Systems",
        "role": "Head of People"
    }

    msg = generator.generate_single(
        campaign_objective="Inquire about software engineering internship and co-op roles",
        campaign_type="INTERNSHIP_OUTREACH",
        recipient=recipient,
        sender_profile=sender_profile,
        startup_info=startup_info
    )

    assert msg.validation_status == ValidationStatus.VALID
    assert "intern" in msg.body.lower()
    assert "ScaleAI Systems" in msg.body or "ScaleAI Systems" in msg.subject


# =========================================================================
# 2. Anti-Hallucination & Missing Information Tests
# =========================================================================

def test_omits_missing_information_cleanly(sender_profile, startup_info):
    """
    CRITICAL: If information is unavailable, omit the claim. Do not fabricate.
    """
    generator = PersonalizationGenerator()
    # Contact has NO company, NO investment focus, NO role
    recipient = {
        "email": "anonymous@domain.com",
        "first_name": "",
        "full_name": "Anonymous Lead",
        "company": "",
        "role": "",
        "investment_focus": ""
    }

    msg = generator.generate_single(
        campaign_objective="Reach out for potential intro call",
        campaign_type="INVESTOR_OUTREACH",
        recipient=recipient,
        sender_profile=sender_profile,
        startup_info=startup_info
    )

    assert msg.validation_status == ValidationStatus.VALID
    # Must not have left raw placeholders
    for bad_token in ["[", "]", "{", "}", "<", ">", "TODO", "UNDEFINED"]:
        assert bad_token not in msg.body
        assert bad_token not in msg.subject


def test_placeholder_validation_failure():
    validator = PersonalizationValidator()

    # Raw message containing unrendered template placeholders
    bad_msg = PersonalizedMessage(
        recipient_email="test@example.com",
        recipient_name="Test User",
        subject="Hello [Insert Company] Team",
        body="Dear {first_name}, I saw that <Company> is working on TODO."
    )

    checked = validator.validate_message(bad_msg)
    assert checked.validation_status == ValidationStatus.INVALID
    assert any("placeholder" in w.lower() for w in checked.warnings)


def test_suspicious_claims_detection():
    validator = PersonalizationValidator()

    # Message with fabricated investment claims
    fabricated_msg = PersonalizedMessage(
        recipient_email="partner@fund.com",
        recipient_name="Partner Jane",
        subject="Intro from a fellow alumnus",
        body="Hi Jane, as a fellow alumnus, I noticed you invested in Stripe and wanted to connect."
    )

    checked = validator.validate_message(fabricated_msg, recipient_profile={"notes": ""})
    assert checked.validation_status == ValidationStatus.INVALID
    assert any("Suspicious unverified claim" in w for w in checked.warnings)


# =========================================================================
# 3. Duplicate and Similarity Detection Tests
# =========================================================================

def test_duplicate_and_similarity_detection():
    validator = PersonalizationValidator()

    msg1 = PersonalizedMessage(
        recipient_email="user1@example.com",
        recipient_name="User 1",
        subject="Quick intro meeting",
        body="Hi Alice, we are building autonomous agents at Resolve AI and would love to connect."
    )

    # Identical body
    msg2 = PersonalizedMessage(
        recipient_email="user2@example.com",
        recipient_name="User 2",
        subject="Quick intro meeting",
        body="Hi Alice, we are building autonomous agents at Resolve AI and would love to connect."
    )

    validator.validate_message(msg1)
    validator.validate_message(msg2)

    assert msg1.validation_status == ValidationStatus.VALID
    assert msg2.validation_status == ValidationStatus.WARNING
    assert any("Duplicate" in w for w in msg2.warnings)

    # Test Jaccard similarity
    sim_identical = PersonalizationValidator.calculate_similarity(msg1.body, msg2.body)
    assert sim_identical == 1.0

    distinct_body = "Hello Bob, I am applying for the backend software engineering role on your data platform team."
    sim_distinct = PersonalizationValidator.calculate_similarity(msg1.body, distinct_body)
    assert sim_distinct < 0.30


def test_length_and_empty_validation():
    validator = PersonalizationValidator()

    # Empty subject
    empty_sub = PersonalizedMessage(
        recipient_email="valid@example.com",
        recipient_name="Name",
        subject="",
        body="This is a valid body with sufficient character length."
    )
    checked_sub = validator.validate_message(empty_sub)
    assert checked_sub.validation_status == ValidationStatus.INVALID
    assert any("empty" in w.lower() for w in checked_sub.warnings)

    # Invalid email
    bad_email = PersonalizedMessage(
        recipient_email="not-an-email",
        recipient_name="Name",
        subject="Valid Subject",
        body="This is a valid body with sufficient character length."
    )
    checked_email = validator.validate_message(bad_email)
    assert checked_email.validation_status == ValidationStatus.INVALID


# =========================================================================
# 4. Batch Generation Test
# =========================================================================

def test_batch_generation(sender_profile, startup_info):
    generator = PersonalizationGenerator(batch_size=3)

    recipients = [
        {"email": f"investor_{i}@fund{i}.com", "first_name": f"Investor{i}", "company": f"Fund {i}", "role": "Partner", "investment_focus": "AI"}
        for i in range(6)
    ]

    messages = generator.generate_batch(
        campaign_objective="Fundraising outreach for Seed round",
        campaign_type="INVESTOR_OUTREACH",
        recipients=recipients,
        sender_profile=sender_profile,
        startup_info=startup_info
    )

    assert len(messages) == 6
    for idx, msg in enumerate(messages):
        assert msg.recipient_email == f"investor_{idx}@fund{idx}.com"
        assert msg.validation_status in [ValidationStatus.VALID, ValidationStatus.WARNING]
        assert len(msg.subject) > 0
        assert len(msg.body) > 0


# =========================================================================
# 5. Omission Across Every Strategy
# =========================================================================

CAMPAIGN_TYPES = [
    "INVESTOR_OUTREACH",
    "JOB_OUTREACH",
    "INTERNSHIP_OUTREACH",
    "CUSTOM_OUTREACH",
]

PLACEHOLDER_ARTIFACTS = ["[", "]", "{", "}", "<", ">", "TODO", "TBD", "UNDEFINED", "None"]


@pytest.mark.parametrize("campaign_type", CAMPAIGN_TYPES)
def test_omits_missing_information_for_every_strategy(campaign_type, sender_profile, startup_info):
    """
    A contact with nothing but an email must still produce a clean, grammatical
    message: no placeholders, no dangling punctuation, no invented facts.
    """
    generator = PersonalizationGenerator()
    recipient = {"email": "unknown.lead@example.com"}

    msg = generator.generate_single(
        campaign_objective="Open a conversation",
        campaign_type=campaign_type,
        recipient=recipient,
        sender_profile=sender_profile,
        startup_info=startup_info,
    )

    assert msg.validation_status == ValidationStatus.VALID, msg.warnings
    text = f"{msg.subject}\n{msg.body}"

    for artifact in PLACEHOLDER_ARTIFACTS:
        assert artifact not in text, f"{campaign_type} leaked '{artifact}': {text}"

    # No grammar artifacts left behind by an omitted clause.
    assert " ," not in text
    assert " ." not in text
    assert ".." not in text
    assert "  " not in text
    assert "'s's" not in text

    # Nothing was fabricated to fill the gaps.
    assert msg.personalization_used == []


@pytest.mark.parametrize("campaign_type", CAMPAIGN_TYPES)
def test_full_profile_is_used_and_reported(campaign_type, sender_profile, startup_info):
    """personalization_used must be an audit trail of what actually rendered."""
    generator = PersonalizationGenerator()
    recipient = {
        "email": "dana@northwind.io",
        "first_name": "Dana",
        "full_name": "Dana Whitfield",
        "company": "Northwind Labs",
        "role": "Director of Engineering",
        "investment_focus": "Developer tooling",
    }

    msg = generator.generate_single(
        campaign_objective="Open a conversation",
        campaign_type=campaign_type,
        recipient=recipient,
        sender_profile=sender_profile,
        startup_info=startup_info,
    )

    text = f"{msg.subject}\n{msg.body}"
    assert "Dana" in text
    assert "first_name" in msg.personalization_used

    # Every reported field must genuinely appear in the rendered message.
    field_values = {
        "first_name": "Dana",
        "company": "Northwind Labs",
        "role": "Director of Engineering",
        "investment_focus": "Developer tooling",
    }
    for field in msg.personalization_used:
        assert field_values[field] in text


def test_allowed_fields_restricts_personalization(sender_profile, startup_info):
    """Fields excluded from allowed_personalization_fields must never surface."""
    generator = PersonalizationGenerator()
    recipient = {
        "email": "dana@northwind.io",
        "first_name": "Dana",
        "company": "Northwind Labs",
        "role": "Director of Engineering",
    }

    msg = generator.generate_single(
        campaign_objective="Open a conversation",
        campaign_type="JOB_OUTREACH",
        recipient=recipient,
        sender_profile=sender_profile,
        startup_info=startup_info,
        allowed_personalization_fields=["first_name"],
    )

    text = f"{msg.subject}\n{msg.body}"
    assert "Dana" in text
    assert "Northwind Labs" not in text
    assert "Director of Engineering" not in text
    assert msg.personalization_used == ["first_name"]


# =========================================================================
# 6. N-gram Similarity
# =========================================================================

def test_ngram_similarity_separates_phrasing_from_vocabulary():
    a = "We build autonomous agents that remove manual operations work for finance teams."
    # Same words, completely different order: unigram similarity is high, trigram is not.
    b = "Finance teams remove manual work for operations that build autonomous agents we."

    unigram = PersonalizationValidator.calculate_similarity(a, b, n=1)
    trigram = PersonalizationValidator.calculate_similarity(a, b, n=3)

    assert unigram > 0.9
    assert trigram < 0.2

    assert PersonalizationValidator.calculate_similarity(a, a, n=3) == 1.0
    assert PersonalizationValidator.calculate_similarity("", a, n=3) == 0.0


def test_batch_diversity_audit_flags_cookie_cutter_messages():
    shared = (
        "Hi {name}, I am the founder of Resolve AI and we build autonomous agent "
        "infrastructure that eliminates manual business workflows for operations teams. "
        "Would you be open to a fifteen minute introductory conversation next week?"
    )
    messages = [
        PersonalizedMessage(
            recipient_email=f"person{i}@example.com",
            recipient_name=f"Person {i}",
            subject="Quick intro",
            body=shared.format(name=f"Person{i}"),
        )
        for i in range(3)
    ]

    flagged = PersonalizationValidator.audit_batch_diversity(messages, max_similarity=0.85, n=3)

    assert len(flagged) == 3  # every pair
    for msg in messages:
        assert msg.validation_status == ValidationStatus.WARNING
        assert any("High similarity" in w for w in msg.warnings)


# =========================================================================
# 7. LLM Path: Alignment, Fallback and Repair
# =========================================================================

@pytest.fixture
def recipients_pair():
    return [
        {"email": "first@alpha.com", "first_name": "Ada", "full_name": "Ada Lovelace", "company": "Alpha Fund"},
        {"email": "second@beta.com", "first_name": "Grace", "full_name": "Grace Hopper", "company": "Beta Capital"},
    ]


def _patch_llm(monkeypatch, response):
    monkeypatch.setattr(
        "app.personalization.generator.invoke_llm",
        lambda prompt: response,
    )


def test_llm_output_is_aligned_by_email_not_position(monkeypatch, recipients_pair, sender_profile, startup_info):
    """A reordered model response must never mis-address an email."""
    reordered = json.dumps(
        [
            {
                "recipient_email": "second@beta.com",
                "recipient_name": "Grace Hopper",
                "subject": "Beta Capital and Resolve AI",
                "body": "Hi Grace, I lead Resolve AI and would value fifteen minutes to share what we are building.",
                "personalization_used": ["first_name", "company"],
                "confidence": 0.9,
            },
            {
                "recipient_email": "first@alpha.com",
                "recipient_name": "Ada Lovelace",
                "subject": "Alpha Fund and Resolve AI",
                "body": "Hi Ada, I am the founder of Resolve AI and would welcome a short introductory conversation.",
                "personalization_used": ["first_name", "company"],
                "confidence": 0.9,
            },
        ]
    )
    _patch_llm(monkeypatch, f"```json\n{reordered}\n```")

    generator = PersonalizationGenerator()
    messages = generator.generate_batch(
        campaign_objective="Fundraising",
        campaign_type="INVESTOR_OUTREACH",
        recipients=recipients_pair,
        sender_profile=sender_profile,
        startup_info=startup_info,
    )

    assert [m.recipient_email for m in messages] == ["first@alpha.com", "second@beta.com"]
    assert "Ada" in messages[0].body
    assert "Grace" in messages[1].body


def test_llm_partial_output_falls_back_per_recipient(monkeypatch, recipients_pair, sender_profile, startup_info):
    """A recipient the model skipped is still served by the deterministic template."""
    partial = json.dumps(
        [
            {
                "recipient_email": "first@alpha.com",
                "recipient_name": "Ada Lovelace",
                "subject": "Alpha Fund and Resolve AI",
                "body": "Hi Ada, I am the founder of Resolve AI and would welcome a short introductory conversation.",
                "personalization_used": ["first_name"],
                "confidence": 0.9,
            }
        ]
    )
    _patch_llm(monkeypatch, partial)

    generator = PersonalizationGenerator()
    messages = generator.generate_batch(
        campaign_objective="Fundraising",
        campaign_type="INVESTOR_OUTREACH",
        recipients=recipients_pair,
        sender_profile=sender_profile,
        startup_info=startup_info,
    )

    assert len(messages) == 2
    assert messages[1].recipient_email == "second@beta.com"
    assert "Grace" in messages[1].body
    assert "Beta Capital" in messages[1].body
    assert messages[1].validation_status in (ValidationStatus.VALID, ValidationStatus.WARNING)


def test_hallucinated_llm_output_is_repaired(monkeypatch, recipients_pair, sender_profile, startup_info):
    """
    A model that leaves a placeholder or invents an investment must not ship;
    the deterministic template replaces it and the reason is recorded.
    """
    bad = json.dumps(
        [
            {
                "recipient_email": "first@alpha.com",
                "recipient_name": "Ada Lovelace",
                "subject": "Following [Insert Company]",
                "body": "Hi Ada, as a fellow alumnus I noticed you invested in Stripe and wanted to reach out about {company}.",
                "personalization_used": ["first_name"],
                "confidence": 0.95,
            },
            {
                "recipient_email": "second@beta.com",
                "recipient_name": "Grace Hopper",
                "subject": "Beta Capital and Resolve AI",
                "body": "Hi Grace, I lead Resolve AI and would value fifteen minutes to share what we are building.",
                "personalization_used": ["first_name", "company"],
                "confidence": 0.9,
            },
        ]
    )
    _patch_llm(monkeypatch, bad)

    generator = PersonalizationGenerator()
    messages = generator.generate_batch(
        campaign_objective="Fundraising",
        campaign_type="INVESTOR_OUTREACH",
        recipients=recipients_pair,
        sender_profile=sender_profile,
        startup_info=startup_info,
    )

    repaired = messages[0]
    assert repaired.validation_status != ValidationStatus.INVALID
    assert "[Insert Company]" not in repaired.subject
    assert "invested in Stripe" not in repaired.body
    assert "fellow alumnus" not in repaired.body
    assert "{company}" not in repaired.body
    assert any("failed validation" in w for w in repaired.warnings)
    assert "Alpha Fund" in repaired.body


def test_unparseable_llm_response_falls_back_to_templates(monkeypatch, recipients_pair, sender_profile, startup_info):
    _patch_llm(monkeypatch, "I'm sorry, I cannot help with that request.")

    generator = PersonalizationGenerator()
    messages = generator.generate_batch(
        campaign_objective="Fundraising",
        campaign_type="INVESTOR_OUTREACH",
        recipients=recipients_pair,
        sender_profile=sender_profile,
        startup_info=startup_info,
    )

    assert len(messages) == 2
    for msg, recipient in zip(messages, recipients_pair):
        assert msg.recipient_email == recipient["email"]
        assert msg.validation_status != ValidationStatus.INVALID


def test_llm_exception_falls_back_to_templates(monkeypatch, recipients_pair, sender_profile, startup_info):
    def boom(prompt):
        raise RuntimeError("429 rate limited")

    monkeypatch.setattr("app.personalization.generator.invoke_llm", boom)

    generator = PersonalizationGenerator()
    messages = generator.generate_batch(
        campaign_objective="Fundraising",
        campaign_type="INVESTOR_OUTREACH",
        recipients=recipients_pair,
        sender_profile=sender_profile,
        startup_info=startup_info,
    )

    assert len(messages) == 2
    assert all(m.validation_status != ValidationStatus.INVALID for m in messages)


# =========================================================================
# 8. Large Diverse Batch
# =========================================================================

def test_batch_generation_ten_diverse_recipients(sender_profile, startup_info):
    generator = PersonalizationGenerator(batch_size=4)

    recipients = [
        {"email": "gp@apex.vc", "first_name": "Sarah", "company": "Apex Ventures", "role": "General Partner", "investment_focus": "B2B SaaS"},
        {"email": "principal@northstar.vc", "first_name": "Ravi", "company": "Northstar Capital", "role": "Principal"},
        {"email": "angel@example.com", "first_name": "Mei", "investment_focus": "Developer tools"},
        {"email": "vp.eng@hightech.com", "first_name": "Marcus", "company": "HighTech Corp", "role": "VP of Engineering"},
        {"email": "recruiter@scaleai.com", "first_name": "Charlotte", "company": "ScaleAI Systems", "role": "Technical Recruiter"},
        {"email": "cto@fintechco.io", "first_name": "Yusuf", "company": "FinTechCo", "role": "CTO"},
        {"email": "people@datalake.dev", "full_name": "Priya Raman", "company": "DataLake", "role": "Head of People"},
        {"email": "founder@seedling.app", "first_name": "Tom", "company": "Seedling"},
        {"email": "lead@quantum.works", "first_name": "Ana", "company": "Quantum Works", "role": "Engineering Manager"},
        {"email": "sparse@example.org"},
    ]

    messages = generator.generate_batch(
        campaign_objective="Introduce Resolve AI and request a short conversation",
        campaign_type="INVESTOR_OUTREACH",
        recipients=recipients,
        sender_profile=sender_profile,
        startup_info=startup_info,
    )

    assert len(messages) == 10
    for idx, msg in enumerate(messages):
        assert msg.recipient_email == recipients[idx]["email"]
        assert msg.validation_status != ValidationStatus.INVALID, msg.warnings
        assert msg.subject.strip()
        assert msg.body.strip()
        for artifact in ["[", "]", "{", "}", "TODO"]:
            assert artifact not in msg.body

    # Every recipient got their own address, and no body is empty.
    assert len({m.recipient_email for m in messages}) == 10


# =========================================================================
# 9. Regression: existing features are untouched
# =========================================================================

def test_preserve_all_existing_features():
    """
    The personalization engine is additive: campaign planning, contact parsing
    and the support toolchain must behave exactly as before.
    """
    # --- Campaign planning -------------------------------------------------
    from app.campaigns.planner import CampaignPlanner
    from app.campaigns.models import CampaignPlan, CampaignType

    plan = CampaignPlanner.plan_from_goal(
        "Use the uploaded contacts. Send a personalized fundraising email to investors "
        "explaining my startup and asking for an investment conversation."
    )
    assert isinstance(plan, CampaignPlan)
    assert plan.campaign_type == CampaignType.INVESTOR_OUTREACH

    # --- Contact parsing / normalization / validation ----------------------
    from app.contacts.parser import FileParser
    from app.contacts.normalizer import ContactNormalizer
    from app.contacts.validator import ContactValidator

    csv_bytes = (
        "Full Name,Email,Company\n"
        "Alice Smith,alice@example.com,Acme Corp\n"
        "Bob Jones,bob@example.com,Beta Inc\n"
    ).encode("utf-8")
    headers, rows = FileParser.parse_csv(csv_bytes)
    assert headers == ["Full Name", "Email", "Company"]
    assert len(rows) == 2

    mapping = ContactNormalizer.detect_column_mapping(headers)
    normalized = ContactNormalizer.normalize_row(rows[0], mapping)
    assert normalized["email"] == "alice@example.com"

    validator = ContactValidator()
    is_valid, errors, _ = validator.validate_and_deduplicate(normalized)
    assert is_valid, errors
    assert ContactValidator.is_valid_email("alice@example.com")
    assert not ContactValidator.is_valid_email("not-an-email")

    # --- Support chat toolchain -------------------------------------------
    from app.tools.order_tools import get_order_status
    from app.tools.code_tools import execute_python_calc

    order = get_order_status("ORD123")
    assert order["success"] is True
    assert order["order_id"] == "ORD123"
    assert execute_python_calc("2 + 2")["success"] is True
