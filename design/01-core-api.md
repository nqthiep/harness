# 01 — Core API

**This file follows [`00-foundation.md`](00-foundation.md).** The vocabulary (`Effect`,
`Verdict`, `Decision`, `Actor`, `Ledger`, `Label`, `Run`, `ToolSpec`), the two lattices,
and the four architecture rules R-1…R-4 are taken as-is from there, not redefined here.

Scope: the **public surface** — what 90% of users touch. The policy engine is in
[`02`](02-safety-engine.md), tools/MCP in [`03`](03-tools-and-mcp.md), the graph runtime
in [`04`](04-runtime-durability.md), `Ledger` in [`05`](05-cost-and-memory.md).

---

## 1. The minimal public API

The **minimal** surface (enough for Tiers 0-2, see §2) is **14 names**, one `import`.
This is NOT the whole surface — Tier 3 (§2, production) needs more — an earlier draft of
this section claimed "the whole surface" and then its own Tier-3 example, in the SAME
file, imported 20 names from 4 modules, contradicting itself
([review-kiss.md](review-kiss.md) K-22). The more honest number: **14 names minimum, up
to 20 when using the full production surface** (checkpointing, plugins, MCP policy),
through **1 root import + at most 3 submodules** when they're actually needed.

```python
from harness import (
    Agent, tool, Effect, ToolSpec,          # defining things
    Budget, Workspace,                       # spend ceiling, isolation
    Approver, Answer, Verdict, Decision,     # approval
    Plugin, Result, StopReason, Label,       # extension, results
)
```

### 1.1 `Agent`

```python
from typing import Generic, Literal, Sequence, TypeVar

OutT  = TypeVar("OutT")

EndStrategy = Literal["early", "graceful", "complete"]

class Agent(Generic[OutT]):
    def __init__(
        self,
        *,                                                  # keyword-only, no exceptions
        name: str,
        job: str,
        model: ModelProvider | ModelName,
        budget: Budget | str,
        tools: Sequence[ToolSpec] = (),
        sandbox: Sandbox | None = None,
        output_type: type[OutT] = str,
        policies: Sequence[Policy] = (),
        approve: Approver | None = None,
        plugins: Sequence[Plugin] = (),
        checkpointer: Checkpointer | None = None,
        end_strategy: EndStrategy = "graceful",
    ) -> None: ...
```

Thirteen parameters, **and each one exists because of a measured finding**:

| parameter | why it's there | source |
|---|---|---|
| `model` is required, no default | Google ADK has `DEFAULT_MODEL: ClassVar[str] = 'gemini-3.5-flash'` — a vendor affinity hidden in a class variable. MS Agent Framework gets it right: `client` is required, dependency inversion enforced | ([§02](../research/02-api-comparison.md) §6) |
| `budget` is **required**, must have a money axis | The whole industry has loop limits and almost no spend ceiling: pydantic-ai's 3.6 budget/kLOC is the highest, LangGraph's is 0.0; `max_turns` caps round count, and a 200k-token round costs a hundred times more | ([§03](../research/03-safety-reliability.md) §20) |
| `sandbox` has no `"local"` default | smolagents puts `executor_type` in the constructor — the best isolation design in the research — then defaults it to `"local"`, undercutting the value of the mechanism itself | ([§05](../research/05-ideal-harness.md) §31 lesson 2 and 10) |
| `output_type` | PydanticAI's end-to-end typing is the only Agent API to score "highest type safety" | ([§02](../research/02-api-comparison.md) §6) |
| `end_strategy` | A model returning **both** a final answer **and** a tool call at once is a tricky semantic. PydanticAI named it, gave it three options, and changed the default from `early` to `graceful` because the old default was wrong. The third value was renamed from PydanticAI's original `"exhaustive"` to `"complete"` — `"exhaustive"` is already the name of one of pydantic-ai's own three, completely different **parallel modes** ([03 §3.2](03-tools-and-mcp.md)), and reusing it here for an unrelated axis would create a vocabulary collision within this very design ([review-kiss.md](review-kiss.md) K-11) | ([§02](../research/02-api-comparison.md) §6) |
| `approve` takes an `Approver`, returns an `Answer` — never a `bool` | Approval everywhere is a permission state, not an auditable event; Java's `ToolConfirmation` is exactly one `boolean` | ([§09](../research/09-memory-context-multiagent-hitl.md) §14.1, [§10](../research/10-governance-health-languages.md) §28) |
| `checkpointer` | Durability is architectural or it doesn't exist: LangGraph at 51.6 recover/kLOC vs. everything else under 2. And it's only installable because of the topology | ([§11](../research/11-workflow-and-dx.md) §12) |
| `plugins` | an around-hook — see §4 | ([§08](../research/08-tool-mcp-plugin.md) §24) |
| `policies` | the verdict lattice composed with `max()`, tighten-only | [`00`](00-foundation.md) §3.1 |
| `*` keyword-only | Google ADK's `extra='forbid'` turns a typo into a construction-time error, cheap and effective. Keyword-only is the pure-Python version of the same idea | ([§02](../research/02-api-comparison.md) §6, [§05](../research/05-ideal-harness.md) §31 lesson 6) |

