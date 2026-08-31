"""T-9.2 (Service API) — docs/17-research-alignment.md M9.

`harness[server]`: an ASGI **app object**, not a bundled server — the operator runs it
with whatever they already use (`uvicorn harness.server:create_app(...)`). Library-first,
same choice `docs/01-requirements.md §1.5` already made and `docs/17` W-08 restates:
"Service API is an extra, not core."

Routes (docs/17 §252 T-9.2):

    POST /v1/runs                              start a run -> 202 {id, status, replayed}
                                                body may carry "idempotency_key": str —
                                                a client retrying the same POST (its own
                                                timeout, a proxy retry) gets back the
                                                SAME run (200, replayed=true) instead of
                                                starting a second one. In-process only —
                                                see `RunStore._by_key` for the scope.
    GET  /v1/runs/{id}                          status, result (if done), pending approvals
    GET  /v1/runs/{id}/events                   SSE — canonical Event JSON (T-9.3)
    POST /v1/runs/{id}/cancel                   cancel the background run
    POST /v1/runs/{id}/approvals/{approval_id}  resolve one pending ASK
    POST /v1/runs/{id}/resume                   classic backend only — `Agent.resume()`

**Approval over HTTP — scoped to agents with no `approve=` of their own.** `Agent.with_()`
(ADR-004) injects a bridge callback that turns an in-flight `PolicyEngine.resolve()` await
into an `asyncio.Future` the `/approvals/{id}` route resolves. This works for BOTH backends
in principle (`resolve()` awaits whatever `approve` returns for either), but the LangGraph
backend's `_regate` calls `resolve()` through a NESTED `asyncio.run()` (`lg/runtime.py`) —
a `Future` created on the Service API's own event loop cannot be awaited from a different
one. **v1 therefore only wires the bridge for classic (`Agent`) agents registered without
their own `approve=`.** A `build_agent()`-based agent registered here runs to completion
using whatever `approve=`/`INTERRUPT` it was already built with; the HTTP approvals route
returns 409 for it. Said plainly, same discipline as N-1/N-3: a known backend asymmetry,
not silently painted over.
"""
from __future__ import annotations

import asyncio
import itertools
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

from .agent import Agent
from .observe.canonical import to_canonical_json
from .observe.events import Event
from .result import Result

try:
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import JSONResponse, StreamingResponse
    from starlette.routing import Route
except ImportError as exc:                                    # pragma: no cover
    raise ImportError(
        "harness.server needs the 'server' extra: pip install 'harness[server]'"
    ) from exc


def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return str(value)


def _result_to_json(result: Result) -> dict[str, Any]:
    return {
        "ok": result.ok,
        "text": result.text,
        "stop_reason": result.stop_reason.value,
        "steps": result.steps,
        "cost_usd": str(result.cost.decimal),
        "tainted": result.tainted,
        "tools_run": list(result.tools_run),
        "value": _jsonable(result.value),
        "detail": result.detail,
    }


class ApprovalBridge:
    """`approve=` <-> HTTP. Một run một bridge riêng — không chia sẻ giữa các run, cùng lý
    do `PolicyEngine` phải cache theo `run_id` thay vì dùng chung một instance (S-15/S-24).
    """

    def __init__(self) -> None:
        self._pending: dict[str, tuple["asyncio.Future[bool]", str, Mapping[str, Any]]] = {}
        self._n = itertools.count(1)

    async def __call__(self, call: Any, ctx: Any) -> bool:
        approval_id = f"appr_{next(self._n)}"
        fut: "asyncio.Future[bool]" = asyncio.get_running_loop().create_future()
        self._pending[approval_id] = (fut, call.name, dict(call.arguments))
        try:
            return await fut
        finally:
            self._pending.pop(approval_id, None)

    def list_pending(self) -> list[dict[str, Any]]:
        return [{"approval_id": aid, "tool": name, "arguments": _jsonable(args)}
                for aid, (_, name, args) in self._pending.items()]

    def resolve(self, approval_id: str, ok: bool) -> bool:
        entry = self._pending.get(approval_id)
        if entry is None or entry[0].done():
            return False
        entry[0].set_result(ok)
        return True


@dataclass
class _Run:
    id: str
    agent_name: str
    message: str
    status: str = "running"           # running | completed | failed | cancelled
    events: list[Event] = field(default_factory=list)
    result: Result | None = None
    error: str | None = None
    bridge: ApprovalBridge | None = None
    task: "asyncio.Task[Any] | None" = None


