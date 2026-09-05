# Adversarial review, round 3 — re-verifying round 2's own six findings, then fresh discovery

**Commit reviewed:** current working tree, `3a5b2d7` (`Add round-2 architect review
(H-1..H-6)`) — the tip of the branch, unchanged since round 2 wrote its report. `git log`
confirms no commit landed between round 2's report and this one: every file round 2 cited
is byte-for-byte what it read.

**In scope for part (a):** independently re-verifying `H-1`..`H-6` from
[`review-architect-round2.md`](review-architect-round2.md) against current source —
holds / overstated / wrong, each with its own evidence, not round 2's say-so.

**In scope for part (b), per this round's tasking:** fresh discovery against the same
five questions rounds 1-2 answered, explicitly not restating `H-1`..`H-6`. New findings
continue the `H-` prefix from `H-7`.

**Explicitly out of scope for re-litigation:** `G-1`..`G-17` (round 1, closed) and the
six `H-1`..`H-6` findings themselves as *targets to re-discover* — they are re-verified,
not re-found.

**Method note — baseline, run before touching anything:**

```
python -m pytest -q            -> 864 passed, 94 warnings, 41.1s
ruff check src tests examples  -> All checks passed!
mypy src                        -> Success: no issues found in 72 source files
```

Green, matching round 2's own baseline exactly (864 passed). This is the baseline every
finding below is checked against.

**This file fixes nothing.** It only records findings, per this round's tasking.

---

## Part (a): H-fix verification — re-checking round 2's own six findings

Nothing has changed in the source tree since round 2 wrote its report (confirmed by
`git log`: `3a5b2d7`, the commit that added `review-architect-round2.md`, is the tip of
the branch). So this is not "did the fix regress" — it's "was round 2's own reasoning
right." Each of the six was re-derived from the current code independently, including one
live reproduction (`H-1`) run fresh this round rather than trusting round 2's transcript.

| id | verdict | evidence |
|---|---|---|
| H-1 | **holds** — and broader than round 2's own file list suggests | Reproduced live this round (script below): a `FakeModel`-backed provider priced at real `claude-haiku-4-5` rates, wrapped to fail twice then succeed. Control (0 failures): `$0.0002`, 1 call. Two-timeouts-then-success: **`$0.0002`, 3 calls** — identical spend. All-attempts-fail (5/5): correctly bills `$0.2006`, matching `settle_worst_case`'s worst-case formula. Round 2 cited only `run.py:127-146`; the identical gap is independently present in the **LangGraph backend too** — `lg/runtime.py:322-323`'s success path (`led.settle(_RESERVED(...), u, self._price)`) carries no attempts multiplier either, the exact same shape as `run.py`'s. `with_provider_retry` (`retry.py:91-124`) still only attaches `.attempts_made` to the exception it raises on final failure (`retry.py:119`), never to a successful return — round 2's own point stands, and is symmetric across both engines, which round 2's evidence didn't state outright. |
| H-2 | **holds** | `grep -rn "isolation" src/harness/*.py src/harness/*/*.py` outside `sandbox.py` and its own docstring cross-references still returns only comments in `tools/code.py` (`:94,97`) telling a reader to check the attribute manually. `dispatch.py`'s three event-emission sites (`:287-288`, `:300-302`, `:373-374`) carry no `isolation` field; `ToolSpec` (`tools/__init__.py:114-130`) has no sandbox/isolation reference of any kind. Confirmed independently, not merely re-read. |
| H-3 | **holds** | `src/harness/eval/benchmark.py::benchmark` (`:51-`) still takes only `run_fn`, `n`, `concurrency` — no `total_budget`, no cost-tracking hook of any kind, `grep -n "total_budget" src/harness/eval/golden.py` finds five hits (all in `golden.py`), zero in `benchmark.py`. `run_golden_set`'s own `total_budget=` (`golden.py:93-139`) is unchanged from round 2's reading. |
| H-4 | **holds** | `docs/02-architecture.md:10` still reads "Five things are pluggable"; `:26`'s layer diagram still lists four names plus "the 5 plugin seams" with `Sandbox` absent; `:174` still carries the self-aware "Six seams... this sentence wasn't [updated]" admission next to §4's own six-row table. The module map (`:196-271`) is still missing `mcp/`, `lg/`, `eval/`, `server/`, `sandbox.py`, `workspace.py`, `session.py`, `tasks.py`, `progress.py`, `retry.py`, `idempotency.py`, `dispatch.py`, `_value.py` — 13 modules, re-counted directly against `find src/harness -name '*.py'` this round, matching round 2's count. Still names 6 files that do not exist (`_typing.py`, `tools/invoke.py`, `context/caching.py`, `observe/bus.py`, `observe/redact.py`, `tools/builtin/shell.py`/`math.py`), confirmed with `ls`. |
| H-5 | **holds** | `agent.py::_check_subagent_safety` (`:912-923`) is unchanged: `if parent_approve is not None and child.approve is None:` — a presence check only. `child.approve = lambda call, **kw: True` (or any always-approve callable) satisfies `child.approve is None` being `False` and passes construction, exactly as round 2 described. This is an inherent limitation of a callback-shaped interface, not a regression — round 2's own framing ("worth noting," not "should fix") is the right call. |
| H-6 | **holds** | `mcp/__init__.py:84`'s `reviewed_tools: Mapping[str, str] | None = None` and `_effect_for` (`:151`, `if policy.reviewed_tools is not None:`) are unchanged — `reviewed_tools=None` still skips the pinning check entirely, falling through to `_effect_from_hints` for any `trusted=True` server, exactly the S-9/G-13 rug-pull window round 2 described. No `UserWarning` was added for `trusted=True` + `reviewed_tools=None` (round 2's own suggested minimal fix) — confirmed absent by inspection of the same file. |

