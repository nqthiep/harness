# 04 — Runtime & Durability

**Owns:** the graph, checkpoints, resume, `unguarded_paths()`, cancel, the sub-agent
boundary, OTel spans.
**Does not own:** the `Decision` lifecycle and the audit sink (-> [`02-safety-engine.md`]),
the `Ledger`'s shape (-> [`05-cost-and-memory.md`]), `ToolSpec` (-> [`03-tools-and-mcp.md`]).
Vocabulary follows [`00-foundation.md`](00-foundation.md) §6 exactly.

---

## 1. Why a graph, not a loop

This is the strongest architectural argument in the entire research effort, and it's an
argument **from data**, not taste ([§11](../research/11-workflow-and-dx.md) §12).

| package | kLOC | graph | declarative | visualize | **recover** |
|---|---:|---:|---:|---:|---:|
| langgraph 1.2.11 | 27 | **29.2** | 0.6 | 0.2 ᵃ | **51.6** |
| autogen-agentchat 0.7.5 | 11 | 19.0 | 6.5 | 0.0 | 2.3 |
| haystack-ai 3.1.0 | 54 | 2.9 | **22.7** | **1.3** | **0.3** |
| openai-agents 0.22.0 | 125 | **0.2** | 3.4 | 0.1 | 5.3 |
| agno 3.0.1 | 419 | 0.4 | 5.3 | 0.0 | 2.4 |

Ten packages sort into **three** philosophies, not eleven
([§11](../research/11-workflow-and-dx.md) §12):

1. **Explicit graph** (langgraph, autogen) — topology is a data structure built before
   the run starts.
2. **Declarative pipeline** (haystack) — topology is *serializable*, round-trips to YAML.
3. **Imperative loop** (openai-agents, agno, google-adk, pydantic-ai) — no topology at
   all; just a `while`, a model, a tool table, and `max_turns`. openai-agents scores 0.2
   in the `graph` column because it **has no** graph, and that's a design, not a flaw.

### 1.1 Three consequences of one decision

The real axis isn't "power vs. simplicity" but **how much is known before the model
runs**. A `while` loop can't state in advance what it will do, so it **cannot**:

- checkpoint at a meaningful boundary,
- be statically checked,
- be drawn.

A graph does all three. That's why LangGraph's checkpoint density beats the rest of the
industry by an order of magnitude: **not because they care about durability more, but
because having a topology is what makes durability installable**
([§11](../research/11-workflow-and-dx.md) §12).

> For a harness, **a graph is not a feature — it's a prerequisite.** Durability, static
> guard checking, and a drawable topology are **three consequences of ONE decision.**

This is exactly the fifth invariant in [`00-foundation.md`](00-foundation.md) §1: *"a
topology is what makes durability installable"* -> the runtime is a graph, not a
`while` loop.

### 1.2 A counterexample that must be read: haystack

haystack is the most declarative entry in the research (`declarative` 22.7 —
`to_dict`/`from_dict` on every component, and the only first-class visualization) and
**almost cannot recover** (`recover` **0.3**) ([§11](../research/11-workflow-and-dx.md) §12).

> **Serializing a DEFINITION is a different problem from checkpointing EXECUTION, and
> solving the first buys nothing toward the second.**

The direct consequence for this file: a "config-driven, round-trips to YAML" harness
does **not** automatically get resume for free. Both are needed, and they have to be
built separately. That's why §4 below exists and can't be shrunk to "just dump the graph
to JSON."

### 1.3 Choosing LangGraph is taking on two more jobs

LangGraph wins on Recovery but scores **budget 0.0** and **otel 0.1**
([§03](../research/03-safety-reliability.md) §17, §18). This isn't a criticism: it's a
runtime, deliberately leaving those two jobs to the layer above. But it defines this
harness's scope:

| layer | who does it | where |
|---|---|---|
| topology, checkpoint, resume, interrupt | LangGraph | this file reuses it |
| a budget ceiling before every model call | **the harness** | §2's `budget` node, detail in `05` |
| observability | **the harness** | §8 |
| proving a gate can't be bypassed | **the harness** | §3 |

Evidence that LangGraph's checkpointing is **deliberate architecture, not one
codebase's quirk**: `@langchain/langgraph` reproduces the exact same density in
TypeScript — **23.4 vs. 21.1/kLOC** ([§10](../research/10-governance-health-languages.md)
§28). Two independent implementations, one number => reproducible, not luck.

---

## 2. Graph structure

Six nodes. No more. Every node is a checkpoint boundary.

```
        START
          |
          v
      +--------+  stop_reason?  +--------+
      | budget |---------------->| finish |--> END
      +--------+                +--------+
          | ok                      ^ ^ ^
          v                         | | |
      +--------+  stop/no tools ----+ | |
      | model  |                      | |
      +--------+                      | |
          | has tool_calls            | |
          v                           | |
      +--------+   has an ASK  +---------+|
      | policy |-------------->| approve ||
      +--------+               +---------+|
          | all ALLOW              |      |
          v                        v      |
      +--------+<------------------+      |
      | tools  |                          |
      +--------+--------------------------+
          +---------> budget  (next round)
```

