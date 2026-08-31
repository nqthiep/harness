"""T-6.1 — idempotency key + `execute_once` contract, docs/17-research-alignment.md.

No new plugin seam: `Store` (docs/02-architecture.md §2.4 — already a seam, real
implementations at `memory/inmemory.py`, `memory/sqlite.py`) is reused as the backing
store. `execute_once` is the whole contract this module exists to hold — check the key,
replay on a hit, run-and-record on a miss.

**Not wired into `Agent`/`Dispatcher`/`Runtime` yet, on purpose.** The scenario T-6.1's
own "Why" names — "client timeout rồi retry có thể gửi email hai lần" — is a caller
(an HTTP client hitting a Service API) retrying a request the harness has no way to
recognize as a retry, because `run_id` is generated fresh every `atry_run()` call
(`agent.py`): `idempotency_key = f"{run_id}:{call_id}"` only protects against replay
within work that shares a `run_id`, and nothing shares one across two separate calls
today. That external key has to come from somewhere — M9's Service API is the first
real source of one. Wiring `execute_once` into the dispatch path with no caller that can
ever supply a meaningful key would be dead code no test exercises for real, the exact
"speculative generality" ADR-002's rejected alternative warns against. This module is
the tested, documented primitive M9 wires in when it lands (docs/17 §5: M6 is listed
before M9 for exactly this reason — idempotency is the prerequisite, not the
integration).
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
