import logging
import math
from typing import Optional
from datetime import datetime, timezone

from app.automation.models import AutomationJob, AutomationStatus

logger = logging.getLogger(__name__)

class RetryService:
    """
    Manages retry policies, exponential backoff, and failure classification for AutomationJobs.
    """

    def __init__(
        self,
        initial_interval_seconds: float = 1.0,
        backoff_factor: float = 2.0,
        max_interval_seconds: float = 60.0,
        default_max_retries: int = 3
    ):
        self.initial_interval_seconds = initial_interval_seconds
        self.backoff_factor = backoff_factor
        self.max_interval_seconds = max_interval_seconds
        self.default_max_retries = default_max_retries

        # Non-retryable error substrings
        self.non_retryable_markers = [
            "invalid recipient",
            "unauthorized",
            "validation error",
            "syntax error",
            "malformed"
        ]

    def calculate_backoff_delay(self, attempt_count: int) -> float:
        """
        Calculates exponential backoff delay:
        delay = initial_interval * (backoff_factor ** attempt_count)
        capped at max_interval_seconds.
        """
        raw_delay = self.initial_interval_seconds * (self.backoff_factor ** max(0, attempt_count))
        return min(raw_delay, self.max_interval_seconds)

    def is_retryable_error(self, error_message: Optional[str]) -> bool:
        """Determines if an error is transient and eligible for retry."""
        if not error_message:
            return True
        error_lower = error_message.lower()
        for marker in self.non_retryable_markers:
            if marker in error_lower:
                return False
        return True

    def should_retry(self, job: AutomationJob, error_message: Optional[str] = None) -> bool:
        """
        Checks whether a job should be retried based on attempt counts and error nature.
        """
        max_r = job.max_retries if job.max_retries is not None else self.default_max_retries
        if job.attempt_count >= max_r:
            logger.info(f"Job '{job.id}' exceeded max retries ({job.attempt_count}/{max_r}).")
            return False

        if not self.is_retryable_error(error_message or job.error):
            logger.info(f"Job '{job.id}' encountered non-retryable error: '{error_message or job.error}'.")
            return False

        return True

    def process_failure(self, job: AutomationJob, error_message: str) -> AutomationJob:
        """
        Processes a failure event on a job, updating its status to READY for retry or FAILED.
        """
        job.error = error_message
        job.updated_at = datetime.now(timezone.utc)
        job.lease_owner = None
        job.lease_expires_at = None

        if self.should_retry(job, error_message):
            job.status = AutomationStatus.READY
            logger.info(f"Job '{job.id}' scheduled for retry (Attempt {job.attempt_count}/{job.max_retries}).")
        else:
            job.status = AutomationStatus.FAILED
            job.completed_at = datetime.now(timezone.utc)
            logger.warning(f"Job '{job.id}' marked as FAILED permanently: {error_message}")

        return job

retry_service = RetryService()


# ==========================================================================
# Campaign execution retry policy
#
# The RetryService above serves the generic automation pipeline. The policy
# below serves the campaign execution engine, where failures arrive as provider
# SendStatus values rather than as free-text exceptions.
# ==========================================================================

import random
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Tuple

from app.integrations.base import SendStatus


@dataclass
class RetryDecision:
    """What to do about one failed attempt."""

    should_retry: bool
    delay_seconds: float = 0.0
    reason: str = ""
    failure_code: str = ""
    permanent: bool = False
    #: Set when the failure will affect every other job too, so the worker
    #: should stop the whole campaign rather than burn through the queue.
    pause_campaign: bool = False

    def next_attempt_at(self, now: Optional[datetime] = None) -> datetime:
        base = now or datetime.now(timezone.utc)
        return base + timedelta(seconds=self.delay_seconds)


