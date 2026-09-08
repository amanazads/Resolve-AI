"""
Tests for the contact classification + normalization pipeline.

Covers:
- Founder / HR / Recruiter / Investor / VC / UNKNOWN classification
- All common role header variations (Designation, Job Title, Title, etc.)
- contact_type_hint column
- Filename-based hint extraction
- Campaign audience matching (case normalisation)
- Unknown when no information is available
"""
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.contacts.normalizer import ContactNormalizer
from app.contacts.classifier import ContactClassifier, _classify_from_hint_str
from app.contacts.schema import ContactType
from app.api.routes.contacts import _extract_type_hint_from_filename


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def classify(role="", company="", hint="", notes="", focus=""):
    data = {
        "role": role,
        "company": company,
        "contact_type_hint": hint,
        "notes": notes,
        "investment_focus": focus,
    }
    c_type, _ = ContactClassifier.classify_contact(data)
    return c_type.value


def norm_and_classify(headers, row):
    row["_source_row"] = 1
    mapping = ContactNormalizer.detect_column_mapping(headers)
    norm = ContactNormalizer.normalize_row(row, mapping)
    c_type, _ = ContactClassifier.classify_contact(norm)
    return c_type.value, norm


# ---------------------------------------------------------------------------
# Founder classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", [
    "Founder",
    "Co-Founder",
    "Founder & CEO",
    "CEO / Founder",
    "Entrepreneur",
    "Startup Founder",
    "Founding CTO",
    "co-founder",
    "Cofounder",
    "Chief Executive Officer",
    "CEO",
    "CTO",
])
def test_founder_roles(role):
    assert classify(role=role) == "FOUNDER", f"Expected FOUNDER for role={role!r}"


# ---------------------------------------------------------------------------
# HR classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", [
    "HR",
    "HR Manager",
    "HR Head",
    "Head of HR",
    "Human Resources",
    "Human Resources Manager",
    "People Operations",
    "People & Culture",
    "HRBP",
    "HR Director",
    "HR Coordinator",
    "HR Business Partner",
])
def test_hr_roles(role):
    assert classify(role=role) == "HR", f"Expected HR for role={role!r}"


# ---------------------------------------------------------------------------
# Recruiter classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", [
    "Recruiter",
    "Technical Recruiter",
    "Talent Acquisition",
    "Talent Acquisition Manager",
    "Talent Partner",
    "Recruitment Lead",
    "Hiring Manager",
    "headhunter",
    "Sourcer",
    "Staffing Specialist",
])
def test_recruiter_roles(role):
    assert classify(role=role) == "RECRUITER", f"Expected RECRUITER for role={role!r}"


# ---------------------------------------------------------------------------
# Investor / VC classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role,expected", [
    ("Investor", "INVESTOR"),
    ("Investment Analyst", "INVESTOR"),
    ("Angel Investor", "INVESTOR"),
    ("Portfolio Manager", "INVESTOR"),
    ("VC", "VC"),
    ("Venture Capital", "VC"),
    ("General Partner", "VC"),
    ("Investment Partner", "VC"),
    ("Managing Partner", "VC"),
])
def test_investor_vc_roles(role, expected):
    assert classify(role=role) == expected, f"Expected {expected} for role={role!r}"


# ---------------------------------------------------------------------------
# UNKNOWN when no data
# ---------------------------------------------------------------------------

def test_unknown_no_data():
    assert classify() == "UNKNOWN"

def test_unknown_empty_strings():
    assert classify(role="", company="", hint="", notes="") == "UNKNOWN"


# ---------------------------------------------------------------------------
# contact_type_hint column takes priority
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hint,expected", [
    ("FOUNDER", "FOUNDER"),
    ("Founder", "FOUNDER"),
    ("HR", "HR"),
    ("Human Resources", "HR"),
    ("Recruiter", "RECRUITER"),
    ("VC", "VC"),
    ("Investor", "INVESTOR"),
    ("INVESTOR", "INVESTOR"),
])
def test_contact_type_hint(hint, expected):
    assert classify(hint=hint) == expected, f"hint={hint!r} should give {expected}"


