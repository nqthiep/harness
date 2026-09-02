# Harness Design — Cost and Memory

**Scope:** the `Ledger` (`reserve`/`hold`/`release`/`snapshot`), context compaction, and
memory provenance. Vocabulary follows [`00-foundation.md`](00-foundation.md) §6 —
`Ledger`, `Label(integrity, confidentiality)`, `Effect`, `Decision`, `Run`.

Three measured shortcomings this file fixes:

| shortcoming | evidence | section |
|---|---|---|
| No project reserves budget **before** calling the model; `max_turns` caps **steps**, not **money** | ([§03](../research/03-safety-reliability.md) §20), ([§11](../research/11-workflow-and-dx.md) §22) | A.1 |
| A handoff gives **no isolation at all** — no fault, no privilege, no information | ([§09](../research/09-memory-context-multiagent-hitl.md) §13) | A.2 |
| **No memory system records provenance** — the largest security gap in the entire research effort | ([§09](../research/09-memory-context-multiagent-hitl.md) §10.2) | C.1 |

---

# PART A — COST

## A.1 `Ledger` — pre-flight `reserve()`

### The shortcoming being fixed

Measured across 23 Python packages: the highest `budget/kLOC` is pydantic-ai at 3.6;
LangGraph is **0.0** — no budget concept at all
([§03](../research/03-safety-reliability.md) §20). The only common mechanism is
`max_turns`/`max_iterations`. The research's conclusion:

> the industry has **loop limits** but almost no **spend ceiling** — a round with 200k
> input tokens costs a hundred times more than a short one, so counting rounds is not
> controlling cost ([§03](../research/03-safety-reliability.md) §20).

Concrete consequence: a 10-step run with an enormous context blows a budget that
`max_turns=10` never touches at all. And §22 confirms it from the developer's side:
*"Nothing reserves budget before a model call; `max_turns` bounds steps, not spend"*
([§11](../research/11-workflow-and-dx.md) §22).

### Whose design this borrows

pydantic-ai (the only general-purpose framework treating cost as a first-class concept,
`cost/kLOC` at 1.1) and **browser-use** — `cost_per_token`, `cost_usd`,
`_usage_from_events_with_costs()`, pricing each call at `beta/service.py:3197`
([§07](../research/07-remaining-python.md) *Second Correction*). Both do real
**accounting**. Neither **blocks in advance**. This harness takes their accounting and
adds what's missing.

### Signature

```python
from decimal import Decimal
from typing import Final

from harness._value import value
from harness.models.pricing import Price
from harness.result import Money, Usage

MIN_USEFUL_OUTPUT_TOKENS: Final[int] = 256      # below this, a cut-off answer isn't an answer
INPUT_MARGIN: Final[Decimal] = Decimal("1.15")  # compensates for input counting being an ESTIMATE


@value
class Budget:
    usd: Decimal                      # REQUIRED, no default, never None — 00 §1 invariant 2
    steps: int = 20
    wall_clock_s: float = 300.0


@value
class Reservation:
    id: str
    estimate: Money
    input_tokens: int
    max_tokens: int
    exact: bool             # True when the hard ceiling fit inside budget — see B.3
    created_at: float


class Ledger:
    def __init__(self, budget: Budget, *, clock: Callable[[], float] = time.monotonic) -> None: ...

    # --- three required calls, in this exact order, around EVERY model call ---
    def size_call(self, input_tokens: int, price: Price, model_max: int) -> int: ...
    def reserve(self, input_tokens: int, max_tokens: int, price: Price,
                *, hard_max_input: int | None = None) -> Reservation: ...
    def settle(self, reservation: Reservation, usage: Usage, price: Price) -> Money: ...

    # --- reading state ---
    def remaining_usd(self) -> Money | None: ...
    def remaining_steps(self) -> int: ...
    def remaining_wall_clock(self) -> float: ...
    def tool_timeout(self, spec_timeout_s: float) -> float: ...   # clamped to the remaining wall clock
```

### `max_tokens` is DERIVED, not a constant

This is where this design differs from every project read. `max_tokens` is neither set
by the caller nor an SDK default; it's the **quotient of the remaining balance**:

```
input_cost   = input_tokens x INPUT_MARGIN x calibration / 1e6 x price.input_per_mtok
affordable   = (remaining_usd - input_cost) / price.output_per_mtok x 1e6
max_tokens   = min(affordable, model_max)
```

With two stopping conditions, both `raise BudgetExceeded` instead of silently
truncating:

- `affordable <= 0` -> *"input alone costs more than the budget has left"*;
- `affordable < MIN_USEFUL_OUTPUT_TOKENS` -> an answer cut off at token 40 isn't an
  answer, it's just money spent with nothing to show.

