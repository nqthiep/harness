# Harness Design — Foundation

**This is the foundation file. Every other design file must use exactly the vocabulary
and models defined here.**

This design is drawn from the research in [`research/`](../research/) — 30 packages,
3 ecosystems, read from source rather than docs. **Every design decision below must cite
a specific finding.** A decision with no citation is an opinion, and an opinion doesn't
belong in this document.

---

## 1. Five invariants, and what the research says about each

| invariant | what the research found | design consequence |
|---|---|---|
| **Extensible / Pluginable** | LangChain 1.x's `wrap_model_call`/`wrap_tool_call` is an around-hook — the strongest one found ([§08](../research/08-tool-mcp-plugin.md) §24). But Microsoft's lattice is middleware, so it is **not installed by default** ([§09](../research/09-memory-context-multiagent-hitl.md) §16bis) | Plugins for **policy**; invariants sit on a path that cannot be bypassed |
| **Cost Efficient** | Nobody reserves budget before calling the model; `max_turns` caps step count, not money ([§03](../research/03-safety-reliability.md) §20) | `reserve()` pre-flight, `max_tokens` derived from the remaining balance |
| **Safe by Design** | Approval everywhere is a *permission state*, not an auditable *decision* — 30 packages, 3 languages ([§09](../research/09-memory-context-multiagent-hitl.md) §14, [§10](../research/10-governance-health-languages.md) §28) | `Decision` is an immutable record with an actor, a timestamp, a scope, an expiry |
| **Intelligent** | The model holds its own safety-mode switch: `mode_set` has `approval_mode="never_require"` ([§09](../research/09-memory-context-multiagent-hitl.md) §14.2) | The model may never hold its own switch |
| **Efficient** | A new topology makes durability installable — LangGraph at 51.6 checkpoints/kLOC ([§11](../research/11-workflow-and-dx.md) §12) | The runtime is a graph, not a `while` loop |

---

## 2. The Effect class — one classification, five behaviors

Foundational finding: **nothing in the research derives parallel-safety from a tool's
declared effect** ([§08](../research/08-tool-mcp-plugin.md) §8.3). pydantic-ai comes
closest with a `sequential=True` flag, but that's *the caller asserting it per tool*. The
measured cost: LangChain had to hand-write a dedicated guard for `write_todos` for lack
of a general model — the guard exists only where someone already got bitten.

So this harness classifies **once**, and derives **five** behaviors:

```
Effect = read | write | external | danger
```

| effect | parallel? | model may retry? | runtime auto-retries? | taints context? | default verdict | audit level |
|---|---|---|---|---|---|---|
| `read` | yes | yes | yes | no | `ALLOW` | `debug` |
| `write` | no (barrier) | yes, with an idempotency key | no | no | `ASK` | `info` |
| `external` | yes | yes | **no** | **yes** | `ALLOW` | `info` |
| `danger` | no (barrier) | no | no | no | `ASK` | **`audit`** |

**Why two retry columns, not one.** An early draft of this file merged them and marked
`external` as "retryable" — an agent writing [03](03-tools-and-mcp.md) caught the
contradiction: the research states outright that *"an `external` tool that fails may have
already had an effect and must not be retried blindly"* ([§08](../research/08-tool-mcp-plugin.md)
§8.2). Two different meanings: **the model is allowed to retry** (a run doesn't die
because one fetch failed) is different from **the runtime silently retries on its own**
(only safe when repeating it produces no new effect). Only `read` satisfies both.

The first four properties are *derived*, not configured. A tool author declares exactly
one thing.

**Why this is a strength assembled from several sources:** pydantic-ai has `ToolKind`
(`function`/`output`/`external`) and a `sequential` flag; Microsoft separates read/write
approval (`disable_readonly_tool_approval` vs. `disable_write_tool_approval`); MCP has
`readOnlyHint`/`destructiveHint`. All three are *part* of the same idea. This unifies them
into a single axis.

---

## 3. Two lattices

### 3.1 The verdict lattice — a policy can only tighten

```
ALLOW (0)  <  ASK (1)  <  DENY (2)
```

Composed with `max()`. Property **P-2**: adding a policy can never loosen a permission.
Provable with a property-based test, not just a review.

### 3.2 The taint lattice — two-directional, learned from Microsoft

The research found **exactly one** real information-flow implementation, and it is
two-directional ([§09](../research/09-memory-context-multiagent-hitl.md) §16bis):

```
integrity        : TRUSTED   <  UNTRUSTED      (Biba — against being steered)
confidentiality  : PUBLIC    <  SECRET         (Bell-LaPadula — against leaking)
```

