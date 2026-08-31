# Harness

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
out as a tutorial you can read and judge — [§15 — Your First Agent](docs/15-first-agent.md).

---

## Status

Every milestone this project set for itself is built, tested, and documented:
**core safety and cost engine, both run-loop backends, and the full M6–M10 growth
roadmap** (reliability, isolation, observability, integration, evaluation). Nothing
here is a plan waiting to be implemented.

```
python -m pytest -q          # 613 tests, offline, no API key, a few seconds
ruff check src tests examples
mypy
python3 examples/proof.py    # walks HARNESS.md's requirements and asserts each in code
python3 tests/test_roadmap.py  # prints how the M6–M10 growth roadmap stands, live
```

One `Agent`, one API. Underneath, two engines: a **classic** async loop (no extra
dependencies) and a **LangGraph-backed** one (`harness[graph]`) that adds durable
checkpointing — a run that survives a process restart. `Agent(durable=True)` runs on the
second engine through the exact same methods as the first — no LangChain vocabulary
reaches the caller either way (`docs/03-public-api.md §3.5`). The raw compiled graph
(`harness.lg.build_agent`) stays directly available as an escape hatch for LangGraph
itself. `tests/test_parity.py` states every safety rule once and runs it against all
three call shapes; a row that differs is a defect, never a documented difference.

Measured against an independent study of 12 frameworks and 9 harnesses
([`docs/17`](docs/17-research-alignment.md)): a self-scored **67.8/100** on that study's
weighted matrix *before* the growth roadmap — strong on Safety, Cost, Testability, DX;
weak on Integration, Performance, Observability, Reliability. M6–M10 closed the concrete
gaps that score named (see `design/08-roadmap-and-release-plan.md §3.2` for what closed
each one); re-scoring against the original study's own rigor needs a human grader, not
another self-assessment — that table is honest about the difference.

---

## What's in the box

**Core** (`pip install harness` — three dependencies, imports in well under 100ms):
budget ledger with pre-flight reservation, a two-axis taint/confidentiality lattice, an
effect-classified tool system (`read`/`write`/`external`/`danger` — one declaration,
five derived behaviours), an approval system where every decision is an immutable,
auditable record, deterministic prompt caching, transcripts + resume, a SQLite memory
store, subagents, idempotency and cancellation primitives, `harness.middleware` for
cross-cutting behavior (logging, caching, retries — `docs/03-public-api.md §3.6`), and a
CLI.

**Extras** — each is optional, none is imported by `import harness`:

