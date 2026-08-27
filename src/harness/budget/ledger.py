"""Budget and Ledger — docs/04-interfaces.md §4, task T-1.5.

The ceiling holds because max_tokens is DERIVED from what is left (ADR-017), so the
worst-case reservation can never exceed the budget.  Before that fix, budget="$0.05"
against a default max_tokens=16000 reserved $0.406 and refused to make any call.
"""
from __future__ import annotations

from .._value import value

import re
import time
import uuid
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Final

from ..errors import BudgetExceeded, InvalidBudgetError
from ..result import Money, Usage

MIN_USEFUL_OUTPUT_TOKENS: Final = 256      # IDL-28 — below this a truncated answer is not one

#: Applied to the counted input on every reservation.  count_input_tokens is an estimate,
#: and the worst-case output ceiling does nothing to absorb an input under-count (ADR-026).
INPUT_MARGIN: Final = Decimal("1.15")


@value
class Budget:
    usd: Decimal | None = Decimal("0.50")
    steps: int = 20
    wall_clock_s: float = 300.0
    tokens: int | None = None

    @classmethod
    def parse(cls, value: "Budget | str | None") -> "Budget":
        if value is None:
            return DEFAULT_BUDGET
        if isinstance(value, Budget):
            return value
        if not isinstance(value, str):
            raise InvalidBudgetError(
                f"budget must be a Budget, a string like \"$0.10\", or None — got {type(value).__name__}"
            )
        b = DEFAULT_BUDGET
        matched = False
        for part in value.split(","):
            part = part.strip().lower()
            if not part:
                continue
            if m := re.fullmatch(r"\$?\s*([\d.]+)\s*(?:usd|dollars?)?", part):
                b = replace(b, usd=Decimal(m.group(1))); matched = True
            elif m := re.fullmatch(r"([\d.]+)\s*cents?", part):
                b = replace(b, usd=Decimal(m.group(1)) / 100); matched = True
            elif m := re.fullmatch(r"(\d+)\s*steps?", part):
                b = replace(b, steps=int(m.group(1))); matched = True
            elif m := re.fullmatch(r"([\d.]+)\s*m(?:in|ins|inutes)?", part):
                b = replace(b, wall_clock_s=float(m.group(1)) * 60); matched = True
            elif m := re.fullmatch(r"([\d.]+)\s*s(?:ec|ecs|econds)?", part):
                b = replace(b, wall_clock_s=float(m.group(1))); matched = True
            else:
                raise InvalidBudgetError(
                    f"could not read budget {part!r}.\n\n"
                    f'  Try:  "$0.10"  ·  "10 cents"  ·  "5 steps"  ·  "$1, 50 steps, 10m"\n\n'
                    f"  -> docs/07-cost.md#1-the-budget-is-a-ceiling-not-an-alert"
                )
        if not matched:
            raise InvalidBudgetError(f"budget {value!r} set no limit")
        return b


DEFAULT_BUDGET: Final = Budget()


@value
class Reservation:
    id: str
    estimate: Money
    input_tokens: int
    max_tokens: int
    created_at: float


