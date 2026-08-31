# 03 — The Public API (the DX contract)

> **Rule:** the public API is exactly what `harness/__init__.py` exports. Nothing else is
> stable, and CI fails if an example imports from a private path.

## 1. The five-line agent

```python
from harness import Agent
from harness.tools.web import search

helper = Agent(
    name="Helper",
    job="Answer questions using the web. Be brief and cite your sources.",
    tools=[search],
)

print(helper.run("What time does the Louvre open on Sundays?").text)
```

Three concepts. **`name`** — what it is called. **`job`** — what it should do. **`tools`** —
what it can use. One method: **`run`**.

Everything a production system needs — the loop, retries, caching, budget, permissions,
telemetry — is already active with safe defaults. Nothing above had to be configured.

## 2. Progressive disclosure ladder

Each level is useful on its own. No level requires understanding the one above it. A user
who never leaves Level 0 gets a correct, cheap, safe agent.

### Level 0 — first agent (5 minutes)

```
pip install harness
harness setup          # asks for a key, checks it works
harness new joker      # writes joker.py AND the .gitignore that protects the key
python joker.py
```

`Agent(name, job, tools)` · `agent.run(text) -> Result` · `print(result)`

While it runs on a terminal, it says what it is doing. When output is piped it says
nothing (ADR-014). The full child-facing version of this level is [§15](15-first-agent.md).

### Level 1 — your own tools and limits (30 minutes)

```python
from harness import Agent, tool

@tool(effect="read")
def get_order(order_id: str) -> dict:
    """Look up an order by its ID."""
    return db.fetch(order_id)

support = Agent(
    name="Support",
    job="Help customers with order questions. Never guess an order status.",
    tools=[get_order],
    budget="$0.10",          # a ceiling, enforced before each call
    model="claude-sonnet-5", # if you want a different one
)

result = support.run("Where is order A-4471?")
print(result.text, result.cost, result.steps)
```

