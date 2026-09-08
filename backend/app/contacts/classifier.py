"""
Multi-tier contact classifier.

Tier 1: contact_type_hint – explicit classification column in the spreadsheet.
Tier 2: Deterministic regex against role / company / focus / notes.
Tier 3: LLM fallback for genuinely ambiguous contacts.

The classifier NEVER invents information.  If no confident classification can
be made the contact remains UNKNOWN rather than being guessed.

Classification metadata is returned alongside the ContactType:
    {
        "contact_type":           "FOUNDER",
        "classification_method":  "deterministic",
        "classification_confidence": 0.95,
        "classification_reason":  "Role contains 'Founder & CEO'",
    }
"""

import re
import logging
from typing import Dict, Any, Tuple, Optional, List

from app.contacts.schema import ContactType
from app.llm.client import invoke_llm

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Keyword / pattern sets
# ---------------------------------------------------------------------------

# Each entry is a tuple (pattern_string, re.flags).
# Patterns are checked against the *role* string unless noted.

_VC_PATTERNS: List[str] = [
    r"\b(general partner|managing partner|venture partner|growth partner|investment partner)\b",
    r"\b(venture capital|vc firm|vc fund|seed fund)\b",
    r"\b(principal|associate)\b.*\b(vc|ventures|capital|fund|investments?)\b",
    r"\b(managing director)\b.*\b(capital|ventures|fund|partners)\b",
    r"\b(partner)\b.*\b(ventures|capital|fund)\b",
    r"\b(principal)\b.*\b(invest|portfolio)\b",
    r"\b(vc)\b",
]

_INVESTOR_PATTERNS: List[str] = [
    r"\b(angel investor|angel|seed investor|early stage investor)\b",
    r"\b(family office|limited partner|lp|syndicate lead|syndicate)\b",
    r"\b(private equity|pe associate|pe principal|pe partner)\b",
    r"\b(investment analyst|portfolio manager|investment manager|investor)\b",
    r"\b(venture scout|investing|investment committee)\b",
]

_FOUNDER_PATTERNS: List[str] = [
    r"\b(co[\s\-]?founder|cofounder|founding partner|founding ceo|founding cto|founding engineer)\b",
    r"\b(founder)\b",
    r"\b(owner|proprietor)\b",
    r"\b(ceo|cto|cpo|coo)\b",
    r"\b(chief executive officer|chief technology officer|chief product officer|chief operating officer)\b",
    r"\b(entrepreneur)\b",
    r"\b(startup founder|startup ceo)\b",
]

_RECRUITER_PATTERNS: List[str] = [
    r"\b(technical recruiter|tech recruiter|talent recruiter|executive recruiter)\b",
    r"\b(headhunter|talent sourcer|sourcer|recruiting lead|recruiting coordinator)\b",
    r"\b(staffing specialist|recruitment consultant|recruiter)\b",
    r"\b(hiring manager)\b",
    r"\b(talent acquisition)\b",
    r"\b(talent partner|recruitment lead)\b",
]

_HR_PATTERNS: List[str] = [
    r"\b(head of people|vp of people|vp people|chief people officer)\b",
    r"\b(human resources|hrbp|hr business partner|hr manager|hr director|hr head|head of hr)\b",
    r"\b(people operations|people ops|people and culture|people & culture)\b",
    r"\b(human resource|hr coordinator|hr specialist)\b",
    r"\b(hr)\b",  # plain "HR" is unambiguous
]

_OTHER_PATTERNS: List[str] = [
    r"\b(software engineer|developer|programmer|architect|data scientist|machine learning)\b",
    r"\b(account executive|sales manager|sdr|bdr|sales director)\b",
    r"\b(product manager|project manager|scrum master|marketing|designer|graphic)\b",
    r"\b(accountant|legal counsel|attorney|lawyer|student|intern|analyst)\b",
]

# ---------------------------------------------------------------------------
# Explicit hint → ContactType mapping (for contact_type_hint column)
# ---------------------------------------------------------------------------

