# 00 — The Implementation Design Council

## 1. Composition

The council was assembled by role, not by seniority. Each member had standing to **block**
convergence within their domain; a block could only be cleared by a design change, never by
a vote. Members are named by role throughout the round log so their positions can be traced.

| Role | Mandate | Blocking authority |
|---|---|---|
| **Runtime Architect** | Agent loop, concurrency, control flow, state machine | Architecture, component design |
| **API/DX Designer** | Public surface, naming, progressive disclosure, error messages | Developer experience |
| **Beginner Advocate** | Plays a total newcomer. Never allowed to read source code to answer a question. | Beginner experience |
| **Security Engineer** | Threat model, prompt injection, permissions, secrets, supply chain | Security |
| **Cost Engineer** | Token accounting, caching, model spend, budget enforcement | Cost |
| **Reliability/SRE** | Failure modes, observability, deployment, upgrade, recovery | Operations |
| **Test Architect** | Testability, determinism, fakes, CI gates | Testing |
| **Poka-Yoke Reviewer** | Cross-cutting. Asks one question: *can a competent person still get this wrong?* | All dimensions |
| **Implementer** | Plays an engineer receiving a task on Monday morning with no context | Implementation tasks |
| **OSS Maintainer** | Versioning, plugin ecosystem, migration, long-term maintenance cost | Plugin architecture, documentation |

Two standing rules were adopted at Round 0:

- **R1 — No documented defects.** If a review finds a real problem, it is resolved and the
  plan is re-reviewed. It is never written into "Open Issues" to make the round close.
  Open Issues holds only genuinely non-blocking items, and every entry states why.
- **R2 — Simplicity is a blocking concern too.** The Poka-Yoke Reviewer may block a
  *safety* proposal for being over-engineered, and the Security Engineer may block a *DX*
  proposal for being unsafe. Neither side automatically wins.

---

## 2. Round log

Each entry records the substantive disagreement, not a summary of agreement. Decisions
that changed the architecture are cross-referenced to the Design Decision Log (`ADR-nn`).

### Round 0 — Understand

Established goals, constraints, NFRs and success criteria (→ [§01](01-requirements.md)).
Three ambiguities were material enough to resolve before any design work:

**A0.1 — "A ten-year-old can use it" vs. "a Python library for OSS developers."**
The Beginner Advocate pointed out that a ten-year-old does not run `pip install`, so the
requirement as literally stated is unsatisfiable and would push the design toward a
low-code product nobody asked for. The API/DX Designer proposed reading the requirement as
a *cognitive-load budget* rather than a literal audience.
**Resolved:** the target is **≤ 3 concepts and ≤ 5 lines to a working agent**, measured by
the Time-to-First-Agent test in [§14](14-validation-plan.md). The audience remains
developers. This became SC-1.

**A0.2 — "Cost-efficient" read as "use small models."**
The Cost Engineer opened by proposing a default of Haiku with escalation. The Runtime
Architect blocked: silently choosing a weaker model than the user asked for is a
correctness decision disguised as a cost decision, and it is the library author making a
call that belongs to the application author.
**Resolved:** the default model is the strong one; cost efficiency comes from *mechanism*
(pre-flight budget checks, cache-safety by construction, effort control, result
truncation, subagent delegation), never from silent downgrade. → **ADR-006**.

**A0.3 — What "plugin" means.**
Deferred to Round 2 with an instruction: produce a *test*, not a list.

**Gate:** 4/16 Ready. Proceeded.

---

### Round 1 — Initial plan (baseline)

A deliberately naive baseline was produced so it could be attacked: `Agent`, `Tool`,
`Runner`, `Memory`, `Plugin`, everything abstract, everything swappable, the SDK's
`tool_runner` doing the loop.

The baseline survived nineteen minutes.

**Gate:** 6/16. Two blocks filed immediately (Round 2).

---

### Round 2 — Architecture-to-code

**B2.1 — Security Engineer blocks: delegating the loop to the SDK tool runner.**
"If the SDK owns the loop, there is exactly one place I can enforce a permission check —
inside the tool function itself. That means every tool author is responsible for security,
which is the opposite of safe-by-design. I also cannot enforce a budget, because I never
see the decision point *before* the model call."
The Runtime Architect added a second, independent reason: the Python tool runner does not
auto-resume `pause_turn` and does not expose its message history, so a long server-tool
turn silently truncates the answer with no error raised.
The API/DX Designer counter-argued KISS: "you are proposing to rewrite something that
exists."
**Resolved:** own the loop, but keep it *exactly* the documented manual-loop shape — no
cleverness, roughly 150 lines. Owning the loop is justified precisely because the loop is
the only place invariants 2 and 3 can be enforced. → **ADR-001**.

**B2.2 — OSS Maintainer blocks: "everything is a plugin."**
"Nine abstract base classes and one implementation each is not extensibility, it is
speculative generality. Each one is a public contract I have to keep stable for years."
**Resolved:** adopted the three-part plugin-boundary test (→ [§2.4](02-architecture.md)).
Applying it cut nine extension points to **five**: Tool, Model Provider, Store, Policy,
Exporter. The loop, context assembly, budget accounting, schema generation and retry are
core and deliberately not overridable. → **ADR-002**.

**Missing contracts found:** no `RunContext` passed to tools; no defined verdict lattice
for policies; no token-counting interface on the provider (making pre-flight budget checks
impossible); no cancellation path. All four added to [§04](04-interfaces.md).

**Gate:** 8/16.

---

### Round 3 — Developer review

The Implementer walked eight baseline tasks asking only *"can I write this today?"* Five
came back **No**:

| Blocker | Resolution |
|---|---|
| "`Tool` is a class with five hook methods. Which do I implement?" | Replaced with one `@tool` decorator over a plain function. Schema generated from type hints. |
| "What exactly do I return from a tool?" | Return contract pinned: any JSON-serializable value, or `str`. Non-serializable → `ToolContractError` at return, naming the offending field. |
| "Sync or async?" | Async core, sync facade. Sync tools auto-offloaded to a thread pool. → **ADR-007**. |
| "What is in the context passed to a tool?" | `RunContext` fully specified in [§04](04-interfaces.md). |
| "How do I test this without spending money?" | `harness.testing` with `FakeModel`, `record`/`replay`, and `no_network()` promoted from "nice to have" to an **M0 deliverable** — because a testing story added at the end never gets used. |

**Gate:** 10/16.

---

### Round 4 — Beginner UX review

The Beginner Advocate attempted first-agent-in-five-minutes against the Round 3 API and
failed on three counts.

**C4.1 — Nine required imports.** Resolved: one import (`from harness import Agent`), with
pre-built tool packs under `harness.tools.*`.

**C4.2 — `system_prompt=` is jargon.** The word makes the newcomer believe there is a
hidden protocol they must learn. Renamed to **`job=`**, described in the docs as "tell it
what to do." → **ADR-009**.

**C4.3 — the `can=` debate.** The Beginner Advocate proposed `can=[search]`, which reads as
English. The OSS Maintainer blocked: every peer library calls this `tools=`, and shipping
both names is two ways to do one thing — an anti-pattern the same council had just spent a
round removing.
**Resolved: `tools=`, single name, no alias.** Naturalness is recovered in prose and in
the error messages, not by duplicating the parameter. Recorded as a deliberate loss for
the Beginner Advocate. → **ADR-010**.

**Gate:** 12/16.

---

### Round 5 — Poka-Yoke review

The heaviest round. The reviewer produced 34 concrete "a competent person still gets this
wrong" scenarios; all 34 are in the [register](08-poka-yoke.md). Four forced architecture
changes:

**D5.1 — A tool can be defined without anyone knowing what it does to the world.**
`@tool` over a plain function tells the harness nothing about whether the tool reads,
writes, calls out, or deletes production data. Every downstream safety and scheduling
decision therefore has to guess.
**Resolved:** `effect=` is a **required** argument on `@tool`, with four values
(`read`/`write`/`external`/`danger`). Parallel-safety, retryability, default permission,
taint propagation and audit level are all *derived* from it — the developer classifies
once and never configures the five consequences. → **ADR-003**.

The API/DX Designer immediately counter-blocked: a required argument in the very first
example violates the cognitive-load budget from A0.1.
**Counter-resolved, and this is the pattern the council reused four more times:**
*beginners consume, authors declare.* The built-in tool packs are pre-classified, so the
five-line first agent never writes `@tool`. The moment you author one, the omission is an
**import-time** `MissingEffectError` that prints the four options and guesses the likely
one from the function name. Runtime error → configuration-time error.

**D5.2 — Cache invalidation is invisible, expensive, and diagnosed weeks later.**
A `datetime.now()` in a system prompt silently costs 10× forever, and nothing fails.
**Resolved:** the context assembler renders the prefix twice at agent construction and
byte-compares. A difference raises `NonDeterministicPromptError` naming the differing byte
range — *before a single token is spent.* Additionally, tools are frozen and name-sorted
at construction and the system prompt is immutable, so the two most common invalidators
are not merely detected but structurally impossible. → **ADR-004**.

**D5.3 — Adding a policy could accidentally loosen security.**
With arbitrary composition, a permissive policy registered late could override a
restrictive one.
**Resolved:** verdicts form a lattice (`ALLOW < ASK < DENY`) and composition takes the
**maximum**. A policy can therefore only ever restrict. This is checked by a property test.

**D5.4 — Budget overrun is discovered by reading the invoice.**
**Resolved:** the ledger checks the *worst case* (`counted input tokens × input price +
max_tokens × output price`) **before** the call. The invariant is "never exceed", not
"alert on exceed". Defaults are finite (`$0.50`, 20 steps, 300 s) — an infinite default is
fail-open. → **ADR-005**.

**Gate:** 13/16. Security still **No** (Round 7 pending).

---

### Round 6 — Cost & performance

Sources of waste found and closed:

- **Unbounded tool results.** One tool returning a 2 MB file poisons the context for the
  rest of the run and is re-billed on every subsequent turn. → `max_result_tokens`
  (default 4 000) on every tool, truncating with an explicit marker Claude can see.
- **Serial execution of parallel-safe calls.** The effect class from D5.1 already tells us
  which calls are parallel-safe; the loop now runs `read`/`external` calls concurrently
  and returns all `tool_result` blocks in a single user message (splitting them across
  messages silently teaches the model to stop batching).
- **Retrying non-retryable failures.** Effect class again: `write` and `danger` are never
  auto-retried. A retried `send_payment` is a duplicate payment.
- **Subagent forks missing the parent's cache.** A fork that rebuilds `system`/`tools`
  from scratch misses the parent prefix entirely. → forks copy the parent's rendered
  prefix verbatim and append.
- **Compaction/context-editing chosen ad hoc.** → single documented policy in [§07](07-cost.md).

The Cost Engineer re-proposed model routing. The Runtime Architect blocked it as
speculative: no two-model routing policy could be named that the council agreed was
correct today. **Deferred to v2 as an explicit non-goal**, with the honest note that
`subagent(model=...)` already captures most of the available savings without any routing
magic. → **ADR-006**.

**Gate:** 14/16.

---

### Round 7 — Security & safety (red team)

The Security Engineer ran attacks against the Round 6 design. Three landed.

**E7.1 — The lethal trifecta.** An agent with a web-fetch tool and a `send_email` tool can
be instructed by a fetched page to exfiltrate whatever it has read. Nothing in the design
prevented it.
The first proposal was an injection *detector*. The Poka-Yoke Reviewer blocked it as both
over-engineered and ineffective — a heuristic classifier gives false confidence.
**Resolved with a capability rule instead of a detector:** output of `effect="external"`
tools is **tainted**; once tainted content enters the transcript the run is in tainted
mode; in tainted mode `effect="danger"` tools are **DENY**, not ASK. Approval prompts are
the wrong instrument here because a human cannot reliably audit a wall of fetched text —
approval fatigue makes ASK equivalent to ALLOW. Roughly twenty lines, one sentence to
explain. → **ADR-011**.

The escape hatch was itself debated. A global `Agent(allow_tainted_danger=True)` was
rejected as a footgun that would be copy-pasted from Stack Overflow. **Chosen:**
`@tool(effect="danger", accepts_tainted=True)` — a per-tool, code-level opt-in that
appears in the diff a reviewer reads.

**E7.2 — Auto-discovery of installed plugins is a supply-chain backdoor.** Most plugin
systems load every installed entry point at import. A transitively installed package then
executes code and registers tools without anyone deciding it should.
**Resolved:** entry-point discovery is **opt-in** (`Agent(discover=True)`), never the
default. → **ADR-008**.

The council also refused to claim plugin sandboxing. A plugin is Python; installing it is
`pip install`. [§06](06-safety.md) states the trust boundary plainly rather than implying
a protection that does not exist.

**E7.3 — Secrets leak through transcripts and tracebacks.** → `Secret` type that does not
render in `repr`/`str`/logs/tracebacks, unwrapped only inside `with s.reveal()`, plus a
redaction pass on every transcript write.

**Gate:** 15/16. Observability still **No**.

---

### Round 8 — Production engineering

- **Recovery.** A full durable-execution engine was proposed and rejected as
  over-engineering for a library. → append-only JSONL transcript + `Agent.resume()` gets
  ~90 % of the value at ~5 % of the cost. Durable execution is a stated non-goal.
- **Refusals.** `stop_reason: "refusal"` on current models returns HTTP 200 — code that
  reads `content` without checking `stop_reason` produces a confident empty answer. →
  explicit `StopReason.MODEL_REFUSAL`, and server-side fallbacks enabled by default.
- **Observability.** A closed 15-event taxonomy defined in [§05](05-data-and-state.md);
  the transcript and the OTel exporter are two renderings of the same stream, so they can
  never disagree.
- **Versioning.** Public API = exactly what `harness/__init__.py` exports. Everything else
  is private and may change in a patch. Plugin contracts get a declared `API_VERSION`.

**Gate:** 16/16 for the first time.

---

### Rounds 9–11 — Recursive review

Re-reviewing the whole design after Rounds 5–8 surfaced three regressions caused by
earlier fixes. This is why the recursive rounds existed.

**F9.1 — The taint rule (E7.1) broke the most common beginner agent.**
`Agent(job="research and email me a summary", tools=[search, send_email])` now fails at
runtime with `DENY`, after doing the work and spending the money. The Beginner Advocate
filed this as a beginner-experience block.
**Resolved:** the check moved to **construction time**. If the tool set contains both an
`external` tool and a `danger` tool that does not declare `accepts_tainted`, `Agent(...)`
raises immediately with the exact one-line fix. The dangerous combination is now
impossible to *discover late* — and, notably, this made the design safer *and* friendlier
at once, which the council treated as evidence the rule was right.

**F9.2 — Frozen tool sets (ADR-004) broke subagents.** A subagent with a different tool
set appeared to violate the freeze.
**Resolved:** the freeze is per-`Agent`; a subagent is a *different* `Agent` with its own
frozen set and its own cache prefix. No conflict, but it was undocumented — now specified.

**F9.3 — `.run()` raising on budget exhaustion (Round 5) destroyed partial work.**
**Resolved:** `.run()` raises (loud, correct for beginners); `.try_run()` returns the
`Result` either way; and the raised exception carries `.partial: Result` so nothing is
lost. Both audiences served without an options flag.