Because `max_tokens` is derived from the remaining balance, **a reservation's worst case
never exceeds the budget, by definition** — no need to trust the estimate to get a
ceiling.

### Invariants

Naming them `COST-1…4` (not `C-1…4`) is deliberate: `03 §6.3` has four CANCEL rules also
numbered `C-1…4` — the two namespaces collide by coincidence, unrelated to each other
([review-kiss.md](review-kiss.md) K-13).

- **COST-1.** No path in the graph to the `model` node skips `reserve()`. Proven with
  `unguarded_paths()`, not by review — [`00-foundation.md`](00-foundation.md) §5 R-2
  (23 review rounds: 0 security bugs; 16 *runtime* rounds: 4 security bugs).
- **COST-2.** `reserve()` running out of budget -> `StopReason.BUDGET_EXHAUSTED`, never
  an exception leaking out and never a smaller call made "for appearances."
- **COST-3.** `settle()` uses **four** price tiers (input, output, cache read, cache
  write). Dropping cache read from the formula under-reports cost right when the harness
  is optimizing for caching.
- **COST-4.** After `settle()` pushes `spent` past the ceiling, the ledger `_blocked`s —
  every subsequent `size_call` raises. The **authorization** ceiling is exact; the
  **spend** ceiling can be exceeded by at most one call, and that overshoot is
  measurable via `Ledger.overshoot`. Stating that number plainly instead of claiming
  "the budget is absolute," because it isn't.

---

## A.2 `hold()` / `release()` — a sub-agent draws from an already-held slice

### Why this is needed: the research says a handoff gives no isolation at all

Reading `openai_agents`'s source: a handoff **is a tool call** whose return value is a
system prompt plus a toolset — no new process, no new context, no new client
([§09](../research/09-memory-context-multiagent-hitl.md) §13). None of the three things
"isolation" implies are present:

- no context isolation — `input_filter` defaults to `None`, the new agent sees the
  entire history;
- no fault isolation — same loop, same process;
- no privilege isolation — `_resolve_approval_key` combines the tool name + namespace +
  lookup key and **has no agent identity in the key** (`run_context.py:168`), so a tool
  approved while agent A was running is still approved when B calls it.

The research's conclusion: *"Multi-agent as implemented is a router, not an
architecture,"* with this consequence for the harness: if a sub-agent needs to be
genuinely isolated — unable to spend the parent's balance, not inheriting the parent's
approvals — **the framework won't give that; it has to be a first-class construct with
its own ledger** ([§09](../research/09-memory-context-multiagent-hitl.md) §13).

### A strength of openai-agents that must be kept

The same research passage found something they get **right**, and it's not obvious:
`current_turn = 0` appears **exactly once**, at `run.py:762`, when a run starts. At the
handoff site (`line 2050`) only `current_agent` is reassigned. **A handoff cannot reset
the turn budget** — a naive implementation (one `max_turns` per agent) turns a
delegation chain into an infinite loop; three agents handing off in a circle run forever
([§09](../research/09-memory-context-multiagent-hitl.md) §13).

So this harness's rule: **money is split; steps and the clock are not.**

| resource | across a sub-agent boundary | why |
|---|---|---|
| `usd` | **split** — the child draws from an already-held slice | the isolation §13 says the framework doesn't give you |
| `steps` | **inherits the remaining count**, never reset | openai-agents's strength, §13 |
| `wall_clock_s` | **inherits the remaining time**, never reset | same reason; resetting it would leave a delegation chain with no time ceiling |

### Signature

```python
@value
class Hold:
    id: str
    amount: Money
    opened_at: float


class Ledger:
    def hold(self, amount: Money) -> Hold:
        """Reserves a slice of budget in advance. `spent` rises IMMEDIATELY, before the child runs."""

    def release(self, hold: Hold, actual: Money) -> None:
        """Replaces the held slice with what was actually spent: spent = spent - hold.amount + actual."""

    def slice_for_child(self, hold: Hold) -> "Ledger":
        """The child's ledger: usd = hold.amount, steps and wall clock are the parent's REMAINING amounts."""
        return Ledger(Budget(usd=hold.amount.decimal,
                             steps=self.remaining_steps(),
                             wall_clock_s=self.remaining_wall_clock()))
```

`hold()` returns what was **actually held** (`min(amount, max(remaining, 0))`), not what
was asked for. Because `spent` rises immediately at `hold()`, N sub-agents running in
parallel **split** the remaining balance instead of each reading the same `remaining`
and each asking for all of it — exactly the ledger TOCTOU this very repo made in
Round 28.

### Why this isn't a context manager

The temptation is `async with ledger.slice(Money("0.10")) as child:` so `release` runs
in a `finally`. Not used, because a sub-agent can **pause mid-run** waiting for a human
`Decision` ([`00-foundation.md`](00-foundation.md) §4) and the run can resume in a
different process. When it does, the `finally` never runs in the process that opened the
hold. So a hold is **data in checkpointed state**, not a `try` block:

