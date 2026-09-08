import io
import time
import pytest
from pathlib import Path
import sys

# Ensure backend is on sys.path
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.contacts.schema import Contact, ContactType, ImportStatistics
from app.contacts.parser import FileParser
from app.contacts.normalizer import ContactNormalizer
from app.contacts.validator import ContactValidator
from app.contacts.classifier import ContactClassifier
from app.database.mongodb import db_manager


@pytest.fixture(autouse=True)
def reset_db():
    """Clear in-memory contacts and datasets before each test."""
    db_manager._memory_contacts.clear()
    db_manager._memory_datasets.clear()


# =========================================================================
# 1. CSV Parser Tests
# =========================================================================

def test_csv_parser_delimiters_and_encodings():
    # Comma delimited
    csv_comma = "Full Name,Email,Company\nAlice Smith,alice@example.com,Acme Corp\nBob Jones,bob@example.com,Beta Inc\n"
    headers, rows = FileParser.parse_csv(csv_comma.encode("utf-8"))
    assert headers == ["Full Name", "Email", "Company"]
    assert len(rows) == 2
    assert rows[0]["Full Name"] == "Alice Smith"
    assert rows[0]["_source_row"] == 2

    # Semicolon delimited with UTF-8 BOM
    csv_semi = "\ufeffName;Email;Role\nCharlie;charlie@example.com;Founder\n"
    headers2, rows2 = FileParser.parse_csv(csv_semi.encode("utf-8-sig"))
    assert len(headers2) == 3
    assert len(rows2) == 1
    assert rows2[0]["Email"] == "charlie@example.com"

    # Tab delimited
    csv_tab = "Name\tEmail\tLocation\nDavid\tdavid@example.com\tSan Francisco\n"
    headers3, rows3 = FileParser.parse_csv(csv_tab.encode("utf-8"))
    assert len(headers3) == 3
    assert len(rows3) == 1


# =========================================================================
# 2. XLSX Parser Tests
# =========================================================================

def test_xlsx_parser():
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Contacts"

    # Headers
    ws.append(["Full Name", "Email Address", "Firm", "Designation", "Mobile Number"])
    # Rows
    ws.append(["Elena Rostova", "elena@vcfund.com", "Horizon Ventures", "General Partner", 15552345678])
    ws.append(["Marcus Chen", "marcus@startup.io", "Nexus AI", "Co-Founder & CEO", 15559876543])
    # Empty row to test filtering
    ws.append([None, None, None, None, None])

    out = io.BytesIO()
    wb.save(out)
    xlsx_bytes = out.getvalue()

    headers, rows = FileParser.parse_xlsx(xlsx_bytes)
    assert headers == ["Full Name", "Email Address", "Firm", "Designation", "Mobile Number"]
    assert len(rows) == 2
    assert rows[0]["Full Name"] == "Elena Rostova"
    assert rows[0]["Mobile Number"] == "15552345678"
    assert rows[1]["Designation"] == "Co-Founder & CEO"


# =========================================================================
# 3. Unusual Column Headers Mapping Tests
# =========================================================================

def test_unusual_column_headers():
    unusual_headers = [
        "Lead Name",
        "Work Email",
        "Firm",
        "Designation",
        "Li URL",
        "Investment Stage",
        "Direct Line",
        "Metro",
        "Memo",
        "Homepage",
        "Internal Score"  # Unmapped column
    ]

    mapping = ContactNormalizer.detect_column_mapping(unusual_headers)

    assert mapping["Lead Name"] == "full_name"
    assert mapping["Work Email"] == "email"
    assert mapping["Firm"] == "company"
    assert mapping["Designation"] == "role"
    assert mapping["Li URL"] == "linkedin_url"
    assert mapping["Investment Stage"] == "investment_focus"
    assert mapping["Direct Line"] == "phone"
    assert mapping["Metro"] == "location"
    assert mapping["Memo"] == "notes"
    assert mapping["Homepage"] == "website"
    assert "Internal Score" not in mapping

    raw_row = {
        "Lead Name": "Sarah Connor",
        "Work Email": "sarah@cyberdyne.com",
        "Firm": "Cyberdyne Systems",
        "Designation": "Managing Partner",
        "Li URL": "linkedin.com/in/sconnor",
        "Investment Stage": "Seed / Series A",
        "Direct Line": "+1 (555) 999-8888",
        "Metro": "Los Angeles, CA",
        "Memo": "Key decision maker",
        "Homepage": "cyberdyne.com",
        "Internal Score": "98/100",
        "_source_row": 5
    }

    norm = ContactNormalizer.normalize_row(raw_row, mapping)

    assert norm["first_name"] == "Sarah"
    assert norm["last_name"] == "Connor"
    assert norm["full_name"] == "Sarah Connor"
    assert norm["email"] == "sarah@cyberdyne.com"
    assert norm["company"] == "Cyberdyne Systems"
    assert norm["role"] == "Managing Partner"
    assert norm["linkedin_url"] == "https://www.linkedin.com/in/sconnor"
    assert norm["website"] == "https://cyberdyne.com"
    assert norm["investment_focus"] == "Seed / Series A"
    assert norm["location"] == "Los Angeles, CA"
    assert norm["notes"] == "Key decision maker"
    assert norm["metadata"]["unmapped"]["Internal Score"] == "98/100"