_HINT_MAP: Dict[str, ContactType] = {
    # Founders
    "founder": ContactType.FOUNDER,
    "co-founder": ContactType.FOUNDER,
    "cofounder": ContactType.FOUNDER,
    "ceo": ContactType.FOUNDER,
    # HR
    "hr": ContactType.HR,
    "human resources": ContactType.HR,
    "people": ContactType.HR,
    "people operations": ContactType.HR,
    # Recruiter
    "recruiter": ContactType.RECRUITER,
    "recruitment": ContactType.RECRUITER,
    "talent": ContactType.RECRUITER,
    "talent acquisition": ContactType.RECRUITER,
    # Investor / VC
    "investor": ContactType.INVESTOR,
    "angel": ContactType.INVESTOR,
    "vc": ContactType.VC,
    "venture capital": ContactType.VC,
    "venture capitalist": ContactType.VC,
    # Pass-through exact enum values
    "INVESTOR": ContactType.INVESTOR,
    "VC": ContactType.VC,
    "FOUNDER": ContactType.FOUNDER,
    "HR": ContactType.HR,
    "RECRUITER": ContactType.RECRUITER,
    "OTHER": ContactType.OTHER,
    "UNKNOWN": ContactType.UNKNOWN,
}


def _match_any(patterns: List[str], text: str) -> bool:
    """Returns True if any pattern matches text (case-insensitive)."""
    for pat in patterns:
        if re.search(pat, text, re.IGNORECASE):
            return True
    return False