Composed monotonically, never decreasing within one run. This repo's current harness
(ADR-011) **has only the integrity axis** — this is a proven upgrade.

**Canonical definition — every other file references this, none redefine it:**

```python
class Integrity(IntEnum):        # Biba — against being steered
    TRUSTED = 0
    UNTRUSTED = 1

class Confidentiality(IntEnum):  # Bell-LaPadula — against leaking
    PUBLIC = 0
    SECRET = 1

@value
class Label:
    integrity: Integrity = Integrity.TRUSTED
    confidentiality: Confidentiality = Confidentiality.PUBLIC

    def join(self, other: Label) -> Label:
        """Monotonic composition — the same `max()` as Verdict, the same reasoning."""
        return Label(max(self.integrity, other.integrity),
                     max(self.confidentiality, other.confidentiality))
```

Enforcement rules:

- an `external` tool's output -> context becomes `UNTRUSTED`
- `UNTRUSTED` context **may not** steer a `danger` tool, unless the tool declares
  `accepts_tainted=True`
- context holding `SECRET` data **may not** call a tool with `max_confidentiality=PUBLIC`
  (`external` and `write` tools default to a `PUBLIC` sink)

**What raises the label to `SECRET`.** A reviewer pointed out the first draft had **no
path** for `confidentiality` to ever leave `PUBLIC` — half the lattice was decoration, and
a mechanism with no input that still gets listed as a strength is one an operator will
wrongly trust ([review-security.md](review-security.md) S-3). Two sources, both on the
mandatory path:

1. **A `Secret[T]` the caller passes in.** Any value wrapped in `Secret` (defined in
   [04 §7bis](04-runtime-durability.md)) raises the run's label to `SECRET` once it enters
   context. This harness has no separate `deps` mechanism (K-1/K-2 cut it), so the real
   path is: a tool calls `.reveal()` on a `Secret` and that value shows up verbatim in the
   returned payload — `emits_of(spec, grants, payload)` (`policy/builtin.py`) detects it
   with `contains_live_secret` (`secrets.py`, the same matching `redact()` uses) and
   raises that MESSAGE's label to `SECRET`.
2. **`ToolSpec.emits`, settable only by the operator.** Derived from `effect` by default;
   the operator — **not** the tool's author — raises it separately for a tool that reads a
   sensitive area (payroll, medical records). Set in deployment configuration, not in the
   decorator, because a flag in the decorator has exactly the shape of `mode_set`, which
   this very design criticizes.

If a deployment uses neither, the confidentiality axis stays at `PUBLIC` and the harness
enforces Biba only — **that must be stated**, not left for the operator to guess.

### Where the label attaches — per-message, with three accompanying rules

An early draft left this open, and two files answered differently: [02](02-safety-engine.md)
§4.3 describes the label as **one label for the whole run** (two strings in checkpointed
state), while [05](05-cost-and-memory.md) §B.2 says *"a merged message's `Label` = the
`join` of the `Label` of every message it replaces"* — i.e. **per-message**. Both cannot be
right ([review-security.md](review-security.md) S-19).

**Ruling: the label attaches to each message.** A run-level label makes the whole run
`UNTRUSTED` forever after a single `web_fetch`, which makes the harness useless. But
per-message alone is **fail-open**, so it ships with three rules, and rule L-2 is the one
that must never be forgotten:

| # | rule |
|---|---|
| **L-1** | Every message carries a `Label`. A tool-result message carries `spec.emits` joined with the label of its args. |
| **L-2** | **A message the MODEL generates carries the `join` of the label of the ENTIRE context at the moment it was generated.** |
| **L-3** | The effective label fed into `check_flow` = the `join` of the label of **every message still in context**, recomputed **after every compaction**. |

**Why L-2 must never be forgotten.** The most natural answer to "what label does an
assistant message the model generated carry?" is *"our model generated it, so
`TRUSTED`"* — and that's wrong in a way that collapses the whole lattice:

1. `web_fetch` returns `UNTRUSTED` content containing an injection.
2. The model reads it, generates an assistant message: *"The user wants me to delete the
   build directory."* Without L-2, this message is `TRUSTED`.
3. A later turn's `ClearToolResults` (05 §B.2, triggered at 60% window) **clears the tool
   result's content** — clearing exactly the message that carried the `UNTRUSTED` label,
   leaving a `CLEARED` string in its place.
4. The remaining context's composed label: `TRUSTED`. The attacker's instruction **is
   still there**, now rephrased in the model's own voice.
