# Memory, Context Engineering, Multi-Agent, and Human-in-the-Loop

Sections §10, §11, §13, §14 of the research brief.

**Data collected on: 2026-08-30.** Every claim below comes from package source
read on that date. Versions are named at each claim. Method as in the rest of
this study: measure with `research/harvest.py`, then **verify every standout
number by reading the code**. Five of the numbers in the first table survived
that step; four did not, and the corrections are the most useful part of this
document.

---

## §10.1 The measurement, and what it got wrong

`probe2.py` (probe set recorded in full below) over 23 Python packages, hits per
kLOC:

| package | kLOC | memory | semantic | summarise | handoff | orchestr | hitl | resume | audit |
|---|---|---|---|---|---|---|---|---|---|
| langmem 0.0.30 | 6.9 | **49.7** | 2.7 | **24.2** | 0.0 | 1.4 | 0.0 | 6.2 | 0.1 |
| mem0ai 2.0.19 | 31.7 | **46.2** | **28.5** | 1.4 | 0.0 | 0.2 | 0.1 | 0.1 | 2.2 |
| agno 3.0.1 | 420.5 | 13.8 | 3.0 | 2.2 | 0.5 | ~~34.6~~ | 3.4 | 1.1 | 1.4 |
| autogen-agentchat 0.7.5 | 11.1 | 7.1 | 0.0 | 0.5 | **21.8** | ~~43.0~~ | 7.0 | 1.8 | 0.0 |
| crewai 1.15.18 | 118.1 | 10.5 | 7.5 | 0.6 | 0.6 | ~~48.2~~ | 0.2 | 1.1 | 0.1 |
| openai-agents 0.22.0 | 125.5 | 2.6 | 0.0 | 1.7 | **8.4** | 0.2 | **7.8** | 2.5 | 0.0 |
| agent-framework-core 1.16.0 | 61.6 | 5.7 | 2.2 | **6.9** | 0.6 | 0.4 | **7.4** | 3.4 | 0.5 |
| letta-client 1.12.1 | 47.8 | 4.1 | 7.3 | **12.0** | 0.0 | 0.1 | 3.3 | 0.0 | 0.0 |
| semantic-kernel 1.44.1 | 81.9 | 9.3 | 22.7 | 0.7 | 1.1 | 3.4 | 0.2 | 3.1 | ~~4.4~~ |
| langgraph 1.2.11 | 28.0 | 0.9 | 0.0 | 0.1 | 0.5 | 0.0 | 0.5 | **10.3** | 0.5 |
| langgraph-checkpoint-sqlite 3.1.1 | 3.9 | 6.6 | 17.3 | 0.0 | 0.0 | 0.0 | 0.0 | **51.5** | 0.0 |
| google-adk 2.8.0 | 171.4 | 3.8 | 1.0 | 2.3 | 0.8 | 0.5 | 0.8 | 1.2 | 0.0 |
| pydantic-ai-slim 2.36.0 | 119.5 | 0.5 | 4.6 | 3.6 | 0.4 | 0.0 | 2.6 | 1.6 | 0.1 |
| haystack-ai 3.1.0 | 55.0 | 0.7 | 7.6 | 4.6 | 0.2 | 1.1 | 5.5 | 0.2 | 0.0 |

Struck-through cells are the ones that did not survive verification:

- **crewai's 48.2 "orchestration" is mostly the package's own name.** Of the
  hits, 2401 are the literal string `crewai` and 458 are `crewai_event_bus`;
  the actual orchestration vocabulary is `crew` (1235) and `router` (126). Any
  package whose name collides with the probe will top the table it is measured
  in. This is the clearest example in the whole study of why the density table
  is a *pointer*, not a finding.
- **autogen-agentchat's 43.0 and agno's 34.6 are one construct each** —
  `team` (296 and 7367 hits respectively). Real, but it is one feature name
  repeated, not breadth.
