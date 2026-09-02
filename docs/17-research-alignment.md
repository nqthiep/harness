# §17 — Alignment against the unified research, and the build-out plan

> Source: *"Unified Research: Agent Framework, Agent Harness, Interface and
> Architecture"* (snapshot 2026-08-29), covering 12 frameworks, 9 harnesses, a
> weighted 10-category matrix, and an API interface appendix with 5 anti-patterns and
> one proposed interface.
>
> This document does three things: **what to learn**, **what to avoid**, and **what to
> build next**. Every gap named here is **checked by running code**, not by rereading
> our own documentation — the past 41 rounds have shown that reading doesn't find what
> only running finds.

> **Current status: the M6-M10 plan in `## 4` is COMPLETELY DONE** — see
> `design/08-roadmap-and-release-plan.md` for the table of what's done, where, and
> which tests. THIS file stays as an ANALYSIS RECORD at the time it was written
> (the self-score in §1, the framework comparisons in §2/§3, the interface decision in
> §3.3) — old numbers/statuses are not rewritten to pretend they were always current;
> every table below has an annotation right next to it stating where that item was
> closed.

---

## 1. Self-scoring against the research's own weighted matrix

This is a **self-score**, so it isn't directly comparable to the research's own scores
(a different grader, and they scored already-mature projects). Its value is in its
**shape**: which dimensions are high, which are low.

| Dimension | Weight | Score | Contribution | Why |
|---|---:|:---:|---:|---|
| Control / Safety | 15% | 4/5 | 12.0 | Taint lattice, effect class, policy lattice, refused at construction, `Secret`, egress. **Penalty: no isolation layer at all** |
| Reliability | 12% | 3/5 | 7.2 | A budget ceiling, checkpointing, resume. **Penalty: no idempotency key, cancellation swallowed, no failure injection** |
| Testability | 12% | 4/5 | 9.6 | 233 tests, `FakeModel`, a parity suite, property tests, conformance. **Penalty: no declarable trajectory contract, no golden set** |
| Extensibility | 10% | 4/5 | 8.0 | 5 seams, proven with a third-party implementation. **Penalty: no MCP** |
| Observability | 10% | 3/5 | 6.0 | 15 closed event kinds, exporters, a transcript. **Penalty: no real OTel, an envelope missing traceId/tenantId/schemaVersion** |
| Cost efficiency | 10% | 4/5 | 8.0 | A pre-flight ceiling, 95.3% cache hit rate, ADR-026. **Penalty: cost/successful-task never measured** |
| Developer experience | 10% | 4/5 | 8.0 | Errors reading at grade <=5, progressive disclosure. **Penalty: SC-1b not yet measured** |
| Integration | 8% | 2/5 | 3.2 | **No MCP, no Service API, no connectors** |
| Performance | 6% | 2/5 | 2.4 | **Latency/throughput/concurrency never measured** |
| Intelligence | 5% | 3/5 | 3.0 | `effort`, adaptive thinking, `returns=`, subagents. No routing (ADR-006) |
| Ecosystem | 2% | 1/5 | 0.4 | Doesn't exist yet |
| **Total** | **100%** | | **67.8** | |

For reference, the research scored LangGraph at 89.4, PydanticAI at 87.1, Goose at
83.5, Pi at 76.8.

**The honest conclusion at the time this was written: this harness is strong on
exactly the heaviest-weighted dimensions (Safety, Cost, Testability, DX) and weak on
Integration, Performance, Observability, Reliability.** The plan below (`## 4`) is
ordered by `weight x gap`, not by what's fun to build — and **has since run to
completion**. The 67.8/100 table above is the ORIGINAL score, before M6-M10; a new
self-score would need the same rigor the original had (against a real corpus, a
different grader) that an automated pass can't reproduce —
`design/08-roadmap-and-release-plan.md §2` checks each PENALTY reason in the "Why"
column against today's code instead of inventing a new number.

---

## 2. STRENGTHS WORTH LEARNING FROM

