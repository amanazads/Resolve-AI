import re
import logging
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical Header Aliases
# ---------------------------------------------------------------------------
# Rules:
#  - All aliases are lowercase (compared against _clean_header_name output)
#  - Aliases ordered most-specific first to avoid false substring hits
#  - "contact_type_hint" preserves any pre-classified "Type"/"Category" column
# ---------------------------------------------------------------------------

CANONICAL_HEADER_MAP: Dict[str, List[str]] = {
    "email": [
        "email", "email address", "email_address", "e-mail",
        "work email", "business email", "contact email",
        "primary email", "corporate email", "user email", "mail",
        "e mail",
    ],
    "full_name": [
        "name", "full name", "full_name", "fullname",
        "contact name", "person", "lead name", "lead",
        "investor name", "candidate",
        # "founder name", "founder", "contact" removed – caused collisions
    ],
    "first_name": [
        "first name", "first_name", "firstname", "first", "given name", "fname",
    ],
    "last_name": [
        "last name", "last_name", "lastname", "last", "surname",
        "family name", "lname",
    ],
    "company": [
        "company", "company name", "firm", "organization", "organisation",
        "venture firm", "startup", "employer", "business", "corp", "corporation",
    ],
    "role": [
        "role", "designation", "title", "job title", "job_title",
        "position", "occupation", "headline", "current role", "function",
        "current position", "work title", "professional title",
        "job function", "current title",
    ],
    # A column literally called "Type", "Category", "Contact Type" etc.
    # carries the classification already decided by the spreadsheet author.
    # We preserve it so the classifier can use it as the highest-priority signal.
    "contact_type_hint": [
        "contact type", "contact_type", "type", "category",
        "contact category", "classification", "segment", "persona",
        "audience type", "lead type", "person type",
    ],
    "phone": [
        "phone", "phone number", "phone_number", "telephone", "mobile",
        "cell", "tel", "contact number", "work phone", "direct line",
        "contact no",
    ],
    "linkedin_url": [
        "linkedin", "linkedin url", "linkedin_url", "linkedin profile",
        "linkedin link", "li url", "li link", "li",
        "profile url", "linkedin profile url",
        # "profile" alone removed – too ambiguous
    ],
    "website": [
        "website", "company website", "domain", "site", "homepage",
        "company url", "web",
    ],
    "location": [
        "location", "city", "country", "region", "address", "state",
        "geography", "metro", "hq location", "office",
    ],
    "investment_focus": [
        "investment focus", "investment_focus", "focus", "sectors",
        "investment stage", "thesis", "sector focus", "check size",
        "fund focus", "preferred stage", "industry",
    ],
    "notes": [
        "notes", "note", "comments", "description", "bio", "summary",
        "about", "context", "remarks", "memo", "background",
        "additional info", "other info",
    ],
}

# Minimum alias length required for substring matching (prevents "work" -> email)
_MIN_ALIAS_SUBSTR_LEN = 6