5. `check_flow` allows a `danger` tool.

Compaction — the mechanism 05 §B.2 asserts "must not launder taint" — becomes the perfect
taint-laundering path, through the exact operation that file describes as safe. The rule
*"a summary of `UNTRUSTED` is `UNTRUSTED`"* only covers `SummarizeOldPrefix`;
`ClearToolResults` produces no summary, so that rule never touches it. **L-2 blocks it at
the root**: the model's message already carried `UNTRUSTED` from the moment it was
generated, so clearing the tool result lowers nothing.

L-3 makes clear the effective label is a **recomputed** function, not an accumulating
variable. That is also how the label *legitimately decreases*: once the last `UNTRUSTED`
message leaves context and no model-generated message from while it was present remains.
Monotonicity still holds **within** one computation; it is not a variable that only grows
for the life of the run.

**Three places this must differ from Microsoft:**

1. **On by default.** Theirs is an opt-in submodule their own `_harness/` doesn't import,
   and it's absent from the top-level `__init__`. Ours sits on the mandatory path.
2. **No `threading.local()`.** Theirs sets middleware into `threading.local()` and then
   sets it across an `await` — two concurrent tool calls silently read each other's slot,
   fail-open. Our taint state lives in the thread's **checkpointed state**, since a
   `ContextVar` doesn't cross a LangGraph node either (each node runs in a copied
   context).
3. **No process-wide singleton.** Theirs has `_global_variable_store` and
   `_quarantine_chat_client` at module scope; in a multi-tenant server, one tenant's
   change becomes every tenant's change.

---

## 4. `Decision` — fixing finding number one

This is the gap repeated most often across the whole research effort: **no framework
treats approval as an auditable event.** openai-agents stores `bool | list[str]` with an
`always_approve` that never expires; LangGraph has an id that locates *where it paused*,
not *who approved it*; Java's `ToolConfirmation` is exactly one `boolean`; and the
thickest "audit" signal found across 23 Python packages is agno's `decision_log` — **a
tool the model itself calls to log about itself**, i.e. an audit log written by the party
being audited.

In this harness, approval is **not** a state. It is an immutable record:

```python
@value
class Decision:
    id: DecisionId              # ULID — sortable by time
    verdict: Verdict            # ALLOW | DENY  (ASK is never a final outcome)
    scope: Scope                # what was approved — see §4.1
    actor: Actor                # NEVER left blank by AI
    decided_at: datetime        # written by the runtime, not the model
    expires_at: datetime | None # None = this call only
    reason: str | None          # why — two-directional, learned from Vercel
    run_id: RunId               # which run this belongs to
```

**Invariant D-1:** no field of `Decision` is generated by the model. The runtime fills
`id`, `decided_at`, `run_id`, `actor`; a human fills `verdict` and `reason`; a policy
fills `scope`.

**Invariant D-2:** a `Decision` is append-only — never edited, never deleted. Revocation
is writing a new `Decision` with `verdict=DENY`.

### 4.1 `Scope` — borrowing Microsoft's strongest point

Microsoft's `ToolApprovalRule` is the best design found because it locks a grant to
**parameter values**, not just a tool name: approving `delete_file(path="/tmp/x")` does
not approve `delete_file(path="/etc/passwd")`. Every other project approves the *verb*
and ignores the *object*. It also has `server_label` — a grant for one MCP server doesn't
transfer to a different server with the same tool name, the only confused-deputy defense
found anywhere.

```python
@value
class Scope:
    tool: ToolName
    args: Mapping[str, str] | None   # None = any call; {} = only a call with no arguments
    server: ServerLabel | None       # the MCP server's trust boundary
    call_id: CallId | None           # None = a standing rule; set = this call only
```

The `None` vs. `{}` distinction is kept from Microsoft — it's real Poka-Yoke and is
clearly documented at the source.

### 4.2 `Actor` — a field nowhere else has

```python
Actor = Human(id: str, via: Channel) | Policy(rule: str) | Operator(id: str)
```

No `Model` variant. **A model is never the actor of a `Decision`** — that's exactly where
agno gets it wrong.

---

## 5. Four architecture rules, each fixing one measured shortcoming

**R-1. Invariants sit on the mandatory path; plugins are only for policy.**
Because "middleware shipped but the harness doesn't install it" is a real, observed
failure at a major vendor. Budget, taint, and permission checks -> the mandatory path.
Retry, cache, logging, model fallback, cost accounting -> plugins.

