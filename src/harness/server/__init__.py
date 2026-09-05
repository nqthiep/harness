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

**`authenticate=` is required — no default (G-3, `design/review-architect.md`, fixed).**
`create_app` used to ship with every route open by default, honestly documented as such —
but a tier-1 mechanism (a docstring saying "put this behind your own auth") is not a
mechanism: demonstrated concretely, an unauthenticated `POST .../approvals/{call_id}` let
any network caller approve a `danger` tool, recorded under a self-declared `Actor` name,
degrading the entire `Decision`/`AuthEvidence` apparatus (`design/06-poka-yoke-matrix.md`
§A row 1, tier 3 at the model-vs-approver boundary) to "whoever can reach the port." Same
discipline as `Budget(usd=None)`/`Agent(allowed_hosts=...)`: unrestricted access is still
possible (`authenticate=lambda request: True`), it just can never be the silent default —
an operator has to type the word that means it.

**What this still does NOT do, honestly (mirrors `EgressPolicy`'s "say the limit, don't
paper over it" discipline):**

- **In-memory run registry, one process — including under multiple workers (G-10,
  fixed as documentation; the mechanism itself is out of scope for this pass — see
  `design/06-poka-yoke-matrix.md` §C).** A run's state (buffered events, pending
  approvals) does not survive a process restart, and **does not survive `--workers > 1`**
  either: `uvicorn --workers N` runs N separate processes, each with its own
  `_RunRegistry` and its own in-process idempotency lock (`idempotency.py::_locks`) — the
  same `Idempotency-Key` sent to two different workers starts two real runs, `GET
  /v1/runs/{id}` 404s on whichever worker didn't start it, and a pending approval can
  never be resolved from a different worker than the one awaiting it. No `resume`
  endpoint ships in v1 for the restart case — `Session`/T-8.6 scoped itself the same way
  (ADR-053): naming a resume contract this module cannot actually keep would be worse
  than not shipping one. A future version needs a `Store`-backed run registry (not just
  a `Store`-backed idempotency check) before either restart or multi-worker means
  anything; single-process, single-worker is the only deployment this module actually
  keeps its promises in today.
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
import inspect
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Mapping

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from ..agent import Agent
from ..idempotency import execute_once
from ..memory.base import Store
from ..memory.inmemory import InMemoryStore
from ..observe.events import Event, to_dict
from ..policy.decision import Actor, Approval, AuthEvidence
from ..result import Result, StopReason
from ..secrets import redact

#: G-3 — one caller per route, kept in one place so every route agrees on what
#: "authenticated" means and none can accidentally skip the check.
Authenticator = Callable[[Request], "bool | Awaitable[bool]"]


async def _is_authenticated(authenticate: Authenticator, request: Request) -> bool:
    result = authenticate(request)
    if inspect.isawaitable(result):
        result = await result
    return bool(result)

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
    row = to_dict(event)
    row["ts"] = round(row["ts"], 6)
    line = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str)
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


def _evidence_from_body(raw: Any) -> "AuthEvidence | None":
    """S-11, đã sửa — `POST .../approvals/{call_id}`'s optional `evidence` object.
    `None` means the caller supplied nothing (today's behavior, unchanged); a dict
    missing `channel`/`channel_message_id`/`principal` (`AuthEvidence`'s own required
    floor — review-security.md's "sửa tối thiểu") is a 400, not a silent `None` — a
    caller that tried and typo'd a field should not read back as one that never tried.
    """
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError("'evidence' must be a JSON object")
    missing = [k for k in ("channel", "channel_message_id", "principal") if not raw.get(k)]
    if missing:
        raise ValueError(f"'evidence' is missing {', '.join(missing)}")
    verified_at = raw.get("verified_at")
    signature = raw.get("signature")
    return AuthEvidence(
        channel=raw["channel"], channel_message_id=raw["channel_message_id"],
        principal=raw["principal"],
        signature=bytes.fromhex(signature) if signature else None,
        verified_at=datetime.fromisoformat(verified_at) if verified_at else None)


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
    #: call_id -> Future[(ok, approved_by, evidence)] — see `_bridge_approvals`.
    pending: "dict[str, asyncio.Future[tuple[bool, str | None, AuthEvidence | None]]]" = \
        field(default_factory=dict)
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
        fut: "asyncio.Future[tuple[bool, str | None, AuthEvidence | None]]" = \
            asyncio.get_running_loop().create_future()
        run.pending[call.id] = fut
        run.pending_info[call.id] = {"tool": call.name, "arguments": dict(call.arguments)}
        was_running = run.status == _STATUS_RUNNING
        run.status = _STATUS_WAITING
        try:
            ok, approved_by, evidence = await fut
        finally:
            run.pending.pop(call.id, None)
            run.pending_info.pop(call.id, None)
            if was_running and not run.pending:
                run.status = _STATUS_RUNNING
        # S-11, đã sửa: `approved_by`/`evidence` are exactly as self-declared as any
        # other `approve=` callback's report — the HTTP caller controls the JSON body,
        # nothing here authenticates it (module docstring's "No authentication" is
        # unchanged by this). `evidence` gives an operator sitting a REAL channel
        # integration in front of this endpoint (one that already verified a Slack
        # signature, an OAuth session, ...) somewhere to put that proof instead of a
        # bare name; `Agent(require_approval_evidence=True)` is the knob that then
        # REFUSES a human actor with none, rather than trusting it. Neither of those two
        # things happens unless the deployment opts into them.
        return Approval(ok, actor=Actor.human(approved_by or "anonymous", via="service-api"),
                        evidence=evidence)
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

    def resolve_approval(self, run_id: str, call_id: str, ok: bool, approved_by: str | None,
                         evidence: "AuthEvidence | None" = None) -> bool:
        run = self._runs.get(run_id)
        if run is None:
            return False
        fut = run.pending.get(call_id)
        if fut is None or fut.done():
            return False
        fut.set_result((ok, approved_by, evidence))
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


