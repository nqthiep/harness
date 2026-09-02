# 08 — Roadmap and release plan

This file merges two backlogs that used to live side by side in this repo —
`07-risks-and-open-issues.md` (findings from two adversarial review rounds, the
S-/K-/N-series) and `docs/17-research-alignment.md` (the gap against the unified
research, M6-M10) — into one roadmap, and records what was done in the order it ran.

**Status: THE ENTIRE ROADMAP IS DONE.** `S-20 -> M6 -> M7 -> M8 -> K-13 -> M9 -> M10` —
six milestones, all 20 sub-tasks, each with a test + an ADR. The full suite is green,
ruff/mypy are clean, every `examples/*.py` runs. What's left before v1.0 is no longer
code — see `## 3`.

---

## 1. What's done, in the order it ran

```
S-20 -> M6 (Reliability) -> M7 (Isolation) -> M8 (Observability) -> K-13 -> M9 (Integration) -> M10 (Evaluation)
 1/1        4/4                 4/4                 6/6          cleanup       3/3               3/3
```

**Why this order.** M6 before M7 because idempotency is a prerequisite for both retry
and the Service API. M7 before M9 because opening up MCP (an untrusted third-party tool
ecosystem) before Isolation exists would widen the attack surface before the wall is
built. K-13's namespace cleanup slots in before M9 because MCP needs to name its own new
invariants, and cleaning up first avoids stacking another colliding namespace on top.
M10 last because the trajectory contract needs an already-stable surface (MCP,
idempotency, sandbox) for `must_call`/`must_not_call` to mean anything. Full original
reasoning: see this file's git history.

