"""
Contacts API routes.

Endpoints:
  POST /contacts/upload               - Upload and import a CSV/XLSX dataset
  POST /contacts/datasets/{id}/reclassify  - Re-run classifier on existing dataset
  GET  /contacts                      - Paginated contact list with filtering
  GET  /contacts/{contact_id}         - Single contact
  GET  /contacts/datasets/list        - Paginated dataset list
  GET  /contacts/datasets/{id}        - Single dataset
  GET  /contacts/datasets/{id}/preview - Classification preview / summary
"""

import math
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, UploadFile, File, Query, HTTPException

from app.contacts.schema import (
    Contact,
    ContactType,
    ImportStatistics,
    DatasetRecord,
    PaginatedContactsResponse,
)
from app.contacts.parser import FileParser
from app.contacts.normalizer import ContactNormalizer
from app.contacts.validator import ContactValidator
from app.contacts.classifier import ContactClassifier
from app.database.mongodb import db_manager

logger = logging.getLogger(__name__)

router = APIRouter()


def _accumulate_type_stats(stats: ImportStatistics, c_type: ContactType) -> None:
    """Increment the correct bucket in ImportStatistics for a contact type."""
    if c_type in (ContactType.INVESTOR, ContactType.VC):
        stats.investors += 1
    elif c_type == ContactType.FOUNDER:
        stats.founders += 1
    elif c_type == ContactType.HR:
        stats.hr += 1
    elif c_type == ContactType.RECRUITER:
        stats.recruiters += 1
        stats.hr += 1
    elif c_type == ContactType.OTHER:
        stats.other += 1
    else:
        stats.unknown += 1


@router.post("/contacts/upload", response_model=ImportStatistics)
async def upload_contacts_file(file: UploadFile = File(...)):
    """
    Uploads and processes a CSV or XLSX file containing contacts.

    Performs automatic header mapping, value normalisation, email validation,
    in-batch deduplication, deterministic role classification (with optional
    LLM fallback), and MongoDB persistence.

    Every uploaded column is preserved – unmapped columns are stored in the
    contact's `metadata.unmapped` dictionary so no information is lost.
    """
    filename = file.filename or "uploaded_file.csv"
    logger.info("Receiving contact upload: '%s'", filename)

    try:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        # 1. Parse file
        headers, raw_rows = FileParser.parse_file(content, filename)
        if not raw_rows:
            return ImportStatistics(
                total_rows=0,
                valid_contacts=0,
                invalid_contacts=0,
                duplicates=0,
                filename=filename,
            )

        # 2. Automatic column mapping
        column_mapping = ContactNormalizer.detect_column_mapping(headers)
        logger.info(
            "Column mapping for '%s': %s (unmapped headers: %s)",
            filename,
            column_mapping,
            [h for h in headers if h not in column_mapping],
        )

        # 3. Create dataset record
        dataset = DatasetRecord(
            filename=filename,
            file_type="xlsx" if filename.lower().endswith((".xlsx", ".xlsm")) else "csv",
            file_size_bytes=len(content),
            column_mapping=column_mapping,
        )

        validator = ContactValidator()
        contacts: List[Contact] = []
        stats = ImportStatistics(dataset_id=dataset.dataset_id, filename=filename)

        # 4. Normalise → validate → classify
        for row in raw_rows:
            stats.total_rows += 1
            norm_data = ContactNormalizer.normalize_row(row, column_mapping)

            # Validation & deduplication
            is_valid, errors, is_duplicate = validator.validate_and_deduplicate(norm_data)
            if is_duplicate:
                stats.duplicates += 1

            if is_valid:
                stats.valid_contacts += 1
            else:
                stats.invalid_contacts += 1

            # Classification
            c_type, confidence = ContactClassifier.classify_contact(norm_data)

            # Log when role was successfully extracted (debugging aid)
            if norm_data.get("role"):
                logger.debug(
                    "Row %s: role=%r -> type=%s (conf=%.2f)",
                    norm_data.get("source_row"),
                    norm_data.get("role"),
                    c_type.value,
                    confidence,
                )
            elif norm_data.get("contact_type_hint"):
                logger.debug(
                    "Row %s: hint=%r -> type=%s (conf=%.2f)",
                    norm_data.get("source_row"),
                    norm_data.get("contact_type_hint"),
                    c_type.value,
                    confidence,
                )

            # Accumulate stats for valid, non-duplicate contacts
            if is_valid and not is_duplicate:
                _accumulate_type_stats(stats, c_type)

            contact = Contact(
                dataset_id=dataset.dataset_id,
                first_name=norm_data.get("first_name"),
                last_name=norm_data.get("last_name"),
                full_name=norm_data.get("full_name"),
                email=norm_data.get("email"),
                phone=norm_data.get("phone"),
                company=norm_data.get("company"),
                role=norm_data.get("role"),
                linkedin_url=norm_data.get("linkedin_url"),
                website=norm_data.get("website"),
                location=norm_data.get("location"),
                contact_type=c_type,
                contact_type_hint=norm_data.get("contact_type_hint"),
                investment_focus=norm_data.get("investment_focus"),
                notes=norm_data.get("notes"),
                source_row=norm_data.get("source_row", 0),
                metadata=norm_data.get("metadata", {}),
                is_valid=is_valid,
                validation_errors=errors,
            )
            contacts.append(contact)

        # 5. Persist dataset and contacts
        dataset.statistics = stats
        await db_manager.save_dataset(dataset.model_dump())
        await db_manager.save_contacts_batch([c.model_dump() for c in contacts])

        logger.info(
            "Processed dataset '%s': %d/%d valid  "
            "investors=%d founders=%d hr=%d recruiters=%d other=%d unknown=%d  "
            "duplicates=%d",
            dataset.dataset_id,
            stats.valid_contacts,
            stats.total_rows,
            stats.investors,
            stats.founders,
            stats.hr,
            stats.recruiters,
            stats.other,
            stats.unknown,
            stats.duplicates,
        )

        return stats

    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Error processing contacts upload: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to process file: {exc}")


