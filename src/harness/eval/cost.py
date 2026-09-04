"""T-8.4 — cost per successful task, docs/17-research-alignment.md M8 / S-06.

"Cost/successful task, không phải cost/task": a run that fails is not free — the
provider was still paid for the tokens it burned — so the honest denominator is not
"total runs" but "runs that actually succeeded". Reporting cost/task alone rewards a
policy that succeeds rarely but cheaply per attempt over one that succeeds reliably at
a slightly higher per-attempt cost — exactly the wrong axis to optimize, and exactly
the axis a bare average invites.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence


class Scored(Protocol):
    """What this module actually needs from a run: an outcome and a cost.

    A `Protocol`, not `Result`, because the docstrings here have always promised the
    structural contract — "any object shaped the same way (a test double, a record read
    back from a golden-set run) works too" — while the ANNOTATION said `Sequence[Result]`.
    mypy proved the mismatch on the exact use the docstring blesses:
    `examples/coding_bench.py` passes a `list[_Scored]` whose own docstring says the
    shape is supported deliberately, and got
    `Argument 1 to "cost_per_success" has incompatible type` (ADR-087).

    Read-only properties rather than plain attribute declarations: `Result` is a frozen
    `@value` class, and a protocol declaring `ok: bool` demands a settable one.

    `cost` is `Any` on purpose — `_cost_of` accepts `Money`-shaped (`.decimal`), a plain
    number, or a `"$1.23"` string, and narrowing the annotation here would reject two of
    the three the function handles.
    """

    @property
    def ok(self) -> bool: ...

    @property
    def cost(self) -> Any: ...

#: z-scores for the confidence levels this function actually supports — a lookup
#: table, not `scipy.stats.norm.ppf`, so this stays a stdlib-only module (NFR-05: core
#: dependencies are budgeted, and eval tooling should not need a new one for a
#: four-value table any statistics reference already has memorized).
_Z_SCORES = {0.80: 1.2816, 0.90: 1.6449, 0.95: 1.9600, 0.99: 2.5758}


@dataclass(frozen=True)
class CostPerSuccess:
    total_cost_usd: float
    n_runs: int
    n_success: int
    success_rate: float
    confidence: float
    #: `None` when `n_success == 0` — cost per success is UNDEFINED, not infinite and
    #: not zero, when nothing succeeded. Money was still spent; reporting a number
    #: here would be exactly the "confident empty answer" IDL-30/fail-visible exists
    #: to prevent.
    cost_per_success_usd: float | None
    ci_low_usd: float | None
    ci_high_usd: float | None

    def __str__(self) -> str:
        if self.cost_per_success_usd is None:
            return (f"${self.total_cost_usd:.4f} spent across {self.n_runs} run(s), "
                    f"0 succeeded — cost per success is undefined")
        high = "∞" if self.ci_high_usd == float("inf") else f"${self.ci_high_usd:.4f}"
        return (f"${self.cost_per_success_usd:.4f} per success "
                f"(${self.ci_low_usd:.4f}–{high} at {self.confidence:.0%} CI, "
                f"{self.n_success}/{self.n_runs} succeeded)")


def _wilson_interval(successes: int, n: int, z: float) -> tuple[float, float]:
    """The Wilson score interval for a binomial proportion — behaves sanely at small
    `n` and extreme proportions, where the normal approximation
    (`p ± z·sqrt(p(1-p)/n)`) can produce a bound outside `[0, 1]`."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = (z / denom) * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (max(0.0, center - margin), min(1.0, center + margin))


def _cost_of(run: Scored) -> float:
    """Accepts anything with a `.cost` that is `Money`-shaped (has `.decimal`) or a
    plain number/`$`-prefixed string — decoupled from a hard `Money` import so a test
    double doesn't need to construct a real one."""
    cost = run.cost
    if hasattr(cost, "decimal"):
        return float(cost.decimal)
    if isinstance(cost, (int, float)):
        return float(cost)
    return float(str(cost).lstrip("$"))


def cost_per_success(runs: "Sequence[Scored]", *, confidence: float = 0.95) -> CostPerSuccess:
    """`total_cost / P(success)` — never a bare number, always accompanied by a
    confidence interval (§45: "thà nói 'chưa đủ evidence' còn hơn đoán" applies to a
    single point estimate over a small sample exactly as much as it applies to a
    missing measurement).

    The interval is Wilson-scored on the underlying success RATE, then propagated to
    a range on cost-per-success — a decreasing function of the success rate, so the
    rate's LOW bound gives cost-per-success's HIGH bound and vice versa.

    `runs` needs only `.ok: bool` and a `Money`-shaped-or-numeric `.cost`, which is now
    what the signature SAYS as well as what the body does: `Scored` above. `Result`
    satisfies it structurally, with no import of it here at all, and so does any object
    shaped the same way (a test double, a record read back from a golden-set run).
    """
    if confidence not in _Z_SCORES:
        raise ValueError(
            f"confidence={confidence!r} not supported — pick one of "
            f"{sorted(_Z_SCORES)} (Wilson-interval z-scores are looked up, not "
            f"computed, to keep this module free of a scipy dependency)")
    if not runs:
        raise ValueError("cost_per_success needs at least one run")

    n = len(runs)
    n_success = sum(1 for r in runs if r.ok)
    total_cost = sum(_cost_of(r) for r in runs)
    success_rate = n_success / n
    z = _Z_SCORES[confidence]
    lo, hi = _wilson_interval(n_success, n, z)

    if n_success == 0:
        return CostPerSuccess(total_cost, n, n_success, success_rate, confidence,
                              None, None, None)

    cost_per = total_cost / success_rate
    ci_high = total_cost / lo if lo > 0 else float("inf")
    ci_low = total_cost / hi if hi > 0 else float("inf")
    return CostPerSuccess(total_cost, n, n_success, success_rate, confidence,
                          cost_per, ci_low, ci_high)
