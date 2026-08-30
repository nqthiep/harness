# License, Governance, Repository Health, and Language Support

Sections §25–§28 of the research brief.

**Data collected on: 2026-08-30.** Repository metrics come from the GitHub
search API on that date; licenses and dependency counts from the installed
package metadata (PyPI wheels, Maven Central source jars) of the versions
named. Star counts move daily — treat them as an order of magnitude, not a
measurement.

This section also closes the Java question that three earlier files left as
*Chưa đủ evidence*, and reports a **methodological error that invalidated one
number already published in this study**.

---

## §26.1 The correction: license headers are not code

Measuring the Java packages surfaced a defect in the probe method that had
already produced a wrong published number.

Spring AI scored **11.8 `permission` hits per kLOC** — the highest permission
density of any Java package, and higher than all but one Python package. It
would have been the headline of this section.

It is entirely the Apache-2.0 license header:

> `* See the License for the specific language governing permissions and`

Every `.java` file carries it. Of 294 matches in `spring-ai-model` and
`spring-ai-client-chat`, **292 are that sentence**. The two real hits are both
the same field, an allowlist of tool exceptions to unwrap. Corrected density:
**0.03/kLOC**, not 11.8 — a 390× overstatement.

The same check across the Java set:

| package | raw hits | license header | real | published density → corrected |
|---|---:|---:|---:|---|
| spring-ai (model + client-chat) | 294 | 292 (99%) | 2 | 11.8 → **0.03** |
| google-adk-java 1.8.0 | 232 | 211 (91%) | 21 | 4.8 → **0.43** |
| langchain4j 1.19.0 | 0 | 0 | 0 | 0.0 |

**This affects Java systematically**, because Java convention puts the license
header in every source file. It affects Python only where a project follows the
same convention.

### Auditing this study's own published numbers

Eight Python packages re-checked, excluding lines matching
`governing permissions|under the License|Licensed under`:

| package | raw | real | inflation |
|---|---:|---:|---:|
| **google-adk 2.8.0** | 902 | 216 | **76%** |
| agno, mem0ai, letta-client, haystack-ai, langchain-core, openai-agents, agent-framework | — | — | **0%** |

**google-adk is the only Python package in this study that puts the Apache
header in every source file**, and it is the only Python number that was wrong.
`00-executive-summary.md` published its `permission` density as **5.3**; the
correct value is **216 / 171.4 kLOC = 1.3**. That file has been corrected in
place with a dated note explaining why.

The correction changes an interpretation, not just a digit. At 5.3 google-adk
looked like a mid-table project with meaningful permission machinery. At 1.3 it
sits with the majority that have almost none, and smolagents' 16.3 — verified
earlier by reading code — stands even further clear of the field.

