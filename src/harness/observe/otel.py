"""T-8.3 — a real OTel exporter, docs/10-observability-ops.md §2 (already specified the
exact mapping before any code existed behind `[otel]`) / docs/17-research-alignment.md M8.

**Checked docs/10 §2 before writing this** — the mapping, span names, attribute names,
and metric names below are the ones already published there, not invented fresh:

| Event | OTel |
|---|---|
| `run.started`/`run.finished` | span `harness.run`, attrs `gen_ai.agent.name`, `gen_ai.request.model` |
| `step.started`/`step.finished` | child span `harness.step` |
| `model.request`/`model.response` | child span `gen_ai.chat`, attrs `gen_ai.usage.input_tokens`, `.output_tokens`, `harness.cache_read_tokens`, `harness.cost_usd` |
| `tool.started`/`tool.finished` | child span `harness.tool`, attrs `harness.tool.name`, `.effect`, `.truncated` |
| `policy.decided` | span event on the TOOL span |
| `budget.exhausted`, `taint.raised`, `error.raised` | span events, span status `ERROR` where applicable |

Metrics: `harness.run.cost` (histogram, USD), `harness.run.steps` (histogram),
`harness.tool.duration` (histogram, ms), `harness.cache.hit_ratio` (histogram),
`harness.policy.denials` (counter, by `tool`/`reason`).

**N-6, closed:** this module's own attribute mapping (`gen_ai.usage.output_tokens`,
`harness.cache_read_tokens` below) is what surfaced the gap in the first place —
`model.response`/`run.finished` used to emit only `stop_reason`/`cost_usd`/`steps`/
`tainted`, never the `usage`/`latency_ms`/`duration_s` docs/05's own event table always
promised. `run.py` and `lg/runtime.py::call_model`/`finish` now emit them on both
backends (`design/07-risks-and-open-issues.md` N-6) — this exporter needed no change at
all, since its mapping was already written against the promised shape, waiting for data.

**`include_content=False` (default)** strips any data key this module recognizes as
carrying raw prompt/completion/tool-argument text before it reaches a span attribute —
sending conversation content to a telemetry backend is a data-governance decision this
library must not make silently (docs/10 §2's own line). No event `data` payload
currently carries such a key (docs/05: `tool.requested` carries a digest, never
arguments; nothing carries model text) — this is a floor for when one does, not a
no-op limitation on today's events.

Imported lazily, and never from `harness/__init__.py`: core stays at 3 dependencies,
under 100 ms import (IDL-07) — the same reason `AnthropicProvider` only imports inside
`atry_run()`, not at package load.
"""
from __future__ import annotations

from typing import Any

from .events import Event, EventKind

_SCALAR = (str, int, float, bool)
#: Keys never exported as span attributes regardless of `include_content` — literal
#: conversation text, if a future event ever carries one of these under this name.
_CONTENT_KEYS = frozenset({"message", "text", "content", "arguments", "prompt"})


def _safe_attrs(prefix: str, data, *, include_content: bool) -> dict[str, Any]:
    out = {}
    for k, v in data.items():
        if not include_content and k in _CONTENT_KEYS:
            continue
        if isinstance(v, _SCALAR):
            out[f"{prefix}.{k}"] = v
        elif isinstance(v, (list, tuple)) and all(isinstance(x, _SCALAR) for x in v):
            out[f"{prefix}.{k}"] = list(v)
    return out


