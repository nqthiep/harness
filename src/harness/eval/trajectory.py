"""T-10.1 — trajectory contract, docs/17-research-alignment.md M10.

`must_call`, `must_not_call`, `requires_approval`, `max_model_calls`, `max_tokens`,
`max_cost`, `output_schema`, `no_duplicate_side_effects` — the eight assertions §9 of
the research names. `check_trajectory` is pure: given a `Result` and the `Event` list a
run actually produced, it returns a report. It does not run anything itself — `golden.py`
is the caller that drives a real run and hands both back in.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping, Sequence

from .._value import value

if TYPE_CHECKING:
    from ..observe.events import Event
    from ..result import Result


@value
class Trajectory:
    """Every field is optional — an unset field asserts nothing. A `Trajectory()` with
    no fields set always passes; it exists to be filled in, not to gate by default.
    """
    must_call: frozenset[str] = frozenset()
    must_not_call: frozenset[str] = frozenset()
    #: Tool NAMES that must have gone through the ASK resolution path — not necessarily
    #: been APPROVED (a contract asserting a dangerous action required a decision point
    #: is different from asserting it was allowed; `must_call` already covers "and it
    #: ran").
    requires_approval: frozenset[str] = frozenset()
    max_model_calls: int | None = None
    max_tokens: int | None = None
    max_cost_usd: float | None = None
    #: JSON Schema (the same `jsonschema` this package already depends on core-side —
    #: no new dependency for eval tooling) checked against `result.value` when set
    #: (`returns=`, ADR-022), else `result.text`.
    output_schema: Mapping[str, Any] | None = None
    no_duplicate_side_effects: bool = False


@value
class TrajectoryResult:
    ok: bool
    violations: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.ok

    def __str__(self) -> str:
        return "ok" if self.ok else "; ".join(self.violations)


def _cost_usd(result: "Result") -> float:
    cost = result.cost
    if hasattr(cost, "decimal"):
        return float(cost.decimal)
    return float(str(cost).lstrip("$"))


def check_trajectory(contract: Trajectory, result: "Result",
                     events: "Sequence[Event]") -> TrajectoryResult:
    violations: list[str] = []
    ran = set(result.tools_run)

    missing = contract.must_call - ran
    if missing:
        violations.append(f"must_call: {sorted(missing)} did not run "
                          f"(ran: {sorted(ran) or ['nothing']})")

    forbidden = contract.must_not_call & ran
    if forbidden:
        violations.append(f"must_not_call: {sorted(forbidden)} ran anyway")

    # A call went through ASK resolution iff `policy.decided`'s `policy` field reads
    # "approval" (PolicyEngine.resolve stamps that whether the callback approved,
    # denied, or there was no callback at all — see policy/engine.py).
    approval_seen: set[str] = {str(e.data["tool"]) for e in events
                              if e.kind.value == "policy.decided"
                              and e.data.get("policy") == "approval"}
    not_asked = contract.requires_approval - approval_seen
    if not_asked:
        violations.append(f"requires_approval: {sorted(not_asked)} never reached an "
                          f"ASK (auto-allowed, or not called at all)")

    n_model_calls = sum(1 for e in events if e.kind.value == "model.request")
    if contract.max_model_calls is not None and n_model_calls > contract.max_model_calls:
        violations.append(f"max_model_calls: {n_model_calls} > {contract.max_model_calls}")

    if contract.max_tokens is not None and result.usage.total > contract.max_tokens:
        violations.append(f"max_tokens: {result.usage.total} > {contract.max_tokens}")

    if contract.max_cost_usd is not None:
        spent = _cost_usd(result)
        if spent > contract.max_cost_usd:
            violations.append(f"max_cost_usd: ${spent:.4f} > ${contract.max_cost_usd:.4f}")

    if contract.output_schema is not None:
        import jsonschema
        subject = result.value if result.value is not None else result.text
        try:
            jsonschema.validate(subject, contract.output_schema)
        except jsonschema.ValidationError as exc:
            violations.append(f"output_schema: {exc.message}")

    if contract.no_duplicate_side_effects:
        # A tool that actually STARTED (`tool.started`, not merely requested — a denied
        # or deduped-in-batch request never reaches this kind, T-2.5) more than once
        # under the same canonical arguments double-ran a side effect. `tool.started`
        # carries no `arguments` of its own (dispatch.py) — only `tool.requested` does,
        # correlated by `call_id`.
        from ..context.assembler import canonical as _canonical
        args_by_call: dict[str, Any] = {
            str(e.data["call_id"]): e.data.get("arguments", {})
            for e in events if e.kind.value == "tool.requested"
        }
        seen: dict[tuple[str, str], int] = {}
        for e in events:
            if e.kind.value != "tool.started":
                continue
            args = args_by_call.get(str(e.data.get("call_id", "")), {})
            key = (str(e.data.get("tool", "")), _canonical(args))
            seen[key] = seen.get(key, 0) + 1
        dupes = {k: v for k, v in seen.items() if v > 1}
        if dupes:
            violations.append(f"no_duplicate_side_effects: {dupes}")

    return TrajectoryResult(ok=not violations, violations=tuple(violations))
