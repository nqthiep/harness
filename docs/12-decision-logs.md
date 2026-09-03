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

### ADR-021 — Approval is a resolution step, not a policy
**Status:** Accepted (Round 19) · **Fixes a contradiction between the Round 2 and Round 5 specs**

**Context.** `Policy.check` was specified synchronous, pure, sub-millisecond, no I/O
(Round 2). `ApprovalPolicy` was specified as a built-in `Policy` whose job is a human round
trip via a possibly-async callback (Round 5). Both were approved. They are incompatible.

**Decision.** Delete `ApprovalPolicy`. Policies compose to a verdict; the **engine** then
awaits approval if the verdict is `ASK`.

```
policies (sync, pure, fast)  →  composed verdict  →  if ASK: engine awaits approval
```

**Rejected alternative.** Relax `Policy.check` to async. Rejected: purity is what makes
policies cheap enough to evaluate on every call, and hidden I/O from a third-party policy on
the hot path is precisely what the rule exists to prevent. Loosening a rule to admit the one
thing it was written to exclude is how a contract stops meaning anything.

**The tell, recorded for next time.** The original entry described `ApprovalPolicy` as
"terminal". A step described as terminal *within* a composition is not part of the
composition — the word was the defect, sitting in plain sight through two reviews.

---

### ADR-022 — Strict tool arguments and optional structured output
**Status:** Accepted (Round 21) · **Reverses OI-03**

**Context.** Invariant 4 (Intelligent) had no section, no decision, no requirement and no
test — one sentence in the README describing what the harness does not do. Separately, tool
schemas were specified "strict-ready" (`additionalProperties: false`, complete `required`)
and strict mode was never turned on.

**Decision.** (1) `strict: true` on every generated tool definition. (2)
`Agent(returns=SomeType)` sets `output_config.format`; `result.value` is that type,
validated.

**Why these two and nothing else.** Both are provider features that already exist, cost
nothing extra, need no new concepts, and remove *waste* rather than adding machinery:

- Without strict, malformed tool arguments reach the tool, raise, return as an `is_error`
  result, and cost a round trip to rediscover what the API would have prevented.
- Without structured output, a caller wanting typed data parses a string, fails sometimes,
  and re-prompts — a doubling of cost that buys no additional thinking.

**Reversal of OI-03** ("defer structured output until a user asks"). Deferring a free,
one-parameter provider feature while claiming Intelligent as an invariant is
under-delivering against a stated requirement. The not-over-engineering rule forbids
building for a speculative future; it does not license leaving a stated requirement
unaddressed.

**Beginner impact: none.** `returns=` is one optional parameter at Level 2.

---

### ADR-023 — No planner, reflection, or self-critique loop
**Status:** Rejected (Round 21) · *recorded so it is not re-proposed as an obvious omission*

**Proposal.** Add a planning pass, or a critique-and-revise loop, as the obvious way to make
agents smarter.

**Rejected on the invariant's own wording.** A critique pass is a second model call for an
unmeasured quality gain. "Maximum intelligence per unit of cost" argues against it, not for
it. It is also the archetypal speculative capability — built because it sounds like what a
smart harness would have, not because a requirement asked for it.

**And it is already expressible.** A user who wants reflection writes an agent whose `job`
says so and gives it a subagent. No new machinery, and the behavior is visible in their code
instead of hidden in ours — which also means they can measure whether it helped.

**What would change this.** A measurement on a real task showing a critique pass beats
spending the same tokens on higher `effort`. Absent that number, this stays rejected.

---

### ADR-024 — The secret registry is an id-keyed weakref map, not a WeakSet
**Status:** Accepted (Round 24) · **Fixes an incompatibility between IDL-32 and IDL-33**

**Context.** Round 19 set `Secret.__hash__ = None` and, in the same round, specified the
redaction registry as a `WeakSet`. A `WeakSet` hashes its members. The first line of the
first test that constructed a `Secret` raised `TypeError: unhashable type: 'Secret'`.

**Decision.** `dict[int, weakref.ref[Secret]]` keyed by `id()`, with a finalizer callback
removing the entry. Same lifetime guarantee; never hashes the referent.

**Why it is worth an entry.** Both halves were made by the round whose subject was executing
contracts rather than reading them, and neither was executed. A rule adopted but not applied
is indistinguishable from one never adopted.

---

### ADR-025 — Cache determinism is checked free, in two places
**Status:** Accepted (Round 24) · **Supersedes IDL-17**

**Context.** T-2.3's contract said the linter "adds < 5 ms when the prompt is static".
IDL-17 mandated a 150 ms sleep between renders. Both were approved. Measured, the sleep cost
150 ms on every construction — 150 s across P-1's thousand runs, which is what made the
property suite time out.

Worse, what it detects:

| pattern | caught by a 150 ms sleep |
|---|:--:|
| `uuid4()` | ✅ |
| `time.time()` | ✅ |
| `datetime.now()` to seconds | ❌ |
| `date.today()` | ❌ |

It cost 150 ms and missed the two most likely cases.

**Decision.** Two free checks. At construction, render twice back-to-back — catches
everything that changes on every call. At run time, compare the prefix actually sent across
the first two calls — catches time-based drift with a real inter-call gap, and the watcher
lives on the `Agent` so it spans runs. Construction: 150 ms → 0.02 ms.

**Rank change.** Register #28 moves from "Construction" to "Construction **or** first two
calls", which is honest: the datetime case was never caught at construction, it was only
claimed to be.

---

### ADR-026 — The budget has two guarantees, not one
**Status:** Accepted (Round 24) · **Restates SC-2; corrects RISK-03's mitigation**

**Context.** P-1 found **380 budget violations in 1 000 runs, one at 27× the limit.**

The pre-flight ceiling is only as good as `count_input_tokens`. RISK-03 recorded this risk
as mitigated because "worst-case estimation over-counts by construction". **That is false.**
The worst case over-counts `max_tokens` — the *output* term. It does nothing about an
*input* under-count. The two terms were never separated.

**Decision — three mechanisms:**

1. A 15 % margin on counted input.
2. Calibration from the previous response's real input, **ratcheting upward only**: an
   under-count is the dangerous direction, an over-count merely wastes headroom.
3. A **hard local upper bound** — no tokenizer emits more tokens than the prompt has
   characters. When even that bound fits the remaining budget, the reservation uses it and
   the ceiling is **exact** for that call. Most real calls qualify.

**And SC-2 restated as two claims, because one was not true:**

- **SC-2a (exact):** the harness never *authorizes* a call whose estimate exceeds the
  remaining budget, and authorizes nothing further once spend crosses it.
- **SC-2b (bounded):** actual spend may exceed the budget only by one call's input-count
  error.

| | violations | worst |
|---|---|---|
| as designed | 380 / 1 000 | 27× |
| + margin, calibration | 115 / 1 000 | 8.4× |
| + hard character bound | **3 / 3 000** | **1.008×** |

Measured with adversarial 10× count drift. A real provider's counting endpoint errs by a few
per cent.

**Why not simply reserve the hard bound always.** It is typically ~4× the true count, so
small budgets would refuse to fire — Round 17's defect, reintroduced. The bound is used when
it fits and the estimate when it does not, and the transcript records which applied.

**The honest statement, now in the docs.** A library cannot know the true input cost before
the call. "Never exceeds" was not achievable and should not have been written as a success
criterion.

---

### ADR-027 — `@value` replaces bare `@dataclass(frozen=True, slots=True)`
**Status:** Accepted (Round 25) · **Amends IDL-05**

**Context.** IDL-05 mandates `frozen=True, slots=True` everywhere. Assigning a declared
field raises `FrozenInstanceError`; assigning a **non-field** name — a typo, or attaching
state — raises `TypeError: super(type, obj): obj must be an instance or subtype of type`.
CPython's, not ours: `slots=True` rebuilds the class and the generated `__setattr__`'s
zero-arg `super()` closes over the original.

**Decision.** A single `@value` decorator that applies the dataclass options and replaces
`__setattr__`/`__delattr__` with a message naming the type, the offending attribute, the
field list, and a `difflib` did-you-mean.

