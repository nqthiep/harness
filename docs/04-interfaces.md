# 04 — Interfaces & Contracts

Every signature here is normative. An implementer copies these into code; disagreements
between code and this file are bugs in the code.

Conventions: all data types are `@dataclass(frozen=True, slots=True)`. All protocols are
`typing.Protocol` with `@runtime_checkable` only where isinstance checks are actually
needed. No public signature contains `Any` (NFR-04).


---

## 0. Core value types

Defined first because everything below references them. Round 20 found that seven types
appearing in normative signatures had **no definition anywhere in this package** — including
`Money`, which is publicly exported. An implementer would have had to invent them, which is
precisely the bar this document exists to clear.

```python
# harness/result.py

class Money:
    """A USD amount. Decimal-backed; never a float (IDL-01)."""
    def __init__(self, value: Decimal | int | str) -> None: ...
    def __add__(self, other: "Money") -> "Money": ...
    def __sub__(self, other: "Money") -> "Money": ...
    def __lt__(self, other: "Money") -> bool: ...          # full ordering
    def __str__(self) -> str: ...                          # "$0.0143" — 4 dp, always signed with $
    def __repr__(self) -> str: ...                         # "Money('0.0143')"
    @property
    def decimal(self) -> Decimal: ...
    ZERO: ClassVar["Money"]

@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def total(self) -> int: ...
    @property
    def cache_hit_ratio(self) -> float:
        """cache_read / (input + cache_read). 0.0 when nothing was sent."""
    def __add__(self, other: "Usage") -> "Usage": ...      # accumulated across steps

@dataclass(frozen=True, slots=True)
class Step:
    index: int
    usage: Usage
    cost: Money
    tool_calls: tuple[str, ...]
    duration_ms: float
```

```python
# harness/models/base.py

ContentBlock = Mapping[str, object]
"""One provider content block: text, tool_use, tool_result, thinking, compaction.
Deliberately a Mapping rather than a class hierarchy — the harness routes blocks by
their "type" key and never interprets their bodies, so modelling them would be a
per-provider maintenance cost with no reader. Only models/anthropic.py inspects them."""

SystemBlock = Mapping[str, object]
"""A system content block: {"type": "text", "text": ..., "cache_control"?: ...}."""

DeltaFn = Callable[[str], None]
"""Streaming callback. Receives text fragments only — never thinking, never tool input.
Must not raise; the provider wraps it exactly as the EventBus wraps exporters (§04.6)."""
```

```python
# harness/budget/ledger.py

@dataclass(frozen=True, slots=True)
class Reservation:
    id: str
    estimate: Money
    input_tokens: int
    max_tokens: int      # derived — ADR-017
    created_at: float
```

```python
# harness/observe/events.py

class EventKind(str, Enum):
    RUN_STARTED = "run.started";        RUN_FINISHED    = "run.finished"
    STEP_STARTED = "step.started";      STEP_FINISHED   = "step.finished"
    MODEL_REQUEST = "model.request";    MODEL_RESPONSE  = "model.response"
    BUDGET_RESERVED = "budget.reserved"; BUDGET_EXHAUSTED = "budget.exhausted"
    TOOL_REQUESTED = "tool.requested";  POLICY_DECIDED  = "policy.decided"
    TOOL_STARTED = "tool.started";      TOOL_FINISHED   = "tool.finished"
    TAINT_RAISED = "taint.raised";      CONTEXT_MANAGED = "context.managed"
    ERROR_RAISED = "error.raised"
```

