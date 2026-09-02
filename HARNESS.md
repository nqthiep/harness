# HARNESS.md — The Project Owner's Requirements

> This is a **requirements record**, not a design document. It answers *"what did the
> project owner actually ask for?"*, kept separate from *"how did the council design
> it?"* — the latter lives in [`docs/`](docs/).
>
> Every item below is drawn from the project owner's own words during the work. Anywhere
> the council interpreted rather than quoted is marked clearly. The **Where** column
> points to where each requirement is actually met, so a requirement can never be
> "agreed to" without an address.

> **Current status (read this before §XIX — the section below stands as a historical
> record; Rounds 0-39 are not rewritten, only continued):** Rounds 0-39 brought the
> package to "Ready for Implement." After that came **two real build phases**, outside
> §VII's original round count:
>
> 1. **58 findings from two adversarial review rounds** (`design/review-kiss.md`,
>    `design/review-security.md`) — each one re-checked against the real code, the ones
>    still live got fixed. See `design/07-risks-and-open-issues.md`.
> 2. **The M6-M10 growth roadmap** (`docs/17-research-alignment.md`, benchmarked against
>    an independent survey of 12 frameworks / 9 harnesses) — idempotency, proper
>    cancellation, chaos testing (M6); workspace confinement, egress denied by default,
>    the `Sandbox` seam (M7); event envelope v1, real OTel, cost/successful-task (M8); an
>    MCP client, the Service API, one event model across three transports (M9); the
>    trajectory contract, a golden set, a performance benchmark (M10). **All five
>    milestones are done** — see `design/08-roadmap-and-release-plan.md`.
>
> §XIX below still ends at "Round 39" in the original record; its three "still open"
> items (SC-1b, OI-10, OI-11 — needing real children, a real OpenViking server, a real
> API key) **remain open today**, unchanged — neither of the two phases above touched
> them, because all three need A REAL PERSON outside the code/test loop, not more code.

**Source:** the full design conversation, the council running from Round 0 to Round 39.
**Scope:** the Python library `harness`, branch `claude/ai-agent-harness-design-ti5vk3`.

---

## 0. The original mandate

Use the **Personal Stack** method to assemble an **Implementation Design Council** of the
most suitable experts, to turn the **AI Agent Harness** idea and architecture into an
**extremely detailed, realistic, Ready-for-Implement Implementation Plan**.

> This is **not** a brainstorming session, and not a task to produce an implementation
> plan in one pass.

The council works in repeated rounds following this cycle:

```
Understand → Design → Challenge → Find Gaps → Debate → Resolve → Refine → Validate → Repeat
```

and **may only stop** once the Implementation Plan is clear enough that an engineering
team can start coding immediately, without having to make any significant architectural
or technical decision on its own.

---

## I. Five invariant principles

> **These principles may never be traded away just to make implementation easier.**
> If a design violates one of them, the council must catch it, challenge it, and
> redesign.

### 1. Extensible / Pluginable

Capability can be added, replaced, or extended without unnecessarily touching core.

> **Pluginable does not mean "Everything is a Plugin."**

The council must **determine for itself** what a reasonable plugin boundary is, what
belongs in the core primitives, and what should NOT become a plugin. Nothing gets turned
into an abstraction/plugin just because extensibility sounds nice.

**Where:** [`docs/02-architecture.md §4`](docs/02-architecture.md) — a three-part test for
the plugin boundary, cutting 9 proposed abstractions down to **5 seams**: Tool,
ModelProvider, Store, Policy, Exporter. Three things are kept in core **precisely
because** a replacement could disable an invariant.

### 2. Cost Efficient

The question that must be asked continuously:

> "Is there a way to reach the same result with fewer tokens, fewer model calls, less
> infrastructure, and less computation?"

Must be considered: model selection, model routing, small vs. large model, caching,
context management, memory, RAG, tool usage, retry, parallel execution, batch
processing, token usage, cost monitoring, fallback strategy.

> **Cost must be an architectural concern, not something optimized after the build is
> done.**

**Where:** [`docs/07-cost.md`](docs/07-cost.md) — a pre-flight budget ceiling (ADR-017),
cache-safety by construction (ADR-029, measured at 95.3%), ADR-026 after SC-2 was
disproven by measurement.