- **semantic-kernel's 4.4 "audit" is the Dapr actor model** — 119 `actor`,
  34 `actors`, 32 `actor_id`, 30 `actorstatekeys`. Zero of it is audit.

With `actor` removed, audit across all 23 packages collapses to almost nothing:

```
152  agno            (116 "audit" + 31 "decision_id" + 5 misc)
 28  agent-framework
 11  langchain-core
  7  crewai
  6  pydantic-ai      5 openai-agents      3 langchain      1 langmem
```

**Fifteen of twenty-three packages contain the word zero times.**

---

## §14.1 The bound question: is approval a record, or a boolean?

This was the question the section was set to answer. The answer is **a boolean
almost everywhere, with one partial exception, and no project anywhere records
who approved.**

### agno's decision log is written by the agent about itself

agno is the densest audit signal in Python, so it is the one to check first.
All 31 `decision_id` hits live in a single file,
`agno/learn/stores/decision_log.py`, whose docstring says it is
"Useful for auditing, debugging, and learning from past decisions."

Reading it, the mode is declared in the header:

```
Supported Modes:
- AGENTIC: the agent logs decisions via tools.
```

and enforced in `__post_init__`:

```python
if self.config.mode != LearningMode.AGENTIC:
    log_warning("DecisionLogStore is AGENTIC-only: the agent logs decisions via tools. Proceeding as AGENTIC.")
```

The write path is `_build_log_decision_tool`, which hands the model a
`log_decision(...)` tool. The schema fields are `decision`, `reasoning`,
`decision_type`, `context`, `alternatives`, `confidence` — every one of them
model-authored prose. There is no field the runtime fills in and the model
cannot.

This is an audit log the auditee writes. It is genuinely useful for the thing
the module is actually named for — `agno.learn`, feeding decisions back as
training signal — but it cannot answer "did anyone authorise this?", because a
model that skips the tool call leaves no trace, and a model that calls it
controls every word of the entry.

### openai-agents: a *scoped* boolean, and the scope is the good part

`agents/run_context.py:57`:

```python
@dataclass(eq=False)
class _ApprovalRecord:
    """Tracks approval/rejection state for a tool.

    ``approved`` and ``rejected`` are either booleans (permanent allow/deny)
    or lists of call IDs when approval is scoped to specific tool calls.
    """
    approved: bool | list[str] = field(default_factory=list)
    rejected: bool | list[str] = field(default_factory=list)
    rejection_messages: dict[str, str] = field(default_factory=dict)
    sticky_rejection_message: str | None = None
    sticky_scope: str | None = None
```

The class is named `_ApprovalRecord` but it is not a record of an event; it is
the *current state* of a permission. `is_tool_approved` returns
`bool | None` — approved, denied, or not yet asked. That tri-state is right,
and scoping approval to a list of concrete `call_id`s is right: "yes to *this*
call" is a different grant from "yes to this tool forever", and the type
distinguishes them.

What is missing is uniform across the field: **no actor, no timestamp, no
expiry**. `approve_tool(item, always_approve=True)` writes `approved = True`
and that grant never lapses for the life of the context. Nothing records which
human clicked, or when.

### Microsoft's is the best approval design in Python

`agent_framework/_harness/_tool_approval.py:86` is the one design that goes
beyond "which tool":

```python
class ToolApprovalRule(SerializationMixin):
    """A standing rule for approving future matching tool calls."""
    tool_name: str
    arguments: dict[str, str] | None
    server_label: str | None
```

Three things this gets right that nothing else does:

1. **Approval is scoped to argument values, not just the tool.** The docstring
   is precise about the ternary: `arguments=None` matches every call;
   `arguments={}` matches *only* no-argument calls. Approving
   `delete_file(path="/tmp/x")` does not approve `delete_file(path="/etc/passwd")`.
   Every other project in this study approves the *verb* and ignores the *object*.