**When `sandbox` is required.** Not always — required *when it's relevant*. If `tools`
contains any tool with `Effect.WRITE`, `Effect.EXTERNAL`, or `Effect.DANGER` and
`sandbox=None`, that is a **construction-time error**. An agent with no tools, or only
`read` tools, never touches the world so it needs no choice. This keeps hello-world at 5
lines without loosening Poka-Yoke: whatever is dangerous when missing gets no default
([§05](../research/05-ideal-harness.md) §35–36).

### 1.2 Four ways to run

```python
class Agent(Generic[OutT]):
    async def run(self, prompt: str, *, run_id: RunId | None = None) -> Result[OutT]: ...
    async def try_run(self, prompt: str, *, run_id: RunId | None = None) -> Result[OutT]: ...
    def stream(self, prompt: str, *, run_id: RunId | None = None) -> AsyncIterator[Event]: ...
    async def resume(self, run_id: RunId, *, answer: Answer | None = None) -> Result[OutT]: ...
    async def cancel(self, run_id: RunId) -> None: ...

    def run_sync(self, prompt: str, *, run_id: RunId | None = None) -> Result[OutT]: ...
```

`run()` **raises** when a run doesn't complete; `try_run()` **always** returns a
`Result`. Both return the same type, so switching between them doesn't require rewriting
result-handling code.

The sample interface `interface Agent { AgentResult run(AgentRequest) }` is missing
**five** things the evidence says are required: streaming, cancellation, sessions,
approval round-trip, and idempotency ([§05](../research/05-ideal-harness.md) §35–36).
**The first three above** cover streaming, cancellation, and approval round-trip.
`session` is NOT here on purpose: there is no `Session` type anywhere in this file or in
`02`/`04`/`05`, only `run_id`; tenant/owner/TTL — the isolation boundary a `session`
would have to provide — sits under *Not Enough Evidence* in three files (`02`, `04`,
`05`). The more honest statement: a session is the scope of the **service layer**
wrapping the harness, not of the harness itself ([review-kiss.md](review-kiss.md) K-28,
see `07-risks`). Idempotency isn't on this surface because the gateway **generates** the
key rather than accepting one — a caller can't forget what they're never allowed to
supply.

`cancel()` is a **signal**, not a stop reason the model chooses for itself — smolagents
scores 9 on Security but 4 on Runtime because `cancel` sits at 0.0/kLOC
([§05](../research/05-ideal-harness.md) §30).

### 1.3 `@tool`

```python
@overload
def tool(fn: Callable[..., Any], /) -> NoReturn: ...          # missing effect -> error immediately
@overload
def tool(*, effect: Effect, name: str | None = None,
         ) -> Callable[[Callable[..., OutT]], ToolSpec]: ...
```

A tool author declares **exactly one thing**: `effect`. The five behaviors — parallelism,
retry, taint, default verdict, audit level — are *derived* ([`00`](00-foundation.md) §2).
No `sequential=` flag, no `handle_tool_error=`, no per-tool `retries=` — and **no
`accepts_tainted=` or `max_confidentiality=`**, see [03 §1.1](03-tools-and-mcp.md).

`@tool` has no bare-call form (`@tool` with no arguments): the first overload returns
`NoReturn` so the **type checker reports an error before it even runs**, and the runtime
raises `MissingEffectError` at import time. This is the first row in the Poka-Yoke table:
a tool with no `effect` is blocked at *import time*
([§05](../research/05-ideal-harness.md) §35–36).

`ToolSpec` keeps a `__call__` that delegates to the original function, so a tool remains
directly unit-testable without building an `Agent`.