| node | responsibility | why it's its own node |
|---|---|---|
| `budget` | reserves money + counts steps + wall clock, derives `max_tokens` | the invariant "nothing reaches the model without a reservation" has to be an **edge**, not a convention ([§03](../research/03-safety-reliability.md) §20: nobody reserves before calling the model) |
| `model` | exactly one model call, settles the reservation, classifies the stop reason | a natural checkpoint boundary: the most expensive point |
| `policy` | each `ToolCall` -> one `Verdict` | the invariant "nothing reaches a tool without a verdict" |
| `approve` | `ASK` -> `ALLOW`/`DENY` via a `Decision` | separated from `policy` because **only this node is allowed to pause for a long time** (§4) |
| `tools` | execution, the redaction scope, raising taint, the barrier for `write`/`danger` | the only place tool output becomes bytes => the only place redaction has to hold |
| `finish` | the single exit | see §2.2 |

### 2.1 Three nodes that must always be crossed

```python
GUARDED: Mapping[NodeName, NodeName] = {
    MODEL:  BUDGET,     # no path to model that skips budget
    TOOLS:  POLICY,     # no path to tools that skips policy
    END:    FINISH,     # no path to END that skips finish
}
```

This is data, not prose — §3 reads this exact table.

### 2.2 Why `END` is in the table too

Round 35 of this repo found the graph only ever emitted **9 of the 15** declared event
kinds, `run.finished` among the missing, because every exit branch went straight to
`END`. The same failure class Round 27 caught in the hand-written loop: an event
declared with nowhere that emits it.

Routing every exit through **one** node turns the run-closing event into
**structure**, instead of something every branch has to remember. Same rule as the other
two gates — that's why it lives in the same table, not as a separate convention.

### 2.3 What this graph deliberately does NOT do

The research notes: **first-class state machines are almost universally absent (at most
0.3)** — "the agent must not refund before verifying" is enforced everywhere in the
industry with a prompt or a hand-written conditional
([§11](../research/11-workflow-and-dx.md) §12 *What nobody has*).

We do **not** build a state machine for business logic. The six nodes above are
*enforcement* topology, not *business* topology. Business state travels in
`state["workflow"]` and is checkpointed along with everything else. Why: KISS — a
mechanism that doesn't fix a measured shortcoming gets cut
([`00-foundation.md`](00-foundation.md) §8.4), and here the measured shortcoming is
*enforcement being bypassed*, not *missing a state DSL*.

---

## 3. `unguarded_paths()` — R-2, provable rather than reviewable

### 3.1 Why this has to be a program, not a reader

The number that decides this, recorded in [`00-foundation.md`](00-foundation.md) §5 R-2:

> **23 review rounds found 20 bugs and 0 security bugs; 16 RUNTIME rounds found 38+ bugs
> and 4 security bugs.** Reading doesn't find what running finds.

For a graph, "running" is even stronger than a test: reachability on the **compiled**
graph covers even the paths no test ever walks. That's exactly what a hand-written loop
can't offer ([§11](../research/11-workflow-and-dx.md) §12: *"An imperative loop … cannot
be checkpointed at a meaningful boundary, statically checked, or drawn"*).

### 3.2 Signature

```python
from collections.abc import Mapping, Sequence
from typing import NewType
from langgraph.graph.state import CompiledStateGraph

NodeName = NewType("NodeName", str)


@value
class UnguardedPath:
    """A concrete counterexample: a path from START to `node` that never touches `gate`."""
    node: NodeName                 # the guarded node (model / tools / END)
    gate: NodeName                 # the gate it should have crossed (budget / policy / finish)
    witness: tuple[NodeName, ...]  # the actual path, START -> … -> node


def unguarded_paths(
    compiled: CompiledStateGraph,
    guarded: Mapping[NodeName, NodeName] = GUARDED,
) -> Sequence[UnguardedPath]:
    """Every way to route around a gate. Empty <=> enforcement holds on EVERY path.

    A DFS **from every valid entry point**, not just `START`, **refusing to pass through**
    `gate`. Reaching `node` under that condition is a counterexample, and `witness` is
    the path to print.

    With LangGraph, resuming loads a checkpoint and continues from an arbitrary node, so
    the entry-point set is **every node** — see §3.5. That's why this proof alone is NOT
    enough.
    """
```

**One difference from the current implementation in `src/harness/lg/graph.py`:** the
current function returns `list[tuple[str, str]]` — it knows *that* there's a bug, not
*where*. `witness` is the difference between "a red test" and "a red test fixable in
thirty seconds." This is measurable DX:
[§11](../research/11-workflow-and-dx.md) §22 measures *"errors with context"* — an
error that interpolates the offending value instead of stating the general condition —
and the industry only reaches 25-61%. No reason for this harness to sit in the bottom
half.

### 3.3 WHEN it runs — both, and each for a different reason

**(a) Compile time — fail-closed, the only path to get a graph at all.**

```python
class UnguardedPathError(RuntimeError):
    def __init__(self, paths: Sequence[UnguardedPath]) -> None: ...


def compile_guarded(
    g: StateGraph,
    *,
    checkpointer: BaseCheckpointSaver,
) -> CompiledStateGraph:
    """The ONLY API that returns a runnable graph. `build()` is private."""
    compiled = g.compile(checkpointer=checkpointer)
    bad = unguarded_paths(compiled)
    if bad:
        raise UnguardedPathError(bad)
    return compiled
```

Poka-Yoke: a caller has **no** path to a compiled graph that skips this check, because
`build()` isn't public. This fixes exactly the failure class
[§09](../research/09-memory-context-multiagent-hitl.md) §16bis describes — Microsoft has
the best lattice in the research, but their own `_harness/` **doesn't import it**: a
correct mechanism that isn't on the mandatory path doesn't exist. Here, the gate check
*is* the path.

**(b) A test — because a checker that always returns empty also "passes."**

