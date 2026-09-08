"""
Pre-Campaign Outreach Safety & Compliance Validator.

Executes a mandatory 7-point safety validation before ANY campaign starts:
1. Recipient count (bounds checking against campaign and provider limits)
2. Duplicate recipients (identifies and blocks duplicate audience entries)
3. Malformed emails (RFC 5322 compliance checking)
4. Suppressed recipients (blocks OPT_OUT, UNSUBSCRIBE, DO_NOT_CONTACT, BOUNCE_PERMANENT)
5. Campaign purpose (validates non-empty, legitimate, policy-compliant objective)
6. Sender identity (verifies authenticated and valid sender profile)
7. Integration status (checks connectivity and enforces first-run dry-run rules)
"""

import re
import logging
from typing import Dict, Any, List, Optional, Tuple, Set
from pydantic import BaseModel, Field

from app.safety.policy import (
    CampaignLimits,
    ProviderSafetyCeilings,
    SafetyViolationError,
    enforce_dry_run_policy,
)
from app.safety.suppression import SuppressionManager, suppression_manager
from app.safety.audit import SafetyAuditLogger, SafetyAuditEventType, safety_audit_logger

logger = logging.getLogger(__name__)

# Standard RFC 5322-compliant email regex (supports localhost for local dev/testing)
EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9_.+-]+@(?:localhost|[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+)$",
    re.IGNORECASE,
)

# Unacceptable / abusive purpose patterns (anti-phishing / anti-scam)
PROHIBITED_PURPOSE_PATTERNS = [
    re.compile(r"\b(?:crypto\s+giveaway|double\s+your\s+money|lottery\s+winner|urgent\s+transfer|wire\s+funds)\b", re.IGNORECASE),
    re.compile(r"\b(?:login\s+to\s+verify|suspended\s+account|password\s+reset\s+alert)\b", re.IGNORECASE),
]


class ValidationReport(BaseModel):
    is_valid: bool
    campaign_id: str
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    recipient_count: int = 0
    duplicate_emails: List[str] = Field(default_factory=list)
    malformed_emails: List[str] = Field(default_factory=list)
    suppressed_emails: List[Dict[str, Any]] = Field(default_factory=list)
    sender_valid: bool = True
    purpose_valid: bool = True
    integration_valid: bool = True
    effective_dry_run: bool = False
    details: Dict[str, Any] = Field(default_factory=dict)


