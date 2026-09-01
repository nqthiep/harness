"""Provider-level retry — N-5, docs/10-observability-ops.md §3.

`docs/10 §3` has promised since before this file existed: "`ProviderRateLimited`/
`ProviderUnavailable`/`ProviderTimeout` are all intended to retry — honoring
`Retry-After`, exponential backoff." Before this module, that was the entire contract:
each was caught and turned into `Result(stop_reason=ERROR)`, never retried at all.

One implementation, both backends: `run.py`'s `self._p.complete(...)` and
`lg/adapter.py::ProviderChatModel._generate()`'s `self.provider.complete(...)` both
route every model call through `with_provider_retry()` — the same reason `dispatch.py`
is the ONE place tool retry (T-6.3) lives, not two hand-written copies that could drift
(R-17, `docs/02-architecture.md §3.1`).
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import random
from typing import Awaitable, Callable, Iterator, TypeVar

from .errors import ProviderError, ProviderRateLimited, ProviderTimeout, ProviderUnavailable

T = TypeVar("T")

#: The three vendor-side, by-nature-transient errors docs/10 §3 names. Never
#: `ProviderAuthError`/`ProviderBadRequest` — retrying a malformed or unauthorized
#: request wastes money and time on a failure that will not change (docs/02 §7's own
#: failure-philosophy table already drew this line; retry.py just enforces it).
RETRYABLE: tuple[type[ProviderError], ...] = (
    ProviderRateLimited, ProviderTimeout, ProviderUnavailable)

#: Bounded by attempts too, not ONLY by wall-clock — a deadline that resets every retry
#: (it doesn't; `deadline_s` is consumed, never replenished) still wants an attempt
#: ceiling so a provider stuck returning 0-second Retry-After values can't spin forever.
MAX_ATTEMPTS = 5
BACKOFF_BASE_S = 0.5
BACKOFF_MAX_S = 20.0

OnRetry = Callable[[Exception, int, float], None]

#: `lg/runtime.py::call_model` -> `lg/adapter.py::ProviderChatModel._generate()`'s way
#: to pass `deadline_s`/`on_retry` down WITHOUT going through `BaseChatModel.invoke()`'s
#: `**kwargs`. That seam already carries `max_tokens` safely (a real Anthropic API
#: field a caller is meant to be able to override per call) — but a real `BaseChatModel`
#: (`ChatAnthropic`, checked directly: `_get_request_payload` does `{**self.model_kwargs,
#: **kwargs}` into the literal HTTP request body) forwards EVERY kwarg it doesn't
#: recognize straight into the vendor payload. `deadline_s`/`on_retry` are not Anthropic
#: fields; sending them would 400 a real call the moment anyone used the escape hatch
#: with a real model. A `contextvars.ContextVar`, entered by `call_model` around the
#: exact `self._model.invoke(...)` call and read by `_generate()` — the same call stack,
#: no thread hop, so this needs none of `middleware.py`'s cross-thread verification —
#: reaches only `ProviderChatModel` (the only thing that ever reads it) and touches
#: nothing a vendor SDK ever sees.
_DEADLINE_S: contextvars.ContextVar[float] = contextvars.ContextVar(
    "harness_retry_deadline_s", default=0.0)
_ON_RETRY: contextvars.ContextVar["OnRetry | None"] = contextvars.ContextVar(
    "harness_retry_on_retry", default=None)


@contextlib.contextmanager
def retry_scope(*, deadline_s: float, on_retry: OnRetry | None = None) -> Iterator[None]:
    """Entered once per model call by `lg/runtime.py::call_model`, around
    `self._model.invoke(...)`. Not part of the public API."""
    t1 = _DEADLINE_S.set(deadline_s)
    t2 = _ON_RETRY.set(on_retry)
    try:
        yield
    finally:
        _DEADLINE_S.reset(t1)
        _ON_RETRY.reset(t2)


def current_deadline_s() -> float:
    return _DEADLINE_S.get()


def current_on_retry() -> "OnRetry | None":
    return _ON_RETRY.get()


async def with_provider_retry(fn: Callable[[], Awaitable[T]], *, deadline_s: float,
                              on_retry: OnRetry | None = None) -> T:
    """Call `fn()`; on a `RETRYABLE` error, wait and call it again — up to `MAX_ATTEMPTS`
    times, never past `deadline_s` seconds of TOTAL wait (the run's remaining
    wall-clock, `Ledger.remaining_wall_clock()` — retries cannot outlive the budget,
    docs/10 §3's own promise). `on_retry(exc, attempt, wait_s)` fires just before each
    sleep, for callers that want to emit an event or log — never for the final,
    re-raised failure.

    A non-`RETRYABLE` exception (including a real `asyncio.CancelledError`, which is not
    an `Exception` subclass and so is never caught here at all — T-6.2 still holds)
    propagates on the first attempt, exactly as if this wrapper weren't here.
    """
    attempt = 0
    remaining = deadline_s
    while True:
        try:
            return await fn()
        except RETRYABLE as exc:
            attempt += 1
            wait = _backoff(exc, attempt)
            if attempt >= MAX_ATTEMPTS or remaining <= 0 or wait > remaining:
                raise
            if on_retry is not None:
                on_retry(exc, attempt, wait)
            await asyncio.sleep(wait)
            remaining -= wait


def _backoff(exc: Exception, attempt: int) -> float:
    """`Retry-After`, when the vendor sent one (`ProviderError.retry_after_s`) — that is
    the vendor telling us exactly how long, and guessing a shorter wait would be
    ignoring it. Otherwise exponential backoff with jitter (spread out simultaneous
    retries from concurrent runs so they don't re-hit the provider in lockstep)."""
    retry_after = getattr(exc, "retry_after_s", None)
    if isinstance(retry_after, (int, float)) and retry_after > 0:
        return float(retry_after)
    base = min(BACKOFF_BASE_S * (2 ** (attempt - 1)), BACKOFF_MAX_S)
    return base * (0.5 + random.random())
