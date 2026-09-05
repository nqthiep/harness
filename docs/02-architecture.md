# 02 — Architecture

## 1. The shape in one paragraph

An `Agent` is an **immutable configuration**. Running one creates a mutable `Run` that
executes a small, explicit loop: assemble context → call the model → decide on each
requested tool → execute the allowed ones → append results → repeat. Four services hang
off that loop — the **Ledger** (may we spend?), the **Policy engine** (may we act?), the
**Context assembler** (what exactly do we send?), and the **Event bus** (what happened?).
Five things are pluggable; everything else is core and deliberately not overridable.

Immutability of `Agent` is not stylistic. It is what makes the prompt prefix stable, which
is what makes caching work, which is the single largest cost lever in the system.

## 2. Layers

```
┌────────────────────────────────────────────────────────────────┐
│  L4  Facade      Agent · @tool · Secret · Result · run()       │  ← 99% of users stop here
├────────────────────────────────────────────────────────────────┤
│  L3  Services    Ledger · PolicyEngine · ContextAssembler      │
│                  EventBus · TaintTracker · Scheduler           │
├────────────────────────────────────────────────────────────────┤
│  L2  Core        RunEngine (the loop) · ToolSet · Transcript   │
├────────────────────────────────────────────────────────────────┤
│  L1  Ports       ModelProvider · Store · Policy · Exporter     │  ← the 5 plugin seams
│                  (+ Tool, declared at L4 via @tool)            │
├────────────────────────────────────────────────────────────────┤
│  L0  Adapters    AnthropicProvider · SqliteStore · OtelExporter│
└────────────────────────────────────────────────────────────────┘
```

Dependencies point **downward only**. L2 knows the L1 protocols and never the L0 adapters —
this is what makes the whole system testable with fakes, and it is enforced by
`tests/test_layering.py::L2NeverNamesAnL0Adapter`, not by discipline.

That sentence used to name an **import-linter rule in CI**, and there was no such rule:
`grep -rn "import-linter"` hit three `.md` files and nothing else — no `.importlinter`,
nothing in `pyproject.toml`, not in the dev dependencies. The property held nearly
everywhere and was checked nowhere. Two things are true about it that a bare "never" hid:

* A **composition root** is supposed to know concrete types — `agent.py` building the
  exporters a run gets, `cli`, `server`, `credentials` resolving a provider name, the
  `testing` kit handing out fakes. Forbidding that would only move the wiring behind the
  factory §8 below rejects.
* Three modules are neither composition roots nor compliant, and the test names them as
  KNOWN GAPs rather than excluding them quietly: `dispatch.py` hard-wires
  `InMemoryStore` for idempotency dedup **without** exposing `idempotency_store=`, which
  `lg/runtime.py` does — the same capability, two backends, one switch; `tools/code.py`
  picks `Subprocess` rather than taking a `Sandbox`; and `contrib/driver.py` imports
  `FakeModel` for the demo its own tier rule says belongs in `examples/`.

## 3. The run loop

This is the heart of the system and it is intentionally boring. It is the *only* place
where cost and safety can be enforced, which is why the harness owns it rather than
delegating to the SDK's tool runner (ADR-001).

```
                     ┌──────────────┐
                     │   START      │
                     └──────┬───────┘
                            v
              ┌─────────────────────────────┐
              │ assemble context            │  deterministic; cached prefix
              └─────────────┬───────────────┘
                            v
              ┌─────────────────────────────┐
        ┌────>│ ledger.reserve(worst_case)  │──── insufficient ──> STOP(BUDGET_EXHAUSTED)
        │     └─────────────┬───────────────┘
        │                   v
        │     ┌─────────────────────────────┐
        │     │ provider.complete(request)  │──── refusal ───────> STOP(MODEL_REFUSAL)
        │     └─────────────┬───────────────┘     error ─────────> retry / STOP(ERROR)
        │                   v
        │           stop_reason == "tool_use" ? ──── no ─────────> STOP(COMPLETED)
        │                   │ yes
        │                   v
        │     ┌─────────────────────────────┐
        │     │ for each tool_use block:    │
        │     │   policy.decide(call, ctx)  │  ALLOW / ASK / DENY
        │     └─────────────┬───────────────┘
        │                   v
        │     ┌─────────────────────────────┐
        │     │ scheduler.execute(allowed)  │  parallel-safe → concurrent
        │     │   timeout · truncate · taint│  others        → serial
        │     └─────────────┬───────────────┘
        │                   v
        │     ┌─────────────────────────────┐
        │     │ append ALL tool_results as  │  one user message — never split
        │     │ a single user message       │
        │     └─────────────┬───────────────┘
        │                   v
        │           step_count < limit ? ──── no ──────────────> STOP(STEP_LIMIT)
        └───────────────────┘ yes
```