### 1.4 `Approver` and `Answer` — fixing finding number one

> **Three names, three roles, don't conflate them.** A `Ruling` is what a **policy**
> decides ([02 §1](02-safety-engine.md)); an `Answer` is what a **human** replies with; a
> `Decision` is the immutable record the **runtime** seals from an `Answer` + `Actor` +
> `Scope` ([00 §4](00-foundation.md)). Only `Decision` ever enters the audit log.

```python
@value
class Answer:
    verdict: Literal[Verdict.ALLOW, Verdict.DENY]   # ASK is never a final outcome
    reason: str                                      # required, even for ALLOW
    expires_at: datetime | None = None

@value
class ApprovalRequest:
    scope: Scope                 # tool + parameter values + server — 00 §4.1
    reason: str                  # which policy asked
    label: Label                 # the context's current label
    estimated_cost: Money

class Approver:
    def __init__(self, fn: Callable[[ApprovalRequest], Awaitable[Answer]],
                 *, actor: Actor) -> None: ...
```

**`Approver` returns an `Answer`, never a `Decision`.** This is how invariant D-1 is
enforced *by the type system*, not by review: the approver only fills in `verdict`,
`reason`, `expires_at`; the runtime seals it into a `Decision` with `id`, `decided_at`,
`run_id`, and `actor` — where `actor` is attached **when the `Approver` is constructed**,
not on every call. There is no path for a `Decision` to carry an actor the approved
party declared itself. This is exactly where agno gets it wrong: the thickest audit
signal across 23 Python packages is `decision_log` — a tool the model itself calls to
log about itself ([§09](../research/09-memory-context-multiagent-hitl.md) §14.1).

`reason` is required even for `ALLOW`: a grant with no reason can't be audited six months
later.

---

## 2. Zero-to-Agent — progressive disclosure

The research answers "how long to a production agent" very plainly: **under an hour to a
demo, in any framework — that problem is solved and isn't a differentiator; the
production part is where frameworks don't get you, and that gap isn't documentation**
([§11](../research/11-workflow-and-dx.md) §22). The four tiers below are designed so
**each new tier reveals exactly one concept**, and tier 4 is where the research says the
whole industry leaves a gap.

### Tier 0 — it runs: 5 lines

```python
from harness import Agent

agent = Agent(name="Helper", job="Answer briefly.",
              model="claude-opus-5", budget="$0.05")
print(agent.run_sync("What is the capital of Vietnam?").text)
```

Four parameters. `budget` is the fourth parameter because it's required — and that's
**deliberate**: the one thing that forces a newcomer to type an extra line is the one
thing the whole industry is missing ([§03](../research/03-safety-reliability.md) §20).
`"$0.05"` parses into a `Budget`; a syntax error is an `InvalidBudgetError` at
construction.

### Tier 1 — add a tool: +5 lines

```python
from harness import Agent, Effect, tool

@tool(effect=Effect.READ)
def word_count(text: str) -> int:
    """Count the words in a piece of text."""
    return len(text.split())

agent = Agent(name="Helper", job="Count words when asked.",
              model="claude-opus-5", budget="$0.05",
              tools=[word_count])
print(agent.run_sync("How many words in 'hello there everyone'?").text)
```

New concept: **exactly one** — `effect`. No `sandbox` because the tool set is all
`read`. The schema is generated from type hints; the docstring becomes the tool
description.

### Tier 2 — add a side effect: sandbox and approval appear *because the tool set changed*

```python
from harness import (Agent, Approver, Channel, Effect, Human, Answer,
                     Verdict, Workspace, tool)

@tool(effect=Effect.WRITE)
def save_note(path: str, body: str) -> str:
    """Write a note into the workspace and return the path written."""
    ...

async def ask_terminal(req):
    print(f"  {req.scope.tool}({dict(req.scope.args or {})})")
    print(f"  because: {req.reason} . estimated {req.estimated_cost}")
    ok = input("  approve? [y/N] ") == "y"
    return Answer(verdict=Verdict.ALLOW if ok else Verdict.DENY,
                  reason="user answered at the terminal")

agent = Agent(
    name="Notetaker", job="Take notes as the user requests.",
    model="claude-opus-5",
    budget="$0.20, 15 steps, 60s",
    tools=[save_note],
    sandbox=Workspace("./ws", egress="deny"),
    approve=Approver(ask_terminal, actor=Human(id="nqthiep", via=Channel.CLI)),
)
```

