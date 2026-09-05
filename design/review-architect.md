# Adversarial review — architecture, of the M6-M10 growth surface

**Commit reviewed:** `d5ddb2dff20b23950ff55157de09129c380263a5`
(branch `claude/ai-agent-harness-design-ti5vk3`).

**In scope:** the modules added during and after the M6-M10 roadmap, i.e. everything
that postdates both [`review-kiss.md`](review-kiss.md) and
[`review-security.md`](review-security.md) — `workspace.py`, `sandbox.py`,
`tools/code.py`, `server/__init__.py`, `mcp/__init__.py`, `idempotency.py`, `tasks.py`,
`progress.py`, `retry.py`, `middleware.py`, `eval/*`, `observe/otel.py`, and
`tools/builtin/{calc,files,web}.py`.

**Out of scope:** the core loop (`run.py`, `dispatch.py`, `agent.py`, `policy/*`,
`budget/*`, `lg/*`) except where a growth module's behaviour depends on it — those are
already covered by 58 recorded `S-`/`K-` findings. Two of those (`S-21`, `S-28`) were
independently re-checked as a calibration control; see §0.

**Posture:** identical to `review-security.md` — assume wrong until a scenario is built.
Every finding below carries either a command that was actually run against this commit or
a traced call path. Where I had suspicion and no scenario, it is in §V, not in the table.

**Method note:** the full suite is green at this commit (`799 passed in 44.95s`). Every
finding below is live in a passing test suite; none is caught by an existing test.

**ID prefix collision, stated up front.** `G-` was assigned for this review ("Growth
roadmap"). `review-kiss.md`'s *"What's already well-balanced — DON'T CUT THESE"* table
already uses `G-1`…`G-16` for a completely different meaning. That collision is exactly
`K-13`'s failure mode one namespace over. `07-risks-and-open-issues.md` and
`06-poka-yoke-matrix.md` are both clean of `G-`, so nothing here breaks, but whoever
reconciles this file should consider renaming this series to `GR-` — recorded here rather
than fixed unilaterally, since renaming is the orchestrator's call.

**This file fixes nothing.** It only records findings.

---

## 0. Calibration control — two already-"Fixed" findings, re-checked

Re-verified independently against today's code before touching anything new, to establish
that this review's bar matches the project's actual standard rather than a different one.

**S-21 (`Ledger` snapshot/restore) — I reach the same "Fixed" verdict.** `blocked` is in
both `snapshot()` (`budget/ledger.py:111`) and `restore()` (`:125`), and every path that
sets `_blocked` (`settle`, `release`, `charge`) is round-tripped. The one thing I probed
that the original finding did not: `restore()`'s `except (ArithmeticError, TypeError,
ValueError): pass` can leave `_blocked` unrestored if an *earlier* field raises — but the
only field that can raise is `steps`/`spent`, and in that state `remaining_usd()` is
already negative so `reserve()` refuses anyway. Fail-closed either way. No finding.

**S-28 (sub-agent ASK) — I reach the same "already correct" verdict, with a caveat that
became a finding of its own.** `PolicyEngine.resolve()` (`policy/engine.py:57-64`) is
reached identically by parent and child; with `approve=None` a `danger` tool DENYs and
everything else ALLOWs, so a child spawned inside the parent's `tools` node can never
block waiting for a human. The "no hang" claim holds exactly as `07`'s §1 states. What
`07` does **not** state is that `approve=` is not inherited by a sub-agent while `safety`
is — see **G-15**.

---

## Summary table — id · tier · one line

Tier uses `06-poka-yoke-matrix.md`'s own vocabulary where a mechanism is claimed, and
review-security.md's severity vocabulary otherwise.

| id | tier / severity | one line |
|---|---|---|
| G-1 | release-blocking | `CodeTools`'s `write_source` + `run_tests` is arbitrary code execution, and BOTH are classified `write`, not `danger` — the module docstring's "importing this module never by itself creates the lethal trifecta" is false |
| G-2 | release-blocking | `run_tests(target=…)` and `git_diff(path=…)` never pass through `confine()`, so code executes **outside** the workspace root on a path `read_source` refuses with `WorkspaceEscapeError` |
| G-3 | release-blocking | An unauthenticated `POST /v1/runs/{id}/approvals/{cid}` executes a `danger` tool — the entire `Decision`/`Actor`/`AuthEvidence` apparatus (`06 §A` row 1, claimed **tier 3**) degrades to "whoever can reach the port" |
| G-4 | severe | `calculate("9**9**9**9")` — an `effect="read"` tool, floor `ALLOW`, no approval — freezes the whole process; `timeout_s`, the wall-clock budget and T-6.2 cancellation are all defeated, and the one test that covers this file blesses `**` |
| G-5 | severe | `TaskLedger`: 10 concurrent `add_task`s against a shared `SqliteStore` leave **1** task. Its own "read-modify-write is safe here" argument is scoped to one batch and silently assumed to cover concurrent runs — an R-4 regression in a new module |
| G-6 | severe | `ProgressLedger`'s `STALL_AFTER=6` is evaded forever by one varying nonce argument: 50 identical steps → 0 stalled, control → stall at 6 |
| G-7 | severe (tier 1 dressed as tier 3) | `Sandbox` is a bare `Protocol`; nothing at type or runtime level separates "isolated" from "zero isolation", and `InProcess` is not in-process — it spawns a subprocess, differing from `Subprocess` by one kwarg |
| G-8 | severe | `with_provider_retry()` makes up to **5** real provider calls under **one** `Reservation`, and a run that ends in provider failure `settle()`s nothing — `Ledger.overshoot`'s documented "bounded by one call" no longer holds |
| G-9 | should fix | `allowed_hosts=()` means deny-all and `allowed_hosts=None` means allow-all — two adjacent falsy values with opposite security meanings, separated only by a comment |
| G-10 | should fix | The Service API's headline `Idempotency-Key` feature, its run registry and `idempotency.py`'s lock are all process-local; `uvicorn --workers 4` — the ordinary way to run a Starlette app — silently breaks all three, and no docstring names multi-worker |
| G-11 | should fix | A Service-API approval that `require_approval_evidence=True` DENYs returns `{"resolved": true, "approve": true}` to the approver and the run reports `done`/`ok` — the refusal is invisible to everyone who could act on it |
| G-12 | should fix | An untrusted MCP server chooses `tool.name`, therefore chooses the slug, therefore can make `Agent(...)` construction raise `DuplicateToolError` — availability, not confidentiality, but chosen by the attacker |
| G-13 | should fix | `07`'s S-9 remainder ("ONE label re-pointed to a different endpoint") understates the gap: the **same** endpoint under a `trusted=True` label can lower `danger`→`read` between two `connect()` calls, which is S-9's original scenario verbatim |
| G-14 | should fix | `run_golden_set()`/`benchmark()` have no aggregate ceiling: N cases × the agent's per-run `Budget`, with nothing to stop it partway — shortcoming #4 reappearing one layer up |
| G-15 | should fix | A sub-agent inherits `safety` (checked) but not `approve=` (unchecked): a parent with an approver delegates its `write` tools to a child without one, and they auto-`ALLOW` |
| G-16 | should fix | `confine()` rejects any path containing `%`, so a legitimate filename (`test_100%_coverage.py`, a URL-encoded fixture) is unreachable — an over-rejection with no escape hatch |
| G-17 | should fix | A `Middleware` hook that raises is indistinguishable from a tool failure: swallowed into a tool-error result and, for `read`/`external`, retried 3× with its side effects re-run |

Eight hypotheses I tried to build a scenario for and **could not** are in §V — including
three the tasking specifically asked about. They are recorded so a future round does not
redo the work.

---

# I. Release-blocking

## G-1 — `CodeTools` hands the model arbitrary code execution, classified `write`

**Files:** `src/harness/tools/code.py` (module docstring; `write_source` `:222`,
`run_tests` `:249`), `src/harness/tools/__init__.py` `EFFECT_PROFILES`.

**The claim under test.** `tools/code.py`'s own module docstring, verbatim: *"Không có
tool `danger` nào trong module này… Nhập module này không bao giờ tự nó tạo ra bộ ba chết
người"* — "no `danger` tool in this module… importing this module never by itself creates
the lethal trifecta." Every one of its eleven tools is `read` or `write`; `EFFECT_PROFILES`
gives `write` `decision_standard = ALLOW`, so at the default `safety="standard"` with no
`approve=` callback none of them ever reaches a human.

**The scenario, run against this commit.**

```
write_source(path="test_pwned.py",
             text="import os\nos.system('id > /tmp/ctest/PWNED.txt')\ndef test_x(): pass\n")
  -> "đã ghi 68 ký tự vào test_pwned.py"