2. **`server_label` is a trust boundary.** "Hosted approvals only match future
   approvals from the same server label" — a grant to one MCP server does not
   transfer to another offering a same-named tool. That is a real confused-deputy
   defence, and it is the only one found.
3. **`ToolApprovalState` is serialisable** (`to_dict`/`from_dict`) and
   session-backed, so a grant survives a resume rather than being silently
   re-asked or silently re-granted.

Still absent: actor, timestamp, expiry. A grep of the whole 665-line file for
`actor|approved_by|user_id|timestamp|created_at|expire|expiry|ttl` returns one
incidental hit.

### LangGraph: the id addresses the pause, not the person

`langgraph/types.py:851`, `interrupt(value: Any)`. The `Interrupt` carries
`value: Any` and `id: str`, where the id is `xxh3_128_hexdigest` of the node
namespace. Resume is `Command(resume=...)`, again `Any`.

The id is worth crediting: it addresses *which* interrupt is being answered, so
a resume cannot be misapplied to a different pause — a real correctness
property, and it is why langgraph tops the `resume` column honestly (10.3, and
51.5 in the sqlite checkpointer, where the hits are `thread_id` 110 and
`checkpoint_id` 93, both genuine). But it is a *position* identifier. Nothing
in the type system distinguishes a resume value that came from a human who
clicked Approve from one produced by a script, another agent, or a replay.

### Conclusion for §14

> **No framework surveyed treats approval as an auditable event.** The best
> available (Microsoft) is a well-scoped *standing rule*: tool + argument values
> + server boundary, serialised across resume. Approval is universally modelled
> as permission state, never as a decision with an author, a time, and a
> lifetime. A harness that needs to answer "who approved this, when, and is
> that grant still valid?" must build it; nothing here provides it.

---

## §14.2 A Poka-Yoke inversion inside one package

Microsoft ships both the strongest approval design found and a clear example of
the failure it is meant to prevent — in the same `_harness/` module.

**Enforced properly (file access).** `_file_access.py:1444`:

```python
readonly_approval: ApprovalMode = "never_require" if self.disable_readonly_tool_approval else "always_require"
write_approval:    ApprovalMode = "never_require" if self.disable_write_tool_approval  else "always_require"
```

Approval is **on by default**, and read and write are separately switchable —
an effect-class distinction expressed in the API. Path handling is enforced in
code, not prose: `.` and `..` segments are rejected outright
(`_file_access.py:183`), and `is_link_or_reparse_point` guards the symlink and
Windows-junction escape. This is Poka-Yoke at the right level.

**Not enforced (agent mode).** `_mode.py` implements plan/act modes. The system
prompt says:

> "Only use mode_set if the user explicitly instructs/allows you to change modes."

And the tool, at `_mode.py:289`:

```python
@tool(name="mode_set", approval_mode="never_require")
def mode_set(mode: str) -> str:
    """Switch the agent's operating mode."""
```

The mode is a safety posture — "plan" is the read-only, ask-first mode — and
the model holds the switch, with `approval_mode="never_require"` making that
switch un-gateable by construction. The only thing standing between a model and
leaving plan mode is a sentence of English addressed to the model itself.

The same file that carefully argues `arguments=None` versus `arguments={}`
hands the model the mode lever. The lesson is not that Microsoft was careless;
it is that **enforcement is per-tool, so it has to be re-derived correctly for
every tool**, and the twentieth tool is where it slips. A harness that
classifies effects once and derives the gate from the classification cannot
have this inconsistency — the switch that changes the safety posture is
`danger` by construction, not by whoever wrote the decorator that day.

---

## §16bis A verified concurrency defect in a security module

`agent_framework/security.py` (3532 lines) implements the most sophisticated
safety architecture in this study — and contains a defect that silently
disables it under concurrency.

### What it implements