**Method update, now recorded in `research/harvest.py`:** exclude license-header
lines before counting, and treat any language with a per-file header convention
(Java, Go, C#, Kotlin) as requiring that exclusion by default. The general rule
this study has applied throughout — *verify every standout by reading the code*
— is what caught it. The standout was checked; it did not survive.

---

## §25 Licenses

Read from installed package metadata, not from the repository README.

| package | version | license (metadata) | license (repo) |
|---|---|---|---|
| langgraph, langgraph-checkpoint, langchain, langchain-core, langmem | 1.2.11 / 4.2.0 / 1.3.18 / 1.6.1 / 0.0.30 | MIT | MIT |
| openai-agents | 0.22.0 | MIT | MIT |
| agent-framework-core | 1.16.0 | MIT | MIT |
| autogen-core, autogen-agentchat | 0.7.5 | MIT | MIT |
| semantic-kernel | 1.44.1 | MIT | MIT |
| pydantic-ai, pydantic-ai-slim | 2.36.0 | MIT | MIT |
| llama-index-core | 0.14.24 | MIT | MIT |
| dspy | 3.3.1 | MIT | MIT |
| mcp | 2.1.1 | MIT | MIT |
| browser-use | 0.13.8 | MIT | MIT |
| haystack-ai | 3.1.0 | Apache-2.0 | Apache-2.0 |
| mem0ai | 2.0.19 | Apache-2.0 | Apache-2.0 |
| letta-client | 1.12.1 | Apache-2.0 | Apache-2.0 |
| google-adk | 2.8.0 | Apache-2.0 | Apache-2.0 |
| agno | 3.0.1 | Apache-2.0 (classifier only) | Apache-2.0 |
| smolagents | 1.26.0 | Apache-2.0 (LICENSE file only) | Apache-2.0 |
| **crewai** | **1.15.18** | **none declared** | **MIT** |
| langchain4j, langchain4j-core | 1.19.0 | Apache-2.0 | Apache-2.0 |
| spring-ai | 2.0.1 | Apache-2.0 | Apache-2.0 |
| google-adk (Java) | 1.8.0 | Apache-2.0 | Apache-2.0 |

**Every project surveyed is permissively licensed** — MIT or Apache-2.0, no
copyleft, no source-available or BSL licence anywhere in the set. For a harness
that must vendor or fork a dependency, this is the good case: nothing here
constrains a commercial derivative.

Two packaging defects, both real and both invisible to anyone reading the repo:

- **crewai 1.15.18 declares no license in its wheel.** No `License-Expression`,
  no `License` field, no `Classifier: License ::`, and no `LICENSE` file in the
  distribution. The repository is MIT, so the *legal* answer is clear — but an
  SBOM generator, a license scanner, or a corporate dependency gate reading the
  installed artefact finds nothing, and "unknown license" is what most such
  gates block on. The fix is one line of `pyproject.toml`.
- **agno and smolagents** declare a license only via a classifier or a bundled
  `LICENSE` file, not the machine-readable `License-Expression` field that
  PEP 639 standardises. Detectable, but only by tools that look in the second
  place.

For the four projects that get it right — MIT or Apache in
`License-Expression` — a scanner needs no heuristics.

---

## §27 Repository health

| project | stars | forks | open issues | issues/k-star | created | language |
|---|---:|---:|---:|---:|---|---|
| browser-use/browser-use | 111,702 | 12,260 | 388 | 3.5 | 2024-10 | Python |
| mem0ai/mem0 | 64,345 | 7,540 | 704 | 10.9 | 2023-06 | Python |
| microsoft/autogen | 60,699 | 9,165 | 996 | 16.4 | 2023-08 | Python |
| crewAIInc/crewAI | 57,818 | 8,287 | 771 | 13.3 | 2023-10 | Python |
| run-llama/llama_index | 51,917 | 8,053 | 668 | 12.9 | 2022-11 | Python |
| agno-agi/agno | 41,971 | 5,842 | 1,284 | 30.6 | 2022-05 | Python |
| langchain-ai/langgraph | 40,697 | 6,862 | 724 | 17.8 | 2023-08 | Python |
| stanfordnlp/dspy | 37,656 | 3,271 | 642 | 17.0 | 2023-01 | Python |
| **openai/openai-agents-python** | 29,069 | 4,631 | **64** | **2.2** | 2025-03 | Python |
| huggingface/smolagents | 29,048 | 2,905 | 741 | 25.5 | 2024-12 | Python |
| microsoft/semantic-kernel | 28,519 | 4,747 | 264 | 9.3 | 2023-02 | **C#** |
| deepset-ai/haystack | 26,362 | 3,052 | 116 | 4.4 | 2019-11 | Python |
| a2aproject/A2A | 25,551 | 2,591 | 240 | 9.4 | 2025-03 | (protocol) |
| **letta-ai/letta** | 24,492 | 2,601 | **39** | **1.6** | 2023-10 | — |
| google/adk-python | 21,328 | 3,913 | 513 | 24.1 | 2025-04 | Python |
| pydantic/pydantic-ai | 19,581 | 2,615 | 757 | 38.7 | 2024-06 | Python |
| microsoft/agent-framework | 13,217 | 2,240 | 645 | **48.8** | 2025-04 | Python |

Every repository in the set was pushed to within 48 hours of collection. None
is archived or disabled. **Abandonment is not a risk in this ecosystem; churn
is** — see §27.2.

### §27.1 What issues-per-k-star does and does not say

The spread is 30×, from letta at 1.6 to microsoft/agent-framework at 48.8. It is
a real signal but a noisy one, and it measures at least three different things
at once:

- **Triage discipline.** openai-agents at 2.2 with 29k stars is genuinely
  exceptional: a large, young, heavily-used repo holding 64 open issues means
  someone closes them.
- **Where issues are allowed to live.** letta's 1.6 is partly that its Python
  package is a *generated client* and the substantive work sits in the server
  repo. haystack's 4.4 comes with GitHub Discussions absorbing questions.
- **Age and surface.** agent-framework's 48.8 is a four-month-old repository
  spanning Python *and* .NET, published alongside a migration path from two
  predecessor frameworks. High open-issue counts on a new, broad, actively
  adopted project are expected.

Read it as "how much unresolved work is visible", never as "how good is this
software". A project can post a beautiful ratio by closing issues as stale.

### §27.2 The real governance risk is version churn, not abandonment

The versions this study measured, against project age:

- **agno 3.0.1** — a major version, on a repo created 2022-05.
- **crewai 1.15.18** — 1.x, but with 15 minors.
- **pydantic-ai 2.36.0** — 36 minors inside 2.x, on a repo created 2024-06.
- **langgraph 1.2.11**, **langchain 1.3.18** — both reached 1.0 recently.
- **mcp 2.1.1** — the protocol SDK is itself on a second major version.
- **agent-framework-core 1.16.0** — 16 minors in roughly four months.

For a harness, this is the governance finding that matters. Every one of these
projects is moving fast enough that a pinned integration will be a version
behind within weeks, and a *major* version behind within a year. The
architectural consequence is the same one this study reaches from four other
directions: **depend on a narrow surface of these libraries, and own the
adapter.** A harness that spreads its dependency across 40 call sites of a
framework's API inherits that framework's release cadence as its own migration
schedule.

### §27.3 "Harness" has become a self-description

Repository topics, which maintainers choose:

- `openai/openai-agents-python` — topics include **`harness`**
- `pydantic/pydantic-ai` — topics include **`harness`**, **`harness-engineering`**

Two of the most-used agent projects now label themselves with the word this
study spent §1 defining, and pydantic goes further with `harness-engineering`
as a named discipline. That is external evidence for the framework/harness
distinction in `01-landscape-and-taxonomy.md`: the split is not this study's
invention, it is being adopted as vocabulary by the field.

---

## §26.2 Cost of adoption: required vs optional dependencies

`Requires-Dist` from each wheel, split by whether the dependency is behind an
`extra` marker. The split matters: a naive count of all `Requires-Dist` lines
puts agno at **380 dependencies**, which is true and deeply misleading.

| package | required | optional | note |
|---|---:|---:|---|
| autogen-agentchat 0.7.5 | **1** | 0 | |
| pydantic-ai 2.36.0 | **1** | 27 | meta-package over `-slim` |
| langgraph-checkpoint 4.2.0 | 2 | 0 | |
| langchain 1.3.18 | 3 | 18 | |
| agent-framework-core 1.16.0 | **5** | 32 | |
| langgraph 1.2.11 | **6** | 0 | |
| autogen-core 0.7.5 | 6 | 0 | |
| letta-client 1.12.1 | 6 | 2 | |
| smolagents 1.26.0 | 6 | 47 | |
| openai-agents 0.22.0 | **7** | 26 | |
| langmem 0.0.30 | 8 | 0 | |
| mem0ai 2.0.19 | 8 | 48 | |
| **agno 3.0.1** | **10** | **370** | lean core, vast integration surface |
| langchain-core 1.6.1 | 10 | 0 | |
| dspy 3.3.1 | 14 | 27 | |
| mcp 2.1.1 | 16 | 3 | |
| haystack-ai 3.1.0 | **18** | **0** | all mandatory |
| google-adk 2.8.0 | 25 | 224 | |
| semantic-kernel 1.44.1 | 25 | 43 | |
| **llama-index-core 0.14.24** | **29** | **0** | all mandatory |
| crewai 1.15.18 | 31 | 25 | |
| browser-use 0.13.8 | 36 | 24 | |

Three groups, and the useful distinction is not size but **whether the size is
optional**:

1. **Genuinely minimal cores** — langgraph (6, zero extras), agent-framework-core
   (5), openai-agents (7). You can install these into a constrained environment
   and know what you got.
2. **Lean core, huge optional surface** — agno (10 + 370), google-adk (25 + 224),
   mem0 (8 + 48). The eye-catching totals are integration breadth, correctly
   gated behind extras. This is the right way to ship a large ecosystem, and
   agno's 380 is a *credit* to its packaging once the split is visible.
3. **Everything mandatory** — llama-index-core (29 + 0), haystack-ai (18 + 0),
   browser-use (36 + 24), crewai (31 + 25). Here the cost is unavoidable: you
   take 29 transitive trees to import `llama_index.core`, whether you use the
   vector stores or not. For a cost- and supply-chain-conscious harness this is
   the group to avoid, and it is not visible from a stars-and-README comparison.

**langgraph at 6 required and 0 optional is the strongest result in this table**,
and it is consistent with the checkpoint density finding (21.1/kLOC Python,
23.4 TypeScript): the project does one thing and does not drag an ecosystem in
behind it.

---

## §28 Language support, and the Java answer

### The Java ecosystem is real, and it is one-tenth the size

The question left open in three earlier files. It is now answerable.

| project | stars | open issues | language | created |
|---|---:|---:|---|---|
| langchain4j/langchain4j | 12,976 | 873 | Java | 2023-06 |
| alibaba/spring-ai-alibaba | 10,742 | 149 | Java | 2024-09 |
| spring-projects/spring-ai | 9,381 | **1,456** | Java | 2023-06 |
| ageerle/ruoyi-ai | 5,672 | 6 | Java | 2024-01 |
| embabel/embabel-agent | 4,407 | 59 | **Kotlin** | 2025-04 |
| google/adk-java | 1,709 | 99 | Java | 2025-05 |
| agents-flex/agents-flex | 1,043 | 1 | Java | 2024-01 |

A GitHub search for agent frameworks in Java above 1,000 stars returns **three
results**. The equivalent Python search returns dozens.

- The **largest** Java agent framework (langchain4j, 13.0k) would rank
  **below tenth** among the Python projects in §27.
- The **entire** Java set above 1k stars totals ~46k stars. `browser-use` alone
  has 111k.
- spring-ai carries **1,456 open issues against 9,381 stars — 155 per k-star**,
  three times the highest ratio anywhere in the Python set. Read with the
  caveats in §27.1, but the gap is large enough to be a signal about a project
  under heavy adoption pressure.

### Java has libraries; it does not yet have a harness

Measured from Maven Central source jars with the same probe set as the Python
and TypeScript sections, after excluding license headers:

| package | kLOC | sandbox | approval | permission | budget | retry | checkpt | otel | cost | mcp |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| google-adk-java 1.8.0 | 49.3 | 2.1 | **2.0** | 0.43 | 1.5 | 2.6 | 1.1 | **11.4** | 0.1 | 1.9 |
| langchain4j-core 1.19.0 | 32.2 | 0.0 | **0.0** | 0.0 | **0.0** | 3.8 | 0.1 | 0.0 | 0.2 | 0.0 |
| langchain4j 1.19.0 | 16.0 | 0.0 | **0.0** | 0.0 | **0.0** | 1.1 | 0.2 | 0.0 | 0.0 | 0.1 |
| spring-ai-model 2.0.1 | 20.7 | 0.0 | 0.0 | 0.03 | 0.0 | 0.1 | 0.0 | 0.7 | 0.0 | 0.0 |
| spring-ai-client-chat 2.0.1 | 7.1 | 0.3 | 0.0 | 0.03 | 0.0 | 0.8 | 0.0 | 0.8 | 0.1 | 0.0 |

**langchain4j has zero hits for approval, permission, budget, and sandbox across
48 kLOC.** Verified, not an artefact — the words do not appear. This is not a
criticism: langchain4j's own description is "an idiomatic Java library ... a
unified API over popular LLM providers and vector stores." It is a *framework*
by this study's §1 test, and a good one. But it supplies none of the five
properties a harness is defined by. The same holds for Spring AI, whose only
real permission-adjacent code is an allowlist of exceptions to unwrap.

**google/adk-java is the exception, and it is a port.** It carries the only
approval mechanism in Java (`ToolConfirmation`, 2.0/kLOC), a sandbox notion, a
budget notion, MCP support, and by far the strongest observability of any
package in *any* language in this study — **11.4 OpenTelemetry hits per kLOC**,
of which 63 are direct `opentelemetry` references and the rest genuine span
plumbing (`spanId`, `spanContext`, `spanRecord`). Google instruments its agent
runtime the way it instruments its services.

### The §14 conclusion holds in Java too

`com/google/adk/events/ToolConfirmation.java`:

```java
public abstract class ToolConfirmation extends JsonBaseModel {
  @Nullable @JsonProperty("hint")      public abstract String hint();
           @JsonProperty("confirmed")  public abstract boolean confirmed();
  @Nullable @JsonProperty("payload")   public abstract Object payload();

  public static Builder builder() {
    return new AutoValue_ToolConfirmation.Builder().hint("").confirmed(false);
  }
}
```

`confirmed(false)` as the builder default is correct — fail-closed, so a
half-constructed confirmation denies rather than approves. But the decision
itself is **a boolean, a hint string, and an untyped `Object payload`**. No
actor, no timestamp, no expiry — exactly what `09-memory-context-multiagent-hitl.md`
§14 found in Python and `06-typescript.md` found in TypeScript.

> **Across Python, TypeScript, and Java — 30 packages, three ecosystems, every
> major vendor — not one models an approval as an auditable decision.** It is
> permission state everywhere. This is now the most consistently reproduced
> finding in the study, and the strongest argument that a harness needing
> accountable approvals must build that itself.

### The answer on Java / Spring Boot

> **If the requirement is Java, use google/adk-java and accept that you are
> early.** It is the only Java package with any harness mechanism, and its
> observability is best-in-study. It is also 1,709 stars and fifteen months old.
>
> **langchain4j and Spring AI are the mature, well-adopted choices, and neither
> is a harness** — they are provider abstraction layers with RAG and tool
> calling. Building on them means building all five harness properties yourself,
> in an ecosystem with no prior art to copy.
>
> **Python remains where harness engineering is actually happening**, by an
> order of magnitude in both adoption and mechanism density. A Java deployment
> target does not require a Java harness: the A2A protocol (25.5k stars, Linux
> Foundation) and MCP both exist to let a harness in one language serve agents
> and tools in another. **Running the harness in Python and exposing it to a
> Spring Boot application over A2A or MCP is better supported today than
> building the harness in Java.**

### Other languages

- **TypeScript** — covered in `06-typescript.md`. Second-strongest ecosystem;
  the Vercel AI SDK has the best approval *shape* found anywhere, and
  `@langchain/langgraph` reproduces its Python checkpoint density (23.4 vs
  21.1/kLOC), which is what turned a single-codebase number into evidence of
  deliberate architecture.
- **C#/.NET** — **microsoft/semantic-kernel's primary language is C#**, not
  Python, and microsoft/agent-framework ships Python *and* .NET from one repo.
  .NET is the only ecosystem besides Python with first-party agent frameworks
  from a major vendor. **Not measured: Chưa đủ evidence** — no C# source was
  read for this study, and the Python bindings say nothing about the .NET
  implementation.
- **Go, Rust** — **Chưa đủ evidence.** No project in either language met the
  selection criteria in §2; absence from this study is not evidence of absence
  in the world.

---

## §45 Evidence limits

- **Star counts are a popularity proxy, and a bad one.** They are included
  because §46 requires them and because order-of-magnitude gaps (Python vs Java)
  are meaningful. Differences under ~2× are not.
- **Issues-per-k-star mixes triage policy, project age, and where a community
  is allowed to ask questions.** §27.1 states this; do not lift the number out
  of that context.
- **Java mechanism density comes from three projects' core modules only**
  (`langchain4j-core` + `langchain4j`, `spring-ai-model` + `spring-ai-client-chat`,
  `google-adk`). Spring AI in particular is a large multi-module project;
  advisors, vector stores, and MCP integrations live in modules not measured
  here. The claim "Spring AI is not a harness" rests on its core chat and model
  modules, which is where a harness's control loop would have to live — but it
  is not a claim about every Spring AI artefact.
- **spring-ai-alibaba (10.7k stars) was not measured.** Its description claims
  graph, workflow, and multi-agent support, which would make it the most
  harness-like Java project. **Chưa đủ evidence.**
- **License findings describe the artefact, not legal advice.** crewai's
  repository is MIT; only its wheel is silent.
- The license-header defect in §26.1 was found by verification, not by review.
  It had been published in a table that had been read several times without
  anyone noticing — consistent with this project's own measured finding that
  reading does not find what running finds.

---

## Sources

All repository metrics: GitHub search API, 2026-08-30.
Python package metadata: PyPI wheels of the versions named.
Java package sources: Maven Central `-sources.jar` for
`dev.langchain4j:langchain4j-core:1.19.0`, `dev.langchain4j:langchain4j:1.19.0`,
`org.springframework.ai:spring-ai-model:2.0.1`,
`org.springframework.ai:spring-ai-client-chat:2.0.1`,
`com.google.adk:google-adk:1.8.0`.

| project | repo |
|---|---|
| langchain4j | https://github.com/langchain4j/langchain4j |
| Spring AI | https://github.com/spring-projects/spring-ai |
| spring-ai-alibaba | https://github.com/alibaba/spring-ai-alibaba |
| google/adk-java | https://github.com/google/adk-java |
| embabel-agent | https://github.com/embabel/embabel-agent |
| agents-flex | https://github.com/agents-flex/agents-flex |
| A2A protocol | https://github.com/a2aproject/A2A |

Python and TypeScript repository URLs: `09-memory-context-multiagent-hitl.md`
and `06-typescript.md`.
