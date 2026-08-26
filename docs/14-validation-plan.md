# 14 — Validation Plan

> **Purpose.** CI proves the code does what the code says. This document proves the code
> does what *this design package* says. Those are different claims, and the second one is
> the one that decays silently.

## 1. Traceability

Every requirement maps to an executable check. A requirement with no check is not a
requirement, it is a wish.

| Req | Check | Where | Gate |
|---|---|---|---|
| G1 / SC-1 | Beginner study, 5 participants | T-5.4 | Blocks 1.0 |
| G2 / SC-2 | Property P-1, 1 000 adversarial runs | `tests/property/test_budget.py` | Blocks merge |
| G3 / SC-3 | Red-team suite, 14 scenarios | `tests/redteam/` | Blocks merge |
| G4 / SC-4 | Cache benchmark, 10-turn fixture | `benchmarks/cache.py` | Blocks merge |
| G5 / SC-5 | `no_network()` autouse; suite runs offline | CI | Blocks merge |
| G6 / SC-6 | 5 out-of-tree plugin examples | `examples/plugins/` | Blocks M4 |
| G7 / SC-7 | Golden replay, byte-identical | `tests/golden/` | Blocks merge |
| FR-01…18 | Named integration test per requirement | `tests/integration/` | Blocks merge |
| NFR-01 | `python -X importtime` < 200 ms | CI | Blocks merge |
| NFR-02 | Null-provider benchmark, < 15 ms p95 | `benchmarks/overhead.py` | Blocks merge |
| NFR-03 | Memory profile, 100-step run < 50 MB | `benchmarks/memory.py` | Blocks merge |
| NFR-04 | `mypy --strict` | CI | Blocks merge |
| NFR-05 | Dependency count assertion | CI | Blocks merge |
| NFR-07 | Property P-6 | `tests/property/` | Blocks merge |
| NFR-08 | Tool-raises integration test | `tests/integration/` | Blocks merge |
| NFR-10 | Docstring examples executed | CI | Blocks merge |
| ADR-001…011 | Architecture conformance tests (§4) | `tests/conformance/` | Blocks merge |

## 2. SC-1 — Time to First Agent

The measurement the whole DX argument rests on. Run at T-5.4, before 1.0.

**Protocol**
- **Participants:** 5 Python developers who have never seen the library. At least two
  should have no prior LLM-API experience — that is the population the design claims to
  serve.
- **Materials:** the README and nothing else. No walkthrough, no help.
- **Task:** "Build an agent that answers questions using the web, and run it."
- **Observation:** screen recording plus think-aloud. The observer does not answer
  questions; unanswered questions are the data.
- **Measured:** wall-clock to a first successful run; number of times the participant left
  the README to look something up; every point of confusion, verbatim.

**Thresholds**

| Metric | Pass |
|---|---|
| Median time to first successful run | ≤ 10 min |
| Participants succeeding unaided | ≥ 4 / 5 |
| Participants who had to read library source | 0 |
| Distinct confusion points reported by ≥ 2 participants | 0 |

**On failure.** 1.0 is blocked and the council reconvenes on the API — not on the docs.
If the fix is "explain it better", the API is wrong. This is stated in advance so the
result cannot be rationalized after the fact.

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

AC-04 and AC-05 are the important two. They are AST tests rather than behavioral tests
because they must hold on *every* path, including ones no test exercises — that is exactly
where a security check gets accidentally bypassed.

## 5. Acceptance walkthrough (Day 1 → first deployment)

The Round 12 simulation, kept as a runnable checklist. It is re-run at the end of each
milestone; a step that stops working is a regression in the *plan*, not just the code.

| Step | Expected | Verifies |
|---|---|---|
| 1. Clone, `uv sync`, `pytest` | Green in < 60 s, no API key needed | SC-5, T-0.1 |
| 2. Read [§11](11-implementation-plan.md), pick T-0.2 | Contract, tests and DoD are unambiguous | Plan quality |
| 3. Implement T-0.2, run its tests | Pass; error messages match [§03.7](03-public-api.md#7-error-message-standard) | T-0.2 |
| 4. Wire T-0.5 loop with fakes | Eight integration scenarios pass | T-0.5 |
| 5. Run `examples/01_hello.py` with a real key | Real agent answers | M0 exit |
| 6. Add a tool with no `effect` | Import-time error listing four options | Register #9 |
| 7. Add `datetime.now()` to `job=` | Construction-time error with byte offset | Register #28 |
| 8. Build `Agent(tools=[search, send_email])` | `UnsafeToolSetError` naming both tools | RT-04 |
| 9. Set `budget="$0.01"` on a long task | Graceful `BUDGET_EXHAUSTED` with partial text | SC-2 |
| 10. Inspect the transcript | Every model call, tool call, verdict and cost present; no secrets | SC-7 |
| 11. `harness cost transcript.jsonl` | Reports spend and cache hit rate | T-5.1 |
| 12. `kill -9` mid-run, then `resume` | Completes correctly; no `write` re-executed | T-3.3 |
| 13. Deploy behind a web handler | Module-scope agent; cache hits across requests | §10.5 |

## 6. Sign-off

1.0 ships only when all of the following are simultaneously true. There is no partial
release.

- [ ] Every row in §1 green
- [ ] All 12 conformance tests in §4 green
- [ ] SC-1 thresholds met with real participants
- [ ] 14/14 red team
- [ ] P-1 green at 1 000 cases
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
3. **The decision logs are append-only.** Reversing a decision adds a superseding entry; it
   never edits the original. Future maintainers need to see what was tried and why it lost.
