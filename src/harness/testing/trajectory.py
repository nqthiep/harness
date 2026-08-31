"""T-10.1 — trajectory contract, docs/17-research-alignment.md M10 / S-05.

Một hợp đồng khai báo được, viết TRƯỚC khi chạy, chấm SAU khi chạy — đọc `Result` (đã đủ
cho `must_call`/`must_not_call`/`max_model_calls`/`max_tokens`/`max_cost_usd`/
`output_schema`, không cần đọc lại `Event`) cộng `events` (chỉ cần cho hai luật mà
`Result` không mang đủ dữ liệu: `requires_approval`, `no_duplicate_side_effects`).
Chạy được với `FakeModel` — không cần model thật, không cần I/O, đúng kỷ luật testing của
package này (`docs/09-testing.md`).
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from .._value import value

if TYPE_CHECKING:
    from ..observe.events import Event
    from ..result import Result


def _canonical(args: Mapping[str, Any]) -> str:
    return json.dumps(args, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                      default=str)


@value
class TrajectoryViolation:
    rule: str
    detail: str


@value
class TrajectoryReport:
    violations: tuple[TrajectoryViolation, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.violations

    def __str__(self) -> str:
        if self.ok:
            return "trajectory: OK"
        lines = "\n".join(f"  - [{v.rule}] {v.detail}" for v in self.violations)
        return f"trajectory: {len(self.violations)} vi phạm\n{lines}"


@value
class Trajectory:
    """docs/17 §260 T-10.1: `must_call`, `must_not_call`, `requires_approval`,
    `max_model_calls`, `max_tokens`, `max_cost`, `output_schema`,
    `no_duplicate_side_effects`.
    """
    must_call: frozenset[str] = frozenset()
    must_not_call: frozenset[str] = frozenset()
    requires_approval: frozenset[str] = frozenset()
    max_model_calls: int | None = None
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    output_schema: type | None = None
    no_duplicate_side_effects: bool = False

    def check(
        self,
        result: "Result",
        events: Sequence["Event"] = (),
        *,
        effect_of: Mapping[str, str] | None = None,
    ) -> TrajectoryReport:
        """`effect_of`: tên tool -> `"read"|"write"|"external"|"danger"` — bắt buộc chỉ
        khi `no_duplicate_side_effects=True` (cần biết tool nào là side-effecting để so
        khớp; `must_call`/`must_not_call` không cần, chúng chỉ so tên).
        """
        v: list[TrajectoryViolation] = []
        called = set(result.tools_run)

        missing = self.must_call - called
        if missing:
            v.append(TrajectoryViolation("must_call", f"chưa gọi: {sorted(missing)}"))

        forbidden = self.must_not_call & called
        if forbidden:
            v.append(TrajectoryViolation("must_not_call",
                                         f"đã gọi dù bị cấm: {sorted(forbidden)}"))

        # `Result.steps` đếm từ 0 (step cuối cùng chạy), nên số LƯỢT gọi model thật là
        # steps + 1 — một run một lượt duy nhất có steps=0, không phải 1.
        model_calls = result.steps + 1
        if self.max_model_calls is not None and model_calls > self.max_model_calls:
            v.append(TrajectoryViolation(
                "max_model_calls", f"{model_calls} > {self.max_model_calls}"))

        if self.max_tokens is not None and result.usage.total > self.max_tokens:
            v.append(TrajectoryViolation(
                "max_tokens", f"{result.usage.total} > {self.max_tokens}"))

        if self.max_cost_usd is not None:
            cost = float(result.cost.decimal)
            if cost > self.max_cost_usd:
                v.append(TrajectoryViolation(
                    "max_cost_usd", f"${cost:.4f} > ${self.max_cost_usd:.4f}"))

        if self.output_schema is not None and not isinstance(result.value, self.output_schema):
            v.append(TrajectoryViolation(
                "output_schema",
                f"result.value là {type(result.value).__name__}, cần {self.output_schema.__name__}"))

        if self.requires_approval:
            if not events:
                raise ValueError(
                    "Trajectory.requires_approval cần events= (policy.decided) — "
                    "Result một mình không mang lại quyết định policy")
            # `policy.decided` được phát SAU khi `PolicyEngine.resolve()` đã đổi ASK thành
            # ALLOW/DENY (dispatch.py/lg/runtime.py) — verdict lúc đó không còn là "ASK"
            # nữa. `policy="approval"` mới là dấu vết đúng: `resolve()` (policy/engine.py)
            # gắn đúng tên đó cho MỌI lần thật sự đi qua vòng approve(), bất kể ALLOW hay
            # DENY sau đó — chứ không phải "kết quả cuối cùng còn ASK", điều không tồn tại.
            asked = {str(e.data.get("tool")) for e in events
                    if e.kind.value == "policy.decided" and e.data.get("policy") == "approval"}
            never_asked = (self.requires_approval & called) - asked
            if never_asked:
                v.append(TrajectoryViolation(
                    "requires_approval",
                    f"chạy mà không qua ASK lần nào: {sorted(never_asked)}"))

        if self.no_duplicate_side_effects:
            if effect_of is None:
                raise ValueError(
                    "Trajectory.no_duplicate_side_effects cần effect_of= (tên tool -> "
                    "effect) — không có cách nào biết tool nào side-effecting nếu không")
            seen: dict[tuple[str, str], int] = {}
            for e in events:
                if e.kind.value != "tool.requested" or e.data.get("duplicate_of") is not None:
                    continue
                name = e.data.get("tool")
                if not isinstance(name, str) or effect_of.get(name) not in ("write", "danger"):
                    continue
                key = (name, _canonical(e.data.get("arguments", {})))
                if key in seen:
                    v.append(TrajectoryViolation(
                        "no_duplicate_side_effects",
                        f"{name} gọi lại với cùng tham số ({e.data.get('arguments')})"))
                seen[key] = seen.get(key, 0) + 1

        return TrajectoryReport(tuple(v))