- a hold not yet released after a restore = the money is still counted as spent ->
  **fail-safe** (better to overcount than undercount);
- `Hold.id` lets the right slice be released after a resume.

---

## A.3 `snapshot()` / `restore()` — the ledger lives in state, not on an object

### The shortcoming being fixed

[`00-foundation.md`](00-foundation.md) §5 R-4: no shared state outside checkpointed
state. The research found a weaker version of exactly this bug at a major vendor:
Microsoft's security module sets middleware into `threading.local()` and then sets it
across an `await`, so two concurrent tool calls read each other's slot, silently and
**fail-open**; plus `_global_variable_store` and `_quarantine_chat_client` at module
scope — in a multi-tenant server, one tenant's change becomes every tenant's
([§09](../research/09-memory-context-multiagent-hitl.md) §16bis).

The ledger is the worst-case instance of this bug: a ledger sitting on a shared Runtime
object means **customer A's budget is charged to customer B**. And a `ContextVar`
doesn't save this either, since every LangGraph node runs in a copied context.

### Signature

```python
@value
class LedgerState:
    """Decimal arithmetic, encoded as str so it survives any JSON checkpointer."""
    spent: str
    steps: int
    calibration: str
    overshoot: str
    holds: tuple[tuple[str, str], ...]     # (hold_id, amount) — slices not yet released


class Ledger:
    def snapshot(self) -> LedgerState: ...

    @classmethod
    def restore(cls, budget: Budget, state: LedgerState | None,
                *, clock: Callable[[], float] = time.monotonic) -> "Ledger": ...
```

### One rule, and it's fail-closed

**If the ledger state can't be read, the run doesn't run.** Not "start over from zero" —
that's fail-open: a corrupted checkpoint would turn a budget already 90% spent into an
untouched one, and a retry loop against a corrupted checkpoint is the fastest way to
burn money there is. `restore()` hitting unparseable state -> `CorruptLedgerState`, the
run stops, a human looks at it. This differs from the current implementation in
`src/harness/budget/ledger.py`, which swallows the error and starts clean.

`calibration` only **rises** on restore (`max(1, saved)`) — undercounting is the
dangerous direction, overcounting only costs a bit of headroom.

---

## A.4 Real cost — turning tokens into money

### Who holds the pricing table

Exactly one module: `harness.models.pricing`. It holds `PRICES`, `MAX_CONTEXT`,
`MAX_OUTPUT`, and a constant `AS_OF: Final[str]` — the date the pricing table was
confirmed.

```python
@value
class Price:
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cache_write_per_mtok: Decimal
    cache_read_per_mtok: Decimal


AS_OF: Final[str] = "2026-06-24"

def price(model: str) -> Price:
    """An unknown model -> UnknownModelError. NEVER returns zero."""
```

This module's most important rule is its error rule: **a model with no known price is
refused, not called.** Returning `Price(0,0,0,0)` for an unknown model turns the cost
ceiling into a no-op right at the moment a caller has just switched to a new model. This
is the exact failure class both `03-safety-reliability` §20 and §22 describe — "a
framework with a limit, no concept of money" (MS Agent Framework: `budget/kLOC` at 1.9,
`cost/kLOC` **0.0**).

### When a model's price changes

Three rules, KISS:

1. **Pinned per run.** `Price` is resolved once at the start of a run and written into
   checkpointed state alongside `AS_OF`. A run paused for two days and resumed after the
   pricing table changes is still billed at the price it started with — otherwise
   `spent` already recorded and `remaining` computed against it would be using two
   different pricing tables, and no number in a report would still be correct.
2. **Recorded in the audit trail.** The run-ending event carries `model`, `price_as_of`,
   `usage`, `cost_usd`. A pricing table is an input to a money calculation; a money
   calculation whose input can't be traced isn't auditable.
3. **A provider may replace the pricing table, never bypass it.**
   `Provider.price(model) -> Price` is a seam for a customer with negotiated pricing or
   their own gateway. Per [`00-foundation.md`](00-foundation.md) §5 R-1: **cost
   accounting is a plugin, budget enforcement is the mandatory path** — the *pricing
   table* can be swapped, *having to reserve* cannot.

### What number to report

Not cost/task. The research provides a formula
([§03](../research/03-safety-reliability.md) §20):

```
Cost_successful = (LLM + Tool + Infra + Sandbox + Observability + HumanReview) / P(success)
```

The harness can measure the first two terms and the denominator (via `StopReason`); the
rest belongs to the deployment. We report **`cost` and `stop_reason` per run** so the
denominator is computable, instead of reporting one averaged cost number that hides half
the runs failing.

