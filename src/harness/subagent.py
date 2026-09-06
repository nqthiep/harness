"""Running a tool that is itself an agent, and nesting its budget into the parent's.

Split out of `dispatch.py` (ADR-119) rather than raising IDL-13's 250-line ceiling on
that file — the same move that produced `dispatch.py` from `run.py` in Round 28,
`stop.py` from `run.py`, and `audit.py` from `dispatch.py` in ADR-111. The rule is that
the ceiling is a forcing function, not a measurement: when a file overruns you look for
the seam it has grown, and this one is real. Everything here is about DELEGATION and the
three budget axes it has to nest; nothing here is about choosing, gating, scheduling or
recording a tool call, which is what the rest of `dispatch.py` does.

It takes a `Ledger` rather than the dispatcher, because that is all it ever touched.
"""
from __future__ import annotations

from dataclasses import replace as _replace
from typing import Any

from .result import Money
from .tools import ToolSpec


async def run_subagent(ledger: Any, spec: ToolSpec, kwargs: dict) -> str:
    """§06.4: a subagent is capped by the parent's REMAINING budget, and its spend
    settles into the parent's ledger.

    Round 28 found both documented and unenforced.  Each child kept an independent
    ledger, so a $0.10 parent spent $30 through six children while reporting $0.0000 —
    SC-2a's ceiling leaking entirely through a documented feature.

    S-13: that fix covered `usd` only. `steps` and `wall_clock_s` used to come straight
    from the child's own declared `Budget`, untouched — four subagents spawned in one
    turn, each declaring `steps=20`, could burn 80 steps against a parent whose own
    ceiling was 20. `hold_steps()`/`release_steps()` apply the same TOCTOU fix `hold()`
    already has for money to the step axis; `wall_clock_s` needs no hold/release (it is
    not a pooled resource — two children running concurrently do not add up to twice the
    elapsed time), just a cap to what the parent actually has left at spawn time.
    """
    child = spec.subagent
    remaining = ledger.remaining_usd()
    want = Money(child.budget.usd) if child.budget.usd is not None else None
    held = ledger.hold(want) if remaining is not None and want is not None else None
    held_steps = ledger.hold_steps(child.budget.steps)
    child_wc = ledger.child_wall_clock(child.budget.wall_clock_s)
    run_child = child.with_(budget=_replace(
        child.budget, usd=(held.decimal if held is not None else child.budget.usd),
        steps=held_steps, wall_clock_s=child_wc))
    r = await run_child.atry_run(kwargs.get("task", ""))
    if held is not None:
        ledger.release(held, r.cost)               # settle into the PARENT ledger
    else:
        ledger.charge(r.cost)
    ledger.release_steps(held_steps, r.steps)
    return r.text if r.ok else f"{child.name} stopped: {r.stop_reason.value}. {r.text}"
