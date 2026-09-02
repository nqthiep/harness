# Harness Design — Safety Engine

**Scope:** the policy engine, the `Decision` lifecycle, the audit sink, two-directional
taint enforcement, quarantine, and the mechanism that keeps the model from holding any
switch.

This file **extends** [`00-foundation.md`](./00-foundation.md). The vocabulary, `Effect`,
the two lattices, `Decision`/`Scope`/`Actor` are taken as-is from there — no renaming, no
remodeling.

This is the part that fixes **finding number one of the entire research effort**:

> Across Python, TypeScript, and Java — 30 packages, 3 ecosystems, every major vendor —
> **no package models approval as an auditable decision.** Everywhere, it's a
> *permission state*. ([§10](../research/10-governance-health-languages.md) §28,
> summarizing [§09](../research/09-memory-context-multiagent-hitl.md) §14,
> [§06](../research/06-typescript.md) Finding 2)

---

## 0. A required vocabulary note

`00-foundation.md` §6 assigns the name `Decision` to **the approval record**. The current
implementation in `src/harness/policy/base.py` uses that name for *a policy's result*.
These are two different concepts and must carry two different names:

| concept | name | created by |
|---|---|---|
| a lattice element | `Verdict` | — |
| the policy engine's composed result | **`Ruling`** | the runtime, pure computation |
| the immutable approval record | **`Decision`** | the runtime + a human |

`Ruling` is **not** a synonym for `Decision`: it has no `actor`, doesn't outlive the
call, and never enters the audit log as a grant. This is a deliberate rename in the
current implementation, not a new concept being introduced.

---

## 1. PolicyEngine

### 1.1 Types

```python
from __future__ import annotations

from datetime import datetime, timedelta
from enum import IntEnum
from typing import Any, Literal, Mapping, Protocol, Sequence, runtime_checkable


class Verdict(IntEnum):
    """Composed with max(): a policy can only ever tighten (property P-2)."""
    ALLOW = 0
    ASK = 1
    DENY = 2


@value
class Ruling:
    verdict: Verdict
    reason: str            # always has text; empty only when the verdict is the default ALLOW
    policy: str             # which policy emitted it — traceable back to one line of code
    scope: Scope | None    # a policy may propose a grant scope when the verdict is ASK


@value
class ToolCall:
    id: CallId
    name: ToolName
    arguments: Mapping[str, Any]
    spec: ToolSpec
    __hash__ = None        # holds a Mapping — not hashable, deliberately


@runtime_checkable
class Policy(Protocol):
    name: str

    def check(self, call: ToolCall, ctx: PolicyContext) -> Ruling:
        """Pure, synchronous, no I/O. See POL-4."""
```

`PolicyContext` is a **read-only view** over the run's checkpointed state: `label`, the
`ledger` (a balance, not an object with `spend()`), `run_id`, `step`, `actor_of_run`. It
holds no reference to the `PolicyEngine`, the `DecisionLog`, or the `AuditSink` — see §6.

### 1.2 The engine

```python
EFFECT_FLOOR: Mapping[Effect, Verdict] = {
    Effect.READ:     Verdict.ALLOW,
    Effect.WRITE:    Verdict.ASK,
    Effect.EXTERNAL: Verdict.ALLOW,
    Effect.DANGER:   Verdict.ASK,
}


class PolicyEngine:
    """Sits on the mandatory path (R-1). Not middleware — if it isn't installed, nothing runs."""

    def __init__(self, builtins: Sequence[Policy], user: Sequence[Policy] = ()) -> None:
        self._policies: tuple[Policy, ...] = tuple(builtins) + tuple(user)
        # Builtins come first, and no API removes them — there is no remove_policy().

    def decide(self, call: ToolCall, ctx: PolicyContext) -> Ruling:
        worst = Ruling(EFFECT_FLOOR[call.spec.effect], "effect floor", "core.effect", None)
        for p in self._policies:
            try:
                r = p.check(call, ctx)
            except Exception as exc:                       # fail closed, always
                r = Ruling(Verdict.DENY, f"policy {p.name!r} raised: {exc}", p.name, None)
            if r.verdict > worst.verdict:
                worst = r
            if worst.verdict is Verdict.DENY:
                break                                      # DENY is the top of the lattice
        return worst
```

