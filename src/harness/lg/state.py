"""State the graph carries — Round 35.

Everything the enforcement needs lives here, so a checkpointer persists it: workflow
state now survives a process restart, which Round 34 recorded as a stated gap.
"""
from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    spent_usd: str          # Decimal as a string: JSON-checkpointable, never a float
    step: int
    tainted: bool
    stop_reason: str | None
    detail: str
    workflow: dict[str, Any]   # the caller's business state, checkpointed with the rest

    #: Verdicts handed from the policy gate to the tools node.  It must be declared:
    #: LangGraph silently discards any state key absent from the schema, and the first
    #: version of this file omitted it — so the tools node received nothing, no tool ever
    #: ran, and a test asserting that a *denied* tool did not run passed for the wrong
    #: reason (Round 35).
    _pending: list[dict[str, Any]]
    #: The ledger's accumulated state — spend, steps, ADR-026's calibration. It lives
    #: here, not on the Runtime: a Runtime is built once per compiled graph and serves
    #: every conversation, so a ledger it held billed one customer for another's tokens
    #: (Round 37). In state it is per-thread and survives a restart.
    ledger: dict[str, Any]
    #: Handed from the budget gate to the model node. Also state rather than an
    #: attribute: LangGraph runs each node in its own copied context.
    max_tokens: int
    #: Consecutive `pause_turn` responses this turn. Declared, not implicit: LangGraph
    #: silently discards an undeclared key (IDL-41), and an undiscarded counter is what
    #: keeps a paused model from becoming an unbounded loop.
    paused: int
    #: N-6 — running `Usage` total for THIS TURN (reset alongside `asks`/
    #: `turn_started_at` in `budget_gate`, accumulated by `call_model`), serialized as
    #: `dataclasses.asdict(Usage(...))` — JSON-checkpointable, same convention as
    #: `ledger`. `run.finished`'s `input_tokens`/`output_tokens`/... fields read it back.
    turn_usage: dict[str, int]
    #: Approval requests resolved so far THIS TURN — S-25(b). Reset like the ledger's
    #: `steps` (`Runtime._ledger`, `_is_new_turn`): a model repeatedly forcing ASKs is a
    #: per-turn attack (approval fatigue), not something a long-lived conversation should
    #: accumulate towards forever.
    asks: int
    #: N-6 — `time.time()` when THIS TURN started (`budget_gate`, same "reset on a new
    #: turn" instant as `asks`/the ledger's `steps`). `run.finished`'s `duration_s` reads
    #: it back in `finish()`. Per-turn, not per-thread, because `RUN_FINISHED` itself
    #: already fires once per turn on this backend (every `graph.ainvoke()` reaches
    #: `finish`), unlike `RUN_STARTED` (once per thread, ever) — the two were already
    #: asymmetric before this field existed; `duration_s` matches the one that repeats.
    turn_started_at: float
