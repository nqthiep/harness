"""Event taxonomy and bus — docs/05-data-and-state.md §1, task T-3.1.

Sixteen kinds, closed.  A raising exporter is disabled for the rest of the run: a
telemetry bug must never take down an agent.

`BUDGET_UNLIMITED` (`budget.unlimited`) is the 16th, added for S-20 (ADR-041,
docs/12-decision-logs.md) — `docs/04-interfaces.md`/`docs/07-cost.md` had already
promised it ("Unlimited is possible; it is not silent") before any code emitted it.
"""
from __future__ import annotations

from .._value import value

import time
from dataclasses import field
from enum import Enum
from typing import Any, Mapping, Protocol


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


class Exporter(Protocol):
    def emit(self, event: Event) -> None: ...
    def close(self) -> None: ...


class EventBus:
    def __init__(self, run_id: str, exporters=()) -> None:
        self._run_id = run_id
        self._seq = 0
        self._exporters = list(exporters)
        self._broken: set[int] = set()
        self.events: list[Event] = []

    def emit(self, kind: EventKind, *, step: int | None = None, **data: Any) -> Event:
        ev = Event(self._seq, time.time(), self._run_id, kind, step, data)
        self._seq += 1
        self.events.append(ev)
        for i, ex in enumerate(self._exporters):
            if i in self._broken:
                continue
            try:
                ex.emit(ev)
            except Exception as exc:
                self._broken.add(i)          # disabled for the rest of the run
                self.events.append(Event(self._seq, time.time(), self._run_id,
                                         EventKind.ERROR_RAISED, step,
                                         {"where": "exporter", "type": type(exc).__name__,
                                          "message": str(exc), "retryable": False}))
                self._seq += 1
        return ev