run_tests(target="test_pwned.py")
  -> "exit 0\n. [100%]\n1 passed in 0.01s"
cat /tmp/ctest/PWNED.txt
  -> uid=0(root) gid=0(root) groups=0(root)
```

Two `write`-classified tool calls, no `danger` anywhere, no approval, and pytest imports
the model-authored file at collection time and runs whatever is at module scope. The
default `test_command` is `("python", "-m", "pytest", "-q")`; the argument-injection
variants (`-p`, `--rootdir`) are not even needed — collection alone is enough, because
importing a test file *is* executing it.

**Why the "no shell string, only argv" defence doesn't reach this.** The class docstring
correctly notes that `Sandbox.run` takes `argv`, so there is nothing to inject through
`;`/`&&`. That is true and irrelevant: the model does not need shell metacharacters when
it can write the program that the trusted argv is going to execute.

**Why this is architectural, not a tool-author slip.** `00 §2`'s central idea is that
`effect` is the ONE declaration from which parallelism, retry, auto-approval and taint are
derived, and `06 §A` row 7 makes it tier 3 precisely because `effect` has no default. The
declaration here is present and wrong, and nothing in the system can notice: `06`'s
mechanism guarantees an effect is *declared*, never that it is *true*. `run_tests`'s own
inline comment reasons carefully about why it is `write` rather than `read` (cache writes,
artefacts, no parallel) and never asks the only question that matters — whether the thing
being run is model-authored.

Combined with `search_code`/`read_source` (private data in) and `Subprocess`'s
unrestricted host network (out), this is the complete lethal trifecta, assembled by
importing exactly the module whose docstring promises it cannot be.

**Minimal fix.** `run_tests` and `refresh_codebase_docs` become `effect="danger"` — they
execute code, and `danger`'s floor is `ASK` in both safety levels. If that is judged too
coarse for a test runner, the alternative is narrower and stronger: `run_tests` refuses to
execute any path whose file mtime is newer than the `CodeTools` instance, i.e. anything
this run wrote. Either way the module docstring's trifecta claim has to go — it is the
kind of sentence `06 §D` calls "a guarantee an operator *thinks* they have."

**Independently verified (this session, not just by the reviewing subagent):** read the
full file directly — `write_source`/`edit_source`/`run_tests`/`git_commit`/
`refresh_codebase_docs` are all `@tool(effect="write")`; none of the module's 11 tools is
`effect="danger"`. Confirmed as claimed.

---

## G-2 — Two `CodeTools` arguments skip `confine()`, and one of them executes code

**Files:** `src/harness/tools/code.py` (`run_tests` `:249`, `git_diff` `:265`,
`CodeTools.path` `:102`), `src/harness/workspace.py::confine`.

**The claim under test.** `CodeTools`'s class docstring, verbatim: *"Mọi đường dẫn model
đưa vào đều đi qua `confine()`"* — every path the model supplies goes through `confine()`.
Nine of the eleven tools do. `run_tests(target=…)` and `git_diff(path=…)` append the
model's string straight to `argv`.

**The scenario, run against this commit.** With a workspace root of `/tmp/ctest` and an
unrelated directory `/tmp/outside/` containing a `conftest.py` whose module body runs
`os.system("id > /tmp/outside/ESCAPED.txt")`:

```
read_source(path="../outside/conftest.py")   -> WorkspaceEscapeError      # confined
run_tests(target="../outside")               -> "exit 0 | 1 passed"       # NOT confined
/tmp/outside/ESCAPED.txt                     -> exists                    # executed
```

The same relative path, in the same run, on the same object: refused when read, executed
when passed to the test runner. `confine()` itself behaved perfectly — it was simply never
called on the argument that mattered.

**Why this is separate from G-1.** G-1 is a misclassification inside the root; this is an
escape from the root, and it survives every fix to G-1 that does not also confine
`target`. It also means `workspace.py`'s guarantee — `06 §C` row 1, honestly recorded as
tier 2 because a malicious tool can call `open()` directly — is weaker than that row
states: here it is a *carefully written* tool, in this repo, that walks around it via an
argument nobody routed.

**Minimal fix.** `target` and `path` go through `me.path(...)` like every sibling, and
`run_tests` additionally rejects any `target` beginning with `-` (option injection into a
runner whose plugin flags load arbitrary modules). The deeper fix is structural: `_run()`
is the single place every subprocess argv is assembled, so the confinement check belongs
there, applied to every argv element that names a path — a precondition at the point of
consumption, exactly the shape `I-1` took for S-2.

**Independently verified (this session):** read the full file directly. Every path-taking
tool except `run_tests` and `git_diff` opens with `p = me.path(path)` (`read_source`
`:149`, `outline` `:199`, `write_source` `:226`, `edit_source` `:235`). `run_tests`
(`:250-258`) and `git_diff` (`:266-268`) build their argv directly from the raw parameter
with no call to `me.path`/`confine` anywhere in either function body. Confirmed as claimed
— not by trusting the reviewer's prose, by reading the two function bodies directly.

---

## G-3 — Every route of the Service API is unauthenticated, including the one that approves `danger` tools

**Files:** `src/harness/server/__init__.py` (module docstring; `_bridge_approvals` `:157`;
`resolve_approval` route `:333`, `:358`).

**The claim under test.** The module docstring says so itself: *"No authentication. Every
route is open."* That honesty is genuine and matches `06 §D`'s second rule. But `06 §D`'s
*third* rule is the one that applies: **"A tier-1 mechanism is not a mechanism. If all that
exists is documentation saying don't, that row belongs in `07-risks-and-open-issues.md`."**
There is no such row. It is one bullet in one module docstring, and the module in question
is the network front door.

**The scenario, run against this commit.** A default deployment — no
`require_approval_evidence`, one `danger` tool:

```
POST /v1/runs                          {"message": "go"}          -> 202, run_id
GET  /v1/runs/{id}                     -> pending_approvals: [{"call_id":"c1","tool":"wipe"}]
POST /v1/runs/{id}/approvals/c1        {"approve": true, "approved_by": "anyone-on-the-network"}
                                       -> {"resolved": true, "approve": true}
