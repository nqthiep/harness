# 12 — Decision Logs

Each entry records the losing argument as well as the winner. A decision whose counter-case
is not written down gets re-litigated every six months by someone who only sees the cost of
the choice, never the cost of the alternative.

---

## Design Decision Log (architecture)

### ADR-001 — The harness owns the agent loop
**Status:** Accepted (Round 2) · **Supersedes:** the Round 1 baseline

**Context.** The Anthropic SDK ships a tool runner that drives the loop. Rewriting it looks
like a KISS violation.

**Decision.** Own the loop, in ~200 lines, following the documented manual-loop shape
exactly.

**Why.** The loop is the only point where a budget check can happen *before* a model call
and a permission check *before* a tool executes. Delegating it means invariants 2 and 3
cannot be enforced by the harness at all — they become each tool author's responsibility,
which is the definition of unsafe-by-design. Secondary: the Python tool runner does not
auto-resume `pause_turn` and does not expose its message history, so a long server-tool
turn silently truncates the answer with no error raised.

**Rejected alternative.** Delegate to `client.beta.messages.tool_runner` and enforce policy
inside each tool function. Rejected: every tool author becomes a security engineer, and
budget enforcement has nowhere to live.

**Cost accepted.** ~200 lines to maintain, and the loop must track API evolution
(`pause_turn`, `refusal`, compaction blocks).

---

### ADR-002 — Five plugin seams, everything else core
**Status:** Accepted (Round 2)

**Decision.** Tool, ModelProvider, Store, Policy, Exporter are seams. The loop, context
assembly, budget accounting, schema generation and retry are core and not overridable.

