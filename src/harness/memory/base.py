"""Store protocol — docs/04-interfaces.md §5, task T-4.2.

Values are strings, not arbitrary objects: pickling user objects into a store is a
deserialization vulnerability and a versioning trap.
"""
from __future__ import annotations

from typing import Protocol, Sequence

from .._value import value
from ..errors import HarnessError


@value
class Memo:
    key: str
    value: str
    score: float
    updated_at: float


class Store(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def put(self, key: str, value: str, *, ttl_s: float | None = None) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def search(self, query: str, *, limit: int = 5) -> Sequence[Memo]: ...
    async def close(self) -> None: ...

class ConcurrentWriteError(HarnessError):
    """Two writers raced on one key and one update would have been lost.

    Raised rather than retried: the caller knows what its change meant and this module
    does not. Re-reading and re-applying an `enroll` is right; re-applying a `forget`
    after somebody else already re-enrolled that person is not.
    """


async def read_modify_write(store: Store, key: str, mutate, *,
                            what: str = "this record") -> str | None:
    """Get, transform, put — with a lost-update CHECK.

    `Store` is whole-blob get/put with **no compare-and-swap** (that is the protocol's
    documented shape, above). So every ledger built on it does read-modify-write, and two
    writers silently lose one update. Both ledgers in this repository — `tasks.TaskLedger`
    and `examples/vision_tools.IdentityLedger` — carried a paragraph explaining why they
    were safe anyway: their mutating tools are `write`/`danger`, neither of which is
    `parallel_safe`, so they serialise within a step. True, and it stops being true the
    moment anything else writes. `harness.contrib.Driver` runs a background pump, which
    is precisely that (ADR-100).

    **This detects; it does not prevent.** The value is re-read immediately before the
    write and compared with what was read at the start. A race that lands inside that
    window is still lost. What changes is that the overwhelmingly likely case — two
    writers overlapping across an `await` on the model or the disk — becomes an error
    instead of a silently discarded update. Making it impossible needs CAS in the
    protocol, which is a `Store` change and a bigger decision than this one.

    `mutate` receives the raw stored string (or `None`) and returns the raw string to
    write, or `None` to write nothing.
    """
    before = await store.get(key)
    after = mutate(before)
    if after is None:
        return before
    current = await store.get(key)
    if current != before:
        raise ConcurrentWriteError(
            f"{what} was changed by somebody else while this update was being "
            f"prepared, and writing now would discard their change.\n\n"
            f"  key: {key!r}\n\n"
            f"  `Store` has no compare-and-swap, so this is detected rather than "
            f"prevented.\n  Re-read, re-apply your change, and write again — this code "
            f"cannot do that for\n  you, because only you know whether re-applying it "
            f"still means the same thing.\n\n"
            f"  -> docs/04-interfaces.md#5-store")
    await store.put(key, after)
    return after