Fifteen values, matching [§05.1](05-data-and-state.md#1-the-event-taxonomy-closed) exactly.
A test asserts the enum and that table have the same membership — two lists of the same
thing drift, so one of them is checked against the other.

### Hashability — the rule, and why it needs one

`ToolSpec`, `ModelRequest`, `Message` and `Event` are all `frozen=True` and all carry a
`Mapping` field. **A frozen dataclass with a dict field is not hashable**, and the design
originally relied on hashing two of them:

```
frozenset({tool_spec})   ->  TypeError: unhashable type: 'dict'
hash(model_request)      ->  TypeError: unhashable type: 'dict'
```

| Type | `__hash__` | Where identity is needed instead |
|---|---|---|
| `ToolSpec` | `None` | `ToolSet` keys by `name` (a `str`) |
| `ModelRequest` | `None` | Memoization keys on `blake2b(canonical_json(request))` |
| `Message`, `Event` | `None` | Never used as keys |
| `Money`, `Usage`, `Budget`, `Price`, `Reservation`, `Step` | generated | Scalar fields only — genuinely hashable |

**Every type in this package that defines `__eq__` either has a consistent `__hash__` or
sets `__hash__ = None`.** Checked package-wide by AC-22, not per type — the Round 19 defect
was the same contract violation on `Secret`, and the Round 20 one landed on `ToolSpec`. It
will land somewhere else next.

---

## 1. Tools

```python
# harness/tools/__init__.py

class Effect(str, Enum):
    READ     = "read"
    WRITE    = "write"
    EXTERNAL = "external"
    DANGER   = "danger"

@dataclass(frozen=True, slots=True)
class EffectProfile:
    parallel_safe: bool
    retryable: bool
    taints_output: bool
    decision_standard: Verdict   # verdict when safety="standard"
    decision_strict: Verdict     # verdict when safety="strict"
    audit_level: Literal["debug", "info", "warning"]

EFFECT_PROFILES: Final[Mapping[Effect, EffectProfile]] = {
    Effect.READ:     EffectProfile(True,  True,  False, ALLOW, ALLOW, "debug"),
    Effect.WRITE:    EffectProfile(False, False, False, ALLOW, ASK,   "info"),
    Effect.EXTERNAL: EffectProfile(True,  True,  True,  ALLOW, ASK,   "info"),
    Effect.DANGER:   EffectProfile(False, False, False, ASK,   ASK,   "warning"),
}
```

`EFFECT_PROFILES` is the single source of truth for tool handling. It is a module-level
constant, not configuration: a user who could edit it could disable the taint rule.

```python
@dataclass(frozen=True, slots=True)
class ToolSpec:
    __hash__ = None                    # holds a Mapping — see §0 Hashability
    name: str                          # ^[a-z][a-z0-9_]{0,63}$ — validated at decoration
    description: str                   # from the docstring summary; required, non-empty
    input_schema: Mapping[str, object] # JSON Schema; additionalProperties:false, complete
                                       # `required`, and emitted with strict:true (ADR-022)
    effect: Effect
    fn: Callable[..., Awaitable[object]]  # always async; sync fns are wrapped at decoration
    timeout_s: float = 30.0
    max_result_tokens: int = 4_000
    source: str = ""                   # "module.py:41" — used in every error message
    subagent: object = None            # the child Agent, when this tool wraps one (as_tool())
    server: str | None = None          # T-9.1 — a ServerLabel when this came from
                                       # harness.mcp.connect(); None for a local tool

def tool(
    *,
    effect: Effect | Literal["read", "write", "external", "danger"],
    name: str | None = None,
    description: str | None = None,
    timeout_s: float = 30.0,
    max_result_tokens: int = 4_000,
) -> Callable[[Callable[..., object]], ToolSpec]: ...
```

### Tool contract

| Aspect | Contract |
|---|---|
| **Preconditions** | Every parameter annotated with a supported type; docstring present with a summary line. |
| **Return value** | Any JSON-serializable value, or `str`. Serialized with `json.dumps(sort_keys=True, default=None)`. |
| **Return violation** | `ToolContractError` raised at return, naming the offending field path. Not a silent `str()`. |
| **Exceptions** | Any exception is caught by `invoke.py`, converted to `tool_result(is_error=True)` with `type(e).__name__: str(e)`. The traceback is emitted as an event, **never** to the model. |
| **Timeout** | Effective timeout is **`min(spec.timeout_s, remaining_wall_clock)`** — an unclamped tool timeout lets a run overshoot its wall-clock budget by up to `timeout_s`, and a ceiling that overshoots is not a ceiling (Round 23). On expiry → `is_error`, and the message names which term bound: `"timed out after 30s"` versus `"timed out: run wall-clock budget reached"`, because those call for different fixes. |
| **Truncation** | Results exceeding `max_result_tokens` are cut at a token boundary and suffixed with `\n[truncated: N of M tokens shown]` so the model knows. |
| **Cancellation** | `asyncio.CancelledError` propagates; it is not converted to a tool error. |
| **Concurrency** | The harness may call a `read`/`external` tool concurrently with others. Tool authors must not assume serialization. Stated in the `@tool` docstring. |

### Supported parameter types (T-1.3)

`str`, `int`, `float`, `bool`, `list[T]`, `dict[str, T]`, **bare `list` and `dict`**,
`Literal[...]`, `Enum`, `Optional[T]`, `T | None`, and any `@dataclass` / `TypedDict` /
`pydantic.BaseModel` composed of the above. Defaults become non-required properties.

Bare `list` and `dict` are supported deliberately: they are what a beginner writes, and
unlike an *unannotated* parameter they carry real type information — an array is an array.
`list` renders as `{"type": "array"}` with no `items` constraint. This is the one place the
schema is deliberately loose, and it is loose in a direction that cannot produce a silently
wrong answer (contrast IDL-22).

**Anything else raises `ToolSchemaError` at import**, naming the parameter and its type.
There is no "best effort" fallback: a silently-wrong schema produces malformed tool calls
that fail at runtime and cost money to discover.

### MCP — tools from a third-party server (T-9.1)

`harness[mcp]` — an extra, `import harness` never imports the `mcp` SDK. Full contract
and rationale: `design/03-tools-and-mcp.md §5`, ADR-054.

```python
# harness/mcp/__init__.py

ServerLabel = NewType("ServerLabel", str)

@dataclass(frozen=True, slots=True)
class McpServerPolicy:
    identity: ServerLabel
    trusted: bool = False                                   # hint may set the DEFAULT
    default_effect: Effect = Effect.DANGER                   # used when not trusted
    effects: Mapping[str, Effect] = field(default_factory=dict)   # always wins
    accepts_tainted: frozenset[str] = frozenset()             # never from a hint
    allow: frozenset[str] | None = None                       # None = every tool listed

def classify_mcp_tool(tool: "mcp.types.Tool", policy: McpServerPolicy,
                      call: Callable[[str, Mapping], Awaitable[str]]) -> ToolSpec: ...

async def connect(session: "mcp.ClientSession", policy: McpServerPolicy) -> list[ToolSpec]: ...
```

- **Precedence:** `policy.effects[name]` > (`policy.trusted` ? hint : `policy.default_effect`).
- **Naming:** `f"{server}__{tool}"`, slugged — two servers never collide on the same
  protocol tool name in one `ToolSet`.
- **`accepts_tainted`** is read by the CALLER, merged into `Agent(accepts_tainted=...)` —
  this module never sets it on an `Agent` itself, same separation `Grants` already
  enforces for local tools (S-16).
- **`connect()` calls `tools/list` exactly once.** No live re-listing — a rug-pull
  (§5.4) is handled by never giving it a chance to reclassify a tool mid-run, not by
  detecting and rejecting it after the fact.
- **`Scope.server`** (already a `Decision`/`policy/decision.py` field, unused before
  T-9.1) now participates in grant matching: an approval for a tool on one server never
  authorizes the same-named tool on another.

---

## 2. Model provider

```python
# harness/models/base.py

@dataclass(frozen=True, slots=True)
class ModelRequest:
    __hash__ = None                    # holds Mappings — memoize on canonical bytes, §0
    model: str
    system: tuple[SystemBlock, ...]     # pre-rendered, immutable
    tools: tuple[Mapping[str, object], ...]  # name-sorted, canonically serialized
    messages: tuple[Message, ...]
    max_tokens: int                     # derived by the ledger (ADR-017); never user-supplied
    effort: str
    thinking: Mapping[str, object] | None
    stream: bool
    cache_breakpoints: tuple[int, ...]  # indices carrying cache_control
    output_format: Mapping[str, object] | None   # from Agent(returns=...) — ADR-022

@dataclass(frozen=True, slots=True)
class ModelResponse:
    content: tuple[ContentBlock, ...]
    stop_reason: Literal["end_turn","tool_use","max_tokens","pause_turn","refusal"]
    stop_details: Mapping[str, object] | None
    usage: Usage
    model: str
    raw_id: str

@dataclass(frozen=True, slots=True)
class Price:
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cache_write_per_mtok: Decimal
    cache_read_per_mtok: Decimal

class ModelProvider(Protocol):
    name: str

    async def complete(
        self, request: ModelRequest, *, on_delta: DeltaFn | None = None
    ) -> ModelResponse: ...

    def price(self, model: str) -> Price: ...

    async def count_input_tokens(self, request: ModelRequest) -> int: ...

    def max_context(self, model: str) -> int: ...
```

**Provider contract**

- `complete` must raise `ProviderError` subclasses, never vendor-SDK exceptions. The
  mapping table for the Anthropic adapter is in [§10.3](10-observability-ops.md).
- `complete` must **not** retry permanent errors (4xx other than 408/409/429).
- `count_input_tokens` must not mutate the request and must be safe to call before
  `complete` (it is on the budget hot path). **Memoize on `blake2b(canonical_json(request))`,
  not on the object** — `ModelRequest` holds `Mapping` fields and is unhashable. This reuses
  the assembler's canonical serializer, which has to exist anyway for caching, so there is
  one definition of "the same request" in the system rather than two.
- `price` must raise `UnknownModelError` for an unlisted model rather than return zero.
  A zero price silently disables the budget ceiling — fail-safe, not fail-open.
- Every tool definition is sent with **`strict: true`** (ADR-022), which is why the schema
  generator's `additionalProperties: false` and complete `required` list are mandatory
  rather than stylistic.
- `pause_turn` must be surfaced, not swallowed. The `RunEngine` re-sends to resume, capped
  at `max_pause_resumes = 5`. **A resume does not consume a step** — it continues one model
  turn rather than starting a new one — but it **does** consume budget and wall clock.
  Charging a step would let a server-tool-heavy turn exhaust `budget.steps` without the
  agent making progress; charging nothing would leave the loop bounded only by the cap.
- **Every provider `stop_reason` maps to exactly one `StopReason`, and the mapping is
  exhaustiveness-tested against the protocol's value set** (ADR-019):

  | provider | `StopReason` | `ok` |
  |---|---|:--:|
  | `end_turn` | `COMPLETED` | ✅ |
  | `tool_use` | *(loop continues — not terminal)* | — |
  | `max_tokens` | `TRUNCATED` | ❌ |
  | `refusal` | `MODEL_REFUSAL` | ❌ |
  | `pause_turn` | *(resumed — not terminal)* | — |
  | anything else | `ERROR`, carrying the raw string | ❌ |

  The last row is the load-bearing one. A value the provider adds tomorrow must fail
  visibly, never map to success.

---

## 3. Policy & verdicts

```python
# harness/policy/base.py

class Verdict(IntEnum):        # IntEnum so max() composes them
    ALLOW = 0
    ASK   = 1
    DENY  = 2

@dataclass(frozen=True, slots=True)
class Ruling:                  # renamed from `Decision` — see policy/decision.py::Decision below,
    verdict: Verdict           # the audit-record class this name would otherwise collide with (K-13)
    reason: str                # shown to the user on ASK, to the model on DENY
    policy: str                # which policy produced it

@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: Mapping[str, object]
    spec: ToolSpec

@dataclass(frozen=True, slots=True)
class RunContext:                       # dispatch.py — the classic backend's PolicyContext
    run_id: str
    agent_name: str
    step: int
    label: Label                        # two-axis integrity × confidentiality (§06.2)
    safety: Literal["standard", "strict"]
    deadline: float                     # monotonic clock
    tenant_id: str | None = None        # threaded from Agent.tenant_id — a Policy can
                                        # read this to decide differently per tenant
    @property
    def tainted(self) -> bool: ...      # True iff label.integrity is UNTRUSTED — kept
                                        # for callbacks written against the one-axis model
    # Deliberately absent: the message history. Tools must not read the transcript —
    # it is the largest available exfiltration surface. See §06.4.
    # The LangGraph backend's equivalent (lg/runtime.py::_Ctx) carries the same three
    # fields Policy.check actually reads — label, safety, tenant_id — under a lighter
    # __slots__ type; the two are kept in sync by tests/test_parity.py.

class Policy(Protocol):
    name: str
    def check(self, call: ToolCall, ctx: RunContext) -> Ruling: ...
```

### Composition — the only rule

```python
final = max(p.check(call, ctx) for p in policies)   # by Verdict value
```

- **Most restrictive always wins.** Adding a policy can only ever restrict. This is a
  property test, not a convention (`test_adding_policy_never_loosens`).
- Evaluation short-circuits on the first `DENY` (policies may be expensive).
- Built-in policies are always registered **first** and cannot be removed. User policies
  are appended. There is no "replace the policy chain" API.
- `check` must be **pure and fast** (< 1 ms). It must not perform I/O or call a model. A
  policy needing I/O should be a tool wrapper instead. Enforced by a timing assertion in
  debug mode. Human approval is not an exception to this rule — it is not a policy at all
  (see below).

### Built-in policies (always on, in this order)

| Policy | Rule |
|---|---|
| `EffectPolicy` | Verdict from `EFFECT_PROFILES[spec.effect]` and `ctx.safety`. |
| `TaintPolicy` | `check_flow(ctx.label, spec, grants)` — two branches, one per `Label` axis. `label.integrity is UNTRUSTED and effect is DANGER and name not in grants.accepts_tainted` → **DENY**; `label.confidentiality is SECRET and max_confidentiality is PUBLIC` → **DENY**. `accepts_tainted` is never a tool-decorator field — only `Agent(accepts_tainted=[...])` / `build_agent(accepts_tainted=[...])` set it (S-16). |
| `EgressPolicy` | `effect is EXTERNAL` and a host argument is outside `allowed_hosts` → **DENY**. Default `allowed_hosts=()` — an empty allowlist denies every external host (T-7.2). Inactive (unrestricted) only when `allowed_hosts=None` is passed explicitly. |
*(There is no `ApprovalPolicy`. See below — approval is not a policy.)*

### Approval is a resolution step, not a policy (ADR-021)

`Policy.check` is synchronous, pure and sub-millisecond. A human approval is I/O, unbounded
in time, and may be a coroutine. Those are incompatible, so approval sits **after**
composition rather than inside it:

```
policies (sync, pure, fast)  →  composed verdict  →  if ASK: engine awaits approval
```

```python
ApprovalFn = Callable[[ToolCall, RunContext], bool | Approval | Awaitable[bool | Approval]]
```

The engine — not a policy — resolves a surviving `ASK`:

| `approve` callback | Verdict |
|---|---|
| provided, returns `bool` | `ALLOW` / `DENY` from the value |
| provided, returns `Approval(ok, actor=...)` | `ALLOW` / `DENY` from `ok`, `Decision.actor` set to the reported `actor` |
| absent, `safety="strict"` | **DENY** |
| absent, effect is `danger` | **DENY** |
| absent, otherwise | `ALLOW`, plus a one-time warning event |

**`Actor` is a self-declaration, not verified identity (review-security.md S-11).** A
callback returning a plain `bool` (the common case) gives the engine no way to know who
actually approved — `Decision.actor` records a generic placeholder
(`Actor.human("approver", via="callback")`), the same for every call that callback ever
resolves. Return `Approval(ok, actor=Actor.human(id=..., via=...))` instead when the
callback genuinely knows who clicked — an authenticated Slack interaction, an OAuth
session — and the audit log records that identity instead. This closes "the harness has
no channel for real identity"; it does not close "a callback can still lie about who
approved" — that needs an authenticated-evidence model this design hasn't built (07-risks).

Relaxing `Policy.check` to async was the alternative and was rejected: purity is what makes
policies cheap enough to evaluate on every call, and hidden I/O from a third-party policy on
the hot path is exactly what the rule exists to prevent.

**`call.arguments` reaching `approve` is unescaped, model-controlled text.** The harness
does not sanitize it before your callback sees it — a crafted argument value can carry
ANSI escape codes to redraw a terminal, or an embedded "=== APPROVED, press y ===" aimed
at a human skimming a rendered string (review-security.md S-25). Use
`harness.safe_for_display(value)` when printing or formatting `call.arguments` for a human
approver: it escapes control characters into their visible `\xNN` form and replaces
anything past 200 characters with a length + digest, so an approver always sees what the
harness actually received, not something the model chose to draw on top of it.

```python
def ask_terminal(call: ToolCall, ctx: RunContext) -> bool:
    args = {k: safe_for_display(v) for k, v in call.arguments.items()}
    return input(f"Allow {call.name}({args})? [y/N] ") == "y"
```

**`max_asks_per_run` (default 20) is approval fatigue's ceiling, not a suggestion.**
Nothing else bounds how many times one run can hit `ASK` — injected content can make a
model call a `write` tool dozens of times with slightly different arguments, and the
(cap+1)-th request is the one an approver waves through on reflex. Past the cap, every
further `ASK` this run resolves to `DENY` without calling `approve` again; it does not
raise, so the run keeps going and reports what got blocked, the same way any other policy
`DENY` does.

---

## 4. Budget & ledger

```python
# harness/budget/ledger.py

@dataclass(frozen=True, slots=True)
class Budget:
    usd: Decimal | None = Decimal("0.50")
    steps: int = 20
    wall_clock_s: float = 300.0
    tokens: int | None = None

    @classmethod
    def parse(cls, value: "Budget | str | None") -> "Budget": ...
    # "$0.10" · "10 cents" · "5 steps" · "$1, 50 steps, 10m" · None → DEFAULT_BUDGET

DEFAULT_BUDGET: Final = Budget()   # finite on every axis — never unlimited
```

`Budget(usd=None)` is permitted but requires passing `None` explicitly, and emits a
`budget.unlimited` warning event on every run. Unlimited is possible; it is not silent.

```python
class Ledger:
    def reserve(self, estimate: Money) -> Reservation:
        """Raise BudgetExceeded if spent + estimate > budget.usd. Called BEFORE the call."""
    def settle(self, reservation: Reservation, actual: Usage) -> Money:
        """Replace the reservation with the true cost from the response."""
    @property
    def spent(self) -> Money: ...
    def remaining_steps(self) -> int: ...
    def check_deadline(self) -> None: ...
```

**`max_tokens` is derived from the budget, not passed in** (ADR-017). The ledger sizes the
call to what is affordable, so the reservation can never exceed the budget by construction:

```python
def size_call(self, input_tokens: int, price: Price, model_max: int) -> int:
    input_cost = Decimal(input_tokens) / 1_000_000 * price.input_per_mtok
    affordable = (self.remaining_usd - input_cost) / price.output_per_mtok * 1_000_000
    if affordable < 256:
        raise BudgetExceeded(...)          # a truncated answer is not an answer
    return min(int(affordable), model_max)
```

| budget | derived `max_tokens` (Opus-tier: $5 / $25 per MTok) |
|---|---|
| `$0.05` | ~1 760 |
| `$0.10` | ~3 760 |
| `$0.50` (default) | ~19 760 |

**Worst-case estimate** (the value passed to `reserve`), using that derived figure:

```
estimate = count_input_tokens(request) / 1e6 * price.input_per_mtok
         + max_tokens                  / 1e6 * price.output_per_mtok
```

Because `max_tokens` was chosen *from* the remaining budget, `estimate <= remaining` always
holds. Before ADR-017 the two were independent and routinely contradicted: a `$0.05` budget
against a default `max_tokens=16000` reserves `$0.406` and refuses to make any call at all.

Cache reads are *not* subtracted from the estimate — over-estimating is safe, under-
estimating breaks the ceiling. `settle` corrects to the true cost from `response.usage`,
including the cache-read discount.

All money arithmetic uses `Decimal`. Floats are banned in the ledger by a lint rule; a
float rounding error in a budget check is a real, if small, class of bug.

---

## 5. Store

```python
# harness/memory/base.py

class Store(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def put(self, key: str, value: str, *, ttl_s: float | None = None) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def search(self, query: str, *, limit: int = 5) -> Sequence[Memo]: ...
    async def close(self) -> None: ...

@dataclass(frozen=True, slots=True)
class Memo:
    key: str
    value: str
    score: float
    updated_at: float
```

`search` in the SQLite implementation is FTS5 keyword search — **not** vector similarity.
This is stated plainly so nobody assumes semantic recall. Vector search is a non-goal
([§01.5](01-requirements.md#5-non-goals)); a user who wants it implements `Store`.

Values are strings, not arbitrary objects. Pickling user objects into a store is a
deserialization vulnerability and a versioning trap.

### 5.1 Retrieval stores taint the run

Three `Store` implementations ship: `InMemoryStore`, `SqliteStore`, and — under
`harness[viking]` — `VikingStore` over [OpenViking](https://github.com/volcengine/OpenViking),
which is the semantic recall this section called a non-goal. **A user who wanted it did
implement `Store`; the seam held.**

A store that retrieves is not the same kind of thing as a store that returns what you put
in it, and the difference is a safety rule rather than a performance note:

> **Anything a retrieval store hands back is untrusted input.** A context database ingests
> web pages. What comes out may have been written by an attacker, months ago, on somebody
> else's machine.

So `VikingStore.tools()` ships `recall` classified **`external`**, not `read`. It taints
the run, and the construction-time refusal of external + irreversible applies to it. The
classification lives with the store rather than with the author because the author cannot
reasonably know what the database ingested (ADR-035).

**Any future retrieval binding carries the same rule.** A vector store, a RAG index, a web
archive: retrieval is `external`. A binding that classifies it `read` is the defect, not a
configuration choice.

---

## 6. Events & exporters

```python
# harness/observe/events.py

@dataclass(frozen=True, slots=True)
class Event:
    seq: int              # monotonic within a run, from 0
    ts: float             # time.time()
    run_id: str
    kind: EventKind       # closed enum — see §05.1
    step: int | None
    data: Mapping[str, object]   # JSON-serializable; schema per kind in §05.1

class Exporter(Protocol):
    def emit(self, event: Event) -> None: ...
    def close(self) -> None: ...
```

**Exporter contract:** `emit` must not raise and must not block. The `EventBus` wraps every
exporter in a try/except that converts an exception into a single `error.raised` event and
**disables that exporter for the rest of the run**. A broken telemetry exporter must never
take down an agent — that is a self-inflicted outage.

### 6.1 Service API (T-9.2)

`harness[server]` — `starlette` only; bring your own ASGI server. Full contract and
rationale: ADR-055.

```python
# harness/server/__init__.py

def create_app(agent: Agent, *, store: Store | None = None) -> "starlette.applications.Starlette": ...
```

| Route | Contract |
|---|---|
| `POST /v1/runs` | Body `{"message": str}`. `Idempotency-Key` header (optional) routes through `execute_once` — a retried request with the same key returns the same `run_id`, `"replayed": true`, and does not start a second run. `202` on a new run, `200` on a replay. |
| `GET /v1/runs/{id}` | `{"id", "status", "result", "error", "pending_approvals"}`. `status` is one of `running`, `waiting_approval`, `done`, `error`, `cancelled`. |
| `GET /v1/runs/{id}/events` | `text/event-stream` — buffered history first, then live-tails. Every line is a JSON `Event` (envelope v1 fields included), already `redact()`-ed. |
| `POST /v1/runs/{id}/cancel` | `200` while cancellable, `409` once the run has already finished, `404` for an unknown id. |
| `POST /v1/runs/{id}/approvals/{call_id}` | Body `{"approve": bool, "approved_by": str?}`. Resolves a pending `ASK` the same way any `approve=` callback would — `approved_by` is exactly as self-declared as any other `approve=` report (S-11's open gap, not a new one). |

**Not in v1:** authentication (put this behind your own reverse proxy — the module is
not the security boundary, same posture `EgressPolicy` takes toward real network
isolation), a persistent run registry (in-memory, one process), and consequently no
`resume` route.

### 6.2 One event model, three transports (T-9.3)

```python
# harness/observe/events.py
def to_dict(event: Event) -> dict[str, object]: ...   # the canonical JSON shape
```

`docs/17-research-alignment.md`'s M9: *"a canonical event model, many transports —
in-process, SSE, CLI/JSON. No transport has its own semantics."* All three build on
`to_dict()` rather than each defining the shape independently (ADR-056 — this is the
fix for a real staleness bug `TranscriptWriter` had before it: written before envelope
v1, it never picked up `schema_version`/`trace_id`/`tenant_id`/`session_id`).

| Transport | How | A transport MAY add on top of `to_dict()` |
|---|---|---|
| In-process | `async for ev in agent.stream(msg)` — real `Event` objects (T-8.5) | Nothing; the caller gets the `Event`, not a serialization |
| SSE | `GET /v1/runs/{id}/events` (`harness[server]`, §6.1) | `redact()`, on the run's own task (RT-13) |
| CLI/JSON | `harness run <file> <msg> --json` — one JSON line per event on stdout | `redact()`, same as SSE |
| Transcript (disk) | `Agent(transcript=<path>)` (§05.2) | `redact()`; digests `tool.requested`'s `arguments` for at-rest privacy — the one place a transport's OWN policy legitimately differs, not the event model |

---

## 7. Exception hierarchy

```
HarnessError
├── ConfigError                     ← always raised at import or construction, never at run
│   ├── MissingEffectError
│   ├── ToolSchemaError
│   ├── DuplicateToolError
│   ├── NonDeterministicPromptError
│   ├── UnsafeToolSetError          ← the external+danger construction check (F9.1)
│   ├── InvalidBudgetError
│   └── UnknownModelError
├── ToolContractError               ← tool returned something unserializable
├── SyncInAsyncContextError
├── RunFailed                       ← raised by .run() when result.ok is False
│   └── .partial: Result            ← partial work is never lost
├── BudgetExceeded
├── PolicyDenied
└── ProviderError
    ├── ProviderAuthError           (401/403)  — no retry
    ├── ProviderBadRequest          (400/404)  — no retry
    ├── ProviderRateLimited         (429)      — retry with Retry-After
    ├── ProviderUnavailable         (5xx)      — retry with backoff
    └── ProviderTimeout                        — retry with backoff
```

The `ConfigError` branch is the Poka-Yoke branch: **everything under it is detected before
a token is spent.** A test asserts that no `ConfigError` subclass is ever raised from
within `RunEngine.step()` — if one could be, its check belongs earlier.

---

## 8. Stability

| Surface | Stability | Change policy |
|---|---|---|
| `harness.__all__` | Stable | Semver major for breaking changes; 2-release deprecation |
| `ToolSpec`, `Effect`, `Verdict`, `Event` field sets | Stable | Additive only within a major |
| The 5 protocols | Stable, versioned | Each carries `API_VERSION: int`; a mismatch raises at registration with the required version |
| `EventKind` values | Additive | New kinds may appear in a minor; consumers must ignore unknown kinds |
| Anything under `harness._*` or not in `__all__` | Private | May change in a patch |