### 2.1 What the research gets right, and where the harness already does it

| Principle in the research | Where in the harness |
|---|---|
| *"Design the harness so the agent finds it hard to get wrong"* | 83 failure modes, ranked Impossible -> Documented ([§08](08-poka-yoke.md)) |
| *"The model may PROPOSE a tool call, never self-grant execution"* | The policy gate is a graph edge, proven by `unguarded_paths()` (ADR-032) |
| Poka-Yoke's **Schema** layer | `@tool` generates a JSON Schema, `strict:true`, `additionalProperties:false` |
| Poka-Yoke's **Execution** layer | An explicit-transition graph, a bounded loop, timeouts |
| Poka-Yoke's **Permission** layer | The verdict lattice composes with `max()` — a policy can only ever tighten (P-2) |
| Poka-Yoke's **Human gate** layer | `approve=` + a durable `interrupt()` |
| Poka-Yoke's **Budget** layer | A 3-axis ceiling, reserved **before** every call (ADR-017/026) |
| Poka-Yoke's **Audit** layer | 15 closed event kinds, a transcript, every verdict recorded |
| Anti-pattern 1 *(only `run(prompt)->str`)* | `Result` carries stop_reason, cost, usage, steps, tainted, messages, value, `tools_run` |
| Anti-pattern 2 *(final text as the only state)* | A transcript + checkpointing; state is an event log |
| Anti-pattern 5 *(treating the protocol as the security boundary)* | The boundary sits at the policy engine, not the transport |
| *"Cost/successful-task, not cost/task"* | The right formula — **not yet measured**, see M8 |
| *"Approval doesn't substitute for isolation"* (Cline) | Already stated as a non-goal in [§01.5](01-requirements.md) — **but that was an evasion, see M7** |

### 2.2 What's worth learning that the harness **lacked** — checked by code (at the time)

```
Current event envelope : ['seq','ts','run_id','kind','step','data']
The research wants     : + schemaVersion, traceId, tenantId
Current RunContext     : ['agent_name','deadline','run_id','safety','step','tainted']
The research wants     : + principal, tenantId, scopes
Current ToolCall       : ['id','name','arguments','spec']
The research wants     : + authorization{principal,scopes}, idempotency_key,
                           budget{timeout_ms,max_retries}
grep -ril idempot src/ -> just one comment line, no mechanism
grep -ril sandbox src/ -> NOTHING
grep -ril mcp src/     -> NOTHING
```

**A snapshot table AT THE TIME OF WRITING — the "Status" column is kept as a fixed
marker, the new column on the right says what's true today:**