A **two-dimensional information-flow lattice**: integrity
(`TRUSTED`/`UNTRUSTED`, i.e. Biba) × confidentiality (`PUBLIC`/`PRIVATE`, i.e.
Bell-LaPadula), combined monotonically by `combine_labels`:

```python
def get_context_label(self) -> ContentLabel:
    """...It starts as TRUSTED + PUBLIC and gets "tainted" as untrusted or private
    content is added to the context."""
```

with the enforcement rule at `security.py:2000`:

```
# Integrity policy: an UNTRUSTED (tainted) context may not drive a tool that has not ...
```

and read-only tools explicitly exempted, because "they are safe to call even
when the agent context is tainted — it cannot exfiltrate" (`security.py:3033`).
It goes further than anything else surveyed: `set_quarantine_client` provides a
separate, cheaper model for reasoning over untrusted content (the dual-LLM /
CaMeL pattern), and `_map_mcp_annotations_to_labels` derives labels from MCP
tool annotations so a third-party server's `readOnlyHint` becomes a lattice
label.

This is the only *bidirectional* information-flow control found in 23 Python
and 5 TypeScript packages. Every other taint-like mechanism, including the one
in this repository's own harness, tracks integrity only.

### The defect

`security.py:685`:

```python
# Thread-local storage for current middleware instance
_current_middleware = threading.local()
```

set and cleared inside an **`async def`** (`security.py:1173`, `1207`, `1288`):

```python
async def process(self, context, call_next):
    _current_middleware.instance = self
    try:
        ...
        await call_next(context)     # <-- yields to the event loop
        ...
    finally:
        _current_middleware.instance = None
```

`threading.local()` is per-OS-thread. Under asyncio, many coroutines share one
thread, so two concurrent tool invocations share one slot. With tool calls A
and B interleaving across the `await`:

1. A sets `instance = A`, awaits, yields.
2. B sets `instance = B`.
3. A resumes; `get_current_middleware()` (read at `security.py:2613` and
   `2864`) returns **B's** middleware — A labels its result against the wrong
   context.
4. B's `finally` sets `instance = None`.
5. A's remaining reads get **None** — label tracking is off, and the caller is
   not told.

Both outcomes are silent, and both fail *open*: the taint tracker either
attributes content to the wrong conversation or stops tracking. The correct
primitive is `contextvars.ContextVar`, which is per-task rather than per-thread.

Fair mitigation: the class carries `@experimental(feature_id=ExperimentalFeature.FIDES)`,
so it is flagged as not production-ready.

*(This is the same defect class this repository hit at Round 34 and fixed at
Round 41 — shared mutable state leaking between concurrent runs. The fix there
also established that `ContextVar` alone is insufficient across LangGraph
nodes, which each run in a copied context; state has to live in the thread's
checkpointed state. `threading.local()` is strictly weaker than the primitive
that was already not enough.)*

### The larger finding: the good module is not wired in

`grep` for any import of the security middleware from `_harness/` returns
**nothing**. `security` does not appear in the top-level `agent_framework/__init__.py`
either. Microsoft's own harness agent — `create_harness_agent`, the thing a user
actually calls — does not install the lattice.

Two process-wide singletons compound this:

```python
_global_variable_store = ContentVariableStore()          # security.py:2393
_quarantine_chat_client: SupportsChatGetResponse | None = None   # security.py:2396
```

both mutated through module-level setters (`global _quarantine_chat_client`).
In a multi-tenant server, one tenant's `set_quarantine_client` changes every
tenant's.

> **The best safety architecture in the study is opt-in, experimental, unwired
> by its own harness, and not concurrency-safe.** The gap between what a
> framework *can* do and what it does *by default* is the single most important
> thing this study measured — and it is widest exactly where the engineering is
> most sophisticated.

---

## §11 Context engineering: who preserves the tool-call pairing?

Every compaction strategy faces one correctness obligation: an assistant
message carrying `tool_call`s and the corresponding tool-result message must
survive or be dropped **together**. Break the pair and the next request is
rejected by the provider. Two projects answer it differently, and the contrast
is the cleanest Poka-Yoke ladder in the study.

