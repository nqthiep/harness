# 07 — Cost

> Cost is an architectural concern here, not a post-hoc optimization. Three of the five
> core services exist for it, and two of them cannot be replaced by a plugin precisely
> because a replacement could disable the guarantee.

## 1. The budget is a ceiling, not an alert

Most agent frameworks report cost after the fact. That is a dashboard, not a control. The
harness checks **before** each model call:

```python
max_tokens = ledger.size_call(input_tokens, price, model_max)   # derived — ADR-017

estimate = (input_tokens / 1e6) * price.input_per_mtok \
         + (max_tokens   / 1e6) * price.output_per_mtok

if ledger.spent + estimate > budget.usd:
    return Result(stop_reason=BUDGET_EXHAUSTED, ...)   # graceful, with partial text
```

**`max_tokens` is derived from what is left in the budget, not configured separately.**
This is not a refinement; it fixes a defect found in Round 17. When the two were
independent, a `$0.05` budget against the default `max_tokens=16000` reserved `$0.406` and
therefore **refused to make any call at all** — a beginner's first run would stop having
said nothing. The default `$0.50` budget cleared the same reservation by nine cents, by
accident.

Deriving one from the other makes the contradiction unrepresentable, and a small budget now
produces a **short answer** rather than **no answer**:

| budget | derived `max_tokens` | behavior |
|---|---|---|
| `$0.05` | ~1 760 | a few paragraphs |
| `$0.10` | ~3 760 | a longer answer |
| `$0.50` (default) | ~19 760 | effectively unconstrained |
| below ~256 tokens affordable | — | stops instead; a truncated answer is not an answer |

The estimate is deliberately the **worst case**: it assumes the model emits its full
`max_tokens` and ignores the cache-read discount. Over-estimating stops a run slightly
early; under-estimating breaks the ceiling. After the call, `settle()` records the true
cost from `response.usage`, so the ledger tracks reality while the gate stays conservative.

**Defaults are finite on every axis** — `$0.50`, 20 steps, 300 s. An unlimited default is
fail-open, and the failure it opens onto is a five-figure invoice. `Budget(usd=None)` is
available, requires typing `None`, and emits a warning event on every run.

| Axis | Default | Checked |
|---|---|---|
| USD | `$0.50` | Before each model call |
| Steps | 20 | Top of each loop iteration |
| Wall clock | 300 s | Top of each iteration and before each tool |
| Tokens | none | Before each model call, when set |

Budget exhaustion is a `StopReason`, not an exception, from `try_run` — an expected
boundary rather than a failure. `run()` raises `RunFailed` carrying `.partial`, so the
work already done is never lost.

### 1.1 What the budget does **not** cover, and where that gap is closed

A per-run budget says nothing about running the same script eighty times. Round 14 raised
this as a real beginner cost event (G13.6). The answer is deliberately split, and the split
matters:

| Mechanism | Strength | Where |
|---|---|---|
| **Per-run budget** | A **ceiling.** Enforced before every call. Cannot be exceeded. | This library (ADR-005) |
| **Session warning** | A **warning only.** One message per process when cumulative spend across runs passes `$5`. No files, no locks, no cross-process state. | This library (ADR-016) |
| **Account spend limit** | A **hard ceiling across everything.** | **The provider.** `harness setup` prints the URL and asks the user to set one. |

The council rejected a persistent daily cap in the library: cross-process spend accounting
means file locking, clock skew and a race a library cannot win — to reimplement, badly, a
limit the provider already enforces properly.

**The session warning is never described as a limit.** Two mechanisms that sound alike and
have different strengths are worse than one, unless the difference is restated every time
either is mentioned. That is why this table exists rather than a sentence.

## 2. Caching by construction

Prompt caching is a **prefix match**: any byte change anywhere in the prefix invalidates
everything after it. Render order is `tools` → `system` → `messages`. Get this wrong and
you silently pay roughly 10× forever, with no error, no warning, and no obvious symptom.

The documented invalidators are all "remember not to do this". The harness makes the
common ones **impossible**, and detects the rest **before spending anything**.

