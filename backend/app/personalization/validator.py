import re
import hashlib
from enum import Enum
from typing import List, Dict, Any, Optional, Set, Tuple
from pydantic import BaseModel, Field

from app.contacts.validator import ContactValidator

class ValidationStatus(str, Enum):
    VALID = "VALID"
    WARNING = "WARNING"
    INVALID = "INVALID"

class PersonalizedMessage(BaseModel):
    recipient_email: str
    recipient_name: str
    subject: str
    body: str
    personalization_used: List[str] = Field(default_factory=list)
    confidence: float = 1.0
    validation_status: ValidationStatus = ValidationStatus.VALID
    warnings: List[str] = Field(default_factory=list)

class PersonalizationValidator:
    """
    Deterministic validator for AI personalized emails.
    Enforces email syntax, length boundaries, placeholder detection,
    suspicious claim auditing, duplicate bodies, and similarity tracking.
    """

    # Patterns indicating unrendered or hallucinated template tokens
    PLACEHOLDER_PATTERNS = [
        re.compile(r"\[(?:insert|company|name|first_name|role|firm|portfolio|link|date|website|title|recipient).*?\]", re.IGNORECASE),
        re.compile(r"\{(?:name|company|first_name|last_name|role|firm|portfolio|title|stage).*?\}", re.IGNORECASE),
        re.compile(r"<(?:insert|company|name|role|firm|portfolio).*?>", re.IGNORECASE),
        re.compile(r"\b(?:TODO|TBD|REPLACE_ME|XXX|INSERT_HERE|UNDEFINED|NAN)\b", re.IGNORECASE)
    ]

    # Patterns indicating high-risk hallucinated personal or business claims
    SUSPICIOUS_CLAIM_PATTERNS = [
        (re.compile(r"\b(?:as a fellow (?:alumnus|alum|student|graduate))\b", re.IGNORECASE), "Unverified alumni claim"),
        (re.compile(r"\b(?:our mutual (?:friend|acquaintance|connection))\b", re.IGNORECASE), "Fabricated mutual relationship"),
        (re.compile(r"\b(?:noticed you invested in|saw your investment in|loved your portfolio company)\b", re.IGNORECASE), "Fabricated investment claim"),
        (re.compile(r"\b(?:congrats on your recent (?:funding|series [a-e]|raise))\b", re.IGNORECASE), "Unverified recent funding claim"),
        (re.compile(r"\b(?:having worked together at|when we met at)\b", re.IGNORECASE), "Fabricated prior acquaintance")
    ]

    def __init__(self, min_subject_len: int = 3, max_subject_len: int = 150, min_body_len: int = 30, max_body_len: int = 3500):
        self.min_subject_len = min_subject_len
        self.max_subject_len = max_subject_len
        self.min_body_len = min_body_len
        self.max_body_len = max_body_len
        self.seen_body_hashes: Set[str] = set()
        self.seen_bodies: List[str] = []

    def validate_message(
        self,
        message: PersonalizedMessage,
        recipient_profile: Optional[Dict[str, Any]] = None,
        check_duplicate: bool = True
    ) -> PersonalizedMessage:
        """
        Runs comprehensive deterministic checks on a single generated message.
        Updates validation_status and warnings in place.
        """
        errors = []
        warnings = []

        # 1. Email check
        if not message.recipient_email or not message.recipient_email.strip():
            errors.append("Recipient email is empty.")
        elif not ContactValidator.is_valid_email(message.recipient_email):
            errors.append(f"Invalid recipient email format: '{message.recipient_email}'")

        # 2. Empty checks
        subject = (message.subject or "").strip()
        body = (message.body or "").strip()

        if not subject:
            errors.append("Subject line is empty.")
        elif len(subject) < self.min_subject_len:
            errors.append(f"Subject is too short ({len(subject)} chars, min {self.min_subject_len}).")
        elif len(subject) > self.max_subject_len:
            warnings.append(f"Subject is excessively long ({len(subject)} chars).")

        if not body:
            errors.append("Email body is empty.")
        elif len(body) < self.min_body_len:
            errors.append(f"Body is too short ({len(body)} chars, min {self.min_body_len}).")
        elif len(body) > self.max_body_len:
            warnings.append(f"Body exceeds target maximum length ({len(body)} chars).")

        # 3. Hallucinated Placeholder Detection
        full_text = f"{subject} {body}"
        for pat in self.PLACEHOLDER_PATTERNS:
            matches = pat.findall(full_text)
            if matches:
                errors.append(f"Unrendered placeholder detected: {matches}")

        # 4. Suspicious Fabrications Scanner
        for pat, claim_desc in self.SUSPICIOUS_CLAIM_PATTERNS:
            if pat.search(full_text):
                # Verify if recipient profile actually supports the claim
                is_substantiated = False
                if recipient_profile:
                    notes = (recipient_profile.get("notes") or "").lower()
                    if "investment" in claim_desc.lower() and recipient_profile.get("investment_focus"):
                        is_substantiated = True
                if not is_substantiated:
                    errors.append(f"Suspicious unverified claim detected: '{claim_desc}'")

        # 5. Duplicate Body Check
        if check_duplicate and body:
            norm_body = re.sub(r"\s+", " ", body.lower().strip())
            b_hash = hashlib.md5(norm_body.encode("utf-8")).hexdigest()
            if b_hash in self.seen_body_hashes:
                warnings.append("Duplicate email body detected across batch.")
            else:
                self.seen_body_hashes.add(b_hash)
                self.seen_bodies.append(body)

        # Update message status
        if errors:
            message.validation_status = ValidationStatus.INVALID
            message.warnings.extend(errors)
            message.confidence = 0.0
        elif warnings:
            message.validation_status = ValidationStatus.WARNING
            message.warnings.extend(warnings)
            message.confidence = min(message.confidence, 0.75)
        else:
            message.validation_status = ValidationStatus.VALID

        return message

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        return re.findall(r"\b\w{3,}\b", (text or "").lower())

    @classmethod
    def _ngrams(cls, text: str, n: int = 1) -> Set[str]:
        """Builds the set of word-level n-grams for a text."""
        words = cls._tokenize(text)
        if n <= 1:
            return set(words)
        if len(words) < n:
            return set()
        return {" ".join(words[i : i + n]) for i in range(len(words) - n + 1)}

    @classmethod
    def calculate_similarity(cls, text_a: str, text_b: str, n: int = 1) -> float:
        """
        Computes n-gram Jaccard similarity between two texts (0.0 to 1.0).

        n=1 compares vocabulary overlap; n>=2 compares phrasing, which is what
        catches cookie-cutter messages that merely swap out a name.
        """
        set_a = cls._ngrams(text_a, n)
        set_b = cls._ngrams(text_b, n)

        if not set_a or not set_b:
            return 0.0

        return len(set_a & set_b) / len(set_a | set_b)

    @classmethod
    def audit_batch_diversity(
        cls,
        messages: List[PersonalizedMessage],
        max_similarity: float = 0.90,
        n: int = 3,
        downgrade_status: bool = True,
    ) -> List[Tuple[int, int, float]]:
        """
        Audits a batch of messages so a campaign does not devolve into identical
        clones. Compares n-gram phrasing (default trigrams) rather than raw
        vocabulary, since near-duplicates differ only by a name or a firm.

        Returns list of (index_a, index_b, similarity_score) exceeding max_similarity.
        Flagged messages are annotated and, unless downgrade_status is False,
        moved to WARNING (never overriding an existing INVALID verdict).
        """
        flagged_pairs: List[Tuple[int, int, float]] = []
        count = len(messages)

        for i in range(count):
            for j in range(i + 1, count):
                sim = cls.calculate_similarity(messages[i].body, messages[j].body, n=n)
                if sim < max_similarity:
                    continue

                flagged_pairs.append((i, j, sim))
                for src, other in ((i, j), (j, i)):
                    warning_msg = (
                        f"High similarity ({sim:.2%}) with recipient at index {other}."
                    )
                    if warning_msg not in messages[src].warnings:
                        messages[src].warnings.append(warning_msg)
                    if (
                        downgrade_status
                        and messages[src].validation_status == ValidationStatus.VALID
                    ):
                        messages[src].validation_status = ValidationStatus.WARNING
                        messages[src].confidence = min(messages[src].confidence, 0.75)

        return flagged_pairs
