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
this is what makes the whole system testable with fakes, and it is enforced by an
import-linter rule in CI ([§09.6](09-testing.md)), not by discipline.

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
| Run loop | ❌ | — | ❌ one | **CORE** — and see below |
| Context assembler | ❌ | ❌ must be byte-deterministic | ❌ | **CORE** |
| Budget ledger | ❌ | ❌ security-critical | ❌ | **CORE** |
| Schema generation | ❌ | ✅ | ❌ | **CORE** |
| Retry | ❌ | ✅ | ❌ SDK does it | **CORE** |
| Model router | ❌ | — | ❌ | **CORE** (and mostly deferred, ADR-006) |

**Five seams. Everything else is core.** Three of the core items are core specifically
because making them overridable would let a plugin *defeat* an invariant: a replaceable
ledger can be replaced with one that always says yes; a replaceable assembler can
reintroduce cache invalidation; a replaceable loop can skip the policy check. Extension
points that can disable safety are not extension points, they are vulnerabilities.

The loop is not extensible, but it is **observable** (events) and **interceptable at the
defined seams** (policies decide, tools act). That covers the legitimate reasons someone
would want to override it.

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

  tools/
    __init__.py         @tool decorator, Effect, ToolSpec, EFFECT_PROFILES
    schema.py           Python signature → JSON Schema (strict-compatible)
    registry.py         ToolSet: frozen, name-sorted, deterministic serialization
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