---

# PART B — CONTEXT / COMPACTION

## B.1 Preserving `tool_call`/`tool_result` pairing — invariant I-3

### The cleanest Poka-Yoke ladder in the research

The research calls this *"the cleanest Poka-Yoke ladder in the study"*
([§09](../research/09-memory-context-multiagent-hitl.md) §11):

**LangChain DOCUMENTS the invariant.** `langchain_core/messages/utils.py:1133`,
`trim_messages`, the docstring says *"a `ToolMessage` can only appear after an
`AIMessage` that involved a tool call. To achieve this, set `start_on='human'`."* But
`_first_max_tokens` (line 1970) and `_last_max_tokens` (line 2086) **have no pairing
logic at all**. Three things worth noting:

1. the invariant depends on a flag the **caller** passes, defaulting to `None` -> the
   default path can cut between a tool call and its result;
2. the invariant only holds if the caller **has read the docstring**;
3. and even when it's on, `start_on='human'` "fixes" it by **throwing away the whole
   exchange** rather than keeping the pair.

Meanwhile the very same package has `filter_messages`, which does this right
(`exclude_tool_calls` removes matching `ToolMessage`s and rewrites `tool_calls` on the
`AIMessage`) — **the capability already exists in the package, and the token-budget path
doesn't use it**.

**Microsoft COMPUTES the invariant.** `agent_framework/_compaction.py:105`:

```python
def _unambiguous_function_call_result_pairs(messages: Sequence[Message]) -> list[tuple[int, int]]:
```

Walks the transcript building `call_id -> [declaration indices]`, matches each
`function_result` against pending declarations and pops one. The word **unambiguous** in
the name is deliberate: a **duplicate** `call_id` is handled as a candidate list rather
than assumed unique. Compaction afterward only operates on **index pairs**, so a pair
can never be half-deleted.

### The design

Learned from Microsoft: **compute it**, don't document it. And go one step further than
they do.

```python
@value
class Pairing:
    pairs: tuple[tuple[int, int], ...]   # (assistant-with-tool_call index, tool_result index)
    orphan_calls: tuple[int, ...]
    orphan_results: tuple[int, ...]


def call_result_pairs(messages: Sequence[Mapping[str, Any]]) -> Pairing:
    """A duplicate call_id is matched in order of appearance (a candidate list, popping the
    earliest).

    A duplicate call_id is a real thing when merging two branches or replaying a
    checkpoint; "unambiguous" is exactly the assumption Microsoft's function name is
    flagging.
    """
```

Three rules:

- **I-3.** Every message-list transformation may only ever read `Pairing.pairs`. A
  half-deleted pair is a request the provider refuses — a runtime error caused by a
  programming error.
- **I-3a.** `orphan_*` non-empty -> emit an event and **do not compact** that turn. An
  orphan is a symptom of a bug somewhere else; compacting further on top of an already
  broken transcript destroys the evidence.
- **I-3b — where this goes further than Microsoft.** The pairing check is a
  **postcondition on the mandatory path**, not something living inside a strategy:

```python
class Compactor(Protocol):
    async def compact(self, messages: Sequence[Msg], *, target_tokens: int,
                      pairing: Pairing) -> Sequence[Msg]: ...


async def compact_checked(c: Compactor, messages: Sequence[Msg], *,
                          target_tokens: int) -> list[Msg]:
    out = list(await c.compact(messages, target_tokens=target_tokens,
                               pairing=call_result_pairs(messages)))
    after = call_result_pairs(out)
    if after.orphan_calls or after.orphan_results:
        raise CompactionBrokePairing(c, after)
    return out
```

Reasoning per [`00-foundation.md`](00-foundation.md) §5 R-1: compaction is a **plugin**,
the invariant is the **mandatory path**. Microsoft places pairing *inside* their own
compaction module, so a third-party strategy someone writes could break the pairing.
Here, it can't.

### And we don't throw away the whole exchange

LangChain's fix (`start_on='human'`) preserves the invariant by **dropping the whole
conversation segment**. This harness clears the **content** of an old `tool_result` and
keeps **both messages** intact:

```python
CLEARED: Final[str] = "[earlier tool result cleared to save context]"
```

The pair stays intact, the structure stays intact, the model still sees *that* it called
a tool — only the 40 kB payload is gone. This is both cheaper and less lossy at the same
time.

---

## B.2 The compaction strategy — Microsoft's priority order, but **two** strategies instead of five

### Whose design this borrows

Around `_unambiguous_function_call_result_pairs` sit **five** strategies:
`SelectiveToolCallCompactionStrategy`, `ToolResultCompactionStrategy`,
`SummarizationStrategy`, `ContextWindowCompactionStrategy`, and a `CompactionStrategy`
Protocol. **Two of the five target tool results specifically**, and the research calls
that *the right priority*:

