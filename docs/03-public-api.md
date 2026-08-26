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

`Agent(name, job, tools)` · `agent.run(text) -> Result` · `result.text`

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
        budget: Budget | str | None = None,  # None → DEFAULT_BUDGET, never unlimited
        # --- safety ------------------------------------------------------------
        safety: Literal["standard", "strict"] = "standard",
        approve: ApprovalFn | None = None,
        policies: Sequence[Policy] = (),     # can only ever restrict further
        allowed_hosts: Sequence[str] | None = None,
        # --- state -------------------------------------------------------------
        memory: Store | str | None = None,   # str → SQLite path
        # --- observability -----------------------------------------------------
        transcript: str | Path | None = None,
        exporters: Sequence[Exporter] = (),
        # --- advanced ----------------------------------------------------------
        provider: ModelProvider | None = None,
        max_steps: int | None = None,        # None → from budget
        max_parallel_tools: int = 8,
        discover: bool = False,              # opt-in entry-point plugin discovery
    ) -> None: ...
```

Nineteen parameters may look like a lot; **three are required-by-use and sixteen have
defaults that are correct for a first agent.** The parameters are ordered by the level of
the ladder at which a user meets them, and grouped with comments in the source so the
grouping survives.

All parameters are **keyword-only**. This is deliberate Poka-Yoke: it makes
`Agent("Bob", "answer questions")` a `TypeError` rather than a silently mis-assigned
argument, and it means parameters can be reordered or deprecated without breaking callers.

### Methods

| Method | Returns | Notes |
|---|---|---|
| `run(message, *, stream=None) -> Result` | Result | Raises `RunFailed` on non-success. Sync facade. |
| `try_run(message, *, stream=None) -> Result` | Result | Never raises for run outcomes; check `result.ok`. |
| `arun(...)` / `atry_run(...)` | Awaitable[Result] | Async originals. |
| `chat() -> Chat` | Chat | Stateful multi-turn session. |
| `as_tool(*, name=None, description=None) -> ToolSpec` | ToolSpec | Turns this agent into a subagent tool. |
| `resume(transcript) -> Result` | Result | Continue an interrupted run. |
| `with_(**overrides) -> Agent` | Agent | Returns a **new** agent. `Agent` is frozen; there are no setters. |

`with_()` exists because `Agent` is immutable, and immutability is what keeps the cache
prefix stable (ADR-004). Mutating an agent mid-run is not "discouraged" — it is impossible.

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
    def raise_for_status(self) -> None: ...
```

`StopReason` is a closed enum: `COMPLETED`, `BUDGET_EXHAUSTED`, `STEP_LIMIT`, `TIMEOUT`,
`DENIED_BY_POLICY`, `MODEL_REFUSAL`, `CANCELLED`, `ERROR`.

`MODEL_REFUSAL` exists because current Claude models return HTTP 200 with
`stop_reason: "refusal"`. Code that reads `content` without checking would present a
confident empty answer; the harness converts it into an explicit outcome.

## 6. Complete export list

```python
# harness/__init__.py
__all__ = [
    # core
    "Agent", "Chat", "tool", "Result", "StopReason", "Usage", "Money",
    # safety
    "Effect", "Secret", "Policy", "Verdict", "Allow", "Ask", "Deny", "RunContext",
    # cost
    "Budget", "DEFAULT_BUDGET",
    # extension points
    "ModelProvider", "Store", "Exporter", "ToolSpec",
    # errors
    "HarnessError", "ConfigError", "MissingEffectError", "ToolSchemaError",
    "ToolContractError", "NonDeterministicPromptError", "UnsafeToolSetError",
    "SyncInAsyncContextError", "RunFailed", "BudgetExceeded", "PolicyDenied",
    "ProviderError", "__version__",
]
```

Thirty-two symbols. A user reaching Level 0 needs three of them.

## 7. Error-message standard

Error messages are part of the API and are reviewed like API. Every `ConfigError`
subclass must supply all four of:

1. **What happened**, in the user's vocabulary — not the implementation's.
2. **Where**, with `file:line` of the user's code.
3. **The fix, as copy-pasteable code.**
4. **A docs anchor.**

Enforced by a test that asserts every `ConfigError` subclass renders all four sections
([§09.4](09-testing.md)). The `MissingEffectError` above is the reference example.
