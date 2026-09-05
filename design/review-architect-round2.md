# Adversarial review, round 2 — the M6-M10 growth surface after its own fixes

**Commit reviewed:** current working tree, immediately after the 17-commit sequence
`caf75fc..7e57ee1` that closed every finding in
[`review-architect.md`](review-architect.md) (`G-1`..`G-17`).

**In scope, same as round 1:** `mcp/__init__.py`, `lg/*`, `eval/*`, `server/__init__.py`,
`sandbox.py`, `workspace.py`, `tasks.py`, `progress.py`, `retry.py`, `budget/ledger.py`,
`middleware.py`, `dispatch.py`, `agent.py` — plus, per this round's tasking,
`docs/02-architecture.md` and `design/06-poka-yoke-matrix.md` themselves, as documents
under review rather than as ground truth.

**Explicitly out of scope for re-litigation:** `G-1`..`G-17`. Each was independently
re-verified against the actual code (not the commit message) before being accepted as
closed — see the "G-fix verification" table below — and none is re-reported here except
where the verification turned up a **residual** gap the fix's own commit message and
docstring do not claim to have closed. Those residuals are new findings (`H-` prefix,
continuing past `G-`/`S-`/`K-`/`N-` already in use in this repo's design docs) and are
labeled as continuations of their `G-` finding, not as new discoveries from nothing.

**Method note — baseline, run before touching anything:**

```
python -m pytest -q         -> 864 passed, 94 warnings, 32.5s
ruff check src tests examples  -> All checks passed!
mypy src                        -> Success: no issues found in 72 source files
```

Green, as expected — this is the baseline the findings below are checked against, not a
claim any of them is already caught by a red test.

**This file fixes nothing.** It only records findings, per this round's tasking.

---

## G-fix verification — what round 1 actually closed

Each of `G-1`..`G-17` was read against the current source, not trusted from the commit
message. All 17 hold as fixed, in the narrow sense that the exact scenario round 1 built
no longer reproduces:

| id | still holds? | evidence |
|---|---|---|
| G-1/G-2 | yes | `run_tests`/`refresh_codebase_docs` are `effect="danger"`; `run_tests`/`git_diff`'s `target`/`path` now call `me.path(...)` (`tools/code.py:280,296`) |
| G-3 | yes | `create_app(agent, *, authenticate, ...)` — required kwarg, no default; every route wrapped in `_guarded()` (`server/__init__.py:399-420`) |
| G-4 | yes | (not re-run in this round; round 1's fix commit bounds `ast.Pow`, out of this round's active file list) |
| G-5 | yes | `TaskLedger.add`/`set_status` hold `_lock_for(store, key)` around the full read-modify-write (`tasks.py:151,163`) — reproduced round 1's 10-concurrent-`add()` scenario against the fixed code: all 10 tasks survive |
| G-6 | yes | `signature(..., declared_keys=schema_of(...))` filters to declared tool parameters before hashing (`progress.py:98-119`); reproduced round 1's nonce scenario: stall now fires at step 6 exactly as the control does |
| G-7 | **partially** — see **H-2** | `isolation: Literal[...]` is a real declared attribute now (`sandbox.py:40,53,96,127`); but see H-2 for what this does not do |
| G-8 | **partially** — see **H-1** | `settle_worst_case()` exists and is called on total exhaustion (`run.py:141`); but see H-1 for the case it does not cover |
| G-9 | yes | `Agent(allowed_hosts=None)` fires a `UserWarning` naming the exact risk, confirmed live in this round's own baseline pytest run (94 warnings, all this one) |
| G-10 | yes, as documented | `06 §C`'s new row states the single-worker limitation plainly; no code fix was claimed or attempted, matching G-10's own "should fix" framing (a `Store`-backed registry is a larger redesign) |
| G-11 | yes | `resolve_approval` returns HTTP 400 with `"the tool will NOT run"` before ever calling `registry.resolve_approval` when `require_approval_evidence` is set and evidence is absent (`server/__init__.py:388-393`) |
| G-12 | yes | `classify_mcp_tool` appends a `blake2b(identity, tool.name)` digest when the slug differs from the joined input (`mcp/__init__.py:178-184`) |
| G-13 | **partially** — see **H-6** | `reviewed_tools`/`tool_fingerprint()` exist and are wired into `_effect_for` (`mcp/__init__.py:140-155`); but see H-6 for what "the default changes nothing" means in practice |
| G-14 | **partially** — see **H-3** | `run_golden_set(..., total_budget=...)` exists and stops the sweep early, marking `budget_exhausted` (`eval/golden.py:93-139`); but see H-3 |
| G-15 | **partially** — see **H-5** | `_check_subagent_safety` now refuses construction when a parent has `approve=` and a child does not (`agent.py:912-923`); but see H-5 for what the check does not verify |
| G-16 | yes | `_SUSPICIOUS_PERCENT` narrowed to `%2e`/`%2f`/`%5c`/`%00`; a bare `%` (`"50% off.txt"`) now passes `confine()` |
| G-17 | yes | `MiddlewareHookError` is a distinct exception, caught before the generic tool-failure branch in both `dispatch.py:306` and `lg/runtime.py:652`, reported `where="middleware"`, never retried |

Six of seventeen close **less** than their own fix commit's docstring claims. That six is
this round's real yield — not new attack surface, but the gap between what a "Fixed" fix
actually fixed and what its own text says it fixed, which is exactly the discipline this
document exists to apply a second time.

---

## Summary table

| id | severity | one line |
|---|---|---|
| H-1 | severe (continuation of G-8) | `with_provider_retry`'s success path still settles only the winning attempt's real usage — a call that times out twice and succeeds on the third real provider call is billed, in the `Ledger`, as if it were made once |
| H-2 | should fix (continuation of G-7) | `06 §C`'s own row for `Sandbox.isolation` claims "an audit event can now show whether a call ran none/process/container" — no event anywhere in the codebase carries `isolation`; `ToolSpec`/`TOOL_FINISHED` have no path to it at all |
| H-3 | should fix (continuation of G-14) | `benchmark()` — the sibling function G-14's own finding named alongside `run_golden_set()` — still has no aggregate ceiling; only `run_golden_set` got one |
| H-4 | should fix (documentation) | `docs/02-architecture.md`'s own intro sentence and layer diagram still say "five" plugin seams and its §5 module map is missing 13 real modules/packages and names 6 files that do not exist, confirmed against the actual `src/harness/` tree |
| H-5 | worth noting (continuation of G-15) | The sub-agent `approve=` construction check verifies a callback is *present*, not that it does anything — a child can pass `approve=lambda **_: True` and satisfy the guard while rubber-stamping every decision the parent's real approver exists to gate |
| H-6 | worth noting (continuation of G-13) | `reviewed_tools=None` (the default) leaves S-9/G-13's rug-pull window exactly as open as before the fix — documented honestly inline, but the vulnerable configuration is still what an operator gets by not doing anything extra |

Two hypotheses this round specifically tasked ("check whether G-7's `06 §C` framing still
holds," "check whether the two run backends have drifted again the way G-17 needed
fixing") were investigated and **refuted** — recorded in §IV, not as findings.

---

# I. Severe

## H-1 — G-8's fix covers the failure path; the success-after-retry path is unbilled

**Files:** `src/harness/retry.py::with_provider_retry` (`:91-124`),
`src/harness/run.py` (`:127-146`), `src/harness/budget/ledger.py::settle`/
`settle_worst_case` (`:185-199`, `:269-292`).

**The claim under test.** G-8's own fix commit and `06 §C`'s cross-reference both frame
the fix in terms of "retry-exhausted" calls: `overshoot`'s docstring (`ledger.py:178-183`)
now reads *"bounded by one call, TIMES the retry attempts actually made"* — a sentence
whose only cited mechanism is `settle_worst_case()`, called from exactly one place,
`run.py:141`, inside the `except Exception as exc:` branch that runs when
`with_provider_retry` gives up entirely.

**What that branch does not cover.** The ORIGINAL G-8 finding named two consequences, not
one: *"A `ProviderTimeout` is a client-side observation, not a server-side one... Four
such attempts followed by a success bill four full calls to the account and one to the
`Ledger`"* — that is the success path, and the fix commit's own title
(`ff4843e G-8: settle worst-case spend on retry-exhausted model calls`) says, accurately,
which path it touched. The success path was never revisited.

**The scenario, run against this commit.**

```python
class FlakyThenOkPriced:
    def price(self, m): return pricing.price("claude-haiku-4-5")   # real, non-zero rates
    async def complete(self, request, *, on_delta=None):
        self._n += 1
        if self._n <= self._fail:
            raise ProviderTimeout("chaos timeout")
        return await self._inner.complete(request, on_delta=on_delta)

p0 = FlakyThenOkPriced([...], fail_times=0)   # succeeds on the 1st real call
a0 = Agent(provider=p0, model="claude-haiku-4-5"); r0 = a0.try_run("go")

p2 = FlakyThenOkPriced([...], fail_times=2)   # 2 real timeouts, succeeds on the 3rd
a2 = Agent(provider=p2, model="claude-haiku-4-5"); r2 = a2.try_run("go")
```
```
control (0 failures) cost: $0.0002   calls made: 1
2-timeouts-then-success cost: $0.0002   calls made: 3
```

Three real calls to the (simulated) vendor, identical `Ledger.spent` to a single call.
`with_provider_retry` returns only `fn()`'s final result on success — it never surfaces
how many attempts preceded it (`attempts_made` is set only on the exception raised at
`retry.py:119`, on the failure path) — so `run.py:145`'s `self._l.settle(reservation,
resp.usage, price)` has no way to know two earlier real calls happened, and bills exactly
one.

**Why this is the more likely case in production, not the edge case.** `MAX_ATTEMPTS = 5`
exists precisely because transient failures are expected to often succeed on retry — a
provider having a rate-limited or momentarily-unavailable minute, then recovering, is the
*modal* outcome `retry.py` was built for (docs/10 §3's promise is about exactly this
case). The failure-exhaustion path G-8 fixed is the rarer tail; the success-after-N-
attempts path, which is still silently underbilled, is the common case the whole module
exists to handle gracefully.

**Why `06 §C`'s framing invites missing this.** Nothing in `06`'s own text distinguishes
the two paths — `overshoot`'s docstring says "times the retry attempts actually made"
with no qualifier that this only applies once every attempt has failed. A reader who
checks `06`'s claim by testing the retry-exhaustion case (as this round's G-fix
verification table did first) gets a true result and stops there.

**Minimal fix.** Either (a) `with_provider_retry` returns `(result, attempts_made)` or
sets `attempts_made` as an attribute on the successful call's own object so `run.py` can
call `settle_worst_case` for `attempts_made - 1` failed attempts plus one real `settle()`
for the successful one, or (b) accept the actual invariant is "money is bounded by one
call except on total exhaustion" and say so explicitly in `overshoot`'s docstring and in
`06 §C`, rather than the current unqualified "times the retry attempts actually made."
Either is a small change; the point is the current state matches neither text.

---

# II. Should fix

## H-2 — `06 §C`'s own claim about `Sandbox.isolation` overstates what the code does

**Files:** `design/06-poka-yoke-matrix.md:72`, `src/harness/sandbox.py` (`Isolation`
`:40`, `Sandbox` Protocol `:51-56`), `src/harness/tools/__init__.py::ToolSpec` (`:114`,
no sandbox/isolation field of any kind), `src/harness/dispatch.py`
(`TOOL_STARTED`/`TOOL_FINISHED` emit sites, `:287-288`, `:300-302`, `:373-374`).

**The claim under test.** `06 §C`'s row on `Sandbox.isolation`, written as an honest
tier-2 admission (exactly the kind of row this document was told to trust as accurate):
*"the field makes the boundary **inspectable** (an audit event can now show whether a
call ran `"none"`, `"process"`, or `"container"`)"*. This is presented as the one thing
the fix DOES buy, distinct from what it doesn't (raising the ceiling).

**What is actually true.** `isolation` is a real, declared attribute on `InProcess`
(`"none"`) and `Subprocess` (`"process"`) — that half of G-7's fix is solid and confirmed
by `tests/test_m7_t73_t74_sandbox.py`. But grep across the entire source tree for every
event emission in `dispatch.py` — the only place `TOOL_STARTED`/`TOOL_FINISHED` are ever
built — turns up zero occurrences of `isolation`. The reason is structural, not an
oversight of one call site: `ToolSpec` (`tools/__init__.py:114`), the object `dispatch.py`
actually has in hand when it emits an event, carries no reference to a `Sandbox` at all.
A `Sandbox` only exists inside `CodeTools` (`tools/code.py:100-108`), consumed entirely
inside that class's own `_run()` method; nothing hands it, or its `isolation` value,
back out to the generic dispatch/event layer that every tool call passes through. There
is no code path by which an audit event **could** show `isolation` today, let alone one
that does.

**Confirmed directly, not by absence alone.** `grep -rn "isolation" src/harness/*.py
src/harness/*/*.py` outside `sandbox.py` and its own docstring cross-references returns
only comments in `tools/code.py` telling a reader to check the attribute themselves —
never a line that reads it programmatically. `policy/builtin.py` has no
`RequireIsolation`-shaped policy either, so the fix's own "Minimal fix" text (in
`review-architect.md` G-7 — *"readable by a policy... lets an operator write
`RequireIsolation("container")` as a policy"*) is aspirational prose that was never built,
and `06 §C`'s rewrite of that same claim into "an audit event can now show..." asserts a
narrower version of the same unbuilt thing as if it existed.

**Why this matters more than a wording nitpick.** `06`'s own §D states the document's
entire discipline: *"A tier-2 mechanism must say plainly that it's tier 2."* This row
does say plainly that isolation doesn't raise the ceiling — but it also states, as fact,
an observability property that isn't there. An operator reading this table to decide
whether they can build a dashboard or an alert on "which tool calls ran with real
isolation" would conclude the data already exists to query. It doesn't.

**Minimal fix.** Either wire it for real — thread `getattr(tool_impl, "isolation",
"none")` onto `TOOL_FINISHED` for tools that expose a sandbox (which needs `ToolSpec` or
the `@tool` decorator to optionally carry that value, a small but real addition, not a
one-line fix) — or correct `06 §C`'s row to say what's actually true today: `isolation` is
readable via `getattr()` on a `CodeTools` instance directly, is not on any event, and
`RequireIsolation` is a suggested future policy, not a shipped one.

## H-3 — G-14's fix reached `run_golden_set`; `benchmark()` — named in the same finding — did not

**Files:** `src/harness/eval/golden.py::run_golden_set` (`:93-139`, fixed),
`src/harness/eval/benchmark.py::benchmark` (`:51-85`, unchanged).

**The claim under test.** The original G-14 finding named both functions in the same
sentence: *"`run_golden_set()`/`benchmark()` have no aggregate ceiling: N cases × the
agent's per-run `Budget`, with nothing to stop it partway."* The fix commit
(`baee335 G-14: aggregate budget for run_golden_set (total_budget=)`) title says, again
accurately, which one it touched.

**What's actually in `benchmark()` today.** Read in full: `benchmark(run_fn, *, n=20,
concurrency=1)` takes an **opaque** zero-arg callable — by design, per its own docstring,
*"decoupled from `Agent` construction so a caller benchmarking a specific scenario... does
not have to route it through this module's API."* That decoupling is a real and
reasonable design choice, but it is also precisely why `benchmark()` cannot see a
`Budget`, an `Agent`, or a `Result.cost` to build a shared `Ledger` against the way
`run_golden_set` (which does take an `Agent` directly) now can. Nothing was added here at
all: `benchmark(lambda: agent.atry_run("go"), n=1000, concurrency=50)` against a
budget-bearing agent spends exactly as unboundedly today as it did before G-14's fix
landed — the exact scenario the original finding described, unchanged, one function away
from the one that got fixed.

**Why this isn't merely "the same design constraint, so no fix is possible."** A caller
who reads `06 §A` row 4 ("Caps step count, not money", tier 3 at construction) and then
reads `run_golden_set`'s new `total_budget=` parameter has a reasonable expectation that
the module's OTHER aggregate-runner has the same property, especially since the original
finding grouped them as one failure class. It doesn't, and nothing in `benchmark()`'s
docstring says so.

**Minimal fix.** Either (a) accept an optional `on_result: Callable[[object], None]` or
a `cost_of: Callable[[object], Money]` hook the caller can use to charge a shared `Ledger`
themselves between iterations — keeping `benchmark()` decoupled from `Agent` while still
giving it a stop condition — or (b) a one-line addition to `benchmark`'s own docstring
naming this as a known, currently-unaddressed gap, the same honesty `06 §C` already
extends to five other tier-2 limits.

## H-4 — `docs/02-architecture.md`'s own staleness, confirmed against the real tree

**Files:** `docs/02-architecture.md` (intro `:10`, layer diagram `:26`, module map
`:196-268`), `src/harness/` (actual tree).

This round's tasking flagged this doc's staleness as already found by a prior read-only
pass and asked for independent confirmation before counting it. Confirmed, directly:

**§1/§2 still say "five."** Line 10: *"Five things are pluggable; everything else is
core."* Line 26, the L1 layer of the diagram: *"ModelProvider · Store · Policy ·
Exporter ← the 5 plugin seams"* — `Sandbox` is simply absent from both, even though it is
a real, shipped seam (ADR-047, M7/T-7.3) with two implementations
(`sandbox.py::InProcess`/`Subprocess`) exactly like the other five.

**§4, twenty lines later, already says "six" — and says so self-awarely.** Line 174:
*"**Six seams** (`Sandbox` added M7/T-7.3, ADR-047 — the table above was updated then
but this sentence wasn't; caught in the T-9.1 pass...)."* So the doc has already caught
and fixed its own staleness once, in §4, in a way that explicitly did not propagate to §1
or §2 — the fix fixed the sentence next to the table it was editing and stopped there.

**§5's module map is missing roughly half the real package.** Every one of these exists
in `src/harness/` today and appears nowhere in the module map: `mcp/`, `lg/`, `eval/`,
`server/`, `sandbox.py`, `workspace.py`, `session.py`, `tasks.py`, `progress.py`,
`retry.py`, `idempotency.py`, `dispatch.py`, `_value.py`. Checked file-by-file against
`find src/harness -name '*.py'` — 13 real modules/packages absent, not an estimate.

**§5 also names six files that do not exist**, confirmed with `ls`: `_typing.py` (the
real file is `_value.py`, doing an unrelated job — a `@value` decorator, not type
aliases), `tools/invoke.py` (execution actually lives inline in `dispatch.py`),
`context/caching.py` (no such file; `cache_control` placement is not broken out),
`observe/bus.py` and `observe/redact.py` (both folded into `observe/events.py` and
`dispatch.py`'s own `redact` import respectively — no dedicated files), and
`tools/builtin/shell.py`/`tools/builtin/math.py` (the real files are `builtin/calc.py`
and `builtin/files.py`/`builtin/web.py`; there is no `shell.py` — `run_command`/danger
tools live in `tools/code.py`, not `builtin/`).

**Why this is worth a finding rather than a typo note.** `docs/02-architecture.md §4`'s
own three-part seam test is the document this round's tasking asks to verify Q3 against,
and the module map is the document a third-party extension author would read first to
find out where a `Store`/`Policy`/`Tool` implementation is supposed to live. A map
missing the very packages (`mcp/`, `lg/`) that demonstrate two of the six seams in
practice undercuts exactly the credibility the doc is trying to establish in §4.

**Minimal fix.** A straight sync pass: add the 13 missing modules/packages to §5 with
one line each (most already have a natural docstring one-liner to lift, e.g. `mcp/`'s
own module docstring), correct the 6 wrong filenames, and change "Five" to "Six" in both
§1 and the §2 diagram — the same edit §4 already made to its own sentence, applied to
the two spots it didn't reach.

---

# III. Worth noting

## H-5 — G-15's construction check verifies an `approve=` callback exists, not that it approves anything

**Files:** `src/harness/agent.py::_check_subagent_safety` (`:877-923`,
specifically `:912-923`).

**The mechanism, and its own documented limit.** G-15's fix docstring is explicit about
the choice it made: *"this does not compare them for equality, only that one exists,
matching how `safety` is only ever rank-compared, not required to be identical."* For
`safety`, that's sound — safety levels are an ordered enum, and rank-comparison is the
right operation. `approve=` is not an ordered value; it's an arbitrary callback, and
"exists" is the only property a construction-time check can cheaply verify about it.

**The scenario.** A parent configured `Agent(approve=ask_a_real_human_over_slack, ...)`
delegates to a child sub-agent configured `Agent(approve=lambda call, **kw: True, ...)`.
`_check_subagent_safety` sees `parent_approve is not None and child.approve is None` —
false, because `child.approve` is not `None`, it's a callable — and construction
succeeds. Every `write`/`danger` decision inside the child that would have gone to the
parent's real human approver is now silently rubber-stamped by a callback that never
asks anyone, and the guard this fix added has nothing to say about it, because from a
construction-time check's point of view a rubber-stamp callback and a real one are the
same shape.

**Why this is "worth noting," not "should fix."** No static check can distinguish a
callback that behaves like human-in-the-loop approval from one that doesn't — this is an
inherent limit of any interface built around a caller-supplied function, not a design
flaw specific to this fix. It is recorded here only because `06 §D`'s own discipline says
a mechanism should state its actual boundary, and `_check_subagent_safety`'s docstring
already does this honestly for the `safety`-rank half of its check but does not say the
equivalent sentence for the `approve`-presence half: something closer to *"this closes
the 'no approver at all' hole; it cannot and does not verify the child's approver behaves
as restrictively as the parent's."*

## H-6 — G-13's `reviewed_tools=None` default leaves S-9's rug-pull window exactly as open as before the fix, by design

**Files:** `src/harness/mcp/__init__.py` (`McpServerPolicy.reviewed_tools` `:67-84`,
`_effect_for` `:140-155`).

**Already honestly documented — recorded here only because the tasking asks whether
`review-security.md` S-9's remainder is still open, and the honest answer is "yes, in the
default configuration."** The field's own comment states plainly: *"`None` (the default)
changes nothing — `trusted` still governs hint-based classification exactly as before
this field existed."* `_effect_for` (`:151`) skips the pinning check entirely when
`reviewed_tools is None`, falling straight through to `_effect_from_hints` for any
`trusted=True` server — which is the literal S-9/G-13 scenario: the same server, same
endpoint, same operator-reviewed label, publishing a re-hinted tool between two
`connect()` calls, still auto-classified from the (possibly rug-pulled) hint.

This is not a hidden regression — it's an opt-in fix, following the same
"unrestricted-must-be-explicit" convention `Budget(usd=None)` and `total_budget=` (H-3)
both use, and the docstring says so in the same sentence a reader would need to notice
it. It is recorded as a finding rather than folded into the "checked, correctly handled"
section below because an operator who sets `trusted=True` without also discovering
`reviewed_tools=` — which nothing in `McpServerPolicy`'s construction enforces or warns
about — gets the exact configuration `06 §A`'s own tier-vocabulary would call tier 1 for
this one axis: the mechanism exists, but only for whoever remembers to turn it on. A
`UserWarning` on `trusted=True` with `reviewed_tools=None`, the same shape G-9 already
uses for `allowed_hosts=None`, would close the gap between "the fix exists" and "the fix
is reached without extra effort."

---

# IV. Checked and correctly handled — not findings

**1. The two run backends' tool-retry loops have not drifted again.** This round's own
tasking asked whether the class of bug G-17 fixed (`dispatch.py` and `lg/runtime.py`
silently diverging on how they handle one non-tool failure mode) recurs anywhere else.
Traced both `_invoke`'s (`dispatch.py:286-320`) and the LangGraph node's
(`lg/runtime.py:580-661`) full retry loop side by side: `attempts = MAX_ATTEMPTS if
retryable else 1`, the same `execute_once`/idempotency-key shape, the same
`MiddlewareHookError`-before-generic-exception ordering, the same `where=`/`retryable=`
event fields on the resulting `ERROR_RAISED`/`TOOL_FINISHED`. Both sides carry their own
`# parity with dispatch.py::_invoke` comments at every point that matters, and
`tests/test_parity.py` states each rule once and runs it against all three call shapes.
No drift found. The hypothesis is refuted.

**2. `Sandbox.isolation`'s type-level honesty holds even though its wiring doesn't
(H-2).** `getattr(sandbox, "isolation", "none")` correctly falls back to `"none"` for a
third-party `Sandbox` written before G-7 existed and correctly reads `"process"`/`"none"`
off the two shipped implementations — verified directly against
`tests/test_m7_t73_t74_sandbox.py`'s own fixture. What's missing (H-2) is downstream of
this, not in it.

**3. The seven tier-3 rows in `06 §A` were spot-checked, not merely re-read.** Row 1
(`Actor` has no `Model` variant — confirmed, `policy/decision.py:30-31`), row 4
(`Budget(usd=None)` warns, doesn't silently open — confirmed live in this round's own
pytest baseline), row 5 (idempotency has no off-switch — no enum, flag, or kwarg
anywhere in `idempotency.py`/`dispatch.py` disables `execute_once` for `write`/`danger`)
and row 7 (`effect` has no default on `@tool` — confirmed, `tools/__init__.py`) all still
hold exactly as claimed. No defeat found for any of these four; the other three (rows
1b, 2, 3, 6) were read but not independently re-derived this round, consistent with this
round's budget being spent on the six growth-module fixes instead.

**4. Core/extension separation (Q3) holds; no hidden coupling found beyond the doc
staleness already recorded as H-4.** Grepped every `.py` file under `src/harness/` for
`isinstance(..., <ConcreteImplementation>)` against `Store`/`ModelProvider`/`Policy`/
`Sandbox`/`Exporter` — zero hits. The only concrete-type imports in files this round
touched are default-value conveniences (`dispatch.py`, `lg/runtime.py`, and
`server/__init__.py` all import `InMemoryStore` only to hand out a default `Store`
instance when the caller supplies none — never to branch behavior on the type), and
`tools/code.py` importing `Subprocess` as `CodeTools`'s own default `sandbox=` — a bundled
tool choosing a sensible default for itself, not core code special-casing an extension.
Traced one concrete example per seam (a third-party `Tool` via `@tool`, a `Store`
subclass, a `Policy` subclass, an `Exporter`, a `Sandbox`) — each requires touching only
files under its own seam, never a file this round or round 1 classified as core.

**5. Auditability (Q4, first half) holds: policy DENY, taint raise, and budget block
each really emit an event on both backends**, not merely per the taxonomy's own claim.
Traced: `POLICY_DECIDED` at `dispatch.py:175` and three sites in `lg/runtime.py`
(`:359`, `:422`, `:546`); `TAINT_RAISED` at `dispatch.py:299` and `lg/runtime.py:700`;
`BUDGET_EXHAUSTED` at `run.py:98` and `lg/runtime.py:256`. All present on both backends
for this round's own traced scenarios. No gap found.

**6. `Agent`'s frozen/immutable design (Q4, second half) was not defeated by any seam
active in this round's scope.** `with_middleware()`/`with_()` construct new `Agent`
instances rather than mutating the existing one (confirmed by object-identity checks in
round 1's own "checked, correctly handled" §IV.1, re-read here); `TaskLedger`/
`ProgressLedger`'s mutable state lives on objects the `RunEngine` builds fresh per run
(`run.py:37,44`), never on `Agent` itself; `McpServerPolicy`/`Sandbox`/`Store` instances
passed into an `Agent` are held by reference but nothing this round found writes back
into them in a way that would leak across concurrent runs sharing one frozen `Agent`.

**7. Extension flexibility (Q5) — no missing seam found beyond what `06`/`02` already
name.** Read `Sandbox`, `Policy`, `Store`, `ModelProvider`, `Exporter` as a third party
would: each is a small `Protocol`/ABC with 1-4 methods and an existing second
implementation to copy from. The one candidate that came up during this round's reading
— whether `CodeTools`'s hardcoded `test_command=("python", "-m", "pytest", "-q")` and
git-specific tool set should themselves be pluggable — was set aside as out of scope:
`CodeTools` is a bundled, opinionated tool collection built ON the `Tool` seam, not
itself a seam, and a real integration wanting a different test runner already has the
actual escape hatch (`test_command=` is a constructor parameter, not hardcoded). No
finding.

---

# V. Answering the five questions directly

**1. Is poka-yoke actually ensured?** Six of the seventeen closed findings (the
"partially" rows in the G-fix verification table) close less than their own commit
message or the current `06`'s own text claims — H-1 and H-2 are the two with real
teeth; H-3, H-5, H-6 are honestly-scoped residuals more than defeats. None of the seven
`06 §A` tier-3 rows spot-checked this round (see §IV.3) were actually defeated. The
matrix's overall claim — most invariants are tier 3 and hold — is still substantially
true; its self-grading on the specific rows this round touched (G-7/G-8/G-14) is
optimistic by one notch each.

**2. SOLID / clean code / KISS.** The one concrete duplication risk this round's tasking
named — `dispatch.py` and `lg/runtime.py` as two hand-written implementations of one
tool-retry contract — was traced side by side and found NOT to have drifted (§IV.1); the
codebase's own parity comments and `test_parity.py` are doing real, demonstrated work,
not decoration. `run.py`/`dispatch.py`'s line-count ceilings (`examples/proof.py`'s
`SIII` check, 251/252 non-comment lines) are enforced and currently exactly at their
ceiling, not merely aspirational — checked by running the same count this round. No
over-engineering or needless abstraction was found in the modules this round covered;
`benchmark()`'s deliberate decoupling from `Agent` (the root cause of H-3) is a
reasonable design choice paying a real cost, not an accidental one.

**3. Core/extension separation.** Confirmed `docs/02-architecture.md §4`'s six-seam table
is accurate against the real tree; §1/§2/§5 are stale in ways confirmed independently in
H-4. No hidden coupling from core to a concrete extension implementation was found
(§IV.4) — the seam boundary itself, as opposed to its documentation, holds.

**4. Stability + auditability of the core.** Policy DENY, taint raise, and budget block
all genuinely emit events on both backends (§IV.5) — this was traced, not assumed. The
two run backends were checked for the specific class of drift G-17 represents and found
not to have re-drifted (§IV.1). `Agent`'s immutability holds against every seam active in
this round's scope (§IV.6). The one real crack in "every safety-relevant decision is
captured" is H-2: `Sandbox.isolation`, a real safety-relevant fact about how a tool call
ran, reaches no event at all, contrary to `06`'s own claim that it does.

**5. Extension flexibility.** All six seams remain ergonomic to extend — each is a small
Protocol/ABC with an existing second implementation to copy (§IV.7). No hardcoded
assumption was found that should obviously be a seventh seam; `CodeTools`'s own
configuration knobs (`test_command=`, `sandbox=`, `env=`) already cover the one candidate
considered.