class ContactNormalizer:
    """
    Maps heterogeneous spreadsheet headers to canonical contact fields and
    normalises all attribute values.

    Matching strategy (in priority order):
      1. Exact alias match (fastest, most reliable)
      2. Exact canonical field name match
      3. Controlled substring match (only for aliases >= _MIN_ALIAS_SUBSTR_LEN chars)
    """

    @classmethod
    def detect_column_mapping(cls, raw_headers: List[str]) -> Dict[str, str]:
        """
        Maps raw spreadsheet header names to canonical contact field names.
        Returns: { raw_header: canonical_field_name }
        """
        mapping: Dict[str, str] = {}
        assigned_canonical: set = set()

        for header in raw_headers:
            clean_hdr = cls._clean_header_name(header)
            if not clean_hdr:
                continue

            matched_canonical: Optional[str] = None

            # Pass 1 – exact alias match
            for canonical, aliases in CANONICAL_HEADER_MAP.items():
                if canonical in assigned_canonical:
                    continue
                if clean_hdr in aliases:
                    matched_canonical = canonical
                    break

            # Pass 2 – exact canonical field name match
            if not matched_canonical:
                if clean_hdr in CANONICAL_HEADER_MAP and clean_hdr not in assigned_canonical:
                    matched_canonical = clean_hdr

            # Pass 3 – controlled substring match (long aliases only)
            if not matched_canonical:
                for canonical, aliases in CANONICAL_HEADER_MAP.items():
                    if canonical in assigned_canonical:
                        continue
                    for alias in aliases:
                        if len(alias) < _MIN_ALIAS_SUBSTR_LEN:
                            continue
                        if alias == clean_hdr:
                            continue  # already handled in pass 1
                        if alias in clean_hdr or clean_hdr in alias:
                            matched_canonical = canonical
                            break
                    if matched_canonical:
                        break

            if matched_canonical:
                mapping[header] = matched_canonical
                assigned_canonical.add(matched_canonical)

        logger.info(
            "Detected column mapping for %d/%d headers: %s",
            len(mapping), len(raw_headers), mapping,
        )
        return mapping

    @staticmethod
    def _clean_header_name(header: str) -> str:
        """Lowercases, collapses punctuation/underscores to single space, trims."""
        h = header.lower().strip()
        h = re.sub(r"[_\-\.:\\/]+", " ", h)
        h = re.sub(r"\s+", " ", h).strip()
        return h

    @classmethod
    def normalize_row(
        cls, raw_row: Dict[str, Any], column_mapping: Dict[str, str]
    ) -> Dict[str, Any]:
        """
        Applies the column mapping, normalises values, and preserves every
        unmapped column in metadata so no information is lost.
        """
        normalized: Dict[str, Any] = {
            "first_name": None,
            "last_name": None,
            "full_name": None,
            "email": None,
            "phone": None,
            "company": None,
            "role": None,
            "linkedin_url": None,
            "website": None,
            "location": None,
            "investment_focus": None,
            "notes": None,
            "contact_type_hint": None,
            "source_row": raw_row.get("_source_row", 0),
            "metadata": {},
        }

        unmapped: Dict[str, str] = {}
        for raw_hdr, val in raw_row.items():
            if raw_hdr == "_source_row":
                continue

            clean_val = str(val).strip() if val is not None else ""
            canonical_field = column_mapping.get(raw_hdr)

            if canonical_field and canonical_field in normalized:
                # Don't overwrite a populated field with an empty value
                if not normalized[canonical_field] or clean_val:
                    normalized[canonical_field] = clean_val or None
            else:
                if clean_val:
                    unmapped[raw_hdr] = clean_val

        # Always preserve unmapped columns
        if unmapped:
            normalized["metadata"]["unmapped"] = unmapped

            # -----------------------------------------------------------
            # Heuristic fallback: recover role from unmapped columns.
            # This handles CSVs where the role header had a spelling we
            # didn't recognise (e.g. "Profession", "What You Do").
            # -----------------------------------------------------------
            if not normalized["role"]:
                recovered_role = cls._extract_role_from_unmapped(unmapped)
                if recovered_role:
                    normalized["role"] = recovered_role
                    normalized["metadata"]["role_source"] = "unmapped_column"

            # Similarly recover contact_type_hint
            if not normalized["contact_type_hint"]:
                hint = cls._extract_type_hint_from_unmapped(unmapped)
                if hint:
                    normalized["contact_type_hint"] = hint

        # Normalise individual fields
        if normalized["email"]:
            normalized["email"] = normalized["email"].strip().lower()

        cls._normalize_names(normalized)

        if normalized["phone"]:
            normalized["phone"] = cls._normalize_phone(normalized["phone"])

        if normalized["linkedin_url"]:
            normalized["linkedin_url"] = cls._normalize_linkedin_url(
                normalized["linkedin_url"]
            )

        if normalized["website"]:
            normalized["website"] = cls._normalize_website(normalized["website"])

        return normalized

    # ------------------------------------------------------------------
    # Unmapped-column fallback helpers
    # ------------------------------------------------------------------

    _ROLE_HEADER_KEYWORDS = frozenset({
        "role", "designation", "title", "position", "occupation",
        "headline", "function", "profession", "job", "work",
        "current role", "current position", "what you do",
    })

    _TYPE_HINT_HEADER_KEYWORDS = frozenset({
        "type", "category", "classification", "segment", "persona",
        "audience", "contact type", "lead type", "person type",
        "profile type",
    })

    @classmethod
    def _extract_role_from_unmapped(cls, unmapped: Dict[str, str]) -> Optional[str]:
        """
        Checks unmapped column headers for role-like keywords and returns the
        first matching value.
        """
        for hdr, val in unmapped.items():
            clean_hdr = cls._clean_header_name(hdr)
            for kw in cls._ROLE_HEADER_KEYWORDS:
                if kw == clean_hdr or (len(kw) >= 4 and kw in clean_hdr):
                    return val
        return None

    @classmethod
    def _extract_type_hint_from_unmapped(cls, unmapped: Dict[str, str]) -> Optional[str]:
        """
        Checks unmapped column headers for contact-type hint keywords.
        """
        for hdr, val in unmapped.items():
            clean_hdr = cls._clean_header_name(hdr)
            for kw in cls._TYPE_HINT_HEADER_KEYWORDS:
                if kw == clean_hdr or (len(kw) >= 5 and kw in clean_hdr):
                    return val
        return None

    # ------------------------------------------------------------------
    # Name normalisation
    # ------------------------------------------------------------------

    @classmethod
    def _normalize_names(cls, data: Dict[str, Any]) -> None:
        full_name = data.get("full_name") or ""
        first_name = data.get("first_name") or ""
        last_name = data.get("last_name") or ""

        honorifics = [r"^(dr|mr|mrs|ms|prof)\.?\s+"]
        for hon in honorifics:
            if full_name:
                full_name = re.sub(hon, "", full_name, flags=re.IGNORECASE).strip()
            if first_name:
                first_name = re.sub(hon, "", first_name, flags=re.IGNORECASE).strip()

        if full_name and not (first_name and last_name):
            parts = full_name.split()
            if len(parts) == 1:
                first_name = parts[0]
            elif len(parts) >= 2:
                first_name = parts[0]
                last_name = " ".join(parts[1:])
        elif first_name and not full_name:
            full_name = f"{first_name} {last_name}".strip()

        data["first_name"] = first_name or None
        data["last_name"] = last_name or None
        data["full_name"] = full_name or None

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        p = phone.strip()
        has_plus = p.startswith("+")
        digits = re.sub(r"\D", "", p)
        if not digits:
            return phone
        return f"+{digits}" if has_plus else digits

    @staticmethod
    def _normalize_linkedin_url(url: str) -> str:
        u = url.strip()
        if not u:
            return ""
        if "linkedin.com" in u:
            if not u.startswith(("http://", "https://")):
                u = f"https://{u}"
            if "www.linkedin.com" not in u:
                u = u.replace("linkedin.com", "www.linkedin.com")
            return u
        handle = u.strip("/")
        if not handle.startswith("in/"):
            handle = f"in/{handle}"
        return f"https://www.linkedin.com/{handle}"

    @staticmethod
    def _normalize_website(url: str) -> str:
        u = url.strip()
        if not u:
            return ""
        if not (u.startswith("http://") or u.startswith("https://")):
            return f"https://{u}"
        return u