**Verdict on the six as a set: all hold, none overstated, none wrong.** Round 2's own
verification discipline (independently re-deriving each fix rather than trusting a commit
message) survives a second, independent pass. The one correction worth recording is not to
severity or existence but to *scope*: H-1 is a two-backend defect, not a one-backend one
— `retry.py`'s missing attempts-on-success plumbing is shared code, so both call sites
that build on it inherit the same gap symmetrically. This does not change H-1's severity
(already "severe"), only broadens which files a fix needs to touch.

**Reproduction script for H-1** (kept for the record, `/tmp` path elided):

```python
class PricedTimeoutProvider(TimeoutProvider):
    def price(self, model): return pricing.price("claude-haiku-4-5")

p2 = PricedTimeoutProvider(FakeModel([FakeModel.text("hi")]), fail_calls=(0, 1))
a2 = Agent(name="a", job="x", provider=p2, model="claude-haiku-4-5",
           budget="$5, 10 steps", allowed_hosts=None)
r2 = await a2.atry_run("go")
# -> cost $0.0002, calls made 3 (2 real timeouts + 1 real success, billed as 1)
```

---

## Part (b): fresh findings

## Summary table

| id | severity | one line |
|---|---|---|
| H-7 | **severe** | Two `external`-effect tool calls in the same model turn run in `dispatch.py`'s **parallel** batch with no re-check gate — the confidentiality-DENY branch of `check_flow` is evaluated once, before either runs, so a call that raises the run's label to `SECRET` mid-batch does not stop a second, already-scheduled `external` call in the *same* batch from returning its result anyway. Reproduced live: a real secret value crosses from one tool's output into another's, on the classic engine only — the LangGraph engine is not vulnerable, because it never parallelizes tool execution at all. |
| H-8 | should fix | `06-poka-yoke-matrix.md §A` row 6 (`A memory write records no provenance`) describes a mechanism — a required `provenance` keyword on `Store.put`, a `Provenance` dataclass, "checked by: `put` cannot be called without provenance" — that does not exist anywhere in the current source tree. `grep -rn "provenance" src/harness/` returns zero hits. The actual, current mechanism (documented correctly in `docs/04-interfaces.md §5.1`) is simpler and structurally weaker: a retrieval `Store`'s own tool author must remember to classify their tool `effect="external"`; nothing enforces this for a third-party `Store`. |
| H-9 | worth noting | `06 §A` row 2's "checked by: a PLUG-1 property test" overstates what exists: `tests/test_m4.py::Plugins` is four example-based unit tests (one ceiling-exceeded case, one within-ceiling case, one version-mismatch case, one name-collision case), not a property test iterating over many plugin/tool combinations. The underlying mechanism (a declared effect ceiling enforced at registration, `plugins/registry.py:23-46`) is real and does hold — this is a documentation-precision note, not a defeat. |

