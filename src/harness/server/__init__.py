"""Service API — `POST /v1/runs`, `GET /v1/runs/{id}`, `GET /v1/runs/{id}/events` (SSE),
`POST /v1/runs/{id}/approvals/{call_id}`, `POST /v1/runs/{id}/cancel`.

T-9.2, docs/17-research-alignment.md M9. `harness[server]` extra — `starlette` only, an
operator brings their own ASGI server (uvicorn, hypercorn, ...); `import harness` never
imports this module.

**Real caller for `execute_once` (T-6.1, ADR-043).** That module shipped with no wiring
on purpose — "the scenario T-6.1's own Why names is a caller (an HTTP client hitting a
Service API) retrying a request the harness has no way to recognize as a retry... M9's
Service API is the first real source of one." `POST /v1/runs`'s `Idempotency-Key` header
is that source: a client that times out and retries the same key gets back the SAME
`run_id` instead of starting a second run.

**What this does NOT do, honestly (mirrors `EgressPolicy`'s "say the limit, don't paper
over it" discipline):**

- **No authentication.** Every route is open. An operator puts this behind their own
  auth (API gateway, reverse proxy, a Starlette middleware they add to the returned
  `app`) — the same posture `EgressPolicy` takes toward real network isolation: this
  module is not the security boundary.
- **In-memory run registry, one process.** A run's state (buffered events, pending
  approvals) does not survive a process restart. No `resume` endpoint ships in v1 for
  exactly that reason — `Session`/T-8.6 scoped itself the same way (ADR-053): naming a
  resume contract this module cannot actually keep would be worse than not shipping one.
  A future version needs a `Store`-backed run registry before `resume` means anything.
- **One `Agent`, shared.** `Agent` is frozen and already builds fresh `Ledger`/
  `TaintTracker`/`run_id` per call (`atry_run()`) — the existing "keep the Agent at
  module scope" guidance applies unchanged; this module does not construct a new `Agent`
  per request.
- **Classic backend only.** Approval bridging (`approve=` awaiting an `asyncio.Future`
  an HTTP request resolves) drives `Agent.atry_run()`/`stream()`, not `build_agent()`'s
  LangGraph `Runtime` — same backend `Session` (T-8.6) chose, for the same reason: that
  is where a durable, awaitable approval channel already exists cleanly.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from ..agent import Agent
from ..idempotency import execute_once
from ..memory.base import Store
from ..memory.inmemory import InMemoryStore
from ..observe.events import Event
from ..policy.decision import Actor, Approval
from ..result import Result, StopReason
from ..secrets import redact

_STATUS_RUNNING = "running"
_STATUS_WAITING = "waiting_approval"
_STATUS_DONE = "done"
_STATUS_ERROR = "error"
_STATUS_CANCELLED = "cancelled"


def _event_json(event: Event) -> str:
    """Same shape `observe/transcript.py::TranscriptWriter.emit()` writes, minus the
    tool-argument digesting (a live API response is not a persisted, PII-averse audit
    log — the caller that started this run already knows what arguments it sent).

    `redact()` still runs, and it MUST run on the run's own asyncio task — a value only
    revealed via `Secret.reveal()` is tracked in a `contextvars.ContextVar` that does not
    cross into whatever task is serving the `GET .../events` request. This function is
    called from `_SseExporter.emit()`, which `EventBus.emit()` invokes synchronously, in
    the run's own task — the same place `TranscriptWriter` calls `redact()`, and for the
    same reason (RT-13, Round 25).
    """
    line = json.dumps(
        {"seq": event.seq, "ts": round(event.ts, 6), "run_id": event.run_id,
         "kind": event.kind.value, "step": event.step, "data": dict(event.data),
         "schema_version": event.schema_version, "trace_id": event.trace_id,
         "tenant_id": event.tenant_id, "session_id": event.session_id},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return redact(line)


class _SseExporter:
    """A per-run `Exporter` (docs/02-architecture.md §2.4 seam) that hands each `Event`
    to a plain callback, synchronously, on the run's own task — see `_event_json`."""

    def __init__(self, on_event: Any) -> None:
        self._on_event = on_event

    def emit(self, event: Event) -> None:
        self._on_event(event)

    def close(self) -> None: ...


