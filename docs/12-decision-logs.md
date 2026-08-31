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
T-7.2 line calls for "một phiên bản deprecation" (a deprecation version). This project has
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

**T-8.2's own Failure/Test criteria were already true, not fixed here.** "Approval hết
hạn không dùng lại được" — `DecisionLog.lookup()` already filters `not d.live_at(now)`.
"Cùng một approval không mở khoá được lần chạy thứ hai" — `lookup()` already filters
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
that data — which, per N-6 (design/07 §1.5), it never does today on either backend
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

**Context.** docs/17's T-8.6: "`Session` là resource — id, ownership, TTL, fork, resume,
và ranh giới đồng thời. Vòng 37 đã sửa phần rò rỉ; đây là phần đặt tên cho thứ đã tồn tại
ngầm." The state isolation this depends on already exists — one `Ledger`/`TaintTracker`/
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

### ADR-054 — MCP client as tool boundary: `ServerLabel`, not `ServerIdentity`; fail-closed rug-pull; a bound description, not a filtered one
**Status:** Accepted (M9/T-9.1)

**Context.** docs/17's T-9.1 and design/03-tools-and-mcp.md §5: the harness must classify
a third-party MCP tool itself, from an operator-controlled policy keyed to the server's
identity, using the server's own `ToolAnnotations` hints at most as a default for a
server the operator has explicitly marked trusted — never as the decision. Landing this
closes five findings deferred on purpose since they had no MCP integration to attach to
(design/07-risks-and-open-issues.md §1.1): S-7…S-10 (server identity, annotation trust,
rug-pull, missing effect classification) and S-17 (injection through `description` before
the first tool call).

**Decision, four parts.**

1. **`ServerLabel = str`, not `ServerIdentity{label, fingerprint}`.** K-12 already
   rejected the fingerprint field for v1 — no format is settled that is the MCP-stdio
   equivalent of a TLS SPKI pin (a subprocess has no certificate to pin). `McpServerPolicy`
   keys on the label alone. What v1 does **not** stop, said plainly: a label re-pointed to
   a different endpoint carries its grants with it. Revisit when a real re-pointing
   incident is observed, not before (`07-risks §5.2`).

2. **`classify_mcp_tool` is pure, and its priority order is fixed:** an explicit
   `policy.effects[name]` always wins; failing that, `policy.trusted` gates whether
   `ToolAnnotations` is even read (M-1); failing that, `policy.default_effect`
   (`DANGER`). `_effect_from_hints` checks `is True`, never `!= False` — copying
   Microsoft's own post-mortem: a real server (GitHub's MCP) sets `readOnlyHint=True` on
   read tools and leaves it **unset**, not `False`, on write tools, so a `!= False` check
   would misclassify them as safe. `Scope.server`/`ToolSpec.server` exist in the type
   (`00-foundation §4.1`) since before this session but were never wired: `Scope.matches()`
   never read `self.server`, so a grant for `search` on a trusted server matched `search`
   on any other server sharing the name — the exact confused-deputy `server_label` exists
   to prevent. Fixed the same session: `Scope.matches()`/`DecisionLog.lookup()` now take
   `server=` and compare it; the one live call site (`lg/runtime.py::_regate`, the classic
   backend has no `DecisionLog`) threads `call.spec.server` through.

3. **Rug-pull is fail-closed, not "reclassify as a new tool."** design/03 §5.4's original
   text proposed treating a tool whose `tools/list` shape changed as a new tool, reclassified
   from scratch. Rejected on implementation: `Decision.scope` keys on the tool **name**, not
   on a fingerprint, so silently reclassifying a tool's effect under a name that already has
   live grants is itself a place a new confused-deputy could open — a grant issued when
   `search` was `read` would still `lookup()` as ALLOW after the same name quietly became
   `write`. `McpBinding.check_for_rug_pull()` instead raises `McpRugPullError` the moment a
   bound tool's `(name, description, input_schema, annotations)` fingerprint drifts, or the
   tool disappears from a re-list. Fail-closed beats a redefinition an operator never sees.

4. **S-17's fix is a bound, not a filter — said as plainly as S-18 said DNS rebinding.**
   `E7.1` already rejected an injection *detector* (a heuristic classifier gives false
   confidence). There is no different answer for a tool `description`: the harness cannot
   distinguish an server's legitimate instructions to the model from an injected one without
   the same rejected heuristic. What it can do, cheaply and honestly, is the same thing
   `max_result_tokens` already does for tool *results* — bound the untrusted input's size
   (`MAX_MCP_DESCRIPTION_CHARS = 2_000`, untrusted servers only; a trusted server's
   description is kept verbatim). This limits blast radius, not content. The actual
   structural defense against S-17 remains M-1: an untrusted server defaults to `DANGER`,
   so even a description that talks the model into calling it still stops at `ASK`.
   `docs/06-safety.md` states this the way it states the `EgressPolicy` DNS-rebinding gap.

**Transport.** `StdioMcpClient` hand-rolls JSON-RPC 2.0 over `asyncio.subprocess` rather
than depending on the official `mcp` SDK — zero new dependencies, consistent with the
project's dependency discipline for optional integrations (`viking` vendors only the
client half of its SDK, ADR-035; `graph` is two packages). stdio only for v1; SSE/HTTP MCP
transports are undemonstrated need, not built (`07-risks` "Chưa đủ evidence" discipline).

**Test.** `tests/test_m9_t91_mcp.py` — the four laws unit-tested against `classify_mcp_tool`
directly (pure function, no I/O needed); `Scope.server` confused-deputy proven both ways
(grant on server A does not authorize server B, does authorize server A); the transport and
rug-pull detection run against a real subprocess (`tests/fake_mcp_server.py`, genuine
JSON-RPC over stdio), not a mock — the same discipline IDL-46 already states for the vendor
SDK integration tests.

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
