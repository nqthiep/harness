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
from .events import Event, EventKind, to_dict

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
        # No try/except: a path that cannot be opened is a CONFIGURATION mistake, and
        # `Agent.__init__` now probes it so this raises there rather than here. Swallowing
        # it produced a fully successful run with no artifact and no error event —
        # indistinguishable from a process that was killed.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("a", encoding="utf-8")

    def emit(self, event: Event) -> None:
        if self.disabled or self._fh is None:
            return
        # T-9.3: `to_dict()` is the canonical base every transport shares — this
        # transport's own policy (digest tool arguments, round `ts` for a smaller file)
        # layers on top of it, rather than redefining the shape from scratch.
        row = to_dict(event)
        row["ts"] = round(row["ts"], 6)
        if event.kind is EventKind.TOOL_REQUESTED and self._level != "debug":
            if "arguments" in row["data"]:
                row["data"]["arguments_digest"] = digest(row["data"].pop("arguments"))
        line = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                          default=str)
        try:
            self._fh.write(redact(line) + "\n")     # redact BEFORE the bytes exist
            self._n += 1
            if event.kind in _ALWAYS_FSYNC or self._n % FSYNC_EVERY == 0:
                self._fh.flush(); os.fsync(self._fh.fileno())
        except OSError:
            # Disabled FIRST, then re-raised: the flag stops the next event trying again,
            # and `EventBus.emit` turns this one raise into an `error.raised` that the
            # surviving exporters actually receive. Swallowing it here defeated a
            # mechanism that already existed — measured with a real ENOSPC mid-run: the
            # writer went quiet after one line and `bus error.raised` was empty. The
            # comment above used to say "a full disk must never kill a run"; a full disk
            # still does not kill the run, because the bus isolates a broken exporter.
            # What it must not do is pass unnoticed.
            self.disabled = True
            raise

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