Four invariants of the loop, each with a test in [§09](09-testing.md):

- **I-1** No model call happens without a successful ledger reservation immediately before it.
- **I-2** No tool executes without a policy verdict of `ALLOW` recorded immediately before it.
- **I-3** Every `tool_use` block receives exactly one `tool_result` block — including denied
  and failed ones (as `is_error`). A missing result is a protocol violation that corrupts
  the conversation.
- **I-4** All `tool_result` blocks for one assistant turn go in **one** user message.

## 3.1 Two engines, one Agent

The loop above is the default engine. `harness[graph]` supplies a second one
(`harness.lg`) in which **LangGraph** owns the loop and the rules are expressed as
topology (ADR-032):

```
START → budget → model → policy → approve → tools ┐
          │        │                              │
          └────────┴──────────→ finish → END      └──→ budget
```

There is no edge into `model` that does not pass `budget`, none into `tools` that does not
pass `policy`, and none into `END` that does not pass `finish`. `unguarded_paths()` proves
it by walking the compiled graph from `__start__` refusing to traverse a gate;
`build_agent()` refuses to return a graph for which it is non-empty:

```python
>>> graph, runtime = build_agent(model=chat, tools=[...], budget="$0.20, 15 steps")
>>> unguarded_paths(graph)
[]
```

**This is a stronger statement than the hand-written loop could make.** A compiled graph is
introspectable, so "every path to the model passes the budget gate" stops being an AST test
over our own source and becomes a reachability proof over the real execution structure —
one that holds for paths no test happens to walk.

**Two engines used to mean two APIs** — `build_agent()` returned a raw compiled graph, and
reaching it meant learning LangChain (`HumanMessage`, `.invoke()`, `thread_id` config)
alongside `Agent`. That was real user-facing friction, not just an implementation detail,
and it is closed now: `Agent(durable=True)` runs on this same engine through the *same*
`run`/`try_run`/`arun`/`atry_run`/`with_` methods everything else in this document uses —
`docs/03-public-api.md §3.5` has the parameter and the (small, tracked) list of what it
doesn't cover yet. `ProviderChatModel` (`lg/adapter.py`) is the seam that makes this
possible without a second model-calling implementation: it wears this Agent's own
`provider=` (the same `AnthropicProvider`/`FakeModel`/... the classic engine calls) as a
LangChain chat model, so a durable run goes through identical error mapping, pricing, and
`ContextAssembler` context — one provider seam, one context-assembly path, two loop
topologies. `build_agent()` and the raw compiled graph remain directly available — the
escape hatch for a power user who wants LangGraph itself, not the primary way to reach this
engine.

**What durability buys:** a run survives a process restart mid-flight (`session_id=` is the
conversation to reconnect to), `approve=INTERRUPT` for a human decision that outlives the
process, and the LangChain model ecosystem for a power user going through `build_agent()`
directly.