@router.post("/contacts/datasets/{dataset_id}/reclassify", response_model=ImportStatistics)
async def reclassify_dataset(dataset_id: str):
    """
    Re-runs the contact classifier on every contact in an existing dataset
    and persists the updated contact_type values.

    Use this after upgrading the classifier or when contacts were imported
    before a classification bug was fixed.  Deduplication is skipped (the
    contacts are already stored).

    If the dataset filename contains a recognisable type keyword (e.g.
    "Founder-list.csv", "Investor-contacts.xlsx", "HR-leads.csv") that
    keyword is used as a last-resort classification hint for contacts that
    have no role or notes field — so a dataset with only Name/Email/Company
    columns can still be classified when the filename carries the intent.
    """
    dataset_record = await db_manager.get_dataset(dataset_id)
    if not dataset_record:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found.")

    # Derive a filename-level type hint (generic — based on keywords in filename)
    filename = dataset_record.get("filename", "")
    filename_hint = _extract_type_hint_from_filename(filename)
    if filename_hint:
        logger.info(
            "Dataset '%s' filename '%s' provides classification hint: %s",
            dataset_id, filename, filename_hint,
        )

    # Load all contacts for this dataset
    total = await db_manager.count_contacts({"dataset_id": dataset_id})
    contacts_raw = await db_manager.query_contacts(
        {"dataset_id": dataset_id}, skip=0, limit=max(total, 1)
    )

    if not contacts_raw:
        raise HTTPException(
            status_code=404,
            detail=f"No contacts found for dataset '{dataset_id}'.",
        )

    stats = ImportStatistics(
        dataset_id=dataset_id,
        filename=filename,
        total_rows=len(contacts_raw),
    )
    updated_contacts = []

    for c_doc in contacts_raw:
        # Build classification input from stored fields
        classify_input = {
            "role": c_doc.get("role") or "",
            "company": c_doc.get("company") or "",
            "investment_focus": c_doc.get("investment_focus") or "",
            "notes": c_doc.get("notes") or "",
            "contact_type_hint": (
                c_doc.get("contact_type_hint")
                or (c_doc.get("metadata") or {}).get("contact_type_hint")
                or ""
            ),
        }

        new_type, confidence = ContactClassifier.classify_contact(classify_input)

        # If the primary classifier returns UNKNOWN and we have a filename hint,
        # apply it as a fallback.  This is generic: ANY dataset named
        # "Founder-*.csv" or "Investor-contacts.csv" etc. gets this treatment.
        if new_type.value == "UNKNOWN" and filename_hint:
            from app.contacts.classifier import _classify_from_hint_str
            hinted = _classify_from_hint_str(filename_hint)
            if hinted is not None:
                new_type = hinted
                confidence = 0.6
                c_doc.setdefault("metadata", {})
                c_doc["metadata"]["classification_method"] = "filename_hint"
                c_doc["metadata"]["classification_reason"] = (
                    f"No role/notes data; dataset filename '{filename}' "
                    f"contains keyword '{filename_hint}'"
                )

        old_type = c_doc.get("contact_type", "UNKNOWN")
        c_doc["contact_type"] = new_type.value
        c_doc["updated_at"] = datetime.now(timezone.utc).isoformat()

        if old_type != new_type.value:
            stats.reclassified += 1

        is_valid = c_doc.get("is_valid", True)
        if is_valid:
            stats.valid_contacts += 1
            _accumulate_type_stats(stats, new_type)
        else:
            stats.invalid_contacts += 1

        updated_contacts.append(c_doc)

    # Persist reclassified contacts
    await db_manager.save_contacts_batch(updated_contacts)

    # Update dataset statistics
    dataset_record["statistics"] = stats.model_dump()
    await db_manager.save_dataset(dataset_record)

    logger.info(
        "Reclassified dataset '%s': %d contacts updated  "
        "founders=%d hr=%d recruiters=%d investors=%d other=%d unknown=%d",
        dataset_id,
        stats.reclassified,
        stats.founders,
        stats.hr,
        stats.recruiters,
        stats.investors,
        stats.other,
        stats.unknown,
    )

    return stats


