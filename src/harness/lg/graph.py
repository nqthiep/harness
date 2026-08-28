"""The enforcement graph — Round 35.

ADR-001 said the harness owns the loop because that is the only place a budget check can
precede a model call and a permission check can precede a tool call.  On LangGraph the
same guarantee is expressed as **topology**: there is no edge into `model` that does not
pass `budget`, and none into `tools` that does not pass `policy`.

That is stronger than the hand-written loop it replaces.  A compiled graph can be
introspected, so "every path to the model passes the budget gate" stops being an AST test
over our own source and becomes a reachability proof over the real execution structure —
one that holds for paths no test happens to exercise (AC-04, AC-05).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from ..errors import BudgetExceeded
from ..policy.base import ToolCall, Verdict
from ..result import Money
from ..tools import EFFECT_PROFILES
from .state import AgentState

BUDGET, MODEL, POLICY, APPROVE, TOOLS = "budget", "model", "policy", "approve", "tools"
FINISH = "finish"

#: `approve=INTERRUPT` waits with LangGraph's durable `interrupt()` instead of calling a
#: Python callback: the run survives a process restart while a person decides.  It is the
#: one approval mode only this backend can offer; every other `approve=` value means
#: exactly what it means in the hand-written loop (Round 35 parity).
INTERRUPT = "__interrupt__"

#: Nodes that must not be reachable without passing the gate that guards them.
#:
#: END is in the table for the same reason as the other two.  Round 35 found the graph
#: could emit only 9 of the 15 declared event kinds, `run.finished` among them, because
#: each exit routed straight to END — the same defect class Round 27 caught in the loop
#: (a declared event with no emit site).  Routing every exit through one node makes the
#: closing event structural instead of a thing each branch must remember.
GUARDED: dict[str, str] = {MODEL: BUDGET, TOOLS: POLICY, END: FINISH}


def build(runtime) -> StateGraph:
    """Assemble the graph.  `runtime` supplies ledger, policy engine, taint and tools."""
    g = StateGraph(AgentState)
    g.add_node(BUDGET, runtime.budget_gate)
    g.add_node(MODEL, runtime.call_model)
    g.add_node(POLICY, runtime.policy_gate)
    g.add_node(APPROVE, runtime.approval_gate)
    g.add_node(TOOLS, runtime.run_tools)
    g.add_node(FINISH, runtime.finish)

    g.add_edge(START, BUDGET)
    g.add_conditional_edges(BUDGET, _after_budget, {MODEL: MODEL, FINISH: FINISH})
    g.add_conditional_edges(MODEL, _after_model,
                            {POLICY: POLICY, BUDGET: BUDGET, FINISH: FINISH})
    g.add_conditional_edges(POLICY, _after_policy,
                            {APPROVE: APPROVE, TOOLS: TOOLS, BUDGET: BUDGET})
    g.add_conditional_edges(APPROVE, _after_approval, {TOOLS: TOOLS, BUDGET: BUDGET})
    g.add_edge(TOOLS, BUDGET)
    g.add_edge(FINISH, END)
    return g


# ── routing ──────────────────────────────────────────────────────────────────
def _after_budget(state: AgentState) -> str:
    return FINISH if state.get("stop_reason") else MODEL


#: A model that pauses forever is a loop the budget would pay for.  Same bound as the
#: hand-written loop, and loud rather than silent.
MAX_PAUSES = 5


def _after_model(state: AgentState) -> str:
    if state.get("stop_reason"):
        return FINISH
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return POLICY
    # `pause_turn`: the provider says resumable, so resume — through the budget gate,
    # which is the only way back to the model and is what bounds the cost (Round 38).
    if 0 < state.get("paused", 0) <= MAX_PAUSES:
        return BUDGET
    return FINISH


def _after_policy(state: AgentState) -> str:
    pend = state.get("_pending", [])
    if any(Verdict(p["verdict"]) is Verdict.ASK for p in pend):
        return APPROVE
    if any(Verdict(p["verdict"]) is Verdict.ALLOW for p in pend):
        return TOOLS
    return BUDGET            # everything denied: the model gets the errors and retries


def _after_approval(state: AgentState) -> str:
    pend = state.get("_pending", [])
    return TOOLS if any(Verdict(p["verdict"]) is Verdict.ALLOW for p in pend) else BUDGET


# ── the structural proof ─────────────────────────────────────────────────────
def unguarded_paths(compiled) -> list[tuple[str, str]]:
    """Every (guarded_node, gate) whose gate can be bypassed.  Empty means the
    enforcement holds for every path, including ones no test walks."""
    graph = compiled.get_graph()
    edges: dict[str, set[str]] = {}
    for e in graph.edges:
        edges.setdefault(e.source, set()).add(e.target)

    broken = []
    for node, gate in GUARDED.items():
        if node not in edges and not any(node in t for t in edges.values()):
            continue                                  # node absent from this graph
        # depth-first from START, refusing to traverse the gate
        seen, stack = set(), ["__start__"]
        while stack:
            cur = stack.pop()
            if cur in seen or cur == gate:
                continue
            seen.add(cur)
            if cur == node:
                broken.append((node, gate))
                break
            stack.extend(edges.get(cur, ()))
    return broken
