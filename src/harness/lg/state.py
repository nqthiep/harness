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
    #: Compaction-immune record of tool names that have SUCCEEDED, ever, on this thread.
    #: ("Succeeded", not "got any `ToolMessage`" — ADR-115: this feeds
    #: `RequireBeforePolicy`, and a gate satisfied by its prerequisite failing is worse
    #: than no gate. `Result.tools_run` asks the other question and is answered by the
    #: `executed` stamp on the message instead.) — mirrors `dispatch.py::Dispatcher.ran` on the
    #: classic backend, which is a plain list appended-to for the life of one run and
    #: never pruned. Before this field existed, `_tools_called()` re-derived its answer
    #: by scanning `state["messages"]` on every call — which real compaction
    #: (`Runtime._compact`, `context/window.py`) can silently erase: once the step
    #: carrying an early `consult_advisor` call ages past `_KEEP_RECENT_MESSAGES`, its
    #: `AIMessage`/`ToolMessage` pair is dropped for a taint-preserving tombstone that
    #: does not carry tool names. `RequireBeforePolicy` (policy/builtin.py) would then
    #: DENY a tool it had genuinely already cleared, purely because the conversation had
    #: grown long enough to compact — confirmed by direct repro, not just read from the
    #: code. Appended to, never pruned by compaction (`RemoveMessage` targets `messages`,
    #: not this key), so the record this field holds survives exactly the same drop that
    #: erases the message-scan answer.
    tools_called_ever: list[str]
    #: N-3 (design/07-risks-and-open-issues.md) — the parsed `build_agent(returns=...)`
    #: answer, set once by `finish()`. Bug found on review: `finish()` already called
    #: `parse_returns()` to VALIDATE the final answer, but threw the parsed result away
    #: — `returns=` worked as a rejection filter and nothing else on the raw
    #: `build_agent()` escape hatch (no way to retrieve the value it just validated).
    #: `Agent(durable=True, returns=...)` was unaffected (`agent.py::_state_to_result`
    #: re-parses the text a second time, entirely outside checkpointed state), which is
    #: exactly how this stayed uncaught. A JSON-safe `dict`/scalar/list, never the
    #: dataclass INSTANCE `run.py`'s `Result.value` holds: state is checkpointed, and a
    #: class instance is not something a checkpointer can promise to round-trip (IDL-42's
    #: same reasoning, one level up — a `Decimal` crosses as a string for the identical
    #: reason).
    value: Any
