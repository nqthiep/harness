"""N-5 — provider-level retry, `docs/10-observability-ops.md §3`.

A separate module, not a `RunEngine` method, for the same reason `progress.py` and
`idempotency.py` are: the decision "should THIS failure be retried, and for how long" is
pure — no bus, no ledger, no I/O — and a pure function is trivially unit-testable without
constructing an `Agent` for every case. `run.py` already sits close to IDL-13's 250-line
cap; growing it with logic that doesn't need to be there is the wrong trade.

**Scope: the classic loop only.** `docs/10 §3`'s table describes what `AnthropicProvider`
maps vendor exceptions to — a mapping the LangGraph backend never goes through, because
`build_agent(model=...)` takes an arbitrary caller-supplied LangChain model and calls
`.invoke()` on it directly. Whatever that model raises is whatever its own library raises,
never translated into this harness's `ProviderRateLimited`/`ProviderUnavailable`/
`ProviderTimeout` hierarchy — there is nothing for this module's `isinstance` checks to
recognize on that backend. Porting this to the graph backend would mean either wrapping an
arbitrary LangChain model in the harness's own vendor-exception mapping (a much bigger
change, and one that assumes every LangChain model raises exceptions this adapter's status
codes would even apply to) or teaching this module a second, generic classification scheme
— neither is what N-5 asked for.

**Retry budget is bounded by the run's wall clock, never by an independent count**
(`docs/10 §3`, verbatim) — the same discipline `Budget` applies everywhere else in this
project: a ceiling the caller set, not a number this module invents. There is deliberately
no `MAX_ATTEMPTS` constant here.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, NamedTuple

from .dispatch import RETRY_BACKOFF_MAX_S, RETRY_BACKOFF_S
from .errors import BudgetExceeded, ProviderRateLimited, ProviderTimeout, ProviderUnavailable
from .observe.events import EventKind
from .result import StopReason


def retry_wait(exc: Exception, attempt: int) -> float | None:
    """How long to wait before retrying `exc`, or `None` if it should not be retried at
    all. `attempt` is 0-based — the Nth retry already made, not the Nth call.

    `ProviderRateLimited` honors the vendor's own `Retry-After` when the response carried
    one: the server said how long, and a client-invented number would only be a worse
    guess than the party that actually knows. Everything else transient
    (`ProviderUnavailable`, `ProviderTimeout`, and a bare built-in `TimeoutError` — the
    shape `harness.testing.chaos.TimeoutProvider` raises, matching what a real provider
    seam under test might genuinely raise) backs off exponentially, the SAME schedule
    `dispatch.py`'s tool retry already uses (T-6.3) — one backoff shape in this codebase,
    not a second one invented here (R-17). A rate limit with no `Retry-After` falls back
    to that identical schedule rather than a bespoke default.

    `ProviderAuthError`/`ProviderBadRequest`/any other `ProviderError` (and anything not
    recognized at all) never retries: a bad API key or a malformed request does not start
    working because the client waited — waiting on those is not resilience, it is a
    slower way to fail.
    """
    if isinstance(exc, ProviderRateLimited) and exc.retry_after is not None:
        # A floor, not a ceiling: `Retry-After: 0` (or a slightly-negative clock-skewed
        # value) must not collapse this into a tight retry loop against a server that
        # just told the client to slow down.
        return max(exc.retry_after, RETRY_BACKOFF_S)
    if isinstance(exc, (ProviderRateLimited, ProviderUnavailable, ProviderTimeout,
                        TimeoutError)):
        return min(RETRY_BACKOFF_S * (2 ** attempt), RETRY_BACKOFF_MAX_S)
    return None


class _CallOutcome(NamedTuple):
    """`call_with_retry`'s result: either a response (`resp` set, `stop`/`detail`
    unused) or a reason the step cannot proceed (`resp` is `None`)."""
    resp: Any = None
    reservation: Any = None
    t0: float = 0.0
    stop: Any = None
    detail: str = ""


async def call_with_retry(engine, step: int, input_tokens: int, max_tokens: int, price,
                          hard_in: int, req, on_delta) -> _CallOutcome:
    """Reserve, call, and retry a transient provider failure until either it succeeds
    or the run's wall clock says to stop trying.

    Takes `engine` (the `RunEngine`, for `._bus`/`._l`/`._p`/`._a`) rather than being a
    `RunEngine` method — the same shape `Dispatcher` already uses ("the RunEngine, for
    bus/ledger/policy/taint/agent") — so this logic can live in its own module instead
    of pushing `run.py` over IDL-13's 250-line cap (Round 28 already made this exact
    call once, splitting tool dispatch out for the identical reason).

    One reservation per ATTEMPT (I-1: no model call without a reservation immediately
    before it — a retried call is a new call), released rather than settled when the
    attempt never produced a response (`Ledger.release_reservation`'s own docstring
    explains why a release, not a leak).

    T-6.4 (chaos test "provider timeout") found the original gap: nothing here ever
    caught a provider failure at all, so a live rate limit or a transient timeout
    crashed straight out of `try_run()`. N-4 fixed the crash (catch, convert to
    `Result(ERROR)`); this closes the actual promise `docs/10 §3` made on top of that
    fix — retry, not just catch.
    """
    attempt = 0
    while True:
        try:
            reservation = engine._l.reserve(input_tokens, max_tokens, price,
                                            hard_max_input=hard_in)
        except BudgetExceeded as exc:
            engine._bus.emit(EventKind.BUDGET_EXHAUSTED, step=step, axis="usd",
                             spent=str(engine._l.spent))
            return _CallOutcome(stop=StopReason.BUDGET_EXHAUSTED, detail=str(exc))
        engine._bus.emit(EventKind.BUDGET_RESERVED, step=step,
                         estimate_usd=str(reservation.estimate),
                         spent_usd=str(engine._l.spent),
                         exact=engine._l.last_call_was_exactly_bounded)
        engine._bus.emit(EventKind.MODEL_REQUEST, step=step, model=engine._a.model,
                         input_tokens=input_tokens, max_tokens=max_tokens,
                         n_tools=len(engine._a.toolset), attempt=attempt)
        # `asyncio.CancelledError` is not an `Exception` subclass (Python's own
        # hierarchy), so this catch cannot swallow a cancellation — T-6.2 still holds.
        t0 = time.monotonic()
        try:
            resp = await engine._p.complete(req, on_delta=on_delta)
            return _CallOutcome(resp=resp, reservation=reservation, t0=t0)
        except Exception as exc:
            engine._l.release_reservation(reservation)
            wait = retry_wait(exc, attempt)
            remaining = engine._l.remaining_wall_clock()
            engine._bus.emit(EventKind.ERROR_RAISED, step=step, where="provider",
                             type=type(exc).__name__, message=str(exc),
                             retryable=wait is not None, attempt=attempt)
            if wait is None or remaining <= 0:
                return _CallOutcome(stop=StopReason.ERROR,
                                    detail=f"{type(exc).__name__}: {exc}")
            await asyncio.sleep(min(wait, remaining))
            attempt += 1