| Known invalidator | How the harness removes it |
|---|---|
| `datetime.now()` / UUID interpolated into the system prompt | **Detected at construction** by the cache linter (§2.1) |
| Tools reordered between calls | `ToolSet` is name-sorted at construction; order cannot vary |
| Non-deterministic JSON serialization | Canonical serializer: `sort_keys=True`, fixed separators, no `set` iteration |
| Tool set changed mid-conversation | `tools` is frozen on the `Agent`; there is no add/remove API |
| System prompt edited mid-conversation | `job` is frozen; `chat.tell()` appends a `role:"system"` message instead |
| Model switched mid-conversation | `model` is frozen; a different model means a different `Agent` |
| Conditional system sections (`if flag: system += ...`) | The system prompt is assembled once, at construction, from frozen inputs |
| Per-user tool sets | Structurally possible, but flagged: constructing agents inside a request handler emits a `cache.per_request_agent` warning once, with the fix |
| A subagent fork rebuilding the prefix | `as_tool()` copies the parent's rendered `system`/`tools`/`model` verbatim and appends |

### 2.1 The cache linter

At `Agent.__init__`, the assembler renders the full prefix twice, 150 ms apart, and
byte-compares the results.

```
NonDeterministicPromptError: your agent's prompt changes between calls, so caching
will never work and every call will cost roughly 10x more.

  The difference is in the system prompt, bytes 118-137:

    run 1: ... Current time: 2026-08-26 14:22:07 ...
    run 2: ... Current time: 2026-08-26 14:22:07 ...
                             ^^^^^^^^^^^^^^^^^^^ differs

  Move it out of `job=` and into the message instead:

      agent.run(f"[now: {datetime.now():%H:%M}] {question}")

  → docs/07-cost.md#21-the-cache-linter
```

This converts the single most expensive invisible bug in LLM applications into a
construction-time exception with the fix printed. It costs nothing to run and nothing to
maintain.

### 2.2 Breakpoint placement

| Situation | Placement |
|---|---|
| Any agent with tools or a non-trivial job | One breakpoint on the last system block — caches `tools` + `system` together, since tools render first |
| Multi-turn conversation | One additional breakpoint on the last content block of the most recent turn; earlier breakpoints stay valid, so hits accrue as the conversation grows |
| Prefix under ~1 024 tokens | **No breakpoint at all.** Below the minimum cacheable prefix, a marker only pays the write premium with zero reads. The assembler counts and omits it. |

Maximum 4 breakpoints; the assembler never emits more.

### 2.3 Runtime verification

Not everything can be caught statically. After step 3, if `cache_read_input_tokens` is
still 0 while the prefix exceeds the minimum, the harness emits a loud
`error.raised{where:"cache"}` event naming the most likely cause. `harness cost <transcript>`
prints realized cache hit rate and the money left on the table.

## 3. Token discipline

| Source of waste | Control | Default |
|---|---|---|
| Huge tool results | `max_result_tokens` per tool, truncated with a visible marker | 4 000 |
| Unbounded history | Context editing clears old tool results | at 60 % of context |
| History beyond that | Server-side compaction summarizes earlier turns | at 80 % of context |
| Verbose reasoning on simple tasks | `effort="medium"` default rather than `high` | medium |
| Duplicate identical tool calls | Detected within a step; second call returns the cached result | on |
| Retrying a non-retryable failure | Effect class decides; `write`/`danger` never auto-retried | derived |
| Serial execution of independent reads | Effect class decides; `read`/`external` run concurrently | derived |
| Re-billing a full history to a subagent | Subagents get an explicit, minimal context — not the parent transcript | always |

**Which limit binds first depends on the model** — the plan originally assumed one answer
for all of them (Round 18):

| model | context | tokens `$0.50` buys | 60 % of context | binds first |
|---|---:|---:|---:|---|
| `claude-opus-5` | 1 000 000 | 100 000 | 600 000 | **budget** |
| `claude-sonnet-5` | 1 000 000 | 250 000 | 600 000 | **budget** |
| `claude-haiku-4-5` | 200 000 | 500 000 | 120 000 | **context** |

On the default model at the default budget, compaction never runs — the budget ends the run
first. It is reached by large-budget agents and by cheap models, and Haiku reaches it
soonest, so T-2.6's fixtures are specified per model rather than from the defaults.

**Context growth policy** (`context/window.py`), in order:

1. Under 60 % of the model's context: do nothing.
2. 60–80 %: **context editing** — clear old tool results (`clear_tool_uses`), oldest first,
   keeping the most recent 3 steps intact. Cheap, lossless for recent work, no model call.
3. Over 80 %: **compaction** — server-side summarization of earlier context. Emits
   `context.managed`. The response content is appended back verbatim, including the
   compaction blocks, because dropping them silently loses the compaction state.
4. Editing is always attempted before compaction: editing is free, compaction costs a
   summarization pass.

## 4. Model spend

The default is `claude-opus-5` at `effort="medium"`.