**What it costs:** 39 transitive packages (`langgraph-checkpoint-sqlite` — `durable=True`'s
default checkpoint store — adds 3 over the `graph` extra's prior 36), and a second
implementation of one set of rules. The second cost is the dangerous one — Round 35 found
three defects in it that the council had already found and fixed in the first. It is
bounded by `tests/test_parity.py`, which states each rule once and runs it against **all
three** call shapes (the hand-written loop, the raw graph, and `Agent(durable=True)`); a row
that differs is a defect, never a documented difference.

## 4. What is a plugin — the test

The council's answer to "pluginable does not mean everything is a plugin". An extension
point is a **plugin seam** only if all three hold:

> **(a)** A reasonable third party would publish one on PyPI.
> **(b)** The core can be written with zero knowledge of any concrete implementation.
> **(c)** We can name **two genuinely different** implementations *today* — not
> hypothetically.

Applied:

| Candidate | (a) | (b) | (c) | Verdict |
|---|:--:|:--:|:--:|---|
| **Tool** | ✅ | ✅ | ✅ hundreds | **SEAM** |
| **ModelProvider** | ✅ | ✅ | ✅ Anthropic / Bedrock / Vertex | **SEAM** |
| **Store** (memory & transcript persistence) | ✅ | ✅ | ✅ memory / SQLite / Redis | **SEAM** |
| **Policy** | ✅ | ✅ | ✅ approval / egress allowlist / PII | **SEAM** |
| **Exporter** | ✅ | ✅ | ✅ console / JSONL / OTel | **SEAM** |
| **Sandbox** (added M7/T-7.3, ADR-047) | ✅ | ✅ | ✅ `InProcess` / `Subprocess` — a real container runtime (Docker/Firecracker/gVisor) plugs into the same seam | **SEAM** |
| Run loop | ❌ | — | ❌ one | **CORE** — and see below |
| Context assembler | ❌ | ❌ must be byte-deterministic | ❌ | **CORE** |
| Budget ledger | ❌ | ❌ security-critical | ❌ | **CORE** |
| Schema generation | ❌ | ✅ | ❌ | **CORE** |
| Retry | ❌ | ✅ | ❌ SDK does it | **CORE** |
| Model router | ❌ | — | ❌ | **CORE** (and mostly deferred, ADR-006) |

**Six seams** (`Sandbox` added M7/T-7.3, ADR-047 — the table above was updated then but
this sentence wasn't; caught in the T-9.1 pass, K-13's "verify before fixing" discipline
applied to prose staleness rather than a bug). **Everything else is core.** Three of the
core items are core specifically
because making them overridable would let a plugin *defeat* an invariant: a replaceable
ledger can be replaced with one that always says yes; a replaceable assembler can
reintroduce cache invalidation; a replaceable loop can skip the policy check. Extension
points that can disable safety are not extension points, they are vulnerabilities.

The loop is not extensible, but it is **observable** (events) and **interceptable at the
defined seams** (policies decide, tools act). That covers the legitimate reasons someone
would want to override it.

**`harness.middleware`** is sugar over three of the seams above (`ModelProvider`, a
tool's own callable, `Exporter`) — a `Middleware` base class with five optional hooks
(`before_model`/`after_model`/`before_tool`/`after_tool`/`on_event`) that
`with_middleware(agent, *middlewares)` wires onto a new `Agent`. It is not a seventh
seam and not the middleware chain rejected below: every hook runs strictly *after* the
core decision it follows (`before_tool` never sees a call `Policy` already denied).

That parenthetical is true; the conclusion this sentence used to draw from it — that
stacking many of these "can only add restriction or observation, never bypass one" — was
not. `before_tool` returns the kwargs the tool is then called with, so a hook cannot
bypass a **verdict** but can change the **subject** of one. Measured with
`allowed_hosts=["docs.python.org"]` and a six-line middleware:

```
policy.decided:                     [('fetch', 'ALLOW', '')]
tool.requested arguments:           [{'url': 'http://docs.python.org/x'}]
the URL the tool was actually called with:  ['http://evil.example/exfil']
```

So a `Middleware` is **as privileged as the policy set**, and belongs on the trust
boundary list in §6 rather than in the "observation only" bucket. Rewriting arguments is
a real and useful power (redaction, defaulting, tenant scoping); the defect was that it
happened silently, leaving the transcript asserting an argument set that never ran. Every
rewrite now emits `tool.arguments_amended` naming the middleware and the fields it
changed (docs/05 §…), so "what was approved" and "what ran" stay two comparable facts.
`docs/03-public-api.md §3.6` documents it as a Level-3 extension.

**`Agent.with_profile()`** is the same shape of sugar for a different recurring need:
packaging a system prompt, a tool set, a model/effort/budget choice, and policies as one
named, reusable unit, without a second way to construct an `Agent`. `Profile` (`profile.py`)
is a two-member `Protocol` — `name: str`, `apply(agent) -> Agent` — built entirely from
`Agent.with_()`; nothing in the run loop knows it exists. What earns it the "sugar, not a
seam" label is the same discipline `Middleware` follows: `Agent.with_profile()` refuses
the result of `profile.apply()` if it loosened a safety knob the caller's own `Agent(...)`
call already set (`ProfileLoosenedSafetyError` — ADR-073), so stacking a profile on top of
an agent can only add capability, never bypass a decision already made. Two genuinely
different profiles ship as evidence the abstraction generalizes rather than merely
naming one shape twice: `examples/coding_profile.py` (file/git tools, a verification
wrapper, a subagent, a path policy) and `examples/research_profile.py` (two `external`
tools, a citation-and-skepticism prompt, no subagent, no write tools).

## 5. Module map

Every module below maps to at least one task in [§11](11-implementation-plan.md). Nothing
in the plan creates a file that is not on this map.

```
src/harness/
  __init__.py           PUBLIC API — the complete stable surface (see §03)
  agent.py              Agent: frozen config, construction-time validation
  run.py                RunEngine: the loop of §3. ~200 lines. No cleverness allowed.
  result.py             Result, StopReason, Usage, Step
  errors.py             Exception hierarchy (§04.7)
  _typing.py            Internal type aliases
  middleware.py         Middleware base class + with_middleware() — sugar composed from
                        ModelProvider/tool/Exporter (§4), not a seventh seam, not the loop

  tools/
    __init__.py         @tool decorator, Effect, ToolSpec, EFFECT_PROFILES
    schema.py           Python signature → JSON Schema (strict-compatible)
    registry.py         ToolSet: sorted tuple + name index, deterministic serialization
                        (not a frozenset — ToolSpec holds a Mapping and is unhashable)
    invoke.py           Execution: timeout, truncation, error capture, taint marking
    builtin/
      web.py            search, fetch          (effect=external, pre-classified)
      files.py          read_file, write_file  (read / write)
      shell.py          run_command            (danger)
      math.py           calculate              (read)

  models/
    base.py             ModelProvider protocol, ModelRequest/Response, Price
    anthropic.py        The v1 implementation
    pricing.py          Per-model $/MTok table with `as_of`; cost arithmetic in Decimal
    fake.py             FakeModel — re-exported by harness.testing

  context/
    assembler.py        Renders tools → system → messages. Deterministic by construction.
    linter.py           Double-render byte comparison → NonDeterministicPromptError
    window.py           Growth policy: context editing, then compaction
    caching.py          cache_control breakpoint placement

  budget/
    ledger.py           Budget, Ledger, reserve/settle, worst-case estimation

  policy/
    base.py             Policy protocol, Verdict lattice (ALLOW < ASK < DENY)
    engine.py           Composition: max() of verdicts, short-circuit on DENY,
                        then approval resolution for a surviving ASK (ADR-021 —
                        approval is the engine's job; a policy is sync and pure)
    builtin.py          EffectPolicy, TaintPolicy, EgressPolicy
    taint.py            TaintTracker

  memory/
    base.py             Store protocol
    viking.py           OpenViking — semantic recall; `recall` is `external` (ADR-035)
    inmemory.py         Dict-backed
    sqlite.py           SQLite-backed (default persistent store)

  observe/
    events.py           The closed 15-event taxonomy (§05.1)
    bus.py              EventBus: sync fan-out, exporter isolation
    transcript.py       Append-only JSONL writer/reader, with redaction
    redact.py           Secret scrubbing
    console.py          Human-readable exporter
    otel.py             OpenTelemetry exporter (optional extra)

  secrets.py            Secret type

  plugins/
    registry.py         Explicit registration; opt-in entry-point discovery

  testing/
    __init__.py         FakeModel, record, replay, no_network, assert_* helpers

  cli/
    __init__.py         new · run · trace · cost · doctor
```

## 6. Concurrency model

- **Async is the core.** `RunEngine` is `async`. `Agent.run()` is a thin sync facade over
  `Agent.arun()` (ADR-007).
- **Sync tools are offloaded** to `asyncio.to_thread` automatically. A beginner writes
  `def` and never learns the word "coroutine".
- **Calling `.run()` from inside a running event loop** is detected and raises
  `SyncInAsyncContextError` telling you to use `await agent.arun(...)` — instead of the
  standard, baffling `RuntimeError: This event loop is already running`.
- **Parallelism is derived, not configured.** Tools whose effect class is parallel-safe
  (`read`, `external`) run concurrently under a semaphore of `max_parallel_tools`
  (default 8). `write` and `danger` serialize. The developer never chooses.
- **Cancellation** is cooperative: cancelling the task cancels in-flight tool calls,
  flushes the transcript, and returns a `Result` with `StopReason.CANCELLED`.

## 7. Failure philosophy

| Class | Behavior | Rationale |
|---|---|---|
| **Tool raised** | Caught, converted to `tool_result(is_error=True)` with the message. Run continues. | The model can often recover. A crashing tool must never destroy a run's accumulated work. NFR-08. |
| **Provider transient** (429, 5xx, connection) | SDK retry (2 attempts), then harness backoff up to the wall-clock budget. | Transient by definition. |
| **Provider permanent** (400, 401, 404) | No retry. `Result(stop_reason=ERROR)` from `try_run`, raised from `run`. | Retrying a malformed request wastes money and time. |
| **Budget / step / time exhausted** | Graceful stop with a normal `Result`; partial text preserved. | Not an error — an expected boundary. |
| **Policy DENY** | Tool not executed; `tool_result(is_error=True, "denied by policy: …")` returned to the model. Run continues. | Tells the model to try another route instead of hanging. |
| **Configuration error** | Raised at `Agent(...)` construction or at `@tool` import. Never at run time. | Poka-Yoke: runtime → configuration-time. |

## 8. Why this is not over-engineered

A fair challenge to any architecture document. The count:

- **4 public classes** a user can construct (`Agent`, `Budget`, `Secret`, plus decorators).
- **5 protocols** total, each with ≥ 2 real implementations planned.
- **1 loop**, no strategy objects, no middleware chain, no dependency-injection container,
  no plugin lifecycle with hooks.
- **0 features built "for later"** — every module above is required by a numbered
  requirement in [§01](01-requirements.md).

Deliberately absent: an `AgentBuilder`, an `ExecutorFactory`, a `MiddlewarePipeline`, a
`ContextStrategy` interface, an `AbstractTool` base class, and a plugin manifest DSL. Each
was proposed and rejected in Round 2 for failing the plugin-boundary test or KISS.