**Gate:** 16/16 held after re-review.

---

### Round 12 — Final implementation simulation

A simulated team ran Day 1 → first deployment purely from [§11](11-implementation-plan.md).
Four blockers were found and fixed in the plan:

1. **Day 1 had no runnable target.** M0 was a set of interfaces with nothing to execute.
   → M0 restructured as a **walking skeleton**: a real agent, one real tool, one real
   model call, end to end, green in CI. Interfaces are extracted from working code, not
   written before it.
2. **T-1.3 (schema generation) had no stated behavior for unsupported types.** → pinned:
   supported set enumerated; anything else raises `ToolSchemaError` at import naming the
   parameter.
3. **No task said who writes the pricing table, or what happens when it is stale.** →
   T-2.1, plus a CI job that fails when the table's `as_of` date is older than 90 days.
4. **The cache-hit acceptance criterion was unmeasurable** ("caching works"). → replaced
   with a benchmark: ≥ 90 % `cache_read_input_tokens` on turns 3+ of the 10-turn fixture.

**Final gate: 16/16.** Convergence declared.


---

### Round 13 — Reopened: the beginner premise was wrong

**The project owner corrected the council's Round 0 premise:** a ten-year-old who has
learned basic Python *does* know `pip install`, `def`, `import`, variables, lists and
strings. Ambiguity A0.1 was resolved on a false assumption, and its resolution is
**superseded**.

**A0.1-R (revised).** The requirement is literal. A ten-year-old with basic Python must be
able to build an agent — including giving it a new ability of their own. The cognitive-load
budget from A0.1 stands as a *floor*, not as a reinterpretation.

The council re-ran the Beginner UX review with an actual child persona: knows `def`,
variables, strings, lists, `print`, `import`, calling functions, `pip install`. Does *not*
reliably know: decorators, type hints, keyword-only arguments, exceptions, context
managers, classes, async, or environment variables.

The Beginner Advocate's finding was blunt: **the Round 4 review had tested the wrong
things.** It checked the shape of the API and passed it. It never checked whether a
newcomer could get from an empty folder to a working agent at all. Six blockers were found,
and only one of them was in the API.

| # | Blocker | Severity |
|---|---|---|
| **G13.1** | **The API key.** Nowhere in the design does a user learn where a key comes from or how to supply one. `export ANTHROPIC_API_KEY=...` is a shell concept, not a Python one. This is the true first wall, and the design was silent on it. | Blocking |
| **G13.2** | **Fifteen seconds of silence.** The agent runs, nothing prints, the child concludes it is broken and presses Ctrl-C. Streaming was an opt-in parameter. | Blocking |
| **G13.3** | **Forty-line tracebacks.** Any error dumps `asyncio` internals. An adult skims for the last line; a child closes the terminal. | Blocking |
| **G13.4** | **`Agent("Helper", "tell jokes")`** — the natural thing to write — produces `TypeError: __init__() takes 0 positional arguments but 2 were given`. | Blocking |
| **G13.5** | **Required type hints in `@tool`.** A child writes `def add(a, b):`. This is the only genuine API barrier, and it sits exactly where the child wants to go next. | High |
| **G13.6** | **Repeated runs.** A per-run budget of $0.50 does nothing about a curious child running the file eighty times. | High |

The Runtime Architect noted the uncomfortable part: **five of six blockers are outside the
API surface the council had spent four rounds polishing.** Time-to-first-agent is dominated
by setup, feedback and error rendering — not by parameter names.

**Gate:** dropped to 14/16. Beginner Experience and Developer Experience → **No**.

---

### Round 14 — Resolving the six blockers

**G13.1 — the API key. `harness setup`.**
An interactive command that asks for a key, validates it with one minimal call, and writes
it where the SDK already looks. Every "no credentials" error is rewritten from
`set ANTHROPIC_API_KEY` to `Run: harness setup`.

The Security Engineer blocked the first proposal (write the key to `~/.harness/config.toml`)
on two counts: inventing a credential location the vendor SDK does not read means two
sources of truth, and a plaintext key in a project folder gets committed to GitHub.
**Resolved:** prefer the environment variable when present; otherwise write a project
`.env` with mode `0600`; and `harness new` generates a `.gitignore` containing `.env` **in
the same command that creates the file that needs it**. The protection cannot be forgotten
because it is never a separate step. → **ADR-013**.

**G13.2 — silence. Progress by default, on a terminal only.**
When `stdout` is a TTY, `run()` prints live progress: *Helper is thinking… · Helper is
searching the web… · done ($0.003, 4s)*. When output is piped or redirected — which is
every production context — it is silent.

