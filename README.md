# Harness — Implementation Design Package

> **Status: READY FOR IMPLEMENT** (Council converged at Round 23)
> The complete, reviewed design and implementation plan an engineering team can start from
> without making further architectural decisions — plus the **M0 walking skeleton**
> (`src/harness/`, `tests/`) built in Round 24 to prove the design executes. Both test
> suites run **offline, with no API key and no third-party packages, in under a second**.
>
> ```
> python3 tests/test_walkthrough.py    # 20 tests — the §14.5 acceptance walkthrough
> python3 tests/test_properties.py     # P-1, P-8, P-9 — the budget and mapping invariants
> python3 tests/test_redteam.py        # 21 tests — the §06.8 red-team scenarios
> ```

**Harness** is a Python library for building AI agents that are *cheap to run, hard to
misuse, and easy to start with*.

```
pip install harness
harness setup          # asks for a key, checks it works
harness new joker      # writes joker.py — and the .gitignore that protects your key
python joker.py
```

```python
from harness import Agent
from harness.tools.web import search

helper = Agent(
    name="Helper",
    job="Answer questions using the web. Be brief and cite your sources.",
    tools=[search],
    budget="$0.05",
)

print(helper.run("What time does the Louvre open on Sundays?"))
```

Three concepts (`name`, `job`, `tools`), one method (`run`). Everything else — the agent
loop, prompt caching, budget enforcement, tool permissions, taint tracking, telemetry —
is handled by the harness and invisible until you need it.

**The beginner target is literal.** A ten-year-old who has taken a basic Python course
should be able to build an agent *and give it a tool of their own*. That claim is written
out as a tutorial you can read and judge — [§15 — Your First Agent](docs/15-first-agent.md) —
and it is **measured with real children before 1.0 ships** (SC-1b). Rounds 13–16 exist
because the first four rounds of beginner review approved the API without ever testing
whether anyone could get from an empty folder to a working agent.

---

## The five invariants

Every decision in this package was tested against these. Where they conflicted, the
council recorded the trade and the reasoning in the [Design Decision Log](docs/12-decision-logs.md).

| # | Invariant | How it is enforced, not merely intended |
|---|---|---|
| 1 | **Extensible** | Exactly five plugin boundaries, chosen by an explicit test (§2.4). Everything else is core. |
| 2 | **Cost-efficient** | Budgets are checked *before* each model call, and `max_tokens` is derived from what the budget can afford so the two cannot contradict. The ceiling is **exact on authorization** and **bounded on spend** — measured at 3 violations in 3 000 adversarial runs, worst case 1.008× ([§07.1](docs/07-cost.md#1-the-budget-is-a-ceiling-not-an-alert)). |
| 3 | **Safe by design** | Every tool declares an effect class or fails at import. Untrusted content mechanically blocks irreversible actions. |
| 4 | **Intelligent** | Waste removed, not model calls added: strict tool arguments, optionally typed answers, adaptive thinking, exposed effort, explicit subagents. No planner, no reflection loop — see [§07.6](docs/07-cost.md#6-intelligence-per-unit-of-cost). |
| 5 | **Efficient** | Async core, sync facade, one round-trip per step, truncation ceilings on every tool result. |

Plus the sixth, which shaped the API more than any other: **Poka-Yoke** — see the
[register of 57 failure modes and their design-level defenses](docs/08-poka-yoke.md).
Ten of those came from Round 13, and **five of the six worst beginner blockers turned out
to be outside the API entirely** — credentials, feedback, error rendering, scaffolding and
repeat-run cost.

---

## How to read this package

Read in order if you are new. Jump straight to §11 if you are picking up a task.

| # | Document | What it answers |
|---|---|---|
| 00 | [Council & round log](docs/00-council.md) | Who reviewed this, what they disagreed about, what changed as a result |
| 01 | [Requirements](docs/01-requirements.md) | Goals, non-goals, NFRs, success criteria |
| 02 | [Architecture](docs/02-architecture.md) | Layers, module map, the plugin-boundary test, the run loop |
| 03 | [Public API](docs/03-public-api.md) | The DX contract, progressive disclosure ladder, every public symbol |
| 04 | [Interfaces](docs/04-interfaces.md) | Exact signatures for every protocol and data type |
| 05 | [Data & state](docs/05-data-and-state.md) | Event taxonomy, transcript format, memory schema, resume semantics |
| 06 | [Safety](docs/06-safety.md) | Threat model, effect lattice, taint tracking, policy engine, secrets |
| 07 | [Cost](docs/07-cost.md) | Budget enforcement, cache-safety-by-construction, pricing, context management |
| 08 | [Poka-Yoke register](docs/08-poka-yoke.md) | 34 ways to get it wrong, and why each one is designed out |
| 09 | [Testing](docs/09-testing.md) | Test pyramid, fakes, golden transcripts, red-team suite, CI gates |
| 10 | [Observability & operations](docs/10-observability-ops.md) | Events, OTel, errors, versioning, release, migration |
| 11 | [**Implementation plan**](docs/11-implementation-plan.md) | **Milestones → epics → tasks, each with contract, failure mode, tests, DoD** |
| 12 | [Decision logs](docs/12-decision-logs.md) | ADRs and implementation decisions, with the losing arguments recorded |
| 13 | [Risk register & open issues](docs/13-risk-register.md) | What could still go wrong, and what is explicitly deferred |
| 14 | [Validation plan](docs/14-validation-plan.md) | How we prove the implementation actually matches this design |
| 15 | [**Your First Agent**](docs/15-first-agent.md) | The child-facing tutorial, written out in full as evidence rather than described |

---

## Scope at a glance

**In scope for v1.0:** the library, one model provider (Anthropic), the safety and budget
core, deterministic caching, transcripts and replay, a SQLite memory store, subagents, the
plugin registry, and a CLI whose `setup`/`new`/`chat` commands ship in the **first**
milestone because first-run experience is built first or not at all.

**Explicitly out of scope for v1.0** (with reasons in [§01](docs/01-requirements.md#5-non-goals)):
a hosted service, a durable workflow engine, plugin sandboxing, LLM-based model routing,
a visual builder, and a vector database.

## Repository conventions once code lands

- Python 3.11+, `src/` layout, `uv` for env management, `ruff` + `mypy --strict` in CI.
- Public API is everything exported from `harness/__init__.py`; nothing else is stable.
- Every module in the plan maps 1:1 to a path listed in [§2.5](docs/02-architecture.md).