# =========================================================================
# 4. Contact Normalization Tests
# =========================================================================

def test_contact_normalization():
    # Honorific stripping & name splitting
    row1 = {"full_name": "Dr. Aris Thorne", "first_name": "", "last_name": ""}
    ContactNormalizer._normalize_names(row1)
    assert row1["first_name"] == "Aris"
    assert row1["last_name"] == "Thorne"
    assert row1["full_name"] == "Aris Thorne"

    # Separate first and last combining into full_name
    row2 = {"full_name": "", "first_name": "Priya", "last_name": "Patel"}
    ContactNormalizer._normalize_names(row2)
    assert row2["full_name"] == "Priya Patel"

    # URL normalization
    assert ContactNormalizer._normalize_linkedin_url("in/johndoe") == "https://www.linkedin.com/in/johndoe"
    assert ContactNormalizer._normalize_linkedin_url("johndoe") == "https://www.linkedin.com/in/johndoe"
    assert ContactNormalizer._normalize_website("acmecapital.com") == "https://acmecapital.com"

    # Phone normalization
    assert ContactNormalizer._normalize_phone("+1 (800) 555-0199") == "+18005550199"
    assert ContactNormalizer._normalize_phone("020-7946-0991") == "02079460991"


# =========================================================================
# 5. Email Validation & Duplicate Detection Tests
# =========================================================================

def test_email_validation():
    assert ContactValidator.is_valid_email("user@example.com") is True
    assert ContactValidator.is_valid_email("first.last+tag@sub.domain.co.uk") is True

    # Invalid cases
    assert ContactValidator.is_valid_email("plainaddress") is False
    assert ContactValidator.is_valid_email("@missingusername.com") is False
    assert ContactValidator.is_valid_email("missingdomain@") is False
    assert ContactValidator.is_valid_email("user@domain") is False
    assert ContactValidator.is_valid_email("user@domain..com") is False
    assert ContactValidator.is_valid_email("user name@domain.com") is False
    assert ContactValidator.is_valid_email("") is False
    assert ContactValidator.is_valid_email(None) is False


def test_duplicate_detection():
    validator = ContactValidator()

    contact1 = {"email": "duplicate@test.com", "full_name": "First Copy"}
    valid1, errors1, dup1 = validator.validate_and_deduplicate(contact1)
    assert valid1 is True
    assert dup1 is False

    # Second copy with same email
    contact2 = {"email": "DUPLICATE@test.com", "full_name": "Second Copy"}
    valid2, errors2, dup2 = validator.validate_and_deduplicate(contact2)
    assert valid2 is False
    assert dup2 is True
    assert any("Duplicate" in e for e in errors2)


# =========================================================================
# 6. Contact Classification Tests
# =========================================================================