Four decisions, each fixing a measured shortcoming. Renumbered `POL-1…4` (K-13,
`07-risks-and-open-issues.md` §2, K-13) — the original `P-1`/`P-3`/`P-4` collided with
`design/01`'s plugin invariant (now `PLUG-1`) and `docs/09-testing.md`'s
property-test-ID catalog (P-1..P-10, unchanged — that namespace is the longest-lived,
referenced by 7+ `docs/*.md` files). `P-2` deliberately keeps its old name: it's the
ORIGINAL invariant defined in [`00`](00-foundation.md) §3.1, with no collision (docs/09's
P-2 test-ID describes this exact SAME invariant, not a different one that happens to
share a number).

**POL-1 — a floor derived from `Effect`, not `ALLOW`.** The engine is seeded from
`EFFECT_FLOOR`, so a run **with no policies at all** still asks before `write`/`danger`.
*Shortcoming fixed:* at Microsoft, the gate is per-tool and has to be re-derived
correctly for each tool — `_file_access.py` gets it right, `mode_set` gets it wrong, in
**the same module**
([§09](../research/09-memory-context-multiagent-hitl.md) §14.2). Classify once and
derive the gate, and that contradiction can't happen.

**P-2 — composed with `max()`, adding a policy never loosens.** Proven in §1.3.

**POL-3 — fail closed when a policy raises.** The opposite of Microsoft's
`threading.local()`: an error there **fails open, silently**
([§09](../research/09-memory-context-multiagent-hitl.md) §16bis). A broken policy here
becomes a `DENY` with the policy's name in the `reason`.

**POL-4 — `Policy.check` is a pure, synchronous function.** No `async`, no I/O, no model
calls. Measurable reasons: (a) a pure function is enumerable, so P-2 is provable with a
property-based test instead of a review — and the research already showed 23 *reading*
rounds found **0 security bugs** while 16 *runtime* rounds found **4** (foundation §5,
R-2); (b) an `async` policy could call a model, and a policy that calls a model is the
model influencing its own permissions. The part that needs `await` (asking a human) isn't
a policy — it's in §2.

### 1.3 Proving P-2 with a test, not a review

`Ruling` is data and `check` is a pure function, so the whole `PolicyEngine` is an
enumerable function. Four properties, written with `hypothesis`:

```python
from hypothesis import given, strategies as st

verdicts = st.sampled_from(list(Verdict))
policies = st.builds(ConstPolicy, name=st.text(min_size=1), verdict=verdicts)


@given(base=st.lists(policies, max_size=6), extra=policies, call=tool_calls())
def test_p2_adding_a_policy_never_loosens(base, extra, call):
    before = PolicyEngine((), base).decide(call, CTX).verdict
    after = PolicyEngine((), [*base, extra]).decide(call, CTX).verdict
    assert after >= before


@given(base=st.lists(policies, max_size=6), call=tool_calls(), seed=st.integers())
def test_verdict_is_order_independent(base, call, seed):
    shuffled = random.Random(seed).sample(base, len(base))
    assert (PolicyEngine((), base).decide(call, CTX).verdict
            == PolicyEngine((), shuffled).decide(call, CTX).verdict)
    # `reason` CAN differ: short-circuits at the first DENY. Only the verdict is invariant.


@given(base=st.lists(policies, max_size=6), call=tool_calls())
def test_p1_effect_floor_holds(base, call):
    assert PolicyEngine((), base).decide(call, CTX).verdict >= EFFECT_FLOOR[call.spec.effect]


@given(base=st.lists(policies, max_size=6), call=tool_calls(), idx=st.integers(0, 5))
def test_p3_a_raising_policy_denies(base, call, idx):
    poisoned = [*base]
    poisoned.insert(min(idx, len(poisoned)), RaisingPolicy())
    assert PolicyEngine((), poisoned).decide(call, CTX).verdict is Verdict.DENY
```

This is exactly what foundation §3.1 requires: *"Provable with a property-based test,
not a review."* The first three properties are algebraic invariants of the lattice; the
fourth is an operational invariant. All four run in CI, independent of anyone reading a
diff.

Complemented at the graph layer (belonging to
[`04-runtime-durability.md`](./04-runtime-durability.md)): `unguarded_paths()` proves no
path reaches the `tools` node without going through the `policy` node. **The reason this
mechanism has to exist is an observation, not an assumption**: "middleware shipped but
the harness doesn't install it" already happened at a major vendor
([§08](../research/08-tool-mcp-plugin.md) §24,
[§09](../research/09-memory-context-multiagent-hitl.md) §16bis).