| # | Strength worth learning | From | Status when written | Today |
|---|---|---|---|---|
| S-01 | **An idempotency key on every tool call** | Anti-pattern 3; OWASP duplicate-action | Absent | `execute_once` (T-6.1) built, a real caller at the RUN LEVEL (T-9.2) — the TOOL-CALL LEVEL was still open, see `design/07 §7` item 2 |
| S-02 | **Approval as a RECORD**, not a boolean: decision id, actor, policy version, expiry, an audit entry | §6 acceptance criteria | Absent | **Done** — `Decision`/`DecisionLog` (T-8.2) |
| S-03 | **Principal / tenant / scopes** in context and in the tool envelope | Anti-pattern 4; tool envelope §8 | Absent | **Tenant done** (`RunContext.tenant_id`, N-9); principal/scopes still open, wider than what T-8.1 promised |
| S-04 | **An isolation layer**: workspace-rooted, network egress denied by default, secrets never entering the sandbox | The Poka-Yoke table, OpenHands/Goose | Absent | **Done** — M7 (`workspace.py`, `Sandbox`, egress-deny-default) |
| S-05 | **A declarable trajectory contract** (Given/When/Then: which tools must be called, which are forbidden, <=N calls, <=T tokens, <=C cost, a retry never duplicating a side effect) | §9 | Partial pieces, not yet a contract | **Done** — `harness.eval.Trajectory` (T-10.1) |
| S-06 | **Cost per successful task** instead of cost per task | §10 | Not measured | **Done** — `cost_per_success` (T-8.4) |
| S-07 | **A canonical event model + an adapter for multiple transports** | Appendix §10 | Had an event model, no adapter | **Done** — `to_dict()`, three transports (T-9.3) |
| S-08 | **A Service API**: `POST /v1/runs`, `GET /runs/{id}`, SSE events, approvals, cancel, resume | Appendix §8 | Absent (§01 chose library-first) | **Done** (except `resume`, deliberately — `harness[server]`, T-9.2) |
| S-09 | **MCP as a tool boundary** (not the whole API) | §5, protocol | Absent | **Done** — `harness.mcp` (T-9.1) |
| S-10 | **Cancellation following the real convention** — cancelling mid-model-call, mid-tool-call, mid-approval, mid-stream | §6 acceptance criteria | **Had a bug, see 3.2** | **Done** — T-6.2 |
| S-11 | **Failure injection** in the test suite | §9 | Absent | **Done** — `harness.testing.chaos` (T-6.4) |
| S-12 | **Measuring p50/p95 latency, throughput, concurrency** | §10 | Never measured | **Done** — `harness.eval.benchmark` (T-10.3) |
| S-13 | **A pass rate with a 95% confidence interval**, never a single bare number | §9, Terminal-Bench | Absent | **Done** — `run_golden_set` (T-10.2) |
| S-14 | **Backpressure**: a slow client never fills memory or silently loses events | §6 | Never considered | Still not addressed — `harness.server`'s SSE buffer has no ceiling; not observed in any pilot (no pilot has run yet) |

---

## 3. WEAKNESSES TO AVOID

### 3.1 From projects in the research

| # | Weakness | Whose | How the harness avoids it |
|---|---|---|---|
| W-01 | **Token/call explosion, blurred semantics behind a role/task abstraction** | CrewAI | No "role"/"crew" abstraction. A subagent is a tool with a budget inside the parent's budget (ADR-030), measurable |
| W-02 | **"You must build permission, sandbox, MCP, subagent, plan yourself"** | Pi | Safety is never left to the caller: `recall` ships already `external`, a `danger` tool is refused by default |
| W-03 | **Approval mistaken for isolation** | Cline | The research states this plainly, and the harness **was making exactly this mistake** -> M7 |
| W-04 | **A wide surface, conflating the agent with a data abstraction** | LlamaIndex | 5 seams, not 9. A plugin-boundary test ([§02.4](02-architecture.md)) |
| W-05 | **Migration risk from having a stated successor** | AutoGen, Semantic Kernel | Not built on anything with a published successor path |
| W-06 | **High release velocity -> regression risk**, stale tool signatures | OpenCode, Mastra | Pinned versions; a parity suite guards against drift; a `contract` version |
| W-07 | **Vendor coupling, alpha churn** | Codex | The provider is a seam; `AnthropicProvider` is an adapter, not core |
| W-08 | **Operationally heavy** | OpenHands | The Service API is an **extra**, not core. Core stays at 3 dependencies, an 87ms import |
| W-09 | **"Stars aren't adoption"** | All of §2 | No argument here is built on popularity |
| W-10 | **A single pass rate used to claim superiority** | §9 | Every number ships with how it was measured; SC-4 = 95.3% has a runnable benchmark |

### 3.2 Weaknesses of **this harness itself**, measured at the time — all five now **CLOSED**

**Y-01 — `CancelledError` swallowed. FIXED (T-6.2).** Measured at the time:

```
t.cancel(); await t   ->  returns Result(stop_reason="cancelled")
                          does NOT raise CancelledError
a side effect running in the background -> No (the tool was correctly cancelled) OK
```