def create_app(agent: Agent, *, authenticate: Authenticator, store: Store | None = None,
               ) -> Starlette:
    """`agent`: one bound `Agent`, shared across every run this app serves (see module
    docstring — this is the same "keep the Agent at module scope" pattern the rest of
    the library already documents, not a new constraint T-9.2 introduces).

    `authenticate`: called with the raw `starlette.requests.Request` on every route,
    before anything else runs; a falsy/raising result is a 401. Required, no default —
    G-3, `design/review-architect.md`. `authenticate=lambda request: True` is the
    explicit, visible way to opt out for a deployment that already sits behind its own
    gateway auth; the point is that no deployment gets an open Service API by omission.
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
        # S-11, đã sửa — optional `evidence`: an operator sitting a real channel
        # integration in front of this endpoint (one that already verified a Slack
        # signature, an OAuth session, ...) has somewhere to put that proof. Malformed
        # over silently-ignored: a caller that TRIED to supply evidence and typo'd a
        # required field gets told so, not treated as if they supplied nothing.
        try:
            evidence = _evidence_from_body(body.get("evidence"))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        # G-11, đã sửa: đoán TRƯỚC chính xác điều kiện `PolicyEngine.resolve()` sẽ áp
        # dụng (policy/engine.py — `ok and require_evidence and actor.kind=="human" and
        # evidence is None`; actor ở endpoint này LUÔN là human, xem `_bridge_approvals`)
        # — một approval sẽ bị từ chối vì thiếu bằng chứng thì trả 400 NGAY, thay vì trả
        # 200 rồi lặng lẽ DENY ở một task khác mà cả người duyệt lẫn `Result` cuối cùng
        # đều không thấy được. Cố ý KHÔNG gọi `registry.resolve_approval` ở đây: future
        # đang chờ chưa bị resolve, nên run vẫn đứng ở `waiting_approval` — người gọi mất
        # một request, không mất luôn cơ hội gửi lại kèm evidence đúng.
        if ok and agent.require_approval_evidence and evidence is None:
            return JSONResponse(
                {"error": "this agent requires AuthEvidence for a human approval "
                         "(Agent(require_approval_evidence=True)) — 'evidence' was "
                         "not supplied. The tool will NOT run without it."},
                status_code=400)
        resolved = registry.resolve_approval(
            request.path_params["run_id"], request.path_params["call_id"], ok, approved_by,
            evidence)
        if not resolved:
            return JSONResponse({"error": "no such pending approval"}, status_code=404)
        return JSONResponse({"resolved": True, "approve": ok})

    def _guarded(handler):
        # G-3: one wrapper, applied to every route below, so no route can be added later
        # without the check — a check repeated by hand in each handler is exactly the
        # kind of thing one new route quietly forgets.
        async def wrapped(request: Request):
            if not await _is_authenticated(authenticate, request):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await handler(request)
        return wrapped

    return Starlette(routes=[
        Route("/v1/runs", _guarded(start_run), methods=["POST"]),
        Route("/v1/runs/{run_id}", _guarded(get_run), methods=["GET"]),
        Route("/v1/runs/{run_id}/events", _guarded(stream_events), methods=["GET"]),
        Route("/v1/runs/{run_id}/cancel", _guarded(cancel_run), methods=["POST"]),
        Route("/v1/runs/{run_id}/approvals/{call_id}", _guarded(resolve_approval),
             methods=["POST"]),
    ])
