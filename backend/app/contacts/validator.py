import re
import logging
from typing import Dict, Any, List, Set, Tuple, Optional

logger = logging.getLogger(__name__)

# Strict RFC-compliant email regex
EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
)

class ContactValidator:
    """
    Validates contact attributes and detects duplicate records within datasets.
    """

    def __init__(self, existing_identifiers: Optional[Set[str]] = None):
        # Set of seen normalized keys for deduplication
        self.seen_emails: Set[str] = set()
        self.seen_composites: Set[str] = set()

        if existing_identifiers:
            self.seen_emails.update(existing_identifiers)

    @staticmethod
    def is_valid_email(email: Optional[str]) -> bool:
        """Validates email format."""
        if not email or not isinstance(email, str):
            return False
        clean = email.strip()
        if len(clean) > 254 or len(clean) < 5:
            return False
        if not EMAIL_REGEX.match(clean):
            return False
        # Domain checks
        domain = clean.split("@")[-1]
        if "." not in domain or domain.endswith(".") or domain.startswith(".") or ".." in domain:
            return False
        tld = domain.split(".")[-1]
        if len(tld) < 2 or not tld.isalpha():
            return False
        return True

    def validate_and_deduplicate(self, contact_dict: Dict[str, Any]) -> Tuple[bool, List[str], bool]:
        """
        Validates contact and checks for duplicates.
        Returns: (is_valid, validation_errors, is_duplicate)
        """
        errors: List[str] = []
        is_duplicate = False

        email = contact_dict.get("email")
        full_name = contact_dict.get("full_name")
        phone = contact_dict.get("phone")
        company = contact_dict.get("company")

        # 1. Email check
        if email:
            if not self.is_valid_email(email):
                errors.append(f"Invalid email address format: '{email}'")
            else:
                # Deduplication check
                norm_email = email.lower()
                if norm_email in self.seen_emails:
                    is_duplicate = True
                    errors.append(f"Duplicate contact with email: '{email}'")
                else:
                    self.seen_emails.add(norm_email)
        else:
            # Missing email check
            if not (phone and full_name):
                errors.append("Missing email address (no alternative phone + name identifier provided)")
            else:
                # Deduplicate by composite key (name + company or name + phone)
                composite_key = f"{full_name.lower()}:{phone}"
                if composite_key in self.seen_composites:
                    is_duplicate = True
                    errors.append("Duplicate contact with matching name and phone")
                else:
                    self.seen_composites.add(composite_key)

        is_valid = len(errors) == 0

        return is_valid, errors, is_duplicate