The compile-time check proves the graph is correct; it does **not** prove the checker is
correct. So the test suite must include a **mutation test**: build the graph, add an
edge `START -> model`, and assert `unguarded_paths` returns non-empty with
`witness == (START, MODEL)`. Three mutations at minimum, one per row of `GUARDED`.

This is exactly R-2 applied to itself: trusting `unguarded_paths` just by *reading* it
would be repeating the exact review that found 0 security bugs.

**(c) CI — runs on the real graph of every example in `examples/`**, not just the
default graph. A plugin adding a node is allowed (R-1); a node that opens a bypass is
not.

### 3.5 Two places the proof has a hole, and the invariants that replace it

An adversarial reviewer found two bypasses `unguarded_paths()` **marks green**
([review-security.md](review-security.md) S-1, S-2). Both share one cause: this
function proves something about **edges in the graph**, while the attacker operates
**somewhere else**.

#### G-1. Resume is a mid-graph entry point

A checkpoint is written at the `approve -> tools` boundary. The process dies. Three
hours later, a resume loads the checkpoint and continues **from the `tools` node** — not
a path from `START`. The `policy` node has been skipped over, so nothing re-checks the
`DecisionLog`: a `Decision` that expired two hours ago, or one revoked with
`Decision(verdict=DENY)` written during the pause, and the tool still runs. It loses
**simultaneously** the TTL (02 §2.5), revocation (02 §2.4 D-2), and R-2 — and it loses
**silently**.

> **Invariant I-1** (K-13: shares a number with `docs/02-architecture.md §2.3`'s I-1 —
> this draft was written first, in different words, but describes the same invariant;
> not a "replacement" for some other I-1 — the ORIGINAL I-1 in
> [`03`](03-tools-and-mcp.md §4.4) is a completely different invariant [idempotency,
> effect-log/checkpoint ordering], since renamed to `IDEM-1` to remove the collision).
> *A gate is a precondition at the point of consumption, not an edge.*
> The `tools` node calls `DecisionLog.lookup(call, run_id, now=clock())` **immediately
> before each call** and fails closed if the result is no longer `ALLOW`. The `policy`
> node still exists — it's where the *asking* happens — but the permission is
> re-checked at the point of *use*. One redundant lookup on the happy path is a cheap
> price for a resume three hours later not becoming a hole.

#### G-2. `GUARDED` guards a NODE NAME, not a call

The compaction threshold in [05](05-cost-and-memory.md) §B.2 calls the model to
summarize once context hits 80% of the window. That call doesn't sit at a node named
`model`, so `GUARDED[MODEL] = BUDGET` doesn't cover it, and `unguarded_paths()` still
returns empty. The reservation made at the `budget` node was sized entirely for the main
call, so the summarization call spends **outside** the ceiling. An attacker only has to
paste a long page so every turn triggers an extra, uncapped model call — a direct hit on
shortcoming #4, the very thing this design claims to fix.

> **Invariant I-2** (shares a number with `docs/02-architecture.md §2.3`'s I-2, for the
> same reason above). *No `ModelProvider` call runs without an open `Reservation`.*
> Enforced at the **seam**, not by node name: `ModelProvider` is wrapped once on the
> mandatory path, and the wrapper raises if `ctx.reservation is None`. Checking at the
> seam layer catches every call regardless of which node it's emitted from, plugin-added
> nodes included.

**Why these are written down instead of silently patched.** `unguarded_paths()` is
advertised as "provable, not just reviewable." A proof whose domain is narrower than its
own advertisement is more dangerous than no proof at all — an operator will trust it.
Its real domain is: *every path from START in the static graph*. I-1 and I-2 cover the
rest, and both are **runtime checks**, not construction-time checks.

### 3.4 What it proves, and what it does NOT prove

