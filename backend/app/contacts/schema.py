from datetime import datetime, timezone
from enum import Enum
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
import uuid

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class ContactType(str, Enum):
    INVESTOR = "INVESTOR"
    VC = "VC"
    FOUNDER = "FOUNDER"
    HR = "HR"
    RECRUITER = "RECRUITER"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"

class Contact(BaseModel):
    contact_id: str = Field(default_factory=lambda: f"cnt_{uuid.uuid4().hex[:8]}")
    dataset_id: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    full_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    role: Optional[str] = None
    linkedin_url: Optional[str] = None
    website: Optional[str] = None
    location: Optional[str] = None
    contact_type: ContactType = ContactType.UNKNOWN
    contact_type_hint: Optional[str] = None
    investment_focus: Optional[str] = None
    notes: Optional[str] = None
    source_row: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)
    is_valid: bool = True
    validation_errors: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

class ImportStatistics(BaseModel):
    total_rows: int = 0
    valid_contacts: int = 0
    invalid_contacts: int = 0
    duplicates: int = 0
    investors: int = 0
    founders: int = 0
    hr: int = 0
    recruiters: int = 0
    other: int = 0
    unknown: int = 0
    dataset_id: Optional[str] = None
    filename: Optional[str] = None
    reclassified: int = 0

class DatasetRecord(BaseModel):
    dataset_id: str = Field(default_factory=lambda: f"ds_{uuid.uuid4().hex[:8]}")
    filename: str
    file_type: str = "csv"
    file_size_bytes: int = 0
    column_mapping: Dict[str, str] = Field(default_factory=dict)
    statistics: ImportStatistics = Field(default_factory=ImportStatistics)
    uploaded_at: datetime = Field(default_factory=utc_now)

class PaginatedContactsResponse(BaseModel):
    items: List[Contact]
    total: int
    page: int
    page_size: int
    total_pages: int