---

## 2. The `Decision` lifecycle

### 2.1 The path, from `ASK` to a record

```
a tool_call the model proposed
   |
   +-(1) PolicyEngine.decide          -> Ruling(ASK, scope=…)
   |
   +-(2) DecisionLog.lookup(scope, now)
   |        +- finds a live grant -> reuse it, do NOT ask again, do NOT write a new Decision
   |        +- finds nothing      -> continue
   |
   +-(3) ApprovalProvider.ask(AskRequest) — the ONLY place a human is ever awaited
   |
   +-(4) the runtime builds a Decision (invariant D-1) — the model never touches this step
   |
   +-(5) AuditSink.commit(decision)  <- DURABLE BEFORE THE TOOL RUNS (§3)
   |
   +-(6) verdict ALLOW -> the tools node ; DENY -> an error tool_result, the run continues
```

Step (2) existing before (3) is a point learned from Microsoft: `ToolApprovalState` is
serializable and session-backed, so a grant survives a resume instead of being *silently
asked again* or *silently re-granted*
([§09](../research/09-memory-context-multiagent-hitl.md) §14).

### 2.2 The type at the human/machine boundary — learned from Vercel

```python
@value
class AskRequest:
    call: ToolCall
    ruling: Ruling
    reason: str                  # REQUEST direction: why it's asking, shown to the approver
    proposed_scope: Scope        # what the policy proposes; the approver may narrow it, never widen it
    max_grant: timedelta         # this run's TTL ceiling — see §2.5


@value
class AskOutcome:
    verdict: Literal[Verdict.ALLOW, Verdict.DENY]   # ASK is never an outcome
    actor: Actor                                     # the provider must name a person
    reason: str | None                               # RESPONSE direction
    scope: Scope                                     # subset of proposed_scope, checked at (4)
    grant_for: timedelta | None                      # None = this call only


class ApprovalProvider(Protocol):
    async def ask(self, req: AskRequest) -> AskOutcome: ...
```

**Whose strength this borrows:** Vercel AI SDK's `ToolApprovalStatus` is the best
approval *shape* found across all three ecosystems — a four-state union, with an
`approvalId`, and `reason` flowing **both ways**: on the request to show the approver, on
the response to record it ([§06](../research/06-typescript.md) Finding 2). The two
`reason` fields above are exactly that detail.

**The shortcoming being fixed:** Vercel stops at *status*. `AskOutcome` is not the final
outcome — it is the **input** the runtime uses to build a `Decision`. And Vercel's
`not-applicable` is represented here by **the absence of a `Decision`**: if nothing was
asked, there is no approval record, only a `policy.allowed` event at `Effect`'s own audit
level. The distinction between "didn't need to ask" and "asked and was approved" is kept,
but by the type system, not by an enum branch.

### 2.3 Who fills which field — invariant D-1

```python
def _record(req: AskRequest, out: AskOutcome, *, clock: Clock, run_id: RunId) -> Decision:
    if not _scope_narrower_or_equal(out.scope, req.proposed_scope):
        raise PolicyViolation("approver widened the scope")     # not even an approver can widen
    ttl = _cap(out.grant_for, req.max_grant)
    return Decision(
        id=DecisionId(ulid_from(clock.now())),   # runtime
        verdict=out.verdict,                     # a human
        scope=out.scope,                         # policy proposes, human narrows
        actor=out.actor,                         # the provider — no Model variant exists
        decided_at=clock.now(),                  # runtime, NOT the model
        expires_at=None if ttl is None else clock.now() + ttl,   # runtime
        reason=out.reason,                       # a human
        run_id=run_id,                           # runtime
    )
```

| field | who fills it | can the model touch it |
|---|---|---|
| `id`, `decided_at`, `run_id` | the runtime (an injected `Clock`) | no |
| `verdict`, `reason` | a human / `Operator` | no |
| `scope` | proposed by the policy, the approver may only **narrow** it | no |
| `actor` | the `ApprovalProvider` | no — `Actor` has no `Model` variant |
| `expires_at` | the runtime, computed from a capped `grant_for` | no |

`_record` is a module-private function, not exported from `harness.__init__`, and no
tool can call it — see §6.

**The shortcoming being fixed (very specifically):** agno's `decision_log` is the
thickest "audit" signal across 23 Python packages, and **every one of its fields is
written by the model**: `decision`, `reasoning`, `decision_type`, `context`,
`alternatives`, `confidence` — "there is no field the runtime fills in and the model
cannot" ([§09](../research/09-memory-context-multiagent-hitl.md) §14.1). The table above
is the direct negation of that sentence.

