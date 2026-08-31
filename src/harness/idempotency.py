"""T-6.1 — idempotency key + `execute_once` contract, docs/17-research-alignment.md.

No new plugin seam: `Store` (docs/02-architecture.md §2.4 — already a seam, real
implementations at `memory/inmemory.py`, `memory/sqlite.py`) is reused as the backing
store. `execute_once` is the whole contract this module exists to hold — check the key,
replay on a hit, run-and-record on a miss.

**Wired into the LangGraph backend, and deliberately NOT into the classic loop.** The key
is `f"{run_id}:{call_id}"`, so it is only worth anything where BOTH halves survive a
process restart. On the graph backend they do: `run_id` is LangGraph's `thread_id`, and
`call_id` comes from an AIMessage that was checkpointed *before* the tools node ran. That
makes a real, nameable bug fixable — a crash inside the tools node re-executes the whole
batch on resume, including a `git_push` that already succeeded. `Runtime(idempotency_store=...)`
(and `build_agent(idempotency_store=...)`) closes it: the second execution replays the
recorded result instead of pushing twice.

The classic loop is not a target, because `run_id` is generated fresh in every
`atry_run()` call (`agent.py`) and `aresume()` goes through `atry_run()` too. Reusing the
old id there is not a small fix: `docs/05 §2` guarantees `seq` is gap-free within a run,
and a resumed run restarts `seq` at 0. So within one classic run the key can only ever
catch a duplicate `call_id`, which the T-2.5 dedup in `dispatch.py` already handles a
step earlier and for free. Wiring it there would be dead code no test exercises for real
— the "speculative generality" ADR-002's rejected alternative warns against.

**Only `write`/`danger` calls go through `execute_once`, and that is a correction to
T-6.1's own spec, not an economy.** T-6.1 describes `read`/`external` going through it
with `fail_open=True`. Replaying a recorded `read` across a restart returns the file as it
was *before* the crash — for a coding agent that is not a safety feature, it is a
correctness bug. Re-running a read is what its effect class means: safe, and current.
`fail_open` therefore has no caller in the dispatch path; it stays part of the tested
primitive for a caller that has a use for it (see ADR-064).
"""
from __future__ import annotations

import asyncio
import json
import weakref
from typing import Awaitable, Callable, TypeVar

from .memory.base import Store

T = TypeVar("T")

#: S-4 re-verify (design/07-risks-and-open-issues.md, T-9.1/T-10.* session) — `Store` has
#: no CAS/insert-if-absent (`memory/base.py::Store.put` is an unconditional write), so a
#: plain get-then-put has a TOCTOU window: two concurrent `execute_once` calls sharing a
#: key can both miss the `get`, both run `fn` (a REAL double side effect for `write`/
#: `danger`), both `put`. design/03 §4.4's three-phase protocol (`in_flight` claim via a
#: DB `UNIQUE INDEX`, `IntegrityError` read-back) closes this at the STORE layer, across
#: processes — that protocol was never built (T-6.1 shipped the simpler two-step
#: `execute_once` instead, deliberately, and this was never reconciled with `03 §4` until
#: this re-verify). A per-key `asyncio.Lock` closes the SAME race WITHIN one process —
#: strictly weaker than the documented protocol (two processes/replicas racing the same
#: key are still unprotected), but a real improvement over none, and honestly scoped: it
#: is what an in-process `execute_once` can promise without the persistent-store schema
#: design/03 describes. `WeakValueDictionary` so a key's lock does not outlive every
#: caller holding it — this module has no lifecycle hook to explicitly release one.
_locks: "weakref.WeakValueDictionary[str, asyncio.Lock]" = weakref.WeakValueDictionary()


def _lock_for(key: str) -> asyncio.Lock:
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


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
    # In-process TOCTOU close — see the module-level note on `_locks`. Two callers
    # racing the SAME key serialize here; the second one through sees the first's `put`
    # via `store.get` and replays instead of re-running `fn`.
    async with _lock_for(key):
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


async def once_if_unsafe(
    store: Store | None, key: str, fn: Callable[[], Awaitable[T]], *, retryable: bool,
) -> tuple[T, bool]:
    """`execute_once` for the calls that need it, a plain call for the rest.

    Returns `(result, was_replayed)`. A `retryable` (`read`/`external`) call, or any call
    at all when no store is configured, runs normally — see the module docstring for why
    replaying a recorded read is a bug rather than a guarantee. `fail_open=False` is not a
    parameter here: the only calls that reach `execute_once` through this path are exactly
    the ones where an unrecorded execution risks an undetectable double effect, and that
    is the definition of fail-closed.
    """
    if store is None or retryable:
        return await fn(), False
    return await execute_once(store, key, fn, fail_open=False)