class Ledger:
    """Enforces the budget.  reserve() runs immediately before every model call."""

    def __init__(self, budget: Budget, *, clock=time.monotonic) -> None:
        self._b = budget
        self._spent = Money.ZERO
        self._steps = 0
        self._clock = clock
        self._started = clock()
        self._open: dict[str, Reservation] = {}
        self._calibration = Decimal(1)      # observed actual_input / counted_input
        self._overshoot = Money.ZERO
        self._blocked = False
        self._last_exact = False

    # -- state ------------------------------------------------------------
    @property
    def spent(self) -> Money: return self._spent
    @property
    def budget(self) -> Budget: return self._b
    @property
    def steps_taken(self) -> int: return self._steps

    def remaining_usd(self) -> Money | None:
        if self._b.usd is None:
            return None
        return Money(self._b.usd) - self._spent

    def remaining_steps(self) -> int: return self._b.steps - self._steps
    def remaining_wall_clock(self) -> float:
        return max(0.0, self._b.wall_clock_s - (self._clock() - self._started))

    def count_step(self) -> None: self._steps += 1

    # -- the ceiling ------------------------------------------------------
    def _adjusted(self, input_tokens: int) -> Decimal:
        return Decimal(input_tokens) * INPUT_MARGIN * self._calibration

    @property
    def last_call_was_exactly_bounded(self) -> bool:
        """True when the reservation used a hard upper bound rather than an estimate."""
        return self._last_exact

    @property
    def overshoot(self) -> Money:
        """How far a settled call pushed spend past the budget.  Bounded by one call."""
        return self._overshoot

    def size_call(self, input_tokens: int, price, model_max: int) -> int:
        """Derive max_tokens from what is left (ADR-017)."""
        if self._blocked:
            raise BudgetExceeded(
                f"budget of {Money(self._b.usd)} is spent ({self.spent}); no further calls"
            )
        if self._b.usd is None or price.output_per_mtok == 0:
            # A free provider (FakeModel, a local model) has nothing to divide by and
            # nothing to spend.  Round 24: without this, the zero-cost testing story
            # required by SC-5 crashes the ledger ADR-017 introduced.
            return model_max
        remaining = self.remaining_usd()
        assert remaining is not None
        input_cost = Money(self._adjusted(input_tokens) / 1_000_000 * price.input_per_mtok)
        affordable_usd = remaining - input_cost
        if affordable_usd.decimal <= 0:
            raise BudgetExceeded(
                f"the input alone costs more than the {Money(self._b.usd)} budget has left"
            )
        affordable = int(affordable_usd.decimal / price.output_per_mtok * 1_000_000)
        if affordable < MIN_USEFUL_OUTPUT_TOKENS:
            raise BudgetExceeded(
                f"only {affordable} output tokens are affordable, below the "
                f"{MIN_USEFUL_OUTPUT_TOKENS}-token floor — a truncated answer is not an answer"
            )
        return min(affordable, model_max)

    def reserve(self, input_tokens: int, max_tokens: int, price,
                *, hard_max_input: int | None = None) -> Reservation:
        """hard_max_input: a locally computable UPPER bound on the true input token
        count — the request's character count, since no tokenizer emits more tokens
        than characters.  When even that bound fits, the ceiling is exact for this
        call rather than merely estimated (ADR-026)."""
        estimated_in = self._adjusted(input_tokens)
        exact = False
        if hard_max_input is not None:
            hard = Money(
                Decimal(hard_max_input) / 1_000_000 * price.input_per_mtok
                + Decimal(max_tokens) / 1_000_000 * price.output_per_mtok)
            if self._b.usd is None or (self._spent + hard).decimal <= self._b.usd:
                estimated_in = Decimal(hard_max_input)   # the bound fits: use it, be exact
                exact = True
        est = Money(
            estimated_in / 1_000_000 * price.input_per_mtok
            + Decimal(max_tokens) / 1_000_000 * price.output_per_mtok
        )
        self._last_exact = exact
        if self._b.usd is not None and (self._spent + est).decimal > self._b.usd:
            raise BudgetExceeded(
                f"this call could cost {est}, and only {self.remaining_usd()} is left"
            )
        r = Reservation(uuid.uuid4().hex, est, input_tokens, max_tokens, self._clock())
        self._open[r.id] = r
        return r

    def settle(self, reservation: Reservation, usage: Usage, price) -> Money:
        self._open.pop(reservation.id, None)
        actual = Money(
            Decimal(usage.input_tokens) / 1_000_000 * price.input_per_mtok
            + Decimal(usage.cache_read_input_tokens) / 1_000_000 * price.cache_read_per_mtok
            + Decimal(usage.cache_creation_input_tokens) / 1_000_000 * price.cache_write_per_mtok
            + Decimal(usage.output_tokens) / 1_000_000 * price.output_per_mtok
        )
        self._spent = self._spent + actual

        # Calibrate: count_input_tokens is an estimate, and the next call should know by
        # how much it was wrong.  Ratchets upward only — an under-count is the dangerous
        # direction, an over-count merely wastes a little headroom (ADR-026).
        billed_in = usage.input_tokens + usage.cache_read_input_tokens
        if reservation.input_tokens > 0 and billed_in > 0:
            observed = Decimal(billed_in) / Decimal(reservation.input_tokens)
            self._calibration = max(self._calibration, observed)

        # Hard stop: the authorization ceiling is exact, the spend ceiling is bounded by
        # this one call.  Once crossed, nothing further is authorized.
        if self._b.usd is not None and self._spent.decimal > self._b.usd:
            self._overshoot = Money(self._spent.decimal - self._b.usd)
            self._blocked = True
        return actual

    def hold(self, amount: Money) -> Money:
        """Claim headroom up front for work that will spend on another ledger.

        Parallel subagents each READ the same remaining budget and each claimed all of
        it — a TOCTOU on the ledger that let N children spend N x the headroom
        (Round 28).  A hold makes them divide it instead.  Returns the amount actually
        held, which may be less than requested.
        """
        remaining = self.remaining_usd()
        if remaining is None:
            return amount
        held = Money(min(amount.decimal, max(remaining.decimal, 0)))
        self._spent = self._spent + held
        return held

    def release(self, held: Money, actual: Money) -> None:
        """Replace a hold with what was really spent."""
        self._spent = self._spent - held + actual
        if self._b.usd is not None and self._spent.decimal > self._b.usd:
            self._overshoot = Money(self._spent.decimal - self._b.usd)
            self._blocked = True

    def charge(self, amount: Money) -> None:
        """Record spend that happened on another ledger — a subagent's run.

        Without this a subagent's cost is invisible to its parent: the parent reports
        $0.0000 and each successive child call sees the full remaining budget (Round 28).
        """
        if amount.decimal <= 0:
            return
        self._spent = self._spent + amount
        if self._b.usd is not None and self._spent.decimal > self._b.usd:
            self._overshoot = Money(self._spent.decimal - self._b.usd)
            self._blocked = True

    def tool_timeout(self, spec_timeout_s: float) -> float:
        """Round 23: clamp to the wall clock, or the ceiling overshoots by timeout_s."""
        return min(spec_timeout_s, self.remaining_wall_clock())