### 3. Safe by Design

Safety from the design stage, not security bolted on at the end. Required mindset:

> **Secure by Design + Fail Safe + Least Privilege + Defense in Depth**

Specifically consider: agent safety, tool safety, plugin safety, prompt injection, data
leakage, unauthorized tool execution, malicious plugins, secret leakage, excessive
permissions, uncontrolled agent loops, resource exhaustion, supply-chain risk.

**Where:** [`docs/06-safety.md`](docs/06-safety.md) — the threat model, the taint lattice
(ADR-011), `Secret`, the plugin trust boundary, 21 red-team scenarios that run in CI.

### 4. Intelligent

An intelligent agent does **not** mean always using a large model or complex reasoning.

> **Maximum intelligence per unit of cost and latency.**

The harness needs to pick the right approach for each task instead of always using the
same model or the same workflow.

**Where:** [`docs/07-cost.md §6`](docs/07-cost.md) — adaptive thinking on by default,
`effort` as a dial the caller holds, `strict` on every tool, typed `returns=`, subagents
running cheaper models, a stable prefix cache.

**⚠️ The council refused part of this requirement, and says so plainly:** **no automatic
model routing** (ADR-006) — nobody could state a routing policy the council agreed was
correct. "Picking the right approach per task" is **manual**: the caller sets `effort=`,
picks `model=`, or delegates to a subagent. The capability exists; automation does not.
Round 38 checked every line of the §07.6 table and found two claims that were wrong or
incomplete (see §XIX).

### 5. Efficient

Efficient in: latency, token usage, compute, memory, network, infrastructure,
**developer effort**, operational effort.

> Not just runtime optimization. **Developer experience is a form of efficiency too.**

---

## II. Poka-Yoke — mistake-proofing from the design stage

> "One of the most important principles."

Instead of *"the developer must remember to do it right,"* prioritize:

> **"Design the system so the developer can almost never do it wrong."**

Every time a possible failure class is found, six questions must be asked:

1. Can this failure be eliminated by design?
2. Can it be detected immediately?
3. Can it be automatically prevented?
4. Can a safe default be provided?
5. Can a runtime error become a compile-time/configuration-time error?
6. Can the API be designed so that misuse becomes hard or impossible?

Priority order:

```
Prevent → Detect Early → Fail Safe → Recover        (NOT: Allow → Detect Later → Debug)
```

Applies to: API, configuration, plugins, agent definitions, tool calling, memory, model
selection, workflow, security, deployment, testing, developer experience.

**Where:** [`docs/08-poka-yoke.md`](docs/08-poka-yoke.md) — 83 failure modes, each with a
design-level mitigation, ranked on the scale Impossible > Import-time >
Construction-time > First-run > Loud warning > Documented.

---

## III. Engineering Principles

| Principle | Requirement |
|---|---|
| **SOLID** | Applied **in substance, not mechanically** |
| **CLEAN CODE** | Readable, understandable, maintainable, explicit, cohesive, low coupling |
| **KISS** | If a simple solution solves the problem, a more complex one **must not** be chosen |
| **NOT OVER-ENGINEER** | **Mandatory.** Do not build a capability just because "it might be needed someday" |

For NOT OVER-ENGINEER, four tiers must be distinguished clearly: **Required now** /
**Required for production** / **Useful later** / **Speculative**.

> Do not turn a future possibility into present-day complexity.

---

## IV. Extreme Developer Experience

> "This is an **especially important** requirement."

Goal:

> **A 10-year-old should be able to understand how to use the Harness to build a basic
> Agent.**

This does **not** mean the internal architecture has to be as simple as a children's app:

> **Complexity inside, simplicity outside.**

Minimize **Time to First Agent** and **Cognitive Load** as far as possible.

Illustrative philosophy (but **must not be taken as the final design by default** — the
council must find the best DX/UX on its own):

```
Create Agent → Give it a name → Tell it what to do → Give it capabilities → Run
```

### IV.a — An important correction from the project owner

In Round 0 the council argued the "10-year-old" requirement was infeasible. The project
owner rejected that:

> **"I'll add that these 10-year-olds already know `pip install` and have already
> learned basic Python programming."**