class RunStore:
    """In-process, không CSDL, không hàng đợi ngoài — cùng phạm vi `Session` (T-8.6) chọn:
    một tiến trình Service API = một `RunStore`. Scale ngang cần một store bên ngoài, ngoài
    phạm vi v1 (chưa có bằng chứng cần — `07-risks` "Chưa đủ evidence" discipline).
    """

    def __init__(self, agents: Mapping[str, Agent]) -> None:
        self._agents = dict(agents)
        self._runs: dict[str, _Run] = {}
        # S-4/S-23 re-verify, docs/12-decision-logs.md ADR-054's own note ("the only place
        # [an idempotency key] will ever come from is M9's Service API") — nay Service API
        # đã tồn tại, đây là chỗ đó. KHÔNG dùng `harness.idempotency.execute_once`: nó
        # nhắm một `Store` BỀN VỮNG (crash giữa hai lần gọi vẫn thấy lại đúng kết quả) —
        # `RunStore` cố ý không có `Store` nào phía sau (ADR-055, "không CSDL"), nên bọc nó
        # bằng `execute_once` sẽ tạo cảm giác về một bảo đảm bền vững không có thật. Đây là
        # một dict trong tiến trình, cùng đúng phạm vi RunStore đã tự đặt — client-side
        # retry trong đời một tiến trình được bảo vệ; qua một lần restart thì không, và đó
        # là giới hạn đã biết, không phải sơ sót.
        self._by_key: dict[str, str] = {}

    def agent_names(self) -> list[str]:
        return sorted(self._agents)

    def start(self, agent_name: str, message: str, *,
              idempotency_key: str | None = None) -> tuple[_Run, bool]:
        """Trả `(run, was_replayed)` — cùng hình dạng `execute_once`'s `(result,
        was_replayed)` để một client đọc quen với cái kia không phải học lại. `
        was_replayed=True` nghĩa là `idempotency_key` đã thấy trước đó VÀ run cũ đó vẫn
        còn trong `_runs` — không tạo run mới, trả lại đúng run cũ.
        """
        if idempotency_key is not None:
            existing_id = self._by_key.get(idempotency_key)
            if existing_id is not None:
                existing = self._runs.get(existing_id)
                if existing is not None:
                    return existing, True
                # Run cũ không còn (không có cơ chế dọn trong v1, nhưng phòng hờ) — coi
                # như key chưa từng thấy, rơi xuống nhánh tạo mới bên dưới.

        base = self._agents[agent_name]                        # KeyError -> 404 ở route
        bridge = None
        driven = base
        if getattr(base, "approve", None) is None:
            bridge = ApprovalBridge()
            driven = base.with_(approve=bridge)

        run = _Run(id="run_" + uuid.uuid4().hex[:20], agent_name=agent_name,
                  message=message, bridge=bridge)
        self._runs[run.id] = run
        if idempotency_key is not None:
            self._by_key[idempotency_key] = run.id

        class _Collector:
            def emit(self, event: Event) -> None:
                run.events.append(event)

            def close(self) -> None:
                pass

        collected = driven.with_(exporters=tuple(driven.exporters) + (_Collector(),))

        async def _drive() -> None:
            try:
                result = await collected.atry_run(message)
                run.result = result
                run.status = "completed" if result.ok else "failed"
                if not result.ok:
                    run.error = result.stop_reason.value
            except asyncio.CancelledError:
                run.status = "cancelled"
                raise
            except Exception as exc:                            # fail visible, not silent
                run.status = "failed"
                run.error = f"{type(exc).__name__}: {exc}"

        run.task = asyncio.ensure_future(_drive())
        return run, False

    def get(self, run_id: str) -> "_Run | None":
        return self._runs.get(run_id)

    def cancel(self, run_id: str) -> bool:
        run = self._runs.get(run_id)
        if run is None or run.task is None or run.task.done():
            return False
        run.task.cancel()
        return True


