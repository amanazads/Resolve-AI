"""
Resolve AI Outreach Safety & Compliance Module.
"""

from app.safety.policy import (
    CampaignLimits,
    ProviderSafetyCeilings,
    SafetyViolationError,
    enforce_dry_run_policy,
)
from app.safety.suppression import (
    SuppressionReason,
    SuppressionRecord,
    SuppressionManager,
    suppression_manager,
)
from app.safety.rate_limits import (
    RateLimiter,
    RateLimitExceededError,
    rate_limiter,
)
from app.safety.audit import (
    SafetyAuditEvent,
    SafetyAuditEventType,
    SafetyAuditLogger,
    safety_audit_logger,
)
from app.safety.validator import (
    CampaignSafetyValidator,
    ValidationReport,
    campaign_safety_validator,
)

__all__ = [
    "CampaignLimits",
    "ProviderSafetyCeilings",
    "SafetyViolationError",
    "enforce_dry_run_policy",
    "SuppressionReason",
    "SuppressionRecord",
    "SuppressionManager",
    "suppression_manager",
    "RateLimiter",
    "RateLimitExceededError",
    "rate_limiter",
    "SafetyAuditEvent",
    "SafetyAuditEventType",
    "SafetyAuditLogger",
    "safety_audit_logger",
    "CampaignSafetyValidator",
    "ValidationReport",
    "campaign_safety_validator",
]
