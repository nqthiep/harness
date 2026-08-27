"""Dict-backed Store."""
from __future__ import annotations

import time
from typing import Sequence

from .base import Memo


class InMemoryStore:
    def __init__(self) -> None:
        self._d: dict[str, tuple[str, float, float | None]] = {}

    def _alive(self, key: str) -> bool:
        rec = self._d.get(key)
        if rec is None:
            return False
        if rec[2] is not None and time.time() > rec[2]:
            del self._d[key]                  # expiry enforced on read, not only by sweep
            return False
        return True

    async def get(self, key: str) -> str | None:
        return self._d[key][0] if self._alive(key) else None

    async def put(self, key: str, value: str, *, ttl_s: float | None = None) -> None:
        self._d[key] = (value, time.time(), time.time() + ttl_s if ttl_s else None)

    async def delete(self, key: str) -> None:
        self._d.pop(key, None)

    async def search(self, query: str, *, limit: int = 5) -> Sequence[Memo]:
        terms = [t for t in query.lower().split() if t]
        hits = []
        for k in list(self._d):
            if not self._alive(k):
                continue
            v, ts, _ = self._d[k]
            score = sum(1 for t in terms if t in k.lower() or t in v.lower())
            if score:
                hits.append(Memo(k, v, float(score), ts))
        hits.sort(key=lambda m: (-m.score, -m.updated_at))
        return hits[:limit]

    async def close(self) -> None: ...