def create_app(agents: Mapping[str, Agent]) -> "Starlette":
    """`agents`: tên -> `Agent` đã dựng sẵn (module scope, §10.5) — Service API không
    nhận code Python qua HTTP, chỉ định tuyến tới agent operator đã tự viết và tự kiểm.
    """
    store = RunStore(agents)

    async def start_run(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "body phải là JSON"}, status_code=400)
        agent_name = body.get("agent")
        message = body.get("message")
        if not isinstance(agent_name, str) or not isinstance(message, str):
            return JSONResponse(
                {"error": "cần {'agent': str, 'message': str}"}, status_code=400)
        if agent_name not in agents:
            return JSONResponse(
                {"error": f"không có agent {agent_name!r}. Có: {store.agent_names()}"},
                status_code=404)
        idem_key = body.get("idempotency_key")
        if idem_key is not None and not isinstance(idem_key, str):
            return JSONResponse({"error": "idempotency_key phải là chuỗi"}, status_code=400)
        run, replayed = store.start(agent_name, message, idempotency_key=idem_key)
        # replayed=True: cùng idempotency_key đã thấy trước — trả lại run CŨ, 200 (không
        # phải "vừa tạo"). replayed=False: run mới, 202 Accepted (đang chạy nền).
        return JSONResponse({"id": run.id, "status": run.status, "replayed": replayed},
                            status_code=200 if replayed else 202)

    async def get_run(request: Request) -> JSONResponse:
        run = store.get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "không có run này"}, status_code=404)
        body: dict[str, Any] = {"id": run.id, "agent": run.agent_name, "status": run.status}
        if run.result is not None:
            body["result"] = _result_to_json(run.result)
        if run.error is not None:
            body["error"] = run.error
        if run.bridge is not None:
            body["pending_approvals"] = run.bridge.list_pending()
        return JSONResponse(body)

    async def stream_events(request: Request) -> StreamingResponse:
        run = store.get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "không có run này"}, status_code=404)  # type: ignore[return-value]

        async def gen():
            # T-9.3 — CÙNG hàm `to_canonical_json` mà `TranscriptWriter` (CLI/JSON) dùng.
            # Không transport nào có semantics riêng.
            i = 0
            while True:
                while i < len(run.events):
                    ev = run.events[i]
                    i += 1
                    import json as _json
                    yield f"data: {_json.dumps(to_canonical_json(ev), ensure_ascii=False)}\n\n"
                if run.status != "running" and i >= len(run.events):
                    yield "event: end\ndata: {}\n\n"
                    return
                await asyncio.sleep(0.05)

        return StreamingResponse(gen(), media_type="text/event-stream")

    async def cancel_run(request: Request) -> JSONResponse:
        ok = store.cancel(request.path_params["run_id"])
        if not ok:
            return JSONResponse({"error": "run không tồn tại hoặc đã xong"}, status_code=404)
        return JSONResponse({"status": "cancelling"})

    async def resolve_approval(request: Request) -> JSONResponse:
        run = store.get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "không có run này"}, status_code=404)
        if run.bridge is None:
            return JSONResponse(
                {"error": "agent này tự khai approve= riêng (hoặc dùng backend LangGraph) "
                          "— endpoint này không áp dụng, xem docstring harness.server"},
                status_code=409)
        try:
            body = await request.json()
        except Exception:
            body = {}
        ok = bool(body.get("approve", False))
        applied = run.bridge.resolve(request.path_params["approval_id"], ok)
        if not applied:
            return JSONResponse({"error": "approval_id không tồn tại hoặc đã xử lý"},
                                status_code=404)
        return JSONResponse({"resolved": True, "approve": ok})

    async def resume_run(request: Request) -> JSONResponse:
        run = store.get(request.path_params["run_id"])
        if run is None:
            return JSONResponse({"error": "không có run này"}, status_code=404)
        return JSONResponse(
            {"error": "resume một run đã dừng cần transcript path — gọi "
                      "Agent.resume(transcript) trực tiếp trong tiến trình bạn tự viết; "
                      "Service API v1 không giữ transcript path theo run_id"},
            status_code=501)

    return Starlette(routes=[
        Route("/v1/runs", start_run, methods=["POST"]),
        Route("/v1/runs/{run_id}", get_run, methods=["GET"]),
        Route("/v1/runs/{run_id}/events", stream_events, methods=["GET"]),
        Route("/v1/runs/{run_id}/cancel", cancel_run, methods=["POST"]),
        Route("/v1/runs/{run_id}/approvals/{approval_id}", resolve_approval, methods=["POST"]),
        Route("/v1/runs/{run_id}/resume", resume_run, methods=["POST"]),
    ])