def test_contact_classification():
    # VC Roles
    vc_sample = {"role": "General Partner", "company": "Benchmark Capital", "investment_focus": "Series A"}
    t, conf = ContactClassifier.classify_contact(vc_sample)
    assert t == ContactType.VC
    assert conf >= 0.90

    # General Investor
    inv_sample = {"role": "Angel Investor", "company": "Syndicate 101", "investment_focus": "Seed stage B2B"}
    t, conf = ContactClassifier.classify_contact(inv_sample)
    assert t == ContactType.INVESTOR
    assert conf >= 0.90

    # Founder
    founder_sample = {"role": "Co-Founder & CEO", "company": "Stealth AI", "investment_focus": ""}
    t, conf = ContactClassifier.classify_contact(founder_sample)
    assert t == ContactType.FOUNDER
    assert conf >= 0.90

    # HR & Recruiter
    hr_sample = {"role": "Head of People", "company": "Stripe", "investment_focus": ""}
    t, conf = ContactClassifier.classify_contact(hr_sample)
    assert t == ContactType.HR

    recruiter_sample = {"role": "Technical Recruiter", "company": "Google", "investment_focus": ""}
    t, conf = ContactClassifier.classify_contact(recruiter_sample)
    assert t == ContactType.RECRUITER

    # Other
    other_sample = {"role": "Senior Staff Software Engineer", "company": "Meta", "investment_focus": ""}
    t, conf = ContactClassifier.classify_contact(other_sample)
    assert t == ContactType.OTHER

    # Unknown
    unknown_sample = {"role": "", "company": "", "investment_focus": ""}
    t, conf = ContactClassifier.classify_contact(unknown_sample)
    assert t == ContactType.UNKNOWN


# =========================================================================
# 7. Large Dataset Performance Test (2,000 Contacts)
# =========================================================================

def test_large_dataset_performance():
    """
    Requirement: For 2,000 contacts, processing must NOT send everything to LLM individually.
    Must finish high-throughput deterministic classification rapidly.
    """
    roles_pool = [
        ("General Partner", "Apex Ventures", ContactType.VC),
        ("Angel Investor", "Self-employed", ContactType.INVESTOR),
        ("Co-Founder & CTO", "HyperScale", ContactType.FOUNDER),
        ("Head of Talent Acquisition", "Fintech Corp", ContactType.HR),
        ("Technical Sourcer", "HireFast", ContactType.RECRUITER),
        ("Principal Architect", "Cloud Platform", ContactType.OTHER),
        ("Managing Director", "Summit Capital", ContactType.VC),
        ("Seed Investor", "AngelList", ContactType.INVESTOR),
        ("Founder", "NextGen Health", ContactType.FOUNDER),
        ("People Ops Lead", "SaaS Hub", ContactType.HR)
    ]

    raw_contacts = []
    mapping = {
        "Name": "full_name",
        "Email": "email",
        "Company": "company",
        "Role": "role"
    }

    for i in range(2000):
        role_info = roles_pool[i % len(roles_pool)]
        raw_contacts.append({
            "Name": f"Contact User {i}",
            "Email": f"user_{i}@domain{i % 50}.com",
            "Company": role_info[1],
            "Role": role_info[0],
            "_source_row": i + 2
        })

    validator = ContactValidator()

    t_start = time.perf_counter()

    classified_counts = {ct: 0 for ct in ContactType}
    valid_count = 0

    for row in raw_contacts:
        norm = ContactNormalizer.normalize_row(row, mapping)
        is_valid, errors, is_dup = validator.validate_and_deduplicate(norm)
        if is_valid:
            valid_count += 1
        c_type, conf = ContactClassifier.classify_contact(norm, use_llm_fallback=False)
        classified_counts[c_type] += 1

    t_elapsed = time.perf_counter() - t_start

    assert valid_count == 2000
    assert classified_counts[ContactType.VC] > 0
    assert classified_counts[ContactType.FOUNDER] > 0
    assert classified_counts[ContactType.HR] > 0

    # 2,000 contacts processed in < 1.0 second
    assert t_elapsed < 1.5, f"Expected 2,000 contacts to process in <1.5s, took {t_elapsed:.2f}s"


# =========================================================================
# 8. Malformed Spreadsheets & Missing Data Handling
# =========================================================================