The side-effect part was **correct**. But swallowing `CancelledError` breaks asyncio's
own cancellation protocol: an outer `TaskGroup` or `asyncio.wait_for` never sees that
the cancellation happened. This is a known failure class, and the research lists
cancellation as its own acceptance test. `try_run()` returning a `Result` is a design
choice (IDL-11) — but **cancellation isn't an ordinary stop reason**, it's a control
signal.

**Y-02 — No isolation layer. PARTIALLY CLOSED (M7).** [§01.5](01-requirements.md)
recorded sandboxing as a non-goal because it "needs process/WASM isolation — a
different product." The research rejects that at the level of principle: *"approval
does not mean sandbox,"* and Isolation is one of 8 Poka-Yoke layers. A container can't
be packaged inside a library, but the three things a library CAN do are now built: a
workspace root (T-7.1), egress denied by default (T-7.2), a seam for plugging in a real
sandbox (`Sandbox`, T-7.3 — `InProcess`/`Subprocess` ship out of the box, making NO
claim of namespace/cgroup isolation, ADR-047 says so plainly; a real container runtime
is still the operator's job).

**Y-03 — The envelope lacked fields for multi-tenant tracing. FIXED (T-8.1), then a
second hole patched (N-9).** No `tenant_id`, `trace_id`, `schema_version` at the time.
Envelope v1 added all four — but when it was first built, `tenant_id` only reached
`Event`/`EventBus` (telemetry), had NOT yet reached `Policy.check()`
(`RunContext`/`_Ctx`) — a policy couldn't decide differently per tenant until N-9
(`design/07 §3`).

**Y-04 — Integration at 2/5. CLOSED (M9).** No MCP, no Service API at the time. The
research weights Integration at 8% and says *"MCP should be the tool boundary"* — the
largest weighted gap at the time. `harness.mcp` (T-9.1) + `harness.server` (T-9.2)
close both.

**Y-05 — Performance never measured. CLOSED (M10).** 6% weight, and the only number
ever measured was import time — now a CALLABLE measurement
(`import_cold_start_ms()`), plus real p50/p95/throughput/concurrency
(`harness.eval.benchmark`, T-10.3).

---

## 3.3 The API interface: does it need a redesign?

**No.** Checked against the exact five contracts the research separates out: two
contracts strong, one contract with a **semantic bug** (fixed in Round 43), and two
contracts **entirely missing** — missing isn't the same as wrong, and adding them
requires no breaking change to [§04.8](04-interfaces.md).

| Contract | Status | Detail |
|---|---|---|
| **1. Invocation** | **Strong** | `run` / `try_run` / `arun` / `atry_run` / `chat` / `resume` / `aresume` / `as_tool` / `with_`. On par with PydanticAI's number of call forms; `run` raising vs. `try_run` returning is a clear, deliberate decision (IDL-11) many libraries lack |
| **2. Tool** | **Strong — possibly ahead of the field** | No library in the research derives **five behaviors from one `effect` classification**: parallel-safety, retryability, whether it taints the run, the default verdict, the audit level. A schema only checks data shape; `effect` checks *consequence* |
| **3. Event / stream** | **Weak at the time -> Strong (T-8.5)** | At the time: only `on_delta(str)`. Now: `async for ev in agent.stream(msg)` over a 16-kind taxonomy (a full envelope v1), plus an SSE transport (`harness.server`) and a CLI `--json` sharing the same shape (T-9.3) |
| **4. Session / state** | **Weak at the time -> Strong (T-8.6)** | The research: *"a session id must be treated as a resource with a lifecycle"* — ownership, TTL, concurrent writers, forking, conflicts. Now: `harness.Session` — id/owner/TTL/`.fork()`/`.resume_from()`, on the classic backend (LangGraph already has `thread_id` as its own session primitive) |
| **5. Transport** | **Deliberately absent -> opened via an extra (M9)** | Library-first ([§01](01-requirements.md)) still holds for CORE. `harness[server]` (the Service API) and `harness[mcp]` (the client) open transport as extras, without changing core |

### A semantic bug found by reading the research closely

The research flags a subtle PydanticAI detail: *"in some modes, the final output can
end the run before dangling tool calls are ever executed."* The council put exactly
that claim to the test against its own harness:

```
model returns:  text "All done." + tool_use{write}   with stop_reason "end_turn"
the loop     :  stop_reason=completed . tools that ran: []      <- SILENTLY DROPPED
                tool_use in the conversation: ['c1']
                tool_result:                  []                 <- VIOLATES I-3
the graph    :  tools that ran: [1] . has a ToolMessage: True    <- CORRECT
```

Two bugs in one. The tool was dropped, **and** the saved conversation carries a
`tool_use` with no matching `tool_result` — replaying that conversation to the provider
would be refused outright.

**The fix: a tool call runs because it EXISTS, not because the provider happened to
label it `"tool_use"`.** The graph backend already got this right; this time the
hand-written loop was the one catching up — the first time the underdog reversed after
seven rounds.

### Which library to learn from, for what — specifically

| Library | The point worth learning | Why the harness needs it |
|---|---|---|
| **PydanticAI** | `run_stream_events()` and `iter()` — consuming **events**, not just text | Closes contract 3. The 15-kind taxonomy already existed; only a pull-style exit was missing |
| **PydanticAI** | Dependency injection (`deps_type`) separating state from the agent | Today, serving multiple tenants from one `Agent` means `with_()`-ing a copy each time. DI allows **one** agent, **many** contexts — and is the natural place to put `principal`/`tenant_id` (S-03) |
| **OpenAI Agents SDK** | **Run state + interruptions as first-class**: serializable, interruptible, resumable | The harness's `resume(transcript_path)` is weaker: state is a plain file, not a typed object |
| **LangGraph** | A **versioned** stream (`astream_events(version=...)`) | The taxonomy has already changed twice (Rounds 27, 35) with no version — this is exactly Y-03 |
| **OpenHands** | An Agent Server over REST/OpenAPI + WebSocket, a session API key | The shape for M9; having an OpenAPI spec makes the contract testable |
| **Goose** | Multiple sessions running concurrently, **isolated from each other** | Contract 4. Round 37 fixed a budget/taint leak across threads, but there was still no Session object |
| **Cline** | Approval as a record with an actor and an audit trail | S-02 |
| **Mastra** | `.generate()` / `.stream()` returning separate `toolCalls`/`toolResults`/`steps`/`usage` | **Not adopted.** Gathering it all into one `Result` is deliberate — four separate promises make it easy to miss reading one |
| **CrewAI** | — | **Not adopted.** The research states plainly: many knobs on Agent -> a wide surface, semantics hidden behind an abstraction |

### The decision

**No redesign. Fix one, add two, leave the rest unchanged.**

Contracts 1 and 2 are this package's strongest parts and are already protected by
§04.8 — breaking them to "modernize" would trade something proven for something
unproven. Contracts 3 and 4 can be added **without changing any existing signature**:
`Agent.stream()` is a new method, `Session` is a new object. Contract 5 was already
deliberately out of scope.

This adds two tasks to the plan:

- **T-8.5 A consumable event stream** — `async for ev in agent.stream(msg)` over the
  exact 15-kind taxonomy, carrying `schema_version`. The research wants text deltas,
  tool-call deltas, tool results, approval requests, retries, cancellation, and finals
  distinguished — this is where they appear.
- **T-8.6 `Session` as a resource** — id, ownership, TTL, fork, resume, and a
  concurrency boundary. Round 37 fixed the leak; this names the thing that already
  existed implicitly.

Both sit in M8 rather than earlier: they depend on envelope v1 (T-8.1), since adding a
stream exit before the envelope is versioned would ship a contract only to have to
break it.

---

## 4. THE PLAN — M6 through M10

**DONE, COMPLETELY.** The table below is the ORIGINAL plan, kept as-is as evidence that
every task answered all nine points before any code existed —
`design/08-roadmap-and-release-plan.md §1` is the table of "done where, which ADR,
which test" for each row below.

Ordered by `weight x gap`. Every task follows the exact nine points from
[HARNESS.md §VIII](../HARNESS.md): **What · Why · Where · How · Depends · Contract ·
Failure · Test · Done**.

### M6 — Reliability: idempotency, cancellation, failure injection *(12% x gap 2/5)*

| Task | Content |
|---|---|
| **T-6.1 An idempotency key** | **What** Every tool call carries `idempotency_key = f"{run_id}:{call_id}"`; `write`/`danger` go through an `IdempotencyStore` before running. **Why** Anti-pattern 3: a client timeout followed by a retry can send an email twice. **Where** `dispatch.py`, `lg/runtime.py`, the `Store` seam. **How** Look up the key before executing; a hit returns the stored result instead of re-running. **Contract** `execute_once(key, fn) -> (result, was_replayed)`. **Failure** The store dies -> fail closed for `write`/`danger`, fail open for `read`. **Test** A retry with the same key doesn't duplicate; two different keys run twice. **Done** New ACs + one parity row. |
| **T-6.2 Correct cancellation** | **What** `CancelledError` is re-raised after cleanup. **Why** Y-01. **Where** `run.py`, `lg/runtime.py`. **How** Catch it to clean up, log `run.finished(cancelled)`, then `raise`. **Failure** A tool must never keep running after cancellation (already correct today). **Test** An outer `TaskGroup` sees the cancellation; no side effect leaks. |
| **T-6.3 Retry policy by effect class** | **What** `read`/`external` can retry; `write`/`danger` never auto-retry. **Why** The effect-class table already derives "retryable" but nothing reads it to actually retry. **Test** Property: no `write`/`danger` ever runs twice because of a retry. |
| **T-6.4 Failure injection** | **What** `harness.testing.chaos`: a provider timeout, a tool raising, a dead store, a policy raising, a model returning garbage. **Why** §9. **Done** Every scenario asserts a specific behavior, none of them crash. |

### M7 — Isolation: the one Poka-Yoke layer still empty *(15% x gap)*

| Task | Content |
|---|---|
| **T-7.1 A workspace root** | **What** A tool touching files can only see what's under a declared `workspace=`. **How** Normalize the path, then reject anything that escapes it — **refuse, don't escape** (IDL-44 already uses this exact approach for keys). **Test** `../../etc/passwd`, symlinks, absolute paths, URL-encoded `..`. |
| **T-7.2 Egress denied by default** | **What** `allowed_hosts` defaults to `()` — deny everything — not `None` = allow everything. **Why** "Safe by default: network egress is restricted." **Failure** This is a breaking change -> needs a deprecation cycle. |
| **T-7.3 The `Sandbox` seam** | **What** A `Sandbox` Protocol with `run(cmd, *, cwd, env, timeout) -> Completed`; ships `InProcess` (no isolation, stated plainly) and `Subprocess` (a clean env, cwd = the workspace, no secrets). **Why** Don't embed a container inside the library, but there must be a place to plug one in. **Done** Someone can plug in Docker/Firecracker without touching core — proven with a third-party implementation, per `proof.py §I.1`'s exact pattern. |
| **T-7.4 Secrets never reach the sandbox** | **What** `Sandbox.run` never accepts a `Secret`; the env is allowlisted. **Test** Red-team: no secret ever appears in a child process's environment. |

### M8 — Observability & Audit *(10% x gap)*

| Task | Content |
|---|---|
| **T-8.1 Envelope v1** | `schema_version`, `trace_id`, `tenant_id`, `session_id` added to `Event`; `seq` already existed. Only a version makes the taxonomy changeable without breaking exporters. |
| **T-8.2 Approval as a record** | `ApprovalRecord(decision_id, actor, policy_version, decided_at, expires_at, verdict, reason)` replacing a `bool`. **Why** S-02. **Failure** An expired approval cannot be reused. **Test** The same approval can't unlock a second run. |
| **T-8.3 A real OTel exporter** | `[otel]` was just an extra's name today. Map the 15 event kinds to spans; propagate the trace id. |
| **T-8.4 Cost per successful task** | `harness.eval.cost_per_success(runs)` — `total cost / P(success)`, with a confidence interval. **Why** S-06, and §10 states plainly that cost/task is the wrong formula. |

### M9 — Integration: MCP and the Service API *(8% x gap 3/5 — the largest weighted gap)*

| Task | Content |
|---|---|
| **T-9.1 An MCP client as the tool boundary** | **What** `harness.mcp.connect(server)` returns `ToolSpec`s. **Why** MCP is where a whole tool ecosystem already exists. **How** A tool from MCP is **required** to declare `effect`; an undeclared one defaults to `external` (taints the run), never to `read`. **Failure** This is the critical point: an MCP server is an untrusted third party, so it is **not** a security boundary (anti-pattern 5) — the policy engine still gates every call. |
| **T-9.2 The Service API** | `POST /v1/runs`, `GET /v1/runs/{id}`, `GET /v1/runs/{id}/events` (SSE), `POST .../approvals/{id}`, `POST .../cancel`, `POST .../resume`. An **extra**, `harness[server]`, core unchanged. |
| **T-9.3 A canonical event model + adapter** | One event model, several transports: in-process, SSE, CLI/JSON. No transport gets its own semantics. |

### M10 — Evaluation *(the remaining part of Testability's 12%)*

| Task | Content |
|---|---|
| **T-10.1 A trajectory contract** | Declarable exactly as §9 of the research describes: `must_call`, `must_not_call`, `requires_approval`, `max_model_calls`, `max_tokens`, `max_cost`, `output_schema`, `no_duplicate_side_effects`. Runnable against `FakeModel`. |
| **T-10.2 A golden set + a CI-gated pass rate** | A representative task set + negative cases + adversarial prompts + tool failures + policy violations. Reports pass rate **with a 95% confidence interval**, tokens, cost — never a bare number. |
| **T-10.3 A performance benchmark** | p50/p95 latency per span, throughput under concurrency, cold start. Closes Y-05. |

---

## 5. Order, and why

**This is the order it actually ran in** (`design/08-roadmap-and-release-plan.md §1`
records what really happened).

```
M6 Reliability --> M7 Isolation --> M8 Observability --> M9 Integration --> M10 Eval
   idempotency       workspace        envelope v1          MCP                trajectory
   cancellation      egress deny      approval record      service API        golden set
   retry policy      sandbox seam     OTel + cost/success  adapters           benchmark
   chaos             secret jail
```

M6 comes first because idempotency is a prerequisite for retry, for the Service API
(an idempotency key in the header), and for M10 (the "a retry never duplicates a side
effect" contract). M7 before M9 because opening up MCP before isolation exists widens
the attack surface before the wall is built.

**A warning aimed at the council itself.** The research warns about *a wide surface*
(LlamaIndex) and being *operationally heavy* (OpenHands). This plan adds MCP, an HTTP
server, a sandbox, eval — exactly the things that bloat a library. The constraint held
throughout: **core stays at 3 dependencies and imports in under 100ms**; everything in
M7-M10 is an `extra`, and the plugin-boundary test ([§02.4](02-architecture.md)) applies
to every new seam. Anything that fails that test doesn't get in.

## 6. Completion condition

**The second half is met: 13/14 of items `S-01…S-14` have a running test proving they
exist** (`## 2.2` — only S-14, backpressure, remains open, low severity, never observed
in practice because no pilot has run yet). The first half (a re-grade of >= 85) needs a
real human grader, not an automated number — see
`design/08-roadmap-and-release-plan.md §2`/`§3`.

The research says exactly what the past 41 rounds learned the hard way:

> *The final decision needs a 2-4 week pilot with the same model, the same task set, the
> same tool set, and the same security policy.* No number above substitutes for that.