class CampaignSafetyValidator:
    """
    Coordinates and executes the pre-campaign validation gate.
    """

    def __init__(
        self,
        suppression_svc: Optional[SuppressionManager] = None,
        audit_svc: Optional[SafetyAuditLogger] = None,
    ):
        self.suppression = suppression_svc or suppression_manager
        self.audit = audit_svc or safety_audit_logger

    @staticmethod
    def is_valid_email_format(email: str) -> bool:
        if not email or not isinstance(email, str):
            return False
        clean = email.strip()
        if len(clean) > 254 or len(clean) < 3:
            return False
        return bool(EMAIL_REGEX.match(clean))

    async def validate_campaign(
        self,
        campaign_id: str,
        recipients: List[Dict[str, Any]],
        campaign_purpose: str,
        sender_profile: Optional[Dict[str, Any]],
        provider: str,
        integration_status: Dict[str, Any],
        limits: Optional[CampaignLimits] = None,
        dry_run: bool = False,
        allow_direct_execution: bool = False,
        is_new_integration: bool = False,
        actor_id: Optional[str] = "system",
        strict: bool = True,
    ) -> ValidationReport:
        """
        Runs the comprehensive pre-campaign validation suite.

        Args:
            campaign_id: Unique campaign identifier
            recipients: List of recipient contact records/dicts
            campaign_purpose: Objective/purpose of the campaign
            sender_profile: Dict containing sender identity (name, email)
            provider: Integration name (e.g. "gmail", "mock", "linkedin")
            integration_status: Result of integration connectivity check
            limits: Configurable campaign limits
            dry_run: Whether user requested dry_run mode
            allow_direct_execution: Explicit authorization to bypass new integration dry-run
            is_new_integration: Whether this integration account has not run direct campaigns yet
            actor_id: Initiating user or system ID
            strict: If True, raises SafetyViolationError on validation failure

        Returns:
            ValidationReport
        Raises:
            SafetyViolationError if strict=True and is_valid is False
        """
        limits = limits or CampaignLimits()
        errors: List[str] = []
        warnings: List[str] = []
        details: Dict[str, Any] = {}

        # -------------------------------------------------------------
        # 1. Recipient Count Validation
        # -------------------------------------------------------------
        recipient_count = len(recipients)
        if recipient_count == 0:
            errors.append("Recipient list is empty. At least one recipient is required.")

        if recipient_count > limits.max_recipients_per_campaign:
            errors.append(
                f"Recipient count ({recipient_count}) exceeds configured campaign limit "
                f"of {limits.max_recipients_per_campaign}."
            )

        provider_daily_ceiling = ProviderSafetyCeilings.get_provider_daily_ceiling(provider)
        if recipient_count > provider_daily_ceiling:
            errors.append(
                f"Recipient count ({recipient_count}) exceeds provider '{provider}' "
                f"hard daily ceiling of {provider_daily_ceiling}."
            )

        # -------------------------------------------------------------
        # 2. Duplicate Recipients & 3. Malformed Emails
        # -------------------------------------------------------------
        seen_emails: Set[str] = set()
        duplicate_emails: List[str] = []
        malformed_emails: List[str] = []
        valid_emails_for_suppression: List[str] = []

        for r in recipients:
            raw_email = r.get("email") or ""
            norm_email = str(raw_email).strip().lower()

            # Check format
            if not self.is_valid_email_format(norm_email):
                malformed_emails.append(str(raw_email))
                continue

            # Check duplicates
            if norm_email in seen_emails:
                if norm_email not in duplicate_emails:
                    duplicate_emails.append(norm_email)
            else:
                seen_emails.add(norm_email)
                valid_emails_for_suppression.append(norm_email)

        if duplicate_emails:
            errors.append(
                f"Duplicate recipients detected ({len(duplicate_emails)} duplicates found: "
                f"{', '.join(duplicate_emails[:5])}{'...' if len(duplicate_emails) > 5 else ''})."
            )

        if malformed_emails:
            errors.append(
                f"Malformed/invalid recipient email addresses detected ({len(malformed_emails)} invalid: "
                f"{', '.join(malformed_emails[:5])}{'...' if len(malformed_emails) > 5 else ''})."
            )

        # -------------------------------------------------------------
        # 4. Suppressed Recipients
        # -------------------------------------------------------------
        _, suppressed_records = await self.suppression.filter_suppressed(valid_emails_for_suppression)
        if suppressed_records:
            sup_summaries = [f"{s.get('email')} ({s.get('reason')})" for s in suppressed_records[:5]]
            errors.append(
                f"Suppression violation: {len(suppressed_records)} recipient(s) are suppressed "
                f"due to prior opt-out, unsubscribe, do-not-contact, or hard bounce ({', '.join(sup_summaries)})."
            )

        # -------------------------------------------------------------
        # 5. Campaign Purpose Validation
        # -------------------------------------------------------------
        purpose_valid = True
        clean_purpose = (campaign_purpose or "").strip()
        if len(clean_purpose) < 5:
            purpose_valid = False
            errors.append("Campaign purpose/objective is missing or too short (minimum 5 characters).")
        else:
            for pat in PROHIBITED_PURPOSE_PATTERNS:
                if pat.search(clean_purpose):
                    purpose_valid = False
                    errors.append(
                        "Campaign purpose violates acceptable use and anti-phishing/anti-scam safety policy."
                    )
                    break

        # -------------------------------------------------------------
        # 6. Sender Identity Validation
        # -------------------------------------------------------------
        sender_valid = True
        if not sender_profile or not isinstance(sender_profile, dict):
            sender_valid = False
            errors.append("Sender profile is missing. Outreach requires a verified sender identity.")
        else:
            sender_email = sender_profile.get("email") or sender_profile.get("sender_email")
            sender_name = sender_profile.get("name") or sender_profile.get("sender_name")
            if not sender_email or not self.is_valid_email_format(str(sender_email)):
                sender_valid = False
                errors.append(f"Sender email '{sender_email}' is invalid or missing.")
            if not sender_name or len(str(sender_name).strip()) < 2:
                sender_valid = False
                errors.append("Sender name is missing or too short.")

        # -------------------------------------------------------------
        # 7. Integration Status & Mandatory Dry-Run Policy
        # -------------------------------------------------------------
        integration_valid = True
        effective_dry_run = dry_run

        is_connected = bool(integration_status.get("connected", False))
        if not is_connected:
            state = integration_status.get("state", "DISCONNECTED")
            detail = integration_status.get("detail", "Provider account is not connected or requires reauth.")
            msg = f"Provider integration '{provider}' is not ready ({state}): {detail}"
            if dry_run:
                warnings.append(f"{msg} (Simulation only; allowed in dry-run mode).")
                integration_valid = True
            else:
                integration_valid = False
                errors.append(msg)

        # Check mandatory dry-run for newly connected integrations
        if is_connected:
            try:
                effective_dry_run = enforce_dry_run_policy(
                    is_new_integration=is_new_integration,
                    requested_dry_run=dry_run,
                    allow_direct_execution=allow_direct_execution,
                )
                if is_new_integration and effective_dry_run and not dry_run:
                    warnings.append(
                        "Mandatory dry-run automatically applied for first campaign on newly connected integration."
                    )
            except SafetyViolationError as exc:
                integration_valid = False
                errors.append(exc.message)

        # -------------------------------------------------------------
        # Compile Report & Audit Log
        # -------------------------------------------------------------
        is_valid = len(errors) == 0

        report = ValidationReport(
            is_valid=is_valid,
            campaign_id=campaign_id,
            errors=errors,
            warnings=warnings,
            recipient_count=recipient_count,
            duplicate_emails=duplicate_emails,
            malformed_emails=malformed_emails,
            suppressed_emails=suppressed_records,
            sender_valid=sender_valid,
            purpose_valid=purpose_valid,
            integration_valid=integration_valid,
            effective_dry_run=effective_dry_run,
            details=details,
        )

        # Persist audit record
        audit_event_type = (
            SafetyAuditEventType.CAMPAIGN_VALIDATION_PASSED
            if is_valid
            else SafetyAuditEventType.CAMPAIGN_VALIDATION_FAILED
        )
        await self.audit.log_event(
            event_type=audit_event_type,
            campaign_id=campaign_id,
            actor_id=actor_id,
            details=(
                "Campaign pre-run safety validation passed."
                if is_valid
                else f"Campaign safety validation failed with {len(errors)} violation(s): {'; '.join(errors)}"
            ),
            metadata={
                "recipient_count": recipient_count,
                "duplicates": len(duplicate_emails),
                "malformed": len(malformed_emails),
                "suppressed": len(suppressed_records),
                "effective_dry_run": effective_dry_run,
                "provider": provider,
            },
        )

        if strict and not is_valid:
            error_summary = "; ".join(errors)
            raise SafetyViolationError(
                f"Campaign safety validation failed: {error_summary}",
                code="PRE_CAMPAIGN_VALIDATION_FAILED",
                details=report.model_dump(mode="json"),
            )

        return report


campaign_safety_validator = CampaignSafetyValidator()