**Why it needed an entry.** [§03.8](03-public-api.md#8-error-message-standard) requires every
error to say what happened, where, and the fix. A blanket rule was emitting a message about
`super()` and types for the mistake people make most often. The standard existed; nothing
checked that a *language-generated* error met it.

---

### ADR-028 — Redaction retention is scoped to the run
**Status:** Accepted (Round 25) · **Fixes a security defect created by ADR-024 + IDL-33**

**Context.** RT-13 failed on execution: a `Secret` constructed inside a tool, revealed, and
named in an exception message reached **the model** in full. The registry holds weak
references, so the secret died with the tool's frame — while the string it had been
formatted into outlived it, leaving nothing to match against.

**Decision.** `reveal()` registers the value with the **active run's** redaction scope,
opened for the run and cleared when it ends. The weak registry continues to cover long-lived
secrets.

**Why this bound and not another.** Process-lifetime retention was rejected in Round 19 for
good reasons that still stand. Object-lifetime retention loses exactly the short-lived,
per-request secrets that dominate in a server. The run is the window in which anything
derived from the value can still be written, so it is the smallest bound that works.

**The principle, generalized:** *a redactor that forgets faster than the data it protects
travels is not a redactor.* Retention must be scoped to the exposure window, never to the
object.

**Also fixed here.** Redaction was specified only "on transcript write". A tool error is
returned to the **model** — a wider audience than a log file, and one the transcript
boundary never sees. Redaction now applies at the tool-result boundary too.

---

### ADR-029 — Cache breakpoints keep a rolling read point
**Status:** Accepted (Round 26) · **Completes §07.2.2, which was specified and unbuilt**

**Context.** The assembler placed a breakpoint on the system block only, so the whole
conversation was re-billed every turn: SC-4 measured **71.8 %** against a 90 % floor,
degrading to 61.7 % by turn 10.

Adding the specified breakpoint on the newest message did not help — still 71.5 %. **A cache
entry is only read at a breakpoint present in the current request.** Marking only the newest
message writes an entry that nothing ever reads back.

**Decision.** Mark the last content block of the current final message **and** the position
the previous request marked (index −3 in a user/assistant/user pattern). With the system
breakpoint that is three, inside the provider's limit of four.

**Result:** 95.3 % on turns 3+, and the hit rate now *improves* with length (94.1 % → 96.0 %)
rather than degrading. Input tokens per turn are flat instead of unbounded.

**What the benchmark taught.** The first version of it called `run()` ten times with no
history — a constant prompt — and reported 99.4 %. **A benchmark that cannot fail is
indistinguishable from one that passes.** It now asserts two things: the 90 % floor, and that
the hit rate does not *degrade* with conversation length, because that was the shape of the
real failure and a floor alone would accept a design that merely stayed flat.

---

### ADR-030 — A subagent holds parent headroom; it does not read it
**Status:** Accepted (Round 28) · **Fixes an unenforced claim in §06.4**

**Context.** §06.4 has claimed since Round 7 that a subagent's budget is capped by the
parent's remaining budget and that a child cannot be less safe than its parent. Executed in
Round 28, neither was true: a `$0.10` parent spent **$30** through six `$5` children and
reported `$0.0000`, because each child ran on an independent ledger that never settled back.
SC-2a's ceiling leaked entirely through a documented feature.

**Decision.** The dispatcher — not the tool closure — owns a subagent call. It **holds**
headroom from the parent ledger, runs the child against that cap, and **releases** the hold
against the child's actual spend. The safety floor is checked at parent construction.

**Why a hold and not a read.** Capping by reading `remaining_usd()` left parallel children
each seeing the same headroom and each claiming all of it — a TOCTOU that turned `$0.50`
into `$0.54`. A hold makes them divide the budget instead of multiplying it.

**What is still bounded rather than exact.** SC-2b bounds overshoot by one call's
input-count error **on one ledger**; delegation composes ledgers, so the bound **compounds
one level per delegation** (≤ 1.25× measured at depth 1). Stated in
[§07.1](07-cost.md#1-the-budget-is-a-ceiling-not-an-alert). Cycles are impossible: an
`Agent` is frozen before it can be wrapped.

---

### ADR-031 — Underscore-prefixed tool parameters are never model-facing
**Status:** Accepted (Round 28)

**Context.** Plumbing the parent's remaining budget into a subagent made it a parameter of
the tool function, and the schema builder demanded an annotation — which would have placed
a harness-internal field in the schema the **model** sees, and therefore can set.

**Decision.** A parameter whose name starts with `_` is harness-internal: excluded from the
generated schema, exempt from the annotation requirement, and stripped from model-supplied
arguments before invocation.

**The general point.** The awkwardness was the signal. A mechanism that forces internal
state through a model-facing surface is telling you the surface is wrong, and the fix is a
rule rather than an annotation.

---

### ADR-032 — LangGraph is the loop, and it is an optional extra

**Context.** The user mandated building on LangChain/LangGraph. ADR-001 held that the
harness owns the loop, because the loop is the only place a budget check can precede a
model call and a permission check can precede a tool call.

**Decision.** The graph backend (`harness.lg`) is the mandated implementation, and it is
installed as `harness[graph]`. `import harness` must not import LangChain.

**Why the extra, and not the core.** Measured, not estimated: `langgraph` resolves to
**36 transitive packages** against NFR-05's budget of three. That is a twelve-fold
overrun, and it is not negotiable away by wanting it less. Making it an extra is what
lets both statements be true — the mandate is satisfied for anyone who wants durability
and the graph, and a user who wants an agent in one `pip install` still gets a 90 ms
import with three dependencies. **The council does not consider the dependency cost
"paid for" by the mandate; it considers it deferred to the people who choose it.**

**What survives from ADR-001 and what changes.** The reasoning survives intact; only its
mechanism changes. **The choke point becomes a graph edge instead of a line of code**, and
that is stronger: `unguarded_paths()` walks the compiled graph refusing to traverse a gate
and reports any guarded node still reachable — a reachability proof that holds for paths
no test walks. `build_agent` refuses to return a graph for which it is non-empty.

**Cost.** Two implementations of one set of rules, which is the defect class Round 35 spent
itself on. That cost is bounded by `tests/test_parity.py`, not by care.

---

### ADR-033 — Every exit routes through one `finish` node

**Context.** Round 35 found six of fifteen event kinds unemittable on the graph.
`run.finished` was missing because each branch routed straight to `END` and each branch
had to remember to emit it — the Round 27 defect in new code.

**Decision.** All exits route to a single `finish` node, and `END` joins `model` and
`tools` in the `GUARDED` table, so the reachability proof covers the closing event.

**Why.** A closing event that depends on every branch remembering is a **Documented**
defense on §08's ladder. One node that every path must traverse is **Impossible-to-omit**.
Adding a branch later cannot silently drop the event, because the branch has nowhere else
to go.

---

### ADR-034 — Redaction is scoped at the write boundary, never around the caller

**Context.** The graph port dropped `redaction_scope()` and RT-13 returned: a per-request
secret reached the model in cleartext (H35.1).

**Decision.** `redaction_scope()` opens inside the tools node, around the code that turns
a tool's return value into bytes — not around `invoke()`.

**Why not around the run.** The caller drives a compiled graph directly; there is no
harness-owned function wrapping it. A rule that requires the caller to remember a context
manager is not a rule. The tools node is the one place a tool's value becomes an outgoing
message, so scoping it there is both sufficient for the leak and bounded to exactly the
window in which the value can still be written.


### ADR-035 — A context database is a `Store`, and its recall is `external`

**Context.** The mandate named OpenViking, a context database for agents. It presents
memories, resources and skills as a virtual filesystem and does semantic retrieval —
precisely the thing [§04.5](04-interfaces.md#5-store) declared a non-goal while reserving
the seam for it.

**Decision.** `harness.memory.viking.VikingStore` implements `Store`. It ships its own
model-facing tools with the effect classes already set, and **`recall` is `external`**.

**Why `external` and not `read`.** A context database ingests web pages. What it returns
may be attacker-authored, months ago, on a different machine. `read` does not taint, so a
`read` classification means a poisoned memory buys `danger`-tool privileges for the rest
of the run — a hole in the taint lattice the size of the memory system. The author cannot
reasonably make this call themselves, so the store makes it: **Impossible-to-omit rather
than Documented** on §08's ladder.

**Why the tools ship with the store.** The construction-time refusal of `external` +
irreversible (F9.1) only fires if the tool set *says* it is external. Leaving the
classification to the caller means the strongest check in the package silently does not
apply to the largest untrusted-input surface in the system.

**Least privilege.** The SDK client can create accounts, regenerate keys, delete sessions
and `rm` paths. The store holds six capabilities and enforces the list on every call.
`delete()` writes an empty value rather than calling `rm`, because emptying is reversible
in the database's own history and `rm` is not.

**Unknown failures are failures.** `NOT_FOUND`/`INVALID_URI` mean absence;
`UNAUTHENTICATED`/`PERMISSION_DENIED` are a `ConfigError`; **everything else, including an
unrecognised code, raises.** Mapping an unknown outcome to "nothing remembered" is
fail-open in the direction where the wrong answer is plausible — the agent tells a customer
their order does not exist. Same rule as IDL-30.

**Dependency.** `harness[viking]` depends on `openviking-sdk` (nine transitive packages,
`httpx` only), never on `openviking` (185, including a web crawler and two other LLM SDKs).
**A database is a process you run, not a library you vendor.**

**Not integrated:** `get_session_context()`, OpenViking's own context assembler. Two
things deciding what goes in the context window is R-17's defect class with a bigger blast
radius. The `Store` seam is the boundary; widening an integration because the vendor
offers more is how a library acquires a second architecture.


### ADR-036 — On a checkpointed backend, the thread's state is the only memory

**Context.** Round 37 found three defects with one cause: `Ledger`, `TaintTracker` and the
open reservation lived on the `Runtime`, which is built once per compiled graph and serves
every conversation that graph ever handles.

**Decision.** Nothing about a run lives on the `Runtime`. The ledger is rebuilt from graph
state on every node via `Ledger.snapshot()`/`restore()`; taint is restored from
`state["tainted"]`; the reservation hand-off from the budget gate to the model node goes
through `state["max_tokens"]`.

**Why not a `ContextVar`.** It was tried first, and it does not work: **LangGraph runs each
node in its own copied context**, so a value set in one node is not there in the next. This
is worth stating because `redaction_scope` *does* use a `ContextVar` correctly — within a
single node, which is exactly the scope that survives.

**What it buys beyond the fix.** Per-conversation accounting that survives a process
restart, including ADR-026's calibration, which was previously per-process and lost on
every deploy.

**Semantics.** USD accumulates across a conversation, the step ceiling is per turn —
parity with `Chat`, which settled this in M4. A conversation-wide step ceiling would kill a
long conversation permanently.

---

### ADR-037 — `stop_reason` is cleared by the budget gate, and only there

**Context.** `stop_reason` is checkpointed, and Round 35 made it load-bearing for routing
when every exit was moved through the `finish` node. A finished thread therefore came back
carrying `"completed"` and the next turn routed straight to `finish`: multi-turn was
silently dead.

**Decision.** The budget gate clears `stop_reason` on its success path. It is the entry
node of every run — proven by the same reachability check that guards the model — so the
clearing cannot be missed by a branch, and it happens before any routing decision reads it.

**Why not clear it in `finish`.** The caller reads `stop_reason` off the returned state;
clearing it there would answer every run with `None`.


### ADR-038 — One stop-reason table, imported by both backends

**Context.** Round 38 found the graph backend never read the provider's stop reason: a
refusal and a `max_tokens` truncation both have no tool calls, so both were reported as
`completed`.

**Decision.** `run._MAP` and `run.CONTINUE` are the single table. The graph imports them.
`pause_turn` joins `tool_use` as a reason that continues the loop rather than ending it,
bounded at `MAX_PAUSES = 5` and loud at the bound on both backends.

**Why imported and not re-listed.** R-17 is scored 25 because two implementations of one
rule drift. A stop-reason table is exactly the kind of thing that gets copied "just for
now" and then diverges on the next model release.

**What `pause_turn` means.** The provider says the turn is resumable — it is what a server
tool returns when the model pauses mid-turn. Treating it as a terminal error was not
conservative, it was wrong: it failed the one feature that produces it.

---

### ADR-039 — Refusal fallbacks are on by default, and off by one flag

**Context.** IDL-19 has said "server-side refusal fallbacks enabled by default" since
Round 7, in three documents. The payload had never carried them (H38.2).

**Decision.** `AnthropicProvider(fallbacks=True)` is the default and sends
`betas=["server-side-fallback-2026-07-01"]` with `fallbacks="default"` on the beta
messages endpoint. `fallbacks=False` restores the plain endpoint.

**Why `"default"` and not a model list.** It routes by refusal category, so there is no
list to maintain as models change — the failure mode of a hard-coded fallback list is that
it silently names a retired model.

**Care required.** The scalar form pairs with `-2026-07-01` and the array form with
`-2026-06-01`; mixing them is a 400. Asserted by a test rather than trusted to memory.

**Still unverified against the live API** — like the rest of this provider (OI-11).

### ADR-040 — `@value` declares itself to type checkers

**Context.** `@value` (IDL-05) returns `type[T]`, so a checker sees the undecorated class
body. Round 39 measured the effect: 86 of 112 mypy errors, and **no type checking at all
for anyone using this library's core value types**.

**Decision.** `@value` carries `@dataclass_transform(frozen_default=True)` (PEP 681). The
two classes that use bare `__slots__` with `object.__setattr__` — `Agent` and `Secret` —
declare their fields as class-level annotations, which under `__slots__` create no class
attribute and change no behaviour.

**Why it matters more outside the package than inside.** The internal error count is
cosmetic. What was not cosmetic: `Usage(input_tokns=1)` passed silently, and `agent.name`
— documented public in §03 and stable under §04.8 — was reported as nonexistent to every
user with a type checker. §II question 5 exists to turn runtime errors into earlier ones;
this had inverted it for the whole data layer.

**Scope.** Not `mypy --strict`. The remaining six diagnostics are narrowing and
monkeypatch limitations, each proved safe and each carrying a one-line reason at the site.

### ADR-041 — `budget.unlimited` is the taxonomy's 16th kind, added for S-20
**Status:** Accepted (S-20 fix)

**Context.** `Budget(usd=None)` is a deliberate escape hatch, not a defect: a free
provider (`FakeModel`, a local model) has nothing to divide by and nothing to spend
(IDL-36). `docs/04-interfaces.md`/`docs/07-cost.md` already committed, in prose, to a
specific promise before any code kept it: "`Budget(usd=None)` is permitted but requires
passing `None` explicitly, and emits a `budget.unlimited` warning event on every run.
Unlimited is possible; it is not silent." An audit this session found the mechanism did
not exist — `Ledger.size_call()`/`reserve()` both silently skip their ceiling check when
`usd is None`, and nothing downstream could tell "chose unlimited on purpose" from
"budget axis dropped by accident" (the exact ambiguity S-20 in `design/review-security.md`
flags, and K-17 in `design/review-kiss.md` flags independently).

**Decision.** Add `EventKind.BUDGET_UNLIMITED` (`"budget.unlimited"`). Fire it exactly
once per run (classic loop) / once per thread (LangGraph, same `step == 0` guard
`RUN_STARTED` already uses), immediately after `RUN_STARTED`, whenever `budget.usd is
None`. Do not touch `Budget`'s type or construction — `usd: Decimal | None` stays exactly
as documented.

**Rejected alternative.** An earlier draft of this fix (`design/08-roadmap-and-release-
plan.md §1`, first pass) proposed rejecting construction outright — `Agent.__init__`/
`build_agent()` raising unless the provider self-declares free. Rejected on discovering it
contradicts the already-published contract above: the two living docs had settled on
"visible, not impossible" (the same Poka-Yoke shape as `Approval` in S-11 — record the
choice, don't forbid it) before this fix was written. Implementing the hard-reject would
have shipped a THIRD, undocumented design for the same knob.

**Cost accepted.** The taxonomy goes from fifteen to sixteen kinds — the exact churn
`docs/05-data-and-state.md §1`'s closed-taxonomy rule warns against, spent on making a
promise the docs had already made in prose keep its side of the bargain in code.

---

### ADR-042 — Retry is bounded, backed off, and reads `EffectProfile.retryable`
**Status:** Accepted (M6/T-6.3)

**Context.** `EFFECT_PROFILES[...].retryable` (ADR-003, Round 5) already derives the right
answer per effect class — `read`/`external` `True`, `write`/`danger` `False` — but nothing
in the dispatch path read it. A transient tool failure (a flaky HTTP call, a momentary
store timeout) surfaced as a single `is_error` tool result and left the model to decide
whether to try again itself, at the cost of a full extra model round trip for something
the harness already had enough information to retry on its own.

**Decision.** `Dispatcher._invoke()` retries a failing call up to `MAX_ATTEMPTS` (3) total
attempts, with a doubling backoff (`RETRY_BACKOFF_S * 2**attempt`, capped at
`RETRY_BACKOFF_MAX_S`), but only when `EFFECT_PROFILES[spec.effect].retryable` is `True`
**and** wall-clock budget remains. `write`/`danger` get exactly one attempt, unconditionally
— `attempts = 1`, no branch that could extend it. `asyncio.CancelledError` is never caught
by the retry loop; it still propagates on the first raise (T-6.2 depends on this).

**Why never retry write/danger.** A tool that raised after starting a side effect has an
UNKNOWN outcome — did the write land before the exception, or not? Retrying assumes "not,"
which is the exact double-effect S-4 warns about. Without a real idempotency key (T-6.1,
not yet built), a silent auto-retry of `write`/`danger` would manufacture the very bug class
docs/05 §3's resume semantics already refuse to guess at ("never re-executed... let the
model decide how to proceed"). Retry for write/danger is out of scope here **on purpose**,
not an oversight — T-6.1 landing is the precondition for ever revisiting this.

**Rejected alternative.** Retry every effect class uniformly (simpler code, one code path).
Rejected: the entire point of `EffectProfile.retryable` existing since Round 5 is that this
decision is not uniform, and a harness that already computed the right answer and then
ignored it is worse than one that never computed it — it looks safe on inspection.

**Cost accepted.** A flaky `write`/`danger` tool fails fast, in full, to the model on the
first error — no different from before this change. Only `read`/`external` behavior moved.

---

### ADR-043 — `execute_once` is built as a standalone contract, not wired in yet
**Status:** Accepted (M6/T-6.1)

**Context.** T-6.1 (`docs/17-research-alignment.md`) names idempotency as the M6
prerequisite for M9's Service API: a client that times out and retries an HTTP request
can double an email/charge/write unless the harness can recognize the retry. `Store`
(ADR-002 — already a seam, `memory/inmemory.py`/`memory/sqlite.py` today) already has
exactly the `get`/`put` shape idempotency needs; no new seam is justified.

**Decision.** Ship `harness/idempotency.py`'s `execute_once(store, key, fn, *,
fail_open=False) -> (result, was_replayed)` as a tested, documented primitive — and stop
there. Do **not** add an `Agent(idempotency_store=...)` parameter or wire it into
`Dispatcher`/`Runtime` in this pass.

**Why stop there.** `idempotency_key = f"{run_id}:{call_id}"` only dedupes calls that
share a `run_id` — and `run_id` is generated fresh on every `atry_run()` (`agent.py`), so
nothing in the harness today calls the same `run_id` twice. The scenario T-6.1 exists to
prevent — an external caller retrying a whole request — needs an external key from
outside the harness, and the only place one will ever come from is M9's Service API. A
`write`/`danger` tool already gets exactly one attempt per call (T-6.3/ADR-042), and
`Agent.resume()` already blanket-refuses to re-run them after a crash (docs/05 §3) — so
wiring `execute_once` into today's dispatch path would gate calls no test could exercise
for a real double-invocation, because none exists yet. That is precisely the
"speculative generality" ADR-002's rejected alternative names: surface with no consumer.

**What this buys M9.** When the Service API lands and a client-supplied idempotency
key exists, wiring it in is a call to an already-tested function at the call site, not a
new design decision made under M9's own time pressure.

**Rejected alternative.** Wire it into `Dispatcher._invoke`/`lg/runtime.py::_run_tools`
now, keyed by `f"{run_id}:{call_id}"`, even with no external caller. Rejected: it would
be dead code by construction (nothing today produces a repeated key), and — per docs/17
T-6.1's own failure-mode line, fail-closed for write/danger — a **store outage with no
real duplicate call in flight** would start failing every write/danger tool call in every
run, a regression with no corresponding safety gain until M9 exists to actually use it.

---

### ADR-044 — Provider failures are caught and returned as a `Result`, never raised
**Status:** Accepted (M6/T-6.4, found by chaos testing)

**Context.** Writing T-6.4's "provider timeout" chaos scenario found that neither
backend ever caught a provider exception: `models/anthropic.py` maps real SDK failures to
`ProviderError`/`ProviderTimeout`/`ProviderRateLimited`/`ProviderUnavailable`/
`ProviderAuthError`/`ProviderBadRequest` (`errors.py`), and grepping the whole of
`src/harness/` for an `except` on any of them found none. Every real rate limit or
transient timeout against a paid provider crashed straight out of `try_run()` /
`graph.invoke()` — the single most common failure mode against a real provider,
contradicting `try_run()`'s own documented contract ("never raises for run outcomes",
docs/03-public-api.md) exactly the way N-2 (`returns=` validation) did, independently,
at a different call site.

**Decision.** `run.py`'s loop wraps `self._p.complete(...)` in try/except; `lg/
runtime.py::call_model` wraps `self._model.invoke(...)` the same way. Both turn the
exception into a normal `stop_reason=ERROR` outcome instead of letting it propagate.
`ERROR_RAISED`'s `retryable=` field is set `True` for the transient subtypes
(`ProviderTimeout`, `ProviderRateLimited`, `ProviderUnavailable`, and a bare
`TimeoutError` for non-`ProviderError` providers) and `False` otherwise — recorded for
the audit trail even though nothing automatically retries a model call yet, the same gap
`EFFECT_PROFILES.retryable` sat in from Round 5 until T-6.3 gave it a reader (ADR-042).
`asyncio.CancelledError` is not an `Exception` subclass in Python's own hierarchy, so this
catch cannot swallow a cancellation — T-6.2 still holds without needing an explicit
exclusion.

**Cost accepted.** None weighed against — this is a straightforward "an error becomes a
Result, not a crash" fix, the same shape as every other stop reason this loop already
produces. The only judgment call was `retryable=`'s classification, which is
observability only in this pass.

---

### ADR-045 — Workspace confinement rejects, it never escapes
**Status:** Accepted (M7/T-7.1)

**Context.** No workspace concept existed anywhere in `src/harness/` before this — a
file-touching tool had no declared boundary at all. T-7.1 (`docs/17-research-alignment.md`)
asks for exactly IDL-44's already-proven shape (`memory/viking.py::check_key`, keys
becoming a `viking://` path): reject anything that would escape, never try to sanitize
or escape it into safety.

**Decision.** `workspace.py::confine(root, path) -> Path` resolves `path` against
`root` and raises `WorkspaceEscapeError` — not `ConfigError` (AC-06 forbids raising one
from inside `RunEngine`, and this fires on a tool-call-time, often model-supplied
argument) — for anything that lands outside `root` after resolution. Four checks run
before the join, not after: absolute paths (POSIX and Windows-shaped, since
`os.path.isabs` only recognizes the platform it runs on), a null byte, and any
percent-encoded sequence (rejected outright, never decoded-then-checked). The
containment check itself (`Path.relative_to`) runs on the fully resolved path, so an
existing symlink inside the workspace that points outside is caught — resolution
follows it before the check runs.

**A real bug this caught in its own first draft.** `Path(root) / path` silently
discards `root` when `path` is itself absolute (`Path("/root") / "/etc/passwd" ==
Path("/etc/passwd")` — pathlib's own documented `/` behavior). The containment check at
the end would still have caught this specific case, but relying on it alone means the
FIRST line of defense for "absolute path" is an accident of what happens to survive
resolution, not a checked precondition — the explicit `isabs` rejection exists so that
guarantee doesn't depend on getting the join-then-resolve step exactly right. Found by
the function's own test suite before this shipped, not in production.

**Rejected alternative.** Strip/rewrite `..` segments and re-validate. Rejected for the
same reason IDL-44 rejected it for store keys: escaping is a thing you can get subtly
wrong (a rewrite step is itself surface for a second bug), refusing is not.

---

### ADR-046 — `allowed_hosts` defaults to deny-all; `None` is the explicit escape hatch
**Status:** Accepted (M7/T-7.2) · **Breaking change**

**Context.** `EgressPolicy` (S-18) was already correct in its own mechanism —
`allowed_hosts=None` meant unrestricted, a non-empty list meant "only these," an empty
list would already have meant deny-all if anyone ever passed one. What was wrong was the
**default**: omitting `allowed_hosts=` entirely meant `None` — unrestricted — silently.
Research named this the exact failure mode of "approval mistaken for isolation" (W-03):
an allowlist nobody has to opt out of is not a control, and the original default was
chosen (per its own comment) so the getting-started example wouldn't need to think about
it — exactly backwards for a *default*, versus a *documented escape hatch*.

**Decision.** `Agent`/`build_agent`'s `allowed_hosts` parameter now defaults to `()`
(empty tuple) instead of `None`. `EgressPolicy`'s internal logic is untouched — `()`
already meant "no host matches, deny everything" the moment anyone passed it; the only
change is that this is now what happens when nobody passes anything. `allowed_hosts=None`
passed EXPLICITLY remains the unrestricted escape hatch — the identical shape to S-20's
`Budget(usd=None)` (ADR-041): the operator can still get no restriction, but only by
typing the word that means it, not by omission.

**Breaking change, accepted without a multi-version deprecation cycle.** docs/17's own
T-7.2 line calls for "a deprecation version". This project has
not shipped a 1.0 — design/08-roadmap-and-release-plan.md's own release plan places all of
M6–M10 before v1.0, and a real deprecate-then-break cycle matters most for a stable API
with external users, which this codebase does not have yet at its current 67.8/100
self-score. Flipping now, documented plainly here and in docs/03/04/06, is the honest
version of "get this right before 1.0" rather than performing a deprecation cycle with no
one on the other end of it.

**Blast radius, checked by running the full suite rather than guessing.** Five existing
tests (two in `test_lg.py`, one each in `test_parity.py`/`test_redteam.py`, one in
`examples/proof.py`) relied on the old allow-all default to let an `external` tool with a
URL-shaped argument through so they could test something else (taint propagation, mostly)
— each fixed with an explicit `allowed_hosts=None`, since none of them were testing egress
restriction in the first place. `tests/test_m7_t72_egress_default.py` is the test that
actually asserts T-7.2's own behavior (both backends), which none of the five above did.

---

### ADR-047 — `Sandbox` is a sixth plugin seam, extending ADR-002
**Status:** Accepted (M7/T-7.3, T-7.4)

**Context.** ADR-002 fixed five plugin seams — Tool, ModelProvider, Store, Policy,
Exporter — on the three-part test (docs/02-architecture.md §2.4). §01.5 names container
isolation a stated non-goal ("needs process/WASM isolation — a different product"), which
research (W-03: "approval mistaken for isolation") named as an evasion, not an answer: a
library cannot ship a container runtime, but it can ship an honest boundary and a seam a
real one plugs into.

**Decision.** `Sandbox` (`sandbox.py`) is a `Protocol` — `run(cmd, *, cwd, env, timeout)
-> Completed` — checked against the same three-part test the other five passed: (a) a
reasonable third party would publish one (Docker/Firecracker/gVisor wrappers exist on
PyPI today), (b) core needs zero knowledge of a concrete implementation, (c) two
genuinely different implementations ship today — `InProcess` (no isolation, says so in
its own name and docstring) and `Subprocess` (clean environment, own process group,
hard timeout — still not namespace/cgroup isolation, but the honest ceiling of what pure
Python can do). It passes all three, so it is a sixth seam, not a violation of ADR-002's
five — the count in that ADR was never meant to be permanent, only to reject speculative
seams that fail the test (its own rejected alternative: "nine ABCs with one
implementation each").

**T-7.4, folded into the same module.** `_check_env` rejects a `Secret` instance in
`env` outright (`TypeError`, pointing at T-7.4 and the fix), rather than letting it
silently stringify to `<name hidden>` (IDL-32) and hand the child process a useless,
confusing placeholder. Neither implementation ever merges `env` with `os.environ` — the
one guarantee that makes "secrets never appear in the sandboxed process's environment"
red-team-testable at all, since this process's own `os.environ` can carry the harness's
own provider API key.

**Not wired into `Agent`/`dispatch.py` in this pass — same reasoning as T-6.1's
`execute_once` (ADR-043).** No tool in this codebase currently declares a need to run an
arbitrary shell command; wiring `Sandbox` into tool dispatch (a `@tool(sandbox=...)`
surface, say) with no such tool to exercise it would be exactly the same "surface with
no consumer" ADR-002's rejected alternative warns against. What ships here is the seam
and its two implementations, provable standalone — a third-party `Sandbox` plugs in
without touching core, which is T-7.3's own Done criterion, independent of whether any
shipped tool uses it yet.

---

### ADR-048 — Envelope v1: `Event` carries `schema_version`, `trace_id`, `tenant_id`, `session_id`
**Status:** Accepted (M8/T-8.1)

**Context.** `Event` (`observe/events.py`) had `seq`, `ts`, `run_id`, `kind`, `step`,
`data` — no version field, so a future breaking change to the shape would break every
exporter reading it with no way to detect the mismatch, and no multi-tenant or
distributed-trace metadata, which T-8.5 (a consumable `agent.stream()`) and T-8.6 (a
`Session` resource) both explicitly depend on before they can be built.

**Decision.** Four fields, appended (not inserted) to `Event` so every existing
positional `Event(seq, ts, run_id, kind, step, data)` construction site keeps working
unchanged: `schema_version` (a module constant, `EVENT_SCHEMA_VERSION`, bumped only for a
breaking shape change), `trace_id` (defaults to `run_id` inside `EventBus.__init__` —
one run is one trace until T-8.3's real OTel exporter or a distributed caller propagates
one in), `tenant_id` and `session_id` (both `None` unless the caller supplies one).
`Agent`/`build_agent` both grew `tenant_id=`/`session_id=` constructor parameters
threaded straight through to `EventBus`. On the LangGraph backend, `session_id` is
always the thread's own `run_id` (== `thread_id`) — a thread already spans many
`invoke()` calls, the same shape a future `Session` resource (T-8.6) would name; no
separate storage was needed for it.

**Why add fields nobody consumes yet.** Unlike T-6.1/T-7.3 (behavioral seams — ADR-043,
ADR-047 — deliberately left unwired because no real caller exists), this is a data-shape
change: the entire point of a schema version is to lock the shape in NOW so a later
consumer (OTel, a multi-tenant Service API — M9) doesn't force a breaking change to
every exporter that already reads today's `Event`. Adding optional metadata fields with
safe defaults carries none of "speculative generality"'s cost — nothing has to change
behavior to gain them, and every existing test kept passing unmodified.

---

### ADR-049 — `Decision` gains `policy_version`; `ApprovalRecord` was already built
**Status:** Accepted (M8/T-8.2)

**Context.** docs/17's T-8.2 reads as if approval were still a bare `bool`:
"`ApprovalRecord(decision_id, actor, policy_version, decided_at, expires_at, verdict,
reason)` thay cho `bool`." Checking today's code before writing anything (this session's
standing discipline) found `policy/decision.py::Decision` already carries `id`, `verdict`,
`scope`, `actor`, `decided_at`, `expires_at`, `run_id`, `reason` — built earlier in this
session for S-11 (`Actor`, self-declared-identity channel) and S-29 (grant reuse logs a
row every time, not just at the original grant). Only `policy_version` was missing.

**Decision.** Add `policy_version: str | None = None` to `Decision`. `POLICY_ENGINE_VERSION
= "1.0"` (`policy/decision.py`, same role `EVENT_SCHEMA_VERSION` plays for `Event`,
ADR-048) is stamped at both sites `lg/runtime.py` records a `Decision` — the original
grant and S-29's reuse-logs-a-row-every-time record.

**T-8.2's own Failure/Test criteria were already true, not fixed here.** "An expired
approval cannot be reused" — `DecisionLog.lookup()` already filters `not d.live_at(now)`.
"The same approval cannot unlock a second run" — `lookup()` already filters
`d.run_id != run_id` first, so a grant is structurally incapable of crossing a run
boundary regardless of expiry. Both are now covered by
`tests/test_m8_t82_approval_record.py`, closing the verification gap rather than a code
gap — the same "specified but never actually checked" shape S-1/S-5/S-12/S-17/S-23/S-26/
S-28 all turned out to be earlier in this project's history.

**Drive-by fix, found while updating docs/04 for this ADR.** `docs/04-interfaces.md`
documented `policy/base.py`'s class as `Decision` — its real name in code is `Ruling`
(renamed at some point specifically to avoid colliding with `policy/decision.py`'s
`Decision`, going by the K-13 namespace-collision finding this same file's `## 1.5`
tracks). The doc never caught up to the rename. Corrected to `Ruling` in place.

---

### ADR-050 — `OtelExporter` follows docs/10 §2's already-published mapping, not a fresh design
**Status:** Accepted (M8/T-8.3)

**Context.** `[otel]` was a named extra with zero implementation. `docs/10-observability-
ops.md §2` had already published the exact mapping before this session — span names,
attribute names (GenAI semantic conventions), and metric names — the first draft of this
exporter did not check that table first and used different, invented names. Caught before
committing, the same discipline as every fix this session: verify the already-published
contract before writing code.

**Decision.** `observe/otel.py::OtelExporter` follows docs/10 §2 verbatim: span
`harness.run` (`gen_ai.agent.name`, `gen_ai.request.model`), child span `harness.step`,
child span `gen_ai.chat` (`gen_ai.usage.input_tokens`/`.output_tokens`,
`harness.cache_read_tokens`, `harness.cost_usd`), child span `harness.tool`
(`harness.tool.name`, `.effect`, `.truncated`); `policy.decided` as a span event ON THE
TOOL SPAN; `budget.exhausted`/`taint.raised`/`error.raised` as span events with span
status `ERROR` where applicable. Metrics: `harness.run.cost` (histogram, USD),
`harness.run.steps`, `harness.tool.duration`, `harness.cache.hit_ratio`,
`harness.policy.denials` (counter, by tool/reason). `include_content=False` (default)
strips any data key recognized as carrying raw text before it reaches a span attribute.

**A real sequencing problem this surfaced, not invented.** `policy.decided` fires BEFORE
`tool.started` for an ALLOWed call (`dispatch.py` resolves policy for every planned call
before any of them run) — so attaching it to "the tool span" per the documented mapping
is impossible at the moment it arrives; that span doesn't exist yet. Fixed by buffering
pending `policy.decided` events keyed like the tool-span store, flushed the instant
`tool.started` creates the span; a DENYed call (no tool span ever comes) attaches
immediately instead, and a duplicate call that is decided but never dispatched (T-2.5/
S-26) is flushed onto the step span at `step.finished` rather than leaking forever.

**Two real bugs the exporter's own test suite caught before commit.** `cost_usd` in
event data is `str(Money(...))` — a `$`-prefixed string (`"$0.0050"`) — and a bare
`float(v)` raises `ValueError` on the `$`, silently swallowed by the surrounding
try/except into "no cost recorded"; `_parse_float` now strips a leading `$` first. A
`pending`/`flushed_ev` variable-name collision across two branches of the same `emit()`
method confused mypy's flow-sensitive typing into rejecting a valid `.pop(key, None)`
call — renamed rather than suppressed.

**Deferred, and named honestly rather than silently worked around:** `gen_ai.usage.
output_tokens`/`harness.cache_read_tokens` populate only when the source event carries
that data — which, per N-6 (design/07-risks-and-open-issues.md §3), it never does today on either backend
(docs/05's own table promises `model.response` carries `usage`/`latency_ms`; the actual
emit sites only pass `stop_reason`/`cost_usd`). Fixing that is a `run.py`/
`lg/runtime.py` change, out of scope for an exporter that can only read what's emitted.
Provider-level retry (N-5, docs/10 §3's Retry column) is a similarly out-of-scope gap
found while reading the same section of docs/10.

---

### ADR-051 — `cost_per_success` reports a Wilson-scored interval, never a bare number
**Status:** Accepted (M8/T-8.4)

**Context.** S-06 (docs/17-research-alignment.md §2.2, citing external research §10):
cost-per-TASK is the wrong formula — a failed run still burned tokens, so averaging
total spend over every attempt rewards a policy that fails often but cheaply per attempt.
The right denominator is `P(success)`, and per docs/17's own T-8.4 line, the result must
carry a confidence interval, not stand alone.

**Decision.** `harness/eval/cost.py::cost_per_success(runs, confidence=0.95)` computes
`total_cost / success_rate`, with the confidence interval built by taking a Wilson score
interval (`_wilson_interval`) on the underlying success rate and propagating it onto the
cost figure — cost-per-success is a decreasing function of the success rate, so the
rate's LOW bound gives cost-per-success's HIGH bound and vice versa. Wilson, not the
normal approximation (`p ± z·sqrt(p(1-p)/n)`): the normal approximation can produce a
bound outside `[0, 1]` at small `n` or extreme proportions, exactly the regime a golden
set with a handful of tasks and a near-100% or near-0% pass rate sits in.

**`cost_per_success_usd` is `None`, not `0` or `inf`, when nothing succeeded.** Money was
spent and the number is genuinely undefined — reporting a placeholder here is exactly the
"confident empty answer" class of bug IDL-30/fail-visible exists to prevent, and
`__str__` says so in words ("cost per success is undefined") rather than printing a
number that looks measured.

**Package shape.** `harness/eval/` (a package, not a flat module) — M10
(`docs/17-research-alignment.md` T-10.1-10.3: trajectory contracts, golden set, bench)
will need `harness.eval.*` too; starting as a package now avoids a reshuffle when those
land, at zero cost today (`__init__.py` re-exports the one function that exists).

**z-scores are a lookup table (`_Z_SCORES`, four values), not `scipy.stats.norm.ppf`.**
`confidence` is restricted to `{0.80, 0.90, 0.95, 0.99}` — the standard set any
statistics reference has memorized — rather than adding `scipy` as a dependency (NFR-05)
for an interpolation this narrow a use case does not need.

---

### ADR-052 — `agent.stream()` yields real `Event`s; deltas stay on `on_delta=`
**Status:** Accepted (M8/T-8.5)

**Context.** docs/17's T-8.5 asks for `async for ev in agent.stream(msg)` over the 16-kind
taxonomy, distinguishing "text delta, tool-call delta, tool result, approval request,
retry, cancellation, final." The taxonomy is closed on purpose (docs/05 §1); inventing a
second, parallel event shape just for streaming would be a second taxonomy to keep in
sync with the first.

**Decision.** `Agent.stream(message, on_delta=None)` is an async generator yielding the
real `Event` objects the run already produces (T-8.1's full envelope included), via a
per-call exporter that pushes onto an `asyncio.Queue`; `on_delta=` remains the existing,
separate token-level text-delta mechanism, unchanged and un-multiplexed into the yielded
stream. Every distinction T-8.5 names maps onto an existing kind: tool-call
request/result → `tool.requested`/`tool.finished`; approval request → `policy.decided`
(verdict `ASK`); retry → `error.raised` (`retryable=True`); cancellation/final →
`run.finished` (`stop_reason="cancelled"`/otherwise). Built via `Agent.with_()` (ADR-004:
frozen, no mutation) to layer one extra exporter onto whatever the agent already carries,
never replacing them. Cancelling the `async for` (a `break`, or `aclose()`) cancels the
underlying run, extending T-6.2's cancellation-propagates guarantee through the
generator rather than swallowing it.

**A real, independently-reachable bug this surfaced: N-7.** Writing `stream()`'s own
transcript test found `Agent.with_()` silently drops `transcript`, `exporters`,
`accepts_tainted`, and `sensitive` — not only when a caller happens to override one, but
on EVERY call, because those four were simply absent from `with_()`'s base-field dict
(`accepts_tainted`/`sensitive` doubly so: they are not even readable as
`self.accepts_tainted` — `__init__` folds them into `self._grants`, a `Grants` of
frozensets, and never keeps a same-named attribute). Confirmed directly before fixing:
`Agent(transcript=..., accepts_tainted=[...]).with_(name="X")` returned an agent with
`transcript=None` and an empty `accepts_tainted`. Fixed by adding the two attribute
names and reading the other two off `self._grants`. `tests/test_n7_with_preserves_
fields.py` (7 tests, plus `stream()`'s own transcript test) locks this in, independent
of `stream()` itself — this is a bug `with_()`'s own callers hit regardless of T-8.5.

---

### ADR-053 — `Session` names what Round 37 already isolated; scoped to the classic backend
**Status:** Accepted (M8/T-8.6)

**Context.** docs/17's T-8.6: "`Session` is a resource — id, ownership, TTL, fork, resume,
and concurrency boundary. Round 37 fixed the leak; this is the part that names something
that already existed implicitly." The state isolation this depends on already exists — one `Ledger`/`TaintTracker`/
`EventBus`/`DecisionLog` per run or thread, never shared (Round 37's original fix, and
S-15/S-24/S-29 in this session's own history for the LangGraph backend specifically).
What's missing is a name and a handful of resource-lifecycle properties.

**Decision.** `harness/session.py::Session` wraps `Chat` (ADR-020, the classic backend's
existing multi-turn object) rather than reinventing multi-turn state: `id` (`"sess_" +
uuid`), `owner`, `ttl_s`/`expires_at`/`expired()`, `.fork()` (a new `Session`, new id,
whose history starts as a value-copy of the original — mutating one never touches the
other), `.resume_from()` (wraps the EXISTING `Agent.resume()` transcript-replay
mechanism in the Session lifecycle, honestly — it does not build a richer resume than
what already exists; `Agent.resume()` re-issues the original message with an
interrupted-tool note, it does not reconstruct full history from a transcript, and
nothing in this codebase does that yet), and a `threading.Lock` for the concurrency
boundary (`threading`, not `asyncio`: `Chat.say()` is synchronous — it calls
`Agent.try_run()`, which itself runs `asyncio.run()` internally — so the lock matches
the primitive it protects rather than forcing an async boundary onto a sync one).

**Scoped to the classic backend, not built symmetrically for LangGraph.** On that
backend, `thread_id` (checkpointer-durable) is already the session primitive —
`session_id` is set to it (T-8.1, ADR-048). A symmetric `Session` there would need
checkpointer-level metadata storage (ownership, TTL) this library does not build; that
is new infrastructure, not the naming pass T-8.6 describes, and building it here would
have meant inventing a design decision beyond what was asked.

**Concurrency boundary, proven with a forced (not timing-based) race.** The test
suite reproduces the exact race `Session._lock` prevents deterministically: two threads
are held inside a controlled provider until BOTH have read `Chat._messages` (empty), then
released in order — without the lock, the second thread's write overwrites the first's,
leaving 2 messages instead of 4. A `time.sleep`-based version of the same test also
exists and passes, but the forced version is what actually PROVES the mechanism rather
than making the race merely likely.

---

### ADR-054 — `harness.mcp` classifies third-party tools itself; hints are a default, never the decision

**Status:** Accepted (M9/T-9.1)

**Context.** `design/03-tools-and-mcp.md §5` specified this in full before any MCP
integration existed in code — the same "living design, no code yet" situation T-8.3's
OTel mapping was in. Five review findings sat on hold for exactly this reason
(`07-risks-and-open-issues.md §0`): S-7, S-8, S-9, S-10 (`ServerIdentity`, rug-pull,
hint-downgrade, `proposed_scope`) and S-17 (description injection). None could be fixed
before something in `src/harness/` actually called `tools/list` — landing them
individually earlier would have been patches with no real caller to test against, the
same anti-pattern ADR-043/047 name for `execute_once`/`Sandbox`.

**Decision.** `harness/mcp/` (an extra — `pyproject.toml`'s `mcp` group, `mcp>=1.9`;
`import harness` never imports it, ADR-032's rule extended) ships:

- `McpServerPolicy` — operator-held, keyed to a `ServerLabel` (v1: a bare string, no
  `fingerprint` — K-12's reasoning carried over unchanged: a field whose format never
  settled has no business in a public type).
- `classify_mcp_tool(tool, policy, call) -> ToolSpec` — M-1 (untrusted server: hint
  never participates, `policy.effects` or `default_effect` decide), M-2 (trusted server:
  hint becomes the default, mapped fail-closed — `readOnlyHint is True` exactly, not
  "anything but `False`", the bug-for-bug fix copied from Microsoft's own
  `_map_mcp_annotations_to_labels`), M-3 (`accepts_tainted` only from `policy`, never a
  hint — kept a `McpServerPolicy` field the caller merges into `Agent(accepts_tainted=)`,
  not something this module wires in on its own, matching how `Grants` already works for
  local tools). Prefixes every tool name `f"{server}__{tool}"` (through `slug()`) so
  `ToolSet`'s existing duplicate-name guard can never silently merge two servers' tools
  of the same protocol name.
- `connect(session, policy) -> list[ToolSpec]` — calls `tools/list` exactly once, never
  again on its own initiative. That is §5.4's rug-pull requirement ("classification is
  locked in at bind time") implemented as an absence rather than a check: nothing in this
  module re-lists mid-run, and `ToolSpec` is frozen, so a stale spec is never mutated in
  place — a fresh `connect()` call is the only way to get a new classification, and it
  returns wholly new objects.
- `ToolSpec.server: str | None` (new field, default `None` — every existing construction
  site is unaffected) and `Scope.server` (existing field, unused until now) now
  participate in `Scope.matches()`/`DecisionLog.lookup()`: a grant recorded for a tool on
  server A never satisfies a lookup for the same-named tool on server B. Only the
  LangGraph backend's `DecisionLog` needed the wiring (`lg/runtime.py`'s three call
  sites) — the classic backend never persists a `Decision` across calls (S-11's finding,
  unchanged).

**S-17, closed by documentation, not a filter.** A tool's `description` reaching the
model before any tool call is not a bug a harness can patch out — that is how
tool-calling APIs work, and no policy or taint mechanism runs before the first turn.
`classify_mcp_tool`'s docstring and `McpServerPolicy.trusted`'s fail-closed default
(§5.3 M-1: an unlisted server gets `DANGER` for everything) are the actual mitigation:
an operator who has not reviewed a server's tool descriptions has, by construction, not
granted it anything beyond DANGER-gated tools requiring an ASK on every call.

**S-9's gap stays open, correctly.** `Scope.server` matching stops a grant crossing
between two DIFFERENT server labels. It does not stop a label being re-pointed at a
different endpoint while keeping its name — that still needs `ServerIdentity` +
`fingerprint`, still deferred to when a real re-pointing is observed (K-12).

**Tests.** `tests/test_m9_t91_mcp.py` — hint-mapping table (including the Microsoft `is
True` bug-compat case, with a mutation restoring `!= False` and showing it misclassifies
a write tool as read), policy-precedence (override > trusted-hint > untrusted-default,
each direction), `ToolSpec` construction (name prefix, `server=`, `fn` calling the
PROTOCOL name not the harness-facing one — mutation confirms a wrong-name `fn` is
detectable), `CallToolResult` unwrapping (`isError` raises, does not silently succeed),
`connect()` against a fake `ClientSession` (allowlist filtering, round-trip through
`call_tool`), and `Scope.server`/`DecisionLog.lookup()` cross-server isolation both as a
direct unit test and as an `lg/runtime.py::_regate()` integration test — each with a
mutation restoring the pre-T-9.1 behavior and confirming it goes red.

---

### ADR-055 — `harness.server`: an ASGI Service API, and `execute_once`'s first real caller

**Status:** Accepted (M9/T-9.2)

**Context.** `idempotency.py`'s own docstring (ADR-043) named exactly what it was
waiting for: "M9's Service API is the first real source of [an idempotency key]... wiring
`execute_once` in with no caller that can ever supply a meaningful key would be dead
code." docs/17 §252-258 specifies the routes (`POST /v1/runs`, `GET /v1/runs/{id}`, `GET
/v1/runs/{id}/events` SSE, approvals, cancel, resume) as `harness[server]`, core
untouched — no deeper living-doc contract existed for this one (unlike T-8.3's OTel
mapping, docs/10 §2), so this ADR is where the design gets made, not just cited.

**Decision.** `harness/server/create_app(agent: Agent) -> Starlette` (an extra —
`starlette` only; an operator supplies their own ASGI server). One shared `Agent`
instance serves every run — already safe, since `Agent` is frozen and `atry_run()`
builds a fresh `Ledger`/`TaintTracker`/`run_id` per call.

- **`POST /v1/runs`** — an `Idempotency-Key` header routes through `execute_once`
  (fail-closed, T-6.1's default for a write/danger-class operation): a retried request
  with the same key returns the SAME `run_id` without starting a second run. Proven, not
  assumed — `tests/test_m9_t92_service_api.py`'s `IdempotencyKey` class asserts the
  underlying `FakeModel` is called exactly as many times after a replay as before it.
- **`GET /v1/runs/{id}/events`** (SSE) — reuses the `Exporter` seam (`_SseExporter`), not
  `Agent.stream()` directly, for one reason: redaction. `Secret.reveal()`'s tracking is a
  `contextvars.ContextVar`, scoped to the task that revealed it — `Agent.stream()`'s own
  queue-based exporter would hand a raw `Event` to whatever task is serving the HTTP
  request, which is NOT the run's task, so a `redact()` call there could miss a revealed
  value. `_SseExporter.emit()` runs synchronously inside `EventBus.emit()`, on the run's
  own task — the same place `TranscriptWriter.emit()` already calls `redact()`, for the
  same reason (RT-13, Round 25) — so `_event_json()` redacts there, before the string
  ever reaches the queue a different task drains.
- **`POST /v1/runs/{id}/approvals/{call_id}`** — `approve=` can be a coroutine
  (`PolicyEngine.resolve` already awaits one, ADR-021), so the bridge is a plain
  `asyncio.Future` per pending call, resolved by the HTTP request. This is the SAME shape
  every other `approve=` callback already has — a resolution step, not a policy — just
  fed by an HTTP request instead of an in-process one.
- **Classic backend only**, same scope `Session` (T-8.6, ADR-053) chose: a durable,
  awaitable approval channel exists cleanly there; `build_agent()`'s LangGraph `Runtime`
  uses `interrupt()`/checkpointer semantics that don't compose with a plain `Future`.

**What does NOT ship, and why (mirrors `EgressPolicy`'s "say the limit" discipline):** no
authentication (an operator's own reverse proxy is the boundary — this module is not
one); no persistent run registry (in-memory, one process — a restart loses run state);
consequently **no `resume` route** — naming a resume contract this module cannot keep
would be worse than shipping none, the same reasoning `Session` already applied to the
same question.

**S-4 is NOT closed by this, and the roadmap said it was — a real correction, caught
soaking on this pass.** `design/08`'s M6 section had claimed "landing M6 closes S-4 and
S-23" — wrong even before T-9.2 (M6 built `execute_once` but deliberately did not wire
it into `Dispatcher`/`Runtime`, ADR-043's own text). T-9.2 supplies a real caller at the
RUN level (`POST /v1/runs`'s idempotency key dedupes a request to START a run); S-4's
scenario is at the TOOL CALL level, inside an already-running run — a client retry of a
single `write`/`danger` call, which `Dispatcher._invoke` still does not protect. Recorded
as N-8 (`07-risks-and-open-issues.md`) rather than silently left implied-fixed by an
adjacent, similarly-named mechanism landing nearby.

**Tests.** `tests/test_m9_t92_service_api.py` (21 tests): basic run lifecycle,
`Idempotency-Key` semantics (including a mutation that bypasses `execute_once` entirely
and shows two runs spawn for one key), the full approval round-trip both ways (approve
and deny) plus a mutation proving a tool cannot run without a real HTTP resolution,
cancel at every reachable state (404/409/200), SSE backlog-then-tail ordering, and
redaction across the SSE boundary — including a mutation that removes `redact()` from
`_event_json` and confirms a revealed secret leaks without it.

---

### ADR-056 — One canonical `Event.to_dict()`; `TranscriptWriter` had drifted from envelope v1

**Status:** Accepted (M9/T-9.3)

**Context.** docs/17 §258: *"a canonical event model, many transports — in-process,
SSE, CLI/JSON. No transport has its own semantics."* Landing T-9.2 (SSE) surfaced that
the principle was already being violated by two transports that predate it:
`observe/transcript.py::TranscriptWriter.emit()` and (as first written) this pass's own
`harness.server::_event_json()` each built the same conceptual dict independently. Diffing
them found a real bug, not just duplication: `TranscriptWriter` was written before
envelope v1 (T-8.1, ADR-048) and was never updated — a persisted transcript and a live
SSE stream of the SAME run disagreed about whether an `Event` carries
`schema_version`/`trace_id`/`tenant_id`/`session_id` at all.

**Decision.** `observe/events.py::to_dict(event) -> dict` is now the one function that
defines the JSON shape — `seq`, `ts`, `run_id`, `kind`, `step`, `data`, and all four
envelope v1 fields. `TranscriptWriter` and `harness.server`'s `_event_json` both build on
it; each still layers its own transport-specific policy on top (the transcript digests
`TOOL_REQUESTED.arguments` for at-rest privacy, `ts` gets rounded for a smaller file — a
live API response has no reason to digest a caller's own just-sent request back at it).
That distinction is the actual dividing line the principle draws: a transport's PRIVACY
policy may legitimately differ; the event MODEL — what fields exist and what they mean —
may not.

**A third, genuinely new transport: CLI/JSON.** `harness run <file> <message> --json`
drives `Agent.stream()` and prints one `to_dict()`-shaped, `redact()`-ed JSON line per
event to stdout — the transport docs/17 names alongside in-process and SSE, previously
missing entirely. A shell pipeline now gets exactly what `GET /v1/runs/{id}/events`
would have sent it, for a run driven by the CLI instead of the Service API.

**Tests.** `tests/test_m9_t93_canonical_events.py`: `to_dict()`'s field completeness and
copy-not-alias behavior on `data`; `TranscriptWriter` now carrying envelope v1 fields
plus a regression check that argument-digesting still works; the CLI JSON transport's
line-per-event shape and exit code on a non-`completed` `stop_reason`; two mutations —
one restoring the pre-ADR-056 transcript dict (missing envelope fields, the exact
staleness this fixes) and one swapping `json.dumps(to_dict(event))` for `str(event)`
(the line stops parsing as JSON at all, breaking the CLI/JSON contract outright).

---

### ADR-057 — `Trajectory` contract: eight assertions, one pure function

**Status:** Accepted (M10/T-10.1)

**Context.** docs/17 §9 (cited at §264): a trajectory contract must express `must_call`,
`must_not_call`, `requires_approval`, `max_model_calls`, `max_tokens`, `max_cost`,
`output_schema`, `no_duplicate_side_effects`, and be "runnable with `FakeModel`" — i.e.
checkable offline, without a live provider.

**Decision.** `harness/eval/trajectory.py::check_trajectory(contract, result, events) ->
TrajectoryResult` is a pure function — it runs nothing itself; `golden.py` is the one
caller that drives a real run and hands both a `Result` and the `Event` list it produced
back in. Each of the eight assertions reads from data ALREADY on `Result`/`Event` —
nothing new had to be emitted to make this work, which is itself informative: `Result.
tools_run` (Round 38, IDL-49) already IS "what must_call/must_not_call check against";
`PolicyEngine.resolve`'s `policy="approval"` stamp (ADR-021) already IS the signal
`requires_approval` needs, as long as one is careful to check that field rather than
merely "a `policy.decided` event exists for this tool" — an auto-`ALLOW` (no `ASK` at
all) also emits one, and a mutation test confirms the naive version passes a `read` tool
that was never asked about.

`no_duplicate_side_effects` is the one assertion needing correlation across TWO event
kinds: `tool.started` carries no `arguments` (`dispatch.py` never puts them there —
`tool.requested` does), so a duplicate check has to join on `call_id` first. A mutation
test confirms the naive version (reading `arguments` straight off `tool.started`, which
is always `{}`) collapses every call of the same tool into one bucket regardless of its
real arguments, hiding genuine duplicates behind false ones.

`output_schema` reuses `jsonschema` — already a core dependency (`pyproject.toml`), no
new one for eval tooling — validated against `result.value` when `returns=` populated it
(ADR-022), else `result.text`.

**Tests.** `tests/test_m10_t101_trajectory.py` (15 tests): one pass/fail pair per
assertion, plus the two mutations described above.

### ADR-058 — Golden set reuses `cost_per_success`'s Wilson interval, doesn't reinvent it

**Status:** Accepted (M10/T-10.2)

**Context.** docs/17 §265: "pass rate WITH a 95% confidence interval, tokens, cost —
never a bare number." T-8.4 (ADR-051) already built and tested Wilson-score interval
math for exactly this shape of question (a proportion from a small sample).

**Decision.** `harness/eval/golden.py::run_golden_set(agent, cases) -> GoldenReport`
imports `_wilson_interval`/`_Z_SCORES` from `eval/cost.py` rather than a second copy —
the interval is over PASS rate here instead of SUCCESS rate, but it is the same binomial
proportion question `cost_per_success` already answers correctly at small n and extreme
proportions. `cost_per_success(...)` itself is called for the cost figure, so a golden
run's report and a standalone cost report never disagree about what "total cost" means.

A case's `Trajectory` is optional — a case with none only asserts `Result.ok`, covering
docs/17 §9's simplest tier; most of the negative/adversarial/failure-injection cases
§265 names want a real contract (`must_not_call` the tool an injection prompt tried to
trigger, say).

**Each run is captured with its own events, without mutating the `Agent` under test** —
the same `with_()` + per-call `Exporter` seam `Agent.stream()` (T-8.5) and
`harness.server` (T-9.2) already use, not a fourth reinvention of "how do I get events
out of a run." A test confirms `agent.exporters` is unchanged after `run_golden_set`
returns.

**A real distinction a mutation test makes visible:** a `GoldenCaseResult.passed` that
only reads `result.ok` marks a case PASS whenever the run completed — even one that
violated its `Trajectory` (called a forbidden tool, blew a token budget). `passed`
checks both `result.ok` AND `trajectory.ok`; the mutation test constructs exactly that
scenario (a completed run that called a `must_not_call` tool) and shows the naive
version marks it passing.

**Tests.** `tests/test_m10_t102_golden.py` (7 tests).

### ADR-059 — Benchmark: bounded concurrency, cold start needs a fresh process

**Status:** Accepted (M10/T-10.3, closes Y-05)

**Context.** docs/17: *"Y-05 — Performance has never been measured. 6% weight, and the
only number ever measured is import time."* Not even that measurement was a real,
reproducible artifact — it was a comment recording a number someone ran once.

**Decision.** Two functions, because "performance" here is actually two different
measurements that cannot share a mechanism:

- `harness/eval/benchmark.py::benchmark(run_fn, *, n, concurrency) -> BenchmarkReport` —
  drives `n` calls to a caller-supplied zero-arg async callable under an
  `asyncio.Semaphore`, the same bounded-concurrency shape `Dispatcher._bounded` already
  uses for tool fan-out (NFR-09) — an unbounded benchmark measures whatever the test
  double or provider happens to tolerate at once, not the harness. Reports `p50`/`p95`
  (own percentile interpolation, no new dependency), `throughput_per_s` as `n / wall
  time` at the given concurrency (not `1000/p50`, which is only correct at
  concurrency 1 and silently assumes perfect scaling otherwise — exactly the "no
  concurrency test" Y-05 names), and separates the FIRST call's latency
  (`cold_start_ms`) from the rest (`warm_p50_ms`) rather than folding a cold-cache call
  into the steady-state percentile and hiding it.
- `import_cold_start_ms()` — a FRESH subprocess times `import harness` alone. This
  cannot be measured in-process: the calling process has already paid the import cost
  and cannot pay it twice. Takes an `env=` override so a checkout that isn't `pip
  install`ed (this repo's own test suite, always run via `PYTHONPATH=src`) can still
  call it — a real, installed deployment needs no override.

**Tests.** `tests/test_m10_t103_benchmark.py` (8 tests) — percentile ordering, cold
start separated from warm p50, `n=1`'s well-defined "no cold start to separate out",
concurrency actually bounded (measured via a live counter, not inferred), a mutation
running the same work through bare `asyncio.gather` with no semaphore and confirming
the peak DOES exceed the cap when the bound is removed, and `import_cold_start_ms`
against this checkout (via `PYTHONPATH`) plus its failure mode when the import
genuinely cannot succeed.

---

### ADR-060 — `tenant_id` threaded into `RunContext`/`_Ctx`, not just `EventBus`

**Status:** Accepted (N-9, found while re-running `tests/test_roadmap.py` ahead of a
documentation pass)

**Context.** `Agent.tenant_id`/`Runtime.tenant_id` (T-8.1, ADR-048) stamped `Event`s for
telemetry, but were never threaded into `RunContext` (`dispatch.py`) or `_Ctx`
(`lg/runtime.py`) — the objects `Policy.check(call, ctx)` actually receives. A
multi-tenant deployment could label its events by tenant but could not WRITE a policy
that decided differently per tenant. `tests/test_roadmap.py`'s pre-registered S-03 (a
"definition of done" written before T-8.1 landed) still failed after T-8.1 shipped —
running that suite again, rather than trusting the earlier "M8 done" claim, is what
surfaced it.

**Decision.** `RunContext.tenant_id: str | None = None` (appended field, every existing
positional construction keeps working) and `_Ctx.tenant_id` (new `__slots__` member),
both populated from `Agent.tenant_id`/`Runtime._tenant_id` at every `ctx` construction
site (one in `dispatch.py`, three in `lg/runtime.py`).

**`test_roadmap.py` itself needed two kinds of fix, not one — worth separating.** Of its
five originally-red checks, two were checking for a NAME the final design didn't use
(`hasattr(harness, "ApprovalRecord")` — the real class is `Decision`;
`from harness.testing import Trajectory` — the real module is `harness.eval`, following
the precedent `cost_per_success` already set there) — those checks were rewritten to
match the shipped names, no code changed. `tenant_id` was the one genuine functional
gap — code changed, not the test. `S-03` (the original, conflating `tenant_id` with a
broader `principal`/`scopes` idea T-8.1 never promised) was split into `S-03a` (tenant —
now green) and `S-03b` (`principal`/`scopes` — intentionally still red, a materially
larger authorization model nothing in this codebase has built).

**Tests.** `tests/test_n9_tenant_in_context.py` (5 tests, both backends): a
`TenantAwarePolicy` that denies unless `ctx.tenant_id == "acme"`, exercised on the
classic backend (`Agent(tenant_id=...)`) and the LangGraph backend
(`build_agent(tenant_id=...)`), plus a mutation constructing a `RunContext` without
`tenant_id` and showing two different tenants read back the identical `None` — the
leak this fix closes, made concrete rather than asserted.

---

### ADR-061 — Advisor consultation is a `Policy` gate, never a grant
**Status:** Accepted (user request: "a strong model to handle hard problems... an advisor
pattern")

**Context.** The user asked for two things: smarter multi-model use in general, and
specifically an "advisor" pattern — a strong model the agent consults when stuck. The
first half needs no new mechanism: `model=`, `effort=`, and subagent delegation
(`.as_tool()`) already let a developer route cheap work to a cheap model and hard work
to a strong one, explicitly, in their own code (docs/07-cost.md §4/§6) — ADR-006 already
covers why the harness itself never auto-picks a model. The second half — advisor —
*is* buildable as exactly that: a subagent tool, with no new harness code, whose
description tells the primary agent when to call it (docs/06-safety.md's existing
"expressible today, with no new machinery" framing for reflection/self-critique).

The open question was whether to go further: make consultation MANDATORY before a
dangerous tool, rather than trust the model to remember a prompt instruction. The
naive shape — let the advisor's own verdict decide ALLOW/DENY for the dangerous call,
the same way a human approver does — was rejected immediately on inspection: it
recreates exactly the mistake `design/00-foundation.md §4.2`'s invariant D-1 was
written to name ("`Actor` deliberately has NO `Model` variant... that's exactly where
agno went wrong" — no `Model` variant, on purpose, because that is where agno's design
let a model approve its own action). An advisor is still a model, however much stronger;
if it could grant a `Decision`, the harness would be letting one model rubber-stamp
another's dangerous action, precisely the self-authorization R-3 ("the model holds no
safety switch") exists to forbid.

**Decision.** `RequireBeforePolicy` (`policy/builtin.py`) DENIES a named tool until
another named tool has already completed earlier in the same run — a purely
PROCEDURAL gate (P-2: a `Policy` can only ever restrict). It never grants anything; the
actual ALLOW for the gated tool still has to come from wherever it always did
(`approve=`, or the effect's own default). The advisor tool it gates on is read for its
OPINION exactly like `search` or `fetch` — the model may ignore what it says, and the
policy does not care what it said, only that it was called.

`ctx.tools_called: frozenset[str]` is the fact the policy reads — tool NAMES that
completed earlier in this run, never arguments or results (IDL-15: `Policy.check`
already excludes message history on purpose, so this stays consistent with that — a
bare name is not exfiltratable content). Sourced for free from state each backend
already has: `dispatch.py::RunContext.tools_called` reads `Dispatcher.ran`
(classic — already tracked for `Result.tools_run`); `lg/runtime.py::Runtime._tools_called()`
scans checkpointed `state["messages"]` for `AIMessage.tool_calls` with a matching
`ToolMessage` (durable) — which, by construction, excludes the CURRENT, not-yet-dispatched
batch, so calling both the advisor and the gated tool in the same model turn does not
satisfy the gate (the advisor hasn't answered yet at decide time). Both fields are
appended with a `frozenset()` default, same backward-compat shape ADR-060's `tenant_id`
used.

**Rejected alternative.** Advisor-as-`approve=` callback (the advisor's verdict directly
resolves the ASK). Rejected for the D-1/R-3 reason above — it is not a smaller version
of this feature, it is a different, disallowed one.

**Tests.** `tests/test_advisor_gate.py` (10): the policy unit (deny without prerequisite,
allow with, unaffected for other tools, safe against a bare `ctx` with no
`tools_called`, and that its `DENY` wins `PolicyEngine.decide()`'s `max()` composition
over an `approve=` that would have said yes); real runs through both `Agent` and
`build_agent()` proving the gate blocks and un-blocks correctly; the same-batch case
proving a same-turn "consult + act" does not satisfy it. `examples/advisor_pattern.py`
runs both the blocked and the allowed scenario end to end.

---

### ADR-062 — Stall detection is mechanical and free; `STALLED` is its own stop reason; the check sits in the budget gate

**Status:** Accepted

**Context.** A long session fails in two very different ways. The first — out of money,
steps, or time — `Budget` has caught since Round 1. The second is more expensive: the
agent is still running, still calling tools, still billing, but repeating what it just
did. The budget ceiling does catch it, at the *last possible moment* and under a
`stop_reason` (`step_limit`) that describes the wrong thing. Nothing distinguished "did
300 steps of work" from "did the same step 300 times."

**Decision — read the signal the harness already has.** `progress.py` marks a step as
*no progress* when it made at least one tool call and **every** call in it repeats a
`tool+args` signature seen earlier in the run. `STALL_AFTER = 6` consecutive such steps
stops the run with `StopReason.STALLED`. No model is consulted, so this costs **zero
tokens** — which is what keeps it clear of ADR-023: paying for a second model call to ask
"am I stuck?" is exactly what that ADR refused, and this is not that.

**Precedent, so this is not a new species of mechanism.** `MAX_PAUSES` already stops a
model that keeps pausing ("stopping rather than paying for a loop"); `max_asks_per_run`
(S-25) already cuts on a mechanical count; the `tool+args` dedup (T-2.5) already computes
this exact signature, but only *within* one step. This is T-2.5 looked at across steps.

**Why one new signature resets the counter.** A real coding loop is
`write_source(file, new-body)` then `run_tests()`. `run_tests()` repeats identically every
lap — but `write_source` carries a different body, so the step contains something new and
the counter goes to zero. The counter only climbs when the agent has stopped changing the
world *and* stopped reading anything it has not already read. `tests/test_progress_stall.py`
runs twelve such laps and asserts the run completes, because a detector that kills honest
work is worse than no detector.

**Why `STALLED` and not `ERROR`.** The `MAX_PAUSES` precedent maps to `ERROR`, and that is
the weaker choice: an agent going in circles and an agent that crashed call for different
responses (rewrite the job or the tool set, versus fix the failure). A closed enum gaining
a member is additive — `ok` is still `COMPLETED`-only, and every existing branch on
`ERROR` keeps meaning what it meant.

**Why the graph backend checks in the budget gate, not where it observes.** `run_tools`
is where the calls are seen, but `budget_gate` is the only node that *clears*
`stop_reason` (it must, or a finished thread would route straight to `finish` forever —
Round 37). A stop set upstream of it is wiped before `_after_budget` reads it. So the tools
node records `seen_calls`/`stalled_steps` into state, and the gate reads them beside the
step and wall-clock ceilings — which is where a ceiling on a third axis belongs anyway.

**Two things kept out of state.** The counter and the seen-set are checkpointed
`AgentState` keys, not attributes on `Runtime` (IDL-47: a Runtime serves every thread, and
a counter on it mixes one conversation's progress into another's). And `seen_calls` holds
16-hex-char digests, never the arguments: a `write_file` call can carry an entire file, and
a checkpoint is not the place for a second copy of user content.

**Known limit, stated rather than discovered later.** `MAX_TRACKED = 512` bounds the
seen-set, so a signature that falls out of the window and returns counts as new. The
mechanism therefore errs toward *missing* a stall rather than toward killing a live run —
the correct direction for something that can end someone else's run.

**Four existing tests changed, and why that is not weakening them.**
`test_redteam.py::RT06`, `test_walkthrough.py::rt06`, `test_lg.py::the_budget_is_still_a_ceiling`
and `test_parity.py::the_budget_stops_both` each drove a runaway with one identical call
repeated, and each now trips the stall detector before the ceiling it means to prove. They
were changed to vary their arguments, so each still proves its own ceiling; the repeating
case moved to `tests/test_progress_stall.py`, where it is the subject rather than the
fixture.

**Test.** `tests/test_progress_stall.py` — the rule computed directly; a new signature
resetting the counter across twelve honest laps; tool-free steps not counted;
underscore-prefixed arguments not manufacturing fake progress (they are stripped before a
tool runs, so two calls differing only there are one call); the digest not containing the
argument text; `input` (Anthropic) and `args` (LangChain) producing one signature; the
stop on both backends, with the same `stop_reason` and the same sentence; and the counter
living in `AgentState`, not on the `Runtime`.

---

### ADR-063 — The decision log is an append-only JSONL journal, and the classic loop finally has one

**Status:** Accepted

**Context.** Two gaps with one root. `dispatch.py` said the first out loud in a comment:
*"the classic loop has no DecisionLog to record into … S-11's reported-actor channel has
nowhere to land"* — on the hand-written backend, `resolve()` computed who approved a
`danger` call and threw the answer away. The second: the graph backend does keep a book,
in RAM, on the `Runtime`. A process restart erases every approval in it — including one a
human pressed a button for thirty seconds earlier. `approve=INTERRUPT` is sold as a
durable wait that survives a restart; the *record* of what that wait produced did not.

**Decision 1 — persistence is an append-only JSONL journal, not a `Store`.** D-2 says this
book is append-only. A key-value `Store` forces read-modify-write of the entire list for
every new row, so one interrupted write loses the whole approval history — precisely what
D-2 exists to prevent. A file opened `O_APPEND` degrades to one bad line, not zero good
ones. `observe/transcript.py` already chose this shape for the same reason
([§05.2](05-data-and-state.md#2-transcript-format): "An audit log you can edit is not an
audit log"). `DecisionLog(journal=path)` loads on construction and appends one line per
`record()`, `flush` + `fsync` each time — a run writes a handful of rows, not one per
event, so the transcript's every-64-events compromise does not apply here.

**Decision 2 — sync, not async.** `record`/`lookup` are called from `_regate`, synchronous
code inside a LangGraph node, *and* from the classic loop's async dispatch path. An async
API would force `asyncio.run()` in the middle of a graph node. Synchronous file I/O is
what lets one implementation serve both backends — the alternative is two, which is how
the two backends drift (R-17).

**Decision 3 — the classic loop records every resolved ASK, including denials.** Same
shape the graph's `approval_gate` already used: one row per resolved ASK, scoped to that
`call_id`, carrying the actor the callback reported (S-11) instead of discarding it. The
ask-cap denial is recorded too — a log that only records what was permitted cannot answer
"what did we refuse, and why", the same rule that makes `policy.decided` fire for `ALLOW`
as well as `DENY` ([§05.1](05-data-and-state.md)). The loop also gains the graph's S-29
reuse: a live row answers without asking a person the same question twice, and a later
`DENY` row revokes it, because `lookup` composes with `max()` and needs no second rule.
An empty log returns `ASK`, so behaviour with no seeded grant is exactly what it was.

**Decision 4 — the default is a fresh in-memory log per run, and `Agent` keeps its shape.**
`Agent(decisions=...)` is optional; `None` builds one per run inside `RunEngine`. An
`Agent` is a frozen template shared across concurrent runs, so a book living on it would
accumulate every run's rows for the life of the process. An operator who wants the record
to outlive the run passes their own and owns its lifetime. `build_agent(decisions=...)`
now forwards to the parameter `Runtime` has accepted since S-29 but that nothing passed —
it existed and was unreachable.

**The privacy trade-off, stated rather than discovered.** `Scope.args` is written to the
journal as the **real argument values**, not a digest — it has to be, because
`Scope.matches` locks a grant to those exact values (that ternary is the design this
project took from Microsoft's `ToolApprovalRule` precisely because everyone else approves
the verb and ignores the object). That makes this file more sensitive than a transcript,
which digests arguments by default ([§05.1](05-data-and-state.md)). Mitigation: the
journal is created `0600`. It is not encryption, and the file should be treated as
credential-adjacent.

**A corrupt row raises; it is never skipped.** Skipping means continuing with an approval
book that is missing rows — possibly missing the `DENY` that just revoked something
(IDL-30). The error names the file and the line number.

**Test.** `tests/test_decision_journal.py` — JSON round-trip preserving every field;
`Verdict` written as a NAME, not an `IntEnum` number, so the file still parses if the
lattice is reordered; an unknown schema version refused rather than guessed; write-then-
reopen recovering the grant; append-only proved by asserting the new file content starts
with the old; revocation by appending a `DENY`; a corrupt line raising with `file:line`;
mode `0600`; the classic loop recording the reported actor, recording denials, reusing a
live grant without asking twice, not leaking another run's grant into this one, and still
falling through to the callback when the book is empty; `with_()` not dropping the field
(the N-7 class); and `build_agent(decisions=...)` reaching the `Runtime`.

---

### ADR-064 — `execute_once` gets its caller: the LangGraph backend only, and only for `write`/`danger`

**Status:** Accepted (supersedes ADR-043's "not wired yet", and corrects one line of T-6.1's spec)

**Context.** ADR-043 shipped `execute_once` as a standalone contract and refused to wire
it, on a specific ground: the key is `f"{run_id}:{call_id}"`, and nothing could supply a
`run_id` that spans two executions of the same call, so any wiring would be dead code. The
condition it named has since become false on one backend — and stayed true on the other.

**Decision 1 — wire the graph backend.** LangGraph writes a checkpoint *after* a node
completes, so a crash inside `run_tools` re-executes the entire batch on resume: a
`git_push` that already succeeded runs a second time. Both halves of the key survive that
restart — `run_id` is the `thread_id`, and `call_id` comes from an AIMessage checkpointed
*before* the tools node ran. That is a real, nameable bug with a real key, which is exactly
what ADR-043 said it was waiting for. `build_agent(idempotency_store=...)` (a `Store`;
`SqliteStore` for it to mean anything across a process) turns it on; without it nothing
changes and nothing extra is read or written.

**Decision 2 — do not wire the classic loop.** `run_id` is generated fresh in every
`atry_run()`, and `aresume()` goes through `atry_run()`. Reusing the old id is not a small
fix: [§05.2](05-data-and-state.md#2-transcript-format) guarantees `seq` is gap-free within
a run, and a resumed run restarts `seq` at 0. So the key could only ever catch a duplicate
`call_id` inside one run — which the T-2.5 dedup in `dispatch.py` already handles a step
earlier, for free. ADR-043's reasoning still holds there, and is left in force rather than
quietly overridden for symmetry.

**Decision 3 — `read`/`external` do NOT go through it, which contradicts T-6.1's spec on
purpose.** T-6.1 says read/external pass through `execute_once` with `fail_open=True`.
Replaying a recorded `read` after a restart returns the file *as it was before the crash*.
For a coding agent that is not a weaker guarantee, it is a wrong answer — and re-running a
read is precisely what its effect class already promises is safe. So `fail_open` has no
caller on the dispatch path; it stays part of the tested primitive. A spec line that turns
out to be wrong is better corrected in the open than implemented because it was written
down.

**A bug this wiring introduced, and the fix.** Routing the call through a coroutine broke
subagent tools: `_run_subagent` is synchronous and spins its own `asyncio.run`, which
raises inside a running loop (`tests/test_lg.py::test_a_subagent_tool_actually_runs` caught
it immediately). Rather than exempt subagents from the guard, the call hops onto a real OS
thread — **not** `asyncio.to_thread`, which always targets the current loop's own default
executor; `asyncio.run()`'s cleanup blocks on `shutdown_default_executor()`, which waits
for every thread ever submitted to that executor, including one an earlier attempt's
timeout gave up on but could not actually stop, so a `to_thread`-based version appeared to
time out correctly while silently keeping the NEXT retry's own `asyncio.run()` from
returning promptly (found separately, N-1's own subagent-timeout fix). `_SUBAGENT_EXECUTOR`
— a dedicated, module-level `ThreadPoolExecutor` no event loop owns — is what the call
actually runs on (`run_in_executor`), so a cancelled attempt's orphaned thread runs out its
course in the background without blocking anything after it. The cost is the same as
`asyncio.to_thread` would have been: nothing next to launching a whole child agent, and a
subagent tool whose effect is `danger` is exactly the kind that must not run twice.

**Test.** `tests/test_idempotency_wired.py` — the same thread and call id running the tools
node twice pushes once; the replayed result equals the first result exactly; no store means
unchanged behaviour (two pushes); a `read` is deliberately NOT replayed; two different
`call_id`s are two real pushes; two threads do not share a record; and the whole thing
proven across a genuinely new `SqliteStore` handle, which is the case the mechanism exists
for.

---

### ADR-065 — `CodeTools`: confinement needs a root, so it needs a constructor; and `read_file` stops being an exfiltration primitive

**Status:** Accepted

**Context.** `confine()` shipped with M7 (T-7.1) and was never called by the file tools
this library actually hands out. `CODING_AGENT_BLUEPRINT.md` said so in writing — "if you
use them as-is, there is no root confinement" — which documented the hole rather than
closing it. `read_file(path="../../.ssh/id_rsa")` is one `tool_use` block, on the tools
the beginner path exists to provide precisely because a beginner has not yet thought about
path confinement.

**Decision 1 — the coding tools are a class taking `root`, not module-level functions.**
A module-level `@tool` has no root to confine against; that is the whole reason these two
never called `confine()`. `CodeTools(root=...)` follows the shape `VikingStore.tools()`
and `TaskLedger.tools()` already established: an object holds the resource, `tools()`
returns tools with it closed over, effects already classified.

**Decision 2 — `read_file`/`write_file` confine to the process CWD.** They needed *a*
root and CWD is the only one a module-level tool has. It is a behaviour change (an
absolute path now raises `WorkspaceEscapeError` instead of working), taken on the same
ground as every other fail-closed default here: an unconfined, model-facing file tool is
the exfiltration primitive, and "documented as unsafe" is not a safety property. An agent
whose root is not the CWD uses `CodeTools`.

**Decision 3 — `run_tests` is `write`, which corrects this project's own blueprint.** That
document's table listed it as `read`. A test run writes caches and artifacts, and two runs
in parallel fight over them; `write` is the only class that states all three relevant facts
(not parallel-safe, never auto-retried, still auto-allowed outside `safety="strict"`).
Found by implementing it, and corrected in the open rather than kept consistent with a
sentence.

**Decision 4 — `edit_source` refuses an ambiguous match.** Whole-file rewrites cost tokens
proportional to the file and are the main source of "fixed one line, deleted three
functions." Exact-string replacement costs tokens proportional to the change — but only
when the string is unique. Two matches means the model does not know which site it is
editing, so the tool returns an error that says how to disambiguate rather than editing the
first one. Zero matches likewise says to re-read the file rather than guessing.

**Decision 5 — `outline` and `search_code` exist because reading whole files is the real
cost.** A `read_source`-only agent reads an entire file to find one function, pays for all
of it, and fills the window with what it did not need. `outline` (Python, via `ast`)
returns the class/def map with line numbers; `search_code` returns `file:line` hits. For a
non-Python file `outline` says so plainly instead of guessing — there is one parser here,
and pretending otherwise is worse than declining.

**Decision 6 — nothing in this module is `danger` or `external`.** `git_push`, `deploy`,
and anything that reaches the network are the agent author's to declare, with the
lethal-trifecta rule and human approval that come with those classes. Importing this module
can therefore never, by itself, produce the construction-time refusal.

**Environment: an allowlist, not `os.environ`.** `Sandbox.run` uses `env` verbatim (T-7.4),
so `PASS_ENV` is the complete list of what a child command sees: `PATH`, `HOME`, `LANG`,
`LC_ALL`, `TZ`. No provider key, no CI token. `HOME` is in it because `git commit` reads
`~/.gitconfig` — leaving it out is the "Author identity unknown" failure
`examples/coding_agent.py` already hit once.

**Test.** `tests/test_tools_code.py` (39) — `../`, absolute paths and a write outside the
root all refused, with the escaping write proven not to have created the file; the two
builtin tools refused the same way; `outline` shorter than the file it maps, honest about
non-Python, and naming the line on a syntax error; `search_code` returning `file:line` and
reporting a bad regex; `.git`/`__pycache__` skipped; a binary file not poured into context;
an ambiguous edit refused with the file byte-identical afterwards; the effect table
asserted tool by tool with `run_tests` pinned to `write`; the env allowlist proven not to
carry a planted secret; and — not mocked — a real `git commit`, a real passing and a real
failing `pytest` run, plus `; touch canary` passed as a `target` proving argv is argv and
not a shell string.

---

### ADR-066 — Compaction drops whole steps; it does not summarize, and it is driven by the ratio, not by editing running dry

**Status:** Accepted

**Context.** `manage()` had returned `"compact_needed"` since T-2.6 and no caller did
anything with it but emit an event. A long run therefore edited (blanking old tool-result
content) until there was nothing left to blank, then walked into the provider's context
limit and had the request rejected — a failure at exactly the point this feature exists to
prevent.

**Decision 1 — drop the oldest whole steps; do not summarize.** Summarizing costs a model
call out of the same budget the caller set as a ceiling, for a gain nobody here has
measured: the trade ADR-023 refused. Worse, a summary is model output derived from tool
results that may be UNTRUSTED, so it would have to carry the `join` of every label it
summarizes or compaction becomes a perfect taint-laundering path (the S-19 class). That is
a design, not a helper. What dropping actually loses is the model's own earlier reasoning
and the record of tools it already called — survivable precisely because
`harness.tasks.TaskLedger` (ADR-061) keeps the plan in a `Store` rather than in the
transcript: one cheap `list_tasks` rebuilds it.

**Decision 2 — a step is dropped whole, and `messages[0]` never is.** An assistant turn
and the user message carrying its `tool_result` blocks go together or not at all; half a
pair is the I-3 violation the editing path exists to avoid. The first user message is the
task, and an agent that forgets the task is worse than one with a short memory.

**Decision 3 — no "[n steps were dropped]" marker.** It would need a role. A second
consecutive `user` message right after the task is a shape not every provider accepts, and
folding the note into the task itself would invalidate the cached prefix — the single
largest cost lever in the system ([§02.1](02-architecture.md)). The drop is recorded in
`context.managed` (`messages_dropped`), which is where a person looks anyway.

**Decision 4 — compaction is driven by the RATIO, and the first version got this wrong.**
It only ran when editing found nothing left to blank. But every step makes exactly one more
tool result stale, so editing ALWAYS has something to clear — compaction would never have
run at all, while the window kept growing, because a blanked result still costs its
envelope and the assistant turns holding the `tool_use` blocks are never blanked. Past
`COMPACT_AT` (80%), blanking one more old result is not a plan. `tests/test_context_
compaction.py::test_nen_KHONG_cho_toi_khi_het_cho_xoa` pins the corrected order.

**A measurement bug this surfaced on the graph backend.** `_manage` sized the context as
`sum(len(str(m.content)))`. On LangChain an `AIMessage` carrying only tool calls has
`content == ""` — the arguments live in `.tool_calls`. So the estimate missed exactly the
part that is never blanked: an agent calling `edit_source(path, old, new)` forty times
measured as roughly zero characters, and context management never ran at all on that
backend. The classic loop was never affected because it measures with `canonical(message)`,
which includes the whole `tool_use` block. Fixed in `_context_chars`.

**The graph backend does not delete; it leaves a labelled tombstone.** Its effective label
is recomputed from the messages still in context (L-3), so removing an UNTRUSTED
`ToolMessage` from state lowers the whole run's label — taint laundering performed by the
operation that calls itself cleanup. Compaction there emits `RemoveMessage` for each
dropped message plus ONE empty `ToolMessage` stamped with the `join` of every label it
removed. Verified end to end: after a forty-step run of an `external` tool, compaction has
cut 82 messages to 12 and the effective label is still UNTRUSTED.

**When nothing can be cleared and nothing can be dropped, the run stops with a sentence.**
Not a new `StopReason` — this is a run that cannot continue, which is what `ERROR` means,
the same call `MAX_PAUSES` makes. `Result.detail` names the cause and the two things a
person can do about it (smaller job, bigger window), instead of letting the provider reject
the next request for a reason the caller has to guess at (IDL-30).

**Test.** `tests/test_context_compaction.py` (16) — the unit rules (task kept, recent steps
kept, no orphaned `tool_result`, a short conversation untouched); the full ladder
`none → edited → compacted → compact_needed`; the corrected ordering pinned; a forty-step
classic run completing with a bounded message count and both strategies observed; the
stop-with-a-sentence path; `_context_chars` counting a tool call's arguments; and on the
graph — compaction running, the task preserved, exactly one tombstone, and the run still
UNTRUSTED afterwards.

---

### ADR-067 — `tools_called_ever`: `RequireBeforePolicy` survives real compaction on the durable backend

**Status:** Accepted (integrity audit of a large concurrent merge that landed ADR-061's
advisor gate and real compaction — `context/window.py`'s `compact()`, ported to the
durable backend as `Runtime._compact()` — in the same window, written by two sessions
neither aware of the other).

**Context.** `Runtime._tools_called()` (ADR-061) answered "which tools already
completed" by scanning `state["messages"]` on every call: an `AIMessage.tool_calls`
entry counted if a matching `ToolMessage` existed. That was correct on its own, and
`Dispatcher.ran` (classic backend) was correct on its own — but real compaction
(`Runtime._compact`, run once the context window crosses `COMPACT_AT`) drops whole
`AIMessage`/`ToolMessage` step pairs older than `_KEEP_RECENT_MESSAGES`, replacing them
with a single taint-preserving tombstone that carries no tool names (S-19: it can't
carry names AND drop content, or the label it protects would itself be reconstructable
from what was supposedly cleared). A `consult_advisor` call made early in a long-running
thread ages out exactly like any other step. Once it does, `_tools_called()`'s
message-scan stops finding it, and `RequireBeforePolicy` (`policy/builtin.py`) DENIES a
`wipe` attempt it had genuinely already cleared — not a security hole (the failure is
fail-closed, over-restrictive, never over-permissive), but a real correctness gap in
exactly the long-conversation case the advisor pattern exists for.

Confirmed by direct repro before writing a fix, not inferred from reading the two
features' code side by side: a synthetic `state["messages"]` with an early
`consult_advisor` step and 400 filler steps, run through the real `Runtime._manage()`/
`_compact()`, showed `_tools_called()` reporting `consult_advisor` before compaction and
not after. The classic backend was checked the same way and found immune:
`Dispatcher.ran` is a plain list appended to for the life of one `Dispatcher`
(one per `try_run()` call) and is never touched by `_manage_context()`, which only ever
edits/drops the separate `msgs` list sent to the provider.

**Decision.** Add `state["tools_called_ever"]` (`lg/state.py`): a list `_run_tools`
appends to — one name per call that gets ANY `ToolMessage` this step (declined, tool
gone, errored, or succeeded — the same "completed" criterion `_tools_called()` already
used), never replaced, never pruned by compaction (`RemoveMessage` targets `messages`,
not this key). `_tools_called()` now returns the UNION of the message scan and this
field, rather than switching to the field alone: the scan stays a harmless, redundant
safety net so a checkpoint written before this field existed still answers correctly for
whatever hasn't been compacted away yet, and the field is what makes the answer correct
once it has been.

**Rejected alternative.** Special-case compaction to never drop a step containing a call
`RequireBeforePolicy` might later need. Rejected: `_compact()` has no way to know which
future policy might ask about which past tool without importing policy configuration
into the context-management layer — a much larger coupling for the same result
`tools_called_ever` gets by simply not forgetting in the first place.

**Tests.** `tests/test_advisor_gate.py::DurableGateSurvivesCompaction` (3): a real
`build_agent()` run long enough to trigger genuine compaction (large tool-call
*arguments*, per `test_context_compaction.py`'s own technique — arguments are never
edited/cleared, so they inflate the ratio in far fewer steps than growing it through
tool *results* would) with `consult_advisor` early and `wipe` after — asserts the run
actually compacted, and that `wipe` is NOT denied; a companion asserts the gate still
DENIES `wipe` correctly when `consult_advisor` was never called, so the fix could not
have simply disabled the gate. `MutationTuyChonEverFieldCoTacDung` reverts
`_tools_called()` to the message-scan-only version and confirms the first test then
fails — proof the new coverage depends on the patch, not on some other mechanism
accidentally masking the bug.


---

### ADR-072 — `refresh_codebase_docs`: OpenWiki "code mode" as an explicit `CodeTools` tool, never an automatic step

**Status:** Accepted

**Context.** Evaluated two external candidates for helping a coding agent (or its human)
keep architecture documentation from going stale: `volcengine/OpenViking` (AGPLv3, a
context-database/agent-memory framework with its own competing agent runtime — rejected;
its license and its "VikingBot" framework both conflict with ADR-001's own-agent-loop
stance, and the user explicitly ruled it out) and `langchain-ai/openwiki` (MIT, a CLI that
only reads a repo and writes a wiki, never edits source). OpenWiki ships two modes: personal
mode ingests Notion/Gmail/local-git/etc. into a per-machine, non-git-tracked wiki with
explicitly no evidence verification for connector-derived facts; code mode reads the
current repo only, writes a git-trackable `openwiki/` directory, and backs every claim with
a `repo://path#Lx-Ly` citation that gets reconciled against the code on `--update`. The user
asked to use code mode "như một thành phần mặc định khi harness hoạt động như một coding
agent" (as a default component when the harness acts as a coding agent), then, given a
choice between passive documentation, an explicit tool, or fully-automatic invocation,
chose explicit: "cách tường minh như các tool khác" (the explicit way, like the other
tools).

**Decision.** Add `refresh_codebase_docs` to `CodeTools.tools()`, `effect="write"`
(undoable via `git reset`/`git diff`, same class as `write_source`/`git_commit`/
`run_tests` — not `danger`; it never pushes or touches anything outside `root`). It runs
`openwiki --init` when `root/openwiki/` does not yet exist, `openwiki --update` otherwise,
through the existing `me._run()` helper — same confinement, same `Sandbox`, same `PASS_ENV`
allowlist as every other tool in the module, no new mechanism. `openwiki` is **not** added
to `pyproject.toml`: it runs as an external process, the same arrangement ADR-035 already
uses for `viking`'s optional extra. A machine without it installed gets `Subprocess`'s
existing `FileNotFoundError` → `exit 127` handling (IDL-30, fail visible) — not a new
special case.

Explicit, not automatic, because automatic means paying for it on every run whether or not
anyone needs the wiki refreshed: OpenWiki's own `--update` pass is itself a model call
(tokens, an API key, wall-clock time), and no other seam in this harness runs anything
without something — model or human — choosing to call it. An agent author who wants it to
run every session can still say so, the same way they already opt into `run_tests` or
`git_commit` running unattended: by having the model call it, or by building an agent whose
job description tells it to.

**Test.** `tests/test_tools_code.py::CapNhatWikiBangOpenwiki` — five tests: the `--init`
vs `--update` heuristic (verified against a fake `Sandbox` that records the exact argv,
regression-checked by reverting the heuristic to a hardcoded `--init` and confirming the
`--update` test goes red), `effect` is `Effect.WRITE`, real (non-mocked) fail-visible
behavior on a machine without `openwiki` installed (`exit 127`, matching the module's
`ChayLenhThat` class's own "don't mock, let the real environment error show" discipline),
and that constructing `CodeTools`/calling `.tools()` never invokes the sandbox at all —
the tool only runs when something calls `.fn()` directly.

### ADR-073 — `Agent.with_profile()`: a `Profile` is sugar over `with_()`, held to the same only-tightens rule as `Policy`/subagent safety

**Status:** Accepted

**Context.** A user building a coding agent on top of this library asked for a way to
package "system prompt + tools + model choice + a feedback loop" as one reusable,
checked-in unit, explicitly rejecting a separate constructor
(`build_coding_agent(CodingProfile(...))`, `examples/coding_profile.py`'s first shape)
in favor of the same `Agent(...)` API everything else uses — either `Agent(...,
profile=)` or `Agent(...).with_profile(...)`.

`Agent(...)`'s real (non-sentinel) defaults for `model`/`effort`/`safety`
(`agent.py:87-91`) rule out the constructor-parameter shape without a second core
change: `__init__` cannot tell "the caller wrote `model="claude-opus-5"` on purpose"
from "the caller wrote nothing", so a profile could not know whether it may fill the
field in. `job=` would also gain two meanings depending on whether `profile=` was
present — the whole prompt (today) vs. an input to a template (with a profile) — the
same "two things decide one value" shape already flagged at R-17/R-20.

The method shape has neither problem, and it is nearly free: `with_()` (`agent.py:575`)
already reconstructs a full `Agent(**base)` from its overrides, so every construction-
time check (the cache-determinism linter, the lethal-trifecta refusal, `ToolSet`'s
duplicate-name guard) already reruns on whatever a profile adds. The one thing nothing
already checked: a profile silently WIDENING a safety knob the `Agent(...)` call had
already narrowed — `accepts_tainted`, `safety`, `allowed_hosts`, the approval gate. Left
unchecked, that reopens exactly the shortcoming `design/README.md`'s own table #7 says
this project fixed once already ("the model holds its own safety switch") — moved from
the model into a profile instead of removed.

**Decision.** `Agent.with_profile(profile)` (`agent.py`) calls `profile.apply(self)`
(the `Profile` Protocol, `profile.py` — `name: str` + `apply(agent) -> Agent`, the same
minimal shape as `Policy`/`Sandbox`) and then runs `_refuse_if_loosened(before, after,
profile.name)` before handing the result back. That function is `_check_subagent_safety`
(`agent.py:858`, "a subagent may only ever be MORE restricted than its parent, never
less") applied to a profile instead of a child agent: it compares `before`/`after` on
`safety` (via the existing `_SAFETY_RANK`), `accepts_tainted` (`_grants`, gained entries
only), `allowed_hosts` (widened, or a list replaced by unrestricted `None`),
`require_approval_evidence` (`True -> False`), `max_asks_per_run` (the S-25
approval-fatigue cap, raised), `approve` (a real callback replaced by `None`), and
`policies` (any dropped from the list — the most direct loosening vector, since a
`Policy` result composes by `max()` (P-2) only over whatever remains in that list). Any
violation raises `ProfileLoosenedSafetyError` naming every culprit, never just the
first.

Deliberately NOT a check on swapping `approve=` for a different callback, or on which
policies get ADDED — those are not mechanically decidable as "less safe"; only the
mechanical cases (a value dropped, widened, or a gate removed entirely) are checked,
the same restraint `_check_tool_set` already applies to the lethal-trifecta check it
sits next to.

`docs/02-architecture.md §4`'s six-seam table does not grow a row for this.
`harness.middleware` already established the precedent for something built entirely
from existing seams that still deserves a name and a rule: its own docstring states
"every hook runs strictly after the core decision it follows... can only add
restriction or observation, never bypass one." `Profile` is exactly that shape, with
`_refuse_if_loosened` playing the enforcement role that ordering plays for
`Middleware`. Because the three-part plugin test's clause (c) — "two genuinely
different implementations today" — is nonetheless real evidence about whether an
abstraction is worth its keep, `examples/research_profile.py` (search/fetch,
citation-and-skepticism prompt, no subagent, no write tools, `external`-shaped taint
instead of `write`/`danger`) ships alongside `examples/coding_profile.py`
(file/git tools, a verification-wrapped edit tool, a reader subagent,
a path-based `Policy`) specifically so that claim is checked rather than asserted.

No new "already applied" bookkeeping exists for reapplying the same profile twice: a
profile that adds tools under names it used before hits `ToolSet`'s existing
duplicate-name guard (`DuplicateToolError`) on the second call, for free — the
mechanism already existed and already fires; adding a second one would be exactly the
"the same rule enforced two ways can drift" shape R-17 warns about.

**Superseded in part by ADR-074.** This paragraph covered reapplying the SAME profile;
composing two DIFFERENT profiles was untested, and turned out to be real and dangerous
(a garbled prompt plus an unreviewed `external`+`write` tool union). ADR-074 adds the
bookkeeping this paragraph argued against — read it for why the argument here turned
out to be correct only for the narrower case it actually tested.

**Test.** `tests/test_profile.py` — one test per knob `_refuse_if_loosened` reads
(downgrading `safety`, expanding `accepts_tainted`, widening or removing
`allowed_hosts`, turning off `require_approval_evidence`, raising `max_asks_per_run`,
replacing `approve` with `None`, dropping a `policies` entry), each changing ONLY that
knob so a passing test proves the check reads that specific field and not some other
one that happened to also differ (the R-16 lesson: a control that is specified and
never executed is not a control). Plus: `with_profile()` returns a new `Agent` and
extends its toolset; a legitimate tightening (adding a policy, raising `safety`) is
allowed; reapplying a tool-adding profile hits `DuplicateToolError`, not a bespoke
error.

---

### ADR-074 — `Agent.with_profile()` refuses a second profile by default; `_profiles` bookkeeping added

**Status:** Accepted. Supersedes ADR-073's "no new bookkeeping" argument for the case
it did not actually test.

**Context.** A systems-engineer-style review of the `Profile` mechanism (asked for
explicitly, after ADR-073 shipped) tested a case none of that ADR's own tests covered:
composing two DIFFERENT profiles on one `Agent`, rather than reapplying the SAME one.

```python
a = Agent(name="X", job="do the thing")
a2 = a.with_profile(CodingProfile(root=root, tasks_db=...))
a3 = a2.with_profile(ResearchProfile())      # constructed. No error. No warning.
```

Two things were wrong with the result, both measured directly. First, the prompt: each
profile's `apply()` rebuilds the WHOLE system prompt from its own template around
`agent.job` (`CodingProfile`'s and `ResearchProfile`'s own `_system_prompt`/`_SYSTEM`),
so the second profile's template wins, but with fragments of the first still present —
`a3.job` read `"You are X, a research assistant... # Your task for this session / You
are X, working in a single repository checkout at ... # Your task for this session / do
the thing"`, two contradictory identity claims and a duplicated section header. Second,
and worse: the resulting toolset unioned `search`/`fetch` (`ResearchProfile`,
`effect="external"` — an untrusted-content source) with `write_source`/`edit_source`/
`git_commit` (`CodingProfile`, `effect="write"` — a code-mutation sink). That is exactly
the "reads the untrusted world, writes the codebase" combination
`CODING_AGENT_BLUEPRINT.md §3` and `CodingProfile`'s own `ask_reader` subagent
(`§06.4`, least privilege) exist to keep on two SEPARATE agents.

`_check_tool_set`'s lethal-trifecta refusal (`agent.py`) does not catch this: it is
scoped to `external`+`danger`, not `external`+`write` — `write` is treated as reversible
throughout this library (`git reset` undoes a bad `write_source`/`git_commit`,
`design/02-safety-engine.md §4.1`), and that is core's own settled scope, unchanged by
anything below. `Agent(tools=[search, write_source])` raised nothing before this ADR
either, and still doesn't — constructing that combination directly has always been
possible. What composing two profiles changed is not the underlying rule; it made
hitting that combination BY ACCIDENT trivial and invisible: `.with_profile(a)
.with_profile(b)` reads as safe composition, the same way `with_middleware(agent, x, y)`
composing two middlewares is safe by construction (§3.6's own "can only add restriction
or observation" guarantee) — except `Profile` never had that guarantee for the
tool-union case, only for the single-agent safety-knob case ADR-073 checked.

**Decision.** `Agent` gains `_profiles: tuple[str, ...]` (`__slots__`/`__init__`/
`with_()` — a new, small, ordinary field, not a workaround built on an existing
mechanism the way ADR-073 chose). `with_profile()` checks it first, before calling
`profile.apply()` at all: if any profile has already been applied and the caller did
not pass `allow_multiple=True`, it raises `ConfigError` naming the already-applied
profile and the exact fix. `allow_multiple=True` is the explicit, visible opt-in this
library asks for everywhere else a real but narrower-than-`danger` risk exists
(`accepts_tainted=`, `allowed_hosts=None` — ADR-032/T-7.2) — it waives ONLY the
one-profile rule; `_refuse_if_loosened` (safety knobs) and `ToolSet`'s duplicate-name
guard (same-profile reapplication) both still run underneath it, unchanged.

Not fixed by widening `_check_tool_set` to cover `external`+`write`: that is a change to
what core considers safe by default, affecting every caller of `Agent(...)` directly —
not something a profile-layer finding should decide unilaterally, and the library's own
`write`-is-reversible reasoning is a deliberate, cited position, not an oversight this
finding contradicts. The fix is scoped to what profiles specifically made worse: the
ACCIDENT rate of hitting a combination that was always constructible on purpose.

**Test.** `tests/test_profile.py::WithProfileRefusesASecondOne` — a second, different
profile refused by default; the same profile reapplied also refused by default (not
just left to `DuplicateToolError`, which now never gets a chance to fire until
`allow_multiple=True` is passed); the specific `external`+`write` union reproduced
directly with the guard bypassed, confirming the guard is what stood between a caller
and it, not some other mechanism; `allow_multiple=True` permitting composition and
recording both names in `_profiles`; and that `allow_multiple=True` still hits both
`DuplicateToolError` (same profile) and `ProfileLoosenedSafetyError` (a knob loosened)
— the escape hatch waives one rule, not all three.

---

### ADR-075 — `ShellCommandPolicy` gains `mode="allowlist"`; denylist mode's threat model stated honestly

**Status:** Accepted.

**Context.** The same review asked whether `ShellCommandPolicy` (`examples/
shell_tools.py`, ADR unlogged at the time — it shipped as part of the shell/findings
work without its own decision-log entry) was actually hard to bypass. It tested six
deliberate evasions against the default denylist patterns:

```
ALLOW  rm ${IFS}-rf${IFS}/tmp/x              ($IFS instead of a literal space)
ALLOW  r""m -rf /tmp/x                       (an empty quote inside the command name)
ALLOW  a=rm; $a -rf /tmp/x                   (indirection through a shell variable)
DENY   rm -rf /tmp/x #                       (the plain form — correctly caught)
ALLOW  echo <base64 of "rm -rf /tmp/x"> | base64 -d | sh
ALLOW  python3 -c "shutil.rmtree('/tmp/x')"  (deletion via a scripting API, not `rm`)
```

Five of six returned ALLOW at the time this was measured. (One of the five — the empty-
quote case — was closed as a side effect of the SAME follow-up commit's unrelated fix to
`_split_clauses`: real POSIX quote removal normalizes `r""m` to `rm` the way an actual
shell would parse it, so the denylist's own `\brm\b` check started seeing it. Four of
six stand; `tests/test_shell_tools.py::DenylistModeEvasionsThat` carries the current,
accurate count, not this paragraph — read the test if the two ever disagree.) This is
not a fixable regex gap either way — the space of ways to express "delete this" is not
enumerable, and a pattern added to catch any one of the remaining four does not
generalize to a fifth. `ShellCommandPolicy` runs on `run_command`/
`run_shell`, `effect="write"` (auto-ALLOW under `safety="standard"` — the whole
mechanism `shell_tools.py`'s own module docstring explains), so a denylist gap here
means the agent ran the command with no human in the loop at all.

**Decision.** State the real guarantee rather than imply a stronger one: denylist mode
(unchanged default, backward-compatible) is documented as a net for a CONFUSED model,
not a boundary against an adversarial or prompt-injected one — an operator combining
`enable_shell=True` with any untrusted-content tool (`effect="external"`, an untrusted
MCP server) is told to pair it with a real sandbox (`shell_sandbox=`), not trust this
policy alone as isolation.

Add `mode="allowlist"` as the fundamentally stronger alternative a denylist cannot be
turned into by patching: `deny` still checked first (a named prohibition should win
even inside an allowlist), then every `;`/`&&`/`||`/`|`-separated CLAUSE of the command
must `re.fullmatch` something in `allow` or the call falls to ASK — never silently
DENY, since an unrecognized command is unvetted, not proven malicious. Matching per
clause with `fullmatch`, not `search` over the whole string, is load-bearing:
`pytest -q && rm -rf /` contains the substring `pytest -q`, and a naive "does the
command contain something safe-looking" check would let the whole line through on
that strength alone. `DEFAULT_ALLOW` ships common read/build/test invocations across a
handful of ecosystems (pytest/ruff/mypy, npm/yarn/pnpm, cargo, go, make) — narrow on
purpose, extend rather than replace, the same convention `DEFAULT_DENY`/`DEFAULT_ASK`
already use.

**Test.** `tests/test_shell_tools.py::DenylistModeEvasionsThat` pins the measured gap
down as a known, tested property of denylist mode (so a future change cannot silently
imply a stronger guarantee than the mode actually gives) — plus its own separate test
for the quote-splitting evasion that stopped being one, so the fix reads as understood
rather than an unexplained change to the count. `AllowlistModeThat` proves the
allowlist fix against the same evasions (falling to ASK where they match no
`DEFAULT_ALLOW` pattern, or DENY where `_split_clauses`'s quote normalization now
surfaces them to the denylist check first), the clause-smuggling case, denylist-still-
wins composition inside allowlist mode, and both call shapes (`argv`/`cmd`) checked
identically. `_split_clauses` (quote-aware, `shlex`-based, replacing a plain
`re.split` on `;`/`&`/`|` that did not know about quoting) is its own fix, covered
separately — see the commit introducing it for the correctness case it closes in
`_check_allowlist` (a quoted `;` inside a benign command must not fragment it into
clauses that spuriously fail to match `allow`).

---

## Implementation Decision Log

| # | Decision | Rationale |
|---|---|---|
| IDL-01 | `Decimal` for all money; `float` banned in `budget/` by lint | A rounding error in a spend ceiling is a real bug class |
| IDL-02 | `Verdict` is an `IntEnum` | Makes `max()` the composition operator for free, which is what enforces restrict-only |
| IDL-03 | ULID for `run_id` | Time-sortable, no coordination, no collisions |
| IDL-04 | Canonical JSON everywhere (`sort_keys=True`, fixed separators) | Byte-stability is a cache correctness property, not a style preference |
| IDL-05 | `@value` on every data class (not bare `@dataclass(frozen=True, slots=True)`) | Immutability + lower memory + a readable `AttributeError`. **The bare form does not deliver the third**: a non-field assignment raises a `super()` TypeError (ADR-027) |
| IDL-06 | Sync tools wrapped at decoration, not at call | One branch at import instead of one per invocation |
| IDL-07 | Provider SDK imported lazily | NFR-01: keeps `import harness` under 200 ms |
| IDL-08 | `no_network()` is an autouse fixture | A contributor cannot accidentally bill themselves (register #34) |
| IDL-09 | Tool arguments stored as a digest by default | Arguments routinely carry PII; a transcript that captures them by default is a leak generator |
| IDL-10 | Exporter exceptions disable that exporter for the run | Telemetry must never cause an outage |
| IDL-11 | `run()` raises, `try_run()` returns | Serves beginners (loud) and production (explicit) without an options flag |
| IDL-12 | `RunFailed.partial` carries the `Result` | Raising must not destroy accumulated work |
| IDL-13 | 250-line ceiling on `run.py`, treated as a design signal. **Fired in Round 28**: subagent binding crossed it, and the response was to split tool execution into `dispatch.py` rather than raise the limit — run.py 137, dispatch.py 163 | The loop staying boring is the property that keeps it auditable |
| IDL-14 | `EFFECT_PROFILES` is a module constant, not configuration | A user who could edit it could disable the taint rule |
| IDL-15 | `RunContext` excludes message history | The transcript is the largest available exfiltration surface |
| IDL-16 | SQLite `STRICT` tables + WAL + busy timeout | Type affinity silently accepts wrong types; WAL avoids `database is locked` |
| ~~IDL-17~~ | ~~Cache linter waits 150 ms between renders~~ | **Superseded by ADR-025.** Measured at 150 ms per construction, and it caught neither `datetime.now()` to seconds nor `date.today()` — the two most likely cases. Expensive and ineffective. |
| IDL-18 | Breakpoints omitted below the minimum cacheable prefix | Below it, a marker pays the write premium and never reads |
| IDL-19 | Server-side refusal fallbacks enabled by default | A routine refusal should route to a fallback, not surface as a dead end |
| IDL-20 | Parallel results reassembled in the model's call order | Order-dependent behavior in a model's reading of results is real; determinism is cheap |
| IDL-21 | `Agent.__init__` takes `*args` **and gives `name`/`job` sentinel defaults** solely to reject them | Keeps keyword-only enforcement while replacing Python's `TypeError`. The sentinels are load-bearing: Python validates required keyword-only parameters **before** the body runs, so without them this check never executes (Round 24) |
| IDL-22 | Unannotated tool parameters are an error, never defaulted to `str` | `def add(a, b)` would receive `"3"`/`"4"` and return `"34"` — a silently wrong answer a learner cannot search for |
| IDL-23 | `effect=` misspellings get a did-you-mean via edit distance | A typo in a four-word vocabulary is the single likeliest mistake with it |
| IDL-24 | Progress output goes to `stderr`, not `stdout` | Keeps `python agent.py > out.txt` clean even on a TTY |
| IDL-25 | `harness setup` validates the key with a minimal call before storing | Converts a silent later failure into an immediate, obvious one |
| IDL-26 | The scaffold includes `budget=` rather than introducing it later | Showing the guard costs one commented line; explaining it after a surprise bill costs trust |
| IDL-27 | `max_tokens` is derived, never exposed | ADR-017. A parameter that cannot be set cannot contradict the budget |
| IDL-28 | A derived `max_tokens` under 256 stops the run instead of calling | A 200-token ceiling produces a sentence fragment, which costs money and answers nothing |
| IDL-29 | Numeric defaults are cross-validated by a test that multiplies them out | The Round 17 defect lived between two correct components, not inside either |
| IDL-35 | `Verdict` lives in `policy/base.py`; `policy` imports `ToolSpec` only under `TYPE_CHECKING` | `EFFECT_PROFILES` needs `Verdict` and `Policy` needs `ToolSpec` — a cycle the module map showed without noting |
| IDL-36 | `size_call` returns `model_max` when the output price is zero | A free provider has nothing to divide by. Without it, SC-5's zero-cost testing crashes on ADR-017's division |
| IDL-38 | Duplicate calls within a step run once, but each `tool_use` still gets its own `tool_result` | Invariant I-3 does not bend for an optimization; a missing result corrupts the conversation |
| IDL-39 | The parallelism semaphore is created in `RunEngine.__init__` from `agent.max_parallel_tools` | The parameter existed and was stored for two milestones without anything reading it. Peak concurrency measured at 30 against a limit of 4 |
| IDL-37 | Calibration ratchets upward only | An input under-count overspends; an over-count only wastes headroom. The two errors are not symmetric |
| IDL-30 | An unrecognized provider `stop_reason` maps to `ERROR` with the raw value | Fail visible. Mapping an unknown outcome to success is how truncated answers ship as correct ones |
| IDL-32 | `Secret.__hash__ = None` | Equal-by-value secrets hashing by name violates Python's hash invariant and silently corrupts sets and dicts. Unhashable also prevents a credential becoming an `lru_cache` key, which the redactor cannot reach |
| IDL-33 | The redaction registry holds weak references keyed by `id()` | A strong registry retains every secret ever constructed until process exit. **Not a `WeakSet`** — that hashes its members, and `Secret` is deliberately unhashable (ADR-024) |
| IDL-34 | Type contracts are reviewed by executing twenty lines, not by reading the signature | Rounds 17, 18 and 19 each found a defect this way; no earlier round found one by inspection |
| IDL-40 | Graph state stores a tool **name**, never a `ToolSpec` | Everything in state is checkpointed, and a spec holds a callable no serializer can write. Durability is what this platform is for, so it failed at exactly its own feature (H35.5) |
| IDL-41 | Every key a node returns must be declared in `AgentState` | LangGraph **silently discards** an undeclared key. The first port lost `_pending`, so no tool ran at all — and the test asserting a denied tool had not run passed for that reason (H35.6) |
| IDL-42 | `Decimal` crosses graph state as a string, never a float | State is JSON-checkpointed; a float here would reintroduce the rounding class IDL-01 exists to exclude |
| IDL-43 | The graph delegates ASK resolution to `PolicyEngine.resolve` | One implementation of the rule. The port had written a second one, with a different callback signature and a different no-callback behaviour (H35.4) |
| IDL-44 | A store key is validated against a strict pattern, never escaped | A key becomes part of a `viking://` URI; `../../resources` reads another namespace. Escaping is a thing you can get subtly wrong, refusing is not |
| IDL-45 | The store's client capabilities are a fixed set, enforced on every call | `SyncHTTPClient` exposes `admin_*`, `rm` and `delete_session`. A model-facing tool holding those is a prompt injection with administrative reach |
| IDL-46 | Integration tests drive the vendor SDK over a stub transport, never a hand-written fake of it | The first fixtures invented an envelope the server never sends, and every parse test was green against it (H36.2) |
| IDL-47 | No run state lives on the `Runtime`; the ledger round-trips through graph state | A Runtime is per-graph and serves every conversation. Holding a ledger billed one customer for another's tokens (ADR-036) |
| IDL-48 | Graph tests must invoke more than once | All three Round 37 defects were invisible to a suite where every scenario called `invoke()` exactly one time |
| IDL-49 | `Result.tools_run` records what EXECUTED, written where execution is decided | The test helpers read `tool_use` blocks — the model's requests — so a tool policy blocked counted as called, and `assert_no_tool` failed on the exact case it exists to prove (H38.4) |
| IDL-50 | A provider payload is asserted offline against the vendor's current documented parameter shapes | This provider has never run against the live API, so "it looks right" was the only check it had. Three of its claims were wrong or absent |
| IDL-51 | Ruff is configured to the package's own style, not the default | 62 of 162 findings were one-line accessors written that way on purpose. Rewriting working lines to satisfy a default is churn; the config records the choice instead |
| IDL-52 | An automatic fix is a change, and the suite runs immediately after | `ruff --fix` removed a re-export and broke `import harness` (H39.4) |
| IDL-31 | Context-management fixtures are specified per model | Whether the budget or the context window binds first depends on the model's price and window ([§07.3](07-cost.md#3-token-discipline)) |
| IDL-53 | `budget.unlimited` fires once, at the same `step == 0` site as `RUN_STARTED`, never inside `Ledger` itself | `Ledger` has no `EventBus` access by design (a ledger that emits telemetry is a ledger with a second reason to change); the guard lives with the caller that already fires exactly once per run/thread (ADR-041) |
| IDL-54 | An agent's plan is durable state in a `Store`, never a key in graph state and never a second model call | ADR-023 rejected spending tokens to think about thinking; it never said the plan should be forgotten at step 40. A `Store` outlives the process, works on both backends, and adds no constructor parameter (ADR-061) |
| IDL-55 | A run-level ceiling is checked in the node that clears `stop_reason`, never in the node that observes the signal | `budget_gate` is the only place the graph clears `stop_reason` (Round 37, or a finished thread routes to `finish` forever), so a stop set in `run_tools` is wiped before any router reads it (ADR-062) |
| IDL-56 | An append-only record persists to an append-only file, never to a key-value `Store` | A `Store` rewrites the whole list per row, so one interrupted write loses the entire history — exactly what D-2 exists to prevent (ADR-063) |
| IDL-57 | A recorded result is replayed only for calls that change the world, never for calls that read it | A replayed `read` returns the state from before the crash. Idempotency protects against a double effect; it must not answer a question about the present with the past (ADR-064) |
| IDL-58 | A path-confining tool is constructed with its root; a module-level tool function confines to the CWD or not at all | `confine()` existed unused for two milestones because the tools that needed it had no root to pass — the missing constructor was the bug, not the missing call (ADR-065) |
| IDL-59 | An escalating policy escalates on the SIGNAL, never on "the cheaper rung ran out of work" | Editing always has one more stale result to blank, so compaction gated on that would never have run once (ADR-066) |
| IDL-60 | Context size is measured over the whole request payload, arguments included — never over `message.content` alone | A LangChain `AIMessage` carrying only tool calls has empty `content`; the arguments are the part that never gets blanked, and they measured as zero (ADR-066) |
| IDL-61 | An external doc-generation CLI (`openwiki`) is wired in as a tool the model must call, never a step the harness runs on its own | Every other seam in this library runs on nothing but an explicit call; `--update` is itself a paid model call, so auto-running it would bill every run for a wiki nobody asked to re-read (ADR-072) |