---

# I. Severe

## H-7 — the same-batch taint re-check gate (S-27) protects `serial` tool calls only; `parallel` calls (which include the one class capable of leaking) get none

**Files:** `src/harness/dispatch.py::_run_tools` (`:208-232`), `src/harness/policy/builtin.py::check_flow` (`:49-72`), `src/harness/tools/__init__.py::EFFECT_PROFILES` (`:77-86`), `src/harness/lg/runtime.py::_run_tools`/`_regate` (`:469-560`, for contrast).

**Background this builds on, not restates.** `tests/test_attack_s27.py` (S-27,
`design/review-security.md`) already found and fixed exactly this class of bug for one
pairing: a `read`-effect tool (parallel-safe) that raises confidentiality to `SECRET`,
followed in the *same batch* by a `write`-effect tool (not parallel-safe, so it runs in
the `serial` loop) whose own policy decision was computed *before* the batch started.
The fix — `gate = check_flow(self._e._taint.label, spec, self._e._a._grants)`,
re-evaluated right before each `serial` call actually executes (`dispatch.py:227`) — is
real, tested on both backends, and I reproduced it holding (see below). That is not this
finding.

**What is not covered.** `EFFECT_PROFILES` (`tools/__init__.py:77-86`) marks `READ` and
`EXTERNAL` `parallel_safe=True`; `WRITE` and `DANGER` `parallel_safe=False`. `check_flow`'s
confidentiality branch (`SECRET` + `max_confidentiality is PUBLIC` → `DENY`) applies to
`WRITE` **and** `EXTERNAL` — both are sinks by the profile table. But only `WRITE`/`DANGER`
land in `dispatch.py`'s `serial` list and get the S-27 re-check gate. `EXTERNAL` tools are
scheduled into `parallel` (`:208-213`, plain `asyncio.gather` over `self._bounded(...)`),
which calls `_invoke` directly — **no `check_flow` call anywhere on that path.** Two
`external`-effect tools requested in the same model turn are decided once, against the
label from *before* the batch, and then run with no further gate at all, regardless of
what either one's own result does to the label.

**Reproduced directly, not inferred.**

```python
@tool(effect="external")
async def peek_secret() -> str:
    """Reads a secret value."""
    SHARED["v"] = "TOP-SECRET-PAYROLL-DATA"
    return SHARED["v"]

@tool(effect="external")
async def leak() -> str:
    """Reads whatever is currently shared, unrelated tool."""
    return SHARED.get("v", "(nothing yet)")

# both tool_use blocks in ONE model turn, same shape test_attack_s27.py uses:
batch = ModelResponse(({"type": "tool_use", "id": "c1", "name": "peek_secret", "input": {}},
                        {"type": "tool_use", "id": "c2", "name": "leak", "input": {}}),
                      "tool_use", Usage(100, 20), "fake")
a = Agent(..., tools=[peek_secret, leak], sensitive=["peek_secret"])
r = await a.atry_run("go")
```

Result: both `POLICY_DECIDED` events for `peek_secret` and `leak` say `ALLOW` (decided
against the pre-batch label). `TAINT_RAISED` fires after `peek_secret`. Both
`TOOL_FINISHED` events say `is_error: False`. The actual `tool_result` content for
`leak` (`tool_use_id: c2`) is **`'TOP-SECRET-PAYROLL-DATA'`** — the exact string
`peek_secret` alone produced, returned through the second tool with no gate in between.

**Control, same scenario, `write` instead of `external` (the S-27-protected shape):**
Same two tools, `leak` reclassified `effect="write"` (hence `serial`, not `parallel`).
Identical setup, identical ordering. Result: `leak`'s `tool_result` is now
`{"is_error": true, "content": "denied by policy: leak_write can only send information
onward, and this run has read something marked secret. It could leak."}` — correctly
blocked, because `serial`'s per-call `check_flow` re-check catches it. The only variable
between the two runs is which bucket the second tool lands in.

