"""T-10.2 — golden set + pass rate with a confidence interval, docs/17-research-alignment.md
M10.

"Report pass rate WITH a 95% confidence interval, tokens, cost — never a bare number."
Reuses the same Wilson-score machinery `cost_per_success` (T-8.4) already built and
tested, rather than a second copy of the same interval math.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Sequence

from .._value import value
from ..budget.ledger import Budget, Ledger
from ..errors import BudgetExceeded
from .cost import _Z_SCORES, _wilson_interval, cost_per_success
from .trajectory import Trajectory, TrajectoryResult, check_trajectory

if TYPE_CHECKING:
    from ..agent import Agent
    from ..result import Result


@value
class GoldenCase:
    """One task in the set. `contract` is optional — a case with none only asserts the
    run completed (`Result.ok`); most of §9's negative/adversarial/failure cases want a
    `Trajectory` (e.g. `must_not_call` the dangerous tool an injection tried to trigger).
    """
    name: str
    message: str
    contract: Trajectory | None = None


@value
class GoldenCaseResult:
    case: str
    result: "Result"
    trajectory: TrajectoryResult

    @property
    def passed(self) -> bool:
        return self.result.ok and self.trajectory.ok


@value
class GoldenReport:
    n: int
    n_passed: int
    pass_rate: float
    confidence: float
    ci_low: float
    ci_high: float
    total_cost_usd: float
    total_tokens: int
    results: tuple[GoldenCaseResult, ...]
    #: G-14, design/review-architect.md — `run_golden_set` stopped running FURTHER cases
    #: because `total_budget` (an aggregate ceiling across the whole set, not any one
    #: case's own `agent.budget`) ran out. `True` means `results` covers a PREFIX of
    #: `cases`, not all of them — `n`/`n_passed`/`pass_rate` are honest about that
    #: prefix, never silently padded or extrapolated to look like a full run.
    budget_exhausted: bool = False

    def __str__(self) -> str:
        failing = [r.case for r in self.results if not r.passed]
        tail = f" — failing: {failing}" if failing else ""
        cut = " (STOPPED EARLY: total_budget exhausted)" if self.budget_exhausted else ""
        return (f"{self.n_passed}/{self.n} passed ({self.pass_rate:.0%}, "
                f"{self.ci_low:.0%}-{self.ci_high:.0%} at {self.confidence:.0%} CI), "
                f"${self.total_cost_usd:.4f}, {self.total_tokens} tokens{tail}{cut}")


class _Collect:
    """Same seam every other per-call collector in this codebase uses (`_SseExporter`,
    `Agent.stream()`'s `_QueueExporter`) — an `Exporter` bound via `with_()` so a golden
    run's events are captured without mutating the `Agent` under test."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    def emit(self, event: Any) -> None:
        self.events.append(event)

    def close(self) -> None: ...


async def _run_with_events(agent: "Agent", message: str):
    collector = _Collect()
    bound = agent.with_(exporters=tuple(agent.exporters) + (collector,))
    result = await bound.atry_run(message)
    return result, collector.events


async def run_golden_set(agent: "Agent", cases: "Sequence[GoldenCase]", *,
                         confidence: float = 0.95,
                         total_budget: "Budget | str | None" = None) -> GoldenReport:
    """`total_budget` — G-14, design/review-architect.md: an aggregate ceiling across
    the WHOLE set, independent of any one case's own `agent.budget` (which only ever
    bounded a single run). Before this parameter existed, nothing did — a golden set of
    a thousand cases against a misconfigured agent could spend a thousand times a single
    case's budget with no way to stop early. `None` (the default) changes nothing: no
    aggregate ledger is built, every case runs exactly as it did before this parameter
    existed — this is an additive safety net, not a new requirement every existing
    caller must now satisfy. Passing an explicit `Budget`/string builds one shared
    `Ledger`, charged (`Ledger.charge`, the same mechanism a sub-agent's spend already
    reports to its parent's ledger) with each case's real `Result.cost` after it runs;
    once the ledger's remaining budget hits zero, the sweep stops BEFORE the next case
    runs and `GoldenReport.budget_exhausted` is `True` — a report over a prefix of
    `cases`, marked as such, never silently padded to look like a full run.
    """
    if confidence not in _Z_SCORES:
        raise ValueError(f"confidence={confidence!r} not supported — pick one of "
                         f"{sorted(_Z_SCORES)}")
    if not cases:
        raise ValueError("run_golden_set needs at least one case")

    ledger = Ledger(Budget.parse(total_budget)) if total_budget is not None else None
    effect_of = {spec.name: spec.effect for spec in agent.toolset}
    results: list[GoldenCaseResult] = []
    budget_exhausted = False
    for case in cases:
        if ledger is not None:
            remaining = ledger.remaining_usd()
            if remaining is not None and remaining.decimal <= 0:
                budget_exhausted = True
                break
        result, events = await _run_with_events(agent, case.message)
        traj = (check_trajectory(case.contract, result, events, effect_of=effect_of)
               if case.contract is not None else TrajectoryResult(ok=True))
        results.append(GoldenCaseResult(case.name, result, traj))
        if ledger is not None:
            ledger.charge(result.cost)

    if not results:
        raise BudgetExceeded(
            f"total_budget={total_budget!r} ran out before even the first of "
            f"{len(cases)} case(s) could run — nothing to report.\n\n"
            f"  Pass a larger total_budget=, or drop it to run every case unbounded.\n\n"
            f"  -> design/review-architect.md G-14"
        )
    n = len(results)
    n_passed = sum(1 for r in results if r.passed)
    z = _Z_SCORES[confidence]
    ci_low, ci_high = _wilson_interval(n_passed, n, z)
    cps = cost_per_success([r.result for r in results], confidence=confidence)
    total_tokens = sum(r.result.usage.total for r in results)

    return GoldenReport(n=n, n_passed=n_passed, pass_rate=n_passed / n,
                        confidence=confidence, ci_low=ci_low, ci_high=ci_high,
                        total_cost_usd=cps.total_cost_usd, total_tokens=total_tokens,
                        results=tuple(results), budget_exhausted=budget_exhausted)