Remove `sandbox=` from this snippet and it **doesn't run** — `UnsafeToolSetError` at
construction, naming the tool and its effect. Same for removing `approve=`, since
`write`'s default verdict is `ASK` ([`00`](00-foundation.md) §2) and an `ASK` with no
one to answer it is a foreseeable deadlock. Both errors happen **before a single cent is
spent**.

This is where it differs from the `.withTimeout().withBudget().run()` builder pattern: a
builder lets you call `.run()` having set nothing at all
([§05](../research/05-ideal-harness.md) §35–36).

### Tier 3 — production: the four remaining concepts

```python
import asyncio
from harness import Agent, Approver, Human, Channel, Workspace, StopReason
from harness.checkpoint import SqliteCheckpointer
from harness.plugins import Retry, CostReport
from harness.policy import DenyHosts

agent = Agent(
    name="Notetaker", job="Take notes as the user requests.",
    model="claude-opus-5",
    budget="$0.20, 15 steps, 60s",
    tools=[save_note, fetch_page],
    sandbox=Workspace("./ws", egress="allowlist:docs.python.org"),
    approve=Approver(ask_terminal, actor=Human(id="nqthiep", via=Channel.CLI)),
    policies=[DenyHosts("*.internal")],
    plugins=[Backoff(on=("rate_limited", "unavailable"), attempts=3), CostReport()],
    checkpointer=SqliteCheckpointer("./runs.db"),
    # `end_strategy` deliberately does NOT appear here (review-kiss.md K-11): the
    # default is already "graceful", and it isn't one of the four industry-wide gaps
    # this section illustrates (retry, cost report, durable checkpoint, deny-hosts) —
    # only declare it when you actually need to change it from the default.
)

async def main() -> None:
    async for ev in agent.stream("Summarize page X then save it to note.md", run_id="r-42"):
        print(ev.type, ev.sequence)

    r = await agent.try_run("Continue", run_id="r-42")
    if r.stop_reason is StopReason.AWAITING_DECISION:
        r = await agent.resume("r-42", answer=await ask_terminal_for(r.pending))
    print(r.output, r.cost, r.label, len(r.decisions))

asyncio.run(main())
```

Four new concepts, and they are **four industry-wide gaps**, not four features added for
show: durability (`checkpointer` — available because the runtime is a graph,
[§11](../research/11-workflow-and-dx.md) §12), a tighten-only policy, plugins for
retry/cost, and an approval round-trip through `AWAITING_DECISION` -> `resume(answer=…)`.

**Total: 5 -> 10 -> 22 -> 30 lines.** No tier requires rewriting the previous one; each
tier only adds parameters to the same constructor.

---

## 3. Extreme DX — where this stands, and why

The research measures DX rather than commenting on it
([§11](../research/11-workflow-and-dx.md) §22). Three numbers, three positions:

### 3.1 Type hints — `py.typed` is no longer a differentiator

**11/12 packages ship `py.typed`** ([§11](../research/11-workflow-and-dx.md) §22); two
years ago that table would have mostly said "no." Shipping `py.typed` today is a
**minimum bar**, not a strength — so saying "we have type hints" says nothing.

A more specific, measurable position:

- ship `py.typed`;
- **no `Any` in a public signature** — the 14 names in §1 must type-check under
  `mypy --strict` from the *caller's* side, not just from inside the source;
- `Agent` is generic over `OutT` so `result.output` has a real type, learned from
  PydanticAI ([§02](../research/02-api-comparison.md) §6);
- **0 `.pyi` files.** Microsoft is the only project shipping stubs (23 files), and that's
  the right call *when the runtime type is dynamic*
  ([§11](../research/11-workflow-and-dx.md) §22). This harness's types are static, so a
  stub is extra maintenance buying nothing — KISS, cut.
- **≤ 3 required dependencies in core.** The dependency table spans from 1
  (autogen-agentchat) to 31 (crewai) ([§11](../research/11-workflow-and-dx.md) §22);
  providers and stores travel as extras.

### 3.2 Error messages — 100% with context, by construction, not by discipline

The "errors with context" rate spans **27%-61%**: smolagents 61% (highest), haystack
48%, openai-agents 30%, llama-index 27% ([§11](../research/11-workflow-and-dx.md) §22).
And the research warns about itself: this metric **measures form, not quality** — an
f-string can still be useless ([§11](../research/11-workflow-and-dx.md) §45).