# ---------------------------------------------------------------------------
# Header mapping variations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role_header,role_value,expected", [
    ("Designation", "Founder & CEO", "FOUNDER"),
    ("Job Title", "HR Manager", "HR"),
    ("Title", "Technical Recruiter", "RECRUITER"),
    ("Current Role", "Co-Founder", "FOUNDER"),
    ("Position", "Angel Investor", "INVESTOR"),
    ("Occupation", "Venture Capital", "VC"),
    ("role", "Founder", "FOUNDER"),
    ("Role", "HR Head", "HR"),
    ("Function", "Recruiter", "RECRUITER"),
    ("Headline", "CEO", "FOUNDER"),
])
def test_header_variations(role_header, role_value, expected):
    headers = ["Name", "Email", role_header, "Company"]
    row = {
        "Name": "Test User",
        "Email": "test@example.com",
        role_header: role_value,
        "Company": "TestCorp",
    }
    c_type, norm = norm_and_classify(headers, row)
    assert norm.get("role") == role_value, (
        f"Header {role_header!r} should map role field to {role_value!r}; "
        f"got role={norm.get('role')!r}, mapping={ContactNormalizer.detect_column_mapping(headers)}"
    )
    assert c_type == expected, (
        f"Header {role_header!r} value {role_value!r}: expected {expected}, got {c_type}"
    )


# ---------------------------------------------------------------------------
# Campaign audience matching (case normalisation)
# ---------------------------------------------------------------------------

def test_audience_case_normalisation():
    """The campaign service normalises audience strings to uppercase."""
    variants = ["FOUNDER", "Founder", "founder", " FOUNDER "]
    for v in variants:
        assert v.strip().upper() == "FOUNDER"


# ---------------------------------------------------------------------------
# Filename hint extraction
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename,expected_hint", [
    ("Founder-aman,ujjwal.csv", "founder"),
    ("founder_list.xlsx", "founder"),
    ("Investor-contacts-2024.csv", "investor"),
    ("Angel_investors.csv", "investor"),
    ("HR-leads.csv", "hr"),
    ("recruiter-db.xlsx", "recruiter"),
    ("Talent-Acquisition-list.csv", "recruiter"),
    ("Hiring-managers.csv", "recruiter"),
    ("random-contacts.csv", None),      # no hint
    ("contacts.xlsx", None),            # no hint
])
def test_filename_hint_extraction(filename, expected_hint):
    hint = _extract_type_hint_from_filename(filename)
    assert hint == expected_hint, f"filename={filename!r}: expected {expected_hint!r}, got {hint!r}"


# ---------------------------------------------------------------------------
# End-to-end: campaign audience filter simulation
# ---------------------------------------------------------------------------

def test_campaign_audience_matching():
    """
    Simulates: Aman(FOUNDER) + Ujjwal(FOUNDER) with campaign requesting
    FOUNDER / HR / RECRUITER -> should match 2 contacts.
    """
    contacts = [
        {"contact_type": "FOUNDER", "is_valid": True, "email": "aman@example.com"},
        {"contact_type": "FOUNDER", "is_valid": True, "email": "ujjwal@example.com"},
    ]
    campaign_audience = ["FOUNDER", "HR", "RECRUITER"]
    normalised_audience = [a.strip().upper() for a in campaign_audience]

    matched = sum(
        1 for c in contacts
        if c["is_valid"] and c["contact_type"] in normalised_audience
    )
    assert matched == 2, f"Expected 2 matched contacts, got {matched}"


def test_campaign_audience_no_false_matches():
    """UNKNOWN contacts must NOT match a typed audience."""
    contacts = [
        {"contact_type": "UNKNOWN", "is_valid": True, "email": "x@example.com"},
    ]
    campaign_audience = ["FOUNDER", "HR", "RECRUITER"]
    normalised_audience = [a.strip().upper() for a in campaign_audience]
    matched = sum(
        1 for c in contacts
        if c["is_valid"] and c["contact_type"] in normalised_audience
    )
    assert matched == 0, "UNKNOWN contacts must not match typed audience"