class CampaignRetryPolicy:
    """
    Exponential backoff with jitter for transient failures; no retry at all for
    permanent ones.

    Classification is driven by the provider's explicit status first, and only
    falls back to matching error text when a raw exception is all there is.
    """

    #: Substrings that mark an error as permanent regardless of status.
    PERMANENT_MARKERS = (
        "invalid recipient",
        "invalid email",
        "recipient address rejected",
        "malformed",
        "validation error",
        "not a valid email",
        "message too large",
        "policy violation",
        "does not exist",
    )

    def __init__(
        self,
        initial_delay_seconds: float = 2.0,
        backoff_factor: float = 2.0,
        max_delay_seconds: float = 900.0,
        jitter_ratio: float = 0.2,
        max_attempts: int = 3,
        random_source: Optional[Any] = None,
    ):
        self.initial_delay_seconds = initial_delay_seconds
        self.backoff_factor = backoff_factor
        self.max_delay_seconds = max_delay_seconds
        self.jitter_ratio = jitter_ratio
        self.max_attempts = max_attempts
        self._random = random_source or random

    # -- backoff -----------------------------------------------------------

    def base_delay(self, attempt_count: int) -> float:
        """Delay before attempt N+1, capped. attempt_count is attempts already made."""
        raw = self.initial_delay_seconds * (self.backoff_factor ** max(0, attempt_count - 1))
        return min(raw, self.max_delay_seconds)

    def delay_with_jitter(self, attempt_count: int) -> float:
        """
        Backoff plus proportional jitter.

        Jitter matters here: without it, a few hundred jobs that all hit the same
        provider hiccup would retry in lockstep and reproduce the spike exactly.
        """
        delay = self.base_delay(attempt_count)
        if self.jitter_ratio <= 0:
            return delay
        spread = delay * self.jitter_ratio
        return max(0.0, delay + self._random.uniform(-spread, spread))

    # -- classification ----------------------------------------------------

    def is_permanent_text(self, message: Optional[str]) -> bool:
        if not message:
            return False
        lowered = message.lower()
        return any(marker in lowered for marker in self.PERMANENT_MARKERS)

    def classify(
        self,
        status: Optional[SendStatus],
        error: Optional[str] = None,
        error_code: Optional[str] = None,
    ) -> Tuple[bool, bool, str]:
        """
        Returns (is_permanent, pause_campaign, failure_code).

        UNAUTHORIZED is treated as permanent for the job and as a reason to pause
        the campaign: the credentials are not going to fix themselves between
        attempts, and every remaining job would fail identically.
        """
        code = error_code or (status.value.lower() if status else "unknown_error")

        if status == SendStatus.UNAUTHORIZED:
            return True, True, code
        if status == SendStatus.RATE_LIMITED:
            return False, False, code
        if status == SendStatus.QUEUED:
            return False, False, code
        if self.is_permanent_text(error):
            return True, False, code or "permanent_error"
        return False, False, code

    def decide(
        self,
        attempt_count: int,
        max_attempts: int,
        status: Optional[SendStatus] = None,
        error: Optional[str] = None,
        error_code: Optional[str] = None,
        retry_after_seconds: Optional[int] = None,
    ) -> RetryDecision:
        """Decides what happens to a job after a failed attempt."""
        permanent, pause_campaign, failure_code = self.classify(status, error, error_code)
        reason = error or (status.value if status else "Unknown provider failure")

        if permanent:
            return RetryDecision(
                should_retry=False,
                reason=reason,
                failure_code=failure_code,
                permanent=True,
                pause_campaign=pause_campaign,
            )

        if attempt_count >= max_attempts:
            return RetryDecision(
                should_retry=False,
                reason=f"{reason} (gave up after {attempt_count} attempt(s))",
                failure_code=failure_code,
                permanent=False,
            )

        # A provider that told us how long to wait knows better than our curve.
        delay = (
            float(retry_after_seconds)
            if retry_after_seconds
            else self.delay_with_jitter(attempt_count)
        )
        return RetryDecision(
            should_retry=True,
            delay_seconds=min(delay, self.max_delay_seconds),
            reason=reason,
            failure_code=failure_code,
        )


#: Shared policy instance.
campaign_retry_policy = CampaignRetryPolicy()
