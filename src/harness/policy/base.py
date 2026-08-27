"""Verdict lattice and Policy protocol — docs/04-interfaces.md §3.

Verdict lives here rather than in tools/ because EFFECT_PROFILES needs it and
policy/ must not import tools/ at runtime (see the TYPE_CHECKING guard below):
tools/__init__.py imports Verdict, so the reverse import would be a cycle.
"""
from __future__ import annotations

from .._value import value

from dataclasses import dataclass
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
class Decision:
    verdict: Verdict
    reason: str
    policy: str


@value
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, Any]
    spec: "ToolSpec"
    __hash__ = None                     # holds a Mapping — docs/04 §0 Hashability


class Policy(Protocol):
    name: str
    def check(self, call: "ToolCall", ctx: Any) -> Decision: ...
