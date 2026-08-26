# 01 — Requirements

## 1. Problem statement

Building an LLM agent in Python today means choosing between two bad options.

Option A is the raw SDK. You write the tool-call loop yourself. It works in an afternoon,
and then you discover, one production incident at a time, that you have no budget ceiling,
no permission model, no cache strategy, no transcript, and no way to test it without
paying for tokens. Every team rebuilds the same five things, badly, in a different order.

Option B is a large framework. It has all five things, plus forty more, arranged behind
nine abstractions you must learn before you can print "hello". The cost of the first agent
is a week, and the cost of understanding why the second one is slow is a month.

**Harness targets the gap:** the loop and the five production concerns, with the cognitive
surface of Option A.

## 2. Goals

| # | Goal | Measured by |
|---|---|---|
| G1 | A working agent in ≤ 5 lines and ≤ 3 concepts | SC-1 |
| G2 | A runaway agent cannot exceed its budget | SC-2 |
| G3 | Untrusted content cannot trigger irreversible actions | SC-3 |
| G4 | Prompt caching works by default, without the author thinking about it | SC-4 |
| G5 | Agents are testable with zero API spend | SC-5 |
| G6 | Third parties can ship tools, providers, stores, policies and exporters without forking | SC-6 |
| G7 | Every run is fully explicable after the fact | SC-7 |

## 3. Success criteria (all are executable)

| # | Criterion | Threshold | Where verified |
|---|---|---|---|
| SC-1 | **Time to first agent.** Five people who have never seen the library, given only the README, produce a working agent. | Median ≤ 10 min; ≥ 4/5 succeed without asking a question | [§14.2](14-validation-plan.md) |
| SC-2 | **Budget is a ceiling.** Over 1 000 randomized adversarial runs (tool loops, huge results, retries), actual spend never exceeds the declared budget. | 0 violations | Property test, [§09](09-testing.md) |
| SC-3 | **Taint containment.** The red-team suite's exfiltration scenarios are blocked. | 100 % of RT-01…RT-12 blocked | Red-team suite, [§09.5](09-testing.md) |
| SC-4 | **Cache effectiveness.** On the 10-turn conversation fixture, cache reads on turns 3+. | ≥ 90 % of input tokens read from cache | Benchmark, [§14.3](14-validation-plan.md) |
| SC-5 | **Zero-cost testing.** The full test suite runs green with no network access. | `no_network()` active in CI; 0 outbound calls | CI gate |
| SC-6 | **Extension without forking.** Each of the 5 plugin types has a working out-of-tree example. | 5/5 in `examples/plugins/` | M4 DoD |
| SC-7 | **Explicability.** From a transcript alone, `harness trace` reconstructs every model call, tool call, verdict and cost. | Byte-identical replay of a recorded run | Golden test |

## 4. Requirements

### 4.1 Functional

| ID | Requirement | Priority |
|---|---|---|
| FR-01 | Define an agent from a name, a job description and a list of tools | Must |
| FR-02 | Run an agent to completion, returning text, cost, steps and stop reason | Must |
| FR-03 | Multi-turn conversation with retained history | Must |
| FR-04 | Define a tool from a plain Python function with type hints | Must |
| FR-05 | Every tool declares an effect class; the harness derives its handling | Must |
| FR-06 | Enforce a per-run budget in USD, steps, wall-clock and tokens | Must |
| FR-07 | Evaluate policies before every tool execution; refuse on DENY | Must |
| FR-08 | Track taint from external tool output and block danger tools | Must |
| FR-09 | Human-in-the-loop approval for ASK verdicts | Must |
| FR-10 | Emit a structured event stream; persist it as a transcript | Must |
| FR-11 | Resume a run from a transcript | Should |
| FR-12 | Delegate a sub-task to a subagent with its own model and budget | Should |
| FR-13 | Persist and recall memory across runs | Should |
| FR-14 | Manage context growth (editing, then compaction) automatically | Must |
| FR-15 | Register plugins explicitly; discover installed ones only on request | Must |
| FR-16 | CLI: scaffold, run, trace, cost, doctor | Should |
| FR-17 | Stream output tokens to a callback | Should |
| FR-18 | Cancel a running agent cooperatively | Must |