**Why this needs no adversarial timing.** This is not a race — `dispatch.py`'s policy
decisions for the whole batch are computed once, at the top of `_run_tools`
(`:105-179`), strictly *before* either the `parallel` or `serial` list is built or run.
Whether `peek_secret` and `leak` actually execute concurrently, in program order, or in
reverse makes no difference to whether `leak` gets re-gated — it never does, on the
`parallel` path, regardless of interleaving. The reproduction above is deterministic.

**Why this reaches production configurations, not just a constructed example.**
`tools/builtin/web.py` ships two effect="external" tools (`search`, `fetch`) out of the
box. Any operator adding a *third* `external`-effect tool that is itself a sink (a
webhook poster, a Slack notifier classified `external` because it also fetches
context — a completely ordinary MCP-classified tool per `mcp/__init__.py::_effect_from_hints`,
which can produce `Effect.EXTERNAL` for `readOnlyHint=True` + `openWorldHint != False`)
recreates this exact shape with zero adversarial intent: any tool whose own result
happens to satisfy `contains_live_secret()` (a live `Secret` value leaking into a
payload — the *other*, non-`grants.sensitive` path `emits_of` already checks,
`policy/builtin.py:43`) raises confidentiality mid-batch for free, no `sensitive=[...]`
configuration required at all.

