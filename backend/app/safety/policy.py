"""
Safety policy rules and limits.

Implements baseline provider ceilings, configurable campaign-level limits,
mandatory first-run dry-run rules for new integrations, and strict anti-bypass
guarantees.
"""

from enum import Enum
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field


class ProviderSafetyCeilings:
    """
    Hard baseline provider ceilings.
    
    CRITICAL POLICY:
    Never implement mechanisms designed to bypass:
    - Gmail limits
    - Provider restrictions
    - LinkedIn restrictions
    - Spam protections
    - Platform policies
    """

    # Gmail personal accounts max 500/24h; Google Workspace max 2000/24h.
    GMAIL_PERSONAL_DAILY_MAX = 500
    GMAIL_WORKSPACE_DAILY_MAX = 2000
    GMAIL_DEFAULT_DAILY_MAX = 500
    GMAIL_MAX_RATE_PER_MINUTE = 60.0

    # LinkedIn invite / messaging safety ceilings
    LINKEDIN_WEEKLY_INVITE_MAX = 100
    LINKEDIN_DAILY_MESSAGE_MAX = 50
    LINKEDIN_MAX_RATE_PER_MINUTE = 5.0

    # Generic provider default safe ceilings
    GENERIC_DAILY_MAX = 500
    GENERIC_MAX_RATE_PER_MINUTE = 60.0

    @classmethod
    def get_provider_daily_ceiling(cls, provider: str) -> int:
        p = (provider or "").strip().lower()
        if "gmail" in p:
            return cls.GMAIL_DEFAULT_DAILY_MAX
        elif "linkedin" in p:
            return cls.LINKEDIN_DAILY_MESSAGE_MAX
        return cls.GENERIC_DAILY_MAX

    @classmethod
    def get_provider_rate_ceiling(cls, provider: str) -> float:
        p = (provider or "").strip().lower()
        if "gmail" in p:
            return cls.GMAIL_MAX_RATE_PER_MINUTE
        elif "linkedin" in p:
            return cls.LINKEDIN_MAX_RATE_PER_MINUTE
        return cls.GENERIC_MAX_RATE_PER_MINUTE


class CampaignLimits(BaseModel):
    """
    Campaign-level limits configurable by the user / admin.
    These limits enforce bounded outreach size, pacing, and domain clustering guards.
    """

    max_recipients_per_campaign: int = Field(
        default=500,
        ge=1,
        le=5000,
        description="Maximum total contacts allowed in a single campaign run."
    )
    max_daily_sends: int = Field(
        default=200,
        ge=1,
        le=2000,
        description="Maximum sends allowed per day across this campaign."
    )
    max_hourly_sends: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Maximum sends allowed in any single hour."
    )
    max_sends_per_domain: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum emails dispatched to the same corporate domain within 24 hours."
    )
    min_delay_seconds: float = Field(
        default=1.0,
        ge=0.1,
        le=60.0,
        description="Minimum gap between consecutive message dispatches to prevent burst flags."
    )

    def clamp_rate_per_minute(self, requested_rate: float, provider: str = "generic") -> float:
        """Clamps rate per minute so it never violates provider ceiling or hourly limit."""
        provider_ceiling = ProviderSafetyCeilings.get_provider_rate_ceiling(provider)
        hourly_implied_rate = float(self.max_hourly_sends) / 60.0
        # Inter-message delay implied max rate
        delay_implied_rate = 60.0 / max(0.1, self.min_delay_seconds)

        safe_rate = min(requested_rate, provider_ceiling, delay_implied_rate)
        if hourly_implied_rate > 0:
            safe_rate = min(safe_rate, hourly_implied_rate)
        return max(0.1, safe_rate)


class SafetyViolationError(Exception):
    """Raised when an outreach safety policy or compliance rule is violated."""

    def __init__(self, message: str, code: str = "SAFETY_VIOLATION", details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


def enforce_dry_run_policy(
    is_new_integration: bool,
    requested_dry_run: bool,
    allow_direct_execution: bool = False
) -> bool:
    """
    Enforces that dry-run is mandatory for the first campaign of a newly connected
    integration unless the user explicitly enables direct execution.

    Returns:
        bool: Effective dry_run flag.
    Raises:
        SafetyViolationError: If attempting a direct send on a new integration without explicit override.
    """
    if is_new_integration:
        if not requested_dry_run and not allow_direct_execution:
            raise SafetyViolationError(
                "Dry-run is mandatory for the first campaign on a newly connected integration. "
                "Run a dry-run test first or explicitly enable direct execution.",
                code="MANDATORY_DRY_RUN_REQUIRED",
                details={"is_new_integration": True, "allow_direct_execution": False}
            )
        return requested_dry_run
    return requested_dry_run