**LangChain documents the invariant.** `langchain_core/messages/utils.py:1133`,
`trim_messages`. Its docstring says:

> "...generally a `ToolMessage` can only appear after an `AIMessage` that
> involved a tool call. To achieve this, set `start_on='human'`."

The trimming helpers themselves — `_first_max_tokens` (line 1970) and
`_last_max_tokens` (line 2086) — contain no tool-pairing logic at all;
`_last_max_tokens` reverses the list, delegates, and reverses back. The only
lever is `start_on`, a caller-supplied message-type filter that defaults to
`None`. So the default path can cut between a tool call and its result, and the
mitigation requires the caller to have read the docstring — and even then
`start_on='human'` fixes it by discarding the whole exchange rather than
keeping the pair.

The sibling function `filter_messages` does it properly: `exclude_tool_calls`
removes the matching `ToolMessage`, rewrites `tool_calls` on the `AIMessage`,
and drops the message entirely if all its calls were filtered. The capability
exists in the package; the token-budget path does not use it.

**Microsoft computes it.** `agent_framework/_compaction.py:105`:

```python
def _unambiguous_function_call_result_pairs(messages: Sequence[Message]) -> list[tuple[int, int]]:
```

It walks the transcript building `call_id → [declaration indices]`, matching
each `function_result` against the pending declarations and popping the match.
The word *unambiguous* in the name is load-bearing: duplicate `call_id`s are
handled as a candidate list rather than assumed unique. Compaction then
operates on index pairs, so a pair cannot be half-removed.

Around it sit five strategies — `SelectiveToolCallCompactionStrategy`,
`ToolResultCompactionStrategy`, `SummarizationStrategy`,
`ContextWindowCompactionStrategy`, and a `CompactionStrategy` Protocol. Two of
the five target **tool results specifically**, which is correct prioritisation:
in a tool-using agent, tool output is where the tokens actually accumulate, and
it is also the most compressible (a 40 kB HTML fetch is worth two sentences on
the next turn). No other framework surveyed separates tool-result compaction
from message-history compaction.

**Nobody has a token-exact budget.** `count_tokens_approximately`
(`langchain_core/messages/utils.py:2244`) is named honestly. The `ctxwindow`
column is near-zero everywhere (max 1.5, llama-index). Frameworks compact
against an estimate and discover the real number when the provider rejects the
request. *(This is the same conclusion the cost section reached from the other
direction: nothing reserves budget before a call — see
`03-safety-reliability.md` §20 and ADR-017 in this repo, which is the
pre-flight `reserve()` design that follows from it.)*

---

## §10.2 Memory: three products, one missing property

The dedicated memory layers separate cleanly from frameworks that bolt memory
on. langmem (49.7 memory / 24.2 summarise per kLOC) and mem0 (46.2 / 28.5) are
memory *products* — the density is honest, not artefact. letta-client's 12.0
summarise is likewise real; its architecture is built on summarisation of a
bounded context.

The typology across all of them is the same three tiers:

| tier | mechanism | example |
|---|---|---|
| working | the message list itself | every framework |
| short-term | running summary of the current thread | `langmem.short_term.summarization.RunningSummary`, `SummarizationNode` |
| long-term | extracted facts in a vector store, retrieved by similarity | mem0 (28.5 semantic/kLOC), `langmem.knowledge.extraction` |

### The missing property: no memory write carries provenance

This is the question worth asking of a long-term memory system, because it is
where prompt injection becomes *persistent*: an agent reads a hostile web page,
extracts a "fact" from it, and writes it to long-term memory, where it is
retrieved and trusted on every future session — including sessions for other
users, if the store is shared.

Searching all 23 packages for `taint|provenance|untrusted`:

