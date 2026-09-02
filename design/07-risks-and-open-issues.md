# Risks, trade-offs, and open issues

This file exists because of the research's own rule §45: **better to say "Not Enough
Evidence" than to guess.** Every finding from the two adversarial review rounds
([review-kiss.md](review-kiss.md), [review-security.md](review-security.md) — 58
findings, `S-`/`K-`) and every bug caught while building the M6-M10 roadmap (`N-`) is
reviewed here, re-checked against **today's real code** before being marked "fixed" or
"stale" — never guessed from the original review text.

**Shared discipline:** every entry below answers — is it still true? where is it fixed?
what is NOT fixed, and why? Deep technical detail (code, ADRs, tests) lives in
`docs/12-decision-logs.md`; this file keeps only enough to understand the decision, not
the full reasoning restated.

---

## 0. Status summary

**All 58 original findings have been reviewed. 10 new findings (N-1…N-10): N-1…N-9
self-caught while building M6-M10, N-10 closes direct user feedback ("2 API interfaces
is confusing") that came afterward.** Genuinely still open, today: **1 item**
(deliberately deferred, not a time shortage) — see `## 7`.

### Security (S-1…S-29)

| Id | Summary | Status |
|---|---|---|
| S-1 | Context compaction calling the model without `reserve()` | Stale — the mechanism the review describes doesn't exist |
| S-3 | `Secret[T]` leaking through a tool result | **Fixed**, both sources (`Grants.sensitive` + `.reveal()`) |
| S-4 | Idempotency at the SINGLE tool-call level | **Partially fixed** (N-8) — retry-mid-run for the same call is deduped on both backends; a crash-across-process is still a known boundary (`docs/05 §3`'s resume rule), not claimed closed |
| S-5 | Memory provenance forgeable | Stale — the real mechanism is simpler, no such hole |
| S-6 | An old grant beating a new taint DENY | Already correct, just missing a test |
| S-7, S-8, S-10 | `ServerIdentity`/hints lowering effect/`proposed_scope` for MCP | **Fixed** — `harness.mcp` (T-9.1) |
| S-9 | An MCP grant leaking between two servers | **Partially fixed** — different labels are blocked; ONE label re-pointed to a different endpoint is not yet |
| S-11 | `Actor` a self-declaration, unauthenticated | **Fixed** — `AuthEvidence` + `Agent(require_approval_evidence=True)` DENYs a `human` actor without evidence |
| S-12 | Three conflicting resume signatures | Stale — doesn't exist in the code |
| S-13 | A sub-agent not capped to the parent's step/wall-clock | **Fixed** |
| S-14 | Overlapping reservations not blocked | **Partially fixed** — the race is blocked; `Ledger.void()` not yet needed (0 callers) |
| S-15 | A stateful policy shared across requests | **Fixed** (LangGraph); the classic backend was already correct, by a deliberately different mechanism |
| S-16 | `accepts_tainted` settable inside `@tool` | **Fixed** — only the operator can set it |
| S-17 | Injection through an MCP tool's `description` | **Fixed** — alongside T-9.1, closed with documentation + a fail-closed default |
| S-18 | `EgressPolicy` not blocking DNS rebinding | **Fixed with documentation** — no fix available at the synchronous policy layer |
| S-19 | Sticky-per-run taint launderable | **Fixed** — a per-message label (LangGraph); sticky is deliberate (classic backend) |
| S-20 | `Budget(usd=None)` skipping a promised warning | **Fixed** |
| S-21 | `Ledger.snapshot()` losing the `blocked` flag | **Fixed** |
| S-22 | Underpricing when a cache write happens | **Fixed** |
| S-23 | `call_key` missing a domain separator | Stale — the mechanism the review describes doesn't exist |
| S-24 | `EventBus`/`seq` shared across threads | **Fixed** — broader than the original description |
| S-25 | An argument not escaped to the approver + no ASK ceiling | **Fixed** |
| S-26 | `Scope.args` coerced to strings | Stale — already `Any`, matching preserves type |
| S-27 | Stale taint/confidentiality within the same batch | **Fixed** — the confidentiality branch was real |
| S-28 | A sub-agent ASK with no path to the parent's `approve` | Already correct, just missing documentation |
| S-29 | Grant reuse not logged to audit | **Fixed** — applies only to the LangGraph backend |

### KISS (K-series)

| Id | Summary | Status |
|---|---|---|
| K-6 | `Confidentiality` with no data source | Moot — S-3 gave it a source |
| K-7 | `Reservation.exact` read by nobody | Stale — **not cut**, has a real consumer |
| K-9 | `Result.raise_for_status()` redundant | **Cut** |
| K-10 | A 9-span OTel taxonomy was too much | Trimmed the plan to 4 spans — no OTel code existed yet to cut |
| K-11 | An `end_strategy` naming collision | **Fixed** (renamed) |
| K-12 | `ServerIdentity{label,fingerprint}`'s `fingerprint` format unsettled | **Fixed** — lowered to `ServerLabel` for v1 |
| K-13 | Invariant numbers colliding across `design/*.md` files | **Fixed**, in two passes |
| K-22 | "14 names, one import" contradicted by its own Tier-3 example | **Fixed** |
| K-23 | Nine tunables was too many | **Not cut** — seven of the nine were already stale or never existed |
| K-28 | The sample interface missing "five things" | **Fixed** — corrected to exactly three, stating that `session` belongs to the service layer |

### New findings, caught while building M6-M10 (N-series)

| Id | Summary | Status |
|---|---|---|
| N-1 | LangGraph had no per-tool timeout | **Fixed** — `Ledger.tool_timeout()` + `_with_timeout()`, the same two-message logic as `dispatch.py` |
| N-2 | `try_run()` raised outright when `returns=` didn't match | **Fixed** |
| N-3 | LangGraph didn't support `returns=` | **Fixed** — `Runtime.finish()` now calls `run.py::parse_returns()` before emitting `run.finished`, the same implementation as the classic backend |
| N-4 | A provider error crashed straight out, on both backends | **Fixed** |
| N-5 | Provider-level retry was documented but never built | **Fixed** — `retry.py::with_provider_retry()`, one implementation for both backends |
| N-6 | `model.response` AND `run.finished` missing documented fields | **Fixed** — `usage`/`latency_ms`/`duration_s` now emitted fully on both backends |
| N-7 | `Agent.with_()` losing four fields, on every call | **Fixed** |
| N-8 | `execute_once` had a real caller but was never wired into tool dispatch | **Fixed** — wired into `Dispatcher._invoke`/`_run_tools`, both backends; closes exactly half of S-4's "retry mid-run," not all of it |
| N-9 | `tenant_id` never reached `Policy.check()` | **Fixed** |
| N-10 | "2 API interfaces" (classic backend vs. LangGraph) | **Fixed** — `Agent(durable=True)` |

---

## 1. Detail — security (S-series)

### Fixed with code

**S-3 — `Secret[T]` leaking through a tool result, both sources.** `Grants.sensitive`
(the operator flagging a tool) pairs with `policy/label.py`'s two-axis `Label` — landing
together with S-16/S-19 (below). The first source, calling `.reveal()` on a `Secret` and
the value showing up verbatim in the returned payload:
`secrets.contains_live_secret()` detects it using the exact matching `redact()` uses (it
never calls `.reveal()` itself); `emits_of()` raises that message's label to `SECRET`
when detected, even though `redact()` already stripped the token from the bytes the
model sees — the rest of the message still needs the label to keep it from leaving
through a `PUBLIC` sink. `tests/test_attack_s3.py`, mutation-tested, both backends.

**S-11 (fixed) — `Actor` a self-declaration, no authentication.** Closed in two steps,
two passes apart: `Approval(ok, actor=...)` (the earlier pass) opened an OPTIONAL
channel for `approve=` to report a real identity instead of a generic placeholder, but
couldn't stop a callback DELIBERATELY lying — `Decision.actor` recorded verbatim
whatever the callback said. `AuthEvidence` (this pass) closes the rest:
`policy/decision.py::AuthEvidence` (requiring `channel`, `channel_message_id`,
`principal` — exactly review-security.md's self-stated "minimal fix" bar: evidence
returned by the CHANNEL ITSELF, not typed by the callback), `Approval`/`Decision` now
carry `evidence=`, and `Agent(require_approval_evidence=True)` /
`build_agent(require_approval_evidence=True)` is where a deployment turns enforcement
ON — `PolicyEngine.resolve(..., require_evidence=True)` DENYs a `human` actor with no
`evidence`, fail-closed, instead of silently trusting it. Off by default, changes no
behavior for any `approve=` that never knew about `evidence=`.

The boundary, stated plainly rather than hidden: the harness verifies no SIGNATURE at
all — verifying one specific channel's signature (Slack differs from Twilio differs from
an OAuth session) is the job of the `approve=` callback ITSELF, the only party holding
that channel's secret, the same provider-seam philosophy `Policy`/`approve=` already
follow throughout. `AuthEvidence` closes exactly its part: a callback can NO LONGER
report `human` with a bare string once `require_approval_evidence=True` — it must
actively build a full evidence record. A callback that FABRICATES the whole
`AuthEvidence` record is still a threat this section does not claim to close.

A side gap found while fixing this: the classic backend used to DROP the actor
`resolve()` returned entirely (`dispatch.py`'s old comment: "the classic loop has no
DecisionLog to record into") — `policy.decided` carried no actor/evidence at all even
though the LangGraph backend had been writing to a `DecisionLog` all along. Fixed in the
same pass: `policy.decided` now carries `actor`/`evidence` on BOTH backends (classic
through the event itself, since that backend has no persistent `DecisionLog` to write to
— the transcript IS the audit log there).

The Service API (T-9.2, `harness[server]`) is the second real caller of `AuthEvidence`:
`POST .../approvals/{call_id}` accepts an optional `evidence=` in the body, letting an
operator put a real channel layer (a verified Slack signature, an OAuth session, …) in
front of this endpoint before it writes evidence — this module itself authenticates
NOTHING (its docstring already said "No authentication" before this, unchanged).

`tests/test_attack_s11.py` (24 tests, expanded from 4 in the earlier pass): `resolve()`
carrying evidence alongside the actor; `require_evidence=True` DENYing a human with no
evidence, ALLOWing with it, unaffected for a bare `bool`/a non-human actor; real runs
through both `Agent` AND `build_agent()` (`policy.decided`/`Decision.evidence` correct on
both backends); `_evidence_from_body` (Service API) parsing/rejecting correctly; one
mutation test locking in that the enforcement is actually load-bearing.

**S-13 — a sub-agent not capped to the parent's `steps`/`wall_clock_s`.**
`Ledger.hold_steps()`/`release_steps()` apply the exact same TOCTOU reasoning as
`hold()` (the money axis) to the step axis; `child_wall_clock()` clamps the child's time
ceiling to exactly what the parent has left at spawn time.
`tests/test_attack_s13.py`.

**S-14 (partial) — overlapping reservations reading the same "available" budget.**
`Ledger._committed()` adds every open reservation into `remaining_usd()`, and both
branches of `reserve()`'s budget check use it. `Ledger.void()` — checked carefully, no
`try/except` on either backend sits between `reserve()` and `settle()` needing to cancel
a reservation mid-flight; 0 callers today, reserved for when a real model-call `Retry`
plugin exists. `tests/test_attack_s14.py`.

**S-15 — a stateful policy shared by every thread (LangGraph backend).**
`build_agent()` now refuses construction for any non-factory policy (`ConfigError`);
`Runtime._engine_for(run_id)` builds a separate `PolicyEngine` per thread. The classic
backend already had a CORRECT mechanism of a different shape
(`_check_shared_policy_state`, detected after running — because it has a clear `run()`
boundary that a graph doesn't) — the "factory only" rule isn't applied there, since doing
so would break a legitimate use case (a purely configuration-based, stateless policy).
`tests/test_attack_s15.py`.

**S-16/S-19/S-3's second source — the two-axis taint model.** `policy/label.py`
(canonical `Integrity` x `Confidentiality` x `Label`, `Grants`), `policy/builtin.py`
(`check_flow`, `emits_of`), a PER-MESSAGE label on the LangGraph backend (against taint
being "laundered" when compaction clears content but not the label). The classic backend
deliberately keeps sticky-per-run — it never recomputes per message, so it's immune to
exactly the per-message laundering it would otherwise need to guard against.
`tests/test_attack_s19.py`, 11 tests.

**S-20 — `Budget(usd=None)` skipping a promised warning.** `usd=None` is STILL allowed
(a deliberate escape hatch, a free provider) — what was missing was the second half of
the documented promise: `EventKind.BUDGET_UNLIMITED` fires exactly once per run/thread,
right after `RUN_STARTED`. The only "release-blocking" finding still alive among the 58.
`tests/test_attack_s20.py`.

**S-21 — `Ledger.snapshot()` losing the `blocked` flag.** Added to both `snapshot()`/
`restore()`.

**S-22 — underpricing when a real cache write happens.** `size_call()`/`reserve()`
now price against the WORST case (`cache_write_per_mtok`), not `input_per_mtok`.

**S-24 — `EventBus`/`seq` shared across threads, broader than originally described.** The
same bug Round 37 fixed for `Ledger`/`TaintTracker`, S-15 fixed for `PolicyEngine`, a
fourth time: `Runtime._bus_cache` now holds a separate `EventBus` per thread, built
lazily.

**S-25 — an argument not escaped to the approver + no `ASK` ceiling.**
`secrets.safe_for_display()` escapes unprintable characters + digests long values; a
`max_asks_per_run` ceiling (default 20) — approval fatigue is a channel the model can
steer.

**S-27 — stale taint/confidentiality within the same tool-call batch.** The original
scenario (external+danger in the same turn) was already blocked at construction by
`_check_tool_set`; the branch STILL ALIVE was confidentiality (SECRET reaching a PUBLIC
sink). Both backends now re-check `check_flow` right before each serial call, using the
LIVE label instead of the batch-start label.

**S-29 — grant reuse not logged to audit (LangGraph backend).** `_regate` now writes a
second `Decision` (id-suffixed `reuse`) every time a live grant is reused — applies only
to LangGraph, since the classic backend has no `DecisionLog`.

### Partially fixed, the rest needs its own design

**S-9 — an MCP grant leaking between two servers.** `Scope.server` (T-9.1) blocks a
collision between TWO DIFFERENT LABELS. Not yet blocked: a label re-pointed to a
different endpoint while keeping the same name — needs `ServerIdentity`+`fingerprint`
(K-12's reason for deferring: the `fingerprint` format is unsettled for MCP stdio),
waiting until a real re-pointing incident is observed.

### Fixed with documentation (no fix available at the code layer)

**S-18 — `EgressPolicy` not blocking DNS rebinding.** `Policy.check` is required to be
pure/synchronous (POL-4), which means it can only match the hostname STRING, never
resolve DNS. No fix available at the `Policy` layer — needs a real network layer (an
egress proxy). Fixed by stating the limit plainly in the docstring + `docs/06-safety.md`.

**S-17 — injection through an MCP tool's `description`.** Closed alongside T-9.1: a
description reaching the model before the first tool call is inherent to the
tool-calling protocol, unblockable without breaking the protocol —
`default_effect=DANGER` for an unapproved server is the real fence.

### Checked — stale or already correct, nothing to fix

S-1 (the compaction mechanism the review describes doesn't exist), S-5 (forgeable
provenance — the real mechanism is simpler, no such hole), S-6 (composition was already
correct, just missing a test), S-12 (three conflicting resume signatures don't exist in
the code), S-23 (the `call_key` idempotency mechanism the review describes doesn't
exist), S-26 (`Scope.args` is already `Any`, no coercion), S-28 (a sub-agent ASK already
has a closed path through the existing "no `approve=`" rule, just missing
documentation). Every id has a test locking in real behavior in
`tests/test_attack_*.py`.

---

## 2. Detail — KISS (K-series)

**Cut:** K-9 (`Result.raise_for_status()` — no consumer besides `run()`).

**Fixed (renamed/clarified):** K-11 (`end_strategy`'s third value renamed to avoid a
collision), K-12 (`ServerIdentity` lowered to `ServerLabel` for v1), K-13 (the invariant
namespace — see `08-poka-yoke-matrix.md`), K-22 ("14 names" corrected to the actual
Tier 0-2 scope), K-28 (the sample interface corrected to exactly three things, `session`
stated as belonging to the service layer).

**Not cut — the stated reason is stale:** K-7 (`Reservation.exact` DOES have a real
consumer — `run.py` reads it for the `BUDGET_RESERVED` event); K-23 (seven of the
review's nine listed tunables are either already stale or were never built — nothing
left to cut).

**No code existed to cut when the review was written:** K-10 (the OTel taxonomy — no
OTel integration existed at all at that point). The plan itself, in
`design/04-runtime-durability.md §8.2`, is trimmed straight to four spans, so when real
OTel gets built (T-8.3), it's built as exactly four from the start — and it was.

---

## 3. New findings, caught while building the M6-M10 roadmap

Not from the two original review rounds — given their own `N-` prefix to avoid
colliding with the existing `S-`/`K-` numbers (per K-13's own lesson).

**N-1 (fixed) — LangGraph had no per-tool timeout.** `lg/runtime.py::_run_tools` had no
`async with asyncio.timeout(...)` around a tool call at all — unlike
`dispatch.py::_invoke` (Round 23). A hanging `read` tool (an HTTP call with no timeout
of its own) hung the whole graph node indefinitely; only the RUN-level wall clock could
catch it, and only checked at the start of each step. **From N-10
(`Agent(durable=True)`): this gap is reachable from the main API surface, not only
`build_agent()` — the same `Runtime`, not two copies** — so this fix closes the gap on
both entry points at once.

Fix: `Ledger.tool_timeout(spec.timeout_s)` — the clamp-to-remaining-wall-clock helper
already used by `dispatch.py::_invoke` on the classic backend — recomputed on EVERY
attempt (not once per batch), wrapping the tool call. One snag: `_run_tools` calls a
tool through a bare `asyncio.run(spec.fn(**args))` — `asyncio.timeout()` needs an
`async with` inside a coroutine, with nowhere to put it around a direct
`asyncio.run()` call. Solved with a small coroutine wrapper,
`_with_timeout(coro, timeout)`, doing exactly one thing — `async with
asyncio.timeout(timeout): return await coro` — then
`asyncio.run(_with_timeout(spec.fn(**args), timeout))` replacing the bare call.
`except TimeoutError:` split from the general `except Exception as exc:`, with the same
two-message logic `dispatch.py` already has: `"timed out: run wall-clock budget
reached"` when the timeout was clamped by the RUN-level budget (`timeout <
spec.timeout_s`), or `f"timed out after {spec.timeout_s}s"` when the tool's own ceiling
hit first — retry (`read`/`external`) still applies as an ordinary tool error.
`asyncio.CancelledError` still `raise`s straight through, never becomes a tool error —
unchanged from before this fix. `tests/test_n1_graph_tool_timeout.py`: a tool hanging
for 5s with `timeout_s=0.05` is cut off in under 2s (not 5s) and the run still ends
`ok=True`; a second tool with a wide own `timeout_s` (30s) but a tighter RUN-level
budget (`wall_clock_s=0.05`) produces exactly the second message — confirming the
clamped-by-run branch, not just the clamped-by-tool one.

**N-2 (fixed) — `try_run()` raised outright when the model returned garbage that didn't
match `returns=`.** `_parse_returns()` now runs BEFORE `RUN_FINISHED` fires, catches
`ToolContractError`, and lowers it into `Result(stop_reason=ERROR)` — a model returning
garbage is an OUTCOME, not a crash.

**N-3 (fixed) — LangGraph didn't support `returns=`.** `build_agent()` had no such
parameter; `Result.value` was always `None` on that backend. N-10
(`Agent(durable=True)`) had already closed the "silent" half —
`Agent(durable=True, returns=...)` raised `ConfigError` right at construction instead of
letting `Result.value` quietly stay `None` forever — this fix closes the gap itself,
removing that `ConfigError`.

Turns out HALF of it already existed, nothing to fix: `_output_format(returns)`
(requiring the model to return the right shape) goes through `Agent._asm` — the SAME
`ContextAssembler` both backends share (`lg/adapter.py::ProviderChatModel._generate()`
builds its request from `self.asm`, no separate request-building) — so the SENDING side
was already correct, nothing to fix there. The real missing half was the READING side:
nothing on the durable backend ever called `run.py::_parse_returns()` to parse the final
answer.

Fix: `_parse_returns` moved from a private `RunEngine` method to a module-level function
`run.py::parse_returns(want, text)` (logic unchanged, only relocated — both `agent.py`
AND `lg/runtime.py` already import from `run.py`, no circular import); `build_agent()`
gained `returns=`, threaded down to `Runtime._returns`; `Runtime.finish()` calls
`parse_returns` RIGHT BEFORE emitting `run.finished` — exact parity with T-6.4's fix for
the classic backend (parsing AFTER the graph has already returned would make
`run.finished` report `completed` for an answer `returns=` rejects, the exact bug T-6.4
fixed). `agent.py::_state_to_result()` parses AGAIN (same `text`, same `returns`,
deterministic) to build the real `Result.value` — that value never travels through
checkpointed state, since state must stay JSON-checkpointable and a dataclass instance
isn't. `test_durable_agent.py` (2 tests replacing the old ones asserting `ConfigError`),
`tests/test_n3_durable_returns.py` (3: `run.finished` correctly reports `error` for a
broken answer instead of the old `completed`-then-fixed; correctly reports `completed`
for a good answer; the raw `build_agent()` escape hatch also parses correctly).

**N-4 (fixed) — a provider error crashing straight out, on both backends.** EVERY real
provider call hitting a rate limit/transient timeout crashed the calling program — no
`except` anywhere in `src/harness/` for `ProviderError`/`ProviderTimeout`/
`ProviderRateLimited` before this fix. More severe than N-2: this is the MOST COMMON
path when running against a real provider. Both backends now catch it and lower it into
`Result(ERROR)`.

**N-5 (fixed) — provider-level retry was documented but never built.**
`docs/10-observability-ops.md §3` promised `ProviderRateLimited`/`ProviderUnavailable`/
`ProviderTimeout` all auto-retry — N-4 only turned the error into `Result(ERROR)`,
retrying nothing. `src/harness/retry.py::with_provider_retry()` — ONE implementation for
both backends (`run.py`'s `self._p.complete(...)`, `lg/adapter.py::ProviderChatModel.
_generate()`'s `self.provider.complete(...)`), the exact reason `dispatch.py` is the
SOLE place tool retry (T-6.3) lives, not two hand-written copies that could drift
(R-17). `ProviderError` now carries `retry_after_s` (`errors.py`);
`models/anthropic.py::_map()` reads the real `Retry-After` header from the
`httpx.Response` when the vendor sends one (`_retry_after()`). Exponential backoff +
jitter when the vendor doesn't. Bounded by `deadline_s` — the run's REMAINING
wall-clock budget (`Ledger.remaining_wall_clock()`) — not just an attempt count: a retry
can never outlive the budget, exactly the promise in docs/10 §3.

A near-miss caught before shipping: the first draft planned to pass
`deadline_s`/`on_retry` through `.invoke()`'s `**kwargs` — the same way `max_tokens`
already passes safely. Checking `langchain_anthropic.ChatAnthropic._get_request_payload`
directly (already installed in the sandbox) revealed: it merges EVERY unrecognized
kwarg straight into the payload sent to the real API (`{**self.model_kwargs, **kwargs}`)
— `max_tokens` is a real Anthropic field so it's safe, `deadline_s`/`on_retry` are not
and would 400 the API the moment anyone used the escape hatch with a real model. Fixed
with `retry.retry_scope()` — a dedicated `contextvars.ContextVar`, set by `call_model`
around exactly the `.invoke()` call, read back by `_generate()` in the SAME call stack
(no thread crossing, so no need for `middleware.py`'s context-copying mechanism) — no
stray kwarg reaches `.invoke()` beyond `max_tokens` anymore.

**N-6 (fixed) — `model.response` AND `run.finished` missing documented fields.**
`docs/05-data-and-state.md §1` promised `model.response` carries
`usage{in,out,cache_read,cache_write}`/`latency_ms`, `run.finished` carries
`usage`/`duration_s` — both backends only emitted `stop_reason`/`cost_usd`/`steps`/
`tainted`. Now emitted fully on both: `run.py` measures `latency_ms` around
`with_provider_retry(...)`, accumulates `usage_total`, measures `duration_s` from
`run_t0`. `lg/runtime.py` needed NEW state (`lg/state.py`): `turn_started_at` (the
`duration_s` clock, reset at the same point `asks` already resets — per turn, not per
thread, since `RUN_FINISHED` on this backend already fires per turn, not per thread) and
`turn_usage` (accumulated by `call_model`, `dataclasses.asdict(Usage(...))` —
JSON-checkpointable, the same convention as `ledger`). `latency_ms` measured inside
`lg/adapter.py::_generate()` (total time INCLUDING retries, not just the last attempt)
then sent through `AIMessage.response_metadata` for `call_model` to read back.

The `step=` part (a backend asymmetry, found while reviewing `middleware.py`, N-10): 11
`_emit` call sites in `lg/runtime.py` were missing `step=`, so `Event.step` was always
`None` there even though the classic backend always has it. Added
`step=state.get("step", 0)` to all 11; verified by printing `event.step` directly
through a real `durable=True` run — no more `None`s except `run.started`/`run.finished`
(matching the classic backend exactly).

Both halves verified: `tests/test_n5_n6_retry_and_usage.py` (8 tests — retry succeeding
after N transient failures on both backends, a non-transient error never retrying,
exhausting `MAX_ATTEMPTS` still landing softly as `Result(ERROR)` rather than crashing,
a retry never outliving the wall-clock budget, both events carrying every field,
`turn_usage` resetting correctly per turn rather than accumulating across different
`try_run()` calls on the same thread).

**N-7 (fixed) — `Agent.with_()` losing four fields, on EVERY call.**
`transcript`/`exporters`/`accepts_tainted`/`sensitive` vanished from a derived agent —
not a bug specific to any one feature being built, affecting every caller of `with_()`.
`tests/test_n7_with_preserves_fields.py`.

**N-8 (fixed, one clearly-defined half) — `execute_once` now has a caller at BOTH
levels.** `POST /v1/runs`'s `Idempotency-Key` (T-9.2) dedupes a REQUEST STARTING A RUN —
`idempotency.py`'s own stated condition for "the first real caller." `Dispatcher._invoke`
(classic backend) and `lg/runtime.py::_run_tools` (durable backend) are now the second
caller, at the TOOL-CALL LEVEL: `execute_once` wraps the actual `spec.fn()` call, keyed
by `idempotency_key(f"{run_id}:{step}", call_id)` — folding `step` into the run_id half
of the key (the `idempotency_key` function's own signature is unchanged,
`tests/test_m6_t61_idempotency.py` already locks in the shape
`f"{run_id}:{call_id}"`) because `FakeModel.tool_call()`'s convenient default
(`call_id="c1"`) isn't unique across steps, and dozens of existing tests harmlessly rely
on that.

What exactly this closes: a `retryable=True` call where `spec.fn()` SUCCEEDED but the
step RIGHT AFTER it (json.dumps/`truncate`/the taint check) is what raised — before this
fix, the dispatch loop treated the whole attempt as failed and retried the ENTIRE thing,
including a `spec.fn()` that had already succeeded — now the second attempt is a cache
hit, `spec.fn()` doesn't run again; `tool.finished`'s `replayed=True` confirms it.
`tests/test_n8_idempotent_tool_calls.py`: the classic backend forces a real error
(monkeypatching `truncate` to raise on the first call) and checks the side effect is
recorded exactly once; the durable backend is a white-box test — calling
`Runtime._run_tools(state)` directly twice with the SAME `run_id`/`step`/`call_id`
(matching the shape of a node retried right at the checkpoint BEFORE it, with the
checkpointed `_pending` unchanged) and checking the side effect also happens only once.

The half NOT closed, and not claimed closed: a crash ACROSS PROCESSES (upstream already
received the side effect, the process dies before `execute_once`'s `store.put()` can
write) — both stores are in-memory (`Dispatcher.__init__`'s fresh `InMemoryStore` per
run; `Runtime._idem_for()`'s cache keyed by `run_id`, the same shape as
`_bus_cache`/`_policy_cache`, R-4-safe since it isn't a source of truth), so they die
with the process. That half stands exactly where it stood before:
`docs/05-data-and-state.md §3`'s resume rule (`write`/`danger` never re-runs on resume)
— S-4's original description is exactly this cross-process crash scenario, and it is
NOT closed by this fix, nor claimed to be.

**N-9 (fixed) — `tenant_id` never reached `Policy.check()`.** `Agent.tenant_id` only
ever flowed to the `EventBus` (telemetry), never to `RunContext`/`_Ctx` — a `Policy`
couldn't read it. Threaded into both context types, both backends.
`tests/test_n9_tenant_in_context.py`. Found while re-running
`tests/test_roadmap.py` (the self-registered "definition done" test suite for M6-M10,
written before the milestones existed) right before this documentation pass — two other
RED checks in the same file turned out to just be guessing the wrong NAME
(`ApprovalRecord`->`Decision`, `harness.testing`->`harness.eval`), a test fix, not a code
fix; `tenant_id` was the only real functional gap among the five originally-red checks.

**N-10 (fixed) — "2 API interfaces" (classic backend vs. LangGraph) confusing users.**
Direct user feedback, not an automated finding: having two backends meant anyone wanting
durability had to learn LangChain vocabulary (`HumanMessage`, `.invoke()`, `thread_id`
in config) on top of `Agent`. Closed with `Agent(durable=True)`
(`docs/03-public-api.md §3.5`, `agent.py::_atry_run_durable`): the SAME
`run`/`try_run`/`arun`/`atry_run`/`with_`, running on `harness.lg.build_agent()`
underneath, with nothing LangChain/LangGraph-shaped leaking out. `ProviderChatModel`
(`lg/adapter.py`) is the seam that makes this possible without a second model-calling
library: it wraps THAT SAME Agent's `provider=` (the same `AnthropicProvider`/
`FakeModel` the classic backend calls) into a LangChain chat model, so `durable=True`
calls the model through exactly one error-mapping/pricing path, and through the SAME
`ContextAssembler` — closing a gap that belonged to `build_agent()` alone (it never sent
a system prompt/`job=` to the model on its own). `checkpoint=` defaults to a local
SQLite file, created automatically, no external infrastructure needed
(`_build_checkpointer`) — matching the experience `SqliteStore` already provides for
memory.

Three NEW gaps, none silent — each raises a clear `ConfigError` rather than quietly
skipping: `durable=True` + `.chat()`, + `.resume(transcript)`, + `on_delta=` are all
refused (the checkpointer already holds conversation history, so `.chat()`/`.resume()`
would duplicate its job; the model call inside `_generate` doesn't stream yet).
`Session`/the Service API (T-8.6/T-9.2) remain classic-backend ONLY — whether to extend
them to `durable=True` hasn't been decided, recorded in §7 below.

A deliberate architectural trade-off, not an oversight: the graph is RECOMPILED on every
call (`_build_durable_graph`), not cached on the `Agent` like `_asm`/`_watch`. Why:
`AsyncSqliteSaver` holds an `aiosqlite` connection that owns a background thread that is
NOT a daemon — a durable `Agent` caching the graph (and connection) for its whole
lifetime would make a script that calls `run()` and then exits normally HANG instead of
returning — a real bug, caught by hand while building this feature (not theoretical:
`tests/test_durable_agent.py::DurableProcessExits` is a regression test for exactly that
bug, running a child script via `subprocess` with a hard timeout). Opening/closing the
connection around exactly one call — the same shape `atry_run()` already uses for its
own `TranscriptWriter` — trades that for this Agent behaving like every other Agent in
the library: it returns. `tests/test_durable_agent.py` (14 tests) and
`tests/test_parity.py` (expanded to THREE call shapes — the loop, the raw graph,
`durable=True`) cover this feature.

---

## 4. Ideas with real architecture, waiting on evaluation

Cut from the mandatory path per rule §8.4, **not thrown away.** If a day comes with
measurement, this is where to pick it back up.

### Quarantine model (dual-LLM / CaMeL)

**What Microsoft gets right, and where it's placed wrong.**
`set_quarantine_client` provides a separate, cheaper model to reason over untrusted
content — the dual-LLM / CaMeL pattern, something **no other package among 28 Python +
TypeScript packages has**
([§09](../research/09-memory-context-multiagent-hitl.md) §16bis). The idea is right; the
placement is wrong: `_quarantine_chat_client` is a **module-level** variable, assigned
through `global`.

**Where it belongs in a `Run`'s lifetime.** Quarantine isn't process configuration —
it's a `Run` seam:

```python
@value
class RunConfig:
    quarantine: ModelProvider | None = None      # None = fail closed
    max_grant_ttl: timedelta = timedelta(hours=1)

@value
class Quarantined[T]:
    """A result extracted from UNTRUSTED content. Schema-typed data only, never free prose."""
    value: T
    label: Label                # always Integrity.UNTRUSTED — cannot be lowered
    source_call_id: CallId
```

Four rules: (1) lifetime = the `Run`'s lifetime, fixed at construction, no setter/
`global`; (2) only schema-typed data returns to the main context — free-form prose never
merges into the main run's `messages`; (3) `Quarantined.label` has no API that lowers it
to `TRUSTED`; (4) no configuration => `DENY`, never silently proceeding (the opposite of
Microsoft). The quarantine model's cost is charged to the same run `Ledger` — a safety
mechanism with its own untracked budget is a safety mechanism nobody can account for.

**Why it's cut.** Exactly ONE implementation in the entire research effort,
`@experimental`, not wired into its own harness, not concurrency-safe. No evaluation
compares the prompt-injection success rate with and without it
([review-kiss.md](review-kiss.md) K-1).

**Condition for picking it back up.** An evaluation against a real prompt-injection
dataset, measuring success rate with and without quarantine, on the same tool set. No
statistically significant difference -> keep it cut.

---

## 5. Trade-offs made, and each one's price

| chose | gained | cost |
|---|---|---|
| A graph instead of a loop | durability, provability, drawability | dependency on LangGraph; users must understand the concept of a node |
| Effect class deriving 5 behaviors | a tool author declares **one** thing; no hand-written per-tool guard | 4 tiers is coarse — a tool that's both reading sensitive data and writing doesn't fit neatly |
| Invariants on the mandatory path, plugins for policy | "not installed" stops being an unsafe default | a plugin can't block a permission check |
| An append-only `Decision` | real audit, revocation via `max()` | the log only ever grows; a retention policy isn't written yet |
| The effect log always on for `write` | shortcoming #5 (`design/README.md`) fixed by default | one extra DB round-trip per `write` call — not yet benchmarked |
| At-most-once instead of exactly-once | honest about what the harness alone can do | a user wanting exactly-once needs an upstream that accepts a key |

---

## 6. Not Enough Evidence — consolidated

- **The effect log's cost** per `write` call. Not measurable from anyone else's source.
- **A `fingerprint` for MCP stdio.** TLS SPKI pinning fits HTTP; no obvious equivalent
  for a child process.
- **LangGraph's exact checkpoint boundary** mid-node.
- **Multi-tenant isolation at the store layer.**
- **How many tiers the confidentiality axis needs.** Two tiers is a design choice, not
  a measured result.
- **The default TTL for a `danger` grant.** No evidence for the right number.
- **A memory expiry policy.** An `UNTRUSTED` memo that lives forever is a real risk, but
  there's no evidence for what the right policy is.
- **The learning curve.** Never measured in the original research; every DX claim here
  rests on something countable, not on real users — SC-1b (`HARNESS.md`) stays open for
  exactly this reason.

---

## 7. Still open today — stated plainly, not hidden

One item, no more, no less (N-1/N-3/N-5/N-6/N-8/S-11 just closed — see `## 1`, `## 3`):

1. **S-9's label-re-pointing half** — needs `ServerIdentity`+`fingerprint`, DELIBERATELY
   deferred until a real MCP re-pointing incident is observed (the same reason as K-12)
   — not a matter of running out of time, but the §45 discipline "better to say not
   enough evidence than to guess": building a fingerprinting model for a threat that's
   never been observed would mean inventing that threat model, exactly what this rule
   forbids.

**S-4 no longer appears in this list** — not because it's fully closed, but because its
remaining half isn't a REMAINING TASK: N-8 (see `## 3`) closes exactly the real
engineering gap that was open (a call retried while a run is in flight); the rest
(a crash ACROSS PROCESSES, between upstream receiving the side effect and the checkpoint
managing to write) is an ACCEPTED boundary, already documented before this
(`docs/05-data-and-state.md §3`'s resume rule, which is exactly "the honest limit of a
library-level solution" — exactly-once across a crash needs a durable execution engine,
a stated non-goal), not a gap waiting on code.

Nothing above blocks v1.0 (see `design/08-roadmap-and-release-plan.md §3` for release
conditions) — every item has a specific, stated reason for being deferred, not something
simply forgotten.