This requirement must be read **literally**. The council reopened Rounds 13-16 and
ADR-012.

**Where:** [`docs/15-first-agent.md`](docs/15-first-agent.md) — documentation aimed at
children, measured at **grade 4.2** on the Flesch–Kincaid scale, used as a runnable
specification; [`docs/16-sc1b-field-kit.md`](docs/16-sc1b-field-kit.md) — a real,
runnable survey kit.

---

## V. Zero-to-Agent Experience

Design a continuous journey:

> **Zero knowledge → First Agent → Useful Agent → Advanced Agent**

Must be determined: minimum mental model, minimum API, minimum configuration, default
behavior, **safe defaults**, convention over configuration, **progressive disclosure**.

Advanced capability appears only when the user actually needs it. **A beginner should
never have to understand** LLM orchestration, agent runtime, context engineering, memory
architecture, RAG, the tool protocol, model routing, or multi-agent coordination just to
create a simple Agent.

**Where:** [`docs/03-public-api.md`](docs/03-public-api.md) — the progressive-disclosure
ladder; [`examples/langgraph_quickstart.py`](examples/langgraph_quickstart.py) — five
tiers, each adding exactly one concept.

---

## VI. The council must find every problem on its own

Not only solve what the project owner explicitly stated. Must proactively look for:
architectural, implementation, API, UX, security, performance, cost, scalability,
testing, deployment, operational, maintainability, migration, versioning,
plugin-ecosystem, and developer-onboarding problems.

> **If I have an assumption that isn't sound, challenge it directly.**
> **Don't try to defend my ideas. The goal is to build the best possible system.**

---

## VII. The Iterative Council process

The Implementation Plan must not be produced in one pass and then closed.

| Round | Content |
|---|---|
| **Round 0** | Understand — goals, constraints, requirements, NFRs, principles, success criteria. Significant ambiguity must be resolved |
| **Round 1** | Initial Implementation Plan — doesn't need to be perfect, creates a baseline to challenge |
| **Round 2** | Architecture-to-Code — find missing components / interfaces / dependencies / contracts / ambiguous behavior |
| **Round 3** | Developer Review — *"I picked up this task today. Do I have enough to start coding?"* If **No** → identify the blocker and fix the plan |
| **Round 4** | Beginner UX — *"Can a 10-year-old build their first Agent?"* |
| **Round 5** | Poka-Yoke — for every mistake: **can we prevent it by design?** |
| **Round 6** | Cost & Performance — token/model/infrastructure waste, latency, unnecessary computation, network use |
| **Round 7** | Security & Safety — **deliberately try to break the system** |
| **Round 8** | Production Engineering — reliability, failure handling, observability, deployment, scaling, recovery, upgrade, migration |
| **Round N** | **Recursive Review** — after every major change, review **everything** again, because one change can create a regression elsewhere. **No cap on the number of rounds** |

**Where:** [`docs/00-council.md`](docs/00-council.md) — the full log, Round 0 through
Round 39, **including the arguments that lost**.

---

## VIII. What level the Implementation Plan must reach

Not just Epic → Story → Task. Every task must fully answer **nine** questions:

**What** · **Why** · **Where** · **How** · **Dependency** · **Contract** · **Failure** ·
**Test** · **Done**

**Where:** [`docs/11-implementation-plan.md`](docs/11-implementation-plan.md) — M0-M5,
every task follows exactly these nine points, with a traceability matrix.

---

## IX. Poka-Yoke for the Implementation Plan itself

Every significant task must have: preconditions, inputs, expected behavior, constraints,
acceptance criteria, a Definition of Done, tests, dependencies.

Two disqualifying conditions:

> - If a developer could understand the task in more than one way → **the task isn't
>   clear enough.**
> - If a developer could implement it wrong and still pass review → **the design isn't
>   Poka-Yoke enough.**

---

## X. Implementation Readiness Gate

After **every round**, the council must self-grade across 16 dimensions:

Architecture · Component Design · Interfaces · Data & State · Security · Cost ·
Performance · Testing · Observability · Deployment · Plugin Architecture · Poka-Yoke ·
Developer Experience · Beginner Experience · Documentation · Implementation Tasks

> **A single critical item graded No → cannot close.** Open another round.