> in a tool-using agent, tool output is where the tokens actually accumulate, and it is
> also the most compressible (a 40 kB HTML fetch is worth two sentences on the next
> turn). No other framework surveyed separates tool-result compaction from
> message-history compaction ([§09](../research/09-memory-context-multiagent-hitl.md)
> §11).

### The shortcoming being fixed: five strategies is five strategies someone has to turn on

The lesson attached to it, from the same vendor: the best safety architecture in the
research is **opt-in, experimental, and their own harness doesn't import it**
([§09](../research/09-memory-context-multiagent-hitl.md) §16bis). A wide configuration
surface is a configuration surface defaulting to off. So KISS: keep Microsoft's
**priority order**, cut the count.

**Two strategies, applied as a ladder:**

| threshold | strategy | cost | what's lost |
|---|---|---|---|
| `used/window >= 0.60` | **`ClearToolResults`** — clears old `tool_result` content, keeps the most recent `KEEP_RECENT_STEPS = 3` steps intact | 0 tokens, deterministic, no model call | only old tool content |
| `used/window >= 0.80` | **`SummarizeOldPrefix`** — one model call summarizes the early portion, keeps the tail intact | one model call, must `reserve()` like any other | detail from the early portion |

```python
EDIT_AT: Final[float] = 0.60
COMPACT_AT: Final[float] = 0.80
KEEP_RECENT_STEPS: Final[int] = 3

def manage(messages: Sequence[Msg], *, used_tokens: int, context_window: int
           ) -> tuple[list[Msg], Literal["none", "edited", "compact_needed"]]: ...
```

Editing first, summarization second: editing is **free and lossless for recent work**;
summarization costs a model call — and that call has to go through `reserve()` too
(§A.1), so compacting context **is also spending**, and it too sits under the ceiling.

Microsoft's remaining three strategies are cut because: `ContextWindow` is exactly the
threshold above; `SelectiveToolCall` sits on the same axis as `ClearToolResults` at a
finer grain the research gives no evidence is worth the extra granularity; the
`Protocol` is kept — it's the plugin seam, and the only one of the five that isn't
itself a policy.

### Compaction must not launder taint

A point no framework checks, and it falls directly into the lattice in
[`00-foundation.md`](00-foundation.md) §3.2:

> **A summary of `UNTRUSTED` content is `UNTRUSTED`.** A summary of context holding
> `SECRET` is `SECRET`.

A merged message's `Label` = the `join` of the `Label` of every message it replaces.

> **`ClearToolResults` must preserve the label too.** Clearing a tool result's *content*
> does **not** clear that message's `Label` — an emptied message still carries its old
> label and still participates in the `join` at L-3. An early draft described this
> operation as safe because it "keeps both messages," but that's only safe when the
> label stays with them. Combined with L-2 ([00 §3.2](00-foundation.md)) — a
> model-generated message already carries the label of context at the moment it was
> generated — the S-19-class laundering path is blocked in two independent places.

Without this rule, summarization becomes the perfect **label-laundering** path:
malicious web content enters as `UNTRUSTED`, exits as a paragraph the model wrote that
looks exactly like trusted content, and the lattice loses effect exactly when context is
longest. Composition is monotonic — never decreasing within a run.

---

## B.3 Counting tokens — nobody's exact, so state the error handling plainly

### The evidence

> **Nobody has a token-exact budget.** `count_tokens_approximately`
> (`langchain_core/messages/utils.py:2244`) is named honestly. The `ctxwindow` column is
> near-zero everywhere (max 1.5, llama-index). Frameworks compact against an estimate
> and discover the real number when the provider rejects the request
> ([§09](../research/09-memory-context-multiagent-hitl.md) §11).

That honest name is a strength of LangChain's, kept here: **the counting function's name
carries the word `approximately`**, so nobody at a call site mistakes it for the truth.

### Four layers of defense

```python
def count_tokens_approximately(request: ModelRequest) -> int:
    """AN ESTIMATE. The name says so because it is one."""

def hard_max_input_tokens(request: ModelRequest) -> int:
    """A HARD CEILING: the character count of the rendered request.

    No tokenizer produces more tokens than there are characters, so this is a
    computable upper bound that needs no provider call.
    """
```

1. **A margin + calibration.** Every reservation multiplies counted input by
   `INPUT_MARGIN = 1.15` and by `calibration` — the ratio `billed_input /
   counted_input` observed at `settle()`, **only ever rising, never falling**.
   Undercounting is the dangerous direction; overcounting only costs headroom.
