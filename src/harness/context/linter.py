"""Cache determinism checking — task T-2.3, revised by ADR-025.

A prompt that changes between calls costs roughly 10x forever and raises nothing.
This turns it into an error.

Two checks, both free:

  1. At construction: render twice back to back.  Catches anything that changes on
     every call — uuid4, random, counters, set iteration order.
  2. At run time, on the first two model calls: compare the prefix that was actually
     sent.  Catches time-based drift with a real inter-call gap.

The original design slept 150 ms between renders at construction (IDL-17).  Round 24
measured it: the sleep cost 150 ms on every construction — 150 s across the 1000-run
property test — and still missed `datetime.now()` formatted to seconds and
`date.today()`, which are the two most common cases.  Expensive and ineffective.
"""
from __future__ import annotations

import os

from ..errors import NonDeterministicPromptError


def _explain(a: str, b: str, when: str) -> NonDeterministicPromptError:
    i = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    lo, hi = max(0, i - 40), i + 40
    return NonDeterministicPromptError(
        "your agent's prompt changes between calls, so caching will never work and every\n"
        "call will cost roughly 10x more.\n\n"
        f"  Detected {when}. The difference starts at byte {i}:\n\n"
        f"    call 1: ...{a[lo:hi]}...\n"
        f"    call 2: ...{b[lo:hi]}...\n\n"
        "  Something in job= or a tool description changes every time — a timestamp, a\n"
        "  random id, today's date. Move it into the message instead:\n\n"
        '      agent.run(f"[now: {datetime.now():%H:%M}] {question}")\n\n'
        "  -> docs/07-cost.md#21-the-cache-linter"
    )


def check_determinism(assembler) -> None:
    """Construction-time half.  Free — no sleep."""
    if os.environ.get("HARNESS_SKIP_CACHE_LINT"):
        return
    a = assembler.render_prefix()
    b = assembler.render_prefix()
    if a != b:
        raise _explain(a, b, "at construction")


class PrefixWatcher:
    """Run-time half.  Compares the prefix actually sent on the first two calls."""

    __slots__ = ("_first", "_checked")

    def __init__(self) -> None:
        self._first: str | None = None
        self._checked = False

    def observe(self, prefix: str) -> None:
        if self._checked or os.environ.get("HARNESS_SKIP_CACHE_LINT"):
            return
        if self._first is None:
            self._first = prefix
            return
        self._checked = True
        if prefix != self._first:
            raise _explain(self._first, prefix, "between two real calls")