### 2.4 Append-only — invariant D-2

```python
class DecisionLog(Protocol):
    def append(self, decision: Decision) -> None: ...
    def lookup(self, call: ToolCall, *, run_id: RunId, now: datetime) -> Verdict | None: ...
    def since(self, run_id: RunId) -> Sequence[Decision]: ...
```

No `update`, no `delete`, no `revoke`. **Revocation = `append`ing a new `Decision` with
`verdict=DENY`, on the same `Scope`.** That works because the lookup composes using the
very same lattice:

```python
def lookup(self, call, *, run_id, now):
    hits = [d for d in self.since(run_id)
            if scope_matches(d.scope, call) and (d.expires_at is None or d.expires_at > now)]
    return max((d.verdict for d in hits), default=None)   # DENY wins, per max()
```

One `max()` line gives three properties: revocation always wins over a grant, append
order doesn't affect the outcome, and it's the same lattice as §1 — no second priority
rule to misread.

### 2.5 `expires_at`, and why `always_approve` is wrong

openai-agents's `_ApprovalRecord` stores `approved: bool | list[str]`. The *scoped* part
is right, and worth learning from: "yes to **this** call" is different from "yes to this
tool forever," and the type distinguishes them. The wrong part is
`approve_tool(item, always_approve=True)` writing `approved = True`, and **that grant
never expires for the life of the context**; it records no who, no when
([§09](../research/09-memory-context-multiagent-hitl.md) §14.1).

Three mistakes, three fixes:

1. **"Forever" isn't representable.** The foundation's convention is `expires_at: None`
   means *this call only*, never *forever*. Wanting a standing rule means stating an
   explicit TTL. "Forever" **has no value that can represent it** — this is
   type-level Poka-Yoke, not review-level.
2. **The TTL ceiling is set by operations, not by the approval UI.**
   `RunConfig.max_grant_ttl` (default 1 hour, `danger` is forced to `None` = this call
   only). `_cap()` in §2.3 enforces the ceiling. A broken or manipulated approval UI
   still can't issue a 100-year grant.
3. **A grant never outlives its `run_id`.** `lookup` filters by `run_id`. A grant that
   spans runs is an `Operator` policy — a different `Actor`, a different path, not one
   Approve click getting reused.

The clock is an injected `Clock`, not `datetime.now()` scattered around — so expiry is
deterministically testable, and no tool has a path to push the clock forward.

### 2.6 Matching a `Scope`

```python
def scope_matches(scope: Scope, call: ToolCall) -> bool:
    if scope.tool != call.name:
        return False
    if scope.server is not None and scope.server != call.spec.server:
        return False
    if scope.call_id is not None and scope.call_id != call.id:
        return False
    if scope.args is not None and scope.args != canonical_args(call.arguments):
        return False
    return True
```

Four axes, in cheapest-to-priciest order:

- **`tool`** — every framework has this. Not enough.
- **`args`** — compared as **the whole mapping being equal**, so `args=None` matches any
  call while `args={}` matches **only** a call with no arguments. This keeps Microsoft's
  ternary exactly as designed, including the documented reasoning: approving
  `delete_file(path="/tmp/x")` does **not** approve `delete_file(path="/etc/passwd")`.
  Every other project in the research approves the *verb* and ignores the *object*
  ([§09](../research/09-memory-context-multiagent-hitl.md) §14.1).
- **`server`** — an MCP server's trust boundary. A grant for one server doesn't carry
  over to a different server with the same tool name. This is the **only
  confused-deputy defense found in the entire research effort**, and it deserves to be
  copied verbatim.
- **`call_id`** — when set, the grant dies right after that call. This is the correct
  part of openai-agents (its `list[str]` of call ids), kept.

`canonical_args` is **syntactic** normalization (sorted keys, canonical JSON, numbers
turned to strings), not **semantic** normalization. Semantic normalization
(`/tmp/../etc/passwd`) belongs to the tool's own validator — exactly where Microsoft does
it right: `.`/`..` are flatly rejected in `_file_access.py:183`, and
`is_link_or_reparse_point` blocks symlinks
([§09](../research/09-memory-context-multiagent-hitl.md) §14.2). The two layers
complement each other; see `## Not Enough Evidence`.

---

## 3. The audit sink

