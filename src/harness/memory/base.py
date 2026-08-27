"""Store protocol — docs/04-interfaces.md §5, task T-4.2.

Values are strings, not arbitrary objects: pickling user objects into a store is a
deserialization vulnerability and a versioning trap.
"""
from __future__ import annotations

from typing import Protocol, Sequence

from .._value import value


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