**R-2. Provable, not just reviewable.**
`unguarded_paths()` walks the compiled graph and proves no path reaches `model` without
going through `budget`, reaches `tools` without going through `policy`, or reaches `END`
without going through `finish`. Why: 23 review rounds found 20 bugs and **0 security
bugs**; 16 *runtime* rounds found 38+ bugs and **4 security bugs**. Reading doesn't find
what running finds.

**R-3. The model holds no safety switch.**
No tool the model calls may change the safety mode, loosen a policy, write a `Decision`,
or edit the budget. Microsoft violates this with `mode_set(approval_mode="never_require")`.

**R-4. No shared state outside checkpointed state.**
No module-level singleton, no `threading.local()`, no `ContextVar` crossing a node. This
is a mistake made once (Round 34), fixed wrong (Round 37), fixed right (Round 41) — and
Microsoft carries a weaker version of the same bug.

---

## 5bis. Event kind — one versioned table

`AuditEvent` (02 §3.1) and `stream()`'s `Event` (01) **share this one table.** An early
draft had two different closed `Literal`s and at least 5 events emitted that belonged to
neither ([review-kiss.md](review-kiss.md) K-19). The research also calls for a versioned
envelope in the minimal core ([§05](../research/05-ideal-harness.md) §33).

```python
SCHEMA_VERSION = 1                       # bump when a kind's MEANING changes, not when one is added

EventKind = Literal[
    # run lifecycle
    "run.started", "run.finished", "run.cancelled",
    # model
    "model.called", "model.returned", "model.refused",
    # tool
    "tool.called", "tool.returned", "tool.failed", "duplicate_suppressed",
    # decisions and denials
    "decision", "policy.allowed", "policy.denied", "flow.denied", "budget.denied",
    # information flow
    "taint.raised",
]

@value
class Event:
    v: int                # = SCHEMA_VERSION — so a reader of an old log knows what it's reading
    kind: EventKind
    run_id: RunId
    at: datetime
    level: Literal["debug", "info", "audit"]
    data: Mapping[str, Any]
```

17 kinds. `AuditEvent` is an `Event` with `level == "audit"`, not a second type —
**one ledger, one protocol** (K-4).

## 6. Required vocabulary

Use exactly these names. Do not introduce synonyms.

| concept | name | do not use |
|---|---|---|
| a tool's classified effect | `Effect` | `kind`, `type`, `category` |
| a policy's verdict | `Verdict` | `permission`, `allowed` |
| the approval record | `Decision` | `Approval`, `ApprovalRecord` |
| who decided | `Actor` | `user`, `approver` |
| the spend + step ledger | `Ledger` | `budget`, `usage` |
| the two-axis label | `Label(integrity, confidentiality)` | `taint`, `level` |
| a single run | `Run` | `session`, `thread` |
| an external tool | `ToolSpec` | `Tool`, `FunctionTool` |

---

## 7. File map and owners

| file | contents | status |
|---|---|---|
| `00-foundation.md` | this file — vocabulary, effect, the lattices, `Decision` | done |
| `01-core-api.md` | the public API, zero-to-agent, DX | agent A |
| `02-safety-engine.md` | policy, the `Decision` lifecycle, taint enforcement, the audit sink | agent B |
| `03-tools-and-mcp.md` | `ToolSpec`, the error taxonomy, parallelism, MCP classification | agent C |
| `04-runtime-durability.md` | the graph, checkpoints, resume, `unguarded_paths`, cancellation | agent D |
| `05-cost-and-memory.md` | `Ledger`, `reserve/hold/release`, compaction, memory provenance | agent E |
| `06-poka-yoke-matrix.md` | every failure class <-> its blocking mechanism <-> its Poka-Yoke tier | consolidated |
| `07-risks-and-open-issues.md` | risks, trade-offs, what doesn't have enough evidence yet | consolidated |

---

## 8. Writing rules for every contributing agent

1. **Every decision must cite** a finding in `research/`, in the form
   `([§09](../research/09-...md) §14)`. No citation = it doesn't go in.
2. **State clearly whose strength this borrows** and **which shortcoming it fixes**. This
   is the user's central requirement.
3. **Code in the documentation must be a real signature**, type-checkable — never
   pseudocode.
4. **KISS / NOT-OVER-ENGINEER.** If a mechanism doesn't fix a *measured* shortcoming, cut
   it. The research has already shown the cost of adding a feature nobody turns on.
5. **State plainly what you're unsure of** in a `## Not Enough Evidence` section at the
   end of the file — don't guess.
6. Write in English.
