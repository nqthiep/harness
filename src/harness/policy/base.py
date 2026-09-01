"""Verdict lattice and Policy protocol — docs/04-interfaces.md §3.

Verdict lives here rather than in tools/ because EFFECT_PROFILES needs it and
policy/ must not import tools/ at runtime (see the TYPE_CHECKING guard below):
tools/__init__.py imports Verdict, so the reverse import would be a cycle.
"""
from __future__ import annotations

from .._value import value

from enum import IntEnum
from typing import TYPE_CHECKING, Any, Mapping, Protocol

if TYPE_CHECKING:                       # breaks the tools <-> policy import cycle
    from ..tools import ToolSpec


class Verdict(IntEnum):
    """Composed with max(): a policy can only ever restrict (property P-2)."""
    ALLOW = 0
    ASK   = 1
    DENY  = 2


@value
class Ruling:
    verdict: Verdict
    reason: str
    policy: str


@value
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any]
    spec: "ToolSpec"
    #: S-01 re-check (design/07-risks-and-open-issues.md) — `f"{run_id}:{id}"`
    #: (`idempotency.py::idempotency_key`), stamped by the caller (`dispatch.py`) that
    #: already has both. `None` only in tests that build a `ToolCall` directly without a
    #: run — never in a real dispatch path. A stable per-call key a `Policy`/tool author
    #: can read, distinct from `dispatch.py::_invoke`'s own INTERNAL retry-dedup key
    #: (folds `step` in too, for a reason this field deliberately does not need — see
    #: that call site).
    idempotency_key: str | None = None
    __hash__ = None                     # holds a Mapping — docs/04 §0 Hashability


class Policy(Protocol):
    name: str
    def check(self, call: "ToolCall", ctx: Any) -> Ruling: ...
