# 08 — Poka-Yoke Register

Thirty-four ways a competent developer could still get this wrong, and what in the design
stops them. The ranking is deliberate:

**Impossible** > **Import-time error** > **Construction-time error** > **First-run error** >
**Loud warning** > **Documented**

An item that only reaches "Documented" is a design smell, and each of the four such items
below states why it could not be raised further.

---

## API & configuration

| # | Failure mode | Defense | Rank |
|---|---|---|---|
| 1 | Positional args mis-assigned: `Agent("Bob", "answer questions")` | All `Agent` params keyword-only | Impossible |
| 2 | Mutating an agent mid-run, breaking cache and races | `Agent` is frozen; only `with_()` returns a new one | Impossible |
| 3 | Adding a tool mid-conversation, silently voiding the cache | `tools` frozen at construction; no add API | Impossible |
| 4 | Two tools with the same name, one silently shadowing the other | `DuplicateToolError` at construction, naming both source locations | Construction |
| 5 | Tool name illegal for the API (spaces, 80 chars) | Validated against `^[a-z][a-z0-9_]{0,63}$` at decoration | Import |
| 6 | Calling sync `.run()` inside a running event loop | Detected; `SyncInAsyncContextError` names `arun()` — instead of the opaque `RuntimeError: This event loop is already running` | First run |
| 7 | Typo in a parameter name silently ignored | Keyword-only + no `**kwargs` anywhere in the public API → `TypeError` | Impossible |
| 8 | Importing from a private path that later moves | `__all__` is the contract; a CI test fails if any example or doc imports outside it | CI |

## Tools

| # | Failure mode | Defense | Rank |
|---|---|---|---|
| 9 | Tool defined without anyone knowing what it does to the world | `effect=` required; `MissingEffectError` prints the four options and guesses from the name | Import |
| 10 | Marking a destructive tool parallel-safe by accident | Not configurable — derived from `effect` | Impossible |
| 11 | A `send_payment` retried into a duplicate payment | `write`/`danger` are never auto-retried; derived from `effect` | Impossible |
| 12 | A tool parameter type that cannot be expressed as JSON Schema | `ToolSchemaError` at import naming the parameter and type; no best-effort fallback | Import |
| 13 | Tool returns a non-serializable object; harness `str()`s it into garbage | `ToolContractError` at return, naming the field path | First run |
| 14 | Missing docstring → the model cannot tell what the tool does | Required; empty docstring is a `ToolSchemaError` | Import |
| 15 | Tool returns 50 MB and poisons the context for the rest of the run | `max_result_tokens` (4 000) truncates with a marker the model can see | Impossible |
| 16 | A hanging tool holds the run open forever | `timeout_s` (30 s) on every tool, no opt-out | Impossible |
| 17 | A raising tool crashes the whole run and loses accumulated work | Caught, converted to `is_error` result; run continues | Impossible |
| 18 | Tool assumes it is called serially and corrupts shared state | Concurrency is stated in the `@tool` docstring and derived from the effect the author chose | Documented — the harness cannot inspect a tool's internal state; the author's own `effect` choice is the control |

## Safety

| # | Failure mode | Defense | Rank |
|---|---|---|---|
| 19 | Fetched web content instructs the agent to exfiltrate data | Taint lattice: `external` output taints; tainted mode denies `danger` | Impossible without an explicit per-tool opt-in |
| 20 | Discovering the taint denial only after two paid steps | `UnsafeToolSetError` at `Agent()` construction, with the exact fix | Construction |
| 21 | A custom policy accidentally loosening security | Verdicts are a lattice composed with `max()`; a policy can only restrict. Property-tested. | Impossible |
| 22 | Secret printed by an f-string, log line, or traceback | `Secret` overrides `__repr__`/`__str__`/`__format__`; not JSON-serializable | Impossible |
| 23 | Secret reaching disk via the transcript | Redaction at write, before bytes exist; entropy scan for unwrapped keys | Impossible for registered secrets; loud warning otherwise |
| 24 | Tool arguments containing PII recorded by default | `tool.requested` stores a sha256 digest, not the arguments | Impossible by default |
| 25 | A tool reading the whole conversation and leaking it | `RunContext` has no message history, by design | Impossible |
| 26 | A transitively installed package registering tools silently | Entry-point discovery is opt-in (`discover=True`) | Impossible by default |
| 27 | A plugin registering a `danger` tool while declaring `read` | Declared capability ceiling enforced at registration | Registration |

## Cost

| # | Failure mode | Defense | Rank |
|---|---|---|---|
| 28 | `datetime.now()` in the system prompt → 10× cost forever, silently | Cache linter double-renders and byte-compares at construction | Construction |
| 29 | Unlimited default budget → the overnight five-figure invoice | Defaults finite on all four axes; unlimited requires typing `None` and warns every run | Impossible by default |
| 30 | Budget checked after spending | `reserve()` before every call; worst-case estimate | Impossible |
| 31 | Float rounding drift in money arithmetic | `Decimal` throughout; a lint rule bans `float` in `budget/` | Impossible |
| 32 | Unknown model priced at zero, silently disabling the ceiling | `UnknownModelError` before any call | Impossible |
| 33 | Pricing table quietly going stale | CI job fails when `as_of` is more than 90 days old | CI |

## Testing & operations

| # | Failure mode | Defense | Rank |
|---|---|---|---|
| 34 | A test accidentally calling the real API and billing the developer | `no_network()` active by default in the pytest fixture; any provider call raises | Impossible in CI |

---

## The four items that only reach "Documented", and why

| # | Why it cannot be raised |
|---|---|
| 18 | The harness cannot inspect a tool's internal shared state. The author's own `effect` classification *is* the control — which is exactly why `effect` is required rather than defaulted. |
| Plugin trust | Sandboxing Python meaningfully requires process or WASM isolation — a different product ([§01.5](01-requirements.md#5-non-goals)). We state the boundary plainly rather than imply one we do not enforce. |
| Model persuasion within reversible actions | The taint rule bounds the blast radius to reversible operations. It does not claim to stop a model being talked into a `read`. Stated in the user docs so nobody believes otherwise. |
| Resume after a partial `write` | A library cannot know whether an interrupted side effect landed. The harness declines to re-run `write`/`danger` and tells the model, which is the honest maximum ([§05.3](05-data-and-state.md#3-resume-semantics)). |

## The rule this register enforces

> Every review of a change to this library asks one question:
> **can a competent person still get this wrong?**
> If yes, the answer is a design change, not a documentation change.