| Extra | What it adds |
|---|---|
| `harness[graph]` | Durability: `Agent(durable=True)` (`docs/03-public-api.md §3.5`) and the raw `harness.lg.build_agent` escape hatch — checkpointed, resumable across process restarts. |
| `harness[viking]` | `VikingStore` — semantic recall over [OpenViking](https://github.com/volcengine/OpenViking), classified `external` (it taints — anything a retrieval store hands back is untrusted). |
| `harness[otel]` | A real OpenTelemetry exporter — spans and metrics on the mapping `docs/10-observability-ops.md §2` publishes. |
| `harness[mcp]` | `harness.mcp.connect()` — turn a Model Context Protocol server into classified `ToolSpec`s. A third-party MCP server is never a security boundary; the policy engine still gates every call (`design/03-tools-and-mcp.md §5`). |
| `harness[server]` | `harness.server.create_app()` — an ASGI Service API (`POST /v1/runs`, SSE event streaming, approvals, cancel). Bring your own ASGI server and your own auth. |
| — (no extra; stdlib + `jsonschema`, already core) | `harness.eval` — trajectory contracts, golden-set pass rate with a confidence interval, latency/throughput/cold-start benchmarks. |

Start here to build an agent: [§15 — Your First Agent](docs/15-first-agent.md) — a
ten-year-old's first tool, five minutes in.

Start here for durability: [`examples/durable_agent.py`](examples/durable_agent.py) —
`Agent(durable=True)`, one API, a simulated process restart mid-conversation.

Start here for a production-shaped agent: [`examples/full_agent.py`](examples/full_agent.py)
— one set of tools and policies exercising every capability, on both engines.

Start here for the raw LangGraph escape hatch: [`examples/langgraph_quickstart.py`](examples/langgraph_quickstart.py)
— five steps, each adding exactly one concept, for a power user who wants LangGraph
itself rather than `durable=True`.

Start here to judge it: [`examples/proof.py`](examples/proof.py) — walks every
requirement in [`HARNESS.md`](HARNESS.md) and asserts each one in running code.

---

## The five invariants

Every decision in this design was tested against these. Where they conflicted, the
[Decision Log](docs/12-decision-logs.md) records the trade and the reasoning.

| # | Invariant | How it is enforced, not merely intended |
|---|---|---|
| 1 | **Extensible** | Six plugin seams (Tool, ModelProvider, Store, Policy, Exporter, Sandbox), each chosen by an explicit three-part test ([§02.4](docs/02-architecture.md#4-what-is-a-plugin--the-test)). Everything else is core. |
| 2 | **Cost-efficient** | Budgets are checked *before* each model call, and `max_tokens` is derived from what the budget can afford so the two cannot contradict. Cache reads measured at 95.3% on turn 3+ ([§07.1](docs/07-cost.md#1-the-budget-is-a-ceiling-not-an-alert)). |
| 3 | **Safe by design** | Every tool declares an effect class or fails at import. Untrusted content mechanically blocks irreversible actions. Egress denies by default. |
| 4 | **Intelligent** | Waste removed, not model calls added: strict tool arguments, optionally typed answers, adaptive thinking, exposed effort, explicit subagents. No automatic model routing — the council rejected it, on the record ([§07.6](docs/07-cost.md#6-intelligence-per-unit-of-cost)). |
| 5 | **Efficient** | Async core, sync facade, one round-trip per step, truncation ceilings on every tool result. |

Plus a sixth that shaped the API more than any other: **Poka-Yoke** — see the
[register of failure modes and their design-level defenses](docs/08-poka-yoke.md).

---

## How to read this repository

Read in order if you are new. Jump straight to §11 if you are picking up a task.

| # | Document | What it answers |
|---|---|---|
| 00 | [Council & round log](docs/00-council.md) | Who reviewed this, what they disagreed about, what changed as a result |
| 01 | [Requirements](docs/01-requirements.md) | Goals, non-goals, NFRs, success criteria |
| 02 | [Architecture](docs/02-architecture.md) | Layers, module map, the plugin-boundary test, the run loop |
| 03 | [Public API](docs/03-public-api.md) | The DX contract, progressive disclosure ladder, every public symbol |
| 04 | [Interfaces](docs/04-interfaces.md) | Exact signatures for every protocol and data type, including MCP and the Service API |
| 05 | [Data & state](docs/05-data-and-state.md) | Event taxonomy, transcript format, memory schema, resume semantics |
| 06 | [Safety](docs/06-safety.md) | Threat model, effect lattice, taint tracking, policy engine, secrets |
| 07 | [Cost](docs/07-cost.md) | Budget enforcement, cache-safety-by-construction, pricing, context management |
| 08 | [Poka-Yoke register](docs/08-poka-yoke.md) | Ways to get it wrong, and why each one is designed out |
| 09 | [Testing](docs/09-testing.md) | Test pyramid, fakes, golden transcripts, red-team suite, eval tooling, CI gates |
| 10 | [Observability & operations](docs/10-observability-ops.md) | Events, OTel, errors, versioning, release, migration |
| 11 | [Implementation plan](docs/11-implementation-plan.md) | M0–M5's milestones → epics → tasks, each with contract, failure mode, tests, DoD |
| 12 | [Decision logs](docs/12-decision-logs.md) | Every ADR and implementation decision, with the losing arguments recorded |
| 13 | [Risk register & open issues](docs/13-risk-register.md) | What could still go wrong, and what is explicitly deferred |
| 14 | [Validation plan](docs/14-validation-plan.md) | How the implementation was proven to match this design |
| 15 | [Your First Agent](docs/15-first-agent.md) | The child-facing tutorial, written out in full as evidence rather than described |
| 16 | [SC-1b field kit](docs/16-sc1b-field-kit.md) | The runnable study protocol for testing the beginner claim with real children |
| 17 | [Research alignment](docs/17-research-alignment.md) | Gap analysis against an independent 12-framework study, and the M6–M10 plan that closed it |

**[`design/`](design/)** holds the working history behind `docs/` — the original design
package, two independent adversarial reviews, and the running risk/roadmap log. Start
at [`design/README.md`](design/README.md) if you want the *why*, not just the *what*.

**[`HARNESS.md`](HARNESS.md)** is the project owner's requirements, verbatim, each one
traced to where it is met — read it to judge the design against its brief rather than
against itself.

---

## Scope

**Built:** the library, one model provider (Anthropic), the safety and budget core,
deterministic caching, transcripts and replay, a SQLite memory store, subagents, the
plugin registry, a CLI, both run-loop backends, an MCP client, an ASGI Service API, and
an evaluation toolkit.

**Deliberately out of scope**, with reasons in [§01](docs/01-requirements.md#5-non-goals):
a durable-execution engine (Temporal-style), automatic LLM-based model routing, a vector
database / RAG engine, arbitrary multi-agent orchestration graphs, and a visual builder.
Three other non-goals — a hosted service, plugin sandboxing, an evals platform — got a
narrow, opt-in version through the growth roadmap without reversing the reasoning next
to them; §01 marks exactly which and why.

## Repository conventions

- Python 3.11+, `src/` layout, `ruff` + `mypy` in CI.
- Public API is everything exported from `harness/__init__.py`; nothing else is stable.
- Every module maps 1:1 to a path listed in [§02.5](docs/02-architecture.md).
