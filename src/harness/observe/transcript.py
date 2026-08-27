"""Append-only JSONL transcript — task T-3.2.

Redaction happens at write, before the bytes exist: redacting on read means the secret
was already on disk.  Tool arguments are stored as a digest by default (register #24) —
arguments routinely carry PII, and a transcript that captures them is a leak generator.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterator, Mapping

from ..secrets import redact
from .events import Event, EventKind

FSYNC_EVERY = 64
_ALWAYS_FSYNC = {EventKind.RUN_FINISHED, EventKind.ERROR_RAISED}


def digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()[:16]


class TranscriptWriter:
    def __init__(self, path: str | Path, *, level: str = "standard") -> None:
        self.path = Path(path)
        self._level = level
        self._n = 0
        self._fh = None
        self.disabled = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self.path.open("a", encoding="utf-8")
        except OSError:
            self.disabled = True        # a full disk must never kill a run

    def emit(self, event: Event) -> None:
        if self.disabled or self._fh is None:
            return
        data = dict(event.data)
        if event.kind is EventKind.TOOL_REQUESTED and self._level != "debug":
            if "arguments" in data:
                data["arguments_digest"] = digest(data.pop("arguments"))
        line = json.dumps(
            {"seq": event.seq, "ts": round(event.ts, 6), "run_id": event.run_id,
             "kind": event.kind.value, "step": event.step, "data": data},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        try:
            self._fh.write(redact(line) + "\n")     # redact BEFORE the bytes exist
            self._n += 1
            if event.kind in _ALWAYS_FSYNC or self._n % FSYNC_EVERY == 0:
                self._fh.flush(); os.fsync(self._fh.fileno())
        except OSError:
            self.disabled = True

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.flush(); os.fsync(self._fh.fileno()); self._fh.close()
            except OSError:
                pass
            self._fh = None


def read(path: str | Path) -> Iterator[Mapping[str, Any]]:
    """Yield events.  A truncated final line is skipped, not raised: a crash mid-write
    leaves a valid prefix, and refusing to read it would defeat the point."""
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                return