| Milestone | Sub-task | Core mechanism | ADR | Test |
|---|---|---|---|---|
| **S-20** | An unlimited-budget warning | `EventKind.BUDGET_UNLIMITED`, fired once per run when `usd is None` | ADR-041 | `test_attack_s20.py` |
| **M6 — Reliability** | T-6.1 Idempotency | `idempotency.py::execute_once` — a full contract, DELIBERATELY not yet wired into `Dispatcher` (no real caller existed yet — see M9) | ADR-043 | `test_m6_t61_idempotency.py` |
| | T-6.2 Cancellation | `CancelledError` propagating correctly, never swallowed, on both backends | — | `test_m6_t62_cancellation.py` |
| | T-6.3 Retry by effect class | `read`/`external` auto-retry with backoff; `write`/`danger` get exactly one attempt | ADR-042 | `test_m6_t63_retry.py` |
| | T-6.4 Chaos testing | `harness.testing.chaos` — 5 scenarios; surfaced N-2, N-4 (fixed right away) | ADR-044 | `test_m6_t64_chaos.py` |
| **M7 — Isolation** | T-7.1 Workspace confinement | `workspace.py::confine` | ADR-045 | `test_m7_t71_workspace.py` |
| | T-7.2 Egress DENIED by default | `allowed_hosts` defaults to `()`, `None` is an explicit escape hatch — a deliberate breaking change | ADR-046 | `test_m7_t72_egress_default.py` |
| | T-7.3/7.4 The `Sandbox` seam | `sandbox.py` — `InProcess`/`Subprocess`, the SIXTH seam; a secret is explicitly refused inside env vars | ADR-047 | `test_m7_t73_t74_sandbox.py` |
| **M8 — Observability** | T-8.1 Envelope v1 | `Event` +`schema_version`/`trace_id`/`tenant_id`/`session_id` | ADR-048 | `test_m8_t81_envelope.py` |
| | T-8.2 Approval as a record | `Decision` +`policy_version` — turned out to already have every field it needed | ADR-049 | `test_m8_t82_approval_record.py` |
| | T-8.3 A real OTel exporter | `observe/otel.py::OtelExporter`, matching the mapping `docs/10 §2` already published | ADR-050 | `test_m8_t83_otel.py` |
| | T-8.4 Cost/successful-task | `harness.eval.cost_per_success` — a Wilson-scored CI | ADR-051 | `test_m8_t84_cost_per_success.py` |
| | T-8.5 Event streaming | `Agent.stream()` — an async generator, real `Event`s | ADR-052 | `test_m8_t85_stream.py` |
| | T-8.6 The `Session` resource | `session.py::Session` — wraps `Chat`, classic backend only | ADR-053 | `test_m8_t86_session.py` |
| **K-13 (remainder)** | The `P-`/`I-` namespace collision | `POL-`/`PLUG-`/`IDEM-` — see `07-risks-and-open-issues.md §2` | — | — |
| **M9 — Integration** | T-9.1 An MCP client | `harness/mcp/` — `classify_mcp_tool()`, `Scope.server`. Closes S-7/S-8/S-10/S-17, S-9 partially | ADR-054 | `test_m9_t91_mcp.py` |
| | T-9.2 The Service API | `harness/server/` — `POST /v1/runs`+`Idempotency-Key`, SSE, approvals over HTTP. The first real caller of `execute_once`, at the RUN LEVEL (doesn't close S-4 — see N-8) | ADR-055 | `test_m9_t92_service_api.py` |
| | T-9.3 A canonical event shape | `observe/events.py::to_dict()` — one shape, three transports (in-process/SSE/CLI `--json`) | ADR-056 | `test_m9_t93_canonical_events.py` |
| **M10 — Evaluation** | T-10.1 The trajectory contract | `harness/eval/trajectory.py::check_trajectory()` — 8 criteria, a pure function | ADR-057 | `test_m10_t101_trajectory.py` |
| | T-10.2 A golden set | `harness/eval/golden.py::run_golden_set()` — pass rate + CI, reusing T-8.4's Wilson interval | ADR-058 | `test_m10_t102_golden.py` |
| | T-10.3 A benchmark | `harness/eval/benchmark.py::benchmark()` + `import_cold_start_ms()` — closes Y-05 | ADR-059 | `test_m10_t103_benchmark.py` |

**Constant throughout, held across the whole roadmap:** core still has 3 dependencies,
a ~80ms import. Everything from M6 on is an `extra`
(`graph`/`viking`/`otel`/`mcp`/`server`; `eval` needs no extra — it only uses
`jsonschema`, already core). Every new seam passes the exact three-part plugin-boundary
test (`docs/02-architecture.md §4`).

### Follow-up fixes after M10, in order

Full detail for every one of these lives in
[`07-risks-and-open-issues.md §1/§3`](07-risks-and-open-issues.md) — this table stays
short on purpose rather than duplicating it.

| Id | Prompted by | One-line summary | Detail / ADR | Tests |
|---|---|---|---|---|
| N-9 | cleanup after M10 | `tenant_id` never reached `Policy.check()` | ADR-060 | `test_n9_tenant_in_context.py` |
| N-10 | user feedback: "2 API interfaces is confusing" | `Agent(durable=True)` — runs on `harness.lg.build_agent()` through the same methods as the classic backend, one model-calling seam, an auto-created default SQLite checkpoint | `docs/03-public-api.md §3.5`, `docs/02-architecture.md §3.1` | `test_durable_agent.py` (14), `test_parity.py` (3 call shapes) |
| middleware | user feedback: "can 4 patterns compose like LangChain middleware", then "standardize the hook parameters", then "invest further in real run_id/call_id" | `harness/middleware.py::Middleware` + `with_middleware()` — sugar built from 3 existing seams, not a 7th one; every hook receives one object carrying `.identity: RunIdentity`. First-pass conclusion ("contextvars don't survive the graph backend") was WRONG, based on a self-built test — re-verified against real `langgraph` internals and reversed | `docs/03-public-api.md §3.6`, `docs/02-architecture.md §4` | `test_middleware.py` (21, incl. `IdentityThreading` — no cross-talk between parallel tool calls, both backends) |
| review + fix | "review the code", then "fix the bugs you just found" | Verified by running real code, not just reading: no crash, no cross-talk; found and fixed a separate gap (11 `_emit` call sites in `lg/runtime.py` missing `step=`) later folded into N-6 | `design/07-risks-and-open-issues.md` N-6 | `test_middleware.py` (23); 636 tests passing |
| N-5/N-6 | "do all 6 remaining items" | Provider-level retry was documented but never built; `model.response`/`run.finished` missing `usage`/`latency_ms`/`duration_s` | detail in `07-risks §3` | `test_n5_n6_retry_and_usage.py` (8); 645 tests passing |
| N-1 | same batch | LangGraph had no per-tool timeout | detail in `07-risks §3` | `test_n1_graph_tool_timeout.py` (2); 647 tests passing |
| N-8/S-4 | same batch | `execute_once` had a caller at the RUN level but not the TOOL-CALL level | detail in `07-risks §3` | `test_n8_idempotent_tool_calls.py` (2); 649 tests passing |
| N-3 | same batch | LangGraph didn't support `returns=` | detail in `07-risks §3` | `test_n3_durable_returns.py` (3); 653 tests passing |
| S-11 | same batch, closing the last open item | `Actor` was a self-declaration with no authentication | `AuthEvidence` — detail in `07-risks §1` | `test_attack_s11.py` (24); 672 tests passing |
| Advisor pattern | user feedback: "how to use multiple models intelligently... an advisor pattern" | `RequireBeforePolicy` — a structural gate requiring a tool to have run before a dangerous one is even offered for approval; the advisor never grants anything itself (D-1, R-3) | ADR-061 (`docs/12-decision-logs.md`), `docs/07-cost.md §4` | `test_advisor_gate.py` (10); `examples/advisor_pattern.py`; 731 tests passing |
| Compaction x advisor gate | "check the integrity of the logic after that merge", then "fix it" | Real context compaction (from a concurrent session) could erase the durable backend's record of an earlier `consult_advisor` call, causing `RequireBeforePolicy` to falsely deny an already-cleared tool. Confirmed by direct repro before patching. The classic backend was checked separately and found immune | `state["tools_called_ever"]`, ADR-067 (`docs/12-decision-logs.md`) | `test_advisor_gate.py::DurableGateSurvivesCompaction` (+3) + a mutation test; 785 tests passing |

## 2. Re-grading — checked against the original, no new number invented

The ORIGINAL self-graded score (`docs/17-research-alignment.md §1`, before M6-M10) was
**67.8/100**. That number is now stale, but producing a new, ACCURATE score would need
the same rigor the original had (benchmarked against 12 frameworks + 9 real harnesses,
graded by someone else) that an automated session cannot reproduce — inventing a
new-sounding-precise number would be exactly the "confident but empty answer" rule §45
forbids. Instead, checking each original PENALTY reason against today's code:

| Dimension | Original penalty reason | After M6-M10 |
|---|---|---|
| Control/Safety | No isolation layer at all | Partially closed — `Sandbox` + `Workspace` + egress-deny-default, but NOT real namespace/cgroup isolation (ADR-047 says so plainly) |
| Reliability | No idempotency key, cancellation swallowed, no failure injection | Fully closed (`execute_once`, even before it was wired into dispatch — N-8; correct cancellation; `chaos`'s 5 scenarios) |
| Testability | No trajectory contract, no golden set | Fully closed (M10) |
| Extensibility | No MCP | Closed (M9/T-9.1) |
| Observability | No real OTel, an envelope missing trace/tenant/schema | Fully closed (M8) |
| Integration | No MCP, no Service API, no connectors | MCP + the Service API closed; connectors (beyond MCP) still don't exist |
| Performance | Latency/throughput/concurrency never measured | Closed (M10/T-10.3) |
| Ecosystem | Didn't exist | Unchanged — out of scope for M6-M10 |

What's needed before attaching a new number: a human (not an automated session)
re-grading using `docs/17 §1`'s exact method — the same 1-5 scale, benchmarked against
the real corpus.

## 3. What's left before v1.0

No longer CODE work. Per `docs/17 §6`'s criteria:

1. **A real re-grading by a human** — see `## 2`.
2. **A 2-4 week pilot** — the same model, task set, tool set, security policy. No
   self-graded number substitutes for this; the golden set (M10) is the tool that
   MEASURES a pilot, not a replacement for one.
3. **One technical item still open** (N-1/N-3/N-5/N-6/N-8/S-11 are all closed), not
   blocking release — `07-risks-and-open-issues.md §7`: S-9's label-re-pointing half,
   DELIBERATELY deferred (the same reason as K-12: building fingerprinting for a threat
   never observed would mean inventing that threat model, exactly what rule §45
   forbids) — not a matter of time.

**After v1.0:** extend based on measured need, not a preset schedule (rule §8.4's "cut
from the mandatory path, don't throw away," applied consistently across this whole
roadmap) — S-9's `ServerIdentity`+`fingerprint` is the most natural candidate if a real
MCP re-pointing incident is ever observed.
