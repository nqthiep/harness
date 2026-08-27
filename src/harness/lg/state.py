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