class OtelExporter:
    """`Exporter` protocol (`emit`, `close`) over a real OpenTelemetry `Tracer`/`Meter`.

    Pass an existing `tracer`/`meter` (from a host application's own
    `TracerProvider`/`MeterProvider`) to plug into infrastructure that already exists;
    omit either and this builds its own provider — a reasonable default for a
    standalone script, not what a service with its own OTel setup should use (a second
    provider would export twice).
    """

    def __init__(self, tracer: Any = None, meter: Any = None, *,
                 service_name: str = "harness", include_content: bool = False) -> None:
        from opentelemetry import trace as _trace

        self._trace = _trace
        self._include_content = include_content
        if tracer is not None:
            self._tracer = tracer
        else:
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
            self._tracer = provider.get_tracer("harness")
        if meter is not None:
            self._meter = meter
        else:
            from opentelemetry.sdk.metrics import MeterProvider
            self._meter = MeterProvider().get_meter("harness")
        self._cost_hist = self._meter.create_histogram(
            "harness.run.cost", unit="usd", description="Total spend per run")
        self._steps_hist = self._meter.create_histogram(
            "harness.run.steps", description="Steps taken per run")
        self._tool_duration_hist = self._meter.create_histogram(
            "harness.tool.duration", unit="ms", description="Tool call duration")
        self._cache_hit_hist = self._meter.create_histogram(
            "harness.cache.hit_ratio", description="Cache-read tokens / total input tokens")
        self._policy_denials = self._meter.create_counter(
            "harness.policy.denials", description="Policy denials, by tool and reason")

        # Every store is keyed by something already unique in the envelope — never a
        # counter this class would have to maintain itself.
        self._root: dict[str, Any] = {}                      # run_id -> Span
        self._step: dict[tuple[str, int | None], Any] = {}    # (run_id, step) -> Span
        self._model: dict[tuple[str, int | None], Any] = {}   # (run_id, step) -> Span
        self._tool: dict[tuple[str, Any], Any] = {}            # (run_id, call_id) -> Span
        # `policy.decided` fires BEFORE `tool.started` for an ALLOWed call
        # (dispatch.py: policy is resolved for every planned call before any of them
        # run) — so at the moment it arrives, the tool span it belongs on (docs/10 §2)
        # does not exist yet. Buffered here, keyed the same as `_tool`, and flushed the
        # moment that span IS created; a DENYed or duplicate-and-never-run call gets
        # its own tool span, so its buffered event is flushed onto the step span
        # instead — at STEP_FINISHED, or immediately for a DENY (no tool span is ever
        # coming for one).
        self._pending_policy: dict[tuple[str, Any], Event] = {}

    def _envelope_attrs(self, ev: Event) -> dict[str, Any]:
        a: dict[str, Any] = {"harness.run_id": ev.run_id,
                             "harness.schema_version": ev.schema_version}
        if ev.trace_id is not None:
            a["harness.trace_id"] = ev.trace_id
        if ev.tenant_id is not None:
            a["harness.tenant_id"] = ev.tenant_id
        if ev.session_id is not None:
            a["harness.session_id"] = ev.session_id
        return a

    def _attrs(self, ev: Event, *, rename: dict[str, str] | None = None) -> dict[str, Any]:
        data = dict(ev.data)
        renamed = {}
        for src, dst in (rename or {}).items():
            if src in data:
                renamed[dst] = data.pop(src)
        merged = {**self._envelope_attrs(ev), **renamed,
                 **_safe_attrs("harness", data, include_content=self._include_content)}
        return merged

    def _open_span_for(self, ev: Event):
        """The span a bare span-EVENT kind attaches to: the tool span if this event
        carries a `call_id` matching one currently open, else the step span, else the
        run's own root span, else nothing (a stray event before `run.started`/after
        `run.finished` — logged nowhere, never crashes; IDL-10 — an exporter's own bug
        must never take a run down). `policy.decided` always carries a `call_id`
        (docs/05), which is what routes it onto the tool span per docs/10 §2's table."""
        call_id = ev.data.get("call_id")
        if call_id is not None:
            tool_span = self._tool.get((ev.run_id, call_id))
            if tool_span is not None:
                return tool_span
        step_span = self._step.get((ev.run_id, ev.step))
        if step_span is not None:
            return step_span
        return self._root.get(ev.run_id)

    def _start(self, name: str, ev: Event, parent, *, rename: dict[str, str] | None = None):
        ctx = self._trace.set_span_in_context(parent) if parent is not None else None
        span = self._tracer.start_span(name, context=ctx, attributes=self._attrs(ev, rename=rename))
        return span

    def _end(self, span, ev: Event, *, rename: dict[str, str] | None = None) -> None:
        for k, v in self._attrs(ev, rename=rename).items():
            if k not in self._envelope_attrs(ev):    # already set at start; avoid noise
                span.set_attribute(k, v)
        if ev.data.get("is_error") or ev.kind is EventKind.ERROR_RAISED:
            span.set_status(self._trace.Status(self._trace.StatusCode.ERROR,
                                               description=str(ev.data.get("message", ""))))
        span.end()

    def emit(self, ev: Event) -> None:
        if ev.kind is EventKind.RUN_STARTED:
            self._root[ev.run_id] = self._start(
                "harness.run", ev, None,
                rename={"agent": "gen_ai.agent.name", "model": "gen_ai.request.model"})
        elif ev.kind is EventKind.RUN_FINISHED:
            span = self._root.pop(ev.run_id, None)
            if span is not None:
                self._end(span, ev)
            cost = _parse_float(ev.data.get("cost_usd"))
            if cost is not None:
                self._cost_hist.record(cost, {"harness.run_id": ev.run_id})
            if ev.data.get("steps") is not None:
                self._steps_hist.record(ev.data["steps"], {"harness.run_id": ev.run_id})
        elif ev.kind is EventKind.STEP_STARTED:
            self._step[(ev.run_id, ev.step)] = self._start(
                "harness.step", ev, self._root.get(ev.run_id))
        elif ev.kind is EventKind.STEP_FINISHED:
            span = self._step.pop((ev.run_id, ev.step), None)
            # Flush any policy.decided still waiting for a tool span that is never
            # coming (a duplicate call, T-2.5/S-26 — decided but never dispatched)
            # before this step's span closes, so the event isn't lost.
            for key in [k for k in self._pending_policy if k[0] == ev.run_id]:
                flushed_ev = self._pending_policy.pop(key)
                target = span if span is not None else self._root.get(ev.run_id)
                if target is not None:
                    target.add_event(flushed_ev.kind.value, attributes=self._attrs(flushed_ev))
            if span is not None:
                self._end(span, ev)
        elif ev.kind is EventKind.MODEL_REQUEST:
            parent = self._step.get((ev.run_id, ev.step)) or self._root.get(ev.run_id)
            self._model[(ev.run_id, ev.step)] = self._start(
                "gen_ai.chat", ev, parent, rename={"input_tokens": "gen_ai.usage.input_tokens"})
        elif ev.kind is EventKind.MODEL_RESPONSE:
            span = self._model.pop((ev.run_id, ev.step), None)
            if span is not None:
                self._end(span, ev, rename={
                    "output_tokens": "gen_ai.usage.output_tokens",
                    "cache_read_tokens": "harness.cache_read_tokens",
                    "cost_usd": "harness.cost_usd"})
            inp = ev.data.get("input_tokens")
            cache = ev.data.get("cache_read_tokens")
            if isinstance(inp, (int, float)) and inp and isinstance(cache, (int, float)):
                self._cache_hit_hist.record(cache / inp, {"harness.run_id": ev.run_id})
        elif ev.kind is EventKind.TOOL_STARTED:
            call_id = ev.data.get("call_id")
            parent = self._step.get((ev.run_id, ev.step)) or self._root.get(ev.run_id)
            span = self._start("harness.tool", ev, parent, rename={"tool": "harness.tool.name"})
            self._tool[(ev.run_id, call_id)] = span
            pending = self._pending_policy.pop((ev.run_id, call_id), None)
            if pending is not None:
                span.add_event(pending.kind.value, attributes=self._attrs(pending))
        elif ev.kind is EventKind.TOOL_FINISHED:
            call_id = ev.data.get("call_id")
            span = self._tool.pop((ev.run_id, call_id), None)
            if span is not None:
                self._end(span, ev, rename={"tool": "harness.tool.name",
                                            "duration_ms": "harness.tool.duration_ms"})
            duration = ev.data.get("duration_ms")
            if isinstance(duration, (int, float)):
                self._tool_duration_hist.record(
                    duration, {"harness.run_id": ev.run_id, "harness.tool.name": str(ev.data.get("tool", "?"))})
        elif ev.kind is EventKind.POLICY_DECIDED:
            call_id = ev.data.get("call_id")
            if ev.data.get("verdict") == "DENY" or call_id is None:
                # DENY (or no call_id at all, defensively): no tool span is ever
                # coming for this call — attach to whatever's open right now instead
                # of buffering forever.
                span = self._open_span_for(ev)
                if span is not None:
                    span.add_event(ev.kind.value, attributes=self._attrs(ev))
            else:
                # ALLOW (or an ASK the engine resolved to ALLOW): tool.started for this
                # exact call_id fires next, in the same run — buffer until then rather
                # than guess a parent early (docs/10 §2: this event belongs on the tool
                # span). A duplicate call that never actually runs (T-2.5/S-26) is
                # flushed at STEP_FINISHED instead, above.
                self._pending_policy[(ev.run_id, call_id)] = ev
            if ev.data.get("verdict") == "DENY":
                self._policy_denials.add(1, {"harness.tool": str(ev.data.get("tool", "?")),
                                             "harness.reason": str(ev.data.get("reason", "?"))})
        else:
            span = self._open_span_for(ev)
            if span is not None:
                span.add_event(ev.kind.value, attributes=self._attrs(ev))
                if ev.kind in (EventKind.BUDGET_EXHAUSTED, EventKind.ERROR_RAISED):
                    span.set_status(self._trace.Status(self._trace.StatusCode.ERROR))

    def close(self) -> None:
        # Defensive: end anything a run left open (crashed before its *_FINISHED
        # counterpart ever fired) rather than leak an unclosed span forever. Four
        # separate loops, not one over a tuple of stores — the stores' key types differ
        # (str vs. two different tuple shapes), which a single mixed loop cannot type.
        for tool_span in self._tool.values():
            tool_span.end()
        for model_span in self._model.values():
            model_span.end()
        for step_span in self._step.values():
            step_span.end()
        for root_span in self._root.values():
            root_span.end()
        self._tool.clear(); self._model.clear(); self._step.clear(); self._root.clear()
        self._pending_policy.clear()      # nothing left to attach them to


def _parse_float(v: Any) -> float | None:
    """`cost_usd` in event data is `str(Money(...))` — `"$0.0050"`, a `$`-prefixed
    string (`result.py::Money.__str__`), never a bare number. `float("$0.0050")` raises
    `ValueError`, which the bare try/except below would have silently swallowed into
    "no cost recorded" — found by this module's own test suite asserting a real metric
    point exists, not by inspection."""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.lstrip("$")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