### 3.1 Interface and guarantees

```python
@value
class AuditEvent:
    schema_version: int          # versioned, otherwise the taxonomy can never change
    run_id: RunId
    seq: int                     # monotonic WITHIN one run
    at: datetime
    type: Literal["decision", "policy.denied", "flow.denied", "budget.denied", "tool.called"]
    payload: Mapping[str, Any]


class AuditSink(Protocol):
    def commit(self, event: AuditEvent) -> None:
        """Returns once DURABLE. If it raises, the caller must treat it as not written."""

    def emit(self, event: AuditEvent) -> None:
        """Best-effort, non-blocking. Only for debug/info level events."""
```

Two methods rather than one, because they are two different guarantees, and merging them
is a way to silently lose the stronger one.

| question | answer | why |
|---|---|---|
| **Durable before the tool runs?** | **Yes, for every `Decision`.** `commit()` must return before the runtime moves to the `tools` node. | An approval only has value if it exists *before* its consequence. A record written after the email is already sent answers "what happened" but not "who allowed it." |
| **Ordering?** | Monotonic **per `run_id`** via `seq`; globally, ordered only by the ULID inside `Decision.id` (time). | Strict global ordering requires a synchronization point in a distributed run — that cost doesn't fix any measured shortcoming. KISS. |
| **On loss?** | `commit()` fails => **`DENY`, the tool doesn't run.** Fail-closed, no off switch. | The invariant being protected is *every action requiring approval has a record*. Running the tool when the record couldn't be written breaks exactly that invariant. |
| **If `emit()` fails?** | Swallowed, counted, doesn't block the run. | It only carries `read`-level events; losing a log line isn't a security bug. |

`AuditSink` is a **plugin seam** (OTel, a file, a DB — per `05-ideal-harness` §33), but
*calling `commit` before the tool runs* is code on the mandatory path, not middleware.
Exactly the R-1 boundary: **plugins for policy, the mandatory path for invariants**
([§08](../research/08-tool-mcp-plugin.md) §24).

### 3.2 Why agno's `decision_log` is architecturally wrong

Not because it's poorly built — it does what `agno.learn` needs well. It's wrong because
**it's named and read as an audit log**, while:

1. **The write path is a tool the model calls.** `_build_log_decision_tool` hands the
   model a `log_decision(...)`. The model skips the call => **no trace at all.** An
   audit log the audited party can choose not to write to is not an audit log.
2. **Every field is model-written prose.** No field is filled by the runtime in a way
   the model cannot edit ([§09](../research/09-memory-context-multiagent-hitl.md) §14.1).
3. **`AGENTIC` is the only mode**, enforced in `__post_init__`: setting another mode logs
   a warning and runs AGENTIC anyway.

Here: `AuditSink` has no tool bound to it (§6), `commit` is called by the runtime at one
specific point in the graph, and every identifying field is filled by the runtime. The
audited party holds no pen.

---

## 4. Two-directional taint enforcement

### 4.1 Labels and rules

```python
# Integrity / Confidentiality / Label: the canonical definition is in 00-foundation.md §3.2.
# Used here only, never redefined.
from harness import Integrity, Confidentiality, Label
```

`join` is `max()` per axis — **the same composition operator as `Verdict` in §1**.
Monotonic, never decreasing within a run. One operation to understand, not two.

```python
def check_flow(label: Label, spec: ToolSpec) -> Ruling:
    if (label.integrity is Integrity.UNTRUSTED
            and spec.effect is Effect.DANGER
            and not spec.accepts_tainted):
        return Ruling(Verdict.DENY, "untrusted context may not drive a danger tool",
                      "core.flow.integrity", None)
    if (label.confidentiality is Confidentiality.SECRET
            and spec.max_confidentiality is Confidentiality.PUBLIC):
        return Ruling(Verdict.DENY, "secret context may not reach a public sink",
                      "core.flow.confidentiality", None)
    return Ruling(Verdict.ALLOW, "", "core.flow", None)


def label_after(spec: ToolSpec, current: Label) -> Label:
    return current.join(spec.emits)   # external => Label(UNTRUSTED, …) per foundation §2
```

`read` is untouched by the confidentiality rule because it isn't a sink — this is a
correct observation from Microsoft, copied with credit: a read-only tool is "safe to
call even when the agent context is tainted — it cannot exfiltrate" (`security.py:3033`,
[§09](../research/09-memory-context-multiagent-hitl.md) §16bis).

