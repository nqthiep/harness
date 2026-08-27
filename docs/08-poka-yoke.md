# 08 — Poka-Yoke Register

Fifty-five ways a competent developer could still get this wrong, and what in the design
stops them. The ranking is deliberate:

**Impossible** > **Import-time error** > **Construction-time error** > **First-run error** >
**Loud warning** > **Documented**

An item that only reaches "Documented" is a design smell, and each of the five such items
below states why it could not be raised further.

Entries 35–44 came from Round 13, when the beginner review was re-run against a child who
knows basic Python. **Five of the six blockers it found were outside the API entirely** —
which is why they are grouped separately below.

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
| 51 | **A frozen dataclass with a dict field used as a set member or cache key** — `TypeError` only on the path that happens to hash it | `__hash__ = None` declared explicitly on every such type; ordered containers keyed by a scalar instead. AC-22 checks the eq/hash pairing package-wide | Impossible |
| 52 | **A type used in a signature that nobody ever defined** — each implementer invents an incompatible version | Walkthrough step 2b traverses every capitalized name in [§04](04-interfaces.md) back to a definition | Plan review |
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
| 53 | **Malformed tool arguments discovered by the tool raising** — a round trip to learn what the API could have rejected | `strict: true` on every tool definition; the schema constraints that make it possible were already mandatory | Impossible |
| 54 | **Answer parsed from a string, failing sometimes, re-prompted** | `returns=` constrains and validates the answer; a validation failure is an error, never a silent `None` | Impossible when `returns=` is set |
| 15 | Tool returns 50 MB and poisons the context for the rest of the run | `max_result_tokens` (4 000) truncates with a marker the model can see | Impossible |
| 16 | A hanging tool holds the run open forever | `timeout_s` (30 s) on every tool, no opt-out | Impossible |
| 55 | **A run overshooting its wall-clock ceiling by a tool's timeout** — the one budget axis whose limit did not actually hold | Effective timeout is `min(timeout_s, remaining_wall_clock)`; the error names which term bound. P-8 multiplies every pair of numeric defaults | Impossible |
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
| 48 | **A `Secret` silently duplicated in a set or dict** because equal-by-value objects hashed by name | `__hash__ = None`. `TypeError`, never a silent duplicate | Impossible |
| 49 | **A credential retained forever as an `lru_cache` key**, beyond the redactor's reach | Same — unhashable cannot be a cache key | Impossible |
| 50 | **Every secret ever constructed retained until process exit** by the redaction registry | `WeakSet`; the redactor's reach ends with the secret's lifetime | Impossible |

## Cost

| # | Failure mode | Defense | Rank |
|---|---|---|---|
| 28 | `datetime.now()` in the system prompt → 10× cost forever, silently | Double render at construction (catches per-call drift) **plus** prefix comparison across the first two real calls (catches time drift). Both free. *The original 150 ms-sleep design caught neither `datetime.now()` to seconds nor `date.today()` — ADR-025.* | Construction or first two calls |
| 29 | Unlimited default budget → the overnight five-figure invoice | Defaults finite on all four axes; unlimited requires typing `None` and warns every run | Impossible by default |
| 30 | Budget checked after spending | `reserve()` before every call. Worst case on output; margin, upward calibration and a hard character bound on input (ADR-026) | Exact on authorization; bounded on spend |
| 31 | Float rounding drift in money arithmetic | `Decimal` throughout; a lint rule bans `float` in `budget/` | Impossible |
| 32 | Unknown model priced at zero, silently disabling the ceiling | `UnknownModelError` before any call | Impossible |
| 33 | Pricing table quietly going stale | CI job fails when `as_of` is more than 90 days old | CI |
| 46 | **A truncated answer reported as success** — the provider's `max_tokens` stop reason had no mapping, so `ok` was `True` for half a sentence | `StopReason.TRUNCATED` with `ok = False`; unmapped provider values map to `ERROR`, never success. P-9 tests the mapping against the protocol rather than against the design | Impossible |
| 47 | **A chat costing N × the budget** because nobody defined whether a budget covers a turn or a session | One ledger per `Chat` (ADR-020). Answers shorten as it depletes, then it ends | Impossible |
| 45 | **A budget too small to permit any call at all** — two independent knobs (`budget`, `max_tokens`) that multiply into one constraint | `max_tokens` is *derived* from the remaining budget (ADR-017), so the contradiction cannot be expressed. Below ~256 affordable output tokens the run stops rather than emitting a fragment. P-8 cross-validates every shipped numeric default against every other. | Impossible |

