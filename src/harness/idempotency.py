"""T-6.1 — idempotency key + `execute_once` contract, docs/17-research-alignment.md.

No new plugin seam: `Store` (docs/02-architecture.md §2.4 — already a seam, real
implementations at `memory/inmemory.py`, `memory/sqlite.py`) is reused as the backing
store. `execute_once` is the whole contract this module exists to hold — check the key,
replay on a hit, run-and-record on a miss.

**Two callers, two different key shapes, S-4/N-8 (docs/07-risks-and-open-issues.md).**

M9's Service API (`server/__init__.py::_Registry.start()`) was the first real caller: an
HTTP client's own `Idempotency-Key` header, deduping a whole RUN across two separate
`POST /v1/runs` calls that share nothing else — `run_id` is generated fresh every
`atry_run()`, so nothing else could recognize that retry as a retry.

`Dispatcher._invoke` (classic backend) / `lg/runtime.py::_run_tools` (durable backend)
are the second caller, wired in later: `idempotency_key(run_id, call_id)` — folded with
`step` too, so two different calls sharing `FakeModel.tool_call()`'s convenience default
id never collide in a test — dedupes a single TOOL call retried mid-run. Scoped
in-memory (`Dispatcher.__init__`'s fresh `InMemoryStore`; `Runtime._idem_for()`'s
per-thread cache, same shape as `_bus_cache`/`_policy_cache`) — it closes the retry
window WITHIN one live run/thread (a call whose fn() succeeded but whose
encode/truncate/taint-check step right after it then raised would otherwise silently
call fn() again), not across a process crash. That wider half of S-4 stays exactly
where it already was: `docs/05-data-and-state.md §3`'s resume rule (`write`/`danger`
tool results are never re-executed on resume) is the answer there, and remains one —
this module's own honest boundary, restated: exactly-once across a crash needs a
durable execution engine, a stated non-goal.
"""
from __future__ import annotations

import json
from typing import Awaitable, Callable, TypeVar

from .memory.base import Store

T = TypeVar("T")


def idempotency_key(run_id: str, call_id: str) -> str:
    """`f"{run_id}:{call_id}"` — a named function instead of an inlined f-string so
    every caller produces the identical key shape (a typo'd separator here would silently
    make two callers' keys never collide, defeating the whole point)."""
    return f"{run_id}:{call_id}"


async def execute_once(
    store: Store, key: str, fn: Callable[[], Awaitable[T]], *, fail_open: bool = False,
) -> tuple[T, bool]:
    """Returns `(result, was_replayed)`. A hit on `key` returns the recorded result
    without calling `fn` again; a miss calls `fn`, records the result, and returns it.

    `result` round-trips through JSON — the same string-only constraint `Store` already
    carries (`memory/base.py`'s own docstring: pickling arbitrary objects into a store is
    a deserialization vulnerability and a versioning trap). Idempotency gains nothing
    from relaxing that.

    **`fail_open`** decides what happens when the store itself raises (unreachable,
    disk full, whatever) — this is the exact fork docs/17 T-6.1 specifies: fail CLOSED
    (the exception propagates, `fn` never runs) for `write`/`danger`, where running an
    unrecorded call risks an undetectable double-effect; fail OPEN (`fn` runs anyway,
    unrecorded — `was_replayed` is always `False` on this path since nothing could be
    checked) for `read`/`external`, where losing the dedup guarantee is strictly better
    than refusing a safe, re-runnable call over a store outage that has nothing to do
    with the call itself.

    The two failure points are not symmetric. A `get` failure happens BEFORE `fn` runs,
    so fail-closed there means `fn` never runs at all — clean. A `put` failure happens
    AFTER `fn` already ran and (for write/danger) already had its side effect: raising
    at that point cannot undo it, it only makes the now-broken idempotency guarantee
    LOUD instead of silently returning success while the record never got written —
    fail visible (IDL-30), the same choice this whole design makes everywhere else.
    """
    try:
        cached = await store.get(key)
    except Exception:
        if not fail_open:
            raise
        cached = None
    if cached is not None:
        return json.loads(cached), True
    result = await fn()
    try:
        await store.put(key, json.dumps(result, sort_keys=True, ensure_ascii=False))
    except Exception:
        if not fail_open:
            raise
        # fail_open: the call already ran and produced a real result — losing the
        # record means a future replay of this same key won't be caught, but returning
        # an error here for a call that SUCCEEDED would be strictly worse.
    return result, False