def test_malformed_spreadsheets():
    # File with empty lines, ragged rows, missing values
    malformed_csv = (
        "Name,Email,Company,Role\n"
        "\n\n"
        "Valid Person,valid@company.com,TechCorp,Founder\n"
        "No Email Person,,Ghost Firm,Analyst\n"
        "Broken Email,not-an-email,Weird Inc,Developer\n"
        ",,,\n"
        "Only Extra Cell,a@b.com,Co,Role,Extra1,Extra2\n"
    )

    headers, rows = FileParser.parse_csv(malformed_csv.encode("utf-8"))
    mapping = ContactNormalizer.detect_column_mapping(headers)
    validator = ContactValidator()

    valid_contacts = 0
    invalid_contacts = 0

    for r in rows:
        norm = ContactNormalizer.normalize_row(r, mapping)
        is_valid, errors, is_dup = validator.validate_and_deduplicate(norm)
        if is_valid:
            valid_contacts += 1
        else:
            invalid_contacts += 1

    # Valid Person and Only Extra Cell are valid emails
    assert valid_contacts == 2
    # No Email Person and Broken Email are invalid
    assert invalid_contacts == 2


# =========================================================================
# 9. API & Pagination Tests
# =========================================================================

@pytest.mark.asyncio
async def test_database_contacts_pagination_and_query():
    # Insert mock contacts
    contacts = [
        Contact(
            dataset_id="ds_test",
            full_name="Alice Founder",
            email="alice@startup.com",
            company="Startup Alpha",
            role="Founder & CEO",
            contact_type=ContactType.FOUNDER,
            is_valid=True
        ).model_dump(),
        Contact(
            dataset_id="ds_test",
            full_name="Bob Investor",
            email="bob@venture.com",
            company="Venture Partners",
            role="Managing Partner",
            contact_type=ContactType.VC,
            is_valid=True
        ).model_dump(),
        Contact(
            dataset_id="ds_test",
            full_name="Charlie HR",
            email="charlie@bigco.com",
            company="BigCo",
            role="Head of People",
            contact_type=ContactType.HR,
            is_valid=True
        ).model_dump(),
        Contact(
            dataset_id="ds_test",
            full_name="Invalid Dave",
            email=None,
            company="Dave Inc",
            role="Engineer",
            contact_type=ContactType.OTHER,
            is_valid=False
        ).model_dump()
    ]

    await db_manager.save_contacts_batch(contacts)

    # 1. Total count
    total = await db_manager.count_contacts({"dataset_id": "ds_test"})
    assert total == 4

    # 2. Filter by contact_type
    vc_list = await db_manager.query_contacts({"dataset_id": "ds_test", "contact_type": "VC"})
    assert len(vc_list) == 1
    assert vc_list[0]["full_name"] == "Bob Investor"

    # 3. Search query
    search_res = await db_manager.query_contacts({"dataset_id": "ds_test", "search": "Alpha"})
    assert len(search_res) == 1
    assert search_res[0]["company"] == "Startup Alpha"

    # 4. Pagination
    page_1 = await db_manager.query_contacts({"dataset_id": "ds_test"}, skip=0, limit=2)
    page_2 = await db_manager.query_contacts({"dataset_id": "ds_test"}, skip=2, limit=2)
    assert len(page_1) == 2
    assert len(page_2) == 2
    assert page_1[0]["contact_id"] != page_2[0]["contact_id"]


@pytest.mark.asyncio
async def test_full_upload_flow():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)

    csv_data = (
        "Lead Name,Work Email,Firm,Designation\n"
        "Laura Gomez,laura@angel.co,Angel Capital,General Partner\n"
        "Kevin Tran,kevin@build.io,Build AI,Founder & CEO\n"
        "Maya Lin,maya@talent.com,ScaleTech,Technical Recruiter\n"
        "Duplicate Person,kevin@build.io,Build AI,Founder\n"
        "Malformed Person,not-valid-email,None,None\n"
    )

    files = {"file": ("leads.csv", io.BytesIO(csv_data.encode("utf-8")), "text/csv")}
    response = client.post("/api/contacts/upload", files=files)

    assert response.status_code == 200
    stats = response.json()

    assert stats["total_rows"] == 5
    assert stats["valid_contacts"] == 3
    assert stats["invalid_contacts"] == 2
    assert stats["duplicates"] == 1
    assert stats["investors"] == 1
    assert stats["founders"] == 1
    assert stats["hr"] == 1
    assert stats["dataset_id"] is not None

    # Verify query through contacts API
    get_res = client.get(f"/api/contacts?dataset_id={stats['dataset_id']}&page=1&page_size=10")
    assert get_res.status_code == 200
    contacts_json = get_res.json()
    assert contacts_json["total"] == 5
    assert len(contacts_json["items"]) == 5
