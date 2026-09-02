# Adversarial review — KISS, over-engineering, DX

**Scope:** all of `design/*.md` (7 files, 3750 lines), checked against
`research/11-workflow-and-dx.md` §22, §45 and `research/05-ideal-harness.md` §33 (Minimal
Core), §35-36.

**Rule this review applies:** [`00-foundation.md`](00-foundation.md) §8.4 —
> *"KISS / NOT-OVER-ENGINEER. If a mechanism doesn't fix a **measured** shortcoming, cut
> it."*

and the user's **Extreme DX** requirement: the simplest tier must be reachable by a
complete beginner.

**This file only comments. It does not fix any design file.** (Historical record — see
[`07-risks-and-open-issues.md`](07-risks-and-open-issues.md) for what was actually done
with each finding below, checked against today's real code.)

---

## Summary in three sentences

1. **The core ideas are strong and had already cut correctly in many places** (one
   `Effect` axis, 2 hooks instead of 6, 1 parallel mode instead of 3, 2 compaction
   strategies instead of 5, refusing a state machine for business logic). See §6 —
   don't let anyone cut those by mistake.
2. **But the design had grown well past the research's own "minimal core"**: §33 lists
   **8 things** belonging in core; the design defined **62 classes** and **38 invariant
   ids** (3 colliding). The "14 names" claim in [`01`](01-core-api.md) §1 was
   contradicted by that very file's own example.
3. **Zero-to-Agent wasn't actually progressive, and Tier 1 didn't run** against
   [`03`](03-tools-and-mcp.md)'s own spec: the Tier 1 tool example was a synchronous
   function with no `ToolCtx`, while `ToolSpec.fn` required
   `Callable[[Mapping, ToolCtx], Awaitable[Any]]`. Tier 2 jumped 13 concepts in one step
   and contained an `input()` call blocking the event loop.

---

## Counting concepts — the real numbers

### Measured from source

| measurement | count | how measured |
|---|---:|---|
| `class` defined across `design/*.md` | **62** | `grep -oE "class [A-Za-z_]+" design/*.md \| sort -u` (4 prose false-positives excluded) |
| public type alias / `NewType` | 10 | `Actor`, `EndStrategy`, `ToolName`, `ServerLabel`, `IdempotencyKey`, `NodeName`, `ModelHandler`, `ToolHandler`, `DepsT`, `OutT` |
| `Final` constants a user might have to tune | 8 | `AS_OF`, `CLEARED`, `COMPACT_AT`, `EDIT_AT`, `INPUT_MARGIN`, `KEEP_RECENT_STEPS`, `MIN_USEFUL_OUTPUT_TOKENS`, `EFFECT_PROFILES` |
| invariant ids (`P-n`, `R-n`, `C-n`, `I-n`, `M-n`, `S-n`, `T-n`, `W-n`, `D-n`) | **38** | of which **3 collide**: `P-3`, `R-1…R-3`, `C-1…C-4` (see K-13) |
| names in the claimed "minimal public API" | 14 | [`01`](01-core-api.md) §1 |
| names **actually** `import`ed in that same file's own 3 examples | **20** | 14 example names + 6 submodule names; only 8/20 are in the 14-name list |

### How many names a user has to learn before…

Counting "a name" = anything that must be typed or understood to read the code: types,
constructor parameters, methods, `Result` attributes, and every string mini-DSL.

| tier | new names | running total | what |
|---|---:|---:|---|
| **Tier 0** — the agent runs | **8** | 8 | `Agent`, `name`, `job`, `model`, `budget`, the `"$0.05"` DSL, `run_sync`, `.text` |
| **Tier 1** — add a tool | **+4** | 12 | `tool`, `effect=`, `Effect` (4 values), `tools=` |
| **Tier 2** — a side effect | **+13** | **25** | `Approver`, `Answer`, `Verdict` (3 values), `Human`, `Channel`, `Workspace`, the `egress=` DSL, `sandbox=`, `approve=`, `req.scope` -> `Scope`, `req.estimated_cost`, `Actor`, `UnsafeToolSetError` |
| **Tier 3** — "production" | **+16** | **41** | `checkpointer`/`SqliteCheckpointer`, `policies`/`Policy`/`DenyHosts`, `plugins`/`Plugin`/`Retry`/`CostReport`, `end_strategy` (3 values), `stream`/`Event`, `run_id`, `resume`, `StopReason` (8 values), `Result.pending`/`.cost`/`.label`/`.decisions`, `Decision`, `Label` (2 axes x 2 values) |
| **+ writing a production tool** ([`03`](03-tools-and-mcp.md)) | +10 | **51** | `ToolCtx`, `IdempotencyMode` (3), `ToolInputInvalid`, `ToolUnavailable`, `ToolOutcome` (3), `CancelToken`, `timeout_s`, `accepts_tainted`, `max_confidentiality`, `server` |
| **+ using MCP** | +6 | **57** | `McpServerPolicy`, `ServerIdentity`, `trusted`, `default_effect`, `effects`, `allow` |
| **+ using memory** ([`05`](05-cost-and-memory.md)) | +4 | **61** | `Provenance`, `Memo`, `Store`, the `recall = external` rule |

**Conclusion on the numbers.** 8 names for "hello world" is **good** — at or below every
framework in the research, and three of eight (`job`, `budget`, `.text`) are
self-explanatory to a 10-year-old. **41 names to reach "production"** is too many
against the claim that "each new tier reveals exactly one concept" — the real
progression is 8 -> 4 -> **13** -> **16**. The jump from Tier 1 to Tier 2 is where it
breaks: **13 concepts at once**, described in the file as merely "sandbox and approval
appear."

**Disguised synonyms found (detailed as K-3, K-4, K-21):**

| concept | names currently used for it | where |
|---|---|---|
| a request sent to an approver | `ApprovalRequest` · `AskRequest` · `PauseRequest` | 01 §1.4 · 02 §2.2 · 04 §4.3 |
| an approver's answer | `Answer` · `AskOutcome` · (`ruling=`) | 01 §1.4 · 02 §2.2 · 01 §2 Tier 3 |
| the person/channel answering | `Approver` · `ApprovalProvider` | 01 §1.4 · 02 §2.2 |
| the `Decision` log | `DecisionLog` · `AuditSink` · "audit store" | 02 §2.4 · 02 §3.1 · 04 §4.3 |
| a "fixable" vs. "definitive" tool error | `Retry`/`ToolFailed` · `ToolInputInvalid`/`ToolUnavailable` | 01 §5.4 · 03 §2.2 |

Five concepts carrying **13 names**. That's 8 names removable without losing a single
guarantee.

---

## Findings

Each finding: what it is, why it's excess, and the recommendation. Severity tiers as
originally scored: **cut now** (unambiguous), **should cut**, **consider**.

| id | tier | finding |
|---|---|---|
| **K-1** | cut now | `Quarantine` + `Quarantined[T]` (02 §5) — the file's own *Not Enough Evidence* section admits exactly ONE implementation exists anywhere in the research, `@experimental`, never wired in, not concurrency-safe, no evaluation comparing injection success rates with/without it. The textbook case for §8.4: a mechanism fixing no measured shortcoming. Recommendation: cut to `07-risks` as "an idea with real architecture, waiting on evaluation" — the ordinary `UNTRUSTED` + `danger` => `ASK`/`DENY` path already covers it. |
| **K-2** | cut now | `deps_type`/`DepsT` (01 §1.1) — `Agent` generic over a dependency-injection type. Two independent reasons, either sufficient: the generic reaches no user (`ToolCtx` in 03 §6.2 has 6 fields, no `deps`); and the only citation for it is "PydanticAI has it," which per §8.4 is an opinion, not a finding. Recommendation: cut `deps_type`/`DepsT`/`deps=` from all four signatures; DI belongs in a tool's closure via `functools.partial`. |
| **K-3** | cut now | Three name sets for one approval round-trip (01 §1.4 · 02 §2.2 · 04 §4.3) — `ApprovalRequest`/`AskRequest`/`PauseRequest`, `Answer`/`AskOutcome`, `Approver`/`ApprovalProvider`. Not just spelling: they disagree on where `actor` comes from (01: fixed at `Approver` construction, matching D-1; 02: self-declared per answer, exactly agno's mistake), on expiry (`expires_at` an absolute time in 01 vs. a `grant_for` timedelta capped in 02 — 01's shape lets a broken UI grant a 100-year approval), and on what `resume()` carries (01's `Answer` vs. 04's `ResumeToken(decision_id)`, which its own fail-closed rule #1 would reject). Recommendation: keep one set, built on 02's safety-correct shape, renamed to 01's friendlier names; drop `PauseRequest` and `ApprovalProvider` entirely — 4 types and 1 protocol removed. |
| **K-4** | cut now | `DecisionLog` and `AuditSink` (02 §2.4, §3.1) are one ledger wearing two protocols — both append-only, durable, keyed by `run_id`, both storing `Decision`; 04 §4.3 itself calls the lookup target "the audit store." Recommendation: merge into one `AuditSink` with `commit`/`emit`/`lookup`/`since`. |
| **K-5** | cut now | `Snapshottable` Protocol (04 §5.2) has **zero** implementers that actually match its signature — `Ledger.snapshot()` returns `LedgerState`, not `Mapping[str, Any]`, and `restore()` takes three parameters, not one; `Label` has no `snapshot`/`restore` at all; the "set of `DecisionId`s" is a plain `list[str]`. A Protocol with 0 correct implementers is pure abstraction — exactly the failure mode 04 §3.2 itself warns about ("a checker that always returns empty also passes"). Recommendation: cut the Protocol, keep rules S-1/S-2/S-3 as property tests on a state dict instead. |
| **K-6** | should cut | The `Confidentiality` axis has no source (02 §4.1's `label_after` reads `spec.emits`, but `ToolSpec` in 03 §1.1 has no `emits` field, and nothing anywhere produces `Confidentiality.SECRET`) — half the lattice is unreachable decoration, and 02's own *Not Enough Evidence* admits no Python package surveyed has a comparable type either. Recommendation: either cut the axis back to integrity-only, or give it a real source (`emits`/`max_confidentiality` on `ToolSpec`, plus at least one default path that actually produces `SECRET`). Leaving it as-is (present but unreachable) is the one option ruled out — it costs concepts while creating false confidence. |
| **K-7** | should cut | Four defense layers for one unmeasured error margin (05 §B.3): `Reservation.exact` changes a `bool` nothing reads — when budget is wide, the 1.15-margin estimate already fits, so `exact=True` never changes behavior anywhere. Recommendation: cut `hard_max_input_tokens()`/`Reservation.exact`, keep the other three (margin, calibration, the 20% buffer, one deterministic retry — each with a distinct role). |
| **K-8** | should cut | `IdempotencyMode` (`NONE`/`KEYED`/`NATIVE`, 03 §1.1) — `NATIVE` differs from `KEYED` in exactly one behavior (whether the key is exposed to `fn`), but the runtime always generates a key anyway; a tool author using it or not is their own business, not an enum value. `NONE` is a separate problem (K-25). Recommendation: `ToolSpec.idempotent: bool`, `ctx.idempotency_key` never `None`. |
| **K-9** | should cut | `run()`/`try_run()`/`Result.raise_for_status()`/`.ok` — four ways of writing the same program (01 admits `run`/`run_sync` is "a convention, not a finding"). Recommendation: keep `run()` and `try_run()`; cut `raise_for_status()` as a rewrite of `run()`; keep `.ok` (a cheap, readable property). |
| **K-10** | should cut | A 9-span x ~50-attribute OTel taxonomy (04 §8.2), justified by citing google-adk-java's **code density** (11.4 hits/kLOC) — someone else's density isn't this design's own measured shortcoming, and 04 itself admits the research never measures that density's runtime overhead. `harness.step` carries exactly one attribute — a whole span for an integer. Recommendation: 4 spans (`harness.run`/`.model`/`.policy`/`.tool`); keep the 4 content rules (`effect` on every tool span, `decision_id` not `approved=true`, `actor_kind` not `actor_id`, only `args_sha256`) — those fix a real measured gap and matter more than span count. |
| **K-11** | consider | `end_strategy` at the Tier 3 constructor — real evidence (PydanticAI changed its own default because the old one was wrong), but it's the one parameter among 13 tied to no failure mode of THIS harness, and its third value's name `"exhaustive"` collides with an unrelated parallel-mode name in 03 §3.2. Recommendation: keep the parameter, drop it from the Tier 3 example (not one of "the four industry-wide gaps" it illustrates), rename the third value. |
| **K-12** | consider | `ServerIdentity.fingerprint` (03 §5.2) — 03 itself admits the rug-pull scenario was never directly observed in any source read, and no format decision has evidence behind it (no obvious SPKI equivalent for stdio). Recommendation: v1 keys on `ServerLabel` (a string, matching Microsoft) — the part WITH evidence; move `fingerprint` to `07-risks` pending a real re-pointing incident. |
| **K-13** | consider (but fix soon) | 38 invariant ids, 3 real collisions: `P-3` means both "a plugin can only weaken" (01 §4.2) and "a policy raising fails closed" (02 §1.2); `R-1`/`R-2`/`R-3` mean both the four global architecture rules (00 §5) and three memory-read rules (05 §C.1); `C-1`…`C-4` mean both cancel rules (03 §6.3) and cost invariants (05 §A.1). Also `I-1` exists in 03 §4.4 with no `I-2` anywhere — the numbering pretends to be one shared namespace when it isn't. A concrete DX cost: an error message or comment citing "violates C-2" is undecodable without knowing which file you're in. Recommendation: prefix by file (`POL-`, `COST-`, `CAN-`, `MEM-R`), keep `R-1…R-4`/`D-1`/`D-2` global to `00`; or, more KISS, drop ids for rules cited exactly once (29 of the 38 ids are never cross-referenced from another file). |
| **K-14** | cut now (fix) | `resume(ruling=…)` survives in **4** places (01 §2 Tier 3 twice, §5.2's table, §6's table) despite 01 §1.2's real signature being `resume(run_id, *, answer: Answer | None)` — and all four contradict 04's `ResumeToken`-only rule anyway. |
| **K-15** | cut now (fix) | `@tool` has two different signatures (01 vs. 03) that disagree on which parameters exist (`idempotency`, `timeout_s`, `max_confidentiality`) and on whether `ToolSpec` is generic; and `ToolSpec` in 03 §1.1 is missing `max_confidentiality`/`emits`, which 02 §4.1 reads anyway. Root cause of K-6. |
| **K-16** | cut now (fix) — **the most severe DX bug found** | The Tier 1 example (`def word_count(text: str) -> int:`, synchronous, no `ToolCtx`) doesn't run against 03's own spec, which requires `fn: Callable[[Mapping, ToolCtx], Awaitable[Any]]`. This is exactly where "a 10-year-old can follow it" lives or dies — the example is right; the spec is what must change: `@tool` must GENERATE the adapter from a plain typed function, and `ToolCtx` must be OPTIONAL (injected only when the author declares a `ctx` parameter). Otherwise Tier 1 doesn't run and the whole Zero-to-Agent ladder collapses at its second step. |
| **K-17** | cut now (fix) | `Budget()` constructs with no arguments and `usd=None` is accepted (05 §A.1), directly contradicting 01's own stated invariant that budget is required and must have a money axis. Recommendation: `usd: Decimal`, no default, never `None`. |
| **K-18** | cut now (fix) | `StopReason` (01 §5.2, 8 values) is missing `"graph_changed"` (used in 04 §4.5) and `TRUNCATED` (used in 05 §B.3) — a closed enum with values used outside it is a runtime error waiting to happen. |
| **K-19** | cut now (fix) | `AuditEvent.type` (02 §3.1) is a closed `Literal` missing at least 5 event kinds actually emitted elsewhere (`policy.allowed`, `duplicate_suppressed`, `run.started`, `run.finished`, `taint.raised`) — and the design has no single list of all event kinds anywhere, despite 04 §2.2 referencing "the graph only emits 9/15" as a past bug. Also where the research's §33 "a versioned event envelope" requirement lands (see K-27). |
| **K-20** | cut now (fix) | `ToolInputInvalid.__init__` (03 §2.2) takes a positional `message` and an optional `fix`, dropping `got`/`doc` — breaking 01 §3.2's own claimed contract that `HarnessError` subclasses always require `what`/`got`/`fix`/`doc`. A contract a subclass can break isn't "structural," it becomes discipline again — exactly what §3.2 claims to avoid. |
| **K-21** | cut now (fix) | Two name sets for the tool error taxonomy (`Retry`/`ToolFailed` in 01 §5.4 vs. `ToolInputInvalid`/`ToolUnavailable` + `ToolOutcome` in 03 §2.2), and `Retry` in 01 names both an exception (§5.4) AND a plugin (§2 Tier 3) — same name, two different things, same file. The default-outcome tables also disagree between the two files. Recommendation: 03's names are better (they describe *what happened*, not *what to do*); 01 §5.4 should be rewritten to match, and the plugin renamed `Backoff`. |
| **K-22** | should cut | "The whole surface is 14 names, one import" (01 §1) is contradicted by that very file's own example, which imports 20 names from 4 modules — and among the claimed 14, `Workspace` is never defined anywhere in the 3750 lines (see K-26), nor are `Result.cost`'s `Money` or `Result.usage`'s `Usage`, nor `Event`. Recommendation: either state the honest number (20 names, 1 root import + 3 submodules) or pull the missing names up to top-level and actually deliver on "one import" — either is fine, leaving the false claim in place is not. |
| **K-23** | should cut | Nine operational tunables (`max_concurrency`, `cancel_grace`, `max_grant_ttl`, `quarantine`, the `depth` cap, the retry budget, `EDIT_AT`/`COMPACT_AT`/`KEEP_RECENT_STEPS`, `INPUT_MARGIN`/`MIN_USEFUL_OUTPUT_TOKENS`) have no path from `Agent(...)` at all — three parallel configuration surfaces (Agent parameters, `RunConfig`, module constants) is exactly what 05 §B.2 itself calls "a wide configuration surface is a configuration surface defaulting to off." Recommendation: one place — operationally meaningful ones (`max_concurrency`, `cancel_grace`, `max_grant_ttl`, `depth`) folded into an `Agent(limits=Limits(...))`, the rest left as clearly-labeled, non-configurable module constants (matching how `EFFECT_PROFILES` is already handled correctly). |
| **K-24** | should cut | Small, fast fixes: broken code block in 03 §6.2 (an orphaned indented class body); `@value class CancelToken` (04 §6.1) claims immutability but has a mutating `cancel()` method — a type contradiction; `Decision.verdict`'s comment says `# ALLOW \| DENY` while other files correctly use `Literal[Verdict.ALLOW, Verdict.DENY]`; `Scope.args` typed two different ways across files; `Verdict` imported from two different paths. |
| **K-25** | cut now (fix) | **Shortcoming #5 (idempotency) was not actually fixed by default.** The README claims "nobody has tool-call idempotency... we do," and 01 §3.4 claims it "cannot happen." But 03 §1.1 defaults `idempotency: IdempotencyMode = IdempotencyMode.NONE`, meaning a `write` tool gets no key and no effect log unless its author opts in — exactly the LangChain/Microsoft failure class (`handle_tool_error` per-tool opt-in; a good module nobody wires in) this design elsewhere criticizes. Recommendation: `write`/`danger` always go through the effect log; the runtime always generates a key; combined with K-8, collapse the enum to one `bool` for "does upstream accept the key." This makes the design simultaneously simpler and more honest about its own promise. |
| **K-26** | cut now (fix) | `Workspace`/`Sandbox` are required at construction (an `UnsafeToolSetError` if missing) with **zero lines specifying what they actually enforce**, anywhere in 3750 lines — a more serious hole than any over-engineering item above, since it's a mandatory mechanism nobody can describe. This is exactly Goose's mistake (§31-8): 4 permission modes, a removed sandbox seatbelt, tools still running with the user's full permissions. Recommendation: a short section answering three questions — how the workspace root is enforced, at what layer `egress` is enforced, and what is explicitly NOT guaranteed (the third matters most). |
| **K-27** | should add | §33 lists a versioned event envelope as 1 of 8 things required in the minimal core; 01 §1.2 returns `AsyncIterator[Event]` and reads `ev.type`/`ev.sequence`, but `Event` is never defined anywhere — only `AuditEvent` exists (02 §3.1), a different type using `seq` instead of `sequence`. Recommendation: define `Event` once in `00-foundation.md`, make `AuditEvent` the same type plus a durability guarantee — folds into K-19 (one shared event-kind table). |
| **K-28** | should add | §33 requires session/tenant identity in core; 01 §1.2 claims all "four things" a sample interface is missing are covered, session included — but no `Session` type exists anywhere, only `run_id`, and multi-tenancy is pushed to *Not Enough Evidence* in three separate files with the same sentence. Recommendation: the honest and cheapest fix — state plainly that session belongs to the service layer (see `07-risks`), not invent a type just to match the claim. |
| **K-29** | should add | `redact()` is referenced from three places (04 §2, §4.3, §8.2) and defined in none of them — the sole mechanism against what 03 §16 calls "no Python package has a dedicated `Secret` type." |

**Estimated effect of applying K-1…K-10 and K-25:** 62 classes -> roughly **48**; the
Zero-to-Agent-to-production path's 41 names -> roughly **33**; and the design's three
biggest claims (idempotency by default, "14 names," "one concept per tier") become
**actually true** instead of approximately true.

---

## What's already well-balanced — DON'T CUT THESE

Recorded so a later review round doesn't cut these by mistake. Each row below is
already the result of a correct cut, with specific evidence for it.

| # | what | why not to cut it |
|---|---|---|
| **G-1** | **One `Effect` axis deriving 5 behaviors** (00 §2, 03 §1.2) | The central idea, and it REDUCES concepts rather than adding them: unifies `ToolKind` + `sequential` + `disable_*_tool_approval` + `readOnlyHint`/`destructiveHint` into one question. A tool author declares exactly one thing. Invariant T-1 (no override field) is what keeps it from bloating back up. |
| **G-2** | **Two around-hooks, not six** (01 §4.1) | The reasoning is already correct and already written down: the other four hooks are just the first/last line of an around-hook. Keep as-is. |
| **G-3** | **One parallel mode, not three** (03 §3.2) | The reason is safety, not taste: a dial that can be turned to `'parallel'` is a dial that erases the barrier. `max_concurrency` is a resource limit, not a safety limit — the distinction is correct. |
| **G-4** | **Two compaction strategies, not five** (05 §B.2) | Keeps Microsoft's priority order, cuts the count, and states a reason for each of the three dropped. A model example of correctly applying §8.4. |
| **G-5** | **Refusing a state machine for business logic** (04 §2.3) | The research notes nobody has first-class state machines — and this design does NOT treat that as an opportunity. Correct: the measured shortcoming is enforcement being bypassed, not a missing state DSL. |
| **G-6** | **`GUARDED` is data, `unguarded_paths()` reads that exact table** (04 §2.1, §3) | Three lines of data instead of three pages of prose, plus `witness` so an error is fixable in 30 seconds, plus a mutation test for the checker itself. Cheap and correct. |
| **G-7** | **`max()` used for `Verdict`, `Label.join`, and `DecisionLog.lookup`** | One operation for three things. "One `max()` line for three properties" (02 §2.4) is the single best sentence in the design. Don't add a second priority rule. |
| **G-8** | **"Forever" has no representable value** (02 §2.5) | Poka-Yoke at the type layer, not the review layer. What can't be constructed doesn't need guarding. |
| **G-9** | **`Actor` has no `Model` variant** (00 §4.2) | One type definition erasing an entire failure class (agno's `decision_log`). And it's tested (02 §6.2). |
| **G-10** | **`Ruling` != `Decision`** (02 §0) | NOT a redundant concept: `Ruling` has no actor, doesn't outlive the call, never enters the audit log. Correctly split. (The redundant one is `AskOutcome` — see K-3 — not `Ruling`.) |
| **G-11** | **`Policy.check` is pure + synchronous** (02 §1.2, P-4) | Two measured reasons, both correct: enumerable => provable with hypothesis; and an `async` policy could call a model => the model influencing its own permissions. |
| **G-12** | **The effect log: a partial-unique index, never swallowing `IntegrityError`, an `in_flight` row that never self-releases** (03 §4) | Copies agno's three technical details verbatim and is HONEST about what it achieves (§4.5's three-mode table). That honesty is worth more than a false exactly-once promise. |
| **G-13** | **`count_tokens_approximately` keeps its honest name** (05 §B.3) | One naming decision that blocks an entire class of misunderstanding. Keep. |
| **G-14** | **Memory-write provenance, `provenance` with no default** (05 §C.1) | Fixes the largest security gap the research found, using the exact lattice already in place (rule: "memory gets no special-case rule"). And rule W-3 parallels D-1 — the same idea, not a second one. |
| **G-15** | **Every file has a `## Not Enough Evidence` section** | 7 files, ~40 entries. This is rare, and it's the exact tool that surfaced K-1, K-2, K-6, K-7, K-12 in this review. Don't drop it. |
| **G-16** | **Tier 0 = 5 lines, 8 names, `budget` the only mandatory line** | Genuinely matches "a 10-year-old": `name`, `job`, `model`, `budget`, print `.text`. And the one thing forcing an extra line to be typed is the one thing the whole industry lacks — an excellent DX decision. Protect it by fixing K-16, not by loosening it. |

---

## Verification appendix — how to re-check each finding

```bash
# K-3, K-14: three approval name sets + leftover ruling=
grep -n "ApprovalRequest\|AskRequest\|PauseRequest\|AskOutcome\|Answer\|ruling=" design/*.md

# K-13: invariant id collisions
for c in P-3 R-1 R-2 R-3 C-1 C-4; do echo "== $c"; grep -n "\*\*$c" design/*.md; done

# K-15, K-6: ToolSpec fields read but never defined
grep -n "spec.emits\|max_confidentiality" design/*.md

# K-18, K-19: enum values used outside their own enum
grep -n "graph_changed\|TRUNCATED\|taint.raised\|duplicate_suppressed\|run.finished\|policy.allowed" design/*.md

# K-22, K-26, K-27, K-29: public names never defined
for n in Workspace Sandbox Event Money Usage redact Channel Human; do
  echo "== $n"; grep -n "class $n\|^$n *=\|def $n" design/*.md; done
```