## Testing & operations

| # | Failure mode | Defense | Rank |
|---|---|---|---|
| 34 | A test accidentally calling the real API and billing the developer | `no_network()` active by default in the pytest fixture; any provider call raises | Impossible in CI |

## Getting started (Round 13)

The failures that stop someone before they reach the API at all.

| # | Failure mode | Defense | Rank |
|---|---|---|---|
| 35 | Beginner cannot supply an API key — `export VAR=...` is a shell concept, not a Python one | `harness setup`: one paste, validated immediately. Every credential error says `Run: harness setup`, never `set ANTHROPIC_API_KEY` | First run |
| 36 | **Beginner commits their API key to GitHub** | `harness new` writes `.gitignore` containing `.env` **in the same command** as the file that needs it — never a separate step to skip | Impossible via the scaffold |
| 37 | Fifteen seconds of silence reads as "broken"; user hits Ctrl-C | Live progress whenever `stdout` is a TTY; silent when piped, so production logs are unaffected | Impossible on a terminal |
| 38 | A 40-line traceback full of `asyncio` frames ends the session | Frames filtered locally in `run()`; `__suppress_context__` set. Full traceback still in the transcript and the `error.raised` event | First run |
| 39 | `Agent("Helper", "tell jokes")` → `TypeError: takes 0 positional arguments` | `*args` accepted **and `name`/`job` given sentinel defaults**, only to reject them with the corrected call printed. *The sentinels are load-bearing: Python validates required keyword-only parameters before the body runs, so without them this defense never executes — Round 24.* | Construction |
| 40 | Tool written without type hints returns `"34"` for `add(3, 4)` | Unannotated parameter is a `ToolSchemaError` showing the exact edit. **Never** defaulted to `str` — a silently wrong answer is worse than an error, because there is nothing to search for | Import |
| 41 | `effect="reed"` — a typo in a four-word vocabulary | Did-you-mean by edit distance, naming the intended value | Import |
| 42 | A curious user runs the script 80 times; per-run budget does nothing | Scaffold ships with `budget=`; one warning per process past `$5` cumulative; `harness setup` points at the provider's **hard** limit | Warning + provider-side ceiling |
| 43 | `print(result)` prints `<Result object at 0x...>` | `Result.__str__` returns the text; `+` still raises rather than silently concatenating | Impossible |
| 44 | Progress output leaks tool arguments containing PII | Tool **names** only, never arguments — consistent with #24 | Impossible |

---

## The five items that only reach "Documented", and why

| # | Why it cannot be raised |
|---|---|
| 18 | The harness cannot inspect a tool's internal shared state. The author's own `effect` classification *is* the control — which is exactly why `effect` is required rather than defaulted. |
| Plugin trust | Sandboxing Python meaningfully requires process or WASM isolation — a different product ([§01.5](01-requirements.md#5-non-goals)). We state the boundary plainly rather than imply one we do not enforce. |
| Model persuasion within reversible actions | The taint rule bounds the blast radius to reversible operations. It does not claim to stop a model being talked into a `read`. Stated in the user docs so nobody believes otherwise. |
| Resume after a partial `write` | A library cannot know whether an interrupted side effect landed. The harness declines to re-run `write`/`danger` and tells the model, which is the honest maximum ([§05.3](05-data-and-state.md#3-resume-semantics)). |
| Obtaining the API key itself (#35) | `harness setup` reduces it to one paste, but it cannot create an account or add a payment method. [§15](15-first-agent.md) says so plainly and marks it as the one step where SC-1b permits an adult to act — rather than pretending the wall is not there. |

## The rule this register enforces

> Every review of a change to this library asks one question:
> **can a competent person still get this wrong?**
> If yes, the answer is a design change, not a documentation change.