So this harness targets both, and makes it *impossible to violate*:

```python
class HarnessError(Exception):
    def __init__(self, *, what: str, got: object = _UNSET,
                 fix: str, doc: str) -> None: ...
```

`fix` and `doc` are **required keywords**. There is no way to raise a harness error
without saying what to do about it. The error-context rate is 100% *by construction*,
and a CI test reuses the exact probe from `research/harvest.py` to prove it never
regresses.

Three content requirements, each fixing one kind of useless message:

1. **Interpolate the offending value** — `got=` must render into the message, not
   "invalid budget" but the actual value the caller typed.
2. **Say what to do** — `fix` is an imperative sentence, not a description of the
   condition.
3. **One documentation anchor** — `doc` points to the exact section, never the home
   page.

```
InvalidBudgetError: a budget needs a money axis.

  You wrote:  budget="15 steps, 60s"
  Write:      budget="$0.20, 15 steps, 60s"

  A step limit is not a spending limit: a 200k-token round costs a
  hundred times more than a short one, so counting rounds doesn't cap money.

  -> design/01-core-api.md §1.1
```

### 3.3 Docstrings — target the surface, not the density

Docstring density spans **21.3-38.0/kLOC**, and the 2-3x spread **doesn't correlate with
a project's size, backing, or popularity** ([§11](../research/11-workflow-and-dx.md)
§22). Density is also a proxy metric: module and class docstrings are counted the same
as function docstrings ([§11](../research/11-workflow-and-dx.md) §45).

So the target isn't a number/kLOC but: **100% of public symbols have a docstring**, and
density lands wherever it lands. Targeting the proxy metric would just be optimizing the
ruler.

### 3.4 Errors happen as early as possible

| mistake | blocked at | mechanism |
|---|---|---|
| a tool declares no `effect` | **import time** | `@tool` has no bare-call form; the overload returns `NoReturn` |
| `sandbox=` missing while the tool set has write/external/danger | **construction** | the tool set is checked in `__init__` |
| `budget` missing a money axis | **construction** | `Budget`'s parser |
| a mistyped parameter name | **construction** | keyword-only + Python's own `TypeError` |
| `approve` returns a `bool` | **type error** | `Approver` requires `Awaitable[Answer]` |
| a tool call missing an idempotency key | **cannot happen** | the gateway generates the key, the API doesn't accept one |

This table is a condensed version of the Poka-Yoke matrix
([§05](../research/05-ideal-harness.md) §35–36); the full one is in
[`06-poka-yoke-matrix.md`](06-poka-yoke-matrix.md).

---

## 4. The plugin API — two around-hooks, and a boundary that must not be crossed

### 4.1 Whose design this borrows

LangChain 1.x's `AgentMiddleware` is the **strongest extension design found in the whole
research effort** ([§08](../research/08-tool-mcp-plugin.md) §24). Not because of density
(24.3 middleware/kLOC) but because of *shape*: among six extension points, the
`wrap_model_call` / `wrap_tool_call` pair is an **around-hook that receives a
`handler`**, so it decides whether to call it, call it differently, or call it twice. The
consequence: retry, cache, an approval gate, model fallback, and cost accounting can all
be expressed **without the framework shipping a dedicated feature for any of them**.
That's Open/Closed, actually achieved.

```python
ModelHandler = Callable[[ModelRequest], Awaitable[ModelResponse]]
ToolHandler  = Callable[[ToolRequest],  Awaitable[ToolOutcome]]

class Plugin:
    name: str

    async def wrap_model_call(self, req: ModelRequest,
                              handler: ModelHandler) -> ModelResponse:
        return await handler(req)

    async def wrap_tool_call(self, req: ToolRequest,
                             handler: ToolHandler) -> ToolOutcome:
        return await handler(req)
```

**Two hooks, not six.** LangChain also has `before_agent` / `before_model` /
`after_model` / `after_agent`, but those four are just the first and last line of an
around-hook. The `wrap_*` pair is the irreplaceable part
([§08](../research/08-tool-mcp-plugin.md) §24); the other four are a shortcut. KISS: cut
four, keep two.

`plugins=[a, b, c]` nests in declaration order: `a` outermost, the model/tool call
innermost.