- **letta-client: 0. langmem: 5. mem0ai: 7.** `agent_framework/_harness/_memory.py`: **0**.
- **agno's 100 `provenance` hits are unrelated** — they are schedule
  provenance (`stamp_schedule_provenance`) and team-member routing, in
  `agno/scheduler/` and `agno/team/_run.py`. Not information flow.
- The only real information-flow implementation is
  `agent_framework/security.py` (140 `untrusted`, 7 `tainted`) — which, as §16bis
  established, is not wired into that framework's own memory or harness.

> **No memory system surveyed records where a memory came from.** A fact
> extracted from an attacker-controlled page is stored identically to one the
> user typed. Retrieval cannot distinguish them, so the injection survives the
> session that carried it. Combined with the §16bis finding that the one lattice
> that could label such content is not connected to the memory subsystem, this
> is the largest unaddressed security gap found in the study.

---

## §13 Multi-agent: necessary, or over-engineering?

The bound question. The answer is available by reading what a handoff *is*.

### A handoff is a tool call

`openai_agents/agents/handoffs/__init__.py:126`:

```python
@dataclass
class Handoff(Generic[TContext, TAgent]):
    tool_name: str
    tool_description: str
    input_json_schema: dict[str, Any]
    on_invoke_handoff: Callable[[RunContextWrapper[Any], str], Awaitable[TAgent]]
    agent_name: str
    input_filter: HandoffInputFilter | None = None
    ...
```

The model is shown a tool. Calling it runs `on_invoke_handoff`, which returns a
different `Agent` object, and the loop continues with that agent. There is no
new process, no new context, no new client. **A handoff is a tool call whose
return value is a system prompt and a toolset.**

And the default is stated plainly in the `input_filter` docstring:

> "By default, the new agent sees the entire conversation history."

So the out-of-the-box semantics of "delegating to a specialist agent" are: same
message list, different instructions, different tool surface. `input_filter` can
narrow it — and, exactly as with LangChain's `start_on`, it is caller-supplied
and `None` by default.

### What it does and does not buy you

Verified in `agents/run.py`: `current_turn = 0` appears once, at line 762, on
run start. At the handoff site (line 2050) only `current_agent` is reassigned:

```python
current_agent = cast(Agent[TContext], turn_result.next_step.new_agent)
```

`current_turn` keeps counting, checked against `max_turns` at line 1442. **A
handoff cannot reset the turn budget** — a genuine strength, and a non-obvious
one. The naive implementation (each agent gets its own `max_turns`) turns a
delegation chain into an unbounded loop; three agents handing off in a cycle
would run forever. OpenAI got this right.

So a handoff gives you:

- ✅ tool-surface reduction — the billing agent cannot call refund tools
- ✅ prompt specialisation without one 4000-token mega-prompt
- ✅ a shared, non-resettable turn budget
- ❌ **no context isolation** — full history by default
- ❌ **no fault isolation** — same loop, same process; the sub-agent's exception is the parent's
- ❌ **no privilege isolation** — `RunContextWrapper._approvals` is keyed by
  `_resolve_approval_key`, which composes tool name + tool namespace + lookup key
  (`run_context.py:168`). **No agent identity enters the key.** A tool approved
  while agent A was running is still approved when the handoff target B calls it
- ❌ **no taint isolation** — nothing to isolate; there is no taint model

### The answer

> **Multi-agent as implemented is a router, not an architecture.** It is
> justified when the goal is reducing the tool surface a model chooses from, or
> avoiding one enormous prompt — both real, both measurable. It is
> over-engineering when reached for as an *isolation* mechanism, because none of
> the three isolations that word implies (fault, privilege, information) is
> provided by any implementation surveyed.

