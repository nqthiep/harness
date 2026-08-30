# Workflow / Orchestration, and Developer Experience

Sections §12 and §22 of the research brief — the two the earlier files did not
reach. **Data collected on: 2026-08-30.**

---

## §12 Workflow and orchestration

Control-flow vocabulary per kLOC, license headers excluded, standouts verified
by reading:

| package | kLOC | graph | conditional | loop | event | declarative | visualize | recover |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **langgraph** 1.2.11 | 27 | **29.2** | 3.2 | 6.1 | 6.0 | 0.6 | 0.2 ᵃ | **51.6** |
| **autogen-agentchat** 0.7.5 | 11 | **19.0** | 1.1 | 5.9 | **8.6** | 6.5 | 0.0 | 2.3 |
| **haystack-ai** 3.1.0 | 54 | 2.9 | 4.8 | 3.1 | 0.7 | **22.7** | **1.3** | 0.3 |
| agent-framework-core 1.16.0 | 61 | 2.0 | 0.7 | 7.9 | 3.7 | 10.4 | 0.6 | 16.3 |
| crewai 1.15.18 | 117 | 0.5 | 5.1 | 2.4 | 7.8 | 1.9 | 0.2 | 7.3 |
| pydantic-ai-slim 2.36.0 | 119 | 3.1 | 3.0 | 4.2 | 2.6 | 3.2 | 0.0 | 3.9 |
| google-adk 2.8.0 | 168 | 1.3 | 3.4 | 4.3 | 1.8 | 1.8 | 0.2 | 2.3 |
| openai-agents 0.22.0 | 125 | 0.2 | 2.6 | 3.9 | 0.8 | 3.4 | 0.1 | 5.3 |
| agno 3.0.1 | 419 | 0.4 | 5.0 | 4.4 | 2.2 | 5.3 | 0.0 | 2.4 |
| semantic-kernel 1.44.1 | 81 | 0.0 | 0.3 | 2.5 | 1.2 | 3.4 | 0.0 | 0.1 |

Verified: langgraph's `recover` is 502 `checkpoint` + 308 `checkpointer` + 88
`resume` — genuine. haystack's `declarative` is 253 `serialized` + 230 `to_dict`
+ 184 `from_dict` — genuine. langgraph's `graph` includes 453 bare uses of the
word, which is the package's domain rather than a distinct mechanism; the
mechanism-specific part (`StateGraph` 78, `add_node` 39, `add_edge` 24) is
smaller and still the highest in the set.

**ᵃ The visualisation column understates LangGraph, and that is a limit of
per-package measurement.** `Pregel.get_graph()` (`pregel/main.py:845`) returns a
`langchain_core` `Graph`, which carries `draw_mermaid`, `draw_ascii`, and
`draw_png`. The capability is real; it lives in the dependency. Measured where
it actually is, the ranking is haystack (80 own hits), agent-framework (33),
langgraph (via langchain-core), and **crewai: zero**.

### Three orchestration philosophies, not ten

The primitives the brief lists — sequential, parallel, conditional, loop, retry,
branch, approval, event-driven, graph, DAG, state machine — do not sort the
field into eleven groups. They sort it into three.

**1. Explicit graph (langgraph, autogen).** The topology is a data structure you
build before running: nodes, edges, conditional edges. Everything else follows —
you can checkpoint it (langgraph, 51.6), draw it, resume it at a node, and
statically prove properties of it. autogen reaches the same place through an
event bus (8.6) rather than edges, which is the same idea with the topology
implied by message routing rather than declared.

**2. Declarative pipeline (haystack).** The topology is *serialisable*: 22.7
declarative density, `to_dict`/`from_dict` on every component, and the only
first-class visualisation in the study. A haystack pipeline round-trips to YAML,
which makes it inspectable and diffable by people who do not read Python. The
cost is that it is a pipeline — good for retrieval and generation flows, weaker
where control flow is genuinely dynamic.

**3. Imperative loop (openai-agents, agno, google-adk, pydantic-ai).** There is
no topology. There is a `while` loop, a model, a tool table, and `max_turns`.
openai-agents scores 0.2 on `graph` because it has no graph — and this is the
design, not a deficiency. Handoffs (`09-…` §13) provide routing without a
declared structure.

### Deterministic vs autonomous is the real axis

The brief asks for this evaluation, and it is where the three groups separate
cleanly:

| | topology known before run | recoverable mid-run | inspectable by a non-author |
|---|---|---|---|
| explicit graph | **yes** | **yes** (langgraph 51.6) | yes, drawn |
| declarative pipeline | **yes** | no (haystack `recover` 0.3) | **yes, as YAML** |
| imperative loop | no | partially (openai-agents 5.3) | no |

The trade is not "power vs simplicity", which is how it is usually framed. It is
**how much you can know before the model runs.** An imperative loop cannot tell
you what it will do, so it cannot be checkpointed at a meaningful boundary,
statically checked, or drawn. A graph can. That is the entire reason
`langgraph`'s checkpoint density is an order of magnitude above the field: it is
not that they cared more about durability, it is that **having a topology is
what makes durability implementable**.

Haystack's row is the interesting one: fully declarative and almost entirely
unrecoverable (0.3). Serialising the *definition* is a different problem from
checkpointing the *execution*, and solving the first buys nothing toward the
second.

> **For a harness, the graph is not a feature, it is the precondition.**
> Durability, a static guard check, and a drawable topology are three
> consequences of one decision. This is the strongest architectural argument
> found in the study for building on a graph runtime rather than a loop — and
> it is an argument from the measurements, not from taste.