2. **The hard ceiling, when it fits.** If computing with `hard_max_input_tokens` *still*
   fits inside budget, use that number directly -> the reservation is **exact**, not an
   estimate, and `Reservation.exact = True`. When the budget is wide, there's no need to
   trust the estimate at all.
3. **Compact to `0.80 x window`, not to `window`.** The 20% margin is room for counting
   error. Compacting right to the edge and then getting refused by the provider is the
   real scenario §11 describes.
4. **One deterministic retry, then stop.** When a provider returns a context-length
   error:
   - the refused call's `reserve` is **cancelled, never `settle`d** — a refused request
     costs nothing, and the ledger must not count it as spent;
   - `calibration` is raised in proportion to the observed overshoot;
   - compaction runs again **once** at a tighter target (`0.60 x window`);
   - a second failure -> `StopReason.TRUNCATED` with `detail` stating both the estimate
     and what the provider reported. **No retry loop** — a loop that keeps shrinking
     against a wrong estimate is how a budget gets burned in six calls.

---

# PART C — MEMORY

## C.1 Provenance on EVERY memory write

### The largest gap the research found

Searching for `taint|provenance|untrusted` across all 23 packages
([§09](../research/09-memory-context-multiagent-hitl.md) §10.2):

| package | hits | note |
|---|---:|---|
| letta-client | **0** | |
| langmem | 5 | |
| mem0ai | 7 | |
| `agent_framework/_harness/_memory.py` | **0** | |
| agno | 100 — **unrelated** | `stamp_schedule_provenance`, routing in `agno/team/_run.py`; not information flow |

The verbatim conclusion:

> **No memory system surveyed records where a memory came from.** A fact extracted from
> an attacker-controlled page is stored identically to one the user typed. Retrieval
> cannot distinguish them, so the injection survives the session that carried it. […]
> this is the largest unaddressed security gap found in the study.

Combined with §16bis: the only real information-flow implementation
(`agent_framework/security.py`) **is never connected to that same framework's own
memory subsystem**.

This is where prompt injection becomes **persistent**: an agent reads a hostile web
page, extracts a "fact," writes it to long-term memory, and that fact gets recalled and
trusted in every session after — including a different user's session, if the store is
shared.

### The design: every record carries a `Label`

Uses the exact two-directional lattice from [`00-foundation.md`](00-foundation.md) §3.2,
inventing no new axis.

```python
# Integrity / Confidentiality / Label: canonical definition in 00-foundation.md §3.2.
# Used only, here, never redefined.
from harness import Integrity, Confidentiality, Label
```


```python
@value
class Provenance:
    run_id: str
    step: int
    source: str            # "human" | a tool name | an agent name — filled by the runtime
    label: Label
    written_at: datetime


@value
class Memo:
    key: str
    value: str
    score: float
    updated_at: float
    provenance: Provenance          # NO default value — see W-3


class Store(Protocol):
    async def get(self, key: str) -> Memo | None: ...
    async def put(self, key: str, value: str, *, provenance: Provenance,
                  ttl_s: float | None = None) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def search(self, query: str, *, limit: int = 5) -> Sequence[Memo]: ...
    async def close(self) -> None: ...
```

`provenance` is a **required keyword, no default.** There's no way to write a memory
without saying where it came from — the type checker blocks it at compile time, not
review blocking it at a PR. This is Poka-Yoke in the literal sense, per
[`00-foundation.md`](00-foundation.md) §8 rule 3.

### Write rules

- **W-1 (the label is derived, never declared).** `provenance.label` = the `join` of the
  `Label` of everything currently in context at that step. The model reads a web page
  and then calls `remember(...)` -> that memory is `UNTRUSTED`, because an `external`
  tool's output makes context `UNTRUSTED`
  ([`00-foundation.md`](00-foundation.md) §3.2) and the label never decreases.
