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
from dataclasses import replace
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

    # -- serialisable state ------------------------------------------------
    #
    # A ledger's accumulated state is four numbers, and on a checkpointed backend it has
    # to live in graph state rather than in memory: LangGraph runs every node in its own
    # copied context, so nothing an object holds survives from one node to the next, and
    # a ledger held on a long-lived object is shared by every conversation it serves
    # (Round 37).  Round-tripping through these two methods keeps spend, the step count
    # and ADR-026's upward-ratcheting calibration per conversation and across restarts.
    def snapshot(self) -> dict:
        return {"spent": str(self._spent.decimal), "steps": self._steps,
                "calibration": str(self._calibration),
                "overshoot": str(self._overshoot.decimal),
                "blocked": self._blocked}

    def restore(self, snap: dict | None) -> "Ledger":
        if not snap:
            return self
        try:
            self._spent = Money(Decimal(str(snap.get("spent", "0"))))
            self._steps = int(snap.get("steps", 0))
            self._calibration = max(Decimal(1), Decimal(str(snap.get("calibration", "1"))))
            self._overshoot = Money(Decimal(str(snap.get("overshoot", "0"))))
            # S-21: `_blocked` used to live only on the object, so checkpoint + resume
            # (the LangGraph backend rebuilds a `Ledger` from `snapshot()` on every node,
            # design/04-runtime-durability.md S-1: `restore(snapshot(x)) == x`) silently
            # un-blocked a ledger whose spend had already crossed the hard ceiling.
            self._blocked = bool(snap.get("blocked", False))
        except (ArithmeticError, TypeError, ValueError):
            pass                    # a corrupt checkpoint starts clean rather than crashing
        return self

    # -- state ------------------------------------------------------------
    @property
    def spent(self) -> Money: return self._spent
    @property
    def budget(self) -> Budget: return self._b
    @property
    def steps_taken(self) -> int:
        """Model calls billed so far — the unit `Budget(steps=)` is a ceiling over.

        Deleted in ADR-113's sweep as dead code, which it then was: nothing read it.
        Restored here with a consumer, because it is the unit `Result.steps` should have
        been reporting all along (ADR-118). `count_step()` fires once per model call, so
        this and `remaining_steps()` are the two ends of the same count.
        """
        return self._steps

    def _committed(self) -> Money:
        """Chi tiêu THẬT cộng mọi reservation đang mở, chưa `settle()`.

        Trước bản vá này, `self._open` được ghi vào (`reserve()`) và đọc ra để pop
        (`settle()`), nhưng KHÔNG bao giờ được CỘNG vào đâu cả — `remaining_usd()` chỉ
        trừ `self._spent`. Vô hại hôm nay vì không chỗ nào trong repo gọi `reserve()`
        hai lần trước khi cái đầu `settle()`. Nhưng đó là an toàn NHỜ HÀNH VI GỌI, không
        nhờ bất biến cấu trúc — đúng thứ R-2 đòi phải khác:
        "worst case của một reservation không bao giờ vượt ngân sách" (05 A.1) chỉ đúng
        cho MỘT reservation nếu không cái này. Một `Retry` plugin gọi handler ba lần
        trước một `settle()` nào — chưa tồn tại trong code, nhưng thiết kế có nhắc tới
        (design/review-security.md S-14) — sẽ để ba reservation cùng đọc một
        `remaining_usd()` và cả ba đều "vừa ngân sách" độc lập, nếu hàm này không sửa.
        """
        if not self._open:
            return self._spent
        outstanding = sum((r.estimate.decimal for r in self._open.values()), Decimal(0))
        return self._spent + Money(outstanding)

    def remaining_usd(self) -> Money | None:
        if self._b.usd is None:
            return None
        return Money(self._b.usd) - self._committed()

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
        """How far a settled call pushed spend past the budget. Bounded by one call, TIMES
        the retry attempts actually made (G-8, `design/review-architect.md`) — sửa lại
        docstring này vì `retry.py` (N-5) làm cho câu cũ không còn đúng: một lần gọi có
        thể là tới 5 lần thử THẬT trước khi thất bại hẳn, mỗi lần một chi phí thật."""
        return self._overshoot

    def settle_worst_case(self, reservation: Reservation, attempts: int, price) -> Money:
        """G-8: `reserve()` mở MỘT `Reservation` cho cả chuỗi retry (`with_provider_retry`),
        và `settle()` bình thường chỉ chạy khi lần thử CUỐI thành công. Khi mọi lần thử
        đều thất bại — provider timeout/rate-limit, mỗi lần vẫn có thể đã được vendor
        tính tiền dù client không bao giờ nhận được response — `reservation` bị bỏ lại
        trong `_open` mãi mãi, và `remaining_usd()` báo sai vì `_committed()` vẫn cộng nó.

        Dùng lại ĐÚNG công thức `reserve()` đã dùng để ước lượng một lần gọi
        (`reservation.estimate`: input tính theo giá `cache_write` — nhánh đắt hơn, worst
        case — và `max_tokens` cho output), nhân với `attempts` — một `Usage` giả lập rồi
        gọi `settle()` thật, không phải một đường tính tiền riêng phải giữ đồng bộ với
        công thức kia."""
        worst = Usage(cache_creation_input_tokens=reservation.input_tokens * attempts,
                     output_tokens=reservation.max_tokens * attempts)
        return self.settle(reservation, worst, price)

    def settle_after_retries(self, reservation: Reservation, attempts: int,
                             usage: Usage, price) -> Money:
        """H-1, design/review-architect-round2.md: the SUCCESS-path twin of
        `settle_worst_case()` above. `with_provider_retry()` (retry.py) now returns how
        many real calls it took to succeed, not just how many it took to finally fail —
        `attempts - 1` of those were real, billable (by the vendor) calls that never
        reached this caller at all, exactly the same "vendor may have charged for a
        request the client never got a usable response to" fact `settle_worst_case`
        already accounts for on total failure. Settles BOTH: a worst-case estimate for
        the `attempts - 1` failed attempts (zero-cost, thus a no-op, when `attempts == 1`
        — the common, no-retry case, byte-for-byte the same as a plain `settle()` call),
        then the real `settle()` for the successful call's own actual `usage`. Two
        `settle()` calls against the same `Reservation` is safe by construction:
        `settle()`'s own `self._open.pop(reservation.id, None)` tolerates being called
        more than once, and each call independently adds its own `actual` to `_spent` —
        which is exactly the arithmetic wanted here (worst-case-for-failures PLUS
        real-cost-for-the-win), not a double-count of one amount.
        """
        if attempts > 1:
            self.settle_worst_case(reservation, attempts - 1, price)
        return self.settle(reservation, usage, price)

    def size_call(self, input_tokens: int, price, model_max: int) -> int:
        """Derive max_tokens from what is left (ADR-017)."""
        if self._blocked:
            raise BudgetExceeded(
                # `_blocked` is only ever set inside an `is not None` guard, so the
                # narrowing a checker cannot see is guaranteed by construction.
                f"budget of {Money(self._b.usd)} is spent ({self.spent}); "  # type: ignore[arg-type]
                f"no further calls"
            )
        if self._b.usd is None or price.output_per_mtok == 0:
            # A free provider (FakeModel, a local model) has nothing to divide by and
            # nothing to spend.  Round 24: without this, the zero-cost testing story
            # required by SC-5 crashes the ledger ADR-017 introduced.
            return model_max
        remaining = self.remaining_usd()
        assert remaining is not None
        # S-22: a reservation used to price input at `input_per_mtok` only, but `settle()`
        # bills at up to FOUR rates (input, output, cache read, cache **write** — COST-3
        # above). Prompt caching is exactly the case this harness optimizes for (COST-3's
        # own words), and `cache_write_per_mtok` is always the highest per-token input
        # rate (`_p()` in models/pricing.py: `input * 1.25`) — so a call that writes to
        # cache was UNDER-estimated by a fixed 25%, every time, not a random rounding
        # error. Pricing the worst case restores `reserve()` as a true upper bound.
        input_cost = Money(self._adjusted(input_tokens) / 1_000_000 * price.cache_write_per_mtok)
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
        # S-22: priced at `cache_write_per_mtok`, the worst-case per-token input rate —
        # see `size_call()` above for why `input_per_mtok` alone under-estimates by a
        # fixed, systematic 25% whenever the call actually writes to cache.
        if hard_max_input is not None:
            hard = Money(
                Decimal(hard_max_input) / 1_000_000 * price.cache_write_per_mtok
                + Decimal(max_tokens) / 1_000_000 * price.output_per_mtok)
            if self._b.usd is None or (self._committed() + hard).decimal <= self._b.usd:
                estimated_in = Decimal(hard_max_input)   # the bound fits: use it, be exact
                exact = True
        est = Money(
            estimated_in / 1_000_000 * price.cache_write_per_mtok
            + Decimal(max_tokens) / 1_000_000 * price.output_per_mtok
        )
        self._last_exact = exact
        if self._b.usd is not None and (self._committed() + est).decimal > self._b.usd:
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
        # min/max over two Decimals; the checker widens them to a comparison protocol.
        held = Money(min(amount.decimal, max(remaining.decimal, 0)))  # type: ignore[arg-type]
        self._spent = self._spent + held
        return held

    def release(self, held: Money, actual: Money) -> None:
        """Replace a hold with what was really spent."""
        self._spent = self._spent - held + actual
        if self._b.usd is not None and self._spent.decimal > self._b.usd:
            self._overshoot = Money(self._spent.decimal - self._b.usd)
            self._blocked = True

    def hold_steps(self, want: int) -> int:
        """`hold()`'s TOCTOU fix (Round 28), mở rộng sang trục step — design/07 S-13.

        `_run_subagent` chỉ giữ `usd` trước khi bản vá này; `steps` và `wall_clock_s`
        của con được kế thừa NGUYÊN VẸN từ `Budget` con tự khai, không liên quan gì tới
        số step cha còn lại. Bốn sub-agent spawn trong một lượt, mỗi đứa tự khai
        `steps=20`, tiêu tới 80 step trong khi trần của run gốc chỉ có 20 — đúng lỗi
        TOCTOU mà `hold()` đã sửa cho `usd`, chưa từng sửa cho `steps`. Trả về số step
        THẬT được giữ, có thể ít hơn `want`.
        """
        held = min(want, max(self.remaining_steps(), 0))
        self._steps += held
        return held

    def release_steps(self, held: int, actual: int) -> None:
        """Trả lại phần step không dùng hết — cùng ngữ nghĩa `release()` cho usd."""
        self._steps += actual - held

    def child_wall_clock(self, want: float) -> float:
        """Trần thời gian cho sub-agent — không phải hold/release, vì wall-clock không
        phải một hồ tài nguyên bị chia sẻ (hai con chạy song song không "tốn" thời gian
        gấp đôi của cha): mỗi con chỉ cần không được hứa nhiều thời gian hơn cha THẬT SỰ
        còn lại tại thời điểm spawn — một cận trên, không phải một khoản giữ trước."""
        return min(want, max(self.remaining_wall_clock(), 0.0))

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