A tool arriving from MCP with **no declared `effect`** gets the LEAST trusted class, not
the safest one: `Effect.EXTERNAL` + `max_confidentiality=PUBLIC` + `accepts_tainted=False`.
The protocol is not a security boundary
([§05](../research/05-ideal-harness.md) §33).

### 4.2 Where this installs on the path

Exactly **two** points, both nodes of the compiled graph:

```
model ──► policy ──► tools ──► label ──► model
           |  ^                  |
           |  +── check_flow()   +── label_after()   (written into checkpointed state)
           +───── PolicyEngine.decide() v check_flow()  => max()
```

1. **Before running** — inside the `policy` node, `check_flow(label, spec)` composes
   with `PolicyEngine.decide(...)` through the exact same `max()`. No detour: a `DENY`
   from flow is indistinguishable from a `DENY` from policy, and both sit at the top of
   the lattice.
2. **After running** — the `label` node calls `label_after` and writes the new label
   into state **before** the tool's result is merged into `messages`. If label-writing
   happened after the merge, there would be a window where context is already dirty but
   the label is still clean.

**Why this can't be disabled just by not installing a plugin:** both are mandatory nodes
in the graph, and `unguarded_paths()` refuses to compile if there's a path to `tools`
that skips `policy`, or a path from `tools` back to `model` that skips `label` (R-2,
foundation §5). There is no `enable_taint=` flag. No `install_middleware()`. The only way
to remove it is to edit the graph builder in core and break a test that knows how to
count.

### 4.3 Three places this must differ from Microsoft

`agent_framework/security.py` is the most sophisticated safety architecture in the whole
research effort — real two-directional IFC, a monotonic `combine_labels`, MCP annotations
turned into labels. It's also the clearest example of the gap between *what's possible*
and *what's on by default*
([§09](../research/09-memory-context-multiagent-hitl.md) §16bis).

| # | Microsoft | here | evidence |
|---|---|---|---|
| **1. On by default** | an opt-in submodule; `grep`ing imports from `_harness/` returns **nothing**; absent from `agent_framework/__init__.py`; `create_harness_agent` doesn't install the lattice | a mandatory node in the graph; `unguarded_paths()` proves it; no on/off switch | §16bis "the good module is not wired in" |
| **2. No `threading.local()`** | `_current_middleware = threading.local()` set/cleared across an `await`; two concurrent tool calls read each other's slot, or read `None` — **silent, fail-open** | the label lives in the thread's **checkpointed state**, attached **per message** (`msg.label_integrity`, `msg.label_confidentiality` — strings, JSON-checkpointable); the effective label is a `join` recomputed per L-3 ([00 §3.2](00-foundation.md)), not an accumulating variable | §16bis, and Rounds 34/37/41 of this very repo |
| **3. No process-wide singleton** | `_global_variable_store` and `_quarantine_chat_client` at module scope, changed through a `global` setter; in multi-tenant, one tenant's change becomes every tenant's | every safety-relevant state is keyed by `run_id` in state; no module variable is writable after import | §16bis |

On (2), an important point that's easy to get wrong:
`contextvars.ContextVar` — the correct primitive §16bis suggests — **still isn't enough
here**, because every LangGraph node runs in a copied context, so a `ContextVar` doesn't
cross a node either. This isn't speculation: this repo made exactly that mistake in
Round 34, **fixed it wrong** in Round 37, and only got it right in Round 41 once the
state moved into checkpointed state (foundation §5, R-4). `threading.local()` is
strictly weaker than a primitive we've already proven insufficient ourselves.

---

## 5. Quarantine model — CUT, moved to the risk register

This section used to specify a cheaper secondary model for reasoning over `UNTRUSTED`
content (the dual-LLM/CaMeL pattern), along with `Quarantined[T]` and a separate
fail-closed branch.

**Cut per this design's own editorial rule** ([00 §8.4](00-foundation.md)): this pattern
has exactly **one** implementation in the entire research effort, and that
implementation is `@experimental`, not wired into its own harness, and not
concurrency-safe. **No evaluation** compares the prompt-injection success rate with and
without quarantine ([review-kiss.md](review-kiss.md) K-1).

The ordinary path is already sufficient and already has evidence:
`UNTRUSTED` + `danger` => `ASK` (a human approves) or `DENY`. The full specification is
kept in [`07-risks-and-open-issues.md`](07-risks-and-open-issues.md) under *"an idea with
real architecture, waiting on evaluation"* — cut from the mandatory path, not thrown
away.