- **W-2 (never writes above the store's own ceiling).** A store declares
  `max_confidentiality`. Writing a `SECRET` memo into a store with
  `max_confidentiality=PUBLIC` -> refused. This is exactly the "shared store" scenario
  §10.2 names: a store shared across tenants is a `PUBLIC` sink.
- **W-3 (the model never fills provenance).** `run_id`, `step`, `source`, `label`,
  `written_at` are filled by the runtime. The model supplies only `value`. A direct
  parallel to `Decision`'s invariant **D-1**
  ([`00-foundation.md`](00-foundation.md) §4), and exactly agno's mistake: a log written
  by the party being audited isn't a log.

### Read rules

Naming them `MEM-R1…3` (not `R-1…3`) is deliberate: `00-foundation.md §5` has four
global architecture rules also numbered `R-1…4` — the two namespaces collide by
coincidence, one local to this section, one global to the whole design
([review-kiss.md](review-kiss.md) K-13). A citation to a GLOBAL rule elsewhere in this
file always writes it out in full as `00-foundation.md §5 R-X`, never shortened.

- **MEM-R1 (recall raises the label).** After `recall`, the run's `Label` = the `join` of
  the current label with the label of **each memo** loaded in. Monotonic.
- **MEM-R2 (no new mechanism).** An `UNTRUSTED` memo in context cannot steer a `danger`
  tool unless the tool declares `accepts_tainted=True` — **the exact same rule already
  in place** for `external` tool output
  ([`00-foundation.md`](00-foundation.md) §3.2). KISS: memory doesn't get its own rule.
- **MEM-R3 (no leaking).** Context holding a `SECRET` memo cannot call a tool with
  `max_confidentiality=PUBLIC` (the default for `external` and `write`). This is what
  blocks the "recall a secret, then POST it to a webhook" scenario.

### What happens when an `UNTRUSTED` memory gets recalled into context

Concretely, since this is the central question:

1. `recall` returns a memo with `provenance.label.integrity == UNTRUSTED`.
2. The run's label rises to `UNTRUSTED`. A `taint.raised` event is logged with
   **`provenance` as the source** — the memo's key, the `run_id` and `step` that wrote
   it, which tool was the `source`. This is something no system surveyed has: **tracing
   back to the exact run that planted the fact.**
3. The run **keeps going**. Refusing to read `UNTRUSTED` memory would make memory
   useless — almost every useful long-term memory is derived from outside content.
   What's blocked is the **action**, not the read.
4. If the model then wants to call a `danger` tool, policy `DENY`s/`ASK`s per MEM-R2.
   The only path forward is a `Decision` from an `Actor` that is a person
   ([`00-foundation.md`](00-foundation.md) §4.2 — no `Model` variant), and that
   `Decision`'s `Scope` carries the provenance of the memo that caused the taint, so the
   approver sees **where this fact came from**, not just a tool name.
5. Marking it in the prompt (wrapping a memo in a delimiter, labeling it) is
   **advisory** — it helps the model, it is **not** an enforcement mechanism.
   Enforcement lives in the lattice. Stated plainly because every design that conflates
   the two ends up with a safety mechanism the model can just talk its way around.

---

## C.2 What effect is `recall`?

### C.2bis — `provenance` may only RAISE the label, never keep it low

An early draft had `recall` join the label of **each record** instead of applying
`spec.emits` the way every other `external` tool does, reasoning "not an exception, just
the same rule in its general form." A reviewer pointed out that general form is only
correct **if the per-record label has integrity** — a condition stated nowhere
([review-security.md](review-security.md) S-5).

Scenario: run A reads a hostile web page, writes a memo with `integrity=UNTRUSTED`
(correct). But `Provenance` **lives in the store**, and the store is an external
system — no signature, no MAC. An attacker, a different tenant, or a tampered backup
resets it to `integrity=TRUSTED`. Run B `recall`s, every memo self-declares `TRUSTED`,
run B's label **never rises**, and the injection has become both persistent and
invisible to the lattice. That's a **mechanism for lowering the effective label, driven
by data the sink itself supplies** — landing squarely on shortcoming #6, the very one
the README claims is fixed.

Three replacement rules:

| # | rule |
|---|---|
| **M-1** | `recall` is `external` like every other `external` tool: **always** `join(UNTRUSTED)`. No default exception. |
| **M-2** | A read-back `provenance.label` is only ever used to **raise** the label, never to keep it low — matching exactly the monotonicity [00 §3.2](00-foundation.md) requires. |
| **M-3** | For `recall` to **not** raise the label requires BOTH: the operator declaring `trusted_provenance=True`, **and** the record carrying a MAC signed by the harness using the deployment's own key. Missing either => M-1. |

M-3 is the only place in this design where a claim from an external system is trusted,
and it's trusted **only when it carries the harness's own signature**. That's the
difference between "trusting data" and "trusting your own signature on data."


**`external`. Not `read`.**

The reasoning follows the Effect table in [`00-foundation.md`](00-foundation.md) §2:
`external` is the **only** class with `taints_output = True`. A context database that
ingests web content (OpenViking has `ov add-resource https://…`) means anything it
returns **could have been written by an attacker**. If `recall` were `read`, a poisoned
memory would buy its way into calling a `danger` tool, and the lattice would have a hole
the exact size of the memory system. This repo's existing implementation already chose
correctly — `src/harness/memory/viking.py` declares `@tool(effect="external")` for
`recall`.

The remaining four behaviors are **derived**, the tool author declares nothing extra:

| property | value | why it's correct for `recall` |
|---|---|---|
| parallel | yes | recall is a read query; several in parallel don't contend |
| retryable | yes | no side effect |
| taints context | yes | this is the whole reason for the classification |
| default verdict | `ALLOW` | blocking recall makes the agent useless; what's blocked is the *action* that follows |
| audit level | `info` | |

**Effect is a static ceiling; `Label` is dynamic precision.** The `external`
classification states *the worst thing* a store could return. `Provenance` on each
`Memo` states *what actually happened* this time: a recall that returns only `TRUSTED`
memos (typed by a human) joins into context without raising the label. This isn't an
exception to the "`external` -> `UNTRUSTED`" rule, it's the same rule in its general
form: `join` the label of each record. For a `web_fetch`, there's no per-record label to
join, so it's always `UNTRUSTED` — a special case. **This is provenance's payoff:**
without it, every recall would have to assume the worst, forever.

**`remember` is `write`, not `danger`**: writing a memory is reversible. The condition
for saying that is the store **has no `rm` capability** — `viking.py` deliberately
leaves `rm` out of `ALLOWED_CALLS` and `delete()` writes an empty value, so the
database's own history stays recoverable. If a backend lacks that property, `remember`
on that backend is `danger`, not `write`.

---

## Summary table: whose design, what it fixes

| section | borrows the strength of | fixes the shortcoming |
|---|---|---|
| A.1 pre-flight `reserve()` | pydantic-ai + browser-use (real cost accounting) | nobody reserves in advance; `max_turns` caps steps, not money ([§03](../research/03-safety-reliability.md) §20) |
| A.2 `hold()`/`release()` | openai-agents: turn budget doesn't reset across a handoff | a handoff gives no isolation at all ([§09](../research/09-memory-context-multiagent-hitl.md) §13) |
| A.3 `snapshot()`/`restore()` | — | `threading.local()` + a module-level singleton ([§09](../research/09-memory-context-multiagent-hitl.md) §16bis); R-4 |
| A.4 the pricing table | browser-use's `_usage_from_events_with_costs()` | "a limit exists, no concept of money" ([§03](../research/03-safety-reliability.md) §20) |
| B.1 pairing | Microsoft's `_unambiguous_function_call_result_pairs` | LangChain only documents it; `start_on` defaults to `None` and "fixes" it by discarding the whole exchange ([§09](../research/09-memory-context-multiagent-hitl.md) §11) |
| B.2 two strategies | Microsoft: 2/5 target tool results specifically | 5 strategies = 5 things someone has to turn on; see §16bis |
| B.3 counting tokens | LangChain's honestly-named `count_tokens_approximately` | nobody has a token-exact budget ([§09](../research/09-memory-context-multiagent-hitl.md) §11) |
| C.1 provenance | — (nobody has it) | the largest security gap the research found ([§09](../research/09-memory-context-multiagent-hitl.md) §10.2) |
| C.2 `recall` = `external` | MCP's `readOnlyHint`, pydantic-ai's `ToolKind` | no memory system distinguishes sources on recall (§10.2) |

---

## Not Enough Evidence

- **Letta's internals.** `letta-client` is a generated API client, not the server; its
  12.0 summarize density describes the API surface, not how Letta implements memory
  ([§09](../research/09-memory-context-multiagent-hitl.md) §45). Any claim about Letta's
  memory architecture: not enough evidence.
- **langmem's 5 `taint|provenance|untrusted` hits and mem0ai's 7 were not read line by
  line.** The lesson from agno (100 `provenance` hits that turned out to be schedule
  provenance) suggests they could also be unrelated — or could be something real.
  Unverified.
- **The 0.60 / 0.80 thresholds and `KEEP_RECENT_STEPS = 3` are not measured.** They are
  reasonable starting points, not the result of optimization. The real break-even point
  (tokens saved minus the cost of the summarization call itself) hasn't been measured on
  any workload.
- **The error distribution of `count_tokens_approximately` has only been observed on one
  model family.** `INPUT_MARGIN = 1.15` is an empirical number; for a different model
  or language (Vietnamese has a different token/character ratio than English) it could
  be too tight or too loose.
- **Whether a provider's context-length error code is stable and classifiable** — the
  "one retry then stop" rule in B.3 assumes this error can be told apart from a
  transient one. Not verified across multiple providers.
- **How often model pricing changes.** The pin-per-run rule in A.4 is fail-safe, but
  there's no data on how long `AS_OF` should be considered current.
- **Multi-tenant isolation at the OpenViking server layer** is unverified. Rule W-2
  blocks writing `SECRET` into a `PUBLIC` store on the harness's side; it doesn't prove
  the server itself doesn't leak between namespaces.
- **The storage cost of provenance.** Every `Memo` carries an extra `Provenance`; with a
  store holding millions of records, the overhead ratio hasn't been measured.
- **Memory TTL/expiry is out of scope for this design.** An `UNTRUSTED` memory that
  lives forever is a real risk, but the research has no evidence about which expiry
  policy is correct, so none is invented here.