**Why.** The three-part test in [§02.4](02-architecture.md#4-what-is-a-plugin--the-test)
requires two *real* implementations today, not hypothetical ones. Beyond that: three of the
core items are core specifically because a replacement could **defeat an invariant** — a
replaceable ledger can always say yes, a replaceable assembler can reintroduce cache
invalidation, a replaceable loop can skip the policy check. An extension point that can
disable a safety guarantee is not an extension point.

**Rejected alternative.** Nine abstract base classes with one implementation each.
Rejected as speculative generality with a permanent maintenance cost.

---

### ADR-003 — Effect classes are required, and derive five behaviors
**Status:** Accepted (Round 5)

**Decision.** `@tool(effect=...)` is required, with four values. Parallel-safety,
retryability, default verdict, taint propagation and audit level are all derived.

**Why.** Without it, the harness cannot tell a `grep` from a `git push`, so it must assume
the worst for everything (serialize, never retry, always ask) — which is both slow and
annoying enough that people disable it. With it, one decision the author is uniquely
qualified to make replaces five they would get inconsistent.

**Rejected alternative A.** Default `effect="read"`. Rejected: fail-open. A `send_email`
tool defaulting to safe-and-parallel is exactly the silent lie the design exists to prevent.

**Rejected alternative B.** Five independent flags. Rejected: five chances to be
inconsistent, and no way to check the combination is coherent.

**Counter-argument acknowledged.** A required argument in the first example costs cognitive
load. Answered by *beginners consume, authors declare*: the built-in packs are
pre-classified, so the five-line example never writes `@tool`. Omission is an **import-time**
error with the four options printed.

---

### ADR-004 — `Agent` is immutable; tools and prompt are frozen
**Status:** Accepted (Round 5)

**Decision.** `Agent` is a frozen dataclass. No setters, no `add_tool`, no mutable system
prompt. Changes go through `with_()`, which returns a new agent.

**Why.** Prompt caching is a prefix match. Mutation is the mechanism by which almost every
real-world cache invalidation happens. Immutability does not *detect* the problem, it makes
it unrepresentable — and it makes an `Agent` safe to define at module scope and share
across concurrent requests, which is how most people will use it.

**Cost accepted.** Dynamic per-request tool sets require constructing a new agent. This is
flagged with a `cache.per_request_agent` warning that explains the cost, rather than being
silently allowed.

---

### ADR-005 — Budget is a pre-flight ceiling with finite defaults
**Status:** Accepted (Round 5)

**Decision.** `reserve()` before every model call, using a worst-case estimate. Defaults
`$0.50` / 20 steps / 300 s. Unlimited requires typing `None` and warns on every run.

**Why.** Reporting cost after the fact is a dashboard, not a control. The failure this
prevents — a loop running overnight — is the most commonly reported agent incident, and it
is unrecoverable by definition.

**Rejected alternative.** Post-hoc accounting with alerts. Rejected: by the time an alert
fires, the money is gone.

**Cost accepted.** Worst-case estimation stops some runs slightly early. This is the correct
direction to be wrong in.

---

### ADR-006 — Strong default model; no automatic routing in v1
**Status:** Accepted (Round 0, reaffirmed Round 6)

**Decision.** Default `claude-opus-5` at `effort="medium"`. No LLM-based or heuristic model
router. Cost efficiency comes from mechanism and from explicit subagents.

**Why.** Silently choosing a weaker model than the user expects is a correctness decision
disguised as a cost decision, and it is the library author making a call that belongs to the
application author. A framework that quietly downgrades produces worse answers that get
blamed on the model. Separately, in Round 6 nobody could name a routing policy the council
agreed was correct today — that is the definition of speculative.

**Deferred, not designed around.** `ModelProvider` is a seam, so a router can be added later
without touching the loop.

---

### ADR-007 — Async core, sync facade
**Status:** Accepted (Round 3)

**Decision.** `RunEngine` is async. `run()` wraps `arun()`. Sync tools are offloaded to a
thread pool automatically.

**Why.** Parallel tool execution and streaming both need async; a sync core cannot get them
back. The beginner writes `def` and never learns the word "coroutine". Calling `.run()`
inside a running loop is detected and produces `SyncInAsyncContextError` naming `arun()`,
rather than the standard baffling `RuntimeError`.

**Rejected alternative.** Two parallel implementations. Rejected: double the surface, double
the bugs, guaranteed divergence.

---

### ADR-008 — Plugin discovery is opt-in
**Status:** Accepted (Round 7)

**Decision.** Entry-point discovery only under `Agent(discover=True)`.

**Why.** Auto-discovery means a transitively installed package can register tools — new
capabilities appearing in an agent because of a dependency-of-a-dependency upgrade. That is a
supply-chain backdoor dressed as convenience.

**Cost accepted.** Plugin authors write one import line in their README. Worth it.

---

### ADR-009 — `job=`, not `system_prompt=`
**Status:** Accepted (Round 4)

**Decision.** The parameter is `job`.

**Why.** "System prompt" makes a newcomer believe there is a hidden protocol to learn before
they can start. "Job" is self-explanatory and, more usefully, produces better prompts —
people describe a job well and write "system prompts" badly.

**Cost accepted.** Divergence from peer libraries. Mitigated: the docs name the equivalence
once, in the tools guide.

---

### ADR-010 — `tools=`, with no `can=` alias
**Status:** Accepted (Round 4) · *Recorded as a loss for the Beginner Advocate*

**Decision.** `tools=` only.

**Why.** `can=[search]` reads better in English, but shipping both is two ways to do one
thing — the exact anti-pattern the council had just spent Round 2 removing. Every peer
library uses `tools=`, so the cost of the unfamiliar word would be paid by every
intermediate user forever, to save beginners one word once. Naturalness is recovered in
prose and error messages instead.

---

### ADR-011 — Taint lattice instead of injection detection
**Status:** Accepted (Round 7) · **The central safety decision**

**Decision.** `external` tool output is tainted; tainted mode denies `danger` tools; the
only opt-out is `@tool(effect="danger", accepts_tainted=True)`. Checked at construction
(ADR-011a, Round 9).

**Why.** Prompt injection cannot be reliably detected by a classifier, and shipping one
creates false confidence that makes users *less* careful. A capability rule needs no
detection: it is enforced structurally, is ~20 lines, and is explainable in one sentence.

**Why DENY rather than ASK.** An approval prompt asks a human to audit a wall of fetched
text for a hidden instruction. Humans cannot do this reliably, and approval fatigue turns
ASK into ALLOW with extra steps.

**Why the opt-out is per-tool.** A global `Agent(allow_tainted_danger=True)` would be
copy-pasted by everyone who hit the error. Per-tool opt-in appears in the diff a reviewer
reads.

**Limitation stated.** It does not prevent the model being persuaded into a `read` or
`write`. It bounds the blast radius to reversible operations. Documented as such.

---

### ADR-012 — The beginner requirement is literal
**Status:** Accepted (Round 13) · **Supersedes:** the Round 0 resolution of A0.1

**Context.** Round 0 concluded that "a ten-year-old can use it" was unsatisfiable as
written, and reinterpreted it as a cognitive-load budget. The project owner corrected the
premise: the target child has completed a basic Python course and knows `pip install`,
`def`, `import`, variables, lists and strings.

**Decision.** Take the requirement literally. A child with that knowledge must reach a
working agent *and* be able to add a tool of their own. The cognitive-load budget stands as
a floor, not as a substitute.

**Why this mattered more than it looked.** Re-running the beginner review against a real
child persona found six blockers, and **five were outside the API surface the council had
spent four rounds polishing** — credentials, feedback, error rendering, scaffolding, and
repeat-run cost. Round 4 had tested the shape of the API and passed it without ever testing
whether someone could get from an empty folder to a working agent.

**The general lesson, recorded because it will recur.** A beginner review that only
inspects the API measures the wrong thing. Time-to-first-agent is dominated by setup,
feedback and error messages.

---

### ADR-013 — Credentials are a guided command, not a shell instruction
**Status:** Accepted (Round 14)

**Decision.** `harness setup` asks for a key, validates it with one minimal call, and
stores it. Environment variable wins when present; otherwise a project `.env` at mode
`0600`. `harness new` generates `.gitignore` containing `.env` **in the same command**.
Every "no credentials" error says `Run: harness setup`.

**Why.** `export ANTHROPIC_API_KEY=...` is a shell concept, and it is the first thing a
beginner meets. Validating the key immediately converts a silent later failure into an
instant, obvious one.

**Rejected alternative.** A new config location the vendor SDK does not read. Rejected: two
sources of truth for one credential is a support burden forever.

**Security position.** A plaintext key in a project folder is a real risk. It is mitigated
by mode `0600` and by generating the `.gitignore` in the same command as the file that
needs it — a protection that is a separate step is a protection that gets skipped.

---

### ADR-014 — Feedback and results are shaped for a terminal
**Status:** Accepted (Round 14)

**Decision.** Three changes: (1) live progress on `stdout.isatty()`, silent otherwise;
(2) `Result.__str__` returns the text, so `print(agent.run(...))` works; (3) tool **names**
only in progress output, never arguments.

**Why.** Fifteen seconds of silence reads as "broken" and gets Ctrl-C'd. `.text` is an
extra concept in the very first example for no benefit.

**Objection answered.** A library printing to stdout unbidden is bad manners — and the TTY
check *is* the manners. Nothing that consumes harness output programmatically is attached
to a terminal.

**Safety.** Arguments are excluded from progress output for the same reason they are
digested in transcripts (register #24): they routinely carry PII.

---

### ADR-015 — Tracebacks are filtered locally, never globally
**Status:** Accepted (Round 14)

**Decision.** `run()` catches, rewrites `__traceback__` to drop harness-internal and
`asyncio` frames, sets `__suppress_context__`, re-raises. `HARNESS_FULL_TRACEBACK=1`
restores everything. **The unfiltered traceback is still recorded in the `error.raised`
event and the transcript.**

**Rejected alternative.** Installing `sys.excepthook` at import. Rejected outright: a
library that mutates global interpreter state on import is hostile to any application
embedding it, and it breaks debuggers and error reporters.

**Why it is safe.** Console rendering changes; diagnostics do not. Nothing is lost, and the
loss would have been the objection.

---

### ADR-016 — Session spend is a warning; the real ceiling belongs at the provider
**Status:** Accepted (Round 14)

**Decision.** Two parts. (1) `harness setup` prints the provider's spend-limit URL and asks
the user to set a hard limit there. (2) In-process only: one warning per process when
cumulative spend across runs passes `$5`. No files, no locks.

**Why.** A per-run budget does nothing about running a script eighty times. But a
cross-process cap in a library means file locking, clock skew and a race the library cannot
win — to reimplement, badly, a hard limit the provider already enforces properly.

**The distinction is load-bearing.** This is documented as a **warning, not a ceiling**,
specifically so it cannot dilute the ADR-005 guarantee. Two mechanisms that sound similar
and have different strengths are worse than one, unless the difference is stated every time
either is mentioned.

---

### ADR-017 — `max_tokens` is derived from the remaining budget
**Status:** Accepted (Round 17) · **Fixes a defect in ADR-005 as originally specified**

**Context.** `budget` and `max_tokens` were independent. The pre-flight reservation
multiplies `max_tokens` by the output price, so with the scaffold's `budget="$0.05"`, the
default `max_tokens=16000` and Opus-tier pricing, the worst-case reservation was `$0.406` —
eight times the budget. **Every first run would have refused to make a call.** The default
`$0.50` budget cleared the same reservation by nine cents, by accident.

**Decision.**

```
max_tokens = clamp(
    floor((remaining_usd − input_cost) / output_price_per_token),
    lower = 256,   # below this, stop with BUDGET_EXHAUSTED — a truncated answer is not an answer
    upper = model's maximum output,
)
```

`max_tokens` is not a public parameter and now never needs to be.

**Why.** Two independent knobs that multiply into one constraint will contradict each
other; the only question is when someone notices. Deriving one from the other makes the
contradiction unrepresentable. A small budget now yields a short answer rather than no
answer — which is what a user expects and what the scaffold was demonstrating.

**Rejected alternatives.** Raise the scaffold budget (treats the symptom, and the same trap
waits for anyone who sets a small budget deliberately). Loosen the worst-case estimate
(breaks the ceiling guarantee, which is the point of ADR-005).

**The general rule this produced.** Every numeric default is validated by **arithmetic
against every other numeric default it can meet**, not by review. The council reviewed the
budget mechanism four times without computing a single number with it. T-1.5 carries this
test.

---

### ADR-018 — No token-by-token streaming to the terminal by default
**Status:** Rejected (Round 17) · *recorded so it is not re-proposed as an oversight*

**Proposal.** Stream the answer to the terminal as it is generated — the single most
delightful behavior for a beginner.

**Rejected because** the first example is `print(agent.run(...))`. Streaming to the
terminal prints the answer, and then `print` prints it again. Every remedy — suppressing
the final print, a hidden "already streamed" flag on `Result` — puts invisible state on the
simplest path in the library, to solve a problem that is already solved.

The progress line (ADR-014) addresses the real issue, which was silence reading as
breakage. Streaming remains available explicitly via `run(..., stream=...)`.

---

### ADR-019 — Closed enums that mirror an external protocol carry an exhaustiveness test
**Status:** Accepted (Round 18) · **Fixes a defect in the Round 8 `StopReason` design**

**Context.** The provider returns `stop_reason: "max_tokens"` when generation hits the
ceiling. `StopReason` had eight values and none of them was it, so a truncated answer was
reported as `COMPLETED` with `ok = True`. Survivable while `max_tokens` was a large
constant; **ADR-017 made small ceilings normal, which made truncation routine.**

**Decision.** Add `StopReason.TRUNCATED` (`ok = False`). More generally: every closed enum
that mirrors an external protocol carries a test asserting it covers that protocol's value
set. An unmapped value maps to `ERROR` carrying the raw string — **never to a success.**

**Why the general rule matters more than the specific value.** `StopReason` was reviewed and
approved in Round 8. It was complete with respect to the design and incomplete with respect
to the API. A closed enum is a promise about someone else's protocol, and promises about
other people's protocols need tests, not review.

**Message requirement.** One provider `stop_reason`, two different user actions: a
budget-derived ceiling (raise the budget) and the model's own maximum output (ask for less).
The message distinguishes them.

---

### ADR-020 — A `Chat` has one ledger for the session
**Status:** Accepted (Round 18)

**Context.** `Budget` is per-run; a `Chat` is many turns. Which one the budget covered was
never stated, and the two readings differ by however long someone talks. Per-turn makes
`harness chat` unbounded across eighty turns; per-session kills a chat after three.

**Decision.** One ledger per `Chat`. `chat(budget=...)` sets it; default is **10 ×** the
agent's run budget, shown in the `harness chat` banner.

**Why it composes.** As the session budget depletes, ADR-017's derived `max_tokens` shrinks,
so answers get shorter and *then* the chat ends with a clear message — it degrades rather
than stopping dead. ADR-017 turned out to be load-bearing for a feature it was not designed
for, which is recorded as evidence that the derivation was the right shape.

---

## Implementation Decision Log

| # | Decision | Rationale |
|---|---|---|
| IDL-01 | `Decimal` for all money; `float` banned in `budget/` by lint | A rounding error in a spend ceiling is a real bug class |
| IDL-02 | `Verdict` is an `IntEnum` | Makes `max()` the composition operator for free, which is what enforces restrict-only |
| IDL-03 | ULID for `run_id` | Time-sortable, no coordination, no collisions |
| IDL-04 | Canonical JSON everywhere (`sort_keys=True`, fixed separators) | Byte-stability is a cache correctness property, not a style preference |
| IDL-05 | `frozen=True, slots=True` on every data class | Immutability + lower memory + attribute typo becomes `AttributeError` |
| IDL-06 | Sync tools wrapped at decoration, not at call | One branch at import instead of one per invocation |
| IDL-07 | Provider SDK imported lazily | NFR-01: keeps `import harness` under 200 ms |
| IDL-08 | `no_network()` is an autouse fixture | A contributor cannot accidentally bill themselves (register #34) |
| IDL-09 | Tool arguments stored as a digest by default | Arguments routinely carry PII; a transcript that captures them by default is a leak generator |
| IDL-10 | Exporter exceptions disable that exporter for the run | Telemetry must never cause an outage |
| IDL-11 | `run()` raises, `try_run()` returns | Serves beginners (loud) and production (explicit) without an options flag |
| IDL-12 | `RunFailed.partial` carries the `Result` | Raising must not destroy accumulated work |
| IDL-13 | 250-line ceiling on `run.py`, treated as a design signal | The loop staying boring is the property that keeps it auditable |
| IDL-14 | `EFFECT_PROFILES` is a module constant, not configuration | A user who could edit it could disable the taint rule |
| IDL-15 | `RunContext` excludes message history | The transcript is the largest available exfiltration surface |
| IDL-16 | SQLite `STRICT` tables + WAL + busy timeout | Type affinity silently accepts wrong types; WAL avoids `database is locked` |
| IDL-17 | Cache linter waits 150 ms between renders | Long enough to catch second-resolution timestamps, short enough not to be noticed |
| IDL-18 | Breakpoints omitted below the minimum cacheable prefix | Below it, a marker pays the write premium and never reads |
| IDL-19 | Server-side refusal fallbacks enabled by default | A routine refusal should route to a fallback, not surface as a dead end |
| IDL-20 | Parallel results reassembled in the model's call order | Order-dependent behavior in a model's reading of results is real; determinism is cheap |
| IDL-21 | `Agent.__init__` takes `*args` solely to reject them | Keeps keyword-only enforcement while replacing Python's unreadable `TypeError` (G13.4) |
| IDL-22 | Unannotated tool parameters are an error, never defaulted to `str` | `def add(a, b)` would receive `"3"`/`"4"` and return `"34"` — a silently wrong answer a learner cannot search for |
| IDL-23 | `effect=` misspellings get a did-you-mean via edit distance | A typo in a four-word vocabulary is the single likeliest mistake with it |
| IDL-24 | Progress output goes to `stderr`, not `stdout` | Keeps `python agent.py > out.txt` clean even on a TTY |
| IDL-25 | `harness setup` validates the key with a minimal call before storing | Converts a silent later failure into an immediate, obvious one |
| IDL-26 | The scaffold includes `budget=` rather than introducing it later | Showing the guard costs one commented line; explaining it after a surprise bill costs trust |
| IDL-27 | `max_tokens` is derived, never exposed | ADR-017. A parameter that cannot be set cannot contradict the budget |
| IDL-28 | A derived `max_tokens` under 256 stops the run instead of calling | A 200-token ceiling produces a sentence fragment, which costs money and answers nothing |
| IDL-29 | Numeric defaults are cross-validated by a test that multiplies them out | The Round 17 defect lived between two correct components, not inside either |
| IDL-30 | An unrecognized provider `stop_reason` maps to `ERROR` with the raw value | Fail visible. Mapping an unknown outcome to success is how truncated answers ship as correct ones |
| IDL-31 | Context-management fixtures are specified per model | Whether the budget or the context window binds first depends on the model's price and window ([§07.3](07-cost.md#3-token-discipline)) |