## 6. The model holds no switch (R-3)

### 6.1 The specific list

| the model can **never** do | the mechanism guaranteeing it (not a prompt) |
|---|---|
| Write or edit a `Decision` | `Actor` has no `Model` variant — a model-generated record **cannot be constructed** at the type level. `_record()` is module-private, never exported. |
| Change a `Verdict`, or add/remove a `Policy` | `PolicyEngine._policies` is a `tuple` fixed when the `Runtime` is built; there's no `add_policy`/`remove_policy`. The `PolicyContext` a tool receives holds no reference to the engine. |
| Change the safety mode (à la `mode_set`) | The safety mode isn't a tool. It's a field of the immutable `RunConfig`, fixed before the first turn. No function changes it once a `Run` has started. |
| Widen `expires_at` or extend a grant | `expires_at` is computed by the runtime from an injected `Clock`, capped by `RunConfig.max_grant_ttl`. |
| Write to the `AuditSink` | No tool is bound to the sink. The sink is only ever called from a graph node. |
| Lower a taint label | `Label.join` only has `max()`; no function to lower a label exists. |
| Change or raise the budget | The `Ledger` lives in checkpointed state; `PolicyContext` only exposes a **numeric balance**, never an object with `spend()`. |
| Call a tool outside the registry | Tool names are looked up in a closed registry; an unknown name is a hard error, not a dynamic lookup. |

### 6.2 Why this is a mechanism, not a promise

Four layers, strongest to weakest:

1. **Not representable.** `Actor` has no `Model`; "grant forever" has no representable
   value. What can't be constructed doesn't need guarding.
2. **Not reachable.** The control plane isn't part of the `ToolContext` a tool receives.
   No reference, no call.
3. **Not declared.** The schema list sent to the model **exactly equals** the registry's
   allowlist. The control plane's functions aren't tools, so there's no schema for the
   model to even name.
4. **Proven by test.**

```python
def test_no_agent_visible_tool_reaches_the_control_plane():
    forbidden = {"harness.policy.engine", "harness.audit", "harness.decision",
                 "harness.budget.ledger"}
    for spec in registry.agent_visible():
        assert not (_module_closure(spec.fn) & forbidden), spec.name


def test_schema_list_equals_the_allowlist():
    assert {t["name"] for t in registry.schemas_for_model()} == set(registry.agent_visible_names())


def test_actor_has_no_model_variant():
    assert "Model" not in {t.__name__ for t in typing.get_args(Actor)}
```

### 6.3 The shortcoming being fixed, named directly

Microsoft's `_mode.py:289`:

```python
@tool(name="mode_set", approval_mode="never_require")
def mode_set(mode: str) -> str:
    """Switch the agent's operating mode."""
```

`plan` is a read-only, ask-first mode — i.e. a **safety posture**. The model holds the
switch, and `approval_mode="never_require"` makes that switch **structurally
un-gateable**. The only thing standing between the model and leaving plan mode is one
English sentence in the system prompt: *"Only use mode_set if the user explicitly
instructs/allows you to change modes."*
([§09](../research/09-memory-context-multiagent-hitl.md) §14.2)

This is the textbook definition of **safety by prompt**, which the research ranks as an
observed anti-pattern across the industry
([§04](../research/04-weaknesses-antipatterns.md) §28).

And the lesson isn't "Microsoft was careless" — that same module also has
`_file_access.py`, which turns on approval by default and separates read/write, i.e. the
correct design. The lesson is: **per-tool enforcement has to be re-derived correctly for
every tool, and the twentieth tool is where it slips.** Here, the safety posture isn't a
tool, so there is no twentieth tool for it to slip on.

---

## 7. Cross-reference: measured shortcoming <-> mechanism