### 4.2 Non-functional

| ID | Requirement | Target |
|---|---|---|
| NFR-01 | **Import time** — the cost of `import harness` | < 200 ms, no network, no provider SDK import until first use |
| NFR-02 | **Harness overhead** — wall-clock added per step, excluding model and tool time | < 15 ms p95 |
| NFR-03 | **Memory** — retained bytes for a 100-step run | < 50 MB excluding tool payloads |
| NFR-04 | **Type safety** — `mypy --strict` on the whole package | 0 errors, no `Any` in public signatures |
| NFR-05 | **Dependency weight** — required runtime dependencies | ≤ 3 (`anthropic`, `typing-extensions`, `jsonschema`); everything else optional extras |
| NFR-06 | **Python support** | 3.11, 3.12, 3.13 |
| NFR-07 | **Determinism** — same transcript + same fake model ⇒ same events | Byte-identical |
| NFR-08 | **Failure containment** — a tool raising must never crash the run | Converted to `is_error` tool result |
| NFR-09 | **Concurrency** — parallel-safe tools execute concurrently | Bounded by `max_parallel_tools`, default 8 |
| NFR-10 | **Documentation** — every public symbol has a docstring with a runnable example | Enforced by a doc test in CI |

### 4.3 Constraints

| ID | Constraint | Source |
|---|---|---|
| C-01 | Python library, `pip install`-able, in-process. No server required to run an agent. | Product decision |
| C-02 | Anthropic Claude is the only provider implemented in v1. The provider seam exists and is proven by a fake, not by a second vendor. | Scope |
| C-03 | Open source. Third-party plugins are untrusted code from a supply-chain perspective, but are **not** sandboxed. | [§06.6](06-safety.md) |
| C-04 | Default model `claude-opus-5` at `effort="medium"`. Cost efficiency never comes from silently substituting a weaker model. | ADR-006 |
| C-05 | No mandatory external infrastructure. SQLite is the heaviest default. | C-01 |

## 5. Non-goals

Each is stated with the reason, so a future contributor does not re-litigate it by accident.

| Non-goal | Why not in v1 |
|---|---|
| **Hosted service / control plane** | A different product with a different cost structure. The library must be the thing that works first; a service can wrap it later without changing the core. |
| **Durable execution engine** (Temporal-style) | Transcript + `resume()` covers the realistic failure (process died) at a fraction of the complexity. Exactly-once side effects need a distributed transaction model that a library cannot provide honestly. |
| **Plugin sandboxing** | Sandboxing Python meaningfully requires subprocess or WASM isolation and an IPC protocol. Claiming a boundary we do not enforce is worse than documenting that there is none. |
| **LLM-based model routing** | Paying a model call to decide which model to call is usually a net loss, and no routing policy could be named today that the council agreed was correct. `subagent(model=...)` captures most of the savings explicitly. |
| **Vector store / RAG engine** | RAG is an application concern built *from* tools, not a harness primitive. A `search` tool is one function. Embedding a vector DB would add the heaviest dependency in the stack to serve a subset of users. |
| **Multi-agent orchestration graphs** | Subagents cover fan-out. Arbitrary agent graphs are a research area, not a v1 requirement. |
| **Visual builder / no-code UI** | Out of scope for a library. |
| **Fine-tuning, evals platform, prompt management** | Adjacent products. |

## 6. Assumptions

| # | Assumption | If wrong |
|---|---|---|
| A-1 | Users have an Anthropic API key and can make outbound HTTPS calls | The library is unusable; no mitigation, this is the premise |
| A-2 | Typical agents have 1–30 tools | Beyond ~50, tool schemas dominate the prefix; tool search becomes necessary (v2, tracked in [§13](13-risk-register.md)) |
| A-3 | Typical runs are ≤ 50 steps and ≤ 10 minutes | Longer runs need durable execution (non-goal) |
| A-4 | Token counting via the provider's `count_tokens` is accurate enough for pre-flight budgeting | Budget becomes advisory rather than a ceiling; RISK-03 |
| A-5 | Published per-model prices change infrequently | Stale pricing table; mitigated by the 90-day CI freshness gate (T-2.1) |