The SRE objected that a library printing to stdout unbidden is bad manners. **Resolved:**
the TTY check *is* the manners. Nothing that reads harness output programmatically is
attached to a terminal. Tool **names** only are shown, never arguments — arguments carry
PII (register #24). → **ADR-014**.

**G13.3 — tracebacks. Filtered, locally, without global state.**
The first proposal installed a `sys.excepthook` at import. The OSS Maintainer blocked it
outright: a library that mutates global interpreter state on import is hostile in any
application that embeds it, and it would break every debugger and error reporter in the
ecosystem.
**Resolved:** no global hook. `run()` catches, rewrites `__traceback__` to drop harness
internals and `asyncio` frames, sets `__suppress_context__`, and re-raises. Purely local to
the call. `HARNESS_FULL_TRACEBACK=1` restores everything.
**The full, unfiltered traceback is still recorded in the `error.raised` event and the
transcript** — nothing is lost, only the console is made readable. → **ADR-015**.

**G13.4 — positional arguments.**
Keyword-only stays (register #1 — with two adjacent strings, mis-assignment is a real and
confusing bug). What changes is the error. `__init__` accepts `*args` solely in order to
reject them with a message that shows the corrected call, rather than letting Python emit
its own.

This became the round's reusable pattern, applied in four more places: **keep the
constraint, replace the error.** A Poka-Yoke that produces an incomprehensible message is
only half-built.

**G13.5 — type hints.** The hardest debate of the round.

*Proposal 1: make hints optional, default unannotated parameters to string.* Rejected —
`def add(a, b)` would receive `"3"` and `"4"` and return `"34"`. A silently wrong answer is
far worse for a learner than an error, because there is nothing to search for.

*Proposal 2: infer from default values.* Rejected — works only when defaults exist, and
produces inconsistent behavior within a single function signature.

*Proposal 3: require hints, but make the error a one-line lesson.* **Accepted.** For
someone who already knows `def`, adding `: int` is a thirty-second lesson, not a wall —
provided the error shows the exact edit:

```
Your tool needs to say what kind of thing each answer is.

    def add(a, b):              ← you wrote this
    def add(a: int, b: int):    ← change it to this

  int = whole number   float = decimal   str = text   bool = yes/no
```

The Poka-Yoke Reviewer added the corollary: `effect="reed"` must produce
*did you mean `"read"`?* — a typo in a four-word vocabulary is the most likely mistake
anyone will make with it.

**G13.6 — repeated runs.** The Cost Engineer proposed a persistent daily cap in a lock-file.
The Poka-Yoke Reviewer blocked it: cross-process spend accounting in a library means file
locking, clock skew and a race the library cannot win, to reimplement — badly — a hard
limit the API provider already enforces properly.
**Resolved, two parts, and the distinction between them is load-bearing:**
1. **The real ceiling belongs at the provider.** `harness setup` prints the console
   spend-limit URL and asks the user to set one. Do not rebuild what already exists.
2. **In-process only:** a once-per-process warning when cumulative spend across runs passes
   `$5`. No files, no locks, no races. It catches the runaway script.

It is documented as a **warning, not a ceiling** — precisely so it cannot dilute the
"budget is a ceiling" guarantee of ADR-005. → **ADR-016**.

Two further changes came out of the same review:

- **`Result.__str__` returns the text**, so `print(agent.run("hi"))` works. `.text` still
  exists for people who want it. One line; removes an entire concept from the first example.
- **`harness new` and `harness chat` move to M0** from M5. A child's first artifact should
  be a commented, runnable file that already contains a small budget — teaching the concept
  by showing it — and talking to your own agent is the moment that makes someone want a
  second one. `harness chat` is roughly twenty lines.

---

### Round 15 — Recursive review of Round 14

Re-reviewing the whole design for regressions introduced by the six fixes:

| Check | Finding |
|---|---|
| Does TTY progress leak data? | Tool **names** only. Arguments never printed. Consistent with register #24. ✅ |
| Does traceback filtering lose diagnostics? | No — the full traceback is in the transcript and the `error.raised` event. Console-only change. ✅ |
| Does `Result.__str__` weaken anything? | No. `print(result.text)` had identical exposure. `+` still raises. ✅ |
| Does the session warning dilute ADR-005? | Only if described as a limit. Documentation is required to call it a warning, and [§07.1](07-cost.md#1-the-budget-is-a-ceiling-not-an-alert) states the distinction. ✅ |
| Does `.env` conflict with the SDK's credential resolution? | No — env var wins; `.env` is the fallback. One source of truth preserved. ✅ |
| Does `harness new` conflict with ADR-004 (frozen agent)? | No — it generates a module-scope agent, which is also the correct production shape ([§10.5](10-observability-ops.md#5-running-in-production)). The child's first file teaches the right habit. ✅ |
| Did any fix make things worse for experienced users? | None. Every change is either TTY-gated, additive, or a strictly better error message. ✅ |

**One genuine regression found.** The M5 documentation task was written for developers.
Under the revised A0.1-R it does not satisfy the requirement at all. → a **separate
child-facing quickstart** is now a deliverable, not a section of the developer docs, and it
is written and reviewed as a demonstration rather than asserted:
[§15 — Your First Agent](15-first-agent.md).

---

### Round 16 — Re-validating the gate

The Test Architect's objection closed the loop: **SC-1 as written measures developers, so
passing it would prove nothing about the requirement the owner actually stated.** Asserting
"a child could do this" without measuring it is exactly the kind of claim the council
refuses everywhere else in this package.

**SC-1 split, both blocking:**

- **SC-1a** — 5 developers, README only. Median ≤ 10 min, ≥ 4/5 unaided. *(unchanged)*
- **SC-1b** — **3 children aged 10–12 who have completed a basic Python course.** Given
  [§15](15-first-agent.md) only. An adult may read words aloud but may not explain, debug,
  or type. **Pass: ≥ 2/3 reach a working agent in ≤ 20 minutes, AND ≥ 2/3 successfully add
  one tool of their own.**

The second half matters more than the first. Running a provided example proves the example
works. Adding a tool of your own is the point at which someone has actually built
something, and it is the only part of the ladder that touches `@tool`.

**If SC-1b fails, 1.0 is blocked and the council reconvenes on the API — not on the
tutorial.** If the fix is "explain it better", the API is wrong. Stated in advance so the
result cannot be rationalized afterwards.

**Gate restored to 16/16**, now against the literal requirement rather than a
reinterpretation of it.


---

### Round 17 — Recursive review of Rounds 13–16

Round 15 reviewed the six fixes against the *existing* design. Round 17 reviewed the design
against the fixes. It found one outright bug, one accessibility gap, and one proposal worth
rejecting on the record.

**H17.1 — CRITICAL: the scaffold budget and the pre-flight reservation contradict each
other. Every first run would have failed before making a single call.**

The Cost Engineer traced the numbers rather than assuming them:

```
Scaffold: budget="$0.05", default max_tokens=16000, model claude-opus-5 ($5 / $25 per MTok)

worst-case reservation = 1200/1e6 × $5   (input)
                       + 16000/1e6 × $25 (output)
                       = $0.406

$0.406 > $0.05  →  ledger.reserve() refuses  →  StopReason.BUDGET_EXHAUSTED at step 0
```

A beginner following [§15](15-first-agent.md) exactly would see their agent stop
immediately, having said nothing, with a budget message — and the natural fix (raise the
budget) is the opposite of the lesson the scaffold was trying to teach.

Worse, the *default* budget only escapes by accident: `$0.406 < $0.50` clears by nine
cents. The design had two independent knobs — `budget` and `max_tokens` — that silently
contradict each other, and nobody had multiplied them out. **The council had reviewed the
budget mechanism four times without ever computing a single number with it.**

**Resolved — `max_tokens` is derived from the remaining budget, not configured beside it:**

```
max_tokens = clamp(
    floor((remaining_usd − input_cost) / output_price_per_token),
    lower = 256,                     # below this, stop instead: a truncated answer is not an answer
    upper = model's maximum output,
)
```

| budget | derived `max_tokens` |
|---|---|
| `$0.05` | ~1 760 |
| `$0.10` | ~3 760 |
| `$0.50` (default) | ~19 760 |

Two knobs that could disagree become one that cannot. A small budget now produces a
**short answer** instead of **no answer**, which is what a beginner expects and what the
scaffold was trying to demonstrate. `max_tokens` is removed from the public surface
entirely — it was never exposed, and now it never needs to be. → **ADR-017**.

The Poka-Yoke Reviewer noted the shape of the miss for the record: the bug was not in any
component. Every component was correct. It lived in the *interaction* between two defaults
chosen in different rounds by different people — which is exactly the class of defect the
recursive rounds exist to catch, and exactly the class that a table of components will
never reveal. **The rule adopted: any numeric default is validated by arithmetic against
every other numeric default it can meet, not by review.** T-1.5 now carries that test.

**H17.2 — `UnsafeToolSetError` is reachable from the tutorial but not explained in it.**
A child who adds `search` alongside a `danger` tool hits a construction-time error that
[§15](15-first-agent.md) never mentions. The message itself is accessible, but meeting it
unannounced is the kind of surprise that ends a session. → added to §15's error section, in
the tutorial's own register.

**H17.3 — Rejected: streaming the answer token by token on a TTY.**
Proposed by the API/DX Designer as the single most delightful thing for a beginner — text
appearing as it is written. Rejected on a concrete conflict: with `print(agent.run(...))`
as the first example, streaming to the terminal prints the answer twice, and every fix for
that (suppressing the final print, a magic "already streamed" flag on `Result`) adds hidden
state to the simplest path in the library.

The progress line from ADR-014 already solves the actual problem, which was silence reading
as breakage. Recorded as considered-and-rejected so it is not re-proposed as an obvious
oversight. → **ADR-018**.

**Gate:** held at 16/16 after the fix. Cost was **No** for the duration of H17.1.


---

### Round 18 — Recursive review of Round 17

Round 17's fix was correct and introduced a new exposure. That is the argument for
recursive rounds in one sentence, so it is recorded rather than smoothed over.

**H18.1 — CRITICAL: there is no stop reason for a truncated answer, and ADR-017 just made
truncation common.**

The API returns `stop_reason: "max_tokens"` when generation hits the ceiling. The
`StopReason` enum has eight values and none of them is it — so a cut-off answer was mapped
to `COMPLETED`. `result.ok` would be `True`. `run()` would not raise. The user gets half a
sentence reported as success.

This was survivable while `max_tokens` was a large constant nobody reached. **Round 17
derived `max_tokens` from the budget, which means a small budget now deliberately produces
a small ceiling** — the exact condition that makes truncation routine. The fix for one
defect promoted a latent one to likely.

**Resolved:** `StopReason.TRUNCATED`, `ok = False`, and a message that names the cause
rather than the mechanism:

```
Your helper's answer got cut off because it reached its budget of $0.05.

    "Why did the cat sit on the..."

  To let it write more, change:  budget="$0.20"
```

The Poka-Yoke Reviewer required the message to distinguish the two cases that produce the
same API stop reason — a budget-derived ceiling (raise the budget) and the model's own
maximum output (the answer is genuinely enormous; ask for less). One `stop_reason` from the
provider, two different things for the user to do.

**The general finding, which is the more important half.** The `StopReason` enum was
reviewed in Round 8 and passed. It was complete with respect to the *design*, and
incomplete with respect to the *API*. **Every closed enum that mirrors an external
protocol is now required to carry an exhaustiveness test against that protocol's values**,
so a value the provider can emit cannot be silently absent. An unmapped value maps to
`ERROR` with the raw string, never to a success. → **ADR-019**.

**H18.2 — `Chat` budget semantics were never defined.**

A `Budget` is per-run. A `Chat` is many turns. Nobody had said whether the budget covers a
turn or the conversation — and the two readings differ by a factor of however long someone
talks. Under the per-turn reading, `harness chat` with the scaffold's `$0.05` is unbounded
across eighty turns; under the per-session reading, a chat dies after three.

**Resolved:** a `Chat` holds **one ledger for the session**. `chat(budget=...)` sets it and
defaults to **ten times** the agent's run budget, stated in the docs and shown in
`harness chat`'s banner. Each turn draws from the shared ledger.

This composes with ADR-017 rather than fighting it: as the session budget depletes, the
derived `max_tokens` shrinks, so answers get shorter and *then* the chat ends with a clear
message. It degrades instead of stopping dead. The Cost Engineer noted that ADR-017 turned
out to be load-bearing for a feature it was not designed for — worth recording as evidence
the derivation was the right shape. → **ADR-020**.

**H18.3 — Which limit binds first depends on the model, and the plan assumed one answer.**

The context-management thresholds (60 % / 80 %) were specified without checking against the
budget, which also terminates long runs:

| model | context | tokens `$0.50` buys | 60 % of context | binds first |
|---|---:|---:|---:|---|
| `claude-opus-5` | 1 000 000 | 100 000 | 600 000 | **budget** |
| `claude-sonnet-5` | 1 000 000 | 250 000 | 600 000 | **budget** |
| `claude-haiku-4-5` | 200 000 | 500 000 | 120 000 | **context** |

So on the default model at the default budget, **compaction never runs** — T-2.6 would have
been exercised by no default configuration and by no test written from these defaults. It
is not dead code (large-budget agents and cheap models reach it, and Haiku reaches it
first), but its fixtures were going to be built on an assumption that does not hold.

**Resolved:** T-2.6's fixtures are specified per-model against this table, and
[§07.3](07-cost.md#3-token-discipline) states which limit binds where. No design change —
the numbers were simply never multiplied out, which is the same omission as H17.1 in a
different place, and the reason ADR-017's arithmetic rule was made general.

**Gate:** held at 16/16 after the fixes. Cost and Architecture were **No** for the duration
of H18.1.


---

### Round 19 — Contract review of the interfaces

Rounds 17 and 18 both found defects by *computing* rather than reading. Round 19 applied
the same discipline to the type contracts in [§04](04-interfaces.md) — executing the
specified semantics instead of reviewing them. Three contradictions surfaced, all of them
inside interfaces that had been reviewed and approved in earlier rounds.

**H19.1 — `ApprovalPolicy` violates the `Policy` contract it is listed under.**

[§04.3](04-interfaces.md#3-policy--verdicts) specifies:

- `Policy.check` is **synchronous**, **pure**, **< 1 ms**, and **must not perform I/O**.
- `ApprovalPolicy` is listed as a built-in `Policy`, and its job is to call
  `ApprovalFn = Callable[..., bool | Awaitable[bool]]` — **a human round trip**.

A human pressing `y` is I/O, is unbounded in time, and may be a coroutine that a sync
`check` cannot await. The two specifications are directly incompatible, and both were
approved: the purity rule in Round 2, the approval policy in Round 5.

**Resolved by separating the two ideas rather than loosening either.** Approval is not a
policy — it is what happens *after* the policies have spoken:

```
policies (sync, pure, fast)  →  composed verdict  →  if ASK: engine awaits approval
```

`ApprovalPolicy` is deleted. The engine owns approval resolution. This is strictly better
than relaxing `Policy.check` to async: the purity rule is what allows policies to be
evaluated cheaply for every call, and third-party policies doing hidden I/O on the hot path
is precisely what it exists to prevent. The word "terminal" in the original entry was the
tell — a thing described as terminal within a composition step is not part of the
composition.

**H19.2 — `Secret` breaks Python's hash invariant, silently.**

As specified: `__eq__` is a constant-time comparison of the value; `__hash__` is "of the
name, not the value". Executed:

```
a = Secret("sk-ant-abc", name="prod_key")
b = Secret("sk-ant-abc", name="backup_key")

a == b             -> True
hash(a) == hash(b) -> False
len({a, b})        -> 2      # equal objects, both present
```

Python requires `a == b ⟹ hash(a) == hash(b)`. Violating it corrupts every set and dict the
type touches, with no error — the failure mode is a duplicate entry, not an exception.

**Resolved: `Secret.__hash__ = None`.** Unhashable, and the reasoning is not merely "it
fixes the contract":

- There is no real use case for a secret as a dict key or set member.
- Unhashable **prevents a secret becoming a cache key** — an `lru_cache` keyed on a
  credential retains that credential in a process-global cache for the life of the process,
  which is a leak the redactor cannot reach.

The Poka-Yoke Reviewer accepted it on the second reason rather than the first: fixing the
contract by hashing a keyed digest of the value would also have been correct, and would
have left the caching footgun open.

**H19.3 — the redaction registry retains secret values for the life of the process.**

[§06.5](06-safety.md#5-secrets) has each `Secret` "registered with the redactor at
construction". As written that is a process-global strong reference: every secret ever
constructed stays in memory until exit, including ones whose owning objects were discarded,
and including short-lived per-request credentials in a long-running server.

**Resolved:** the registry holds a `WeakSet` of `Secret` objects and reads values from live
ones at redaction time. A secret that goes out of scope stops being retained. The redactor's
reach shrinks with the secret's lifetime, which is the correct direction — a redactor that
outlives what it protects is itself the exposure.

**The pattern across all three.** Every one of these interfaces was reviewed and approved.
None of the defects is visible by reading; all three are visible by executing. **The council
adopts: a type contract is reviewed by writing the twenty lines that exercise it, not by
reading the signature.** Rounds 17, 18 and 19 have now each found a defect this way, and
none of the earlier rounds found one by inspection.

**Gate:** held at 16/16 after the fixes. Security and Interfaces were **No** for the
duration of H19.1 and H19.2.


---

### Round 20 — Executing the type contracts

Round 19 adopted the rule that a type contract is reviewed by writing the lines that
exercise it. Round 20 was the first round run that way from the start. It found two
defects, one of which is a straightforward failure of this package's own stated purpose.

**H20.1 — `ToolSet` and the token-count memo both hash things that cannot be hashed.**

[§02.5](02-architecture.md#5-module-map) specifies `ToolSet` as "frozenset-backed".
[§04.2](04-interfaces.md#2-model-provider) specifies `count_input_tokens` as "memoized by
request hash". Both `ToolSpec` and `ModelRequest` are `frozen=True` dataclasses carrying
`Mapping` fields:

```
frozenset({tool_spec})   ->  TypeError: unhashable type: 'dict'
hash(model_request)      ->  TypeError: unhashable type: 'dict'
```

A frozen dataclass generates `__hash__` from its fields, and one of those fields is a dict.
Neither line could ever have run.

**Resolved, and both fixes are simplifications:**

- `ToolSet` is backed by a **sorted tuple plus a name→spec dict**. It was already required
  to be name-sorted for cache determinism (ADR-004), so the ordering the frozenset would
  have destroyed is the ordering the design depends on. The set was the wrong container from
  the start.
- Memoization keys on **`blake2b(canonical_json(request))`**, reusing the assembler's
  canonical serializer — which has to exist anyway for prompt caching. The system now has
  **one** definition of "the same request" instead of two that could diverge.

The Poka-Yoke Reviewer noted the pattern: both defects were introduced by reaching for a set
where an ordered structure was required. AC-22, added in Round 19 for `Secret`, catches this
one too, which is the first evidence that the package-wide framing of that rule was right.

**H20.2 — Seven types in normative signatures have no definition anywhere.**

Scanning [§04](04-interfaces.md) for type names used in signatures but never defined:

```
ContentBlock · DeltaFn · EventKind · Money · Reservation · SystemBlock · Usage
```

`Money` is in `__all__` — **a publicly exported type with no contract**. `Usage` appears in
`Result`, in `ModelResponse` and in three event payloads. An implementer picking up T-0.4 or
T-0.6 would have had to invent all seven and hope the next person invented them compatibly.

This is not a stylistic gap. [§00](00-council.md) states the package's whole purpose as "an
engineering team can begin without making a further architectural decision", and seven
undefined types are seven decisions handed to whoever types fastest.

**Resolved:** [§04.0](04-interfaces.md#0-core-value-types) now defines all seven before
anything references them.

Two of the definitions were themselves decisions worth recording. `ContentBlock` and
`SystemBlock` are `Mapping[str, object]` **deliberately**, not a class hierarchy: the harness
routes blocks by their `type` key and never interprets their bodies, so modelling them would
be a per-provider maintenance cost with no reader. Only the provider adapter looks inside
one. The Runtime Architect proposed a full block hierarchy and withdrew it on that argument.

**Why Round 12's simulation missed this.** The Day-1 walkthrough followed *tasks* and
checked that each had a contract, a test and a definition of done. It never followed a
*type* from its use back to its definition. **The walkthrough now includes that traversal**,
and it is cheap: every capitalized name in a signature must resolve to a definition in the
same package.

**Gate:** held at 16/16 after the fixes. Interfaces was **No** for the duration of H20.2.


---

### Round 21 — Auditing the five invariants against the package itself

Rounds 17–20 hunted defects. Round 21 asked a different question: **for each of the five
invariants, what does this package actually contain?** Counting sections, ADRs,
requirements and tests per invariant produced an uncomfortable result.

| Invariant | Sections | ADRs | Requirements | Tests | Verdict |
|---|:--:|:--:|:--:|:--:|---|
| 1 Extensible | §02.4, §04 | 002, 008 | FR-15, SC-6 | AC-01, AC-08 | solid |
| 2 Cost-efficient | §07 (whole) | 004, 005, 006, 016, 017, 020 | SC-2, SC-4 | P-1, P-8, AC-04 | solid |
| 3 Safe by design | §06 (whole) | 003, 011, 013, 019, 021 | SC-3 | RT-01…17, AC-05 | solid |
| 4 **Intelligent** | **none** | **none** | **none** | **none** | **one line in the README** |
| 5 Efficient | §07.5, §02.6 | 007 | NFR-01…03, 09 | benchmarks | solid |

**H21.1 — Invariant 4 has one sentence in the entire package**, and that sentence describes
what the harness *doesn't* do ("not model-downgrade roulette"). Every other invariant has a
section, several decisions and a measurable gate. The council had been treating "maximum
intelligence per unit of cost" as satisfied by the cost work, which is half the phrase.

Rather than invent a reasoning architecture to fill the hole, the council asked what the
harness could contribute that is **not speculative** — mechanisms that raise the quality of
an answer per dollar, that already exist in the provider, and that need no new concepts.
Two were found; one was rejected.

**H21.2 — `strict: true` is never set, although the schemas were built for it.**

[§04.1](04-interfaces.md#1-tools) specifies tool schemas as "strict-ready" —
`additionalProperties: false`, complete `required` — and then nothing ever turns strict mode
on. Strict tool use guarantees `tool_use.input` validates exactly against the schema. Left
off, malformed tool arguments reach the tool, raise, come back as an `is_error` result, and
cost a round trip to rediscover something the API would have prevented for free.

**Resolved: `strict: true` on every generated tool definition.** It costs nothing, removes a
class of runtime failure, and the work to make it possible had already been done and then
left unused — which is the most annoying kind of gap to find, and the easiest to close.

**Structured output — accepted.** `Agent(returns=SomeType)` sets `output_config.format` and
`result.value` is that type, validated. This is intelligence-per-cost in the literal sense:
a constrained answer eliminates the parse-fail-and-re-prompt loop, which is a doubling of
cost that produces no additional thinking. It is one optional parameter at Level 2 of the
ladder, and it does not touch the beginner path.

The council reversed OI-03 ("defer until a user asks") on the grounds that deferring a
free, one-parameter provider feature while claiming Intelligent as an invariant is
under-delivering against a stated requirement. → **ADR-022**.

**Planning, reflection and self-critique loops — rejected.** Proposed as the obvious way to
make agents smarter. Rejected on the invariant's own wording: a critique pass is a second
model call for an unmeasured quality gain, which is the *opposite* of maximum intelligence
per unit of cost. It is also exactly the speculative capability that "not over-engineered"
forbids. If a user wants reflection, it is an agent whose job says so, calling a subagent —
already expressible today with no new machinery. → **ADR-023**.

**The honest statement, now in the package.** The harness's contribution to intelligence is:
adaptive thinking on by default, exposed effort, strict tool arguments, optionally
constrained output, tool errors returned to the model rather than swallowed, and explicit
subagent delegation. **It does not make a weak model strong, and it does not claim to.**
That sentence is now in [§07.6](07-cost.md#6-intelligence-per-unit-of-cost) rather than
implied by its absence.


---

### Round 22 — Traceability: what does no task own?

Round 21's technique was *count coverage per requirement*. Round 22 applied it mechanically
to everything the package numbers — requirements, red-team scenarios, conformance tests,
properties — and asked one question of each: **which task builds this?**

| Artifact | Total | Owned by a task | Orphaned |
|---|---:|---:|---:|
| Functional requirements (FR) | 25 | 9 | **16** |
| Non-functional (NFR) | 10 | 8 | 2 |
| Red-team scenarios (RT) | 17 | 14 | **3** |
| Conformance tests (AC) | 25 | 7 | **18** |
| Properties (P) | 9 | 9 | 0 |
| Success criteria (SC) | 9 | 9 | 0 |

Most of the orphans are labelling: T-1.5 obviously implements FR-06, T-3.3 obviously
implements FR-11, nobody was going to be confused. But the sweep found the case that
labelling would have caught and reading never did.

**H22.1 — `FR-18` (cooperative cancellation) is a `Must` with no owning task.**

Cancellation is specified in [§02.6](02-architecture.md#6-concurrency-model) — cancel the
task, cancel in-flight tools, flush the transcript, return `StopReason.CANCELLED`. It is in
the `StopReason` enum. It has a requirement marked **Must**.

**No task in [§11](11-implementation-plan.md) builds it.** A team working the plan
end to end would ship 1.0 without it, and nothing in the plan would have complained —
including the Round 12 Day-1 simulation, which walked tasks and therefore saw only what
tasks mentioned.

`FR-17` (streaming) is the same, at `Should`. `DeltaFn` exists in the provider protocol and
`stream=` exists in the API signature; nothing implements the path between them.

**H22.2 — Eighteen conformance tests are specified and unassigned.**

[§14.4](14-validation-plan.md#4-architecture-conformance-tests) defines 25 AC checks, and
the council has been treating them as the executable half of this package — the thing that
catches architectural drift when ordinary tests cannot. Seven are named in a task. The other
eighteen, including **AC-04 and AC-05** (the AST assertions that every model call is preceded
by a budget reservation and every tool execution by a policy verdict — the two invariants the
entire safety and cost argument rests on), are specified in a document and built by nobody.

A test that no task creates does not exist.

**Resolved, three parts:**

1. **T-0.10 — streaming and cancellation**, added to M0. Both touch the loop, so they belong
   with the loop rather than bolted on at M5. Cancellation is a `Must` and is now owned.
2. **T-3.6 — the conformance suite**, one task that builds all 25 AC checks, with AC-04 and
   AC-05 called out as the two that cannot be deferred.
3. **[§11 traceability matrix](11-implementation-plan.md#traceability-matrix)** — every FR,
   NFR, RT and AC mapped to its owning task. An unowned row is now visible at a glance
   instead of requiring the sweep that found this.

**H22.3 — small orphans, fixed in place.** `RT-06` (10 000-call loop), `RT-07` (unknown tool
requested), `RT-17` (a policy performing I/O) named in their tasks; `NFR-06` (Python version
matrix) and `NFR-09` (parallelism bound) given validation rows; `ADR-012` cited from
[§01](01-requirements.md) where it supersedes the original premise.

**The lesson, which is the same one as Round 20 in a different costume.** Round 20 found
types that no definition owned. Round 22 found requirements that no task owned. Both are the
same failure: **this package numbers things, and a numbered thing with no owner silently
becomes nobody's job.** The matrix is cheap; the sweep that produced it is now a CI check
(AC-26), because it will drift the moment someone adds a requirement without a task.

**Gate:** held at 16/16 after the fixes. Implementation Tasks was **No** for the duration of
H22.1 and H22.2.


---

### Round 23 — The last numeric cross-products, and convergence

Round 17 established the rule that numeric defaults are validated by arithmetic against
every other default they can meet. Rounds 17 and 18 applied it to budget × `max_tokens` and
budget × context window. Round 23 finished the cross-product.

| Pair | Result |
|---|---|
| `max_result_tokens` (4 000) × `max_parallel_tools` (8) | 32 000 tokens into context in one step, re-billed at ~$0.16/step thereafter. The default budget absorbs ~3 such steps and then stops. **Working as designed** — loud, bounded, and visible in `harness cost`. |
| `timeout_s` (30 s) × `max_parallel_tools` × `budget.steps` vs `wall_clock_s` (300 s) | Worst serial step is 240 s, so the wall clock binds first. **But `timeout_s` is never clamped to the remaining wall clock** — a tool starting with 20 s left runs its full 30 s and the run overshoots its stated wall-clock budget. |
| `max_pause_resumes` (5) vs `budget.steps` (20) | **Undefined**: nobody said whether a resumed `pause_turn` consumes a step. |

**H23.1 — a run can exceed its wall-clock budget by up to `timeout_s`.**

Small in magnitude, but it is a *ceiling* that does not hold, and this package makes a
point of the difference between a ceiling and a warning ([§07.1.1](07-cost.md#11-what-the-budget-does-not-cover-and-where-that-gap-is-closed)).
A budget axis that overshoots quietly is the thing ADR-005 exists to prevent, on a different
axis than the one that got the attention.

**Resolved:** the effective tool timeout is `min(spec.timeout_s, remaining_wall_clock)`. When
the remaining clock is the binding term, the tool result says so — `timed out: run wall-clock
budget reached` rather than `timed out after 30s`, because those call for different fixes.

**H23.2 — `pause_turn` resumes and the step counter.**

**Resolved:** a resume does **not** consume a step — it is a continuation of one model turn,
not a new one — but it **does** consume budget and wall clock, and is capped at 5. Charging a
step would let a server-tool-heavy turn exhaust `budget.steps` without the agent making any
progress; charging nothing at all would leave a loop bounded only by the cap. Recorded
because the two readings differ and both are defensible until someone writes one down.


---

### Round 24 — Executing the design

Round 23 stopped reviewing on the argument that "the next class of defect is waiting in
M0, not in another round of review." Round 24 tested that claim by building the M0 slice —
`@tool`, `ToolSet`, `Ledger`, the policy engine, the taint tracker, `Secret`, the run loop,
`Agent` — and running the [§14.5](14-validation-plan.md#5-acceptance-walkthrough-day-1--first-deployment)
walkthrough against it.

**The claim held. Six defects, every one of which had survived every prior reading.**

**H24.1 — Two Round 19 fixes are mutually incompatible.**
IDL-32 set `Secret.__hash__ = None`. IDL-33 put secrets in a `WeakSet`. A `WeakSet` hashes
its members. The first line of the first test raised `TypeError: unhashable type: 'Secret'`.
Both decisions were made in the same round, by the round whose entire subject was executing
contracts instead of reading them. → **ADR-024**: an `id()`-keyed dict of `weakref.ref` with
a finalizer callback. Same lifetime guarantee, never hashes the referent.

**H24.2 — The friendly positional-argument error never fires.**
IDL-21 accepts `*args` in order to reject them readably. Python validates required
keyword-only parameters **before** the body runs, so `Agent("Helper", "tell jokes")` raised
`TypeError: __init__() missing 2 required keyword-only arguments` — Python's message, not
ours. Register entry #39 documented a defense that did not exist. → sentinel defaults plus
explicit checks, giving a distinct message for the positional case and for genuinely
missing arguments.

**H24.3 — Zero-cost testing and ADR-017 contradict each other.**
`FakeModel` is priced at zero (SC-5). ADR-017 derives `max_tokens` by dividing by the output
price. Every test using the fake died on `decimal.DivisionByZero`. The library's own
testing story could not run against the library's own budget mechanism.

**H24.4 — The cache linter is expensive and misses the common case.**
T-2.3's contract says it "adds < 5 ms when the prompt is static". IDL-17 mandates a 150 ms
sleep between renders. Both were approved. Measured: 150 ms on **every** construction —
150 seconds across P-1's thousand runs, which is what made the property suite time out.

Then the worse half. What a 150 ms sleep actually detects:

| pattern | caught |
|---|:--:|
| `uuid4()` | ✅ |
| `time.time()` | ✅ |
| `datetime.now()` to seconds | ❌ |
| `date.today()` | ❌ |

It costs 150 ms and **misses the two most likely cases**, because 150 ms rarely crosses a
second boundary and never crosses midnight. → **ADR-025**: render twice back-to-back at
construction (free; catches everything that changes per call) **plus** compare the prefix
actually sent across the first two real calls (free; catches time drift with a real gap).
Construction went from 150 ms to 0.02 ms — 7 500× — and the coverage went up.

**H24.5 — SC-2 is false as written, and RISK-03's mitigation is wrong.**

The property test found **380 budget violations in 1 000 runs**, one at **27× the limit**.

The pre-flight ceiling is only as good as `count_input_tokens`. RISK-03 anticipated this and
recorded it as mitigated because "worst-case estimation over-counts by construction". That
sentence is false: the worst case over-counts **output** (`max_tokens`), and does nothing
about an **input** under-count. Nobody had separated the two terms.

A library cannot know the true input cost before making the call, so **"actual spend never
exceeds the budget" is not achievable** and should never have been written as a success
criterion. → **ADR-026**, three parts:

1. A 15 % margin on counted input.
2. Calibration from the previous response's real input, ratcheting **upward only** — an
   under-count is the dangerous direction; an over-count only wastes headroom.
3. A **hard local upper bound**: no tokenizer emits more tokens than the prompt has
   characters. When even that bound fits the remaining budget, the reservation uses it and
   the ceiling is **exact** for that call. Most real calls qualify.

And SC-2 restated honestly as two claims:

- **SC-2a (exact):** the harness never *authorizes* a call whose estimate exceeds the
  remaining budget, and authorizes nothing further once spend crosses it.
- **SC-2b (bounded):** actual spend may exceed the budget only by one call's input-count
  error.

| | violations | worst overshoot |
|---|---|---|
| As designed | 380 / 1 000 | 27× |
| + margin and calibration | 115 / 1 000 | 8.4× |
| + hard character bound | **3 / 3 000** | **1.008×** |

Measured with adversarial 10× token-count drift injected. Against a real provider's own
counting endpoint the error is a few per cent, not 10×.

**H24.6 — the prefix watcher must span runs.** Written per-run, it compared a prefix only
against itself. Time-based drift appears *between* calls, so it lives on the `Agent`.

**H24.7 — `tools` and `policy` import each other.** `EFFECT_PROFILES` needs `Verdict`;
`Policy` needs `ToolSpec`. The module map showed both without noting the cycle. Resolved by
keeping `Verdict` in `policy/base.py` and guarding the reverse import with `TYPE_CHECKING`.

---

## 2.2 What executing the design proved

The M0 slice is ~1 200 lines. Both suites run **offline, with no API key, no pytest, in
under a second** — SC-5 demonstrated rather than asserted.

| | Rounds 1–23 (reading) | Round 24 (executing) |
|---|---|---|
| Rounds spent | 23 | 1 |
| Defects found | 20 | 7 |
| Defects that falsified a stated success criterion | 0 | **1 (SC-2)** |
| Defects in decisions made by the round about executing contracts | — | **2 (both from Round 19)** |

The two Round 19 findings are the most useful thing in this table. Round 19 adopted the rule
"a type contract is reviewed by writing the twenty lines that exercise it" — and then made
two decisions without writing them, and both were wrong. **A rule that is adopted but not
applied is indistinguishable from one that was never adopted.**

The council's Round 23 reasoning for stopping was correct in direction and wrong in
magnitude: it expected the next defects in M0, and there were seven of them, including one
that falsified the package's central cost guarantee.

**Standing recommendation, replacing the Round 23 convergence note.** The design is not
finished by more review. It is finished by building M1 and M2 the same way this round built
M0 — where the taint lattice, the policy composition and the caching benchmark will meet
execution for the first time.


---

### Round 25 — Executing M1: the red-team suite meets code

Round 24's standing recommendation was to build M1 the same way it built M0. The 17
red-team scenarios of [§06.8](06-safety.md#8-red-team-suite-m1-deliverable-in-ci) were
written as executable tests and run against the safety core. **Three defects, one of them
a live security hole.**

**H25.1 — The library's most likely mistake produces its least comprehensible error.**

IDL-05 mandates `frozen=True, slots=True` on **every** data class. Assigning a declared
field raises a clean `FrozenInstanceError`. Assigning a name that is *not* a field — a typo,
or attaching state to a tool — raises:

```
TypeError: super(type, obj): obj must be an instance or subtype of type
```

That is CPython's: `slots=True` rebuilds the class, and the generated `__setattr__`'s
zero-arg `super()` still closes over the original. It surfaced on the first line of the
red-team file, where the test attached a call log to a tool.

A package whose stated standard is that every error names what happened, where, and the fix
([§03.8](03-public-api.md#8-error-message-standard)) shipped a blanket rule producing a
message about `super()` and types for the mistake people make most. → **`@value`**, a
one-place decorator replacing `__setattr__`/`__delattr__` with a readable message, the field
list, and a did-you-mean. Applied everywhere IDL-05 applies. → **ADR-027**.

**H25.2 — RT-13 fails: a secret reaches the model unredacted.**

A `Secret` constructed inside a tool, revealed, and mentioned in an exception message
arrived at the model in full:

```
RuntimeError: failed while holding sk-ant-INSIDE-TRACEBACK
```

The cause is ADR-024 (Round 24) interacting with IDL-33 (Round 19). The registry holds
**weak** references, so a per-request secret dies with the tool's frame — but the *string it
was formatted into* outlives it, and by the time `redact()` runs there is nothing left to
match against. Round 19 chose weak retention for lifetime hygiene and did not notice it
defeats redaction for exactly the short-lived, per-request secrets that dominate in a
server. Round 24 reworked the registry's mechanism without revisiting the choice.

The Security Engineer's framing: **a redactor that forgets faster than the data it protects
travels is not a redactor.**

**Resolved** — retention scoped to the *run*, not to the object and not to the process:
`reveal()` registers the value with the active run's redaction scope, which is opened for
the run and cleared when it ends. That is exactly the window in which anything derived from
the value can still be written out. Demonstrated: identical code leaks outside the scope,
redacts inside it, and releases retention after. → **ADR-028**.

**H25.3 — Tool errors were redacted at the wrong boundary.**

Redaction was specified "on transcript write, before the bytes exist"
([§05.2](05-data-and-state.md#2-transcript-format)). But a tool error is returned **to the
model**, which is a wider audience than a log file and one the transcript boundary never
sees. Redaction moved to the tool-result boundary as well.

**What passed, and why it is worth stating.** RT-01 and RT-02 pass by *construction-time
rejection* rather than runtime denial — F9.1 working exactly as designed, a year of rounds
later. RT-11's lattice, RT-14's egress check, and the approval path (sync callback, async
callback, and no callback) all held on first execution.


---

### Round 26 — Executing M2: every cost claim meets a measurement

M2 is where the package's cost argument stops being prose. Four defects, and the first one
is about the benchmark rather than the code.

**H26.1 — The first version of the SC-4 benchmark reported 95 % while testing nothing.**

Written as ten `run()` calls against the same agent, it reported **99.4 %** and passed. It
was measuring a *constant prompt*: each call started with no history, so the bytes were
identical every turn and the cache hit trivially. SC-4 says *10-turn conversation*, and a
conversation **grows** — that growth is the entire difficulty.

Rewritten to carry turns 0..N-1 forward, the real number was **71.8 %, degrading 90.8 % →
61.7 %** as the conversation lengthened. The threshold is 90 %.

The Test Architect's note, which the council accepted as the round's main lesson: **a
benchmark that cannot fail is indistinguishable from one that passes.** The first version
had no way to report a bad result. It is now pinned by a second assertion — the hit rate
must not *degrade* with conversation length — because that is the shape the real failure had,
and a single threshold would not have caught a design that merely stayed flat at 90 %.

**H26.2 — Only the system prefix was cached; §07.2.2's multi-turn breakpoint was never built.**

The design specifies a breakpoint on the last content block of the most recent turn, so hits
accrue as the conversation grows. The assembler placed one on the system block and nothing
else, so the entire conversation was re-billed every turn.

Implementing it naively still failed, at 71.5 %. **A cache entry is only *read* at a
breakpoint present in the current request** — marking only the newest message writes an
entry that nothing ever reads back. The fix keeps a rolling read point at the position the
previous request marked. Two message breakpoints plus the system one stays inside the
provider's limit of four.

| | hit rate on turns 3+ | trend |
|---|---|---|
| system breakpoint only | 71.8 % | degrades to 61.7 % |
| + newest-message breakpoint | 71.5 % | still degrades |
| + rolling read point | **95.3 %** | **improves to 96.0 %** |

Input tokens per turn went from growing without bound to flat at ~153.

**H26.3 — `max_parallel_tools` is accepted, stored, documented, and never read.**

Measured peak concurrency: **30, against a configured limit of 4.** The parameter is on
`Agent`, NFR-09 states the bound, [§06.7](06-safety.md#7-loop-and-resource-control) lists it
as the control preventing "fork-bombing a downstream service", and Round 22's traceability
matrix shows NFR-09 owned by T-2.7.

**A configuration parameter that silently does nothing is worse than one that does not
exist**: it is a protection people will rely on.

**H26.4 — Duplicate-call suppression (T-2.5) was specified and never implemented.** Five
identical calls in one step executed five times. Now run once, with every `tool_use` still
receiving its own `tool_result` (invariant I-3 does not bend for an optimization).

**The finding that generalizes.** Round 22 swept for numbered artifacts that no task owned
and fixed every orphan. Both H26.3 and H26.4 were **owned** — NFR-09 → T-2.7, T-2.5 in the
plan — and both were unimplemented.

> **Ownership is not implementation, and a traceability matrix cannot tell the difference.**

Only running the behavior can. AC-31 now asserts that every public parameter is read
somewhere in the package, which is the cheap mechanical half of that gap.


---

### Round 27 — Executing M3: three of fifteen events could never be emitted

M3 built the transcript, resume, the console exporter, and the observability wiring. The
transcript and resume tests passed on first run — the first milestone to do so. The
observability audit did not.

**H27.1 — Three of the fifteen event kinds have no emit site.**

A test that every *emitted* kind is in the closed taxonomy passed easily, because it can
only check what is emitted. Asking the inverse question — **which kinds does the code never
emit?** — returned three:

| kind | why |
|---|---|
| `step.finished` | The loop emitted `step.started` and never its pair. Anything deriving step duration from the stream could not. |
| `error.raised` | Emitted only by the event bus's exporter-failure path. Never by the loop for a tool failure or a provider error — the two cases anyone reading a transcript is looking for. |
| `context.managed` | **`window.manage()` was built, unit-tested and never called from the run loop.** |

The third is Round 26's own finding — *ownership is not implementation* — recurring one
round after being named, in code the council itself had just written. **Naming a failure
class does not prevent it.** What prevents it is the inverse test, which is now AC-32: every
`EventKind` must have an emit site.

**H27.2 — And then the wiring was still wrong.** With `window.manage()` called, it still
never fired. It was passed the token count of the request *already sent* — a number that
predates the tool results just appended, which are exactly what makes the window grow.

**H27.3 — Then the arithmetic said it can never fire at all.**

With the wiring corrected it still did not trigger, and multiplying the defaults out
explained why:

```
max_result_tokens (4 000)  x  budget.steps (20)   =  80 000 tokens, maximum
editing threshold          =  0.60 x 200 000      = 120 000 tokens, smallest window
```

**Context management cannot trigger under any default configuration.** It is dead by
arithmetic — a documented, wired, tested subsystem that nothing shipped can reach.

This is Round 17's own rule (*numeric defaults are validated by arithmetic, not by review*)
applied to a triple nobody had multiplied. P-8 checks that shipped defaults are mutually
**consistent**; it never asked whether they are sufficient to **reach** each conditional
feature.

**Resolved, honestly rather than by deleting or by inflating a threshold:**

- The feature is genuinely useful at raised limits — a research agent at
  `budget="$50, 200 steps"` with `max_result_tokens=20_000` reaches it — so it stays.
- [§07.3](07-cost.md#3-token-discipline) now states plainly that it is **not reachable on
  the shipped defaults**, and why that ordering is correct: the budget and step limits are
  cheaper limits to hit, and they should bind first.
- Its test exercises a reachable configuration instead of pretending a default one reaches
  it.
- **Property P-10** fails the build if the arithmetic and that documentation ever disagree.

**The general rule, added to the package.** A conditional subsystem must either be
reachable from the shipped defaults, or declare in the documentation that it is not. A
feature that no configuration can reach, described as if it were active, is worse than no
feature: it is a claim.


---

### Round 28 — Executing M4: the budget ceiling leaked through a documented feature

M4 built the two `Store` implementations, the plugin registry and subagents. Memory and
plugins passed on first run. Subagents did not — and the first suite written for them
passed too, which is the more useful half of this round.

**H28.1 — The subagent tests passed while the two claims that matter went untested.**

Twenty-three tests covered effect maximization, delegation, and graceful child failure. All
green. None of them asked the two things [§06.4](06-safety.md#4-least-privilege) actually
*claims*:

> *the child's budget is capped by the parent's remaining budget · the child's safety level
> cannot be lower than the parent's*

Asked directly, both were false. A parent with a **$0.10** budget called a **$5** child six
times, spent **$30**, and reported `cost = $0.0000` — each child kept an independent ledger
and nothing settled back. **SC-2a's ceiling leaked entirely through a documented feature.**
And a `safety="strict"` parent could wrap a `safety="standard"` child, silently dropping
the safety level for exactly the work it delegated.

This is the third consecutive milestone where a documented guarantee turned out to be
prose. The pattern is now specific enough to name: **a test suite written by whoever wrote
the feature tests what the feature does, not what the document promises.** M5's checklist
adds the inverse pass — read the guarantee, then write the test that would falsify it.

**H28.2 — Then the cap was read instead of held.**

With capping added, parallel children each *read* the same remaining budget and each claimed
all of it — a TOCTOU on the ledger. `$0.50` became `$0.54`. Fixed by **holding** the
headroom before the child runs and releasing it against the child's actual spend, so
parallel children divide the budget instead of multiplying it.

**H28.3 — The residual bound compounds with delegation depth, and nobody had said so.**

SC-2b bounds overshoot by *one call's* input-count error — on **one** ledger. A subagent
runs on its own, so a parent's worst case is its own error plus each child's. Measured ≤
1.25× at depth 1. Now stated in [§07.1](07-cost.md#1-the-budget-is-a-ceiling-not-an-alert)
rather than discovered, with the note that delegation cannot cycle: an `Agent` is frozen
before it can be wrapped.

**H28.4 — `run.py` crossed IDL-13's ceiling, and the ceiling did its job.**

Subagent budget binding pushed it past 250 code lines. IDL-13 says an overrun is a **design
signal**, not something to refactor around, so the signal was taken rather than the limit
raised: tool execution moved to `dispatch.py`. `run.py` is now the state machine at **137**
lines; `dispatch.py` owns policy resolution, scheduling, timeouts, truncation, taint and
subagent binding at **163**.

This is the first round where a rule written early prevented a decision rather than
diagnosing one afterwards — worth recording, because most of this package's rules have so
far only ever explained damage.

**A smaller finding worth keeping.** Passing the parent's remaining budget into the subagent
closure made it a tool parameter, and the schema builder demanded an annotation for it —
which would have put a harness-internal field in the schema the *model* sees, where the
model could set it. Now: **a parameter whose name starts with `_` is never model-facing**,
is stripped from the schema, and is filtered out of model-supplied arguments. The awkwardness
was the signal; the fix is a rule.


---

### Round 29 — Executing M5: the tutorial becomes a test

M5 built the scaffold, the credential flow, and — the point of the round — turned
[§15](15-first-agent.md) from a document into an executable specification. Every error
message it shows a child is now asserted against the message the code actually produces.

**The result is not what the previous five rounds would predict: the tutorial held.** All
six error messages matched. The concept budget held (the first agent introduces exactly
`name`, `job`, `budget`). The jargon ban held across the child-facing body. Every complete
program parses; every `@tool` it shows decorates.

**H29.1 — But the first version of the test file failed nine times, and every failure was
mine.**

- The fence regex `` ```\n(.*?)``` `` matched the prose *between* a closing fence and the
  next opening one, so "the error message block" was usually a paragraph.
- It asserted every Python block is a standalone program. Several are deliberately
  fragments — a line to change, a keyword argument to add. That is pedagogy, not a defect.
- The jargon check scanned the reviewer preamble, which legitimately *names* the jargon the
  tutorial avoids ("no `async`, no type hints") in order to promise avoiding it.
- One needle (`"can't undo"`) matched two different error blocks.
- It counted *blocks* containing tools rather than *tools*, and skipped the tutorial's
  headline example because that block also builds an `Agent` — leaving the most important
  code in the document untested while reporting a pass.

Nine red tests, zero product defects. The Test Architect's note: **a test that fails for
its own reasons is as misleading as one that passes for none.** Rounds 24–28 built
confidence that a failing test means a real defect; this round is the counterexample, and
it is recorded so that confidence stays calibrated.

The last item is the one worth keeping. The filter `if "Agent(" in b: continue` was written
to skip blocks that need a provider — and its effect was to skip the single most important
example in the tutorial. **An exclusion written for convenience silently narrowed what was
verified.** It now stubs the provider and executes the block instead.

**H29.2 — `harness setup` validates before it stores, and that is testable without a key.**

The credential flow is expressed as three injected functions (read, validate, write), so
"an invalid key is never written to disk" is an ordinary offline test. IDL-25 asked for
validation-before-storage; making it injectable is what made it verifiable.

**H29.3 — `harness new` writes both files or neither.** Register #36 says the `.gitignore`
must be written by the same command that creates the file needing it. Tested: an existing
`.gitignore` is extended rather than clobbered, and the pair is always produced together.


---

### Round 30 — Auditing promises against the implementation

Round 29 reported zero product defects and the council took that as convergence. Round 30
asked a question none of the build rounds had: **not "does what we built work?" but "does
everything the package promises exist at all?"**

A mechanical sweep — every `import harness…` in the docs, every `` `harness <cmd>` `` in
the docs, every `Agent` parameter — against the code:

| Promised | Reality |
|---|---|
| `from harness.tools.web import search` — **§15 tells a child to type this** | `ModuleNotFoundError` |
| `harness chat` · `run` · `trace` · `cost` · `doctor` | **5 of 7 CLI commands unimplemented** |
| `models/anthropic.py` | **did not exist** |
| `harness.testing` | an empty namespace package |
| `Agent(returns=...)` (ADR-022) | accepted and ignored — never reached the request |
| OTel exporter | did not exist |

**The library could not talk to a real model.** Every one of the 111 tests ran against
`FakeModel`, all seven suites were green, and there was no provider. The four-command cold
start — the thing SC-1b measures — would have failed at the fourth command.

And the first item is the worst: §15's "a tool that comes with Harness" section, the step
where a child gives their agent its first real ability, imported a module that was never
written.

**Why five build rounds missed all of it.** Each round built a milestone and tested what it
built. Nothing tested what the *documents* said users could rely on. Round 26 named
*ownership is not implementation*; Round 27 found the same class recurring; both were about
features with an owning task. **This is the class below that: things with no task at all,
promised only in prose.** The traceability matrix could not catch them because they were
never numbered.

**Resolved by building them**, not by trimming the docs: the built-in tool packs
(`web`, `files`, `calc` — pre-classified so the beginner path never writes `@tool`), the
Anthropic provider, the testing kit, `Chat` with its session ledger, `returns=` wired
through to `output_format`, and the five CLI commands.

Two details worth keeping:

- **`calculate` does not use `eval`.** Evaluating model-supplied text is arbitrary code
  execution, which would make it a `danger` tool wearing a `read` label — precisely the
  mislabelling the effect classes exist to prevent. It walks an AST over numbers and seven
  operators instead.
- **The Anthropic adapter never fails a run on a token-count call.** If counting errors, it
  falls back to a character-derived over-estimate, which is a hard upper bound on tokens
  (ADR-026), so the budget ceiling stays safe rather than becoming unenforceable.

**AC-39 and AC-40** now run this sweep in CI: every module the docs import must import, and
every CLI command the docs name must exist.

**The rule this produced.** A test suite verifies what was built. A traceability matrix
verifies what was assigned. **Neither reads the documentation as a promise.** So the
documentation is now a test input: its imports, its commands, and its error messages are all
asserted against the code.


---

### Round 31 — Measuring the half of SC-1b that does not need children

SC-1b has been the package's one open criterion since Round 16, described each round as
"cannot be closed by code". Round 31 asked whether that was entirely true. **It was not.**

A child study cannot succeed if the text is unreadable, and readability is measurable
today. Flesch–Kincaid grade level over §15 and over every error a child can reach:

| | before | after |
|---|---|---|
| §15 child-facing body | grade **4.2** | 4.2 |
| missing `effect=` | grade **12.5** | 3.3 |
| `effect=` typo | grade **11.2** | 3.1 |
| positional `Agent(...)` | grade **14.9** | 3.1 |
| unsafe tool set | grade **7.7** | 4.9 |
| worst case | **14.9** | **4.9** |

The tutorial read at age nine. **The messages a child actually hits read at university
level.**

**H31.1 — And the reason was that the code emitted different text from what §15 promises.**

§15 shows:

```
  read      only looks at things
  write     changes something you could undo
```

The code emitted:

```
  read      only looks at things          (parallel, retryable, auto-allowed)
  write     changes something reversibly  (serial, not retried)
```

Those parentheticals are **the five behaviours ADR-003 derives precisely so the author never
has to think about them**. Putting them in the error re-exposes the complexity the effect
classes exist to hide — to the one audience least able to use it.

**The Round 29 lesson recurring, one round after being recorded.** M5's tutorial-as-spec
test passed because it asserted that the words `read`, `write`, `external` and `danger`
appear in both texts. **A substring check verifies vocabulary, not the message**, so the
code could emit a completely different, grade-12.5 error and stay green. The test now
compares the whole message line for line, ignoring only the `-> docs/…` pointer.

**H31.2 — §15 was corrected by the code, for the first time.** The whole-message comparison
found the tutorial promising *"emailing your stuff to a stranger"* — wording the real
message cannot use, because it does not know the danger tool is an emailer. §15 is the
specification for **tone, structure and vocabulary**; where it guessed at a detail the code
knows better, the code wins. Recorded because it inverts the rule the council had been
applying, and because the comparison is what made it visible rather than silent.

**H31.3 — Two measurement artifacts, found by disbelieving a number.** The first grades were
inflated by counting indented code examples as prose, and by treating a four-row list with
no full stops as one enormous sentence. Both were fixed before the numbers were trusted. A
metric that has not been argued with is not evidence — the same caution Round 26 applied to
a benchmark that could not fail.

---

### The instrument: [§16 — SC-1b Field Kit](16-sc1b-field-kit.md)

**A study nobody can run is not a gate, it is a wish.** SC-1b has been cited as blocking in
fifteen rounds without anyone being able to execute it, so this round produced the runnable
instrument: consent and assent, a facilitator script with an explicit *may / may not* table,
a two-minute stall rule, an observation sheet that captures questions **verbatim**, scoring,
and the mapping from each observation to a decision.

Three details the council insisted on:

- **The adult performs the API-key step**, and it is excluded from the timing. It needs an
  account and a payment method, it is the one acknowledged gap in the child-facing path, and
  pretending otherwise would measure the wrong thing.
- **The facilitator wants it to work.** That bias is why the script forbids explaining, why
  the stall rule is a full two minutes of silence, and why questions are recorded verbatim —
  a paraphrase loses the vocabulary mismatch, which is usually the actual finding.
- **§7 states what the kit cannot tell you.** n = 3 detects gross failures and cannot rank
  two designs. A pass means *this did not fail badly for three children* — the honest claim,
  and the one the council will record.

**SC-1c** is added as the mechanical half, blocking in CI: §15 and every child-reachable
error must read at grade ≤ 5.0, and no error may use internal vocabulary (`parallel`,
`retryable`, `untrusted`, `taint`, `ledger`, `schema`) in its prose.

---

### Round 32 — `as_tool()` was unusable outside ASCII

The subagent name `'ask_chuyên_gia_chính_sách'` fails the tool-name pattern the providers
accept, so **any council of Vietnamese-, Chinese- or Arabic-named subagents could not be
built at all** — a library that ships a Vietnamese tutorial and refuses Vietnamese names.

**H32.1 — The error suggested the name that had just failed.** It read *"use
name='ask_chuyên_gia_chính_sách'"*, which fails identically. An error whose remedy
reproduces the error is worse than no error: it sends the reader looking for a second
cause that does not exist.

**H32.2 — The first fix was wrong, and the test that caught it was an inverse question.**
Slugging the composed string `"ask <name>"` kept the ASCII prefix and dropped everything
else, so `客户支持` and `客服助手` both became `ask` — two different subagents, one name,
silently. `slug()` now normalises (NFKD) the name **alone** and appends a digest when the
result is empty or collides. The question that found it was not *"does slugging work?"*
but *"what two different inputs can produce one output?"*

---

### Round 33 — `returns=` was accepted and never parsed

`Agent(returns=KetLuan)` reached the request, shaped the model's output format, and
**`Result.value` was `None` on every run.** A parameter that is accepted, documented and
half-honoured is worse than one that raises: the caller writes code against a value that
is always absent and reads it as the model's failure.

**H33.1 — Non-ASCII tool results cost 2.2× to 4.8× more than they should.**
`json.dumps` defaults to `ensure_ascii=True`, so `"đã giao"` went to the model as
`"\u0111\u00e3 giao"` — every Vietnamese character billed as six ASCII ones, and every
Japanese one worse. Measured: **2.2× for Vietnamese, 4.8× for Japanese**, on the cost
invariant, in a library whose tutorial is Vietnamese. Three call sites had the same
default (`dispatch.py`, `transcript.py`, `assembler.py`); all three now pass
`ensure_ascii=False`. Found by asking what a default does to an audience the defaults
were not chosen for.

---

### Round 34 — A workflow state machine leaked between customers

§04.3 says a policy's `check` is pure. §10.5 shows policies constructed at module scope.
Both were written by the council; together they mean **one `QuyTrinhHoanTien` instance
serves every customer, and customer B inherits customer A's position in the refund
workflow.** Two documents, each correct, contradicting each other in production.

Policies may now be passed as a class (a factory, constructed per run); a shared instance
that mutates during a run raises rather than leaking.

**H34.1 — The first fix was wrong for a reason worth recording.** The discriminator
`hasattr(p, "check")` is true of a *class* as well as an instance, so every factory was
treated as an already-built policy and nothing changed. `inspect.ismethod` is the correct
test: it is true only of a bound method, which only an instance has.

---

### Round 35 — The LangGraph mandate, and what porting the loop cost

The user mandated building on LangChain/LangGraph, reversing the council's Round-34
recommendation. The council recorded the reversal in one sentence and did not re-litigate
it. The engineering question was the only one left: **ADR-001 says the harness owns the
loop because it is the only place a budget check can precede a model call and a permission
check can precede a tool call. What happens to that argument when someone else owns the
loop?**

**The resolution preserves the reasoning and strengthens the guarantee: the choke point
becomes a graph edge instead of a line of code.** `unguarded_paths()` walks the compiled
graph from `__start__` refusing to traverse a gate, and reports any guarded node still
reachable. That is a reachability proof over the real execution structure — it holds for
paths no test walks, which the AST tests over our own source never could.

```
node             : ['budget', 'model', 'policy', 'approve', 'tools', 'finish']
vào 'model' từ   : ['budget']
vào 'tools' từ   : ['approve', 'policy']
cổng bị đi vòng  : KHÔNG
```

**H35.1 — The port reintroduced RT-13: a per-request secret reached the model in
cleartext.** `redaction_scope()` was never opened on the graph, so a tool that builds a
short-lived `Secret`, reveals it and returns text derived from it sent that text
unredacted — the exact Round-25 defect, in new code, five rounds later. The scope now
opens at the tool-result boundary, inside the node, **not around the caller's `invoke()`**:
a caller drives a compiled graph directly, and a guarantee that depends on the caller
remembering something is not a guarantee.

**H35.2 — `build_agent` accepted a tool set `Agent` refuses.** `external` + irreversible
in one tool set is refused at construction by the hand-written backend (F9.1). The graph
accepted it and relied on the taint policy to catch it mid-run — a demotion from **Prevent**
to **Detect** on the Poka-Yoke ladder, which is exactly the trade §08 exists to forbid.

**H35.3 — Six of fifteen event kinds could never be emitted.** `run.started`,
`run.finished`, `step.started`, `step.finished`, `tool.requested` and `context.managed`
had no reachable emit site on the graph — Round 27's defect, in a new backend. `run.finished`
was missing because every exit routed straight to `END`, so each branch had to remember to
emit it. The fix is structural rather than diligent: **one `finish` node that every exit
routes through**, added to the `GUARDED` table alongside the two gates, so the reachability
proof covers the closing event too.

**H35.4 — Three silent divergences in the public API.** The approval callback took
`(question)` on the graph against the documented `(ToolCall, RunContext)`; a missing
callback blocked on `interrupt()` where the loop applies the safety rule; and the
observability seam was spelled `bus=` on one backend and `exporters=` on the other. The
ASK resolution is now delegated to `PolicyEngine.resolve` — **one implementation of the
rule, not two** — and `approve=INTERRUPT` is an additional mode, not a different rule.

**H35.5 — The durable state could not be written.** `_pending` carried a `ToolSpec`, which
holds a callable; the checkpointer raised *"Type is not msgpack serializable"*. Durability
is the main thing this platform buys, so it failed at exactly the feature it was adopted
for. State now carries the tool **name**, and the spec is looked up from the frozen tool
set — which also means a resumed run whose tool set has changed gets a clean error instead
of a stale closure.

**H35.6 — And the test that was supposed to catch the first of these passed for the wrong
reason.** `_pending` was not declared in `AgentState`, and LangGraph **silently discards**
any state key absent from the schema — so no tool ran at all, and the assertion that a
denied tool did not run was true because nothing ran. A green test asserting an absence is
worth nothing until you have also seen the presence.

---

### The instrument: `tests/test_parity.py`

Every one of H35.1–H35.4 is the same defect class: **two implementations of one rule, each
with its own tests, drifting invisibly.** Naming the class does not prevent it — Round 27
established that only the inverse test does. So the scenarios now live once and run against
both backends, and a row that differs is a defect in whichever backend is wrong, never a
difference to document:

| rule | loop | graph |
|---|:--:|:--:|
| a read tool runs | ✓ | ✓ |
| a danger tool is refused without an approver | ✓ | ✓ |
| an approver lets it through | ✓ | ✓ |
| external output taints the run, `accepts_tainted` passes | ✓ | ✓ |
| the unsafe pair is refused **at construction** | ✓ | ✓ |
| a short-lived secret is redacted (RT-13) | ✓ | ✓ |
| the step limit stops the run | ✓ | ✓ |
| the USD budget stops the run | ✓ | ✓ |
| the same event kinds are emitted — **observed, not grepped** | ✓ | ✓ |

Two fixture defects in the suite itself are recorded because they would have made it
report agreement it had not measured: `FakeModel` prices everything at zero, so the loop
could never exhaust a USD budget with it (the shipped `PricedFake` is the right fixture);
and the two backends were being given different model prices, so the budget row was
comparing fixtures rather than rules.

**The count: eleven build rounds, thirty-plus defects, four of them security.** Three of
Round 35's six were defects the council had already found, fixed, and written down — in
Rounds 25, 27 and 19. **A defect class that has been named and fixed in one implementation
is not fixed in the next one**, and the only thing that catches it is a test that runs
against both.

---

### Round 36 — OpenViking, and what a memory system does to the taint lattice

`openvikking` in the mandate is `openviking` — one `k`. The council had recorded it as
unidentifiable (OI-9); it is [volcengine/OpenViking](https://github.com/volcengine/OpenViking),
a context database for agents that presents memories, resources and skills as a virtual
filesystem under `viking://`.

**It lands on a seam the design already reserved.** [§04.5](04-interfaces.md#5-store) has
said since Round 7 that FTS5 keyword search is not semantic recall, and that *"a user who
wants it implements `Store`."* This is that user. No new abstraction was needed, which is
the plugin-boundary test ([§02.4](02-architecture.md#4-what-is-a-plugin--the-test)) paying
out: the seam was justified by naming two real implementations, and a third arrived.

**H36.1 — A memory system is an untrusted input, and classifying it as `read` puts a hole
in the taint lattice the size of the memory system.** A context database ingests web pages
(`ov add-resource https://…`). Everything it hands back may be attacker-authored. The
obvious classification for `recall` is `read` — it reads, after all — and it is wrong:
`read` does not taint, so a poisoned memory would buy `danger`-tool privileges for the
rest of the run. **`recall` ships as `external`.**

The decision is shipped *in* `VikingStore.tools()` rather than left to each author,
because it is not a judgement call an author can be expected to get right — it depends on
knowing what the database ingested, months ago, on somebody else's machine. Poka-Yoke
grade: **Impossible-to-omit** rather than **Documented**.

Measured end to end: a recall taints the run, and `recall` next to an irreversible tool is
refused at construction (F9.1) — which only works *because* the classification ships with
the store.

**H36.2 — The parse tests passed against an envelope the server never sends.** The first
version of the test file invented `{"results": [...]}`. The SDK's real contract is
`{"status": …, "result": …}` for success and `{"status": "error", "error": {"code": …}}`
for failure, mapped through the SDK's own `ERROR_CODE_TO_EXCEPTION`. Every parse test was
green against a shape that does not exist. **Round 26's benchmark defect, in a new place:
a fixture the author invented cannot falsify the author's belief.** The tests now drive
the real `openviking_sdk` client over a stub transport, so the SDK's URL building, request
shaping, response parsing and error mapping all execute.

**H36.3 — And the failure classification was guessing.** The first cut matched exception
*names* and treated anything unrecognised as absent — so an unknown failure would have
returned "nothing remembered", and the agent would have told a customer their order does
not exist. Fail-open, in the direction where the wrong answer is plausible enough to go
unnoticed. Now: `NOT_FOUND`/`INVALID_URI` are absence; `UNAUTHENTICATED`/`PERMISSION_DENIED`
are a `ConfigError` because retrying cannot fix a wrong key; **everything else, including a
code this binding has never heard of, is a failure.** The same rule as IDL-30 for an
unrecognised provider stop reason, arrived at independently and then recognised.

**H36.4 — A key is not a path.** Keys are interpolated into a `viking://` URI, so
`key="../../resources"` reads outside its namespace. Refused at the boundary against a
strict pattern rather than escaped — escaping is a thing you can get subtly wrong, refusing
is not — and a test asserts the rejected key never reaches the wire.

**H36.5 — The client holds capabilities the store must not.** `SyncHTTPClient` exposes
`admin_create_account`, `admin_regenerate_key`, `rm`, `delete_session`, `import_ovpack`.
A store binding that hands those to a model-facing tool is a least-privilege violation
with a prompt injection on the other end of it. `ALLOWED_CALLS` is a fixed set of six, and
every request goes through one function that enforces it. `delete()` writes an empty value
rather than calling `rm`: emptying is reversible in the database's own history, `rm` is not.

**The dependency finding, measured: 185 versus 9.** The `openviking` package resolves to
**185 transitive packages** — a web crawler (scrapy, twisted), two other LLM SDKs (openai,
litellm), tree-sitter for ten languages, fastapi, uvicorn, pdfplumber, python-pptx. The
`openviking-sdk` client resolves to **nine**, and needs only `httpx`. So the harness
depends on the client, as an extra:

> **A database is a process you run, not a library you vendor.** Nobody links Postgres
> into their application to use it; the 185 packages are the server's business, and the
> server is a separate process by design.

### What the council did *not* build, and why

`get_session_context(session_id, token_budget=…)` is OpenViking's own context assembler,
and it overlaps [§07](07-cost.md)'s. Integrating it would mean two things deciding what
goes in the window, which is R-17's defect class with a bigger blast radius. **Not built.**
The `Store` seam is the boundary the design already justified; widening the integration
because the vendor offers more is how a library acquires a second architecture.

**The limit of this round, stated plainly: none of it has run against a live
`openviking-server`.** The server needs an embedding model and a config wizard that wants
a TTY, neither available here. What is proven is conformance to the SDK's real code path;
what is not proven is the server's actual response *content*. Recorded as OI-10 rather
than implied, because R-16 exists precisely because a control that was specified and never
executed failed on first execution.

---

### Round 37 — Writing the quickstart found that multi-turn had never worked

The round began as documentation: an example of building an agent on the graph backend,
written as a five-step ladder. Step 4 demonstrates the durability the platform was adopted
for — ask twice on one `thread_id`. **The second question cost $0.00.**

**H37.1 — The model was called once per thread, ever, and the caller got their own message
echoed back.** `stop_reason` is checkpointed like every other state key, so a thread that
finished a turn came back carrying `"completed"`, and `_after_budget` routed the next turn
straight to `finish`. No error, no event, no cost — the agent simply stopped answering.
**Silent, and on the primary use case for a checkpointer.**

Round 35 added the `finish` node to make `run.finished` structural, and in doing so made
`stop_reason` load-bearing for routing. The defect is the cost of that fix, and nothing
caught it because every graph test invoked once.

**H37.2 — Two customers shared a budget, and a third had already read the first one's
taint.** The `Ledger` and `TaintTracker` were constructed once in `build_agent` and held on
the `Runtime`. A compiled graph serves every conversation, so:

| | leak |
|---|---|
| spend | customer B's first message reported customer A's spend; a `$0.20` ceiling was consumed by every customer collectively |
| taint | customer A's tainted run denied customer B's `danger` tools — over-restrictive, and the enforcement disagreed with the `tainted` the same run reported |
| taint, across a restart | **under-restrictive**: a tainted conversation resumed in a fresh process came back untainted, so a run that had already read a web page **regained its `danger` tools** — in exactly the durability case the platform was adopted for |

**This is Round 34's defect for the third time, in the third place.** Round 34 was a policy
instance shared across runs leaking one customer's workflow position to the next. The
council fixed it in the hand-written backend, wrote it down, scored it, and then built a
new backend that did the same thing with two different objects.

**H37.3 — And the first fix was wrong, for a reason worth writing down.** The obvious repair
is a `ContextVar` holding per-turn state, which is what `redaction_scope` already uses. It
does not work here: **LangGraph runs every node in its own copied context**
(`context.run(step.invoke, …)`), so a variable set in the budget gate is gone by the time
the model node reads it. The failure was loud — `AttributeError: 'NoneType' has no
attribute 'id'` — which is the only reason it did not become the next silent defect.

The rule that replaced it is the one the platform was telling us all along:

> **The thread's state is the only memory.** Nothing about a run may live on the `Runtime`.

`Ledger` grew `snapshot()`/`restore()`, and the ledger is now rebuilt from state on every
node — which keeps spend, the step count and ADR-026's upward-ratcheting calibration
per-conversation *and* across restarts, rather than per-process.

**Semantics, settled by parity rather than by taste.** `Chat` in the hand-written backend
has answered this since M4: **USD accumulates across a conversation; the step ceiling is
per turn.** Accumulating steps too would kill a long conversation permanently — every later
turn starting already over the limit. The graph backend now matches, because the question
had an answer already and inventing a second one is how two backends drift.

**What this says about the parity suite.** R-17 was scored 25 and the suite was built to
hold it — and none of these three defects were caught, because every parity scenario
invokes once. **A suite that covers the rules can still miss the shape of use.** Multi-turn,
concurrent threads and restart are now covered; the honest lesson is that the parity table
protects the rules it lists against drift, and protects nothing against a case neither
backend was ever asked to handle.

---

### Round 38 — Auditing the source against HARNESS.md

The brief existed for one round before it was used as a test. Four defects, all of them
places where a document asserts something the code does not do.

**H38.1 — `pause_turn` had never been heard of.** T-0.4's own "How" says *"Surface
`pause_turn` rather than swallowing it."* The stop-reason table has three entries and that
is not one of them, so it fell through to *unknown stop reason* and killed the run. The
provider says **resumable**; the harness said **dead**. It is what a server tool — web
search, web fetch — returns when the model pauses mid-turn, so the failure lands on the
one feature that produces it. Now it resumes, bounded at five consecutive pauses and loud
at the bound.

**H38.2 — IDL-19 asserts server-side refusal fallbacks "enabled by default", in three
documents, and the payload had never carried them.** A routine safety decline arrives as
HTTP 200 with `stop_reason: "refusal"`; without fallbacks it is a dead end. The specified-
but-never-built class again, and it survived every earlier audit because nothing exercises
the provider — `AnthropicProvider` has never run against the live API. The payload is now
built and asserted offline, against the parameter shapes in Anthropic's own current
documentation rather than from memory: `betas=["server-side-fallback-2026-07-01"]` with
`fallbacks="default"`, on the beta messages endpoint, the two forms matched (mixing them
is a 400).

**H38.3 — On the mandated backend a refusal and a truncation both reported `completed`.**
The graph's model node never read the stop reason at all; it only asked whether the
message carried tool calls. A refusal has none, and a `max_tokens` truncation has none, so
both routed to `finish` and came back as success. **IDL-30 forbids mapping an
*unrecognised* stop reason to a success; this mapped two recognised failures to one.** The
caller got half an answer labelled as a whole one.

The mapping now comes from the hand-written loop's table, imported rather than re-listed —
two copies of one table is how the backends drift (R-17).

**H38.4 — The shipped test helpers could not tell a blocked tool from an executed one.**
`_tools_called` read `tool_use` blocks, which are the model's *requests*. So for a
dangerous tool that policy correctly refused:

```
tool actually ran     : False
assert_tool_called    : PASS   ← wrong
assert_no_tool        : FAIL "expected 'xoa' NOT to be called, but it was"   ← wrong
```

§09 calls these *"readable assertions on behaviour"*; they were assertions on intent. In a
library whose entire safety story is that dangerous tools get blocked, **the helper shipped
for proving it asserts the opposite.** `Result` now carries `tools_run`, recorded where
execution is actually decided, and the failure message names the distinction.

### What the audit says about the audits

**Three of the four are in code paths no test exercised**, and the fourth is in the test
helpers themselves. R-17 is scored 25 and the parity suite exists to hold it; it missed
H38.3 because **no parity scenario had ever set a provider stop reason** — every one of
them ended `end_turn` or `tool_use`. Round 37's lesson was that a suite can cover the rules
and miss the shape of use; this round is the same lesson one level down: **the suite covered
the rules under the inputs it had thought to supply.**

The parity table now has five stop-reason rows. The pattern worth naming, because it is now
three rounds old:

> Every time this package has been extended, the new code has failed on an input the old
> code handled — never on the feature being added.

**H38.5 — one non-defect, recorded because it nearly became a finding.** `approve_all` /
`deny_all` are factories: §09 documents `approve=deny_all()`, with parentheses. Passing the
function itself raises `TypeError: approve_all() takes 0 positional arguments but 2 were
given`, which does not mention the missing parentheses. That is a real §II question-6 gap
(*"can the API make the wrong use hard?"*) and it is **not** a correctness defect. Kept
separate rather than inflated, because a review that reports its own misuse as a bug is
worth less than one that does not.

---

### Round 39 — Closing the open item: mypy and ruff had never been run

Both tools were listed as never executed since Round 30. Running them is not a formality:
**162 ruff findings and 112 mypy errors**, and the interesting ones were not the counts.

**H39.1 — `@value` was invisible to every type checker, so nobody using this library got
any.** The decorator returns `type[T]`, so a checker sees the original class body with no
generated `__init__`. Measured consequence, from a user's file rather than ours:

```
Usage(100, 50, 0, 0, 999)   # wrong arity      → not caught
Usage(input_tokns=100)      # misspelt field   → not caught
agent.name                  # documented public in §03 → "Agent has no attribute name"
```

**A user running mypy on `agent.name` was told the attribute does not exist**, while
`Money`, `Usage`, `Result`, `Decision` and `ToolCall` accepted any arguments at all. §II's
fifth question asks whether a runtime error can be made a compile-time one; for the entire
value layer the answer had been *no*, silently. `dataclass_transform` (PEP 681) plus
class-level annotations on the two `__slots__` classes fixed both: 112 → 22, and all three
user mistakes above are now caught, the misspelling with a did-you-mean.

**H39.2 — `returns=` accepted an instance where it needs the class**, and failed with
`AttributeError: 'KQ' object has no attribute '__name__'` **after paying for a model
call**. Found by mypy, not by any test. Now refused at construction, naming the fix.

**H39.3 — A lint warning whose obvious fix disables a security test.** RT-08 binds
`s = Secret(...)` and never reads it — `F841`. The binding is what keeps the secret alive
in ADR-024's weak registry; without it `redact()` cannot match the value and the secret
goes out in cleartext. Measured both ways. It fails *loudly* rather than silently, which is
the saving grace — but nothing in the file said why the variable was there, so a
contributor clearing the warning would read the failure as flakiness. Now stated, with a
`noqa` so the tool stops inviting the change.

**H39.4 — And the autofix broke the package.** `ruff --fix` removed
`from .dispatch import ... RunContext` from `run.py` as unused; it is a re-export that
`harness/__init__.py` depends on. `import harness` failed. Caught by running the suite
immediately after, which is the only reason it is a footnote rather than a defect —
**an automatic fix is a change, and a change gets tested like any other.**

**Two dead things and a duplicate.** 38 unused imports, all debris from the two file
splits (Round 28's `dispatch.py`, Round 35's `runtime.py`); and `err()` defined twice in
`dispatch.py`, the bodies identical, so no behaviour rode on which won. Checked before
being written up, because "duplicate function" and "the wrong one wins" are different
findings and only one of them was true.

### What was deliberately not done

E701/E702 — one-line accessors (`def spent(self) -> Money: return self._spent`) and paired
assignment — account for 62 of the 162 ruff findings. They are a house style applied
consistently, and rewriting ~60 working lines to satisfy a default would be churn with risk
and no reader benefit. Ruff is configured to the style the package actually uses; the
config says so. `mypy --strict` is likewise declined: it would buy annotations on internals
nobody calls. **HARNESS.md §III cuts both ways — "not over-engineer" applies to the
cleanup as much as to the feature.**

Both tools are now CI gates (AC-62, AC-63), and a third test asserts the thing that
actually mattered: that a *user's* file gets real type errors on the value types and none
on the documented public attributes.

---

### Round 41 — One agent using every capability, and the two things that stopped it

The request was a single agent exercising the whole library. Writing it found that the
library could not, quite, and that the example itself was wrong.

**H41.1 — `as_tool()` was built, documented, and broken on the mandated backend.** A
subagent `ToolSpec` is not called through `spec.fn`; the loop's dispatcher handles it so
the child's spend settles into the parent's ledger (ADR-030). The graph called `spec.fn`
directly, which raises — and **the `AssertionError` went to the model as a tool result**,
so the agent read *"subagent tools are dispatched, not called directly"* and carried on.
Built fine, failed at run time, degraded silently: the H38.2 shape again.

Ported, with the part that matters intact — the child is capped by the parent's
**remaining** headroom, and the headroom is *held* rather than read, because parallel
children each reading `remaining_usd()` all claimed the whole of it (Round 28). Measured:
a `$0.10` parent with eight greedy children settles at `$0.0158`.

**H41.2 — And the port needed an import that Round 39 had deleted.** `Money` was removed
from `lg/runtime.py` by `ruff --fix` as unused, correctly, at the time. This is the second
time an autofix removal has surfaced later (H39.4 was the first, a re-export). Not an
argument against the tool — an argument for IDL-52, which is why both were caught.

**H41.3 — The example's own state machine let the refund through.** The first draft used
`Enum` with Vietnamese labels and compared `self.buoc.value >= can.value`: a *string*
comparison, alphabetical. `"đã tra đơn" >= "đã kiểm chính sách"` is meaninglessly True, so
`hoan_tien` was ALLOWed on the first call — before the policy check, which is precisely
what the workflow exists to prevent. The printed decision table is what exposed it:

```
✓ tra_cuu_van_chuyen  ALLOW
✓ hoan_tien           ALLOW          ← wrong, and shipped in the first draft
✗ ask_chuyen_gia      DENY  ...
```

Now an `IntEnum`, where order is order. **An example that demonstrates a control has to be
read as carefully as the control**, and the reason it was caught is that the file prints
every verdict rather than only the happy path.

### What the round says about "all capabilities"

No single backend has them all, by design and by record: `returns=`, transcript, resume and
streaming are the loop's; durable checkpointing, multi-turn and per-thread isolation are the
graph's. §14.4.1 has listed that since Round 37. So the agent is **one set of tools and
policies, built twice**, and the file prints the capability table rather than implying a
completeness it does not have. The parity suite is what keeps the shared rows honest.

---

### Round 42 — Đối chiếu với nghiên cứu hợp nhất về framework và harness

Người dùng đưa vào một nghiên cứu độc lập: 12 framework, 9 harness, ma trận 10 tiêu chí có
trọng số, và một phụ lục API với 5 anti-pattern. Hội đồng dùng nó như **một bài kiểm tra
từ bên ngoài** — cái mà 41 vòng vừa qua không có, vì mọi tiêu chí trước đều do chính người
dùng hoặc chính hội đồng đặt ra.

**Tự chấm theo đúng ma trận của nghiên cứu: 67.8/100.** Để tham chiếu, nghiên cứu chấm
LangGraph 89.4, Goose 83.5, Pi 76.8. Con số không so sánh trực tiếp được (khác người
chấm, và họ chấm dự án đã trưởng thành) — **giá trị nằm ở hình dạng**: mạnh ở Safety 4/5,
Cost 4/5, Testability 4/5, DX 4/5; yếu ở Integration 2/5, Performance 2/5,
Observability 3/5, Reliability 3/5.

**H42.1 — Nghiên cứu chỉ đúng một chỗ hội đồng đã lảng tránh.** [§01.5](01-requirements.md)
xếp sandbox vào non-goal, lập luận rằng cách ly thật cần process/WASM và đó là một sản
phẩm khác. Nghiên cứu bác lại ở mức nguyên tắc, và bác đúng: *"approval không đồng nghĩa
sandbox"* là nhận xét của nó về Cline, và **harness mắc đúng lỗi đó**. Isolation là một
trong tám lớp Poka-Yoke; harness có bảy, không có lớp này. Không thể đóng gói container
vào một thư viện — nhưng ba việc thư viện làm được thì đã không làm: workspace root,
egress **mặc định chặn** (hiện `allowed_hosts=None` nghĩa là cho tất cả), và một seam để
cắm sandbox thật. R-24, chấm 20.

**H42.2 — `CancelledError` bị nuốt.** Đo được: `t.cancel()` rồi `await t` trả về
`Result(stop_reason="cancelled")` thay vì raise. Phần side effect thì **đúng** — tool bị
huỷ thật, không chạy ngầm — nên đây là lỗi giao thức chứ không phải lỗ hổng: một
`TaskGroup` hay `asyncio.wait_for` bao ngoài sẽ không thấy việc huỷ đã xảy ra. `try_run()`
trả về `Result` là thiết kế (IDL-11), nhưng **huỷ không phải một stop reason bình thường,
nó là tín hiệu điều khiển.**

**H42.3 — Không có idempotency key.** Anti-pattern 3 của nghiên cứu, và OWASP xếp
duplicate-action thành rủi ro riêng. [§05.3](05-data-and-state.md) từ chối *tự* chạy lại
`write`/`danger` khi resume, và hội đồng đã coi thế là đủ — không đủ: một client timeout
rồi gọi lại vẫn gửi email lần hai, vì không có gì nhận ra đó là cùng một lời gọi. R-25.

**H42.4 — Envelope thiếu trường truy vết.** `Event` có `seq, ts, run_id, kind, step, data`;
nghiên cứu đòi thêm `schema_version`, `trace_id`, `tenant_id`. Không có version thì không
đổi được taxonomy mà không phá exporter của người khác — và taxonomy đã đổi hai lần
(vòng 27, vòng 35).

### Điều nghiên cứu xác nhận là đúng

Bảy trong tám lớp Poka-Yoke đã có, và nguyên tắc trung tâm của nghiên cứu —
*"model được quyền **đề xuất** tool call, không được tự cấp quyền thực thi"* — chính là
điều `unguarded_paths()` chứng minh bằng reachability (ADR-032). Ba trong năm anti-pattern
API đã tránh được từ trước: `Result` không phải một chuỗi, state không phải final text,
và ranh giới an toàn nằm ở policy engine chứ không ở transport.

### Kế hoạch: [§17](17-research-alignment.md)

M6 Reliability → M7 Isolation → M8 Observability → M9 Integration → M10 Evaluation, xếp
theo `trọng số × khoảng trống`. M6 trước vì idempotency là điều kiện tiên quyết cho retry,
cho service API và cho contract "retry không nhân đôi side effect". M7 trước M9 vì mở MCP
ra khi chưa có isolation là mở rộng bề mặt tấn công trước khi dựng tường.

**Và một ràng buộc tự áp lên kế hoạch.** Nghiên cứu cảnh báo *surface rộng* (LlamaIndex)
và *operationally nặng* (OpenHands). Kế hoạch này thêm MCP, HTTP server, sandbox và eval —
đúng những thứ làm thư viện phình. Core giữ nguyên 3 dependency và import dưới 100 ms;
mọi thứ M7–M10 là `extra`; và phép thử plugin boundary áp cho từng seam mới. Mục nào không
qua được phép thử đó thì không vào.

---

### Round 43 — Câu hỏi về API, và lỗi mà chính câu hỏi đó tìm ra

Câu hỏi: *"phần API interface có cần thiết kế lại không?"* Trả lời bằng cách đo theo năm
contract nghiên cứu tách ra, chứ không bằng ý kiến.

**H43.1 — Dangling tool calls bị bỏ im lặng, và hội thoại lưu lại không hợp lệ.**
Nghiên cứu nêu một chi tiết tinh vi về PydanticAI: *"final output có thể kết thúc run
trước khi dangling tool calls được thực thi."* Đem đúng câu đó thử harness:

```
model trả: text + tool_use{ghi}  với stop_reason "end_turn"
vòng lặp : completed · tool đã chạy []        ← bỏ im lặng
           tool_use ['c1'] / tool_result []   ← VI PHẠM I-3
graph    : tool đã chạy [1] · có ToolMessage  ← đúng
```

Hai lỗi trong một. Tool bị bỏ, **và** hội thoại giữ lại một `tool_use` không có
`tool_result` — phát lại hội thoại đó cho provider là bị từ chối thẳng. Luật đã sửa:
**tool call chạy vì nó CÓ MẶT, không vì provider dán nhãn `"tool_use"`.**

Điều đáng ghi nhất: **backend graph vốn đã đúng.** Sau bảy vòng liên tiếp graph là bên
thua kém (H38.3, H41.1, và cả loạt trước), lần này vòng lặp mới là bên phải đuổi theo.
Bảng parity không nói ai đúng — nó chỉ nói hai bên khác nhau, và lần này bên đúng là bên
mới hơn.

**Trả lời câu hỏi: không thiết kế lại.** Contract 1 (invocation) và 2 (tool) là phần mạnh
nhất của gói — không thư viện nào trong nghiên cứu suy ra **năm hành vi từ một phân loại
`effect`**, và cả hai đang được §04.8 bảo hành. Contract 3 (event stream) và 4 (session)
thì thiếu hẳn — nhưng thiếu không phải sai, và thêm chúng **không đổi một chữ ký nào đang
có**: `Agent.stream()` là phương thức mới, `Session` là đối tượng mới. Phá contract 1–2 để
"hiện đại hoá" là đổi thứ đã chứng minh lấy thứ chưa.

Cụ thể cần học ở đâu, ghi trong [§17.3.3](17-research-alignment.md): `run_stream_events`
và dependency injection của PydanticAI, run-state-as-object của OpenAI Agents SDK, stream
có version của LangGraph, session cách ly của Goose. Và một mục **cố ý không học**: bốn
promise rời rạc của Mastra — `Result` gom một chỗ là quyết định, vì bốn thứ rời nhau dễ bị
đọc thiếu một.

---

## 2.4 What the five build rounds cost, and what they found

| Round | Built | Defects | Security | Specified-but-unbuilt | Test-harness bugs |
|---|---|---|---|---|---|
| 24 | M0 walking skeleton | 7 | 0 | 0 | 0 |
| 25 | M1 safety core | 3 | **1** | 0 | 0 |
| 26 | M2 cost core | 4 | 0 | **3** | **1** (a benchmark that could not fail) |
| 27 | M3 observability | 3 | 0 | **1** | 0 |
| 28 | M4 memory · plugins · subagents | 4 | **1** | **2** | 0 |
| 29 | M5 CLI · tutorial-as-test | 0 | 0 | 0 | **9** |
| 30 | promise-vs-implementation audit | **6** | 0 | **6** | 2 |

**27 defects, 2 of them security, 12 features specified and never built** — including the
model provider itself. Against 20 defects and 0 security findings across the 23 rounds of
review that preceded them.

Round 28 found four defects in new code and Round 29 found none, which the council read as
convergence. **Round 30 then found six, including the absence of the model provider.** The
lesson is not that the trend was wrong but that it measured the wrong surface: findings were
falling because the *tests* had run out of things to ask, not because the *package* had run
out of gaps. A convergence claim needs to name what it has stopped finding **and what it
never looked at.**

---

## 2.3 Running score

| Round | Built | Defects found | Security defects | Specified-but-never-built |
|---|---|---|---|---|
| 1–23 | nothing | 20 | 0 | — |
| 24 | M0 slice | 7 | 0 | 0 |
| 25 | M1 safety core | 3 | **1** | 0 |
| 26 | M2 cost core | 4 | 0 | **3** |
| 27 | M3 observability | 3 | 0 | **1** (+1 unreachable by arithmetic) |
| 28 | M4 memory · plugins · subagents | 4 | **1** (budget ceiling leak) | **2** |
| 29 | M5 CLI · tutorial-as-test | **0** | 0 | 0 |
| 30 | promise-vs-implementation audit | **6** | 0 | **6** (incl. the whole provider) |
| 31 | readability & the SC-1b instrument | **3** | 0 | 0 |

Twenty-five rounds in, **the only live security hole in the package was found by running the
red-team suite, not by writing it.** It had been specified since Round 7, reviewed in Rounds
15, 19, 21 and 24, and reworked in Round 24 — by the round whose subject was executing
contracts.

The council's position: this is no longer evidence about *this* design. It is evidence about
design review in general, and it belongs in
[§14.7](14-validation-plan.md#7-after-10--keeping-this-package-honest) as a practice rather
than in the round log as an anecdote.

---

## Convergence

The council's position, stated with its limits rather than as a verdict.

**What is settled.** Twenty-three rounds; twenty-three architectural decisions with their
losing arguments recorded; 54 Poka-Yoke entries; 26 conformance checks; 9 properties; 17
red-team scenarios; every requirement traced to an owning task. The five techniques that
found every post-convergence defect are now CI checks rather than reviewer discipline.

**What is not settled, and cannot be settled by more discussion:**

1. **SC-1b has not been run.** The beginner claim is measured with real children before 1.0
   and not before. The council believes [§15](15-first-agent.md) meets the bar; belief is
   what Round 4 had, and Round 13 found it worthless. **This is the largest open risk in the
   package** (R-05), and it is open by construction — no additional round can close it.
2. **No code exists.** Rounds 17–22 each found a defect that survived every prior *reading*
   and died to the first *execution*. That ratio is the strongest available evidence that
   the next class of defect is waiting in M0, not in another round of review.

**Why the council is stopping here rather than at Round 24.** The findings are getting
smaller — Round 20 found seven undefined types and a `Must` requirement with no owner;
Round 23 found a 30-second overshoot and an undefined interaction. The marginal round is now
returning less than the marginal day of implementation would. **Continuing to review a
package that no one has tried to build is how a design document becomes an artifact instead
of a plan**, and this package's own supreme principle is *optimize for a plan that can
actually be implemented*.

The council reconvenes on the triggers in
[§13.4](13-risk-register.md#4-what-would-make-the-council-reconvene) — five specific,
falsifiable conditions, one of which has already fired once.

**Gate:** 16/16, with every row backed by an executable check and the two judgement rows
marked as such.

---

## 2.1 What the recursive rounds cost, and what they found

Rounds 13–21 were opened after the council had already declared convergence at Round 12
with a 16/16 gate. They found:

| Round | Found | Class |
|---|---|---|
| 13–16 | Six beginner blockers, five of them outside the API | Wrong premise, then wrong scope of review |
| 17 | Scaffold budget made every first run refuse to call | Two correct defaults that multiply into a contradiction |
| 18 | Truncated answers reported as success; `Chat` budget undefined | A closed enum complete against the design, incomplete against the API |
| 19 | Approval violates the policy contract; `Secret` breaks Python's hash invariant | Contracts that read correctly and do not execute |
| 20 | Two unhashable types being hashed; seven types never defined | Same, plus a gap the task-shaped walkthrough could not see |
| 21 | One of five invariants had no section, no decision, no test | Nobody had counted |
| 22 | A `Must` requirement and 18 conformance tests owned by no task | A numbered thing with no owner is nobody's job |
| 23 | A wall-clock ceiling that overshoots by up to 30 s; an undefined step/resume interaction | The last unmultiplied cross-products — and the point of diminishing returns |
| **24** | **Seven defects in the built M0 slice, including one that falsified SC-2** | **Reading cannot find what only running finds** |
| **25** | **Three defects in the built M1 safety core, including a live secret leak to the model** | **Writing a red-team suite is not running one** |
| **26** | **Four defects in the built M2 cost core; three features were specified, owned by a task, and never implemented** | **Ownership is not implementation** |
| **27** | **Three of fifteen event kinds had no emit site; context management was unreachable by arithmetic** | **Naming a failure class does not prevent it — only the inverse test does** |
| **28** | **A $0.10 agent spent $30 through subagents while reporting $0.0000** | **A suite written by the feature's author tests the feature, not the promise** |
| **29** | **Nine red tests, zero product defects — the tutorial held** | **A test that fails for its own reasons misleads as much as one that passes for none** |
| **30** | **The library had no model provider, and §15 told children to import a module that did not exist** | **Neither a test suite nor a traceability matrix reads the docs as a promise** |
| **31** | **The tutorial read at age 9; the errors a child hits read at university level** | **A substring check verifies vocabulary, not the message** |
| **32** | **`as_tool()` could not name a subagent outside ASCII, and the fix collided two names into one** | **Ask what two inputs produce one output, not whether the happy path works** |
| **33** | **`returns=` was accepted and never parsed; non-ASCII tool results cost 2.2×–4.8×** | **A default is chosen for an audience; ask what it does to the others** |
| **34** | **A workflow state machine leaked one customer's position to the next** | **Two documents can each be correct and contradict each other in production** |
| **35** | **Porting to LangGraph reintroduced three defects the council had already fixed, one of them a live secret leak** | **A defect class fixed in one implementation is not fixed in the next one** |
| **36** | **A memory system classified as `read` puts a hole in the taint lattice the size of the memory system; and the parse tests were green against an envelope the server never sends** | **Retrieved memory is untrusted input, and a fixture you invented cannot falsify what you believe** |
| **37** | **Multi-turn had never worked: the model was called once per thread, ever. Two customers shared one budget, and taint was lost across a restart** | **A suite that covers the rules can still miss the shape of use — every graph test had invoked exactly once** |
| **38** | **A refusal and a truncation both reported `completed` on the mandated backend; the shipped test helper could not tell a blocked tool from an executed one** | **Every time this package is extended, the new code fails on an input the old code handled — never on the feature being added** |
| **39** | **Every value type in the package was invisible to type checkers, so users got no checking on `Money`, `Usage`, `Result` — and `agent.name` was reported as not existing** | **A tool nobody has run is a claim, not a check — and its autofix is a change that needs testing like any other** |
| **40** | **`examples/proof.py` — every requirement asserted in running code, validated by breaking the library and watching it fail** | **A proof that cannot fail is not a proof** |
| **41** | **`as_tool()` was built, documented, and broken on the mandated backend — its error reached the model as a tool result; and the example's own state machine let the refund through** | **An example that demonstrates a control must be read as carefully as the control** |
| **42** | **Một nghiên cứu bên ngoài chấm 67.8/100 và chỉ ra lớp Poka-Yoke duy nhất còn trống: Isolation — chỗ hội đồng đã tự cho phép mình lảng tránh** | **Tiêu chí do chính mình đặt ra không phát hiện được thứ mình đã quyết định không nhìn** |
| **43** | **Dangling tool calls bị bỏ im lặng và hội thoại lưu lại vi phạm I-3 — tìm ra bằng cách đem một cảnh báo về thư viện KHÁC ra thử thư viện mình** | **Backend mới hơn không phải lúc nào cũng là bên sai; parity chỉ nói hai bên khác nhau** |

**Every one of these passed a prior review.** The five techniques that found them — multiply
the numbers out, execute the contract, traverse types rather than tasks, count coverage per
requirement, and check that every numbered artifact has an owner — are now walkthrough steps
and CI checks ([§14](14-validation-plan.md)) rather than things a diligent reviewer might do.

The council's position on its own Round 12 verdict: it was wrong, and it was wrong in a
predictable way. **A gate that is assessed by the people who wrote the thing being gated
measures agreement, not readiness.** The gate below is now backed by executable checks for
every row; the rows that remain judgement calls are marked as such.

---

## 3. Implementation Readiness Gate — final

| Dimension | Ready | Evidence |
|---|:--:|---|
| Architecture | **Yes** | [§02](02-architecture.md); ADR-001, 002 |
| Component design | **Yes** | [§02.5](02-architecture.md) module map, 1:1 with tasks |
| Interfaces | **Yes** | [§04](04-interfaces.md) — every signature pinned; contracts exercised in code, not read (Round 19) |
| Data & state | **Yes** | [§05](05-data-and-state.md) — event taxonomy, transcript schema |
| Security | **Yes** | [§06](06-safety.md) — threat model, ADR-003/008/011, red-team suite |
| Cost | **Yes** | [§07](07-cost.md) — ADR-004/005/006/016/017/020; defaults validated by arithmetic, not review |
| Performance | **Yes** | [§07.5](07-cost.md), parallel scheduling from effect classes |
| Testing | **Yes** | [§09](09-testing.md) — pyramid, fakes, CI gates |
| Observability | **Yes** | [§10](10-observability-ops.md) — closed taxonomy |
| Deployment | **Yes** | [§10.4](10-observability-ops.md) — library packaging, semver |
| Plugin architecture | **Yes** | [§02.4](02-architecture.md) — five boundaries, justified by test |
| Poka-Yoke | **Yes** | [§08](08-poka-yoke.md) — 34 modes, each with a design-level defense |
| Developer experience | **Yes** | [§03](03-public-api.md) — disclosure ladder; ADR-014, 015 |
| Beginner experience | **Yes** | [§15](15-first-agent.md) written as a demonstration; **SC-1b measured with real children** ([§14.2](14-validation-plan.md#2-sc-1--time-to-first-agent)) |
| Documentation | **Yes** | M5 in [§11](11-implementation-plan.md), plus [§15](15-first-agent.md) as a separate deliverable |
| Implementation tasks | **Yes** | [§11](11-implementation-plan.md) — every task has contract + DoD, and the [traceability matrix](11-implementation-plan.md#traceability-matrix) shows every requirement has a task |

## 4. Standing verdict

> The council's position is that an engineering team can begin implementation from
> [§11](11-implementation-plan.md) on Monday without making a further architectural
> decision. The decisions that remain open are listed in
> [§13](13-risk-register.md#3-open-issues), and each states why it does not block.

On the beginner requirement specifically, the council's position is deliberately narrower
than "we believe this is simple enough". [§15](15-first-agent.md) exists so the claim can be
read and judged rather than taken on trust, and **SC-1b measures it with actual children
before 1.0 ships**. The council does not consider the requirement met until that
measurement passes — Rounds 13–16 exist because the first four rounds of beginner review
had reassured themselves without ever testing the thing they were reassuring themselves
about.