| provable | NOT provable |
|---|---|
| no path **from START** to `model` skips `budget` | whether `budget` actually calls `reserve()` |
| — | a model call emitted from a different node (I-2's job) |
| — | resuming into the middle of the graph (I-1's job) |
| no path to `tools` skips `policy` | whether `policy` produces `DENY` in the right place |
| no path to `END` skips `finish` | whether `finish` actually emits `run.finished` |

The right column is the job of property tests in `02`/`05`. Stating this boundary
plainly so nobody reads "unguarded_paths is green" as "the harness is safe" — exactly
the misconception [§03](../research/03-safety-reliability.md) §17 flags at Goose: four
permission modes, and the tool still runs with the user's full account privileges.

---

## 4. Checkpoint and resume

### 4.1 Whose design this borrows

LangGraph, and the borrowing has evidence: `recover` at 51.6/kLOC (502 `checkpoint` +
308 `checkpointer` + 88 `resume`, verified not to be noise —
[§11](../research/11-workflow-and-dx.md) §12), plus `durability:
Literal["sync","async","exit"]` and `interrupt()`, described right in the source as
*"Interrupt the graph with a resumable exception from within a node"*
([§03](../research/03-safety-reliability.md) §15). Reproduced in TypeScript at 23.4 vs.
21.1 ([§10](../research/10-governance-health-languages.md) §28) => **deliberate
architecture, not one codebase.**

Tier 3 — smolagents at 0.4, letta-client at 0.0 — has nothing to learn here
([§03](../research/03-safety-reliability.md) §15).

### 4.2 The `durability` default is derived from `Effect`, not from configuration

| a run containing a tool with effect | durability |
|---|---|
| only `read` / `external` | `"async"` — losing a checkpoint only costs time, the tool can retry |
| any `write` or `danger` | **`"sync"`** — losing a checkpoint could cause a side effect a second time |

Why the caller doesn't get a free choice: **none of 16 packages provide exactly-once at
the tool-call level** ([§03](../research/03-safety-reliability.md) §15 — pydantic-ai
talks about an internal no-op, letta is an HTTP header, langgraph's `CachePolicy` hashes
with `pickle`). When exactly-once isn't available, the only thing left is **never losing
track of a call already in flight**, and that's a synchronous checkpoint. Derived from
classifying once, exactly [`00-foundation.md`](00-foundation.md) §2's model — a tool
author never has to think about durability.

### 4.3 `interrupt` / `Command(resume=...)`, and where NOT to redesign the same thing

`langgraph/types.py:851`: `interrupt(value: Any)`; `Interrupt` carries `value: Any` and
`id: str`, with the id an `xxh3_128_hexdigest` of the node namespace. Resuming is
`Command(resume=...)`, again `Any`
([§09](../research/09-memory-context-multiagent-hitl.md) §14.1).

**A strength that must be kept:** that id locates *where it paused*, so a resume can
never be misapplied to a different pause. That's a real correctness property, and it's
why langgraph honestly leads the `resume` column.

**The shortcoming that must be fixed — and where to fix it:** the id identifies *where
it stopped*, **not** *who is answering*. *"Nothing in the type system distinguishes a
resume value that came from a human who clicked Approve from one produced by a script,
another agent, or a replay"*
([§09](../research/09-memory-context-multiagent-hitl.md) §14.1).

> That's `Decision`'s job, in [`02-safety-engine.md`](02-safety-engine.md).
> **This file does not redesign actor/expiry/scope.** This file's job is making the
> resume channel *carry only* a `DecisionId`, never a `bool`.

```python
@value
class PauseRequest:
    """The payload of `interrupt()` — what the approver sees."""
    call_id: CallId
    tool: ToolName
    effect: Effect
    args: Mapping[str, Any]     # already through redact()
    reason: str

@value
class ResumeToken:
    """The ONLY payload `Command(resume=...)` accepts."""
    decision_id: DecisionId
```

The `approve` node, on receiving a resume:

```python
def approval_gate(self, state: AgentState) -> dict[str, Any]:
    """Exchanges a ResumeToken for a Decision from the audit store, THEN routes."""
```

Three fail-closed checks, in this exact order:

1. the resume value is **not** a `ResumeToken` (e.g. `True`, `"yes"`, `1`) -> `DENY`,
   never `ALLOW`. This is the fix for openai-agents's
   `approve_tool(item, always_approve=True)`: a `bool` can't carry an actor, so it isn't
   a valid input at all ([§09](../research/09-memory-context-multiagent-hitl.md) §14.1).
2. `Decision.scope` doesn't match the pending `ToolCall` (tool, arg values, `server`) ->
   `DENY`. Learned from Microsoft's `ToolApprovalRule`: approving
   `delete_file(path="/tmp/x")` doesn't approve `delete_file(path="/etc/passwd")`, and a
   grant for one MCP server doesn't transfer to a different server with the same tool
   name ([§09](../research/09-memory-context-multiagent-hitl.md) §14.1).
3. `Decision.expires_at` has passed -> `DENY`. Fixes exactly *"that grant never lapses
   for the life of the context."*

All three are *lookups*, never *taking a value's word for it*. The resume channel is
never the source of a permission.

### 4.4 A grant must survive a resume

Microsoft gets one thing right that nobody else does: `ToolApprovalState` has
`to_dict`/`from_dict` and is bound to a session, *"so a grant survives a resume rather
than being silently re-asked or silently re-granted"*
([§09](../research/09-memory-context-multiagent-hitl.md) §14.1).

We keep exactly that property, but what gets checkpointed is a **list of `DecisionId`s
still valid in this run**, not a copy of the grant:

```python
class AgentState(TypedDict, total=False):
    decisions: list[str]        # DecisionId — a lookup key, not a copy of the permission
```

Since `Decision` is append-only ([`00-foundation.md`](00-foundation.md) §4, D-2), a copy
in state would never see a later-written revoking `Decision` (`verdict=DENY`). Storing
the id forces every use to re-read the log.

### 4.5 Resuming into a graph that has changed

A checkpoint references **node names**. If the topology changes between a pause and a
resume, the checkpoint still loads, but its meaning has changed. LangGraph doesn't check
this; this repo's current runtime only handles the easy case (`tool no longer
available`).

KISS, one field:

```python
class AgentState(TypedDict, total=False):
    graph_version: str      # sha256 of (sorted node names, sorted edges, GUARDED)
```

Resuming into a graph with a different `graph_version` -> `stop_reason="graph_changed"`,
routed through `finish`, never continues. Fail-closed. This is where haystack's lesson
comes back: a definition and its execution are two different things, so a checkpoint has
to be able to **say** which definition it belongs to
([§11](../research/11-workflow-and-dx.md) §12).

### 4.6 `Agent(durable=True)` — hides the graph, not the durability

Everything in §1-§4.5 describes `harness.lg.build_agent()` — still exactly true, and
still the ONLY way this durability is BUILT. What changes (N-10,
`design/07-risks-and-open-issues.md`) is HOW a caller reaches it: before, only someone
hand-writing `HumanMessage`/`graph.invoke()`/`thread_id` could touch the checkpoint.
`Agent(durable=True)` is a thin layer calling straight into `build_agent()` — not a
second way to get durability — through exactly the methods §03 already defines
(`agent.py::_atry_run_durable`/`_build_durable_graph`):

```python
agent = Agent(name="...", job="...", durable=True, session_id="cust-42")
agent.run("...")           # str in, Result out — nothing LangChain-shaped leaks out
```

Three execution decisions, not part of the graph described above:

1. **The model goes through exactly ONE seam.** `lg/adapter.py::ProviderChatModel`
   wraps THAT SAME Agent's `provider=` (`AnthropicProvider`/`FakeModel`, the same one
   the classic backend calls) into a `langchain_core.BaseChatModel` — pulling in no
   `langchain-anthropic`, no second model-calling path with its own error mapping. It
   also calls `ContextAssembler.build()` (`self._asm`, already available from
   `Agent.__init__`) to build the `ModelRequest` — closing a gap that belonged to
   `build_agent()` ALONE: before this file, nothing sent `job=`/the system prompt to the
   model on the graph backend, even when called directly.
2. **A default SQLite checkpoint, not cached on the `Agent`.** `checkpoint=None` (the
   default) -> `.harness/checkpoints/<slug(name)>.sqlite3`, created automatically. The
   graph is recompiled and the connection opened/closed around EXACTLY one call
   (`_build_durable_graph`), not held for the `Agent`'s whole lifetime like `_asm`/
   `_watch` — because `AsyncSqliteSaver` holds a background thread that is NOT a
   daemon, and an `Agent` caching it would make a script that calls `run()` never exit.
   The trade-off: recompiling the graph (cheap, no I/O) on every call, in exchange for
   an Agent that behaves exactly like every other Agent — it returns.
3. **`session_id=` (already existed from T-8.1) IS the `thread_id`.** No new concept
   added; leaving it unset makes each `Agent` object generate its own, living in RAM —
   different from an explicit `session_id=` at exactly one point: it survives a real
   restart.

`returns=`, `.chat()`, `.resume(transcript)`, `on_delta=` all raise `ConfigError` when
`durable=True` — not something this section redesigns, but today's actual boundary of
`build_agent()` (N-1/N-3, not yet closed) or a mechanism that would duplicate the
checkpointer's own job, stated plainly rather than leaving `Result` silently incomplete.

---

## 5. Where state lives (R-4)

> **The thread's checkpointed state is the ONLY memory.**

### 5.1 Three primitives, and why all three fall short

| primitive | why it's broken | evidence |
|---|---|---|
| an attribute on the runtime object | `Runtime` is built once per compiled graph, serving **every** conversation | Round 37: a shared ledger billed customer A's tokens to customer B; a shared taint tracker leaked A's taint to B |
| `threading.local()` | per-OS-thread; under asyncio, many coroutines share one thread => two concurrent tool calls read each other's slot, **silently and fail-open** | [§09](../research/09-memory-context-multiagent-hitl.md) §16bis, `security.py:685` |
| `ContextVar` | per-task, better than `threading.local()` — **but still doesn't cross a LangGraph node**: every node runs in a **copied** context | a mistake made in Round 34, fixed **wrong** in Round 37, fixed **right** in Round 41 |

Stated plainly so nobody "fixes" this halfway: `threading.local()` is **weaker** than
something already known to be insufficient. The research says exactly this:
*"`threading.local()` is strictly weaker than the primitive that was already not
enough"* ([§09](../research/09-memory-context-multiagent-hitl.md) §16bis).

And no module-level singleton. Microsoft has `_global_variable_store` and
`_quarantine_chat_client` at module scope — in a multi-tenant server, one tenant's
change becomes every tenant's ([§09](../research/09-memory-context-multiagent-hitl.md)
§16bis).

### 5.2 `snapshot()` / `restore()`

This is a good idea already in the current runtime (`Ledger.snapshot()/restore()`),
raised into a shared protocol:

```python
from typing import Any, Protocol, Self
from collections.abc import Mapping


class Snapshottable(Protocol):
    """Everything carrying state that lives within one run implements this protocol."""

    def snapshot(self) -> Mapping[str, Any]:
        """JSON-serializable. No float for money, no callables, no objects."""

    @classmethod
    def restore(cls, snap: Mapping[str, Any] | None, /) -> Self:
        """`None` => the starting state. Must be total: never raises on an old snapshot."""
```

Three things implement it: the `Ledger`, `Label` (the two-directional taint —
[`00-foundation.md`](00-foundation.md) §3.2), and the set of currently-valid
`DecisionId`s.

Every node starts with `restore(state[...])` and ends with `{...: x.snapshot()}`. No
node reads `self` for anything belonging to a run.

### 5.3 Three testable rules

- **S-1. `restore(snapshot(x)) == x`** — a property test, for every Snapshottable type.
- **S-2. `json.dumps(state)` never raises.** This is why `_pending` carries a
  **`ToolName`**, never a `ToolSpec`: a `ToolSpec` holds a callable no serializer can
  write. The spec is *runtime configuration*, looked up when needed.
- **S-3. Money is a `str` of a `Decimal`, never a `float`.**

### 5.4 `Runtime` must be immutable — and today it isn't

`src/harness/lg/runtime.py` opens with exactly this rule ("*the thread's state is the
only memory*") and violates it three lines later, in `budget_gate`:

```python
if state.get("step", 0) == 0 and not self._started:
    self._started = True
```

`self._started` is state **belonging to one run**, sitting on an object **shared by
every thread**. A second conversation on the same compiled graph never emits
`run.started`. The same failure class as Round 37, one spot left over.

The design: `Runtime` freezes after `__init__` (`@value`-style `__setattr__` raises),
and this flag moves to state:

```python
class AgentState(TypedDict, total=False):
    started: bool
```

An object that can't be assigned to can't have a second spot left over. This is R-4 as
Poka-Yoke, not as a comment.

---

## 6. Cancel and cleanup

### 6.1 Whose design this borrows

**autogen-agentchat, 26.9 `cancel`/kLOC — the highest in the entire research effort,
three times the runner-up (pydantic-ai at 9.1)**
([§07](../research/07-remaining-python.md), reconfirmed in
[§00](../research/00-executive-summary.md)). Not noise: 183 occurrences of
`cancellation_token` are **an explicit API parameter**, plus 84 occurrences of
`CancellationToken`.

> Cancellation is **threaded by hand across every boundary**, never relying on a
> runtime's implicit mechanism. Costlier in API surface, but the only way that makes
> cancellation **testable**.

We take exactly that pattern: `CancelToken` is an explicit parameter reaching all the
way to `ToolCtx`. No global variable, no `ContextVar` (§5.1 already explained why).

```python
@value
class CancelToken:
    """Canonical definition. Passed EXPLICITLY across every boundary that could block —
    no ContextVar, no global variable (00 §5 R-4). Learned from autogen, the package
    with the highest cancellation discipline in the research (26.9/kLOC).
    """
    def cancel(self, reason: str) -> None: ...
    def cancelled(self) -> bool: ...
    def raise_if_cancelled(self) -> None: ...
    @property
    def reason(self) -> str | None: ...
```

### 6.2 Where state lands on a mid-flight cancel

Checkpoint boundaries are **node boundaries.** So the precise answer:

| cancelled while at | state rolls back to | why |
|---|---|---|
| `budget` | before the reservation | the reservation was never written into state |
| `model` | before the call | no response came back yet => nothing to settle; see 6.4 |
| `policy` / `approve` | before a verdict exists | no tool has run yet |
| `tools` | **see 6.3** | the only place a side effect can exist |

In every case: `stop_reason="cancelled"`, and it **still goes through `finish`** — §2.2's
rule has no exception for cancel. This is exactly the spot where "every branch
remembers on its own" would break.

### 6.3 What happens to a tool already running — the rule derived from `Effect`

No package has exactly-once at the tool-call level
([§03](../research/03-safety-reliability.md) §15). So it is **not** safe to pretend
cancelling a `write` in flight is harmless.

| effect | on cancel | why |
|---|---|---|
| `read`, `external` | cancel immediately | retryable, no side effect ([`00`](00-foundation.md) §2) |
| `write`, `danger` | **no abort.** Wait up to `cancel_grace` (default 30s), record the result into state, only then stop | a side effect may have already happened; losing the result is more dangerous than waiting |

If `cancel_grace` elapses and the tool still isn't done:

```python
class AgentState(TypedDict, total=False):
    unresolved: list[dict[str, Any]]   # {call_id, tool, effect, started_at}
```

- `stop_reason="cancelled"`, `detail` states how many calls have an unknown outcome;
- `finish` emits `run.finished` with `unresolved` non-empty;
- the run is flagged as **needing reconciliation**, never reported as `completed`.

The principle: a `write` of unknown outcome is **information**, and that information
must live in state, not in an operator's head. This is the failure class
[§03](../research/03-safety-reliability.md) §15 calls "a client timeout -> a retry -> a
second side effect" — this design doesn't solve exactly-once here, but it must not
**hide** the ambiguity.

### 6.4 Cleanup: reservations must be closed

`finish` is the only place that closes the books, and it must close them **on the error
path too**:

- every open reservation -> `release()`d back to the ledger (detail in
  [`05`](05-cost-and-memory.md));
- every `hold()` granted to a sub-agent not yet `release()`d -> released with the known
  actual cost, or with the whole hold if unknown (fail-closed toward **already spent**,
  never toward "refund it");
- the final ledger snapshot is written to state **before** `run.finished` is emitted.

Failing closed toward "already spent" is deliberate: underestimating cost is how a
budget ceiling turns into decoration, and the ceiling is one of the five invariants
([`00`](00-foundation.md) §1).

---

## 7. Sub-agent

### 7.1 What the research says (read before designing)

A handoff **is a tool call**: `on_invoke_handoff` returns a different `Agent`, and the
loop keeps running. No new process, no new context, no new client
([§09](../research/09-memory-context-multiagent-hitl.md) §13).

| | present? |
|---|---|
| reduces tool surface | yes |
| specializes the prompt | yes |
| **a shared turn budget that can't be reset** | yes |
| context isolation | no — sees the entire history by default |
| fault isolation | no — a child's exception is the parent's |
| **privilege isolation** | no — `_resolve_approval_key` **has no agent identity** — a tool approved for A is still approved for B |
| taint isolation | no — there's no taint model to isolate at all |

> **Multi-agent as currently implemented is a router, not an architecture.** It's
> over-engineering to reach for it as an *isolation* mechanism, since no implementation
> provides any of the three kinds of isolation the word implies.

### 7.2 A strength that must be kept: turn budget does NOT reset across a handoff

Verified in `agents/run.py`: `current_turn = 0` appears **exactly once**, at line 762
when a run starts; at the handoff site (line 2050) only `current_agent` is reassigned
([§09](../research/09-memory-context-multiagent-hitl.md) §13).

> A naive implementation — each agent with its own `max_turns` — turns a delegation
> chain into an unbounded loop: three agents handing off in a circle run forever. OpenAI
> gets this right.

**This harness's rule:** `steps` belongs to the **root run**. A sub-agent draws from the
same step ledger, never gets a new quota. Plus:

```python
class AgentState(TypedDict, total=False):
    depth: int          # 0 = the root agent; a hard cap, default 3
```

The `depth` cap blocks exactly the circular case above, along a dimension shared
`steps` doesn't cover (a wide delegation tree can still blow up before hitting the step
limit).

### 7.3 A sub-agent must have its own ledger and its own policy scope

The research's conclusion is direct: *"if you want a genuinely isolated sub-agent … the
framework will not give it to you. It has to be a first-class construct with its own
ledger and its own policy scope"*
([§09](../research/09-memory-context-multiagent-hitl.md) §13).

**Its own ledger — via `hold()`, not via `remaining_usd()`.**

```python
def spawn_subagent(
    parent: Ledger,
    child: AgentSpec,
    *,
    task: str,
    cancel: CancelToken,
) -> SubagentResult:
    """The child runs on its own `thread_id`, its budget a slice of the parent's ALREADY HELD."""
```

Why `hold()` instead of `remaining_usd()`: with several children running in parallel,
each one reading `remaining_usd()` sees the full remaining balance and each one thinks
it can spend all of it (Round 28). This is this repo's own ADR-030, and it exists for
exactly the reason §13 states.

**Its own policy scope — using `Decision.run_id`, no new field needed.**

`Decision` ([`00`](00-foundation.md) §4) already has `run_id`. A sub-agent having its
own `run_id` means a `Decision` granted in the parent's run **doesn't match** when the
child looks it up. That is the precise fix for `_resolve_approval_key` lacking agent
identity, and it needs **no new mechanism** — only a lookup rule: *a `Decision` only
applies when `decision.run_id == state["run_id"]`.*

Stated explicitly so `02` doesn't redesign the same thing: this file only asserts **the
child has its own `run_id`**; the scope-matching rule belongs to
[`02-safety-engine.md`](02-safety-engine.md).

**Fault isolation — the thing §13 says nobody has.**

A child's exception does **not** escape into the parent graph. It becomes a
`ToolMessage(status="error")` in the parent graph, exactly like any other failing tool.
The child stops, the parent reads the error and decides.

**Taint — a two-directional join, never reset.**

A child's `Label` starts from the parent's `Label`; when the child returns a result, the
child's `Label` joins back into the parent's. Monotonic, never decreasing within a run
([`00`](00-foundation.md) §3.2). No "the sub-agent cleans the context" — that's exactly
the hole `input_filter` leaves when it defaults to `None`
([§09](../research/09-memory-context-multiagent-hitl.md) §13).

### 7.4 When NOT to use a sub-agent

When the reason is "isolation." It's only justified when the goal is **reducing tool
surface** or **avoiding a 4000-token prompt** — both real and measurable
([§09](../research/09-memory-context-multiagent-hitl.md) §13). KISS:
[`00`](00-foundation.md) §8.4.

---

## 7bis. `redact()` — one function, three call sites

The `tools` node, `PauseRequest.args`, and OTel attributes all mention "redact," and an
early draft never defined it anywhere ([review-kiss.md](review-kiss.md) K-29). This is
the sole mechanism against what the research records as "no Python package has a
dedicated `Secret` type" ([§03](../research/03-safety-reliability.md) §16).

```python
class Secret:
    """A value that must never be serialized in the clear.

    `__repr__`/`__str__`/`__format__` return "***"; `json.dumps` raises.
    Only `.reveal()` gets the real value, and only the tool holding it can call it.
    """
    def reveal(self) -> str: ...

def redact(value: object) -> object:
    """Replaces every `Secret` nested inside with "***", preserving structure.

    ONE implementation, called from three places: before a tool result enters the
    checkpoint, before a `PauseRequest` reaches an approver, before any value becomes
    an OTel attribute. Three copies would be three chances for one to go stale.
    """
```

**A limit, stated plainly.** `redact()` blocks leaks *from accidental serialization*. It
cannot block a tool that deliberately calls `.reveal()` and puts the string into its
result — that belongs to the lattice's `confidentiality` axis
([00 §3.2](00-foundation.md)), and that's why both mechanisms exist together.

## 8. Observability

### 8.1 Whose design this borrows, and why this is mandatory work

**google/adk-java: 11.4 OpenTelemetry hits/kLOC — the strongest of any package in any
language in the entire research effort** (63 direct `opentelemetry` references, the
rest real span plumbing: `spanId`, `spanContext`, `spanRecord`)
([§10](../research/10-governance-health-languages.md) §28). *"Google instruments its
agent runtime the way it instruments its services."*

At the opposite end: **langgraph at 0.1 otel/kLOC** ([§03](../research/03-safety-reliability.md)
§18) — *"choosing LangGraph means taking on building the observability layer yourself."*
This file is where that job gets picked up.

And [§11](../research/11-workflow-and-dx.md) §22 concludes that observability *"genuine
only in google-adk … and thin elsewhere"* is one of five things a production deployment
needs that no framework provides.

### 8.2 Spans: four spans, not nine (review-kiss.md K-10)

An early draft listed nine span kinds x ~50 attributes, citing google-adk-java's OTel
density (11.4 hits/kLOC) as evidence. A reviewer pointed out someone else's code
density isn't a measured shortcoming of THIS harness — the measured shortcoming is
*"observability genuine only in google-adk … and thin elsewhere"*
([§10](../research/10-governance-health-languages.md) §28), and that sentence demands
**having** observability, not **nine span kinds**. This file (`04`) admits the research
itself *"doesn't measure the runtime overhead of that density"*
([review-kiss.md](review-kiss.md) K-10). `harness.step` carries exactly one attribute
(`step`) — a whole span for one integer; `harness.budget` and `harness.finish` carry
information already present (or mergeable) on another span.

v1 keeps **four spans**; the other five are NOT dropped, their attributes merge into the
nearest still-living span at the point they actually happen in the loop (`reserve()`
happens right before calling the model; approving is a step inside the policy's resolve;
a subagent runs through the exact same tool-call machinery):

| span | attributes |
|---|---|
| `harness.run` (root) | `run_id`, `thread_id`, `graph_version`, `model`, `depth`, `stop_reason`, `steps`, `cost_usd`, `integrity`, `confidentiality`, `unresolved_count` (merged from the old `finish`) |
| `harness.model` | `step` (merged from the old `step`), `reserved_usd`, `remaining_usd`, `remaining_steps`, `remaining_wall_clock_s`, `max_tokens` (merged from the old `budget`), `model`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `provider_stop_reason`, `cost_usd` |
| `harness.policy` | `tool`, `call_id`, `effect`, `verdict`, `policy`, `decision_id`, `pause_id`, `waited_ms`, `actor_kind`, `resumed_from_checkpoint` (merged from the old `approve`) |
| `harness.tool` | `tool`, `call_id`, `effect`, `server`, `is_error`, `error_type`, `retryable`, `taints_output`, `args_sha256`, `child_run_id`, `child_thread_id`, `held_usd`, `actual_usd`, `depth` (merged from the old `subagent`, present only when `spec.subagent is not None`) |

No implementation of this table exists in `src/harness/` today (no `opentelemetry`
import, no `tracer`) — `EventBus`/`Exporter` (`observe/events.py`) is the current
observability mechanism, independent of OTel. This table is the plan for WHEN real OTel
gets built, not a description of running code; recorded here already-trimmed so that
when it's built, it's built as exactly four spans from the start instead of nine and
then cut later.

Four content rules, each fixing a measured shortcoming, KEPT UNCHANGED:

1. **`effect` is present on every tool-related span.** It's the only classification
   axis ([`00`](00-foundation.md) §2); if it never reaches the trace, nobody can answer
   "which runs touched `danger`" without reading raw logs.
2. **`decision_id`, never `approved=true`.** The trace says *a decision exists*; its
   content lives in the audit sink. This is where repeating agno's mistake is avoided:
   the audit trail must not be the only copy, and especially must not be written by the
   audited party ([§09](../research/09-memory-context-multiagent-hitl.md) §14.1).
3. **`actor_kind`, never `actor_id`.** The trace travels to a third-party vendor; an
   approver's identity stays in the audit sink.
4. **No tool argument value ever becomes an attribute** — only `args_sha256`. The
   research records: *no Python package in the research has a dedicated `Secret` type
   guarding against a leak through logs/prompts*
   ([§03](../research/03-safety-reliability.md) §16). Every string-shaped attribute
   goes through `redact()` — the same function the `tools` node uses, never a second
   copy.

### 8.3 A span is NOT an audit record

The boundary, written down so nobody rebuilds the same thing twice:

| | trace | audit (`02`) |
|---|---|---|
| can it be lost | **yes** (sampling, a dead exporter) | **no** |
| who reads it | operators, a dashboard | audit, incident investigation |
| carries identity | no | **yes** (`Actor`) |
| written by | the runtime | the runtime, append-only (D-2) |

If a property has to be provable six months later, it belongs to the audit sink, not the
trace. `unguarded_paths()` proves **structure**; audit proves **what happened**; the
trace only helps *watch* it while it's happening.

---

## Not Enough Evidence

- **The cost of `durability="sync"`.** §4.2 chooses synchronous checkpointing for every
  run with a `write`/`danger` tool, but the research only records *that* the three modes
  exist ([§03](../research/03-safety-reliability.md) §15), not each mode's measured
  latency. If the cost is large, the threshold might need to drop to per-tool. This
  needs measurement, not a guess.
- **`cancel_grace = 30s`** is a chosen number, not a measured one. The research measures
  cancellation *density* (autogen at 26.9), not real tools' timeout behavior.
- **The correctness of resume under cancel.** No research source reads LangGraph's
  source along the "interrupt + cancel + resume at once" path. This combination needs
  to be tested on a real system before it can be trusted.
- **`graph_version` guards against a changed topology, not against changed *node
  behavior*.** Same node name, same edges, different logic inside => the hash doesn't
  change. No research source solves this; noted here as a known hole.
- **The cost of OTel.** §8 describes ~9 span kinds worth of content per step.
  google-adk hits 11.4/kLOC but the research **does not** measure that density's
  runtime overhead ([§10](../research/10-governance-health-languages.md) §28). The
  default sampling rate has no grounding yet.
- **Parallelism inside the `tools` node under mid-flight checkpointing.**
  [`00`](00-foundation.md) §2 lets `read`/`external` run in parallel and makes
  `write`/`danger` a barrier — matching [§08](../research/08-tool-mcp-plugin.md) §8.3.
  But the interaction between that barrier and LangGraph's mid-flight checkpointing was
  never read in source. **Not enough evidence.**
- **Multi-tenancy.** [§03](../research/03-safety-reliability.md) §16 states plainly "Not
  Enough Evidence for most of the library set." This design (state as the only memory,
  no singleton) is a *necessary condition* for multi-tenancy, not sufficient evidence
  of it.