---

## XI. Final Implementation Simulation

Before declaring Ready for Implement, must simulate:

> *"Tomorrow an engineering team starts implementation relying entirely on this
> Implementation Plan."*

Walk through: **Day 1 → Day 2 → First Component → First Integration → First Agent →
First Test → First Deployment**. Find every blocker, then **Fix → Update Plan → Review
Again**.

---

## XII. Absolute stopping conditions

> - Do not stop based on the number of rounds completed.
> - Do not stop because "it's detailed enough."
> - Do not stop because "the council agreed."

Only stop when **all of the following hold at once**:

1. An engineering team can start implementation directly from the Implementation Plan
   without making any further significant architectural decision.
2. A newcomer can use the Harness with minimal cognitive load to create an Agent.
3. The architecture balances **Extensibility + Cost Efficiency + Intelligence + Safety +
   Performance + Simplicity + Developer Experience** without violating **Poka-Yoke +
   SOLID + Clean Code + KISS + Not Over-Engineering**.

---

## XIII. Required deliverables

| Deliverable | Where |
|---|---|
| Final Implementation Plan | [`docs/11-implementation-plan.md`](docs/11-implementation-plan.md) |
| **Design Decision Log** | [`docs/12-decision-logs.md`](docs/12-decision-logs.md) — ADR-001…040 |
| **Implementation Decision Log** | [`docs/12-decision-logs.md`](docs/12-decision-logs.md) — IDL-01…52 |
| **Risk Register** | [`docs/13-risk-register.md`](docs/13-risk-register.md) — R-01…R-23 |
| **Open Issues** (only what is genuinely non-blocking) | [`docs/13-risk-register.md §3`](docs/13-risk-register.md) — OI-1…OI-11 |
| **Definition of Done** | [`docs/11-implementation-plan.md`](docs/11-implementation-plan.md) — per task |
| **Implementation Sequence** | [`docs/11-implementation-plan.md`](docs/11-implementation-plan.md) — M0→M5 |
| **Dependency Graph** | [`docs/11-implementation-plan.md`](docs/11-implementation-plan.md) |
| **Validation Plan** | [`docs/14-validation-plan.md`](docs/14-validation-plan.md) — AC-01…65 |

---

## XIV. The overriding principle

> - **Do not optimize for producing a detailed plan. Optimize for producing a plan that
>   can actually be implemented.**
> - **Do not optimize for architectural sophistication. Optimize for simplicity,
>   extensibility, safety, intelligence, efficiency and usability.**
> - **Do not ask developers to remember how to use the system correctly. Use Poka-Yoke
>   to make the correct way the easiest way.**
> - **Do not expose internal complexity to users unnecessarily.**
> - **Complexity inside. Simplicity outside.**
> - **If the council finds a critical problem, do not document it and move on. Resolve
>   it, update the plan, and review again.**
> - **Keep iterating until the council can confidently say: "This Implementation Plan is
>   ready to implement."**

---

## XV. Required foundation

Verbatim, issued after the council had **recommended the opposite** in Round 34:

> **"Required: build on top of langchain/langgraph, openvikking"**

The council recorded the reversal in one sentence and **did not re-litigate it**.