wipe(x=1) EXECUTED.  run status: done.
```

The audit record for that execution is
`Actor.human("anyone-on-the-network", via="service-api")` — a name the approver typed
about themselves, which is precisely the failure `S-11` was opened to close and `07`
records as **Fixed**.

**Why this is worse than the gaps `06 §C` already lists, and belongs in that table.** Every
row currently in §C is a limit on how *strongly* a mechanism holds: `Workspace` is
in-process, `egress` doesn't cover allowed hosts, exactly-once needs upstream cooperation.
This is different in kind — it is the *complete removal* of the mechanism `06 §A` row 1
scores tier 3, by an actor the threat model never considered (a network peer, not the
model). `06 §A` row 1's "checked by" column reads *"the model cannot construct a
`Decision`"*, and that remains literally true; what the Service API adds is that the model
does not need to, because anyone else can construct one for it.

**Minimal fix.** Two parts, neither large. (a) `create_app(agent, *, authenticate=...)`
with **no default** — a required callable, so an unauthenticated deployment is something an
operator has to actively write (`lambda req: True`) rather than something they get by
omission. This is the same shape `Budget`, `provenance` and `effect` already use, and it
is the difference between tier 1 and tier 3 here. (b) A row in `06 §C` naming this
explicitly until (a) lands, because a gap recorded only in a module docstring is a gap the
matrix claims does not exist.

**Independently verified (this session):** read the full file directly. The docstring
literally reads "No authentication. Every route is open." `_RunRegistry.__init__` defaults
`store` to a fresh `InMemoryStore()` per instance. `_bridge_approvals` builds
`Actor.human(approved_by or "anonymous", via="service-api")` unconditionally from the
caller-supplied JSON body, with a comment explicitly noting nothing here authenticates it.
Confirmed as claimed, independently, before the reviewing subagent's output was accepted.

---

## G-4 — A twelve-character argument to the beginner's first tool freezes the process

**Files:** `src/harness/tools/builtin/calc.py`, `src/harness/tools/__init__.py:188-193`
(sync tools run via `asyncio.to_thread`), `tests/test_m5.py:377-382`.

**The mechanism.** `calc.py`'s docstring is proud of the right thing: no `eval`, so this is
not "a `danger` tool wearing a `read` label." It then admits `ast.Pow` into `_OPS`.
`calculate("9**9**9**9")` asks CPython to compute `9 ** 387420489`, an integer with
~370 million digits, inside a single C-level `long_pow` that never releases the GIL.

**The scenario, run against this commit.** Control first, then the payload:

```
calculate("2+2")                                        -> 4          (0.0s)
async with asyncio.timeout(2): await calculate.fn(expression="9**9**9**9")
   -> the 2-second timeout NEVER FIRES; nothing after it ever prints;
      the process does not exit; killed by an external 20s `timeout`, exit 124.
