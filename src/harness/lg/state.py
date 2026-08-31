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
    #: Mechanical stall detection (`progress.py`), checkpointed for the same reason the
    #: ledger is (IDL-47): a `Runtime` serves every thread, so a counter held on it would
    #: mix one conversation's progress into another's. `seen_calls` holds 16-hex-char
    #: digests, never the arguments themselves — a `write_file` call can carry a whole
    #: file, and a checkpoint is not the place for a second copy of it.
    seen_calls: list[str]
    stalled_steps: int
    #: Approval requests resolved so far THIS TURN — S-25(b). Reset like the ledger's
    #: `steps` (`Runtime._ledger`, `_is_new_turn`): a model repeatedly forcing ASKs is a
    #: per-turn attack (approval fatigue), not something a long-lived conversation should
    #: accumulate towards forever.
    asks: int
