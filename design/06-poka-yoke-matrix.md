# Poka-Yoke Matrix

Every row: **a real failure class** -> **evidence it's real** -> **the blocking
mechanism** -> **its Poka-Yoke tier** -> **how it's checked**.

Three tiers, strictly defined:

| tier | meaning | everyday example |
|---|---|---|
| **1 — Warning** | documentation says don't; nothing stops it | a "mind the step" sign |
| **2 — Detect** | the mistake is possible, but the system reports it immediately | a washing machine beeping when the door isn't shut |
| **3 — Block** | the mistake **cannot be represented** | a three-prong plug that can't be inserted backward |

This design's rule: **an invariant only counts as fixed once it reaches tier 3, or tier
2 with a written reason tier 3 is infeasible.** The reason has evidence: 23 review
rounds found **0** security bugs; 16 *runtime* rounds found **4**
([§00](../research/00-executive-summary.md)). Tier 1 is a review wearing a mechanism's
clothes.

---

## A. Seven industry-wide shortcomings

| # | failure class | evidence | mechanism | tier | checked by |
|---|---|---|---|---|---|
| 1 | Approval is a permission state, not an auditable decision | 30 packages, 3 languages, no exception ([§09](../research/09-memory-context-multiagent-hitl.md) §14) | An append-only `Decision`; `Actor` has **no** `Model` variant; `decided_at`/`run_id` filled by the runtime | **3** | the model cannot construct a `Decision` — no constructor accepts an `Actor` from a tool |
| 1b | A "permanent" grant (openai-agents's `always_approve`) | ([§09](../research/09-memory-context-multiagent-hitl.md) §14) | "Permanent" **isn't representable**: `expires_at=None` means *this call only*; plus a `max_grant_ttl` ceiling | **3** | no value of `expires_at` means forever |
| 2 | A safety mechanism exists but isn't turned on | `agent_framework.security` never imported by `_harness/` ([§09](../research/09-memory-context-multiagent-hitl.md) §16bis) | R-1: budget/taint/permission on the mandatory path; plugins only for policy. PLUG-1 (K-13, renamed from the original `P-3`): the side-effect set with a plugin installed is a subset of without | **3** | a PLUG-1 property test |
| 3 | Shared state leaks between concurrent runs | `threading.local()` set across an `await`, fail-open ([§09](../research/09-memory-context-multiagent-hitl.md) §16bis) | R-4: checkpointed state is the **only** memory; no singleton, no `ContextVar` (it doesn't cross a LangGraph node) | **2** | a test with two interleaved runs; tier 3 would need a linear type Python doesn't have |
| 4 | Caps step count, not money | no project `reserve()`s in advance ([§03](../research/03-safety-reliability.md) §20) | `Budget.usd` **required, no default, never `None`**; I-2: no model call runs without an open `Reservation` | **3** at construction, **2** at runtime | `Budget()` is a `TypeError`; the wrapper raises |
| 5 | No idempotency at the tool-call level | nobody has it; agno only at run-submission level ([§07](../research/07-remaining-python.md)) | the effect log is **always on** for `write`/`danger`; no "off" tier | **3** | no enum value turns it off |
| 6 | A memory write records no provenance | no system records it ([§09](../research/09-memory-context-multiagent-hitl.md) §10.2) | The original design here (a `Provenance` dataclass, a required keyword with no default on `Store.put`, M-2/M-3's per-memo audit trail) never shipped — `grep -rn provenance src/harness/` is zero hits (H-8, `design/review-architect-round3.md`). What shipped instead, narrower: M-1 — a retrieval `Store`'s own tool binding is classified `effect="external"` ([docs/04-interfaces.md §5.1](../docs/04-interfaces.md)), which taints the run unconditionally via `EFFECT_PROFILES[EXTERNAL].emits`, regardless of what the store itself claims about a record. `VikingStore.tools()`'s `recall` does this correctly today | **1** for a third-party `Store` author — nothing checks that a retrieval tool is classified `external` rather than `read`; the one shipped binding gets it right because its author followed the docs, not because any code would have refused the alternative | nothing — `docs/04-interfaces.md §5.1`'s own words ("the defect, not a configuration choice") name a rule enforced by convention, not code |
| 7 | The model holds its own safety switch | `mode_set(approval_mode="never_require")` ([§09](../research/09-memory-context-multiagent-hitl.md) §14.2) | R-3; `effect` **has no default**, so the switch has no defaulted parameter to hide in | **3** | forgetting `effect` is a `TypeError` at tool-definition time |

---

## B. Bugs this design found in itself

This is the most trustworthy part of the document, because it isn't self-praise. Two
adversarial review rounds found **58 findings**; the table below is the ones fixed and
the mechanism that replaced them.

| id | the bug in the draft | the replacement mechanism | tier |
|---|---|---|---|
| S-2 | `unguarded_paths()` DFS starts at `START`, but a resume enters **mid-graph** => TTL and revocation are silently lost | **I-1**: a gate is a *precondition at the point of consumption*, not an edge — the `tools` node re-checks the `DecisionLog` right before each call | **2** (checked at runtime) |
| S-1 | `GUARDED` guards a **node name**, so compaction's model call sits outside the ceiling | **I-2**: enforced at the **seam** — `ModelProvider` is wrapped once, raises with no `Reservation` | **3** |
| S-3 | The confidentiality axis had **no source at all** — half the lattice was decoration | Two sources on the mandatory path: `Secret[T]`, and `emits` settable only by the **operator** | **3** |
| S-5 | `recall` trusted `provenance` **read back from the store itself** => untrusted data self-declaring its label | M-1 `recall` always `join(UNTRUSTED)`; M-2 provenance only **raises**; M-3 the exception needs operator opt-in **and** a harness-signed MAC | **3** |
| K-25 | `idempotency=NONE` as the default => shortcoming #5 only fixed for whoever remembers to opt in | The effect log is always on; a 3-value enum -> 1 `bool` | **3** |
| K-26 | `Workspace` required with **0 lines** specifying what it does | §4bis, and the *"NOT guaranteed"* section given equal weight to the guarantee itself | **2** + honest |
| K-16 | The Tier 1 example doesn't run against the actual `fn` spec | `@tool` **generates** the adapter from type hints; `ctx` is **optional** | **3** |
| K-20 | `ToolInputInvalid` could break the `HarnessError` contract | The subclass requires exactly `what/got/fix/doc` | **3** |
| K-2 / K-5 / K-1 | three abstractions with **no user reaching them** | cut outright | — |

---

## C. What does NOT reach tier 3, and why

This section matters more than the two above. Goose's mistake was exactly **promising
more than it could enforce** ([§05](../research/05-ideal-harness.md) §31-8), so a
guarantee an operator *thinks* they have is more dangerous than one they know they
don't.

| what | actual tier | why it doesn't reach tier 3 |
|---|---|---|
| `Workspace` isolating a tool | **2** | an **in-process** boundary. A deliberately malicious tool calling `open()`/`socket()` directly can't be blocked. It defends against a carelessly written tool and a model under injection, **not** against a hostile tool author — that needs a `Sandbox` with a process boundary plugged in |
| The `egress` allowlist | **2** | doesn't block exfiltration **through an allowed host**. That belongs to the confidentiality axis, not to `egress` |
| Isolation between tenants | **2** | R-4 is a *necessary condition*, not sufficient evidence. The external store is unverified |
| Counting tokens before sending | **2** | nobody has a token-exact budget; compacting against an estimate and then getting refused by the provider is a real scenario |
| `unguarded_paths()` | **2**, narrow domain | only proves something about **paths from `START` in the static graph**. I-1 and I-2 cover the rest, and both are checked **at runtime** |
| Exactly-once for a `write` tool | **2** | at-most-once is the harness's real ceiling on its own. If the process dies right while an HTTP request is in flight, no local record can distinguish "it arrived" from "it didn't." Exactly-once only happens when upstream accepts the key |
| `Sandbox.isolation` (G-7, design/review-architect.md; wired to an event by H-2, design/review-architect-round3.md) | **2** | the field makes the boundary *inspectable* — `ToolSpec.isolation` (set by a tool provider that owns a `Sandbox`, e.g. `CodeTools`) rides onto `TOOL_FINISHED`, so an audit event genuinely can show whether a call ran `"none"`, `"process"`, or `"container"` — but it does not itself raise the ceiling. The shipped `InProcess` sandbox is still `"none"`; `Subprocess` is still only a process boundary, not a container/VM one; a tool that never sets `isolation` on its `ToolSpec` reports `None`, not a guess. Same underlying gap as the `Workspace` row above, now just named on the record instead of silent |
| Timing out a CPU-bound synchronous tool | **2** | `dispatch.py`'s per-call timeout (`asyncio.wait_for`) only preempts a tool that yields to the event loop — an `await`, a syscall, a context switch. A tool that is a tight synchronous loop (a bad regex, an unbounded pure-Python computation) blocks the one thread `asyncio` runs on and cannot be cancelled from outside it; the timeout fires only once the call *returns*. Not a fixable poka-yoke without a process/thread boundary per call — which is exactly what a real `Sandbox` (previous row) would buy back |
| Service API's run registry (`server/__init__.py`) | **2** | in-memory, one process, `--workers 1` only (G-10, design/review-architect.md). `uvicorn --workers N` runs N separate processes; `Idempotency-Key`, run status polling, and pending approvals all key off an object living in exactly one of them, so a request that lands on a different worker than the one holding the run sees "not found," not the run's real state. The module's own docstring says this loudly; a real fix needs a cross-process `Store`-backed registry, which the current `_Run` object (it holds live `asyncio.Future`/`asyncio.Task` values that cannot cross a process boundary) cannot become without a larger redesign |

---

## D. How to read this table

Three sentences.

**A tier-3 mechanism needs no human discipline.** That's the entire difference between
this design and what the research found: LangChain's `handle_tool_error`, Microsoft's
`approval_mode`, openai-agents's `input_filter`, LangChain's `start_on` are all **tier 1
or 2, defaulting to off** — they protect whoever remembers to turn them on.

**A tier-2 mechanism must say plainly that it's tier 2.** That's why section C exists.

**A tier-1 mechanism is not a mechanism.** If all that exists is documentation saying
don't, that row belongs in
[`07-risks-and-open-issues.md`](07-risks-and-open-issues.md), not here.