def _extract_type_hint_from_filename(filename: str) -> Optional[str]:
    """
    Extracts a type keyword from a dataset filename.

    Generic heuristic: if the filename contains a recognisable classification
    keyword (founder, investor, hr, recruiter, vc, etc.) we return it so the
    reclassifier can use it as a last-resort hint.

    Examples:
      "Founder-aman,ujjwal.csv"  -> "founder"
      "Investor-list.xlsx"       -> "investor"
      "HR-contacts-2024.csv"     -> "hr"
    """
    import re as _re
    name_lower = filename.lower()
    # Remove file extension and common separators
    name_clean = _re.sub(r"\.(csv|xlsx|xlsm|xls|tsv)$", "", name_lower)
    name_clean = _re.sub(r"[-_,.\s]+", " ", name_clean)

    keywords = [
        ("founder", "founder"),
        ("co founder", "founder"),
        ("cofounder", "founder"),
        ("ceo", "founder"),
        ("investor", "investor"),
        ("angel", "investor"),
        (" vc ", "vc"),
        ("venture", "vc"),
        (" hr ", "hr"),
        ("human resource", "hr"),
        ("people ops", "hr"),
        ("recruiter", "recruiter"),
        ("recruitment", "recruiter"),
        ("talent", "recruiter"),
        ("hiring", "recruiter"),
    ]

    for kw, hint in keywords:
        if kw.strip() in name_clean:
            return hint

    return None




@router.get("/contacts/datasets/{dataset_id}/preview")
async def dataset_classification_preview(dataset_id: str):
    """
    Returns a structured classification summary for the dataset.

    Shows how many contacts fell into each type so import problems are
    immediately visible before campaign planning.
    """
    dataset_record = await db_manager.get_dataset(dataset_id)
    if not dataset_record:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found.")

    total = await db_manager.count_contacts({"dataset_id": dataset_id})
    breakdown: Dict[str, int] = {}
    for ct in ContactType:
        count = await db_manager.count_contacts(
            {"dataset_id": dataset_id, "contact_type": ct.value}
        )
        breakdown[ct.value] = count

    valid = await db_manager.count_contacts({"dataset_id": dataset_id, "is_valid": True})
    invalid = await db_manager.count_contacts({"dataset_id": dataset_id, "is_valid": False})

    return {
        "dataset_id": dataset_id,
        "filename": dataset_record.get("filename"),
        "total_contacts": total,
        "valid_contacts": valid,
        "invalid_contacts": invalid,
        "classification_breakdown": breakdown,
        "column_mapping": dataset_record.get("column_mapping", {}),
        "uploaded_at": dataset_record.get("uploaded_at"),
    }


@router.get("/contacts", response_model=PaginatedContactsResponse)
async def list_contacts(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
    dataset_id: Optional[str] = Query(None, description="Filter by dataset ID"),
    contact_type: Optional[ContactType] = Query(None, description="Filter by contact type"),
    search: Optional[str] = Query(None, description="Search across name, email, company, role"),
    is_valid: Optional[bool] = Query(None, description="Filter valid / invalid contacts"),
):
    """Returns paginated contacts with optional filtering and search."""
    filter_q: Dict[str, Any] = {}
    if dataset_id:
        filter_q["dataset_id"] = dataset_id
    if contact_type:
        filter_q["contact_type"] = contact_type.value
    if is_valid is not None:
        filter_q["is_valid"] = is_valid
    if search:
        filter_q["search"] = search

    skip = (page - 1) * page_size
    total = await db_manager.count_contacts(filter_q)
    results = await db_manager.query_contacts(
        filter_query=filter_q,
        skip=skip,
        limit=page_size,
    )

    items = [Contact(**r) for r in results]
    total_pages = math.ceil(total / page_size) if total > 0 else 1

    return PaginatedContactsResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/contacts/{contact_id}", response_model=Contact)
async def get_contact_by_id(contact_id: str):
    """Retrieves a single contact record by ID."""
    contact_dict = await db_manager.get_contact(contact_id)
    if not contact_dict:
        raise HTTPException(status_code=404, detail=f"Contact '{contact_id}' not found.")
    return Contact(**contact_dict)


@router.get("/contacts/datasets/list")
async def list_datasets(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """Returns paginated list of uploaded datasets with statistics."""
    skip = (page - 1) * page_size
    total = await db_manager.count_datasets()
    datasets = await db_manager.list_datasets(skip=skip, limit=page_size)
    return {
        "datasets": datasets,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": math.ceil(total / page_size) if total > 0 else 1,
    }


@router.get("/contacts/datasets/{dataset_id}")
async def get_dataset_by_id(dataset_id: str):
    """Retrieves dataset details and summary statistics."""
    dataset_dict = await db_manager.get_dataset(dataset_id)
    if not dataset_dict:
        raise HTTPException(status_code=404, detail=f"Dataset '{dataset_id}' not found.")
    return dataset_dict
