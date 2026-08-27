"""Core value types — docs/04-interfaces.md §0."""
from __future__ import annotations

from ._value import value

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, ClassVar


class Money:
    """A USD amount.  Decimal-backed; never a float (IDL-01)."""

    __slots__ = ("_d",)
    ZERO: ClassVar["Money"]

    def __init__(self, value: "Decimal | int | str | Money") -> None:
        if isinstance(value, Money):
            self._d = value._d
        elif isinstance(value, float):                      # IDL-01
            raise TypeError("Money must not be built from a float; use Decimal or str")
        else:
            self._d = Decimal(value)

    @property
    def decimal(self) -> Decimal:
        return self._d

    def __add__(self, other: "Money") -> "Money": return Money(self._d + other._d)
    def __sub__(self, other: "Money") -> "Money": return Money(self._d - other._d)
    def __lt__(self, other: "Money") -> bool: return self._d < other._d
    def __le__(self, other: "Money") -> bool: return self._d <= other._d
    def __gt__(self, other: "Money") -> bool: return self._d > other._d
    def __ge__(self, other: "Money") -> bool: return self._d >= other._d
    def __eq__(self, other: object) -> bool:
        return isinstance(other, Money) and self._d == other._d
    def __hash__(self) -> int: return hash(self._d)
    def __str__(self) -> str: return f"${self._d:.4f}"
    def __repr__(self) -> str: return f"Money('{self._d}')"


Money.ZERO = Money(0)


@value
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def total(self) -> int:
        return (self.input_tokens + self.output_tokens
                + self.cache_read_input_tokens + self.cache_creation_input_tokens)

    @property
    def cache_hit_ratio(self) -> float:
        sent = self.input_tokens + self.cache_read_input_tokens
        return self.cache_read_input_tokens / sent if sent else 0.0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_input_tokens + other.cache_read_input_tokens,
            self.cache_creation_input_tokens + other.cache_creation_input_tokens,
        )


@value
class Step:
    index: int
    usage: Usage
    cost: Money
    tool_calls: tuple[str, ...]
    duration_ms: float


class StopReason(str, Enum):
    COMPLETED        = "completed"
    TRUNCATED        = "truncated"          # ADR-019
    BUDGET_EXHAUSTED = "budget_exhausted"
    STEP_LIMIT       = "step_limit"
    TIMEOUT          = "timeout"
    DENIED_BY_POLICY = "denied_by_policy"
    MODEL_REFUSAL    = "model_refusal"
    CANCELLED        = "cancelled"
    ERROR            = "error"


@value
class Result:
    text: str
    stop_reason: StopReason
    steps: int
    cost: Money
    usage: Usage
    run_id: str
    tainted: bool = False
    messages: tuple[Any, ...] = ()
    value: object | None = None             # ADR-022
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.stop_reason is StopReason.COMPLETED

    def __str__(self) -> str:               # ADR-014 — print(agent.run(...)) works
        return self.text

    def raise_for_status(self) -> None:
        if not self.ok:
            from .errors import RunFailed
            raise RunFailed(self.detail or f"run stopped: {self.stop_reason.value}", self)
