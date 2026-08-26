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

## 3. Implementation Readiness Gate — final

| Dimension | Ready | Evidence |
|---|:--:|---|
| Architecture | **Yes** | [§02](02-architecture.md); ADR-001, 002 |
| Component design | **Yes** | [§02.5](02-architecture.md) module map, 1:1 with tasks |
| Interfaces | **Yes** | [§04](04-interfaces.md) — every signature pinned |
| Data & state | **Yes** | [§05](05-data-and-state.md) — event taxonomy, transcript schema |
| Security | **Yes** | [§06](06-safety.md) — threat model, ADR-003/008/011, red-team suite |
| Cost | **Yes** | [§07](07-cost.md) — ADR-004/005/006/017; defaults validated by arithmetic, not review |
| Performance | **Yes** | [§07.5](07-cost.md), parallel scheduling from effect classes |
| Testing | **Yes** | [§09](09-testing.md) — pyramid, fakes, CI gates |
| Observability | **Yes** | [§10](10-observability-ops.md) — closed taxonomy |
| Deployment | **Yes** | [§10.4](10-observability-ops.md) — library packaging, semver |
| Plugin architecture | **Yes** | [§02.4](02-architecture.md) — five boundaries, justified by test |
| Poka-Yoke | **Yes** | [§08](08-poka-yoke.md) — 34 modes, each with a design-level defense |
| Developer experience | **Yes** | [§03](03-public-api.md) — disclosure ladder; ADR-014, 015 |
| Beginner experience | **Yes** | [§15](15-first-agent.md) written as a demonstration; **SC-1b measured with real children** ([§14.2](14-validation-plan.md#2-sc-1--time-to-first-agent)) |
| Documentation | **Yes** | M5 in [§11](11-implementation-plan.md), plus [§15](15-first-agent.md) as a separate deliverable |
| Implementation tasks | **Yes** | [§11](11-implementation-plan.md) — every task has contract + DoD |

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