The corollary for a harness: if you want a genuinely isolated sub-agent — one
that cannot spend the parent's remaining budget, cannot inherit the parent's
approvals, and cannot see the parent's secrets — the framework will not give it
to you. It has to be a first-class construct with its own ledger and its own
policy scope. *(This repository's ADR-030 `hold()`/`release()` budget split
exists for exactly this reason: a sub-agent draws against a reserved slice, not
the parent's remaining balance.)*

### One real counter-example on breadth

autogen-agentchat's handoff density (21.8/kLOC over 11.1 kLOC) is not artefact —
`Swarm`, `HandoffTermination`, and handoff-typed messages are distinct
constructs, and it is the only project where handoff is a first-class *protocol*
rather than a tool wrapper. But the isolation analysis above applies equally:
the agents share a runtime and a message bus.

---

## §45 Evidence limits

- Everything here is Python and read from PyPI source distributions of the
  versions named. TypeScript equivalents are in `06-typescript.md`; the Vercel
  AI SDK's `ToolApprovalStatus` four-state union with reasons flowing both
  directions remains the best *shape* for an approval value found in either
  language, and it too carries no actor or timestamp — the §14 conclusion holds
  cross-language.
- **letta-client is a generated API client, not the server.** Its 12.0
  summarise density describes the surface Letta exposes, not how Letta
  implements memory. Claims about Letta's internals: **Chưa đủ evidence.**
- **Java/Spring AI: Chưa đủ evidence.** Still unaddressed; deferred to
  `10-governance-health-languages.md`.
- Concurrency defects are established by reading code, not by executing a race.
  The `threading.local()` finding is a proof from the primitive's documented
  semantics (per-thread) against the usage (per-task); it is not an observed
  failure.

### Probe set used

```python
PROBES = {
  "memory":     r"\b(memory|memories|MemoryStore|remember|recall)\w*",
  "semantic":   r"\b(embedding|vector_store|vectorstore|similarity_search|semantic_search)\w*",
  "summarise":  r"\b(summariz|summaris|compact|compress|trim_messages|prune)\w*",
  "ctxwindow":  r"\b(context_window|max_context|token_limit|context_length|num_ctx)\w*",
  "handoff":    r"\b(handoff|handoffs|delegate|transfer_to)\w*",
  "subagent":   r"\b(subagent|sub_agent|child_agent|spawn|worker_agent)\w*",
  "orchestr":   r"\b(orchestrat|supervisor|router|team|crew|swarm)\w*",
  "hitl":       r"\b(human_in_the_loop|human_input|interrupt\(|approval|confirm|ask_user)\w*",
  "resume":     r"\b(resume|Command\(|checkpoint_id|thread_id)\w*",
  "audit":      r"\b(audit|decision_id|approved_by|actor|justification)\w*",
  "expiry":     r"\b(expires|expiry|ttl|deadline|timeout_at)\w*",
}
```

Same four limits as `research/harvest.py`: counts measure presence not
correctness, include comments and docstrings, are normalised per kLOC, and
**every standout must be verified by reading code**. The `orchestr` probe is
additionally unsafe against packages whose name matches it — see §10.1.

---

## Sources

| project | version | repo |
|---|---|---|
| agent-framework-core | 1.16.0 | https://github.com/microsoft/agent-framework |
| openai-agents | 0.22.0 | https://github.com/openai/openai-agents-python |
| langchain-core | 1.6.1 | https://github.com/langchain-ai/langchain |
| langgraph | 1.2.11 | https://github.com/langchain-ai/langgraph |
| langmem | 0.0.30 | https://github.com/langchain-ai/langmem |
| mem0ai | 2.0.19 | https://github.com/mem0ai/mem0 |
| agno | 3.0.1 | https://github.com/agno-agi/agno |
| autogen-agentchat | 0.7.5 | https://github.com/microsoft/autogen |
| crewai | 1.15.18 | https://github.com/crewAIInc/crewAI |
| letta-client | 1.12.1 | https://github.com/letta-ai/letta |
| semantic-kernel | 1.44.1 | https://github.com/microsoft/semantic-kernel |

Licenses and governance: `10-governance-health-languages.md` (pending).