New concepts: `@tool`, `effect=`, `budget=`. `effect` is required and there is a
[one-line rule](#4-choosing-an-effect) for choosing it.

### Level 2 — conversation, approval, memory, delegation (half a day)

```python
chat = support.chat()                  # stateful multi-turn session
chat.say("Where is order A-4471?")
chat.say("And the one before it?")     # history retained

ops = Agent(
    name="Ops", job="...", tools=[restart_service],
    safety="strict",                   # write/external also require approval
    approve=my_approval_callback,      # called for every ASK verdict
    memory="./ops.db",                 # persists across runs
)

# Delegate reading-heavy work to a cheaper model, with its own budget
reader = Agent(name="Reader", job="Summarize one page.",
               tools=[fetch], model="claude-haiku-4-5", budget="$0.01")
research = Agent(name="Research", job="...", tools=[search, reader.as_tool()])
```

### Level 3 — extending the harness (as needed)

Custom `ModelProvider`, `Store`, `Policy`, `Exporter`; plugin packaging; transcript
replay; direct `RunContext` access. Contracts in [§04](04-interfaces.md).

## 3. `Agent` — the complete signature

```python
class Agent:
    def __init__(
        self,
        *,                                   # keyword-only: positional order can never rot
        name: str,
        job: str,
        tools: Sequence[ToolSpec] = (),
        # --- commonly adjusted -------------------------------------------------
        model: str = "claude-opus-5",
        effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium",
        returns: type | None = None,         # validated structured output — ADR-022
        budget: Budget | str | None = None,  # None → DEFAULT_BUDGET, never unlimited
        # --- safety ------------------------------------------------------------
        safety: Literal["standard", "strict"] = "standard",
        approve: ApprovalFn | None = None,
        max_asks_per_run: int = 20,          # deny past this many ASKs — approval fatigue
        policies: Sequence[Policy] = (),     # can only ever restrict further
        allowed_hosts: Sequence[str] | None = (),  # () → deny all; None (explicit) → unrestricted (T-7.2)
        # --- state -------------------------------------------------------------
        memory: Store | str | None = None,   # str → SQLite path
        # --- observability -----------------------------------------------------
        transcript: str | Path | None = None,
        exporters: Sequence[Exporter] = (),
        tenant_id: str | None = None,        # stamped onto every Event (T-8.1)
        session_id: str | None = None,       # stamped onto every Event (T-8.1)
        # --- advanced ----------------------------------------------------------
        provider: ModelProvider | None = None,
        max_steps: int | None = None,        # None → from budget
        max_parallel_tools: int = 8,
        discover: bool = False,              # opt-in entry-point plugin discovery
        # --- durability ----------------------------------------------------------
        durable: bool = False,               # run on the LangGraph engine, hidden — §3.5
        checkpoint: Any = None,              # str/Path -> SQLite file; or a raw checkpointer
    ) -> None: ...
```

Twenty-two parameters may look like a lot; **three are required-by-use and nineteen have
defaults that are correct for a first agent.** The parameters are ordered by the level of
the ladder at which a user meets them, and grouped with comments in the source so the
grouping survives.

All parameters are **keyword-only**. This is deliberate Poka-Yoke: with two adjacent
strings, `Agent("answer questions", "Bob")` would otherwise be silently mis-assigned. It
also means parameters can be reordered or deprecated without breaking callers.

`__init__` accepts `*args` for one reason only — to **reject** them with a readable message
instead of Python's `TypeError: __init__() takes 0 positional arguments but 2 were given`:

```
ConfigError: Agent needs you to label each part, like this:

    Agent(
        name="Helper",
        job="tell jokes",
    )

  You wrote:  Agent("Helper", "tell jokes")
```

This is the Round 14 pattern, applied throughout: **keep the constraint, replace the
error.** A Poka-Yoke whose message is incomprehensible is only half-built.

### Methods

| Method | Returns | Notes |
|---|---|---|
| `run(message, *, on_delta=None) -> Result` | Result | Raises `RunFailed` on non-success. Sync facade. (Corrected here from an earlier `stream=None` — the shipped parameter has always been `on_delta`, a token-level delta callback; the doc never caught up.) |
| `try_run(message, *, on_delta=None) -> Result` | Result | Never raises for run outcomes; check `result.ok`. **Exception:** `asyncio.CancelledError` propagates instead of returning a `stop_reason="cancelled"` Result — cancellation is a control signal from the caller, not a run outcome (T-6.2, docs/17-research-alignment.md Y-01); an outer `TaskGroup`/`wait_for` must see it happen. |
| `arun(...)` / `atry_run(...)` | Awaitable[Result] | Async originals. |
| `stream(message, *, on_delta=None) -> AsyncIterator[Event]` | AsyncIterator[Event] | T-8.5. `async for ev in agent.stream(msg)` over the real `Event` stream (all 16 kinds, envelope v1 — T-8.1). `on_delta=` stays the separate token-level mechanism; this yields whole `Event`s, not text fragments. Cancelling the iteration cancels the underlying run (T-6.2, extended). |
| `chat(*, budget=None) -> Chat` | Chat | Stateful multi-turn session with **one ledger for the whole session**, defaulting to 10 × the agent's run budget (ADR-020). As it depletes, answers shorten before the chat ends. `harness.session.Session` (T-8.6) wraps a `Chat` with an id, an owner, a TTL, `.fork()`, `.resume_from()`, and a concurrency boundary — the resource-lifecycle layer `Chat` alone doesn't have. **`durable=True` refuses this call** (§3.5) — a durable multi-turn conversation is `session_id=`, not `Chat`. |
| `as_tool(*, name=None, description=None) -> ToolSpec` | ToolSpec | Turns this agent into a subagent tool. |
| `resume(transcript) -> Result` | Result | Continue an interrupted run from a JSONL transcript. **`durable=True` refuses this call** (§3.5) — a durable run needs no separate resume step. |
| `with_(**overrides) -> Agent` | Agent | Returns a **new** agent, every field preserved except what `overrides` names. `Agent` is frozen; there are no setters. (N-7: `transcript`/`exporters`/`accepts_tainted`/`sensitive` used to be silently dropped on every call, not just one that touched them — fixed.) |

`with_()` exists because `Agent` is immutable, and immutability is what keeps the cache
prefix stable (ADR-004). Mutating an agent mid-run is not "discouraged" — it is impossible.

### 3.5 `durable=True` — one Agent, one API, an engine swap underneath

Two backends behind two different vocabularies was the problem, not the two engines: a
caller who wanted a run to survive a process restart had to learn LangChain (`HumanMessage`,
raw `.invoke()`, `thread_id` config) to get it. `durable=True` buys the same durability
through the *same* methods everything above already uses — `run`/`try_run`/`arun`/`atry_run`
take the same `str`, return the same `Result`. Nothing LangChain- or LangGraph-shaped ever
reaches the caller.

```python
agent = Agent(name="Support", job="handle tickets", durable=True,
             session_id="ticket-4471")   # the conversation to reconnect to
agent.run("customer says their order never arrived")
# ... process restarts here — nothing in Python survives it ...
agent.run("they're asking for a refund")   # same session_id -> picks up mid-conversation
```

**What `durable=True` changes:**

* Runs on `harness.lg.build_agent()` internally (§2's "extending the harness" — that
  function, and the compiled graph it returns, stay directly usable for a power user who
  wants LangGraph itself; `durable=True` is the same engine, not a different one).
* `checkpoint=` says where progress is kept. `None` (the default) creates a local SQLite
  file under `.harness/checkpoints/<agent-name>.sqlite3` — zero configuration, durable the
  moment the first run completes. A `str`/`Path` names a specific SQLite file (or
  `":memory:"`, for a durable-shaped run that keeps nothing). Anything else is passed
  straight through as an already-built LangGraph checkpointer — the escape hatch, for a
  Postgres- or Redis-backed store.
* `session_id=` is the conversation identity (already an `Agent` field, T-8.1). Reuse it
  across calls — and across a restart — to continue the same thread; omit it and each
  `Agent` object gets one generated in memory, good for the life of that object only.

**What it does not (yet) change — real, tracked gaps, not silent ones:**

| Not supported when `durable=True` | What happens | Use instead |
|---|---|---|
| `returns=` | `ConfigError` at construction (N-3) | `durable=False`, or parse the text answer yourself |
| `.chat()` | `ConfigError` | Call `run()`/`try_run()` repeatedly with the same `session_id=` |
| `.resume(transcript)` | `ConfigError` | Nothing to do — call `run()` again with the same `session_id=` |
| `on_delta=` (token streaming) | `ConfigError` | `durable=False` for a streamed run |
| Per-tool timeout (N-1) | Not enforced | Keep tool implementations bounded themselves for now |

All five are the LangGraph backend's own pre-existing limits (`design/07-risks-and-open-issues.md`),
now reachable from the primary surface instead of only from the escape hatch — refused
loudly, at the point they'd matter, rather than silently downgraded.

## 4. Choosing an effect

The rule fits on one line each, and the decorator refuses to work without one.

| `effect=` | Ask yourself | Examples | Derived automatically |
|---|---|---|---|
| `"read"` | Does it only look at things? | `get_order`, `read_file`, `calculate` | parallel ✅ · retry ✅ · auto-allow ✅ |
| `"write"` | Does it change something you own, reversibly? | `write_file`, `update_record` | parallel ❌ · retry ❌ · allow (ask in strict) |
| `"external"` | Does it pull in content from the outside world? | `search`, `fetch`, third-party API reads | parallel ✅ · retry ✅ · **output tainted** |
| `"danger"` | Could it do something you cannot undo? | `send_email`, `run_command`, `delete_user`, payments | parallel ❌ · retry ❌ · **always ask** · blocked when tainted |

The developer classifies **once**; five behaviors follow. There is no
`parallel_safe=`/`retryable=`/`requires_approval=` to forget or get inconsistent.

If you omit `effect`, you get this at **import time**:

```
MissingEffectError: tool 'send_invoice' must declare what it does to the world.

    @tool(effect="danger")     # ← likely, based on the name
    def send_invoice(...):

  read      only looks at things          (parallel, retryable, auto-allowed)
  write     changes something reversibly  (serial, not retried)
  external  brings in outside content     (output treated as untrusted)
  danger    cannot be undone              (always asks; blocked after untrusted input)

  → docs/06-safety.md#effects
```

A misspelling — the likeliest mistake in a four-word vocabulary — is caught by name:

```
ConfigError: 'reed' is not one of the four choices. Did you mean "read"?
```

## 5. `Result`

```python
@dataclass(frozen=True, slots=True)
class Result:
    text: str                 # the final assistant text ("" if none was produced)
    ok: bool                  # stop_reason is COMPLETED
    stop_reason: StopReason
    steps: int
    cost: Money               # Decimal-backed; prints as "$0.0143"
    usage: Usage              # input/output/cache_read/cache_write tokens
    messages: list[Message]   # full conversation, for continuation
    run_id: str
    tainted: bool
    value: object | None      # the `returns=` type, validated. None when returns= unset.
    def __str__(self) -> str: return self.text     # print(result) prints the answer
```

`__str__` returning the text is what removes attribute access from the first example. It
is safe: `print(result.text)` had identical exposure, and `Result` defines no `__add__`, so
`"Answer: " + result` still raises rather than silently concatenating.

`StopReason` is a closed enum: `COMPLETED`, `TRUNCATED`, `BUDGET_EXHAUSTED`, `STEP_LIMIT`,
`TIMEOUT`, `DENIED_BY_POLICY`, `MODEL_REFUSAL`, `CANCELLED`, `ERROR`. Only `COMPLETED` sets
`ok = True`.

`TRUNCATED` was added in Round 18: the provider returns `stop_reason: "max_tokens"` when
generation hits the ceiling, and without a value for it a cut-off answer was reported as
success. It matters more since ADR-017, because a small budget now deliberately produces a
small ceiling. An unmapped provider stop reason maps to `ERROR` carrying the raw string —
never to a success (ADR-019).

`MODEL_REFUSAL` exists because current Claude models return HTTP 200 with
`stop_reason: "refusal"`. Code that reads `content` without checking would present a
confident empty answer; the harness converts it into an explicit outcome.

## 6. Complete export list

The real `harness/__init__.py::__all__` — kept in sync by hand; if this ever drifts from
the source file again, trust the source file. `Chat` (from `agent.chat()`) and `Store`/
`Exporter` (protocols, imported from their own submodule when implementing one) are
deliberately NOT here — only types a caller constructs directly, or names needed without
a submodule import, are top-level.

```python
# harness/__init__.py
__all__ = [
    "Agent", "tool", "Result", "StopReason", "Usage", "Step", "Money", "RunContext",
    "Effect", "Secret", "safe_for_display", "Policy", "Verdict", "Ruling", "ToolCall",
    "ToolSpec", "Actor", "Approval", "Session", "SessionExpiredError",
    "Budget", "DEFAULT_BUDGET", "ModelProvider",
    "HarnessError", "ConfigError", "MissingEffectError", "ToolSchemaError",
    "DuplicateToolError", "NonDeterministicPromptError", "UnsafeToolSetError",
    "InvalidBudgetError", "UnknownModelError", "ToolContractError",
    "SyncInAsyncContextError", "RunFailed", "BudgetExceeded", "PolicyDenied",
    "ProviderError", "__version__",
]
```

Everything else is reached through its own submodule — `from harness.lg import
build_agent` (extra: `graph`), `from harness.mcp import connect` (extra: `mcp`), `from
harness.server import create_app` (extra: `server`), `from harness.eval import
cost_per_success, Trajectory, run_golden_set, benchmark`, `from harness.observe.otel
import OtelExporter` (extra: `otel`), `from harness.memory.viking import VikingStore`
(extra: `viking`), `from harness.testing import FakeModel, no_network, ...` (§09).

Thirty-two symbols. A user reaching Level 0 needs three of them.

## 7. The CLI

Promoted from "should" to **must** in Round 14: five of the six beginner blockers were
outside the Python API entirely, and three of them are solved here.

| Command | Does | Why it exists |
|---|---|---|
| `harness setup` | Asks for a key, **validates it with one minimal call**, stores it. Prints the provider's spend-limit URL. | G13.1 — the true first wall. A key is a shell concept; this makes it a paste. |
| `harness new <name>` | Writes a runnable, commented agent file **and** a `.gitignore` containing `.env` | G13.6 + key safety. The scaffold includes a small `budget=` so the concept is shown, not explained later. |
| `harness chat <file>` | Interactive conversation with an agent defined in a file | The moment someone wants a *second* agent. ~20 lines. |
| `harness run <file> <message>` | Runs it once, non-interactively. `--json` streams one canonical `Event` JSON line per event instead of the final text — the CLI/JSON transport, docs/04 §6.2, ADR-056 | Scripting |
| `harness trace <transcript>` | Renders a run: every model call, tool call, verdict, cost | [§05.2](05-data-and-state.md#2-transcript-format) |
| `harness cost <transcript>` | Spend and realized cache hit rate | [§07.2.3](07-cost.md#23-runtime-verification) |
| `harness doctor` | Version, key presence, pricing-table age, cache determinism, plugin compatibility | The standard bug-report attachment |

`harness new` generating the `.gitignore` **in the same command** as the file that needs it
is the design point, not an implementation detail: a protection that is a separate step is
a protection that gets skipped.

## 8. Error-message standard

Error messages are part of the API and are reviewed like API. Every `ConfigError`
subclass must supply all four of:

1. **What happened**, in the user's vocabulary — not the implementation's.
2. **Where**, with `file:line` of the user's code.
3. **The fix, as copy-pasteable code.**
4. **A docs anchor.**

Enforced by a test that asserts every `ConfigError` subclass renders all four sections
([§09.4](09-testing.md)). The `MissingEffectError` above is the reference example.