**A plugin must be stateless.** Per-run state travels in `req.scratch:
MutableMapping[str, Any]`, living in checkpointed state. This is R-4 enforced by the API
shape itself: if a plugin kept state on `self`, two concurrent runs would read each
other's — exactly Microsoft's mistake with `threading.local()`
([`00`](00-foundation.md) §3.2, [§09](../research/09-memory-context-multiagent-hitl.md)
§16bis).

### 4.2 Which shortcoming this fixes — R-1 is the boundary

An extension point is also **attack surface and a bypass path**. Concrete evidence:
Microsoft's information-flow lattice — the most sophisticated safety implementation in
the research — is middleware, and being middleware means *not installed* is the default:
their own `_harness/` doesn't import it
([§08](../research/08-tool-mcp-plugin.md) §24,
[§09](../research/09-memory-context-multiagent-hitl.md) §16bis). An approval gate
written as a plugin only protects the agents someone remembered to wrap.

The solution isn't picking one side, but **placing two kinds of checks at two different
positions relative to the plugin chain**:

| check | position relative to the plugin chain | can a plugin skip it? |
|---|---|---|
| policy verdict (the `max()` lattice) | **before** the chain | no — the chain isn't entered unless this passes |
| taint check (`Label`) | **before** the chain | no |
| requiring a `Decision` when the verdict is `ASK` | **before** the chain | no |
| budget `reserve` / `settle` | **inside** the handler | no — every handler call is charged |
| the idempotency key | **inside** the handler | no — the gateway generates it |
| the audit event | **inside** the handler | no |
| retry, cache, fallback, cost report, log | **is** the chain | that's exactly what a plugin is for |

Two rules, stated concisely:

- **Gate-before** — what skipping would make a *security bug* (permission, taint, a
  `Decision`) sits **before** the chain. A plugin never receives a `handler` until the
  gate has let the call through.
- **Gate-inside** — what a plugin might *repeat* (a model call, a tool call) has its gate
  **inside** the handler. A `Retry` that calls the handler three times reserves three
  times, logs three audit events. Retry cannot dodge the budget.

**Property PLUG-1 (a plugin can only weaken)** — renamed from the original `P-3`
(K-13, `07-risks-and-open-issues.md` §2, K-13): the number `P-3` collided with
`design/02 §1.2`'s own P-3 ("fail closed when a policy raises," now `POL-3`) and
`docs/09-testing.md`'s own P-3 (a property-test ID, "every `tool_use` gets exactly one
`tool_result`") — three different meanings, one number. `PLUG-1`: for any list of
plugins `P`, the set of side effects a run can produce with `P` installed is a **subset**
of the set with nothing installed. A plugin can narrow, never widen. Provable with a
property-based test, the same way P-2 is proven ([`00`](00-foundation.md) §3.1) — for the
reason stated in R-2: 23 review rounds found 0 security bugs, 16 *runtime* rounds found 4
([`00`](00-foundation.md) §5).

Three things a plugin **has no API to touch**, and this is a closed list: the `Ledger`
(read-only, through `req.remaining`), a `Decision` (no public constructor — only the
runtime seals one from an `Answer`), and `Verdict` (not present on `ToolRequest`). Same
reasoning as R-3: the model holds no safety switch, and a plugin the model can indirectly
steer doesn't get one either.

---

## 5. What errors return

### 5.1 `Result` — one type for every outcome

```python
@value
class Result(Generic[OutT]):
    output: OutT                       # typed by output_type
    text: str
    stop_reason: StopReason
    run_id: RunId
    steps: int
    cost: Money                        # Decimal-backed, never a float
    usage: Usage
    label: Label                       # the two-axis label at the end
    decisions: tuple[Decision, ...]    # the audit trail, append-only (D-2)
    pending: Scope | None              # set when AWAITING_DECISION
    tools_run: tuple[CallId, ...]      # tools that RAN, not tools the model asked for
    detail: str

    @property
    def ok(self) -> bool: ...
    def raise_for_status(self) -> None: ...
```

`decisions` sitting right on `Result` is what makes invariants D-1/D-2 *usable*: a caller
can answer "who approved this, when, is the grant still valid" without reading a
separate log. No framework surveyed can answer that question
([§09](../research/09-memory-context-multiagent-hitl.md) §14.1 conclusion).

`tools_run` records tools that **actually executed**, not tools the model **asked for**.
In a library whose safety story is "dangerous tools get blocked," a helper that can't
tell a blocked tool from one that ran is useless for the very check it exists to make.