def _result_json(result: Result) -> dict[str, Any]:
    return {
        "ok": result.ok, "text": result.text, "stop_reason": result.stop_reason.value,
        "steps": result.steps, "cost_usd": str(result.cost), "run_id": result.run_id,
        "tainted": result.tainted, "tools_run": list(result.tools_run),
        "detail": result.detail,
    }


@dataclass
class _Run:
    id: str
    created_at: float
    status: str = _STATUS_RUNNING
    events: list[str] = field(default_factory=list)          # already-JSON, already-redacted
    subscribers: list["asyncio.Queue[str | None]"] = field(default_factory=list)
    result: Result | None = None
    error: str | None = None
    task: "asyncio.Task[None] | None" = None
    #: call_id -> Future[(ok, approved_by)] — see `_bridge_approvals`.
    pending: "dict[str, asyncio.Future[tuple[bool, str | None]]]" = field(default_factory=dict)
    pending_info: dict[str, dict[str, Any]] = field(default_factory=dict)

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id, "status": self.status,
            "result": _result_json(self.result) if self.result is not None else None,
            "error": self.error,
            "pending_approvals": [
                {"call_id": cid, **info} for cid, info in self.pending_info.items()
            ],
        }


def _bridge_approvals(run: _Run):
    """The `approve=` callback `_drive` binds via `agent.with_()`. Awaits a real
    `asyncio.Future` that `POST /v1/runs/{id}/approvals/{call_id}` resolves — the same
    "approval is a resolution step, not a policy" shape (ADR-021) `PolicyEngine.resolve`
    already awaits, just fed by an HTTP request instead of an in-process callback.
    """
    async def approve(call: Any, ctx: Any) -> Approval:
        fut: "asyncio.Future[tuple[bool, str | None]]" = asyncio.get_running_loop().create_future()
        run.pending[call.id] = fut
        run.pending_info[call.id] = {"tool": call.name, "arguments": dict(call.arguments)}
        was_running = run.status == _STATUS_RUNNING
        run.status = _STATUS_WAITING
        try:
            ok, approved_by = await fut
        finally:
            run.pending.pop(call.id, None)
            run.pending_info.pop(call.id, None)
            if was_running and not run.pending:
                run.status = _STATUS_RUNNING
        # S-11: `approved_by` is exactly as self-declared as any other `approve=`
        # callback's report — the HTTP caller types a name into a JSON body, nothing
        # authenticates it. `AuthEvidence` (07-risks) is the same open gap here as
        # everywhere else in this codebase, not a new one this module introduces.
        return Approval(ok, actor=Actor.human(approved_by or "anonymous", via="service-api"))
    return approve


class _RunRegistry:
    def __init__(self, agent: Agent, *, store: Store | None = None) -> None:
        self._agent = agent
        self._store = store if store is not None else InMemoryStore()
        self._runs: dict[str, _Run] = {}

    def get(self, run_id: str) -> _Run | None:
        return self._runs.get(run_id)

    async def start(self, message: str, *, idempotency_key: str | None) -> tuple[str, bool]:
        async def _fn() -> dict[str, str]:
            return {"id": self._spawn(message)}

        if idempotency_key is None:
            return (await _fn())["id"], False
        # T-6.1's `execute_once`: fail CLOSED (default) — starting a run is exactly the
        # "write/danger"-class operation that contract fails closed for. A store outage
        # here must refuse the request, not silently start an unrecorded duplicate.
        result, replayed = await execute_once(
            self._store, f"service-api:runs:{idempotency_key}", _fn)
        return result["id"], replayed

    def _spawn(self, message: str) -> str:
        run = _Run(id="run_" + uuid.uuid4().hex[:16], created_at=time.time())
        self._runs[run.id] = run

        def on_event(event: Event) -> None:
            line = _event_json(event)
            run.events.append(line)
            for q in run.subscribers:
                q.put_nowait(line)

        bound = self._agent.with_(
            exporters=tuple(self._agent.exporters) + (_SseExporter(on_event),),
            approve=_bridge_approvals(run))

        async def _drive() -> None:
            try:
                result = run.result = await bound.atry_run(message)
                if result.stop_reason is StopReason.CANCELLED:
                    run.status = _STATUS_CANCELLED
                elif result.ok:
                    run.status = _STATUS_DONE
                else:
                    run.status = _STATUS_ERROR
                    run.error = result.detail or result.text
            except asyncio.CancelledError:
                run.status = _STATUS_CANCELLED
                raise
            except Exception as exc:                     # pragma: no cover — defensive;
                run.status = _STATUS_ERROR                # atry_run() itself never raises
                run.error = f"{type(exc).__name__}: {exc}"  # for a run-level failure (ADR-044)
            finally:
                for q in run.subscribers:
                    q.put_nowait(None)                    # sentinel: no more events

        run.task = asyncio.ensure_future(_drive())
        return run.id

    async def cancel(self, run_id: str) -> bool:
        run = self._runs.get(run_id)
        if run is None or run.task is None or run.task.done():
            return False
        run.task.cancel()
        return True

    def resolve_approval(self, run_id: str, call_id: str, ok: bool,
                         approved_by: str | None) -> bool:
        run = self._runs.get(run_id)
        if run is None:
            return False
        fut = run.pending.get(call_id)
        if fut is None or fut.done():
            return False
        fut.set_result((ok, approved_by))
        return True

    async def subscribe(self, run_id: str) -> "tuple[list[str], asyncio.Queue[str | None]] | None":
        """Returns the buffered events so far, plus a queue that receives every event
        from this point on (including a final `None` sentinel once the run ends) — a
        late subscriber never misses history, and never double-receives it."""
        run = self._runs.get(run_id)
        if run is None:
            return None
        q: "asyncio.Queue[str | None]" = asyncio.Queue()
        run.subscribers.append(q)
        if run.task is not None and run.task.done():
            q.put_nowait(None)                            # run already finished
        return list(run.events), q