| observed shortcoming | where | mechanism in this file |
|---|---|---|
| Approval is a boolean, no actor/timestamp/expiry | 30 packages, 3 ecosystems ([§10](../research/10-governance-health-languages.md) §28) | an immutable `Decision`, D-1 assigns who writes which field (§2.3) |
| `always_approve` never expires | openai-agents ([§09](../research/09-memory-context-multiagent-hitl.md) §14.1) | "forever" isn't representable + `max_grant_ttl` (§2.5) |
| An audit log written by the audited party, via a tool | agno's `decision_log` (§14.1) | the sink has no tool bound to it; the runtime fills identifying fields (§3.2) |
| Approval locks onto the *verb*, ignoring the *object* | everyone except Microsoft (§14.1) | `Scope.args`, matched as a whole-mapping equality (§2.6) |
| A grant transferring wrongly between two MCP servers sharing a tool name | only Microsoft defends against this (§14.1) | `Scope.server` (§2.6) |
| A good safety module that's **never wired in** | Microsoft's `security.py` (§16bis) | a mandatory node + `unguarded_paths()` (§4.2) |
| `threading.local()` across an `await` => silent fail-open | Microsoft (§16bis) | the label lives in checkpointed state (§4.3) |
| A module-level singleton in a multi-tenant server | Microsoft (§16bis) | quarantine + the label belongs to the `Run` (§5.2) |
| The model holds the safety-mode switch | Microsoft's `mode_set` (§14.2) | R-3: not representable / not reachable / not declared / tested (§6) |
| `ToolConfirmation` is a single `boolean` | google-adk-java ([§10](../research/10-governance-health-languages.md) §28) | same fix as row 1 — the same solution across all three languages |
| Approval mistaken for isolation | Goose ([§03](../research/03-safety-reliability.md) §17) | this file makes **no** isolation claim; the sandbox is a separate seam ([§05](../research/05-ideal-harness.md) §33) |

Whose strength is borrowed, summarized: **Microsoft** for `Scope` (args + server_label +
serializing across a resume) and for two-directional IFC; **Vercel AI SDK** for the
four-state approval shape and two-directional `reason`; **openai-agents** for a grant
keyed to `call_id`; **google-adk-java** for a `confirmed(false)` default — fail-closed at
construction; **LangGraph** for an id that locates a pause point, used in
[`04-runtime-durability.md`](./04-runtime-durability.md).

---

## Not Enough Evidence

- **Parameter normalization for `Scope.args`.** Matching by a canonical string blocks
  `delete_file(path="/etc/passwd")` after approving `/tmp/x`, but does **not** block two
  different strings pointing at the same resource (`/tmp/../etc/passwd`, a symlink, a
  hostname with Unicode homoglyphs). The current design pushes that onto each tool's own
  validator — the same place Microsoft gets it right — but the research **doesn't
  measure** how much of this failure class remains in practice. That needs a real
  runtime round to find out.
- **The cost of a synchronous `commit()`.** The "durable before the tool runs"
  requirement adds one storage round-trip to every tool needing approval. The research
  has no latency measurement for any package's audit sink (`langgraph` OTel at 0.1; MS
  AF at 7.7 is *code density*, not performance —
  [§03](../research/03-safety-reliability.md) §18). What threshold would be
  unacceptable is unknown.
- **What a real `ApprovalProvider` actually looks like.** Vercel is the only evidence
  that anyone has actually built an approval UI (its two-directional `reason` is a trace
  of that — [§06](../research/06-typescript.md)). There's no data on how humans use an
  approval queue at scale: reflex-approval rates, wait times, behavior on expiry. The
  `max_grant_ttl` default of 1 hour is an **educated guess, not a measurement**.
- **Whether quarantine actually reduces risk.** The dual-LLM/CaMeL pattern has exactly
  **one** implementation in the entire research effort, and it's `@experimental`, not
  wired in, and not concurrency-safe (§16bis). No evaluation compares the
  prompt-injection success rate with and without quarantine. §5's design **fixes the
  placement of an unproven idea** — so it's fail-closed and optional, not a default.
- **Multi-tenancy.** The research states plainly "Not Enough Evidence for most of the
  library set" ([§03](../research/03-safety-reliability.md) §16). §4.3 and §5.2 key
  state by `run_id`, enough to avoid the specific bug observed at Microsoft, but that is
  **not** a complete tenancy model (quotas, storage isolation, identity boundaries).
  That's the service layer's job.
- **`Confidentiality` has only two tiers.** The foundation uses `PUBLIC < SECRET`. No
  Python package in the research has a dedicated `Secret` type guarding against leaking
  through logs/prompts ([§03](../research/03-safety-reliability.md) §16), so there is
  **no evidence** whether two tiers are enough or too few. Add a third tier only when a
  measured failure class shows up that two tiers can't express.
- **Forcing the `danger` TTL ceiling to `None`** (this call only) is a cautious
  decision, not a conclusion drawn from data. No source in the research measures how
  often re-approval fatigue makes an operator stop reading.