```

**Why the classification is the finding, not the arithmetic.** `effect="read"` derives
five behaviours from `EFFECT_PROFILES[READ]`: `parallel_safe=True`, `retryable=True`,
floor `ALLOW` in **both** safety levels, `audit_level="debug"`. So this call is
auto-approved, logged at debug, eligible to be issued several at a time by one model turn,
and — if it had failed instead of hung — retried three times. `_EFFECT_HELP` renders `read`
to a beginner as *"only looks at things."*

**Why nothing caught it.** `tests/` has no dedicated test file for `tools/builtin/*` at
all. The single test that touches `calc.py` is `test_m5.py:377`,
`test_calculate_never_evals_model_supplied_text` — and its second assertion is
`assertEqual(calculate.fn(expression="2 ** 10"), 1024)`. The only test covering this file
is the one that ratifies the operator that breaks it.

**Minimal fix.** Drop `ast.Pow` from `_OPS` (a calculator for a language model does not
need tetration), or bound it: refuse when `abs(exponent) > 64` or when
`bit_length(base) * exponent > 10_000`. Structurally, the same class recurs for every sync
tool — `06 §C` should carry a row saying plainly that a CPU-bound sync tool cannot be
timed out or cancelled, because that is true of the `@tool` contract generally, not just
of `calculate`.

---

## G-5 — `TaskLedger` loses 9 of 10 concurrent writes, and its own safety argument covers the wrong axis

**Files:** `src/harness/tasks.py` (module docstring §3; `DEFAULT_KEY` `:40`; `_save` `:107`;
`add` `:111`), `src/harness/policy/decision.py:243-250` (`DecisionLog`'s docstring),
`06 §A` row 3 (R-4).

**The claim under test.** `tasks.py`'s docstring, verbatim: *"Mọi tool sửa sổ đều
`effect="write"`, và `EFFECT_PROFILES[Effect.WRITE].parallel_safe` là `False` — harness đã
tuần tự hoá chúng trong một bước, nên không có hai lời gọi nào cùng đọc-sửa-ghi đè lên
nhau."* The claim is true and scoped to one thing it does not say out loud: `parallel_safe`
serialises tool calls **within one dispatch batch of one run**. It says nothing about two
runs.

**The scenario, run against this commit.** Two `TaskLedger`s on one `SqliteStore`, at the
default key, ten concurrent adds:

```python
st = SqliteStore("/tmp/t.db")
a, b = TaskLedger(st), TaskLedger(st)          # default key, both
await asyncio.gather(*[a.add(f"A{i}") for i in range(5)],
                     *[b.add(f"B{i}") for i in range(5)])
await a.all()
```
```
expected 10 tasks, got 1 [('t1', 'B1')]
```

Nine tasks silently gone. The interleave is real because `SqliteStore.get`/`put` are
`asyncio.to_thread` (`memory/sqlite.py:64,76`), so every `await self._store.get(...)` in
`all()` is a genuine yield point between the read and the write of a read-modify-write.
`add()`'s `id = f"t{len(tasks)+1}"` compounds it: concurrent adders mint the same id.

**Why the default configuration is the vulnerable one.** `DEFAULT_KEY = "harness:tasks"` is
one fixed string with no `run_id`, `session_id` or `tenant_id` in it. Two `TaskLedger`s
built on the same `Store` share a ledger by default, confirmed directly: ledger A adds a
task, ledger B's `summary()` returns it. Sharing a `Store` is not exotic — it is the
documented persistence path, it is what the Service API's single shared `Agent` implies,
and it is what `Session` (T-8.6) is for.

**Why this is an R-4 regression specifically.** `06 §A` row 3 is *"Shared state leaks
between concurrent runs"*, mechanism *"R-4: checkpointed state is the only memory"*, tier
**2**, checked by *"a test with two interleaved runs."* `TaskLedger` is run state that
deliberately lives outside the checkpoint — the docstring argues for that at length, and
the argument for durability is good — and it has no two-interleaved-runs test. The one
existing test file, `test_task_ledger.py`, is entirely sequential.

**The sibling comparison, checked and partly refuted.** The tasking asked whether
`TaskLedger._save()` reproduces the shape `DecisionLog` explicitly rejected. It reproduces
the *shape* — whole array under one key per row — but not the *consequence* the
`DecisionLog` docstring names ("one write failing partway loses the whole approval
history"): `SqliteStore.put` is a single committed `INSERT … ON CONFLICT`, so a torn write
is not the failure mode here. The failure mode is a **lost update**, which is worse in one
respect the docstring's reasoning did not consider: a torn write is loud, and this is
silent — `add_task` returns `"đã thêm t1: …"` to the model for all ten calls.

**Minimal fix.** (a) `DEFAULT_KEY` stops being a constant: the key must include the
run/session identity, or `TaskLedger.__init__` requires `key=` with no default — the same
"unrestricted is possible, never the silent default" discipline `allowed_hosts` and
`Budget(usd=None)` already follow. (b) Task ids become `uuid4().hex[:8]`, not
`len(tasks)+1`. (c) An `asyncio.Lock` per `(store, key)` closes the in-process half exactly
as `idempotency.py::_locks` already does — and, like that module, says plainly that it does
not close the cross-process half. (d) A two-interleaved-runs test, symmetric to the one
`06 §A` row 3 already cites.

---

## G-6 — `STALL_AFTER=6` is evaded forever by one varying argument

**Files:** `src/harness/progress.py` (`signature` `:78`, `observe` `:111`),
`src/harness/run.py:198`, `src/harness/dispatch.py:294`.

**The claim under test.** `progress.py`'s docstring: *"Bế tắc có một chữ ký cơ học đọc được
miễn phí: không có lời gọi nào MỚI trong N bước liên tiếp."* `signature()` hashes
`name + canonical(args)` after stripping `_`-prefixed keys — and the comment explaining that
strip states the right principle: *"counting them as different would hand any caller a
trivial way to look busy while standing still."* The strip covers exactly one channel.

**The scenario, run against this commit.**

```python
p = ProgressLedger()
for i in range(50):
    p.observe([{"name": "read_source", "input": {"path": "a.py", "nonce": i}}])
```
```
NO STALL after 50 identical-but-for-nonce steps; stalled_steps = 0
control (no nonce): stall fired at step 6
```

**Why the evasion is not hypothetical, and is worse than "the model is being clever."**
The model does not have to intend this. `dispatch.py:294` strips only `_`-prefixed keys
before `spec.fn(**kwargs)`, so a stray `nonce=` is passed through and the call dies with
`TypeError: unexpected keyword argument` — and for `read`/`external` it is retried three
times first. So the concrete run is: fifty consecutive steps in which every tool call
**fails**, each failure with a slightly different argument, and the stall detector reads
the sequence as fifty steps of progress. The single most obviously-stuck state a run can be
in is the one that resets the counter. A model flailing at `edit_source(old=…)` with
slightly different context each attempt — the exact loop the docstring's `write_source` /
`run_tests` example is built around — produces this without any adversary.

`Budget.steps` still terminates the run, but that is the failure this module exists to
catch earlier and explain better; `progress.py`'s own opening paragraph says so.

**Minimal fix.** Signature over the tool name plus the arguments *the tool's schema
declares*, discarding unknown keys — `ToolSpec.input_schema` is right there, and discarding
unknown keys matches what dispatch does with them anyway. That alone kills the nonce
channel. Separately, a second counter over *outcomes*: N consecutive steps in which every
tool call returned `is_error=True` is a stall no argument shuffling can hide, and it is one
boolean already present on `TOOL_FINISHED`.

---

## G-7 — `Sandbox` is a name, not a boundary; and `InProcess` is not in-process

**Files:** `src/harness/sandbox.py` (`Sandbox` Protocol `:33`, `InProcess` `:57`,
`Subprocess` `:90`), `src/harness/tools/code.py:96`, `06 §C` row 1.

**Two findings in one file.**

**(a) Nothing distinguishes isolation from its absence.** `Sandbox` is a `Protocol` with a
single `run()` signature. `InProcess` — whose docstring opens *"No isolation at all"* —
satisfies it structurally and identically to any real Docker or gVisor implementation. A
`CodeTools(root, sandbox=...)` parameter typed `Any | None` accepts either; no field, no
marker, no runtime check, nothing in `Completed`, nothing an operator can assert on and
nothing an audit event records. The word "Sandbox" is doing the entire job, which is `06`'s
own definition of tier 1: documentation says which one to use, nothing stops the other.
`06 §C` row 1 honestly grades `Workspace` tier 2 and points at *"a `Sandbox` with a process
boundary plugged in"* as the escape — but there is no way for that row's promise to be
checked, because a `Sandbox`-shaped object that provides nothing is indistinguishable from
one that provides everything.

**(b) The seam's own three-part justification does not survive reading the two
implementations.** The module docstring invokes `docs/02 §2.4`'s test and rests it on *"two
genuinely different implementations ship today — `InProcess` and `Subprocess`."* They are
not genuinely different. Both call `asyncio.create_subprocess_exec` with the same `cwd`,
the same non-inherited `env`, the same pipes, the same timeout. The entire delta is
`start_new_session=True` and `killpg` instead of `kill` — and that delta makes `Subprocess`
strictly better at the one thing they differ on, so `InProcess` has no case at all. It is
also misnamed in the direction that matters: a reader who takes "InProcess" at its word
concludes that a tool runs inside the interpreter and that `Subprocess` is the one that
forks, which is backwards, and the docstring's careful "named for what it honestly is"
paragraph is therefore honest about the wrong property.

**Minimal fix.** (a) One required field on the protocol —
`isolation: Literal["none", "process", "container"]` — set by the implementation, recorded
on every `tool.called` audit event, and readable by a policy. That turns "is this actually
isolated" from a name into data, which is the difference between tier 1 and tier 2, and
lets an operator write `RequireIsolation("container")` as a policy, which is tier 3 for the
deployments that need it. (b) Delete `InProcess`; `Subprocess` dominates it on every axis.
The seam's three-part test is then honestly failed for now and honestly passed the day a
real container implementation exists — which is better than passing it with a duplicate.

---

## G-8 — Five real provider calls run under one `Reservation`

**Files:** `src/harness/retry.py::with_provider_retry` `:91`, `src/harness/run.py:96-146`,
`src/harness/lg/adapter.py:69`, `src/harness/budget/ledger.py` (`reserve` `:219`, `settle`
`:250`, `overshoot` `:178`), `06 §A` row 4.

**The mechanism.** `run.py` takes exactly one `reserve()` (before the `BUDGET_RESERVED`
event), then calls `with_provider_retry(lambda: self._p.complete(req, ...), ...)`, which
may call `self._p.complete` up to `MAX_ATTEMPTS = 5` times. `settle()` runs once, after the
loop returns, on the usage of the one attempt that succeeded. Two consequences, both real:

* **A `ProviderTimeout` is a client-side observation, not a server-side one.** The vendor
  may well have generated and billed the completion that never arrived. Four such attempts
  followed by a success bill four full calls to the account and one to the `Ledger`.
* **A run that ends in provider failure settles nothing at all.** `run.py:129` breaks to
  `StopReason.ERROR` without reaching `settle()`, so five attempts' worth of real spend is
  recorded as `$0.0000`, and the open `Reservation` is never closed — `S-14`'s missing
  `void()`, which `07` records as "not yet needed (0 callers)", now has a caller.

**Why this defeats the invariant rather than merely stressing it.** `06 §A` row 4 is
*"Caps step count, not money"*, tier **3** at construction / **2** at runtime, mechanism
`I-2`: *"no model call runs without an open `Reservation`."* Five calls under one open
reservation satisfies `I-2` word for word and defeats what it is for. `budget/ledger.py:179`
documents `overshoot` as *"How far a settled call pushed spend past the budget. **Bounded by
one call.**"* — a sentence that was true before `retry.py` existed and has been false since,
in the file that N-5 added, with no cross-reference in either direction. `_committed()`
(added for exactly this class of reasoning) cannot help: there is only ever one reservation
open.

**Minimal fix.** Move the reservation inside the retry loop — `with_provider_retry` takes
`reserve`/`settle` callbacks and takes a fresh reservation per attempt, settling or voiding
each. That makes the wall-clock deadline a money deadline too, which is what `docs/10 §3`'s
promise ("retries cannot outlive the budget") should have meant. Cheaper interim: on the
final failure path, `settle()` a worst-case estimate for every attempt made, and correct
`overshoot`'s docstring to "bounded by one call, times the retry attempts made."

---

# III. Should fix

## G-9 — `allowed_hosts=()` denies all, `allowed_hosts=None` allows all

**Files:** `src/harness/agent.py:94-100`, `:199`; `src/harness/policy/builtin.py:136-141`.

T-7.2's breaking change to deny-by-default is correct and well argued in the comment above
the parameter. What it leaves behind is a signature in which `()` and `None` — two falsy
values, adjacent in the same annotation `Sequence[str] | None = ()` — carry opposite
security meanings, and `EgressPolicy.check` short-circuits to `ALLOW` on `None` before it
looks at anything. A caller who has no hosts to allow and writes `allowed_hosts=None`, or
who threads an `Optional[list]` from their own config that comes back `None` when the key
is absent, silently reopens the network. The comment says "`None`, passed EXPLICITLY, is
the escape hatch" — but nothing distinguishes explicit `None` from propagated `None`, which
is exactly the distinction the sentence depends on.

**Fix.** The `Budget(usd=None)` precedent the comment itself cites was resolved by making
"unlimited" a distinct constructed thing, not a falsy default. Do the same:
`allowed_hosts=Unrestricted(reason=...)`, or a separate `Agent(egress="unrestricted")`.
Interim, and cheap: a loud `UserWarning` on explicit `None`, since a silent unrestricted
egress is precisely the "mechanism defaulting to off" class `06 §D` indicts.

## G-10 — Three process-local mechanisms behind an API that is normally run multi-process

**Files:** `src/harness/server/__init__.py` (`_RunRegistry.__init__` `:192`, `_runs` `:195`,
`start` `:200`), `src/harness/idempotency.py:62` (`_locks`).

Confirmed by reading: `_RunRegistry` defaults to a **fresh** `InMemoryStore()` per instance,
`self._runs` is a plain dict, and `idempotency.py::_locks` is a module-level
`WeakValueDictionary` whose own comment scopes it to one process. `create_app()` is called
once per worker. Under `uvicorn --workers 4`:

* the same `Idempotency-Key` sent to two workers starts **two real runs** — defeating the
  exact feature the module docstring leads with (*"a client that times out and retries the
  same key gets back the SAME `run_id`"*), and defeating it in the scenario the feature was
  built for, since a client retry after a timeout is precisely when a load balancer picks a
  different worker;
* `GET /v1/runs/{id}` 404s roughly (N-1)/N of the time;
* `POST .../approvals/{cid}` cannot reach a future living in a sibling worker, so approvals
  hang until the tool timeout.

The docstring's caveat is *"In-memory run registry, one process. A run's state does not
survive a process **restart**."* Restart is named; multi-worker is not, and it is the more
likely of the two. The root cause is the same one `idempotency.py` already admits about
itself in its `_locks` comment — recorded in two places, in different words, neither of
which mentions workers.

**Fix.** `create_app(agent, *, store=...)` makes `store` required (no `InMemoryStore()`
fallback), and the `_runs` registry moves behind the same `Store`. Until then, one sentence
— *"single-worker only; `--workers > 1` breaks idempotency, status and approvals"* — in the
module docstring, and one row in `06 §C`, since this is a mechanism advertised as working
that does not, in the ordinary deployment.

## G-11 — A refused approval is reported as a successful one

**Files:** `src/harness/server/__init__.py` (`resolve_approval` `:333`, `_bridge_approvals`
`:186`), `src/harness/policy/engine.py:73-80`.

Traced end-to-end and then run. With `Agent(require_approval_evidence=True)`, an HTTP
approval whose body omits `evidence` **is** correctly refused — `_bridge_approvals` always
builds `Actor.human(...)`, so `resolve()`'s `actor.kind == "human" and evidence is None`
branch fires and the `danger` tool does not execute. Verified: `DANGER TOOL RAN? []`. The
enforcement is real; that half of the tasking's hypothesis is refuted (see §V).

What is wrong is everything downstream of the refusal. The approver's HTTP response is
`{"resolved": true, "approve": true}` — a 200 saying their approval landed. The run then
finishes as `status: "done"`, `ok: true`, `text: "ok"`. The one party who could fix the
misconfiguration (the operator who just approved) is told they succeeded; the one party who
could notice (whoever reads `Result`) sees a successful run. The refusal exists only in a
`policy.decided` event nobody is looking at.

**Fix.** `resolve_approval` returns `{"resolved": true, "approve": true, "enforced":
false, "reason": "…requires AuthEvidence"}` — or, better, rejects the body with 400 up
front when `agent.require_approval_evidence` is set and `evidence` is absent, which turns a
silent post-hoc DENY into an immediate, fixable error at the only moment the caller can act
on it.

## G-12 — An untrusted MCP server picks the slug, and can therefore refuse to let the agent start

**Files:** `src/harness/mcp/__init__.py::classify_mcp_tool` `:104`,
`src/harness/tools/registry.py` (duplicate-name guard).

The docstring's own collision example was tested directly and its claim **holds** — see §V.
The guard fires:

```
policy(identity="a")   + tool "b_c" -> slug "a_b_c"
policy(identity="a_b") + tool "c"   -> slug "a_b_c"
ToolSet([s1, s2]) -> DuplicateToolError: two tools are both called 'a_b_c'
```

The finding is what that correct behaviour costs. `slug()` collapses repeated `_`, the
operator picks `identity`, and the **server** picks `tool.name`. So a server the operator
has already marked `trusted=False` and `default_effect=DANGER` — the fail-closed
configuration — can still publish a tool named to collide with a local tool or with another
server's, and `Agent(...)` construction raises. Nothing about `trusted=False` limits this;
`allow=` limits it only if the operator enumerated an allowlist, which is optional. This is
`S-7`'s parenthetical *"a `DuplicateToolError` DoS"*, which `review-security.md` filed
under "not enough evidence" because namespacing was unspecified at the time. Namespacing
now exists, and the evidence is above.

**Fix.** `classify_mcp_tool` uses a separator `slug()` cannot collapse, or appends a short
`blake2b` of the exact `(identity, tool.name)` pair when the slug differs from the joined
input — the same trick `tools/__init__.py::slug` already applies when a name slugs to
nothing. Either makes the collision unrepresentable rather than merely detected: tier 3
instead of tier 2, on a mechanism where the input is attacker-chosen.

## G-13 — `07`'s S-9 remainder is narrower than the actual gap

**Files:** `src/harness/mcp/__init__.py` (`_effect_for` `:91`, `_effect_from_hints` `:66`,
`connect` `:150`), `07 §0` S-9 row, `review-security.md` S-9.

`07`'s status table records S-9 as *"Partially fixed — different labels are blocked; ONE
label re-pointed to a different endpoint is not yet."* That framing makes the open
remainder sound like a DNS/rotation edge case. Reading `_effect_for`, the remainder is
larger and does not require the endpoint to change at all.

With `trusted=True`, `_effect_for` returns `_effect_from_hints(tool.annotations)` for any
tool with no entry in `policy.effects`. `connect()` correctly refuses to re-list on its own
initiative, so classification is pinned per connection — but a run is a new connection.
Between run 1 and run 2, the **same** server, at the **same** endpoint, under the **same**
label the operator reviewed, can publish a new `purge_workspace` with `readOnlyHint=true`
and `openWorldHint=false`, and `_effect_from_hints` classifies it `Effect.READ`: floor
`ALLOW` in both safety levels, `check_flow` never touches `read`, audit level `debug`. That
is `review-security.md` S-9's scenario verbatim. Its stated minimal fix had two clauses;
`connect()`'s no-re-listing rule satisfies the monotonicity clause vacuously, and the
second — *"`trusted=True` should bind to a hash of the reviewed tool list, not to the server
generically"* — is not implemented and is not what `07`'s remainder describes.

**Fix.** `McpServerPolicy` gains `reviewed_tools: Mapping[str, str] | None` — tool name to
a hash of `(name, description, inputSchema, annotations)` as the operator reviewed them.
`connect()` compares; anything absent or changed falls to `default_effect` regardless of
`trusted`. That is the K-12-compatible version of `fingerprint`: it hashes what the
operator actually looked at rather than a transport identity whose format nobody settled,
and it needs no new transport knowledge. Independently, `07`'s S-9 row should be widened to
say what is actually open.

## G-14 — The evaluation harness has no aggregate budget

**Files:** `src/harness/eval/golden.py::run_golden_set` `:84`,
`src/harness/eval/benchmark.py::benchmark` `:60`.

Checked and partly refuted first: `run_golden_set` runs the real `Agent` through
`_run_with_events`, so every case passes through the same `Budget`, `PolicyEngine`,
`check_flow` and taint machinery as a normal run — there is no eval-only bypass, and the
tasking's hypothesis on that point is refuted (§V).

What is missing is one level up. Each `atry_run()` builds a fresh `Ledger` from the agent's
per-run `Budget`, so a 500-case golden set at the default `$0.50` is a $250 ceiling that
exists nowhere as a number and cannot be stopped part-way — `GoldenReport.total_cost_usd`
reports the damage after the last case. `benchmark(n=…, concurrency=…)` is the same shape
with a multiplier. This is `06 §A` row 4's own failure class ("caps step count, not money")
reappearing at the harness-of-the-harness layer, in the module whose entire job is to
measure cost.

**Fix.** `run_golden_set(agent, cases, *, total_budget: Budget | str)` — required, not
optional, mirroring `Agent`'s own treatment of `budget` — with a shared `Ledger` that
`charge()`s each case's `Result.cost` and stops the sweep when exhausted, returning a
partial `GoldenReport` marked as such. `Ledger.charge()` already exists for exactly this
(the sub-agent path), so this is wiring, not new mechanism.

## G-15 — `safety` is inherited by a sub-agent; `approve=` is not

**Files:** `src/harness/agent.py::_check_subagent_safety` `:859`,
`src/harness/policy/engine.py:57-64`, `src/harness/dispatch.py::_run_subagent` `:320`.

`_check_subagent_safety` enforces exactly one thing — a child's `safety` rank may not be
lower than its parent's — with a good error message explaining that otherwise *"delegating
work is a way to escape the safety level."* The reasoning is right; the enforcement covers
one of two axes.

`approve=` is a per-`Agent` field, and `_run_subagent` calls `child.atry_run(...)` with the
child's own configuration. So: a parent configured `Agent(approve=ask_slack)` at default
`safety="standard"`, delegating to a child that has `write` tools and no `approve=`, gets
`resolve()`'s no-callback branch — which returns `ALLOW` for everything below `danger`. The
parent's `write` tools ask a human; the identical `write` tool one delegation hop away does
not. Delegating work is still a way to escape a safety control, just not the one
`_check_subagent_safety` checks.

This is the caveat behind §0's S-28 re-check: `07` records S-28 as "already correct, just
missing documentation," and the no-hang property genuinely holds. The property that does
not hold is the one nobody asked about.

**Fix.** Extend `_check_subagent_safety` to a second clause: if the parent has `approve=`
and the child does not, refuse at construction with the same shape of message. Or make the
child inherit the parent's `approve=` when it declares none — restriction-only inheritance,
matching `docs/06 §4`'s existing least-privilege rule for sub-agents.

## G-16 — `confine()` refuses every path containing `%`

**Files:** `src/harness/workspace.py:48-56`.

The reasoning in the comment is sound for its stated threat — decode-then-check invites
"did I decode enough times" — but the conclusion overshoots: a raw filesystem path *does*
legitimately contain `%`. `test_100%_coverage.py`, saved HTTP fixtures, `%s`-named
C-format test data, and anything downloaded with its URL-encoded name are all unreachable
through `read_source`, `write_source`, `edit_source`, `read_file` and `write_file`, with an
error message telling the model to "write the literal character instead" — advice that
cannot work, because the literal character is the one being rejected. There is no escape
hatch and no operator override.

**Fix.** The check that actually matters is already there and already correct: `resolve()`
collapses `..` and follows symlinks, and `relative_to` is the containment test. `%` needs no
special case at all — an encoded `..` is not a path component on any filesystem this
library targets, so `%2e%2e` resolves to a literal directory named `%2e%2e` inside the root,
which is contained. Drop the check; keep the null-byte and absolute-path rejections, which
guard real pathlib footguns. If it is kept for defence in depth, narrow it to `%2e`/`%2f`
(case-insensitive) rather than the character.

## G-17 — A middleware that raises looks exactly like a tool that failed, and is retried

**Files:** `src/harness/middleware.py::_wrap_tool` `:296-312`,
`src/harness/dispatch.py::_invoke` `:286-318`.

Found accidentally, while testing G-nothing: a `Middleware.before_tool` with a typo raised
`AttributeError`, and the observable result was three `before_tool` invocations, a
`tool_result` with `is_error=True` carrying the middleware's Python traceback text to the
model, and a run that reported `ok=True`. `_wrap_tool` catches only `ShortCircuit`;
everything else propagates into `_invoke`'s catch-all, which cannot distinguish "the tool
failed" from "the wrapper around the tool failed" and applies
`EFFECT_PROFILES[spec.effect].retryable` — 3 attempts for `read`/`external`. Any
`before_tool` side effect (a rate-limit counter, an audit row, a metric) runs three times
for one logical call, and the model is shown an internal error message about the operator's
own code.

This does not undermine the module's central safety argument, which I verified holds (§V):
middleware genuinely cannot lower a verdict or waive a reservation. It undermines its
observability story.

**Fix.** `_wrap_tool` catches non-`ShortCircuit` exceptions from hooks and re-raises them as
a distinct `MiddlewareError`; `dispatch.py` treats that class as non-retryable regardless of
effect, and reports it as a run-level error rather than a tool result — a bug in the
operator's code is not something the model should be asked to work around.

---

# IV. Checked and correctly handled — not findings

Recorded briefly so a later round does not re-open them.

**1. `middleware.py`'s stacking.** `agent.with_middleware(a).with_middleware(b)` nests
correctly in all three dimensions, verified by object identity and by execution order:
provider chain is `_MiddlewareProvider(b) -> _MiddlewareProvider(a) -> FakeModel`;
exporters are both present; tool hook order is
`before(b), before(a), fn, after(a), after(b)`. Nothing is discarded and nothing is
double-invoked. The hypothesis is refuted.

**2. `retry.py` and `write`/`danger` double execution.** Every call site of
`with_provider_retry` was traced (`run.py:127`, `lg/adapter.py:69`) — both are model calls
only. Tool retry lives entirely in `dispatch.py::_invoke`, which sets `attempts = 1` when
`EFFECT_PROFILES[spec.effect].retryable is False`, i.e. for `write` and `danger`, and wraps
each call in `execute_once` keyed on the call id. There is no path by which a `write` or
`danger` tool is retried on timeout. The hypothesis is refuted. (The *money* consequence of
model-call retry is real and is G-8.)

**3. The MCP slug-collision guard.** Built the two exact `(server, tool)` pairs the
docstring names and confirmed `ToolSet` raises `DuplicateToolError` with both `source`
values in the message. The docstring's prose claim is accurate — rare among prose claims
about guards. (What it costs is G-12.)

**4. `require_approval_evidence` through the Service API.** Traced `_bridge_approvals` ->
`PolicyEngine.resolve` and then ran it: the `danger` tool does not execute when the HTTP
body omits `evidence`. Enforced end-to-end. No test in `tests/test_m9_t92_service_api.py`
exercises this combination, so the enforcement is correct by construction and unlocked by
any test — worth adding one, but not a defect. (What *is* wrong is what the caller is told:
G-11.)

**5. The eval harness and the safety gates.** `run_golden_set` drives the real `Agent`; no
`Budget`, `PolicyEngine` or taint gate is bypassed or stubbed. `benchmark()` takes an
opaque `run_fn` and adds nothing. The hypothesis is refuted. (The missing aggregate ceiling
is G-14.)

**6. `fetch("file:///etc/passwd")` under the default configuration.** `urllib.request`
does support `file://`, and `builtin/web.py::fetch` applies no scheme check — but
`EgressPolicy.check` sees key `"url"`, gets `urlparse(...).hostname is None`, falls back to
the whole string as the host, and finds it in no allowlist. Combined with T-7.2's
deny-by-default `allowed_hosts=()`, it is refused. Blocked by accident rather than by
design — the scheme check is still worth adding — but blocked. (The `allowed_hosts=None`
path that unblocks it is G-9.)

**7. `execute_once`'s three-phase reasoning.** Re-read against `Store`'s actual contract.
The `fail_open` fork is correct and correctly asymmetric; the `_locks` TOCTOU note is
accurate about its own scope. One inconsistency worth a line but not a finding:
`dispatch.py:296` calls `execute_once` with the default `fail_open=False` for **every**
effect, while the module docstring specifies fail-open for `read`/`external`. Unreachable
today (the store is an `InMemoryStore` that cannot raise), so it is a latent
docstring/caller drift rather than a live bug.

**8. `confine()`'s core containment logic.** Tried the standard escapes against the real
function: `..` traversal, absolute POSIX, Windows-drive and UNC shapes, null bytes, a
symlink whose final component points outside, and a dangling symlink. All refused, all by
the single `relative_to` check after a full `resolve()`. The function does its job
correctly; both findings against it are about who fails to call it (G-2) and one
over-rejection (G-16).

---

# V. Not enough evidence (not counted as findings)

- **A TOCTOU race between `confine()` and the caller's `open()`.** The window is real —
  `confine` returns a `Path` and `read_bytes`/`write_text` happen afterwards — but every
  exploit I could construct needs a concurrent mutator inside the root, and the only such
  mutator available to the model is the subprocess `run_tests` launches, which already has
  unrestricted filesystem access (G-1/G-2) and does not need the race. So the race is
  strictly dominated by a simpler attack, and I could not build a scenario where it is the
  *only* way in. If G-1 and G-2 are fixed, this needs re-examining, not before.
- **`observe/otel.py`.** Read; the four-span shape matches K-10's trimmed plan and nothing
  in it participates in a safety decision. Not deeply exercised — lowest-stakes tier,
  lowest budget, as instructed.
- **`eval/trajectory.py` and `eval/cost.py`.** The Wilson interval is shared with T-8.4
  rather than duplicated (correct), and `check_trajectory` reads `effect_of` from the live
  toolset. No scenario attempted against the interval maths itself.
- **Whether `_bridge_approvals`'s `run.status` restoration is correct under two concurrent
  pending approvals.** `was_running` is captured per-call and `finally` restores only when
  `not run.pending`, which looks right, but the Service API is single-worker and
  single-`Agent`, so I could not construct a real two-pending-approval run to test it.
- **Cross-process behaviour of `TaskLedger` under `SqliteStore`'s WAL.** G-5's lost update
  is confirmed in-process. Whether two OS processes produce the same result depends on
  SQLite busy-timeout interleaving I did not test; the in-process result is sufficient for
  the finding, and the cross-process case can only be worse.
</content>
