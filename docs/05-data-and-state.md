# 05 — Data & State

## 1. The event taxonomy (closed)

Seventeen kinds. Closed on purpose: an open taxonomy becomes a log-message dump within a
year, and nothing downstream can rely on it. New kinds require a minor version and a
decision-log entry — `budget.unlimited` was the one addition since the original fifteen
(ADR-041, [§12](12-decision-logs.md)); `progress.stalled` (the mechanical stall detector,
`progress.py`) is the next.

**Envelope v1** (T-8.1, ADR-048): every `Event` also carries `schema_version` (bumped
only on a breaking shape change — an additive field with a default does not need one),
`trace_id` (defaults to `run_id` — one run is one trace until a real distributed
tracer/OTel exporter — T-8.3 — propagates one in), `tenant_id` and `session_id` (both
`None` unless the caller supplies one via `Agent(tenant_id=..., session_id=...)` /
`build_agent(tenant_id=...)` — the LangGraph backend's `session_id` is always its own
`thread_id`, the closest thing that backend has to a session identity today).

| Kind | When | `data` payload |
|---|---|---|
| `run.started` | Once, first | `agent`, `model`, `budget`, `tool_names[]`, `safety`, `harness_version` |
| `run.finished` | Once, last | `stop_reason`, `steps`, `cost_usd`, `tainted`, `duration_s`, `input_tokens`/`output_tokens`/`cache_read_tokens`/`cache_creation_tokens` (N-6, closed — the durable engine's `duration_s`/usage are per-TURN: it emits `run.finished` once per `graph.ainvoke()`, unlike `run.started`, once per thread ever) |
| `step.started` | Each loop iteration | `step` |
| `step.finished` | Each loop iteration | `step`, `stop_reason`, `tool_calls` |
| `model.request` | Before each call | `model`, `input_tokens`, `n_tools`, `n_messages`, `breakpoints`, `estimate_usd` |
| `model.response` | After each call | `stop_reason`, `cost_usd`, `latency_ms`, `input_tokens`/`output_tokens`/`cache_read_tokens`/`cache_creation_tokens` (N-6, closed — `OtelExporter`'s `gen_ai.usage.*` attributes now have real data) |
| `budget.reserved` | Before each call | `estimate_usd`, `spent_usd`, `remaining_usd` |
| `budget.exhausted` | Ceiling hit | `axis` (`usd`\|`steps`\|`time`\|`tokens`), `spent`, `limit` |
| `budget.unlimited` | Once, right after `run.started`, only when `budget.usd is None` | `reason` |
| `tool.requested` | Model asked | `tool`, `call_id`, `arguments_digest` (sha256, not the arguments) |
| `policy.decided` | Per call, always | `tool`, `call_id`, `verdict`, `reason`, `policy`, `actor`/`evidence` (S-11, closed — only when the call went through `PolicyEngine.resolve()`; `null` otherwise, and `evidence` never carries a raw `signature`, only `has_signature`) |
| `tool.arguments_amended` | Only when a `Middleware.before_tool` returns kwargs different from the ones `Policy` ruled on | `tool`, `middleware`, `fields` (which keys changed), `arguments` — digested at rest by the same rule as `tool.requested`, so an auditor compares two digests and sees they differ without either being exposed. A hook cannot reach a call `Policy` denied, so it cannot bypass a verdict; it CAN change the subject of one, and this is the row that says so |
| `tool.started` | Only if ALLOW; once per attempt (T-6.3 retry) | `tool`, `call_id`, `parallel`, `attempt` |
| `tool.finished` | Per executed call | `tool`, `call_id`, `duration_ms`, `is_error`, `result_tokens`, `truncated`, `replayed` (S-4/N-8, closed — `True` when `execute_once` returned a cached result instead of calling the tool again for this call_id) |
| `taint.raised` | First tainted content | `source_tool`, `call_id` |
| `context.managed` | Editing or compaction ran | `strategy` (`edited` \| `compacted` \| `compact_needed`), `tokens_before`, `messages`, `messages_dropped` |
| `error.raised` | Any handled error | `where`, `type`, `message`, `retryable`, `attempt`. `wait_s` too for a provider retry (N-5) — one event per retry attempt, never for the final, re-raised failure |
| `progress.stalled` | Tool calls kept happening but nothing NEW did, for `STALL_AFTER` (6) steps in a row | `stalled_steps` — the run stops with `StopReason.STALLED`, both backends (see `progress.py`) |

**Design rules for payloads**

- `tool.requested` carries a **digest** of the arguments, not the arguments. Tool arguments
  routinely contain user PII and secrets; a transcript that captures them by default is a
  data-leak generator. Full arguments are recorded only when `transcript_level="debug"` is
  explicitly set, and are redaction-scanned first.
- Every payload is JSON-serializable with sorted keys, so transcripts diff cleanly.
- `policy.decided` is emitted for **every** call including `ALLOW`. An audit log that only
  records denials cannot prove what was permitted.

## 2. Transcript format

Append-only JSONL, one `Event` per line, written through a redaction pass.

```jsonl
{"seq":0,"ts":1756...,"run_id":"r_01J...","kind":"run.started","step":null,"data":{...}}
{"seq":1,"ts":1756...,"run_id":"r_01J...","kind":"step.started","step":0,"data":{"step":0}}
{"seq":2,...,"kind":"model.request","step":0,"data":{"input_tokens":1840,...}}
```

| Property | Guarantee | Why |
|---|---|---|
| Append-only | Never rewritten or truncated | An audit log you can edit is not an audit log |
| Monotonic `seq` | Gap-free within a run | A gap proves loss, which is the point of having it |
| `fsync` policy | On `run.finished`, on `error.raised`, and every 64 events | Crash-durable without an fsync per line |
| Redaction | Applied at write, before the bytes exist — **and at the tool-result boundary**, because a tool error reaches the model, which the transcript boundary never sees (Round 25) | Redacting on read means the secret was already on disk |
| Rotation | Caller's concern; the harness never deletes a transcript | Silent deletion of audit data is unacceptable |
| Encoding | UTF-8, `ensure_ascii=False`, sorted keys | Diffable, greppable |

`run_id` is a ULID (`r_` + 26 chars): sortable by time, collision-free, no coordination.

## 3. Resume semantics

`Agent.resume(transcript)` replays a transcript to reconstruct message history, spend, step
count and taint state, then continues the loop.

**What resume guarantees:** the conversation continues from the last complete step, with
the budget correctly reduced by what was already spent.

**What resume does not guarantee — stated plainly:** if the process died *after* a
`tool.started` but *before* the matching `tool.finished`, the harness cannot know whether
the side effect occurred. Its behavior:

- `read` / `external` tools → re-executed (idempotent by their effect class).
- `write` / `danger` tools → **never** re-executed. The run resumes with an `is_error`
  tool result reading `"interrupted; not retried automatically"`, letting the model decide
  how to proceed.

This is the honest boundary of a library-level solution. Exactly-once side effects require
a durable execution engine, which is a stated non-goal ([§01.5](01-requirements.md#5-non-goals)).
The effect classification is what makes even this much possible.

## 4. Conversation state

```python
@dataclass(frozen=True, slots=True)
class Message:
    role: Literal["user", "assistant", "system"]
    content: tuple[ContentBlock, ...]
```

Held in the `Run`, never in the `Agent` — the `Agent` is a frozen template, so one agent
can serve many concurrent runs without cross-contamination. That property is what makes an
`Agent` safe to define at module scope and share across web requests, which is how most
people will actually use it.

`Chat` wraps a `Run` and retains `messages` between `say()` calls.

**Mid-conversation instructions** (`chat.tell("be terse")`) are appended as a
`{"role": "system"}` message rather than by editing the top-level system prompt. Editing
the system prompt would invalidate the entire cached prefix, re-billing the whole
conversation at full price; a `role: "system"` message sits after the cached history and
costs nothing extra. It is also the non-spoofable operator channel: text injected into a
user or tool message can be forged by anything that writes user-visible content, a
`role: "system"` message cannot. On providers or models that reject it (HTTP 400), the
harness falls back to a `<system-reminder>` block in the user turn, once, and caches the
fallback decision per model.

## 5. Memory schema (SQLite)

```sql
CREATE TABLE memos (
  key         TEXT PRIMARY KEY,
  value       TEXT NOT NULL,
  agent       TEXT NOT NULL,
  created_at  REAL NOT NULL,
  updated_at  REAL NOT NULL,
  expires_at  REAL
) STRICT;

CREATE INDEX idx_memos_agent   ON memos(agent);
CREATE INDEX idx_memos_expires ON memos(expires_at) WHERE expires_at IS NOT NULL;

<!-- This block listed a `CREATE VIRTUAL TABLE memos_fts USING fts5(...)` from Round 7
     until ADR-113 and no such table has ever been created. `search` is a linear scan
     scored in Python: 2.7 ms at 1,000 memos, 171.7 ms at 50,000. Fine for a per-agent
     scratchpad; adding FTS5 would be a schema migration nobody asked for, so the claim
     went instead of the code. -->

CREATE TABLE schema_meta (version INTEGER NOT NULL) STRICT;
```

- `STRICT` tables: SQLite's type affinity silently accepts a string into an integer column,
  which produces a bug you find months later.
- Expiry is enforced on read as well as by a sweep. A `ttl_s` that only works when the
  sweep has run is not a TTL.
- `schema_meta.version` is checked at open. A newer schema than the library supports raises
  rather than corrupting data by guessing.
- Writes use WAL mode and a busy timeout, so a second process reading the same file does
  not produce `database is locked`.

**Memory is not automatically injected into the prompt.** The agent must call a memory tool
to recall. Auto-injecting memory would silently grow the prefix, invalidate the cache, and
raise cost on every single call — the opposite of invariant 2. This surprises people, so it
is documented at the top of the memory guide.
