# 14 — Validation Plan

> **Purpose.** CI proves the code does what the code says. This document proves the code
> does what *this design package* says. Those are different claims, and the second one is
> the one that decays silently.

## 1. Traceability

Every requirement maps to an executable check. A requirement with no check is not a
requirement, it is a wish.

| Req | Check | Where | Gate |
|---|---|---|---|
| G1 / SC-1a | Beginner study, 5 developers | T-5.4 | Blocks 1.0 |
| G1 / SC-1b | **Beginner study, 3 children aged 10–12** | T-5.4 | Blocks 1.0 |
| G2 / SC-2a, SC-2b | Property P-1, 3 000 adversarial runs with injected count drift | `tests/property/test_budget.py` | Blocks merge |
| G3 / SC-3 | Red-team suite, 14 scenarios | `tests/redteam/` | Blocks merge |
| G4 / SC-4 | Cache benchmark, 10-turn fixture | `benchmarks/cache.py` | Blocks merge |
| G5 / SC-5 | `no_network()` autouse; suite runs offline | CI | Blocks merge |
| G6 / SC-6 | 5 out-of-tree plugin examples | `examples/plugins/` | Blocks M4 |
| G7 / SC-7 | Golden replay, byte-identical | `tests/golden/` | Blocks merge |
| G8 / SC-8 | Zero malformed tool args and zero answer parse failures over the fixture set | `tests/integration/` | Blocks merge |
| FR-01…23 | Named integration test per requirement | `tests/integration/` | Blocks merge |
| NFR-01 | `python -X importtime` < 200 ms | CI | Blocks merge |
| NFR-02 | Null-provider benchmark, < 15 ms p95 | `benchmarks/overhead.py` | Blocks merge |
| NFR-03 | Memory profile, 100-step run < 50 MB | `benchmarks/memory.py` | Blocks merge |
| NFR-04 | `mypy --strict` | CI | Blocks merge |
| NFR-05 | Dependency count assertion | CI | Blocks merge |
| NFR-06 | CI matrix on 3.11 / 3.12 / 3.13 | CI | Blocks merge |
| NFR-07 | Property P-6 | `tests/property/` | Blocks merge |
| NFR-09 | Parallel tools bounded by `max_parallel_tools` (default 8) | `tests/integration/` | Blocks merge |
| NFR-08 | Tool-raises integration test | `tests/integration/` | Blocks merge |
| NFR-10 | Docstring examples executed | CI | Blocks merge |
| ADR-001…026 | Architecture conformance tests (§4) | `tests/conformance/` | Blocks merge |

## 2. SC-1 — Time to First Agent

The measurement the whole DX argument rests on. Run at T-5.4, before 1.0. Two populations,
**both blocking**, because passing one proves nothing about the other.

### SC-1a — developers

- **Participants:** 5 Python developers who have never seen the library. At least two with
  no prior LLM-API experience.
- **Materials:** the README and nothing else.
- **Task:** "Build an agent that answers questions using the web, and run it."
- **Observation:** screen recording plus think-aloud. The observer does not answer
  questions; unanswered questions are the data.

| Metric | Pass |
|---|---|
| Median time to first successful run | ≤ 10 min |
| Participants succeeding unaided | ≥ 4 / 5 |
| Participants who had to read library source | 0 |
| Distinct confusion points reported by ≥ 2 participants | 0 |

### SC-1b — children

The requirement the project owner actually stated, measured rather than asserted.

- **Participants:** 3 children aged 10–12 who have completed a basic Python course —
  they know `def`, variables, strings, lists, `print`, `import` and `pip install`.
- **Materials:** [§15 — Your First Agent](15-first-agent.md) and nothing else.
- **Adult role:** may read words aloud on request, and may perform the one account/payment
  step in Step 2. **May not** explain, debug, type code, or point at the screen.
- **Task, in two parts:**
  1. Build a helper that answers questions.
  2. **Give it a tool you wrote yourself.**
- **Environment:** a machine with Python already installed. Installing Python is not part
  of this library's claim and is excluded.

| Metric | Pass |
|---|---|
| Children reaching a working agent | ≥ 2 / 3 in ≤ 20 min |
| **Children adding a tool of their own** | **≥ 2 / 3** |
| Children who needed an adult to explain anything beyond Step 2 | 0 |
| Points where a child gave up and had to be restarted | 0 |

**Part 2 is the one that matters.** Running a provided example proves the example works.
Writing a tool is the point at which someone has built something rather than run something,
and it is the only part of the ladder that touches `@tool`, type hints, and `effect=` — the
three concepts the council argued hardest about.

### On failure — either study

1.0 is blocked and the council reconvenes **on the API, not on the documentation**. If the
proposed fix is "explain it better", the API is wrong. This is written down in advance
precisely so the result cannot be rationalized after the fact — which is what happened in
Round 4, when a beginner review inspected the API, approved it, and never tested whether
anyone could get from an empty folder to a working agent.

**Every confusion point is logged verbatim and mapped to a register entry in
[§08](08-poka-yoke.md)** — a confusion with no entry means the register has a gap.