### What nobody has

- **State machines as a first-class construct**: near-zero everywhere (max 0.3).
  Agent state is modelled as accumulated messages, not as a declared set of
  states with legal transitions. Every "the agent must not refund before
  verifying" rule in the field is enforced by prompt or by hand-written
  conditionals.
- **Visualisation** outside haystack, agent-framework, and langgraph-via-core.
  Debuggability is marketed far more than it is built.

---

## §22 Developer experience

Measured rather than reviewed, because "good docs" is not a finding.

| package | `py.typed` | docstrings/kLOC | errors with context | `.pyi` stubs | required deps |
|---|---|---:|---:|---:|---:|
| pydantic-ai-slim 2.36.0 | yes | **38.0** | 43% | 0 | 9 |
| llama-index-core 0.14.24 | yes | 37.9 | 27% | 0 | 29 |
| haystack-ai 3.1.0 | yes | 34.7 | **48%** | 0 | 18 |
| crewai 1.15.18 | yes | 33.1 | 42% | 0 | 31 |
| autogen-agentchat 0.7.5 | yes | 32.5 | 43% | 0 | 1 |
| agent-framework-core 1.16.0 | yes | 30.6 | 43% | **23** | 5 |
| langchain 1.3.18 | yes | 30.9 | — | 0 | 3 |
| google-adk 2.8.0 | yes | 28.3 | 46% | 0 | 25 |
| openai-agents 0.22.0 | yes | 25.0 | 30% | 0 | 7 |
| langgraph 1.2.11 | yes | 23.9 | 39% | 0 | 6 |
| agno 3.0.1 | yes | 22.2 | 42% | 0 | 10 |
| **smolagents 1.26.0** | **no** | 21.3 | **61%** | 0 | 6 |

*"Errors with context" is the share of `raise …Error(…)` sites using an f-string
— an error that interpolates the offending value rather than stating a generic
condition. It is a proxy for whether a failure tells you what failed, not a
measure of message quality.*

### Type support has converged; nothing else has

**Eleven of twelve ship `py.typed`.** Two years ago this table would have been
mostly "no". Type hints are now table stakes in this ecosystem, and
`smolagents` is the lone holdout — meaning an IDE gives you no completion and
`mypy` sees `Any` throughout a library whose selling point is that agents write
code.

Everything else varies by 2–3×, and the variation does not track project size,
backing, or popularity. Microsoft is the only project shipping `.pyi` stubs
(23), which is what you do when the runtime type is dynamic but you still want
the editor to be right.

smolagents' 61% error-context rate — the best in the set — alongside no
`py.typed` is a useful reminder that DX is not one dimension. It fails you at
edit time and helps you at debug time.

### The question the brief asks

> *"How long does a new developer need to build a production-ready agent?"*

The honest answer separates two things the question runs together.

**To a running agent: under an hour, in any of them.** Every project ships a
quickstart that works. This is a solved problem and not a differentiator.

**To a *production-ready* agent: the frameworks do not get you there, and the
gap is not documentation.** The five properties a production deployment needs
are the five this study measured, and the measurements say:

| property | what the field provides |
|---|---|
| **cost ceiling** | Nothing reserves budget before a model call; `max_turns` bounds steps, not spend. Cost density is near-zero outside pydantic-ai and browser-use (`03-…` §20). |
| **safe by design** | Approval is permission state, never an auditable decision — 30 packages, 3 languages (`09-…` §14, `10-…` §28). The one real information-flow lattice is unwired by its own harness (`09-…` §16bis). |
| **reliability** | No project provides idempotency at the tool-call level; agno protects run submission only (`07-…`). Tool-error policy is per-tool opt-in, and LangChain's default kills the run (`08-…` §8.2). |
| **observability** | Genuine only in google-adk (11.4 OTel/kLOC in Java, `10-…` §28) and thin elsewhere. |
| **recoverability** | Real in langgraph (51.6) and essentially absent in the imperative-loop group. |

So the realistic answer: **an hour to a demo, and then the production work is
yours** — budget ceilings, an approval record with an actor, taint propagation,
idempotency for effectful tools. A new developer's timeline is not set by the
framework's learning curve; it is set by how much of that list they discover
they have to build, and they usually discover it in production.

That is the same conclusion `05-ideal-harness.md` reaches from the design side,
arrived at here from the developer's side. It is also the argument for a harness
as a distinct layer: these five properties are not what a framework is for, and
no amount of documentation closes the gap.

---

## §45 Evidence limits

- **Per-package measurement misses inherited capability.** The visualisation
  column understated LangGraph because `draw_mermaid` lives in `langchain-core`.
  This affects any tightly-coupled package pair; where it was caught it is
  noted, and it may not have been caught everywhere.
- **"Errors with context" measures form, not quality.** An f-string error can
  still be useless, and a constant-string error can be excellent. It is a proxy
  and is labelled as one.
- Docstring density counts `"""` openers halved; module and class docstrings
  count the same as function ones.
- **Installation time, IDE behaviour, community responsiveness, and learning
  curve were not measured.** They would need timing runs and user studies.
  **Chưa đủ evidence** — the DX claims here rest only on what is in the
  distributions.
- The workflow taxonomy is drawn from ten packages; smaller orchestration
  libraries were out of scope (`01-…` §2).

---

## Sources

Packages and versions as listed in `09-memory-context-multiagent-hitl.md`
§Sources and `10-governance-health-languages.md` §Sources. Probe sets recorded
in `research/harvest.py`; the workflow and DX probes used here are reproduced in
the commit that introduced this file.
