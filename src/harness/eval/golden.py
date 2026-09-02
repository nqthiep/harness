"""T-10.2 — golden set + pass rate with a confidence interval, docs/17-research-alignment.md
M10.

"Report pass rate WITH a 95% confidence interval, tokens, cost — never a bare number."
Reuses the same Wilson-score machinery `cost_per_success` (T-8.4) already built and
tested, rather than a second copy of the same interval math.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Sequence

from .._value import value
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

    def __str__(self) -> str:
        failing = [r.case for r in self.results if not r.passed]
        tail = f" — failing: {failing}" if failing else ""
        return (f"{self.n_passed}/{self.n} passed ({self.pass_rate:.0%}, "
                f"{self.ci_low:.0%}-{self.ci_high:.0%} at {self.confidence:.0%} CI), "
                f"${self.total_cost_usd:.4f}, {self.total_tokens} tokens{tail}")


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
                         confidence: float = 0.95) -> GoldenReport:
    if confidence not in _Z_SCORES:
        raise ValueError(f"confidence={confidence!r} not supported — pick one of "
                         f"{sorted(_Z_SCORES)}")
    if not cases:
        raise ValueError("run_golden_set needs at least one case")

    effect_of = {spec.name: spec.effect for spec in agent.toolset}
    results: list[GoldenCaseResult] = []
    for case in cases:
        result, events = await _run_with_events(agent, case.message)
        traj = (check_trajectory(case.contract, result, events, effect_of=effect_of)
               if case.contract is not None else TrajectoryResult(ok=True))
        results.append(GoldenCaseResult(case.name, result, traj))

    n = len(results)
    n_passed = sum(1 for r in results if r.passed)
    z = _Z_SCORES[confidence]
    ci_low, ci_high = _wilson_interval(n_passed, n, z)
    cps = cost_per_success([r.result for r in results], confidence=confidence)
    total_tokens = sum(r.result.usage.total for r in results)

    return GoldenReport(n=n, n_passed=n_passed, pass_rate=n_passed / n,
                        confidence=confidence, ci_low=ci_low, ci_high=ci_high,
                        total_cost_usd=cps.total_cost_usd, total_tokens=total_tokens,
                        results=tuple(results))
