# 10 — Observability & Operations

## 1. One stream, many renderings

Everything observable comes from the single event stream in
[§05.1](05-data-and-state.md#1-the-event-taxonomy-closed). The console output, the JSONL
transcript, and the OpenTelemetry spans are three renderings of the same 15 events — so
they cannot disagree with each other, and a new exporter cannot see less than the
transcript does.

```
RunEngine ──emit()──> EventBus ──┬──> ConsoleExporter    (human, dev)
                                 ├──> TranscriptWriter   (JSONL, audit)
                                 ├──> OtelExporter       (spans + metrics, prod)
                                 └──> user exporters
```

The bus is synchronous and ordered. An exporter that raises is caught, converted to one
`error.raised` event, and **disabled for the remainder of the run**. A telemetry bug must
never take down an agent; that is a self-inflicted outage.

## 2. OpenTelemetry mapping

Optional extra (`pip install harness[otel]`). Attribute names follow the GenAI semantic
conventions where they exist.

| Event | OTel |
|---|---|
| `run.started` / `run.finished` | Span `harness.run`, attrs `gen_ai.agent.name`, `gen_ai.request.model` |
| `step.started` / `step.finished` | Child span `harness.step` |
| `model.request` / `model.response` | Child span `gen_ai.chat`, attrs `gen_ai.usage.input_tokens`, `.output_tokens`, `harness.cache_read_tokens`, `harness.cost_usd` |
| `tool.started` / `tool.finished` | Child span `harness.tool`, attrs `harness.tool.name`, `.effect`, `.truncated` |
| `policy.decided` | Span event on the tool span |
| `budget.exhausted`, `taint.raised`, `error.raised` | Span events, span status `ERROR` where applicable |

Metrics: `harness.run.cost` (histogram, USD), `harness.run.steps`, `harness.tool.duration`,
`harness.cache.hit_ratio`, `harness.policy.denials` (counter, by tool and reason).

**Prompt and completion text are never exported by default.** Sending conversation content
to a telemetry backend is a data-governance decision the library must not make silently.
`OtelExporter(include_content=True)` is available and documented with its implications.

## 2.1 Console behavior and traceback rendering

Two beginner-facing behaviors that production users should know are inert for them.

**Progress output.** When `sys.stdout.isatty()`, a `ConsoleExporter` is attached
automatically and writes live progress to **stderr** — tool names, step transitions, final
cost. When output is piped or redirected, nothing is emitted. Every non-interactive context
is therefore silent by default, and stdout stays clean even on a terminal (IDL-24).
Arguments are never printed, only tool names (register #44).

**Traceback filtering.** `run()` removes harness-internal and `asyncio` frames from
exceptions it re-raises and sets `__suppress_context__`. This is done **locally, inside the
call** — the package assigns nothing to `sys.excepthook` or any other global at import, and
AC-13 asserts it. A library that mutates interpreter state on import breaks debuggers and
error reporters in every application that embeds it.

**Nothing is lost.** The unfiltered traceback is recorded in the `error.raised` event and
the transcript. `HARNESS_FULL_TRACEBACK=1` restores full console rendering. Filtering is a
console concern, never a diagnostic one — which is precisely why it was acceptable.

## 3. Provider error mapping

The Anthropic adapter maps vendor exceptions to the harness hierarchy. Nothing else in the
codebase catches a vendor exception type — that is what keeps the provider seam real.

**N-5, closed.** A provider error is CAUGHT and, on the class of failure the table below
marks retryable, retried automatically — `src/harness/retry.py::with_provider_retry()`,
one implementation shared by both backends (`run.py`'s `self._p.complete(...)`,
`lg/adapter.py::ProviderChatModel._generate()`'s `self.provider.complete(...)`). Only on
final failure (retries exhausted, or the error class isn't retryable at all) does
`try_run()`/`atry_run()` return `Result(stop_reason=ERROR)` — it still never raises for
this. `EFFECT_PROFILES.retryable`-driven retry (T-6.3) stays a separate, TOOL-call
mechanism, a different layer.

| Vendor | Harness | Retry |
|---|---|---|
| `AuthenticationError`, `PermissionDeniedError` (401/403) | `ProviderAuthError` | No |
| `BadRequestError`, `NotFoundError` (400/404) | `ProviderBadRequest` | No |
| `RateLimitError` (429) | `ProviderRateLimited` | Yes — honors a real `Retry-After` header when the vendor sent one (`models/anthropic.py::_retry_after()`), exponential backoff with jitter otherwise |
| `InternalServerError` (5xx) | `ProviderUnavailable` | Yes — exponential backoff |
| `APIConnectionError`, `APITimeoutError` | `ProviderTimeout` | Yes — exponential backoff |
| HTTP 200 + `stop_reason == "refusal"` | *not* an error → `StopReason.MODEL_REFUSAL` | No |

The refusal row matters more than it looks. Current models return a refusal as a **200**
with a `stop_details` category. Code that reads `content` without checking `stop_reason`
presents a confident empty answer. The harness makes it an explicit outcome, and enables
server-side refusal fallbacks by default so a routine refusal is routed to a fallback model
rather than surfacing as a dead end.

Retry budget is bounded by the run's wall clock (`Ledger.remaining_wall_clock()`, passed
as `retry.py`'s `deadline_s=`), never by an independent retry count alone — retries
cannot outlive the budget. `error.raised`'s `retryable=` field is stamped on every
attempt (`attempt=`/`wait_s=` alongside it — one event per retry, never for the final,
re-raised failure) and on the final failure too, when its exception class is inherently
transient-typed, whether or not a retry actually ran.

## 4. Packaging, versioning, release

**Packaging.** `src/` layout, `pyproject.toml`, PEP 621 metadata, `uv` for development.
Required runtime dependencies: `anthropic`, `typing-extensions`, `jsonschema` (NFR-05).
Extras: `[otel]`, `[sqlite]` (stdlib, extra exists for symmetry), `[cli]`, `[dev]`.
Ships `py.typed`.

**Versioning.** Semantic versioning over the [§04.8](04-interfaces.md#8-stability) table.

| Change | Bump |
|---|---|
| Removing or renaming anything in `__all__` | Major |
| Changing a protocol method signature | Major |
| Changing a default that alters cost or safety behavior | Major, and called out at the top of the release notes |
| Adding a public symbol, an `EventKind`, or an optional parameter | Minor |
| Fixing behavior that contradicts this design package | Patch |

Deprecations warn for two minor releases before removal, with the replacement named in the
warning text.

**Release.** Tag → GitHub Actions → build → full gate suite → publish to PyPI via Trusted
Publishing (no long-lived token). Artifacts are hash-pinned; the lockfile is committed.
`CHANGELOG.md` is generated from PR labels and edited by a human before release.

## 5. Running in production

The library is in-process, so "deployment" means whatever hosts the calling code.
Documented recipes for the three real shapes:

| Shape | Guidance |
|---|---|
| **Web request handler** | Construct `Agent` at **module scope**, not per request. Per-request construction re-runs the cache linter, rebuilds the prefix, and (worse) creates per-request prefixes that never share a cache — hence the `cache.per_request_agent` warning. |
| **Background worker** | One `Agent` per worker process. Transcript to durable storage. Use `try_run` and act on `stop_reason`. |
| **Notebook / script** | `agent.run()` directly; progress shown automatically on a terminal, silent when piped. |

Health signals worth alerting on, in priority order: `policy.denials` rate (a spike means
either an attack or a broken tool), `budget.exhausted` rate (budgets too tight, or a loop),
`cache.hit_ratio` drop (someone reintroduced an invalidator — cost doubles quietly),
`error.raised{retryable:false}` rate.

## 5.5 Cost per successful task (T-8.4)

`harness.eval.cost_per_success(runs)` — `total_cost / P(success)`, never a bare number:
always a `CostPerSuccess` carrying `cost_per_success_usd` alongside a Wilson-scored
confidence interval (`ci_low_usd`/`ci_high_usd`) on the underlying success rate,
propagated onto the cost figure itself.

**Why not cost-per-task.** A run that fails still burned tokens — it is not free — so
averaging total spend over every attempt rewards a policy that succeeds rarely but
cheaply per attempt over one that succeeds reliably at a slightly higher per-attempt
cost. The honest denominator is runs that actually succeeded, not runs attempted.

```python
from harness.eval import cost_per_success

runs = [agent.try_run(task) for task in golden_set]
print(cost_per_success(runs))
# "$0.0412 per success ($0.0298–$0.0601 at 95% CI, 47/50 succeeded)"
```

`cost_per_success_usd` is `None` — not `0` and not `inf` — when nothing succeeded: money
was spent and the number this function would otherwise report is undefined, which is a
fact worth surfacing loudly rather than papering over with a placeholder (§45: "would
rather say 'not enough evidence' than guess"). `runs` needs only `.ok: bool` and a
`Money`-shaped-or-numeric `.cost` — any `Result`, or a compatible record from a
golden-set run (M10), works without constructing one.

## 6. Upgrade & migration

- Transcripts carry `harness_version` in `run.started`. The reader supports the current
  major version and refuses older formats explicitly rather than mis-parsing them.
- SQLite `schema_meta.version` is checked at open; forward-only migrations run
  automatically, and a newer-than-supported schema raises.
- Protocol `API_VERSION` mismatch fails at plugin registration, naming the required
  version — never at run time.
- `harness doctor` reports version, key presence and source, pricing-table age, cache
  determinism for a given agent module, and plugin API compatibility. It is the first thing to run when
  something is wrong, and the first thing to attach to a bug report.
