"""SQLite-backed Store — schema in docs/05-data-and-state.md §5.

STRICT tables because SQLite's type affinity silently accepts a string into an integer
column.  WAL and a busy timeout so a second process reading the same file does not
produce `database is locked`.  Expiry is enforced on read as well as by a sweep: a TTL
that only works when the sweep has run is not a TTL.
"""
from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path
from typing import Sequence

from ..errors import ConfigError
from .base import Memo

SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS memos (
  key TEXT PRIMARY KEY, value TEXT NOT NULL, agent TEXT NOT NULL,
  created_at REAL NOT NULL, updated_at REAL NOT NULL, expires_at REAL
) STRICT;
CREATE INDEX IF NOT EXISTS idx_memos_agent ON memos(agent);
CREATE INDEX IF NOT EXISTS idx_memos_expires ON memos(expires_at) WHERE expires_at IS NOT NULL;
CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL) STRICT;
"""


class SqliteStore:
    def __init__(self, path: str | Path, *, agent: str = "default") -> None:
        self._agent = agent
        self._db = sqlite3.connect(str(path), timeout=5.0, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        self._db.executescript(_DDL)
        row = self._db.execute("SELECT version FROM schema_meta").fetchone()
        if row is None:
            self._db.execute("INSERT INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,))
            self._db.commit()
        elif row[0] > SCHEMA_VERSION:
            raise ConfigError(
                f"this memory file was written by a newer version of harness "
                f"(schema {row[0]}, this build understands {SCHEMA_VERSION}).\n"
                f"  Refusing to open it rather than corrupting it by guessing."
            )

    def _sweep(self) -> None:
        self._db.execute("DELETE FROM memos WHERE expires_at IS NOT NULL AND expires_at < ?",
                         (time.time(),))

    async def get(self, key: str) -> str | None:
        def go():
            row = self._db.execute(
                "SELECT value, expires_at FROM memos WHERE key=?", (key,)).fetchone()
            if row is None:
                return None
            if row[1] is not None and row[1] < time.time():
                self._db.execute("DELETE FROM memos WHERE key=?", (key,)); self._db.commit()
                return None
            return row[0]
        return await asyncio.to_thread(go)

    async def put(self, key: str, value: str, *, ttl_s: float | None = None) -> None:
        def go():
            now = time.time()
            self._db.execute(
                "INSERT INTO memos(key,value,agent,created_at,updated_at,expires_at) "
                "VALUES (?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET "
                "value=excluded.value, updated_at=excluded.updated_at, "
                "expires_at=excluded.expires_at",
                (key, value, self._agent, now, now, now + ttl_s if ttl_s else None))
            self._db.commit()
        await asyncio.to_thread(go)

    async def delete(self, key: str) -> None:
        def go():
            self._db.execute("DELETE FROM memos WHERE key=?", (key,)); self._db.commit()
        await asyncio.to_thread(go)

    async def search(self, query: str, *, limit: int = 5) -> Sequence[Memo]:
        def go():
            self._sweep(); self._db.commit()
            terms = [t for t in query.lower().split() if t]
            if not terms:
                return []
            rows = self._db.execute(
                "SELECT key, value, updated_at FROM memos WHERE agent=?", (self._agent,)
            ).fetchall()
            hits = []
            for k, v, ts in rows:
                score = sum(1 for t in terms if t in k.lower() or t in v.lower())
                if score:
                    hits.append(Memo(k, v, float(score), ts))
            hits.sort(key=lambda m: (-m.score, -m.updated_at))
            return hits[:limit]
        return await asyncio.to_thread(go)

    async def close(self) -> None:
        await asyncio.to_thread(self._db.close)
