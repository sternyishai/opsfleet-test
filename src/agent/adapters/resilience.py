"""The wrapping every third-party call gets (HLD 06 § Third-party failures): bounded retries
with backoff, an in-process circuit breaker and a token-bucket rate limiter.

Provider-neutral: each adapter says which of its exceptions are retryable and where a
``Retry-After`` hides. Sleep and the clock are injected, so tests never wait.
"""

from collections import deque
from collections.abc import Callable
from typing import Literal

import tenacity

type Clock = Callable[[], float]
type Sleep = Callable[[float], None]
type BreakerState = Literal["closed", "open", "half_open"]


class Unavailable(Exception):
    """This dependency is not to be called now: its breaker is open, or the rate limiter's
    wait is longer than the caller may queue. Never retried; the caller falls back or fails."""


class CircuitBreaker:
    """Opens after ``failures`` retryable failures within ``window_s``; while open, calls fail
    fast. After ``cooldown_s`` one probe call is let through: success closes it, failure
    opens it again."""

    def __init__(self, *, failures: int, window_s: float, cooldown_s: float, clock: Clock) -> None:
        self._threshold = failures
        self._window_s = window_s
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._failures: deque[float] = deque()
        self._opened_at: float | None = None
        self._probing = False

    @property
    def state(self) -> BreakerState:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at < self._cooldown_s:
            return "open"
        return "half_open"

    def allow(self) -> bool:
        state = self.state
        if state == "half_open" and not self._probing:
            self._probing = True  # exactly one probe
            return True
        return state == "closed"

    def success(self) -> None:
        self._failures.clear()
        self._opened_at = None
        self._probing = False

    def failure(self) -> None:
        now = self._clock()
        if self._probing:  # the probe failed: open for another cool-down
            self._opened_at, self._probing = now, False
            return
        self._failures.append(now)
        while self._failures and now - self._failures[0] > self._window_s:
            self._failures.popleft()
        if len(self._failures) >= self._threshold:
            self._opened_at = now
            self._failures.clear()


class TokenBucket:
    """``per_minute`` requests on average, ``burst`` at once. A caller reserves a token and
    waits the returned seconds, instead of sending a request the quota would refuse (429)."""

    def __init__(self, *, per_minute: float, burst: int, clock: Clock) -> None:
        self._rate = per_minute / 60
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._clock = clock
        self._at = clock()

    def reserve(self, *, max_wait_s: float) -> float | None:
        """Seconds to wait before sending, or None (nothing reserved) if longer than allowed."""
        now = self._clock()
        self._tokens = min(self._capacity, self._tokens + (now - self._at) * self._rate)
        self._at = now
        wait = max(0.0, (1 - self._tokens) / self._rate)
        if wait > max_wait_s:
            return None
        self._tokens -= 1
        return wait


class Retry:
    """Retries one call on retryable errors only, with exponential backoff and jitter, at most
    ``attempts`` times. A provider's ``Retry-After`` replaces the backoff; one longer than
    ``max_wait_s`` stops the retries (the caller falls back instead of hanging).

    Every attempt first asks the breaker; each retryable failure is counted on it.
    """

    def __init__(
        self,
        *,
        attempts: int,
        max_wait_s: float,
        is_retryable: Callable[[BaseException], bool],
        retry_after: Callable[[BaseException], float | None],
        sleep: Sleep,
    ) -> None:
        self._attempts = attempts
        self._max_wait_s = max_wait_s
        self._is_retryable = is_retryable
        self._retry_after = retry_after
        self._sleep = sleep
        self._backoff = tenacity.wait_exponential_jitter(initial=1, max=max_wait_s, jitter=1)

    def call[T](self, fn: Callable[[], T], breaker: CircuitBreaker) -> tuple[T, int]:
        """``(result, attempts)``. Raises ``Unavailable`` when the breaker is open, else the
        last exception once retries are spent or at once for a non-retryable one."""
        attempts = 0

        def attempt() -> T:
            nonlocal attempts
            if not breaker.allow():
                raise Unavailable("circuit breaker open")
            attempts += 1
            try:
                result = fn()
            except Exception as exc:
                if self._is_retryable(exc):
                    breaker.failure()
                raise
            breaker.success()
            return result

        retrying = tenacity.Retrying(
            stop=self._stop,
            wait=self._wait,
            retry=tenacity.retry_if_exception(self._is_retryable),
            sleep=self._sleep,
            reraise=True,
        )
        return retrying(attempt), attempts

    def _stop(self, state: tenacity.RetryCallState) -> bool:
        if state.attempt_number >= self._attempts:
            return True
        delay = self._retry_after(_exception(state))
        return delay is not None and delay > self._max_wait_s

    def _wait(self, state: tenacity.RetryCallState) -> float:
        delay = self._retry_after(_exception(state))
        return delay if delay is not None else self._backoff(state)


def _exception(state: tenacity.RetryCallState) -> BaseException:
    outcome = state.outcome
    exc = outcome.exception() if outcome is not None else None
    assert exc is not None, "tenacity only retries a failed attempt"
    return exc