def create_app(agent: Agent, *, store: Store | None = None) -> Starlette:
    """`agent`: one bound `Agent`, shared across every run this app serves (see module
    docstring — this is the same "keep the Agent at module scope" pattern the rest of
    the library already documents, not a new constraint T-9.2 introduces).
    """
    registry = _RunRegistry(agent, store=store)

    async def start_run(request: Request) -> JSONResponse:
        body = await request.json()
        message = body.get("message")
        if not isinstance(message, str) or not message:
            return JSONResponse({"error": "'message' (non-empty string) is required"},
                                status_code=400)
        run_id, replayed = await registry.start(
            message, idempotency_key=request.headers.get("idempotency-key"))
        return JSONResponse({"id": run_id, "status": "running", "replayed": replayed},
                            status_code=200 if replayed else 202)

    async def get_run(request: Request) -> JSONResponse:
        run = registry.get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "no such run"}, status_code=404)
        return JSONResponse(run.snapshot())

    async def stream_events(request: Request) -> StreamingResponse | JSONResponse:
        subscribed = await registry.subscribe(request.path_params["run_id"])
        if subscribed is None:
            return JSONResponse({"error": "no such run"}, status_code=404)
        backlog, q = subscribed

        async def gen():
            for line in backlog:
                yield f"data: {line}\n\n"
            while True:
                item = await q.get()
                if item is None:
                    break
                yield f"data: {item}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    async def cancel_run(request: Request) -> JSONResponse:
        ok = await registry.cancel(request.path_params["run_id"])
        if not ok:
            run = registry.get(request.path_params["run_id"])
            status = 404 if run is None else 409
            return JSONResponse(
                {"error": "no such run" if run is None else "run already finished"},
                status_code=status)
        return JSONResponse({"status": "cancelling"})

    async def resolve_approval(request: Request) -> JSONResponse:
        body: Mapping[str, Any] = await request.json()
        ok = bool(body.get("approve", False))
        approved_by = body.get("approved_by")
        resolved = registry.resolve_approval(
            request.path_params["run_id"], request.path_params["call_id"], ok, approved_by)
        if not resolved:
            return JSONResponse({"error": "no such pending approval"}, status_code=404)
        return JSONResponse({"resolved": True, "approve": ok})

    return Starlette(routes=[
        Route("/v1/runs", start_run, methods=["POST"]),
        Route("/v1/runs/{run_id}", get_run, methods=["GET"]),
        Route("/v1/runs/{run_id}/events", stream_events, methods=["GET"]),
        Route("/v1/runs/{run_id}/cancel", cancel_run, methods=["POST"]),
        Route("/v1/runs/{run_id}/approvals/{call_id}", resolve_approval, methods=["POST"]),
    ])