### 5.2 Terminal states

```python
class StopReason(str, Enum):
    COMPLETED         = "completed"
    AWAITING_DECISION = "awaiting_decision"
    BUDGET_EXHAUSTED  = "budget_exhausted"
    STEP_LIMIT        = "step_limit"
    TIMEOUT           = "timeout"
    DENIED            = "denied"
    CANCELLED         = "cancelled"
    TRUNCATED         = "truncated"        # context can't compact any further — 05 §B.3
    GRAPH_CHANGED     = "graph_changed"    # the graph changed between two resumes — 04 §4.5
    ERROR             = "error"
```

| stop reason | `ok` | resumable? | money charged | `output` |
|---|---|---|---|---|
| `COMPLETED` | yes | — | yes | full |
| `AWAITING_DECISION` | no | yes, `resume(answer=…)` | up to the stop point | partial |
| `BUDGET_EXHAUSTED` | no | yes, after raising the ceiling | yes, to the ceiling | partial |
| `STEP_LIMIT` | no | yes | yes | partial |
| `TIMEOUT` | no | yes | yes | partial |
| `DENIED` | no | no — needs a new `Decision` | yes | partial |
| `CANCELLED` | no | yes | up to the signal | partial |
| `ERROR` | no | yes, if a checkpointer is set | yes | partial |

**`AWAITING_DECISION` is not a failure — it's a pause point.** This is the approval
round-trip the sample interface omits ([§05](../research/05-ideal-harness.md) §35–36).
The "resumable" column only means something with a `checkpointer` set: durability is
architectural, not a bolt-on option ([§11](../research/11-workflow-and-dx.md) §12).

### 5.3 Exceptions — three groups, three moments

```python
class HarnessError(Exception): ...                 # the root, requires what/got/fix/doc

class ConfigError(HarnessError): ...               # ALWAYS at import/construction time
class MissingEffectError(ConfigError): ...
class UnsafeToolSetError(ConfigError): ...
class InvalidBudgetError(ConfigError): ...
class DuplicateToolError(ConfigError): ...
class UnknownModelError(ConfigError): ...

class RunFailed(HarnessError):                     # from run(), never from try_run()
    result: Result[Any]
class RunPaused(RunFailed):                        # AWAITING_DECISION
    pending: Scope
```

Three moments, and the boundary is a testable invariant: **no `ConfigError` is ever
raised from inside a run.** Whatever is wrong about configuration must blow up before any
money is spent.

`ProviderError` and its subclasses (`ProviderRateLimited`, `ProviderUnavailable`,
`ProviderTimeout`, `ProviderAuthError`) **never leak onto the public surface**: they are
the input to the `Retry` plugin (`req` carries a normalized error code) and become
`StopReason.ERROR` at the boundary. A caller never has to learn each provider's own error
taxonomy.

### 5.4 Tool errors — three outcomes, derived from `Effect`

pydantic-ai has the **only three-branch taxonomy found in the research**
([§08](../research/08-tool-mcp-plugin.md) §8.2):

| raise | model sees it | a corrective prompt | spends retry budget | run continues |
|---|---|---|---|---|
| `Retry` | yes | yes | yes | yes |
| `ToolFailed` | yes | no | no | yes |
| any other exception | no | — | — | no |

Distinguishing "spends retry budget or not" is the subtle part, and it's correct: a
malformed parameter *must* spend quota — the model is guessing and needs to be reined
in; a definitive 404 *must not* — retrying doesn't help, the model needs to **adapt**.

This harness keeps those three outcomes but **doesn't make the tool author choose**.
LangChain defaults to `handle_tool_error=False`, meaning a tool that raises **ends the
whole run** — so one flaky HTTP call kills a 50-step run that was otherwise going fine,
and getting different behavior means every tool author has to opt in
([§08](../research/08-tool-mcp-plugin.md) §8.2). The research's lesson here is clear:
**no single default is right for every tool, so the choice belongs to the tool's
*classification*, not to a per-tool flag.**

The default rule, derived from `Effect` ([`00`](00-foundation.md) §2):

| effect | an unrecognized exception is interpreted as | why |
|---|---|---|
| `read` | `Retry` | retryable, and its failure is information |
| `external` | `Retry` | retryable per the effect table |
| `write` | `ToolFailed` | not retryable without an idempotency key |
| `danger` | `ToolFailed` | not retryable, and its audit level is `audit` |