**The LangGraph backend is not vulnerable to this, structurally, not by a parallel fix.**
`lg/runtime.py::_run_tools` (`:525-`) has no `asyncio.gather` anywhere in it —
confirmed by `grep -n "gather\|Semaphore\|max_parallel_tools" src/harness/lg/runtime.py`,
which returns nothing relevant. Every pending call, regardless of effect class, runs
through a single sequential `for p in state.get("_pending", []):` loop, and `_regate`
(`:469-514`) — the LangGraph equivalent of the `serial`-only gate above — runs for
**every** call in that loop, using a `label` variable updated in place
(`label = label.join(emitted)`, deep inside the same loop) after each call. Running the
identical `peek_secret`+`leak` scenario through `build_agent()` (LangGraph backend)
produces the CORRECT result: `leak` is denied with the same "could leak" message the
`write`-effect control produced on the classic engine. This is not a second fix for the
same bug — the LangGraph engine simply never implements the performance optimization
(`docs/02-architecture.md §6`: "Tools whose effect class is parallel-safe... run
concurrently") that creates the hole on the classic engine. The two backends have
**genuinely different security properties on this one axis** — a form of drift distinct
from (and not covered by) round 2's own check that the tool-*retry* loops hadn't
diverged (§IV.1 of round 2's report); this is the tool-*policy* loop, a different
mechanism entirely, and `test_parity.py` — which states retry/error-shape rules once
against all three call shapes — has no rule stated for this one.

**Severity.** This is a genuine, reproducible bypass of the taint tracker's own
confidentiality gate for a completely ordinary tool configuration (two `external`
effect tools, the exact class the framework's own concurrency model chooses to run
without waiting for each other) on the default/documented backend. It directly answers
this round's Q4 prompt ("does anything let a safety-relevant decision happen without
going through the taint tracker at all") — here, the taint tracker computes the right
answer, `TAINT_RAISED` fires and the label really does rise, but the enforcement point
that was supposed to *consume* that fact for the second call is simply never invoked.

**Minimal fix.** Give the `parallel` batch the same treatment `serial` already has: either
(a) re-check `check_flow(self._e._taint.label, spec, self._e._a._grants)` immediately
before each parallel call's own `_invoke` inside `_bounded` (mirroring `dispatch.py:227`,
just moved one level down so it applies under `asyncio.gather` too — since the label is
read live off `self._e._taint` at the moment of the check, ordering inside the batch
resolves itself the same way it does for `serial` today), or (b) narrow `parallel_safe`
so `EXTERNAL` is no longer batched with other `EXTERNAL` calls without a gate, at the
cost of losing some of the concurrency the profile currently buys. (a) is the smaller
change and preserves the documented concurrency model.

---

# II. Should fix

## H-8 — `06 §A` row 6's own claimed mechanism does not exist in the current source; the real, current mechanism is honestly documented elsewhere but has no structural enforcement for a third-party `Store`

**Files:** `design/06-poka-yoke-matrix.md:32` (row 6), `design/05-cost-and-memory.md:614-642`
(the design intent this row quotes), `src/harness/memory/base.py::Store` (`:21-26`, the
actual shipped protocol), `docs/04-interfaces.md §5/§5.1` (the current, accurate public
doc), `src/harness/memory/viking.py::tools()` (`:159-185`, the one shipped binding that
gets this right).

**The claim under test.** `06 §A` row 6, one of the flagship "seven industry-wide
shortcomings, all closed to tier 3" rows: *"`provenance` is a **required keyword, no
default** on `Store.put`; M-1/M-2/M-3 [...] checked by: `put` cannot be called without
provenance."* This is quoting `design/05-cost-and-memory.md`'s original design almost
verbatim: a `Provenance` dataclass (`run_id`, `step`, `source`, `label`, `written_at`), a
`Memo.provenance` field with no default, and `Store.put(..., *, provenance: Provenance,
...)`.

**What is actually in the code.** `src/harness/memory/base.py`'s `Store` protocol:

```python
class Store(Protocol):
    async def get(self, key: str) -> str | None: ...
    async def put(self, key: str, value: str, *, ttl_s: float | None = None) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def search(self, query: str, *, limit: int = 5) -> Sequence[Memo]: ...
    async def close(self) -> None: ...
```

No `provenance` parameter, anywhere, on any method. `Memo` (`base.py:13-18`) has `key`,
`value`, `score`, `updated_at` — no `provenance` field. `grep -rn "provenance"
src/harness/` (case-sensitive, whole tree) returns **zero** hits — not a partially-wired
field the way `Sandbox.isolation` (H-2) is; the entire apparatus the row describes is
simply not present. `grep -rn "class Provenance" src/harness/` also returns nothing — the
type itself was never built. No test references it either (`grep -rln "[Pp]rovenance"
tests/` — no output).

**This is not a silent regression — the design evolved, and the newer text says so, in
the same document row 6 quotes from.** `design/05-cost-and-memory.md §C.2bis` (the
section *after* the one row 6 cites) already replaces the elaborate scheme with three
narrower rules once a reviewer found the original one's flaw (a store-supplied
`provenance.label` a compromised/tampered backend could reset to `TRUSTED`, silently
un-tainting a poisoned memory — landing squarely on shortcoming #6 itself): **M-1** —
`recall` is `external` like any other `external` tool, always `join(UNTRUSTED)`, no
per-record exception. That is exactly what shipped: `VikingStore.tools()`'s `recall`
carries `@tool(effect="external")` (`viking.py:159`), and `EFFECT_PROFILES[EXTERNAL].emits
= Label(Integrity.UNTRUSTED)` (`tools/__init__.py:82`) taints unconditionally, regardless
of what a `Memo`'s (nonexistent) provenance would have said. `docs/04-interfaces.md §5.1`
documents this accurately and honestly, including the load-bearing sentence: *"Any future
retrieval binding carries the same rule [...] A binding that classifies it `read` is the
defect, not a configuration choice."*

**Why that sentence is the actual gap, and why row 6's tier-3 claim is wrong rather than
merely dated.** The whole point of a *required keyword with no default* (row 6's own
framing, and `06 §D`'s own rule: "an invariant only counts as fixed once it reaches tier
3") was to make it **impossible** to write a memory, or read one back, without the
runtime's own machinery being involved — independent of whether the `Store` author
remembered anything. What actually enforces "recall taints" today is a **convention**:
each `Store` binding's own author must remember to write `@tool(effect="external")` on
their own retrieval-shaped tool. Nothing in `Store`, `Memo`, or anywhere else stops a
third-party `Store` (a Redis-backed one, say — exactly the kind of third-party
implementation this round's Q3 asks whether the documented seam contract supports) from
shipping a `recall`-shaped tool classified `effect="read"` by mistake or by a less
careful author. `docs/04 §5.1`'s own words — "the defect, not a configuration choice" —
describe a rule enforced by nothing at all: precisely `06 §D`'s own definition of tier 1
("documentation says don't; nothing stops it"), for the exact same row `06 §A` currently
files as tier 3.

**Distinguish this from H-2.** H-2 is a field that exists and is half-wired (declared,
readable, never piped to an event). This is stronger: the entire named mechanism
(`Provenance`, the required keyword, the "checked by" claim) has no code footprint
whatsoever. The safety PROPERTY the original design wanted — recall always taints,
monotonically — does hold today, for the one shipped retrieval binding, via a different
and reasonable mechanism; what doesn't hold is the STRUCTURAL, author-independent
guarantee row 6 claims, and specifically the part of it (M-2's "no system surveyed traces
back to the exact run/step/tool that planted a fact") that was dropped along with the
`Provenance` type and never replaced.

**Minimal fix.** Rewrite row 6 to describe what's actually shipped: cite `docs/04
§5.1`'s rule directly, mark its enforcement honestly as tier 1 for a third-party `Store`
author (nothing checks a retrieval tool is classified `external` — the closest existing
guard, `_check_tool_set`'s external+danger combo check, does not touch this), and either
(a) note the traceability half of the original design (M-2's per-memo audit trail) was a
deliberate simplification, not an oversight, or (b) if that traceability is still wanted,
reopen it as a real gap rather than a closed row.

---

# III. Worth noting

## H-9 — `06 §A` row 2's "checked by: a PLUG-1 property test" describes something more thorough than what exists

**Files:** `design/06-poka-yoke-matrix.md:28`, `tests/test_m4.py::Plugins`
(`:98-127`), `src/harness/plugins/registry.py::register` (`:23-46`).

The row's own mechanism claim — "PLUG-1... the side-effect set with a plugin installed
is a subset of without" — is real and does hold: `PluginRegistry.register` (`:32-38`)
raises `ConfigError` at registration if any tool's effect exceeds the plugin's declared
`provides=` ceiling, which is a genuine tier-3 mechanism (a plugin literally cannot
register a tool above its stated ceiling; there's no runtime path around it). What's
overstated is the "checked by" column's specific wording: `tests/test_m4.py::Plugins` is
four ordinary example-based unit tests (`test_a_plugin_cannot_exceed_its_declared_ceiling`,
`test_registering_within_the_ceiling_works`, `test_an_api_version_mismatch_fails_at_registration`,
`test_two_plugins_cannot_shadow_one_tool_name`), not a property test iterating many
plugin/tool-set combinations the way `hypothesis`-based tests elsewhere in this codebase
(`test_attack_s6.py`, `test_middleware.py`, `test_kiss_cuts.py`) do. This is a wording
precision issue, not a defeat of the underlying mechanism — recorded because this round's
tasking explicitly asked for a spot-check of `06 §A` rows beyond the four round 2 already
checked, and this is the honest result of checking row 2: the mechanism holds, the
"checked by" column's specific claim is more confident than the evidence backing it.

---

# IV. Checked and correctly handled — not findings

**1. The S-27 fix itself (read+write pairing) still holds, on both backends, exactly as
tested.** Reproduced `tests/test_attack_s27.py`'s own scenario directly (not merely re-run
via pytest): `read_payroll` (read, parallel-safe, `sensitive=True`) followed by
`send_report` (write, serial) in one batch — `send_report` is correctly denied on both
the classic and LangGraph backends. This is the control that isolates H-7's actual gap to
specifically the `parallel`+`parallel` combination the existing test does not cover
(`read_payroll` there is `read`, not `external`, so it never lands in a scenario where
the *second* call is also un-gated).

**2. `PLUG-1`'s underlying ceiling mechanism (H-9's own subject) is not itself defeated.**
Tried constructing a plugin that registers a `write`-effect tool while declaring
`provides="read"` directly against `PluginRegistry.register` — raises `ConfigError`
exactly as `tests/test_m4.py` already asserts. No way found to route a tool around the
ceiling check (e.g., via `discover()`'s entry-point path) — `discover()` (`registry.py:48-58`)
calls the same `register()`, ceiling check included.

**3. Third-party `Policy` seam friction (Q5) — the "must be sync and pure" constraint is
real but not hidden.** Considered whether a `Policy` that calls out to an external
moderation service is genuinely supported by the documented seam. `Policy.check`
(`policy/base.py:49-51`) is synchronous; `docs/04-interfaces.md:429,477` explicitly
documents this as a deliberate, reasoned rejection of an async alternative ("purity is
what makes [...]"), not a silent gap — a third-party author hitting this reads the exact
constraint and the exact reason for it in the interface doc itself, before writing any
code. Real friction (any such `Policy` must pre-fetch or cache), but documented friction,
not the undocumented-assumption kind Q5 asks about.

**4. Third-party `Store` construction (Q3) otherwise works smoothly.** Set aside the one
real gap found (H-8's "nothing enforces the `external` classification convention"),
building a plausible third-party `Store` (walked through what a Redis-backed one would
need against `memory/base.py`'s five methods + `Memo`'s four fields) requires nothing
beyond what `docs/04 §5`/`§5.1` state, and `SqliteStore`/`VikingStore` are both usable as
reference implementations of real, different shapes (local file vs. remote HTTP client).

**5. Nothing else new found in the growth-surface modules re-read this round for Q2
(SOLID/KISS).** Read `eval/cost.py`, `eval/golden.py`, `eval/trajectory.py`,
`mcp/__init__.py`, and `server/__init__.py` in full looking for a *different* kind of
duplication or complexity issue than the retry-loop parity round 2 already checked. None
of these files carry the sort of near-duplicate logic `dispatch.py`/`lg/runtime.py` are
deliberately allowed given `test_parity.py`'s coverage — `cost_per_success`'s Wilson-score
machinery is reused (not reimplemented) by `run_golden_set`, exactly as both modules'
docstrings claim, and no other pair of functions in this list showed copy-pasted logic
worth flagging. This does not mean the modules are flawless (`lg/runtime.py` at 1125
lines is large by this codebase's own IDL-13-style standards, though it is not capped the
way `run.py`/`dispatch.py` are) — only that the *specific* ask (a different duplication
bug than the retry loop) turned up nothing concrete enough to report as a finding.

---

# V. Answering the five questions directly

**1. Is poka-yoke actually ensured?** `06 §A` row 6 is a real miss — a flagship
"tier 3, industry-wide shortcoming, closed" row whose own cited mechanism has zero
footprint in the current source (H-8). Row 2's "checked by" wording is mildly overstated
(H-9) but its mechanism holds. The four rows round 2 spot-checked (1, 4, 5, 7) and the
one this round independently reproduced as a control (S-27/row-adjacent, read+write)
still hold. So: mostly yes, with one row (6) that should not currently be counted as
tier 3 at all, and one (2) whose evidence column overclaims slightly.

**2. SOLID / clean code / KISS.** No new duplication/complexity finding rose to
reportable severity this round (§IV.5) — the specific ask (a *different* kind of
duplication than the retry loop) came back clean. This is the honest "nothing new" result
for this axis, not an exhaustive clean bill for the whole codebase.

**3. Core/extension separation.** The seam boundary itself still holds (no
`isinstance`-on-a-concrete-type coupling found, consistent with round 2's own check).
The one real crack found from a genuinely third-party-shaped angle is H-8: a `Store`
author following the *documented* contract (`docs/04 §5.1`) faithfully still has no
structural signal, ever, if they get the `external`-classification convention wrong —
the seam's public contract is clear prose with no enforcement behind it for this one
axis, which is exactly the "core can be written with zero knowledge of a concrete
implementation, but does the concrete implementation have anything checking IT" question
Q3 asks. `Policy`'s sync-only constraint is real friction but honestly documented
(§IV.3) — not the same category of gap.

**4. Stability + auditability of the core.** H-7 is a genuine, reproduced answer to this
round's specific question ("does anything let a safety-relevant decision happen without
going through the taint tracker at all"): yes, for two `external`-effect tools in one
batch on the classic backend — the taint tracker computes the correct label and even
emits `TAINT_RAISED`, but the enforcement point that should consume it for the *second*
call in the same batch is never invoked. This is a different mechanism and a different
failure mode than H-2 (a fact that never reaches an event) — here the fact does reach the
event bus correctly, but a downstream gate simply does not exist for this one code path.
The LangGraph backend does not share this gap, for a structural reason (it never
parallelizes), which is itself worth recording as a form of backend divergence beyond
what round 2's retry-loop check covered.

**5. Extension flexibility.** No new friction found beyond what's already honestly
documented (`Policy`'s sync constraint, §IV.3) or already recorded as a real but narrow
gap (H-8's "nothing enforces the convention," which is a poka-yoke finding as much as an
extension-friction one). No seventh seam candidate found; none of the five seams walked
this round required reading source beyond the documented contract, except for exactly the
one convention H-8 names.
