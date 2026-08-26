# 09 — Testing

## 1. The constraint that shapes everything

An agent framework whose tests need an API key is a framework nobody runs tests for. Every
design choice below follows from one rule: **the full suite must run green, offline, in
under 60 seconds, for free.**

This is why `harness.testing` is an **M0 deliverable** and not a nice-to-have. A testing
story added at the end never gets adopted.

## 2. The pyramid

| Layer | Count (target) | Runtime | Network |
|---|---|---|---|
| Unit | ~400 | < 15 s | none |
| Property (Hypothesis) | ~20 properties | < 20 s | none |
| Integration (fakes) | ~80 | < 15 s | none |
| Golden / replay | ~30 fixtures | < 5 s | none |
| Red team | 14 scenarios | < 5 s | none |
| Live smoke (nightly, gated) | 6 | ~60 s | **real API** |

Only the last row costs money, runs only on `main` nightly, and is skipped when
`ANTHROPIC_API_KEY` is absent — so a contributor's first `pytest` always passes.

## 3. `harness.testing`

```python
from harness.testing import FakeModel, no_network, record, replay, assert_no_tool

model = FakeModel([
    FakeModel.tool_call("get_order", {"order_id": "A-4471"}),
    FakeModel.text("Order A-4471 shipped on Tuesday."),
])

agent  = Agent(name="T", job="...", tools=[get_order], provider=model)
result = agent.run("Where is A-4471?")

assert result.text.startswith("Order A-4471")
assert result.cost == Money("0")          # FakeModel is priced at zero
assert model.calls[0].tools[0]["name"] == "get_order"
```

| Helper | Purpose |
|---|---|
| `FakeModel(script)` | Scripted responses. Records every `ModelRequest` it receives, so tests can assert on the *prompt*, not just the output. |
| `FakeModel.from_transcript(path)` | Replays a recorded run — the basis of golden tests. |
| `no_network()` | Context manager (and autouse pytest fixture) that makes any socket use raise `NetworkAccessInTest`. |
| `record(agent, path)` | Wraps an agent to write a transcript from a real run — used once, by a human, to create a fixture. |
| `replay(path)` | Re-runs a transcript through the real engine with a fake provider and asserts the event stream matches byte for byte. |
| `assert_no_tool(result, name)` / `assert_tool_called(...)` | Readable assertions on behavior. |
| `approve_all()` / `deny_all()` | Deterministic approval callbacks. |

**`no_network()` is autouse.** A contributor cannot write a test that bills them, even by
mistake — the single highest-value Poka-Yoke in the test suite (register #34).

## 4. What is tested where

### Unit

Schema generation across every supported and unsupported type. Budget arithmetic in
`Decimal`. Verdict lattice composition. Effect profile derivation. Truncation at token
boundaries. `Secret` rendering across `repr`/`str`/`format`/`json`/`logging`/traceback.
Canonical JSON serialization. Budget string parsing (`"$0.10"`, `"10 cents"`, `"1m"`).

**Error-message conformance:** every `ConfigError` subclass is instantiated and asserted to
render all four required sections — what, where, the copy-pasteable fix, the docs anchor
([§03.7](03-public-api.md#7-error-message-standard)). Error messages are API.

### Property (Hypothesis)

| Property | Statement |
|---|---|
| P-1 | For any budget and any sequence of fake responses, `result.cost <= budget.usd`. **1 000 cases. SC-2.** |
| P-2 | Adding any policy to any policy list never lowers any verdict. |
| P-3 | Every `tool_use` block produces exactly one `tool_result` with a matching id (invariant I-3). |
| P-4 | For any tool-argument shape, the generated schema validates the arguments the model would produce. |
| P-5 | Serializing any `ToolSet` twice yields identical bytes. |
| P-6 | Replaying any transcript reproduces the same event sequence. |
| P-7 | Truncation never splits a UTF-8 character or produces invalid JSON. |

### Integration

The whole loop against `FakeModel`: multi-step tool use, parallel scheduling, denial paths,
budget stop, step stop, timeout, `pause_turn` resume, refusal handling, cancellation,
subagent delegation, chat continuation, resume-from-transcript.

### Golden / replay

Thirty recorded transcripts covering realistic shapes. `replay()` asserts the event stream
matches byte for byte. These are the regression net: a change to the loop that alters
observable behavior fails loudly with a readable diff.

### Live smoke (nightly)

Six scenarios that only a real API can validate: real schema acceptance, real cache hits,
real refusal handling, real `pause_turn`, real streaming, real token counting. Failures
open an issue rather than blocking a PR — they test the vendor, not the code.

## 5. Red-team suite

The 14 scenarios in [§06.8](06-safety.md#8-red-team-suite-m1-deliverable-in-ci). Run on
every PR. **A red-team failure blocks merge unconditionally** — it is not a flake and it is
never re-run to green.

## 6. CI gates

Every gate below must pass before merge. Each maps to a requirement or invariant.

| Gate | Threshold | Guards |
|---|---|---|
| `pytest` | 100 % pass | — |
| Coverage | ≥ 90 % overall; **100 % on `budget/`, `policy/`, `context/assembler.py`** | Invariants 2 and 3 |
| `mypy --strict` | 0 errors | NFR-04 |
| `ruff check` + `ruff format --check` | clean | — |
| Public-API snapshot | `__all__` diff requires an explicit changelog entry | API stability |
| Import-linter | L2 must not import L0 adapters | Layering ([§02.2](02-architecture.md)) |
| No-float-in-budget lint | 0 hits | Register #31 |
| Docstring examples | every `__all__` symbol has a runnable example, executed | NFR-10 |
| Pricing freshness | `as_of` within 90 days | Register #33 |
| Import time | `python -X importtime -c "import harness"` < 200 ms | NFR-01 |
| Benchmark: cache | ≥ 90 % cache reads on turns 3+ of the 10-turn fixture | SC-4 |
| Benchmark: overhead | < 15 ms p95 per step with a null provider | NFR-02 |
| Red team | 14/14 | SC-3 |
| Docs build | 0 broken internal links | — |

## 7. How a contributor writes a test for a new tool

Documented in `CONTRIBUTING.md` and enforced by example:

```python
def test_refund_requires_approval():
    model = FakeModel([FakeModel.tool_call("refund", {"order": "A-1"}),
                       FakeModel.text("done")])
    agent = Agent(name="T", job="...", tools=[refund],
                  provider=model, approve=deny_all())
    result = agent.try_run("refund order A-1")

    assert_no_tool(result, "refund")
    assert result.stop_reason is StopReason.COMPLETED   # denial is not a crash
```

Three lines of setup, no mocking library, no API key, no network.