The council rejected a cheaper default in Round 0. Choosing a weaker model than the user
expects is a correctness decision disguised as a cost decision, and it is the library
author making a call that belongs to the application author. A framework that quietly
downgrades produces worse answers that get blamed on the model.

Cost efficiency instead comes from mechanisms the user does not have to think about
(everything in §1–§3) plus two explicit, honest knobs:

- **`effort=`** — `low` for classification and routing steps, `medium` for most work,
  `high`/`xhigh` when correctness dominates. Lower effort produces fewer, more consolidated
  tool calls and less preamble, so it saves more than the thinking tokens alone.
- **Subagents** — the real savings lever, and it requires no routing magic:

```python
reader   = Agent(name="Reader", job="Summarize this page in 5 bullets.",
                 tools=[fetch], model="claude-haiku-4-5", budget="$0.01")

research = Agent(name="Research", job="Answer research questions thoroughly.",
                 tools=[search, reader.as_tool()], model="claude-opus-5")
```

Reading-heavy fan-out runs on the cheap model; only the summaries reach the expensive
model's context. For a 20-page research task this is typically a 5–10× reduction, and the
decision is explicit and legible in the code rather than hidden in a router.

**Automatic model routing is deferred** (ADR-006). No routing policy could be named in
Round 6 that the council agreed was correct today, and an LLM-based router pays a model
call to decide which model to call. Deferred, not designed-around: `ModelProvider` is a
seam, so a router can be added later without touching the loop.

## 5. Performance

| Metric | Target | Approach |
|---|---|---|
| Harness overhead per step | < 15 ms p95 | No reflection in the hot path; schemas built once at import; frozen dataclasses with `slots` |
| `import harness` | < 200 ms | Provider SDK imported lazily on first call, not at import |
| Parallel tool speedup | ~N× for N independent reads | Derived from effect classes; semaphore-bounded |
| Time to first token | provider-bound | Streaming supported end to end via `on_delta` |
| Memory, 100-step run | < 50 MB | Events streamed to the transcript, not accumulated; truncation caps payloads |

The token-counting call on the budget hot path is cached by request hash, so a multi-turn
conversation does not pay a counting round trip per step for a prefix that has not changed.

## 6. Intelligence per unit of cost

Invariant 4 asks for *maximum intelligence per unit of cost and latency*. Round 21 found
this package had one sentence on it, in the README, describing what the harness does not do.
This section is the honest answer.

**What the harness contributes:**

| Mechanism | Effect on quality | Effect on cost |
|---|---|---|
| **Adaptive thinking on by default** | The model decides depth per request instead of a fixed budget | Neutral to positive — no thinking spent on trivial turns |
| **`effort` exposed, `medium` default** | The one real quality/cost dial, in the user's hands | Directly controls token spend and tool-call consolidation |
| **`strict: true` on every tool** | Tool arguments are guaranteed to validate against the schema | Removes an entire round trip per malformed call |
| **`returns=` structured output** | The answer is the type you asked for, validated | Removes the parse-fail-and-re-prompt loop — a doubling of cost that buys no extra thinking |
| **Tool errors returned to the model** | The model can recover instead of the run dying | One turn instead of a restart |
| **Subagent delegation** | Reading-heavy work on a cheap model; synthesis on a strong one | Typically 5–10× on fan-out tasks |
| **Cache-safe prefixes** | None | ~10× on multi-turn, which is budget that buys thinking elsewhere |

```python
from dataclasses import dataclass

@dataclass
class Order:
    id: str
    status: str
    eta_days: int

support = Agent(name="Support", job="Look up orders.", tools=[get_order],
                returns=Order)

order = support.run("Where is A-4471?").value    # an Order, validated — not a string
```

**What the harness deliberately does not do**, and why (ADR-023):

- **No planner, no reflection pass, no self-critique loop.** Each is a second model call for
  an unmeasured quality gain — the opposite of maximum intelligence per unit of cost, and
  precisely the speculative capability that "not over-engineered" forbids. A user who wants
  reflection writes an agent whose `job` says so and gives it a subagent. That is
  expressible today, with no new machinery, and it is visible in their code rather than
  hidden in ours.
- **No automatic model routing** (ADR-006). No policy could be named that the council agreed
  was correct.
- **No prompt rewriting.** Silently editing what the user wrote is the least debuggable
  thing a harness can do.

**The claim, stated plainly:** the harness raises the quality of an answer per dollar by
removing waste and by making the two real dials reachable. **It does not make a weak model
strong, and it does not claim to.**
