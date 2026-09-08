"""
Rate limiting and dispatch pacing respecting provider limits.

CRITICAL COMPLIANCE GUARANTEE:
Never implement mechanisms designed to bypass:
- Gmail limits
- Provider restrictions
- LinkedIn restrictions
- Spam protections
- Platform policies

Enforces:
- Rolling 24-hour daily send caps per provider
- Rolling 1-hour hourly send caps per provider
- Per-domain send caps (prevents corporate domain spam clustering)
- Minimum dispatch pacing delays
"""

import time
import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from app.safety.policy import CampaignLimits, ProviderSafetyCeilings

logger = logging.getLogger(__name__)


class RateLimitExceededError(Exception):
    """Raised when an outreach send exceeds provider or campaign safety rate limits."""

    def __init__(self, message: str, limit_type: str, retry_after_seconds: float = 60.0):
        super().__init__(message)
        self.message = message
        self.limit_type = limit_type
        self.retry_after_seconds = retry_after_seconds


class RateLimiter:
    """
    Enforces safe outreach rate limits, daily quotas, and domain-level pacing.
    All data is kept in memory or synchronized per process.
    """

    def __init__(self):
        # provider -> list of timestamps (seconds)
        self._provider_sends: Dict[str, List[float]] = defaultdict(list)
        # domain -> list of timestamps (seconds)
        self._domain_sends: Dict[str, List[float]] = defaultdict(list)
        # provider -> timestamp of last dispatch
        self._last_send_time: Dict[str, float] = defaultdict(float)

    @staticmethod
    def extract_domain(email: str) -> str:
        parts = (email or "").strip().lower().split("@")
        return parts[-1] if len(parts) == 2 else "unknown"

    def _prune(self, provider: str, domain: str, now: float) -> None:
        """Prunes timestamps older than 24 hours (86400 seconds)."""
        cutoff_24h = now - 86400.0
        if provider in self._provider_sends:
            self._provider_sends[provider] = [
                t for t in self._provider_sends[provider] if t > cutoff_24h
            ]
        if domain in self._domain_sends:
            self._domain_sends[domain] = [
                t for t in self._domain_sends[domain] if t > cutoff_24h
            ]

    def check_rate_limit(
        self,
        provider: str,
        recipient_email: str,
        limits: Optional[CampaignLimits] = None,
        now: Optional[float] = None,
    ) -> Tuple[bool, Optional[str], float]:
        """
        Checks if sending to recipient_email is allowed under provider & campaign limits.

        Returns:
            Tuple[bool, Optional[str], float]: (is_allowed, violation_reason, retry_after_seconds)
        """
        curr_time = now if now is not None else time.time()
        limits = limits or CampaignLimits()
        norm_provider = (provider or "generic").strip().lower()
        domain = self.extract_domain(recipient_email)

        self._prune(norm_provider, domain, curr_time)

        # 1. Check provider hard daily ceiling
        provider_daily_ceiling = ProviderSafetyCeilings.get_provider_daily_ceiling(norm_provider)
        recent_24h_sends = len(self._provider_sends[norm_provider])
        if recent_24h_sends >= provider_daily_ceiling:
            oldest_relevant = self._provider_sends[norm_provider][0]
            retry_after = max(1.0, 86400.0 - (curr_time - oldest_relevant))
            return (
                False,
                f"Provider hard daily ceiling reached for '{norm_provider}' ({recent_24h_sends}/{provider_daily_ceiling}). "
                f"Never attempting bypass as per compliance policies.",
                retry_after,
            )

        # 2. Check campaign-level configurable daily limit
        if recent_24h_sends >= limits.max_daily_sends:
            oldest_relevant = self._provider_sends[norm_provider][0]
            retry_after = max(1.0, 86400.0 - (curr_time - oldest_relevant))
            return (
                False,
                f"Campaign daily send limit reached ({recent_24h_sends}/{limits.max_daily_sends}).",
                retry_after,
            )

        # 3. Check campaign-level configurable hourly limit
        cutoff_1h = curr_time - 3600.0
        recent_1h_sends = sum(1 for t in self._provider_sends[norm_provider] if t > cutoff_1h)
        if recent_1h_sends >= limits.max_hourly_sends:
            oldest_1h = [t for t in self._provider_sends[norm_provider] if t > cutoff_1h][0]
            retry_after = max(1.0, 3600.0 - (curr_time - oldest_1h))
            return (
                False,
                f"Campaign hourly send limit reached ({recent_1h_sends}/{limits.max_hourly_sends}).",
                retry_after,
            )

        # 4. Check domain-level clustering protection (skip generic public domains like gmail.com)
        public_domains = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com"}
        if domain and domain not in public_domains:
            domain_sends_24h = len(self._domain_sends[domain])
            if domain_sends_24h >= limits.max_sends_per_domain:
                oldest_domain = self._domain_sends[domain][0]
                retry_after = max(1.0, 86400.0 - (curr_time - oldest_domain))
                return (
                    False,
                    f"Domain sending limit reached for '@{domain}' ({domain_sends_24h}/{limits.max_sends_per_domain}). "
                    f"Protected against domain-level spam triggering.",
                    retry_after,
                )

        # 5. Check min delay spacing
        last_send = self._last_send_time.get(norm_provider, 0.0)
        elapsed = curr_time - last_send
        if elapsed < limits.min_delay_seconds:
            retry_after = limits.min_delay_seconds - elapsed
            return (
                False,
                f"Pacing delay active ({elapsed:.2f}s < {limits.min_delay_seconds:.2f}s required).",
                retry_after,
            )

        return True, None, 0.0

    def record_send(
        self,
        provider: str,
        recipient_email: str,
        now: Optional[float] = None,
    ) -> None:
        """Records a completed send attempt for pacing & quota tracking."""
        curr_time = now if now is not None else time.time()
        norm_provider = (provider or "generic").strip().lower()
        domain = self.extract_domain(recipient_email)

        self._provider_sends[norm_provider].append(curr_time)
        self._domain_sends[domain].append(curr_time)
        self._last_send_time[norm_provider] = curr_time

    def reset(self) -> None:
        """Clears all in-memory rate limit counters (for testing)."""
        self._provider_sends.clear()
        self._domain_sends.clear()
        self._last_send_time.clear()


rate_limiter = RateLimiter()