class ContactClassifier:
    """
    High-performance multi-tier contact classifier.
    """

    @classmethod
    def classify_contact(
        cls,
        contact_data: Dict[str, Any],
        use_llm_fallback: bool = False,
    ) -> Tuple[ContactType, float]:
        """
        Classifies a normalised contact dict.
        Returns (ContactType, confidence 0.0–1.0).
        """
        c_type, confidence, _reason = cls.classify_contact_detailed(
            contact_data, use_llm_fallback=use_llm_fallback
        )
        return c_type, confidence

    @classmethod
    def classify_contact_detailed(
        cls,
        contact_data: Dict[str, Any],
        use_llm_fallback: bool = False,
    ) -> Tuple[ContactType, float, str]:
        """
        Classifies a contact and returns (ContactType, confidence, reason).
        """
        role = (contact_data.get("role") or "").strip()
        company = (contact_data.get("company") or "").strip()
        focus = (contact_data.get("investment_focus") or "").strip()
        notes = (contact_data.get("notes") or "").strip()
        hint = (contact_data.get("contact_type_hint") or "").strip()

        # ----------------------------------------------------------------
        # Tier 1 – Explicit hint column (highest priority)
        # ----------------------------------------------------------------
        if hint:
            ct = cls._classify_from_hint(hint)
            if ct is not None and ct != ContactType.UNKNOWN:
                return ct, 1.0, f"Explicit type column value: '{hint}'"

        # ----------------------------------------------------------------
        # Tier 2 – Deterministic regex
        # ----------------------------------------------------------------
        c_type, confidence, reason = cls._classify_deterministic(
            role, company, focus, notes
        )
        if confidence >= 0.8:
            return c_type, confidence, reason

        # ----------------------------------------------------------------
        # Tier 3 – LLM fallback (only when explicitly enabled and role present)
        # ----------------------------------------------------------------
        if use_llm_fallback and (role or notes) and confidence < 0.6:
            llm_type = cls._classify_with_llm(role, company, notes)
            if llm_type and llm_type != ContactType.UNKNOWN:
                return llm_type, 0.85, f"LLM classification from role='{role}'"

        return c_type, confidence, reason

    # ------------------------------------------------------------------
    # Tier 1 helper
    # ------------------------------------------------------------------

    @classmethod
    def _classify_from_hint(cls, hint: str) -> Optional[ContactType]:
        """
        Tries to map an explicit contact_type_hint value to a ContactType.
        """
        norm = hint.strip().upper()
        # Exact enum match first
        for ct in ContactType:
            if ct.value == norm:
                return ct

        # Lowercase keyword map
        norm_lower = hint.strip().lower()
        if norm_lower in _HINT_MAP:
            return _HINT_MAP[norm_lower]

        # Partial keyword scan
        for kw, ct in _HINT_MAP.items():
            if len(kw) >= 3 and kw.lower() in norm_lower:
                return ct

        return None

    # ------------------------------------------------------------------
    # Tier 2 helper
    # ------------------------------------------------------------------

    @classmethod
    def _classify_deterministic(
        cls, role: str, company: str, focus: str, notes: str
    ) -> Tuple[ContactType, float, str]:
        """Deterministic regex and keyword evaluation."""
        combined = f"{role} {company} {focus} {notes}"

        if not role and not focus and not company and not notes:
            return ContactType.UNKNOWN, 0.0, "No information available"

        # Check VC first (more specific than INVESTOR)
        if _match_any(_VC_PATTERNS, combined):
            return ContactType.VC, 0.95, "Matches VC/venture capital pattern"

        # Check INVESTOR
        if _match_any(_INVESTOR_PATTERNS, combined):
            return ContactType.INVESTOR, 0.90, "Matches investor pattern"

        # Check FOUNDER (on role field primarily, then combined)
        if _match_any(_FOUNDER_PATTERNS, role):
            return ContactType.FOUNDER, 0.95, f"Role contains founder/executive pattern: '{role}'"
        if _match_any(_FOUNDER_PATTERNS, combined):
            return ContactType.FOUNDER, 0.85, "Combined fields contain founder pattern"

        # Check RECRUITER
        if _match_any(_RECRUITER_PATTERNS, role):
            return ContactType.RECRUITER, 0.95, f"Role matches recruiter pattern: '{role}'"
        if _match_any(_RECRUITER_PATTERNS, combined):
            return ContactType.RECRUITER, 0.85, "Combined fields match recruiter pattern"

        # Check HR
        if _match_any(_HR_PATTERNS, role):
            return ContactType.HR, 0.92, f"Role matches HR pattern: '{role}'"
        if _match_any(_HR_PATTERNS, combined):
            return ContactType.HR, 0.82, "Combined fields match HR pattern"

        # Check OTHER clear roles
        if _match_any(_OTHER_PATTERNS, role):
            return ContactType.OTHER, 0.85, f"Role matches other professional pattern: '{role}'"

        # Company-based signals for VC/investor when title is generic
        comp_lower = company.lower()
        if any(w in comp_lower for w in ["ventures", "capital", "fund", "venture partners"]):
            return ContactType.VC, 0.75, f"Company name suggests VC: '{company}'"
        focus_lower = focus.lower()
        if focus_lower and any(
            w in focus_lower for w in ["seed", "series a", "pre-seed", "b2b saas", "fintech", "check size"]
        ):
            return ContactType.INVESTOR, 0.75, "Investment focus fields suggest investor"

        if role:
            return ContactType.OTHER, 0.5, f"Role present but no pattern matched: '{role}'"

        return ContactType.UNKNOWN, 0.3, "Insufficient information"

    # ------------------------------------------------------------------
    # Tier 3 helper
    # ------------------------------------------------------------------

    @classmethod
    def _classify_with_llm(
        cls, role: str, company: str, notes: str
    ) -> Optional[ContactType]:
        prompt = (
            "Classify the following professional contact into EXACTLY ONE category: "
            "INVESTOR, VC, FOUNDER, HR, RECRUITER, OTHER, UNKNOWN.\n"
            f"Title/Role: {role}\nCompany: {company}\nNotes: {notes}\n"
            "Return only the category name in uppercase. Nothing else."
        )
        try:
            res = invoke_llm(prompt).strip().upper()
            for ct in ContactType:
                if ct.value == res or ct.value in res:
                    return ct
        except Exception as exc:
            logger.debug("LLM classification fallback exception: %s", exc)
        return None

# ---------------------------------------------------------------------------
# Module-level convenience wrapper used by the reclassify endpoint
# ---------------------------------------------------------------------------

def _classify_from_hint_str(hint: str) -> Optional[ContactType]:
    """
    Public wrapper around ContactClassifier._classify_from_hint().
    Accepts a raw string (e.g. "founder", "hr", "recruiter") and returns
    the matching ContactType, or None if not recognisable.
    """
    return ContactClassifier._classify_from_hint(hint)
