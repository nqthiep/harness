"""Per-model prices — task T-2.1.  Never returns zero for an unknown model."""
from __future__ import annotations

from .._value import value

from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from ..errors import UnknownModelError

AS_OF: Final = "2026-06-24"


@value
class Price:
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cache_write_per_mtok: Decimal
    cache_read_per_mtok: Decimal


def _p(i: str, o: str) -> Price:
    return Price(Decimal(i), Decimal(o), Decimal(i) * Decimal("1.25"), Decimal(i) * Decimal("0.1"))


PRICES: Final[dict[str, Price]] = {
    "claude-opus-5":     _p("5", "25"),
    "claude-opus-4-8":   _p("5", "25"),
    "claude-sonnet-5":   _p("2", "10"),
    "claude-haiku-4-5":  _p("1", "5"),
    "claude-fable-5":    _p("10", "50"),
    "fake":              Price(Decimal(0), Decimal(0), Decimal(0), Decimal(0)),
}

MAX_CONTEXT: Final[dict[str, int]] = {
    "claude-opus-5": 1_000_000, "claude-opus-4-8": 1_000_000, "claude-sonnet-5": 1_000_000,
    "claude-haiku-4-5": 200_000, "claude-fable-5": 1_000_000, "fake": 200_000,
}
MAX_OUTPUT: Final[dict[str, int]] = {
    "claude-opus-5": 128_000, "claude-opus-4-8": 128_000, "claude-sonnet-5": 128_000,
    "claude-haiku-4-5": 64_000, "claude-fable-5": 128_000, "fake": 8_000,
}


def price(model: str) -> Price:
    try:
        return PRICES[model]
    except KeyError:
        raise UnknownModelError(
            f"no price is known for model {model!r}, so the budget ceiling cannot be\n"
            f"enforced. Refusing to call it rather than treating it as free.\n\n"
            f"  Known: {', '.join(sorted(k for k in PRICES if k != 'fake'))}\n\n"
            f"  -> docs/07-cost.md"
        ) from None