## 3. SC-4 — Cache benchmark

**Fixture.** A 10-turn conversation, ~2 000-token system prompt, 5 tools, recorded once
against the real API and replayed thereafter.

**Measure.** `cache_read_input_tokens / input_tokens` on turns 3 and later.

**Pass:** ≥ 90 %. **Regression alarm:** any drop > 5 points from the previous release fails
the build, because a cache regression is invisible in every other signal and shows up only
as a doubled invoice.

**Negative controls** — the benchmark also asserts these *fail* to cache, proving it is
measuring something real: a prompt with an embedded timestamp (must be caught by the linter
first), and a run whose tool set changes between turns (must be impossible to construct).

## 4. Architecture conformance tests

These assert the *design* holds, not just that the code passes. They are the defense
against slow architectural drift, which no ordinary test catches.

| ID | Asserts | ADR |
|---|---|---|
| AC-01 | No module in L2 imports an L0 adapter (import-linter) | §02.2 |
| AC-02 | No public API accepts `parallel_safe` / `retryable` / `requires_approval` | ADR-003 |
| AC-03 | `Agent` has no public setter; all fields frozen | ADR-004 |
| AC-04 | Every `provider.complete` call site is immediately preceded by `ledger.reserve` (AST analysis) | ADR-005, I-1 |
| AC-05 | Every tool execution site is immediately preceded by a policy decision (AST analysis) | I-2 |
| AC-06 | No `ConfigError` subclass is raised from within `RunEngine` | §04.7 |
| AC-07 | `run.py` ≤ 250 lines excluding docstrings | IDL-13 |
| AC-08 | Only `models/anthropic.py` imports the vendor SDK | ADR-002 |
| AC-09 | `EFFECT_PROFILES` is never mutated (no assignment outside its definition) | IDL-14 |
| AC-10 | `RunContext` has no field referencing message history | IDL-15 |
| AC-11 | No `float` literal or annotation in `budget/` | IDL-01 |
| AC-12 | Every `__all__` symbol has a docstring containing a `>>>` example | NFR-10 |
| AC-13 | Nothing in the package assigns to `sys.excepthook`, `sys.path`, `warnings.filters` or any other global at import | ADR-015 |
| AC-14 | Progress output is emitted only when `stdout.isatty()`, and never contains tool arguments | ADR-014 |
| AC-15 | `harness new` output always includes a `.gitignore` listing `.env` | ADR-013, register #36 |
| AC-16 | No `ToolSchemaError` path defaults an unannotated parameter to `str` | IDL-22 |
| AC-17 | The unfiltered traceback appears in the `error.raised` event whenever `run()` filtered one | ADR-015 |
| AC-18 | Every credential-missing error string contains `harness setup` and none contains `ANTHROPIC_API_KEY` | ADR-013 |
| AC-19 | `max_tokens` appears in no public signature and is assigned only by `Ledger.size_call` | ADR-017 |
| AC-20 | Every closed enum mirroring a provider protocol has an exhaustiveness test; no unmapped value resolves to an `ok = True` outcome | ADR-019 |
| AC-21 | No `Policy` implementation in the package is async or performs I/O; approval is resolved by the engine, not by a policy | ADR-021 |
| AC-22 | Every type defining `__eq__` either defines a consistent `__hash__` or sets `__hash__ = None` — checked across the package, not just `Secret` | IDL-32 |
| AC-23 | Every tool definition sent to a provider carries `strict: true` | ADR-022 |
| AC-24 | `returns=` and `tools=` schemas come from the same generator — no second Python-type-to-schema path exists | ADR-022 |
| AC-25 | **Every invariant in [§01](01-requirements.md) has at least one section, one decision-log entry and one test.** *Round 21 found invariant 4 had none of the three; nobody had counted.* | Round 21 |
| AC-27 | No module-level constant sets a sleep in a construction path; `Agent(...)` costs < 5 ms | ADR-025 |
| AC-29 | Every value type uses `@value`; none uses bare `@dataclass(frozen=True, ...)` | ADR-027 |
| AC-30 | Redaction is applied at **both** the transcript boundary and the tool-result boundary | ADR-028 |
| AC-31 | **Every public parameter is read somewhere in the package.** *`max_parallel_tools` was accepted, stored and documented for two milestones without anything reading it (Round 26).* | NFR-09 |
| AC-28 | Every `Secret` guarantee (unhashable, unpicklable, weakly registered) is exercised, not just declared | ADR-024 |
| AC-26 | **Every FR, NFR, RT and AC appears in the [§11 traceability matrix](11-implementation-plan.md#traceability-matrix) with an owning task.** A contiguous range (`AC-01…26`) counts as covering every id it spans; the check expands ranges and verifies none is skipped, so shorthand cannot hide a gap. *Round 22 found FR-18 — a `Must` — and 18 AC checks owned by nobody.* | Round 22 |

**Every AC has a negative fixture** proving it fails when the property is violated. A
conformance test that cannot be made to fail is not testing anything — and 18 of these were
specified but built by nobody until Round 22 (T-3.6).

AC-22 is written package-wide rather than for `Secret` alone. The Round 19 defect was a
generic Python contract violation that happened to land on the security type; the next one
will land somewhere else.

AC-13 deserves a note: it is the test that keeps ADR-015 honest. The friendly-traceback
feature is exactly the kind of thing that gets "simplified" later into a global hook, and
nothing else in the suite would notice.

AC-04 and AC-05 are the important two. They are AST tests rather than behavioral tests
because they must hold on *every* path, including ones no test exercises — that is exactly
where a security check gets accidentally bypassed.

## 5. Acceptance walkthrough (Day 1 → first deployment)

The Round 12 simulation, kept as a runnable checklist. It is re-run at the end of each
milestone; a step that stops working is a regression in the *plan*, not just the code.

| Step | Expected | Verifies |
|---|---|---|
| 0. `pip install harness && harness setup && harness new joker && python joker.py` | A working agent, from nothing, in four commands | SC-1b, ADR-013 |
| 0b. `ls -a` after `harness new` | `.gitignore` exists and lists `.env` | Register #36 |
| 1. Clone, `uv sync`, `pytest` | Green in < 60 s, no API key needed | SC-5, T-0.1 |
| 2. Read [§11](11-implementation-plan.md), pick T-0.2 | Contract, tests and DoD are unambiguous | Plan quality |
| 2b. **Follow every capitalized name in every [§04](04-interfaces.md) signature back to its definition** | All resolve within the package | Round 20 — the Round 12 walkthrough followed tasks, never types, and seven were undefined |
| 3. Implement T-0.2, run its tests | Pass; error messages match [§03.7](03-public-api.md#8-error-message-standard) | T-0.2 |
| 4. Wire T-0.5 loop with fakes | Eight integration scenarios pass | T-0.5 |
| 5. Run `examples/01_hello.py` with a real key | Real agent answers | M0 exit |
| 6. Add a tool with no `effect` | Import-time error listing four options | Register #9 |
| 7. Add `datetime.now()` to `job=` | Construction-time error with byte offset | Register #28 |
| 8. Build `Agent(tools=[search, send_email])` | `UnsafeToolSetError` naming both tools | RT-04 |
| 9. Set `budget="$0.01"` on a long task | Makes a call with a small derived `max_tokens`, then stops gracefully with partial text — **not** a refusal to start | SC-2a, ADR-017 |
| 10. Inspect the transcript | Every model call, tool call, verdict and cost present; no secrets | SC-7 |
| 11. `harness cost transcript.jsonl` | Reports spend and cache hit rate | T-5.1 |
| 12. `kill -9` mid-run, then `resume` | Completes correctly; no `write` re-executed | T-3.3 |
| 13. Deploy behind a web handler | Module-scope agent; cache hits across requests | §10.5 |
| 14. Run a failing agent on a terminal | No `asyncio` frames; full traceback still in the transcript | ADR-015 |
| 15. Pipe a run to a file | Zero progress output in the file | ADR-014 |

## 6. Sign-off

1.0 ships only when all of the following are simultaneously true. There is no partial
release.

- [ ] Every row in §1 green
- [ ] All 12 conformance tests in §4 green
- [ ] SC-1a thresholds met with real developers
- [ ] **SC-1b thresholds met with real children, including the add-your-own-tool half**
- [ ] 14/14 red team
- [ ] P-1 green: SC-2a exact, SC-2b within 1.05× over 3 000 adversarial runs
- [ ] Cache benchmark ≥ 90 %
- [ ] All 13 walkthrough steps in §5 pass on a clean machine
- [ ] Documentation builds with zero broken links; every docstring example executes
- [ ] `pip install harness` in a clean environment, and the five-line example runs

## 7. After 1.0 — keeping this package honest

A design package rots the moment the code diverges from it. Three practices, all cheap:

1. **Every PR that changes behavior described here updates the relevant document in the
   same PR.** A CI check flags PRs touching `src/harness/{run,agent,budget,policy}/` that
   do not touch `docs/`.
2. **The conformance tests in §4 are the executable half of this package.** They fail when
   the architecture drifts, which is the failure mode documentation alone cannot catch.
3. **A specified control is not a control until it has been run.** Rounds 24 and 25 found
   ten defects between them; every one had passed multiple readings, and one was a live
   secret leak to the model. Two of the three Round 25 findings were in decisions made by
   Round 19 — the round whose subject was executing contracts rather than reading them.

   Round 26 sharpened it further. Its three worst findings were features that **had** an
   owning task in the traceability matrix and were simply never implemented — `max_parallel_tools`
   (NFR-09 → T-2.7), duplicate suppression (T-2.5), the multi-turn breakpoint (§07.2.2).
   **Ownership is not implementation, and a matrix cannot tell the difference.**

   So the standing review question is not "is this reviewed?" but **"has this been run?"**
   Concretely: every red-team scenario, every conformance check and every property is an
   executable test that blocks merge, and a PR adding a *specification* for a safety control
   without the test that exercises it is incomplete.

4. **The decision logs are append-only.** Reversing a decision adds a superseding entry; it
   never edits the original. Future maintainers need to see what was tried and why it lost.
