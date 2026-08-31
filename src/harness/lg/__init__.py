"""LangGraph backend — Round 35.

`build_agent()` compiles an enforcement graph.  The caller never touches the model node
directly, so the guarantees are structural rather than advisory: see `graph.unguarded_paths`.
"""
from __future__ import annotations

from typing import Any, Sequence


from ..budget.ledger import Budget, Ledger
from ..models import pricing
from ..observe.events import EventBus
from ..policy.builtin import EffectPolicy, EgressPolicy, TaintPolicy
from ..policy.engine import PolicyEngine
from ..policy.label import Grants
from ..tools.registry import ToolSet
from ..agent import _check_subagent_safety, _check_tool_set
from .graph import GUARDED, INTERRUPT, build, unguarded_paths
from .runtime import Runtime
from .state import AgentState

__all__ = ["build_agent", "unguarded_paths", "GUARDED", "INTERRUPT", "AgentState"]


def build_agent(*, model, tools: Sequence[Any] = (), budget: Any = None,
                model_name: str = "claude-opus-5", safety: str = "standard",
                policies: Sequence[Any] = (), allowed_hosts: Sequence[str] | None = None,
                accepts_tainted: Sequence[str] = (), sensitive: Sequence[str] = (),
                approve=None, checkpointer=None, exporters: Sequence[Any] = (),
                bus: EventBus | None = None):
    """Compile an agent graph.  Returns (compiled_graph, runtime)."""
    # `exporters=` is the spelling `Agent` uses for the same seam; a caller should not
    # have to know that one backend hands them a bus (Round 35 parity).
    if bus is None and exporters:
        bus = EventBus("run", exporters)
    toolset = ToolSet(tools)
    # The construction-time refusals are part of the design, not of the loop: Round 35's
    # parity suite found `build_agent` accepted an external+danger tool set that `Agent`
    # refuses outright.  The taint policy would still have caught it mid-run, but that is
    # a demotion from Prevent to Detect on the Poka-Yoke ladder (docs/08 §1).
    grants = Grants(accepts_tainted=frozenset(accepts_tainted),
                    sensitive=frozenset(sensitive))
    _check_tool_set(toolset, grants)
    _check_subagent_safety(toolset, safety)
    ledger = Ledger(Budget.parse(budget))
    user = tuple(p() if _is_factory(p) else p for p in policies)
    engine = PolicyEngine(
        (EffectPolicy(), TaintPolicy(grants), EgressPolicy(allowed_hosts)), user)
    rt = Runtime(model=model.bind_tools([_lc_tool(s) for s in toolset]) if len(toolset) else model,
                 toolset=toolset, ledger=ledger, engine=engine,
                 price=pricing.price(model_name),
                 max_output=pricing.MAX_OUTPUT.get(model_name, 8_000), model_name=model_name,
                 bus=bus, approve=approve, grants=grants)
    compiled = build(rt).compile(checkpointer=checkpointer)

    broken = unguarded_paths(compiled)
    if broken:                                   # cannot happen unless build() changed
        raise AssertionError(f"enforcement gate bypassable: {broken}")
    return compiled, rt


def _lc_tool(spec) -> dict:
    """ToolSpec → the provider-agnostic tool schema LangChain binds."""
    return {"name": spec.name, "description": spec.description,
            "parameters": dict(spec.input_schema)}


def _is_factory(p) -> bool:
    import inspect
    return not inspect.ismethod(getattr(p, "check", None))
