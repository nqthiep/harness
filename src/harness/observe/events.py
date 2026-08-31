"""Event taxonomy and bus — docs/05-data-and-state.md §1, task T-3.1.

Sixteen kinds, closed.  A raising exporter is disabled for the rest of the run: a
telemetry bug must never take down an agent.

`BUDGET_UNLIMITED` (`budget.unlimited`) is the 16th, added for S-20 (ADR-041,
docs/12-decision-logs.md) — `docs/04-interfaces.md`/`docs/07-cost.md` had already
promised it ("Unlimited is possible; it is not silent") before any code emitted it.

**Envelope v1** (T-8.1, docs/17-research-alignment.md M8, ADR-048): `schema_version`,
`trace_id`, `tenant_id`, `session_id` — the four fields a multi-tenant deployment or a
distributed trace needs and this taxonomy did not carry. `seq` already existed. Every
field beyond `schema_version` defaults to `None`/`run_id` because nothing in this
codebase is multi-tenant or distributed yet (no `Session` object — T-8.6 — no OTel
exporter — T-8.3) — the point of adding them now, ahead of a caller, is exactly
`schema_version`'s own reason to exist: a version lets a later change widen these
fields without breaking an exporter that reads today's shape.
"""
from __future__ import annotations

from .._value import value

import time
from dataclasses import field
from enum import Enum
from typing import Any, Mapping, Protocol

#: Bumped only for a breaking change to the `Event` shape — additive fields with a
#: default do not need a bump (semver-for-one-artifact, not the package version).
EVENT_SCHEMA_VERSION = "1.0"


class EventKind(str, Enum):
    RUN_STARTED = "run.started";         RUN_FINISHED     = "run.finished"
    STEP_STARTED = "step.started";       STEP_FINISHED    = "step.finished"
    MODEL_REQUEST = "model.request";     MODEL_RESPONSE   = "model.response"
    BUDGET_RESERVED = "budget.reserved"; BUDGET_EXHAUSTED = "budget.exhausted"
    BUDGET_UNLIMITED = "budget.unlimited"
    TOOL_REQUESTED = "tool.requested";   POLICY_DECIDED   = "policy.decided"
    TOOL_STARTED = "tool.started";       TOOL_FINISHED    = "tool.finished"
    TAINT_RAISED = "taint.raised";       CONTEXT_MANAGED  = "context.managed"
    ERROR_RAISED = "error.raised"


@value
class Event:
    __hash__ = None                     # holds a Mapping
    seq: int
    ts: float
    run_id: str
    kind: EventKind
    step: int | None = None
    data: Mapping[str, Any] = field(default_factory=dict)
    # Envelope v1 (T-8.1) — appended, not inserted, so every existing positional
    # `Event(seq, ts, run_id, kind, step, data)` construction site keeps working.
    schema_version: str = EVENT_SCHEMA_VERSION
    trace_id: str | None = None
    tenant_id: str | None = None
    session_id: str | None = None


class Exporter(Protocol):
    def emit(self, event: Event) -> None: ...
    def close(self) -> None: ...


class EventBus:
    def __init__(self, run_id: str, exporters=(), *, trace_id: str | None = None,
                 tenant_id: str | None = None, session_id: str | None = None) -> None:
        self._run_id = run_id
        self._seq = 0
        self._exporters = list(exporters)
        self._broken: set[int] = set()
        self.events: list[Event] = []
        # T-8.1: `trace_id` defaults to `run_id` — one run is one trace until a real
        # OTel exporter (T-8.3, not yet built) or a distributed caller propagates a
        # trace context in from outside. `tenant_id`/`session_id` have no such natural
        # fallback within a single run — `None` unless the caller supplies one.
        self._trace_id = trace_id if trace_id is not None else run_id
        self._tenant_id = tenant_id
        self._session_id = session_id

    def _make(self, kind: EventKind, step: int | None, data: Mapping[str, Any]) -> Event:
        ev = Event(self._seq, time.time(), self._run_id, kind, step, data,
                   EVENT_SCHEMA_VERSION, self._trace_id, self._tenant_id, self._session_id)
        self._seq += 1
        return ev

    def emit(self, kind: EventKind, *, step: int | None = None, **data: Any) -> Event:
        ev = self._make(kind, step, data)
        self.events.append(ev)
        for i, ex in enumerate(self._exporters):
            if i in self._broken:
                continue
            try:
                ex.emit(ev)
            except Exception as exc:
                self._broken.add(i)          # disabled for the rest of the run
                self.events.append(self._make(
                    EventKind.ERROR_RAISED, step,
                    {"where": "exporter", "type": type(exc).__name__,
                     "message": str(exc), "retryable": False}))
        return ev