A tool author can still explicitly raise `ToolInputInvalid`/`ToolUnavailable` when they
know better. But **there is no path for a tool to kill the whole run**: our default is
the opposite of LangChain's. In exchange, to avoid the "silent degradation" LangChain's
default guards against, every tool error emits an event at `info` level or higher and is
recorded in `Result.detail` — loud, but never fatal.

---

## 6. Whose design this borrows, and what it fixes for whom

| borrowed from | what | fixed here |
|---|---|---|
| PydanticAI ([§02](../research/02-api-comparison.md) §6) | `output_type`, `end_strategy`, the 3-branch error taxonomy | the 3 error branches are **derived from `Effect`** instead of making every tool choose |
| MS Agent Framework ([§02](../research/02-api-comparison.md) §6) | `client` required — enforced DI | no default model, and no vendor-biased class variable |
| smolagents ([§05](../research/05-ideal-harness.md) §31) | isolation in the constructor | **dropped the `"local"` default** — not choosing means not running |
| Google ADK ([§02](../research/02-api-comparison.md) §6) | `extra='forbid'` | keyword-only + rejecting positional args |
| LangChain 1.x ([§08](../research/08-tool-mcp-plugin.md) §24) | the around-hook `wrap_model_call`/`wrap_tool_call` | 6 hooks -> 2; and **R-1**: invariants pulled out of the plugin chain |
| LangGraph ([§11](../research/11-workflow-and-dx.md) §12) | checkpointing as architecture | `resume(run_id, answer=…)` is an approval round-trip, not just a technical resume |
| Microsoft's `ToolApprovalRule` ([§09](../research/09-memory-context-multiagent-hitl.md) §14) | `Scope` keyed on parameter values + `server_label` | `Answer` -> `Decision`, sealed by the runtime, actor attached at the `Approver` |
| — (nobody has it) | a real spend ceiling ([§03](../research/03-safety-reliability.md) §20) | `budget` is required, must have a money axis |

---

## Not Enough Evidence

1. **`external` retryable — an unresolved internal contradiction.** [`00`](00-foundation.md)
   §2 marks `external` as retryable, while [§08](../research/08-tool-mcp-plugin.md) §8.2
   warns "a failed `external` tool may already have had an effect and must not be retried
   blindly." This file follows the foundation since the foundation is the rule, but the
   contradiction is real and probably needs to be resolved with an idempotency key rather
   than a retry flag. Belongs to [`03`](03-tools-and-mcp.md).
2. **`run` async + `run_sync` is a convention, not a finding.** The research doesn't
   measure this choice's effect on DX; it only records that the two highest-scoring Agent
   APIs both do it.
3. **`deps_type` was CUT.** An early draft had `Agent` generic over `DepsT`. A reviewer
   pointed out two independent reasons, either one sufficient: that generic **reaches no
   user anywhere** (`03`'s `ToolCtx` in §6.2 has no `deps` field), and its only citation
   was "PydanticAI has it" — per [00 §8.4](00-foundation.md) that's an opinion, not a
   finding ([review-kiss.md](review-kiss.md) K-2). Whoever needs DI can capture it in a
   tool's closure; `functools.partial` already exists. If a **measured** need shows up
   later, add it back with a `deps: Any` field on `ToolCtx` — one line, no generic needed
   on `Agent`.
4. **Plugin nesting order.** The research reads `AgentMiddleware` at the signature level;
   the semantics of ordering when several middlewares are installed together **were not
   measured**. "Declared first means outermost" is this file's choice, not a learned fact.
5. **Vercel AI SDK middleware was not measured**
   ([§08](../research/08-tool-mcp-plugin.md) §45) — a better hook shape than the
   `wrap_*` pair might exist that the research never reached.
6. **`≤ 3 required dependencies` is a target, not yet a measurement.** It can only be
   checked once there's an implementation; until then it's a self-imposed constraint.
7. **`Answer` -> `Decision` has no precedent.** No framework separates what the approver
   fills in from what the runtime seals
   ([§09](../research/09-memory-context-multiagent-hitl.md) §14.1), so there's no
   evidence it holds up under a real approval UI (multiple approvers, delegation,
   revocation).
8. **The cost of `output_type`.** How many tokens the validate-retry loop costs when a
   model returns the wrong schema is not measured by any project in the research — so the
   interaction between `output_type` and the spend ceiling is unknown.
