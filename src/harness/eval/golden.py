"""T-10.2 — golden set + pass rate có CI, docs/17-research-alignment.md M10.

"Task set đại diện + negative case + adversarial prompt + tool failure + policy
violation. Báo cáo pass rate kèm khoảng tin cậy 95%, tokens, cost — không bao giờ một con
số trần trụi." `GoldenCase.kind` gắn nhãn năm loại đó; framework này không tự bịa một bộ
case cụ thể (đó là nội dung của người dùng thư viện) — nó là hạ tầng ĐO, cùng công thức
Wilson-score `cost_per_success` (T-8.4) đã dùng, không viết công thức khoảng tin cậy lần
thứ hai trong cùng package.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Mapping, Sequence

from .cost import _Z_SCORES, _wilson_interval
from ..testing.trajectory import Trajectory, TrajectoryReport

if TYPE_CHECKING:
    from ..agent import Agent
    from ..result import Result

#: Năm loại case docs/17 T-10.2 liệt kê — chỉ để gắn nhãn báo cáo (breakdown theo kind),
#: không ràng buộc gì thêm. Một `GoldenCase` không thuộc năm loại này vẫn hợp lệ.
CASE_KINDS = ("positive", "negative", "adversarial", "tool_failure", "policy_violation")


@dataclass(frozen=True)
class GoldenCase:
    name: str
    message: str
    kind: str = "positive"
    trajectory: Trajectory | None = None
    #: Chỉ cần khi `trajectory.requires_approval`/`.no_duplicate_side_effects` được đặt —
    #: xem `Trajectory.check`.
    effect_of: Mapping[str, str] | None = None


@dataclass(frozen=True)
class GoldenCaseResult:
    case: GoldenCase
    result: "Result"
    trajectory_report: TrajectoryReport | None

    @property
    def passed(self) -> bool:
        if not self.result.ok:
            return False
        if self.trajectory_report is not None:
            return self.trajectory_report.ok
        return True


@dataclass(frozen=True)
class GoldenReport:
    cases: tuple[GoldenCaseResult, ...]
    confidence: float
    pass_rate: float
    ci_low: float
    ci_high: float
    total_tokens: int
    total_cost_usd: float

    @property
    def n(self) -> int:
        return len(self.cases)

    @property
    def n_passed(self) -> int:
        return sum(1 for c in self.cases if c.passed)

    def by_kind(self) -> dict[str, tuple[int, int]]:
        """`{kind: (passed, total)}` — breakdown, vì một pass rate gộp có thể che một
        loại case (vd. adversarial) fail 100% trong khi positive fail 0%."""
        out: dict[str, list[int]] = {}
        for c in self.cases:
            bucket = out.setdefault(c.case.kind, [0, 0])
            bucket[1] += 1
            if c.passed:
                bucket[0] += 1
        return {k: (p, t) for k, (p, t) in out.items()}

    def __str__(self) -> str:
        hi = "100%" if self.ci_high >= 1.0 else f"{self.ci_high:.1%}"
        lines = [f"{self.n_passed}/{self.n} pass ({self.pass_rate:.1%}, "
                f"{self.ci_low:.1%}-{hi} tại {self.confidence:.0%} CI) — "
                f"{self.total_tokens} token, ${self.total_cost_usd:.4f}"]
        for kind, (p, t) in sorted(self.by_kind().items()):
            lines.append(f"  {kind}: {p}/{t}")
        return "\n".join(lines)


def _cost_of(result: "Result") -> float:
    return float(result.cost.decimal)


async def run_golden_set(
    agent_factory: Callable[[], "Agent"],
    cases: Sequence[GoldenCase],
    *,
    confidence: float = 0.95,
) -> GoldenReport:
    """Chạy MỖI case qua một `Agent` MỚI (`agent_factory()` gọi lại từng case) — tránh
    state rò giữa case, cùng lý do policy factory phải mới mỗi run (Round 34): một golden
    set đo ĐÚNG nghĩa "case này qua độc lập", không phải "case này qua sau khi case trước
    đã hâm nóng cache/ledger/policy".
    """
    if confidence not in _Z_SCORES:
        raise ValueError(f"confidence={confidence!r} không được hỗ trợ — chọn một trong "
                         f"{sorted(_Z_SCORES)}")
    if not cases:
        raise ValueError("run_golden_set cần ít nhất một case")

    results = []
    for case in cases:
        agent = agent_factory()

        events: list[Any] = []

        class _Collector:
            def emit(self, event: Any) -> None:
                events.append(event)

            def close(self) -> None:
                pass

        collected = agent.with_(exporters=tuple(agent.exporters) + (_Collector(),))
        result = await collected.atry_run(case.message)
        report = (case.trajectory.check(result, events, effect_of=case.effect_of)
                  if case.trajectory is not None else None)
        results.append(GoldenCaseResult(case, result, report))

    n = len(results)
    n_passed = sum(1 for r in results if r.passed)
    pass_rate = n_passed / n
    z = _Z_SCORES[confidence]
    lo, hi = _wilson_interval(n_passed, n, z)
    total_tokens = sum(r.result.usage.total for r in results)
    total_cost = sum(_cost_of(r.result) for r in results)

    return GoldenReport(tuple(results), confidence, pass_rate, lo, hi,
                        total_tokens, total_cost)
