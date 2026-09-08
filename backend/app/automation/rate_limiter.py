"""
Rate limiting for campaign execution.

A token bucket per campaign, plus an optional global bucket, so a large campaign
paces itself instead of emptying its recipient list into a provider's quota in a
few seconds.

Both the clock and the sleep are injectable, which is what lets the tests assert
pacing behaviour deterministically instead of waiting on wall-clock time.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    """
    `rate_per_minute` is the sustained rate. `burst` is how many sends may go out
    back to back before pacing kicks in; it defaults to one minute's worth,
    capped so a huge rate does not turn into an unbounded opening burst.
    """

    rate_per_minute: float = 60.0
    burst: Optional[int] = None

    def resolved_burst(self) -> float:
        if self.burst is not None:
            return max(1.0, float(self.burst))
        return max(1.0, min(float(self.rate_per_minute), 60.0))


class TokenBucketRateLimiter:
    """
    Classic token bucket.

    Tokens accrue at rate_per_minute/60 per second up to the burst size.
    `acquire()` waits for a token; `try_acquire()` never waits.
    """

    def __init__(
        self,
        rate_per_minute: float = 60.0,
        burst: Optional[int] = None,
        time_source: Optional[Callable[[], float]] = None,
        sleep: Optional[Callable[[float], Awaitable[None]]] = None,
        name: str = "default",
    ):
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute must be greater than zero.")

        self.name = name
        self.rate_per_minute = float(rate_per_minute)
        self.rate_per_second = self.rate_per_minute / 60.0
        self.capacity = RateLimitConfig(rate_per_minute, burst).resolved_burst()

        self._time = time_source or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._tokens = self.capacity
        self._updated_at = self._time()
        self._lock = asyncio.Lock()
        self.total_waits = 0
        self.total_wait_seconds = 0.0

    # -- internals ---------------------------------------------------------

    def _refill(self) -> None:
        now = self._time()
        elapsed = max(0.0, now - self._updated_at)
        self._updated_at = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate_per_second)

    @property
    def available_tokens(self) -> float:
        self._refill()
        return self._tokens

    # -- public API --------------------------------------------------------

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Takes a token if one is available. Never waits."""
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    async def acquire(self, tokens: float = 1.0) -> float:
        """
        Waits until `tokens` are available, then consumes them.
        Returns how long it waited, in seconds.
        """
        waited = 0.0
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    if waited:
                        self.total_waits += 1
                        self.total_wait_seconds += waited
                    return waited

                deficit = tokens - self._tokens
                delay = max(deficit / self.rate_per_second, 0.001)
                waited += delay
                await self._sleep(delay)

    async def penalise(self, seconds: float) -> None:
        """
        Drains the bucket for `seconds` worth of capacity.

        Called when a provider answers RATE_LIMITED: the configured rate was
        evidently too high for the moment, so stop handing out tokens rather than
        marching straight back into the same wall.
        """
        if seconds <= 0:
            return
        self._refill()
        self._tokens = min(self._tokens, 0.0) - seconds * self.rate_per_second
        logger.info(
            "Rate limiter '%s' penalised for %.1fs after an upstream rate limit.",
            self.name,
            seconds,
        )


@dataclass
class RateLimiterRegistry:
    """
    Keeps one limiter per campaign, plus an optional process-wide limiter that
    every send also passes through.
    """

    default_rate_per_minute: float = 60.0
    global_rate_per_minute: Optional[float] = None
    time_source: Optional[Callable[[], float]] = None
    sleep: Optional[Callable[[float], Awaitable[None]]] = None
    _limiters: Dict[str, TokenBucketRateLimiter] = field(default_factory=dict)
    _global: Optional[TokenBucketRateLimiter] = None

    def for_campaign(
        self,
        campaign_id: str,
        rate_per_minute: Optional[float] = None,
        burst: Optional[int] = None,
    ) -> TokenBucketRateLimiter:
        existing = self._limiters.get(campaign_id)
        if existing is not None:
            return existing

        limiter = TokenBucketRateLimiter(
            rate_per_minute=rate_per_minute or self.default_rate_per_minute,
            burst=burst,
            time_source=self.time_source,
            sleep=self.sleep,
            name=f"campaign:{campaign_id}",
        )
        self._limiters[campaign_id] = limiter
        return limiter

    def global_limiter(self) -> Optional[TokenBucketRateLimiter]:
        if self.global_rate_per_minute is None:
            return None
        if self._global is None:
            self._global = TokenBucketRateLimiter(
                rate_per_minute=self.global_rate_per_minute,
                time_source=self.time_source,
                sleep=self.sleep,
                name="global",
            )
        return self._global

    async def acquire(self, campaign_id: str, rate_per_minute: Optional[float] = None) -> float:
        """Passes through the campaign limiter and then the global one."""
        waited = await self.for_campaign(campaign_id, rate_per_minute).acquire()
        global_limiter = self.global_limiter()
        if global_limiter is not None:
            waited += await global_limiter.acquire()
        return waited

    def release_campaign(self, campaign_id: str) -> None:
        self._limiters.pop(campaign_id, None)

    def reset(self) -> None:
        self._limiters.clear()
        self._global = None


#: Shared registry used by the worker.
rate_limiters = RateLimiterRegistry()