| Component | Status | Note |
|---|---|---|
| **LangChain / LangGraph** | Done | [`src/harness/lg/`](src/harness/lg/) — LangGraph holds the loop; the safety rules become the **shape of the graph** (ADR-032) |
| **`openvikking`** | Done | The real name is **`openviking`** (one `k`) — [volcengine/OpenViking](https://github.com/volcengine/OpenViking). Initially misreported as "not on PyPI" due to the typo. Integrated through the `Store` seam (ADR-035): [`src/harness/memory/viking.py`](src/harness/memory/viking.py) |

**What this requirement broke, stated plainly:** NFR-05 caps runtime dependencies at
≤ 3. `langgraph` pulls in **36 packages**; the `openviking` server pulls in **185
packages**. The honest solution is an extra: `harness[graph]`, `harness[viking]` — core
stays at 3 dependencies and a 90ms import, with a test enforcing that boundary (AC-46).

---

## XVI. Scope decisions

Settled through direct Q&A in Round 0:

| Question | Decision |
|---|---|
| Language | **Python** |
| Form | **Library-first**, a service considered later |
| Audience & threat model | **Open-source / general-purpose developers**, with **untrusted third-party plugins** in the threat model |

---

## XVII. Requirements that emerged during the work

| # | Verbatim | Outcome |
|---|---|---|
| 1 | *"give me a summary report"* | A consolidated report |
| 2 | *"in short, is the design finished, error-free, ready for implement?"* | A direct answer, with a list of what's still open |
| 3 | *"show me example code for building a multi-capability agent"* | [`examples/support_agent.py`](examples/support_agent.py) — 6 tools across all 4 effect classes, a subagent, `returns=`, a budget, approval, a transcript, `Secret` |
| 4 | *"can the Support Assistant agent run multi-turn, multi-workflow, cross-workflow?"* | Answered, with proof that it runs |
| 5 | *"wouldn't a business process be better managed with a state machine?"* | **Agreed.** [`examples/refund_workflow.py`](examples/refund_workflow.py) + [`docs/06-safety.md §4.1`](docs/06-safety.md) — a state machine is a `Policy`, so it can only **tighten**, never loosen |
| 6 | *"should this harness be built on an existing framework like langchain/langgraph?"* | The council recommended **no** → the project owner **overrode** it in §XV |
| 7 | *"give me an example building an agent with harness + langgraph"* | [`examples/langgraph_quickstart.py`](examples/langgraph_quickstart.py). **Writing this example itself found 3 bugs** — multi-turn had never actually worked (Round 37) |
| 8 | *"write up all of my requirements ... into one file"* | This file |

---

## XVIII. Git process requirements

- Develop on branch **`claude/ai-agent-harness-design-ti5vk3`** (repo `nqthiep/harness`).
- Commit with clear, descriptive messages.
- **Never** push to a different branch without explicit permission.

---

## XIX. Current status against requirements

| Requirement | Status | Evidence |
|---|:--:|---|
| The 5 invariant principles each have their own section, decision, and test | Done | Round 21 counted them; one principle had **no section at all** until then |
| The plugin boundary is determined by a test, not by feel | Done | 5 seams, [`docs/02-architecture.md §4`](docs/02-architecture.md) |
| Cost is an architectural concern | Done | ADR-017/026/029; SC-4 = 95.3% measured for real |
| **Intelligent** — picking the right approach per task | ⚠️ **Partial** | The dials exist (`effort`, `model`, subagent) but **no automatic routing** — the council refused it, with reasons (ADR-006). See §I.4 |
| Safe by Design | Done | 21 red-team tests running in CI; 4 security bugs found and fixed |
| Poka-Yoke | Done | 83 failure modes, ranked on the prevention scale |
| Extreme DX / 10-year-old | ⚠️ **Partial** | Measured: docs at grade 4.2, worst error message at grade 4.9. **Not yet measured with real children** — see SC-1b |
| Zero-to-Agent | Done | A progressive-disclosure ladder + a 5-tier quickstart |
| Rounds 0-8 + recursive Round N | Done | **Round 0 → Round 39**, a full log including the arguments that lost |
| 16-dimension Readiness Gate | Done | [`docs/00-council.md §3`](docs/00-council.md) |
| Final Implementation Simulation | Done | [`docs/14-validation-plan.md §5`](docs/14-validation-plan.md) |
| 9 deliverables from §XIII | Done | Table in §XIII |
| Required foundation (LangChain/LangGraph + OpenViking) | Done | §XV |

### Round 38 — checking the source code against this very file

Four bugs, all cases where **the documentation claimed one thing and the code did
another**:

| # | Bug | Status |
|---|---|---|
| H38.1 | `pause_turn` was never handled — the API says *it can keep running*, the harness said *error, stop*. This is what a server tool (web search) returns, so the bug landed exactly on the feature that produces it | Fixed (ADR-038) |
| H38.2 | IDL-19 claimed "refusal fallbacks are on by default" in **three documents**; the payload never actually carried it | Fixed (ADR-039) |
| H38.3 | **On the very backend that's required**, both `refusal` and `max_tokens` reported `completed` — the user got half an answer labeled as finished | Fixed (ADR-038) |
| H38.4 | `assert_tool_called`/`assert_no_tool` read **intent**, not **behavior** — couldn't tell a blocked tool from one that actually ran | Fixed (IDL-49) |

Three of the four sat on a code path **no test ever exercised**; the fourth was inside the
very helpers used to write tests. The parity table (R-17, point 25) missed H38.3 because
**no parity scenario had ever set the provider's stop reason** — every one of them ended
in `end_turn` or `tool_use`.

> Every time this package is extended, the new code breaks on an **input the old code
> already handled** — never on the feature actually being added.

### Round 39 — running mypy/ruff, closing an item left open from Round 30

| # | Finding | Status |
|---|---|---|
| H39.1 | **`@value` was invisible to the type checker** → `Usage(input_tokns=1)` went through, `Usage(1,2,3,4,5)` went through, and `agent.name` — a public attribute per §03 — was reported as **not existing** for every user who ran mypy. §II question 5 was inverted across the entire data layer | Fixed with PEP 681 (ADR-040); 112 → 0 |
| H39.2 | `returns=` accepted an **instance** instead of a class → a raw `AttributeError`, **after already paying for a model call** | Refused at construction time, with a clear message about what to write instead |
| H39.3 | A lint warning whose obvious fix **would have broken a security test** (a variable keeping a `Secret` alive in ADR-024's weak registry) | Documented with a reason + `noqa` |
| H39.4 | **`ruff --fix` broke the package** — removed a re-export, `import harness` died | Caught because a test ran immediately after (IDL-52) |

**Deliberately NOT done:** 62 of 162 ruff findings were single-line style
(`def spent(self) -> Money: return self._spent`) — used consistently, and rewriting 60
lines of code that already work is risky churn nobody finds more readable. Ruff is
configured to match the project's real style. `mypy --strict` was rejected too. **§III's
"not over-engineer" applies to cleanup as much as to features.**

### Still open — stated plainly, not hidden

| # | Issue | Why it can't close yet |
|---|---|---|
| **SC-1b** | Not yet measured with **real 10-12 year old children** | Needs real people. [`docs/16-sc1b-field-kit.md`](docs/16-sc1b-field-kit.md) is a runnable kit, but the council **does not consider §IV met** until that measurement happens |
| **OI-10** | The OpenViking binding **has never run against a real server** | The server needs an embedding model and a wizard that requires a TTY. The tests run through **the SDK's real code** over a stub transport; the real response content is still unverified |
| **OI-11** | `AnthropicProvider` **has never run against the real API** | No `ANTHROPIC_API_KEY`. The payload is now asserted offline against Anthropic's current documentation (AC-56/57) — that alone caught 3 false claims in Round 38. **What it can't catch:** a parameter the docs describe differently than the endpoint actually behaves. One real call would close this |
| ~~—~~ | ~~mypy / ruff had never been run~~ — **CLOSED (Round 39)** | Run now: 162 ruff errors, 112 mypy errors. The number matters less than this: **no user of this library had type checking on `Money`, `Usage`, `Result`**, and `agent.name` was reported as not existing. Fixed; both are now CI gates (AC-62/63/64) |

---

## XX. Lessons the project owner's own requirements produced

Recorded because they are a direct result of §VI forcing the council to challenge itself.

**23 read-and-review rounds** found 20 bugs and **0 security bugs**.
**16 real-build rounds** found more than 38 bugs, **4 security bugs**, and **12+ features
that were specified but never actually written** — including the model provider.

> **Reading alone cannot find what only running can find.**

The council **declared convergence incorrectly twice** (Round 12 with a 16/16 gate,
Round 23 with "findings are tapering off"). The project owner's §XII — *"do not stop
because the council agreed"* — is the only thing that kept both of those from becoming a
stopping point.

Three bugs recurred in a class already fixed once before (Round 25 → 35, Round 27 → 35,
Round 34 → 35 → 37):

> **A failure class that was named and fixed in this implementation does not mean it's
> fixed in the next one.**

And the Round 37 lesson, found only by writing the documentation itself:

> **A test suite that covers every rule can still miss a usage shape.**
> Every graph scenario called `invoke()` exactly once, so multi-turn had never actually
> worked, and nobody knew.

---

*This record is updated whenever the project owner adds or changes a requirement.*
