"""RunEngine — the loop.  docs/02-architecture.md §3, task T-0.5.

The only place where a budget check can precede a model call and a permission check can
precede a tool call (ADR-001).  Deliberately boring; IDL-13 caps this file at 250 lines
of code and treats an overrun as a design signal.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .errors import BudgetExceeded, ToolContractError
from .context.assembler import canonical as _canonical
from .context.linter import PrefixWatcher
from .context.window import manage as manage_context
from .models.pricing import MAX_CONTEXT
from .observe.events import EventBus, EventKind
from .policy.base import Decision, ToolCall, Verdict
from .result import Money, Result, StopReason, Usage
from .secrets import redact
from .tools import EFFECT_PROFILES, Effect, ToolSpec


@dataclass(slots=True)
class RunContext:
    run_id: str
    agent_name: str
    step: int
    tainted: bool
    safety: str
    deadline: float
    # Deliberately no message history: a tool that could read the transcript could
    # exfiltrate the whole conversation (IDL-15).


def _truncate(text: str, max_tokens: int) -> tuple[str, bool]:
    limit = max_tokens * 4                     # ~4 chars/token, conservative
    if len(text) <= limit:
        return text, False
    cut = text[:limit]
    while cut and not cut.encode("utf-8", "ignore").decode("utf-8", "ignore") == cut:
        cut = cut[:-1]                         # never split a UTF-8 character (P-7)
    return cut + f"\n[truncated: ~{max_tokens} of ~{len(text)//4} tokens shown]", True


class RunEngine:
    def __init__(self, agent, provider, ledger, engine, taint, assembler, bus: EventBus,
                 watcher: PrefixWatcher) -> None:
        self._a, self._p, self._l = agent, provider, ledger
        self._engine, self._taint, self._asm, self._bus = engine, taint, assembler, bus
        self._watch = watcher
        self._sem = asyncio.Semaphore(max(1, getattr(agent, "max_parallel_tools", 8)))

    async def run(self, message: str, *, messages: Sequence[Mapping[str, Any]] = (),
                  on_delta=None) -> Result:
        run_id = self._bus._run_id
        msgs: list[Mapping[str, Any]] = list(messages) + [{"role": "user", "content": message}]
        usage_total = Usage()
        text = ""
        self._bus.emit(EventKind.RUN_STARTED, agent=self._a.name, model=self._a.model,
                       tool_names=[t.name for t in self._a.toolset], safety=self._a.safety,
                       message=message)

        stop, detail = StopReason.COMPLETED, ""
        step = 0
        try:
            while True:
                if self._l.remaining_steps() <= 0:
                    stop, detail = StopReason.STEP_LIMIT, f"reached {self._l.budget.steps} steps"
                    break
                if self._l.remaining_wall_clock() <= 0:
                    stop, detail = StopReason.TIMEOUT, "ran out of time"
                    break

                self._bus.emit(EventKind.STEP_STARTED, step=step)
                price = self._p.price(self._a.model)
                self._watch.observe(self._asm.render_prefix())
                probe = self._asm.build(msgs, max_tokens=1)
                input_tokens = await self._p.count_input_tokens(probe)

                try:
                    max_tokens = self._l.size_call(input_tokens, price,
                                                   self._p.max_output(self._a.model))
                    req = self._asm.build(msgs, max_tokens=max_tokens,
                                          stream=on_delta is not None)
                    hard_in = len(canonical_len(req))   # chars >= tokens, always
                    reservation = self._l.reserve(input_tokens, max_tokens, price,
                                                  hard_max_input=hard_in)
                except BudgetExceeded as exc:
                    self._bus.emit(EventKind.BUDGET_EXHAUSTED, step=step, axis="usd",
                                   spent=str(self._l.spent))
                    stop, detail = StopReason.BUDGET_EXHAUSTED, str(exc)
                    break

                self._bus.emit(EventKind.BUDGET_RESERVED, step=step,
                               estimate_usd=str(reservation.estimate),
                               spent_usd=str(self._l.spent),
                               exact=self._l.last_call_was_exactly_bounded)
                self._bus.emit(EventKind.MODEL_REQUEST, step=step, model=self._a.model,
                               input_tokens=input_tokens, max_tokens=max_tokens,
                               n_tools=len(self._a.toolset))

                resp = await self._p.complete(req, on_delta=on_delta)
                self._l.settle(reservation, resp.usage, price)
                self._l.count_step()
                usage_total = usage_total + resp.usage
                self._bus.emit(EventKind.MODEL_RESPONSE, step=step, stop_reason=resp.stop_reason,
                               cost_usd=str(self._l.spent))

                text = "".join(b.get("text", "") for b in resp.content if b.get("type") == "text") or text
                msgs.append({"role": "assistant", "content": list(resp.content)})

                mapped = _MAP.get(resp.stop_reason)
                if resp.stop_reason == "tool_use":
                    results = await self._run_tools(resp, step, run_id)
                    msgs.append({"role": "user", "content": results})   # I-4: one message
                    msgs = self._manage_context(msgs, input_tokens, step)
                    self._bus.emit(EventKind.STEP_FINISHED, step=step,
                                   stop_reason=resp.stop_reason,
                                   tool_calls=[b["name"] for b in resp.content
                                               if b.get("type") == "tool_use"])
                    step += 1
                    continue
                self._bus.emit(EventKind.STEP_FINISHED, step=step,
                               stop_reason=resp.stop_reason, tool_calls=[])
                if mapped is None:
                    self._bus.emit(EventKind.ERROR_RAISED, step=step, where="provider",
                                   type="unknown_stop_reason", message=resp.stop_reason,
                                   retryable=False)
                    stop, detail = StopReason.ERROR, f"unknown stop reason {resp.stop_reason!r}"
                    break
                stop = mapped
                if stop is StopReason.TRUNCATED:
                    detail = (f"the answer got cut off because it reached its budget of "
                              f"{Money(self._a.budget.usd) if self._a.budget.usd else 'unlimited'}")
                elif stop is StopReason.MODEL_REFUSAL:
                    detail = "the model declined this request"
                break
        except asyncio.CancelledError:
            stop, detail = StopReason.CANCELLED, "cancelled"

        self._bus.emit(EventKind.RUN_FINISHED, stop_reason=stop.value, steps=step,
                       cost_usd=str(self._l.spent), tainted=self._taint.tainted)
        return Result(text, stop, step, self._l.spent, usage_total, run_id,
                      self._taint.tainted, tuple(msgs), None, detail)

    def _manage_context(self, msgs: list, _unused: int, step: int) -> list:
        """T-2.6, wired.  Round 27 found window.manage() was built, tested, and never
        called from the loop — so `context.managed` was one of three event kinds the
        code could not emit.

        The size is measured from the messages being managed, not from the token count of
        the request already sent: that count predates the tool results just appended,
        which are exactly what makes the window grow.
        """
        window = MAX_CONTEXT.get(self._a.model, 200_000)
        used = sum(len(_canonical(m)) for m in msgs) // 4
        out, action = manage_context(msgs, used_tokens=used, context_window=window)
        if action == "none":
            return msgs
        self._bus.emit(EventKind.CONTEXT_MANAGED, step=step, strategy=action,
                       tokens_before=used, messages=len(msgs))
        return out

    # -- tools ------------------------------------------------------------
    async def _run_tools(self, resp, step: int, run_id: str) -> list[dict[str, Any]]:
        calls = [b for b in resp.content if b.get("type") == "tool_use"]
        ctx = RunContext(run_id, self._a.name, step, self._taint.tainted,
                         self._a.safety, self._l.remaining_wall_clock())
        planned: list[tuple[dict, ToolSpec | None, Decision | None]] = []

        for b in calls:
            spec = self._a.toolset.get(b["name"])
            self._bus.emit(EventKind.TOOL_REQUESTED, step=step, tool=b["name"],
                           call_id=b["id"], arguments=b.get("input", {}))
            if spec is None:
                planned.append((b, None, None)); continue
            call = ToolCall(b["id"], b["name"], b.get("input", {}), spec)
            d = self._engine.decide(call, ctx)
            d = await self._engine.resolve(d, call, ctx, self._a.approve)
            self._bus.emit(EventKind.POLICY_DECIDED, step=step, tool=b["name"],
                           call_id=b["id"], verdict=d.verdict.name, reason=d.reason,
                           policy=d.policy)
            planned.append((b, spec, d))

        # I-3: every tool_use gets exactly one tool_result, in the model's call order.
        out: list[dict[str, Any]] = [None] * len(planned)      # type: ignore[list-item]
        parallel, serial = [], []
        seen: dict[str, int] = {}          # (name, canonical args) -> index that runs it
        dupes: list[tuple[int, int]] = []  # (duplicate index, original index)
        for i, (b, spec, d) in enumerate(planned):
            if spec is None:
                out[i] = _err(b["id"], f"no tool called {b['name']!r} is available"); continue
            if d is None or d.verdict is Verdict.DENY:
                out[i] = _err(b["id"], f"denied by policy: {d.reason if d else 'unknown'}"); continue
            key = f"{b['name']}:{_canonical(b.get('input', {}))}"
            if key in seen:
                # T-2.5: run it once, but still return a result per tool_use (I-3).
                dupes.append((i, seen[key]))
                self._bus.emit(EventKind.TOOL_REQUESTED, step=step, tool=b["name"],
                               call_id=b["id"], duplicate_of=planned[seen[key]][0]["id"])
                continue
            seen[key] = i
            (parallel if EFFECT_PROFILES[spec.effect].parallel_safe else serial).append((i, b, spec))

        if parallel:
            done = await asyncio.gather(
                *(self._bounded(b, spec, step) for _, b, spec in parallel),
                return_exceptions=False)
            for (i, _, _), r in zip(parallel, done):
                out[i] = r
        for i, b, spec in serial:
            out[i] = await self._invoke(b, spec, step)
        for i, origin in dupes:
            out[i] = {**out[origin], "tool_use_id": planned[i][0]["id"]}
        return out

    async def _bounded(self, b: Mapping[str, Any], spec: ToolSpec, step: int) -> dict[str, Any]:
        """NFR-09: parallelism is bounded, so a fan-out cannot fork-bomb a downstream
        service.  The semaphore was specified and the parameter stored, but nothing read
        it until Round 26 measured peak concurrency at 30 against a limit of 4."""
        async with self._sem:
            return await self._invoke(b, spec, step)

    async def _invoke(self, b: Mapping[str, Any], spec: ToolSpec, step: int) -> dict[str, Any]:
        self._bus.emit(EventKind.TOOL_STARTED, step=step, tool=spec.name, call_id=b["id"])
        t0 = time.monotonic()
        timeout = self._l.tool_timeout(spec.timeout_s)          # Round 23: clamped
        try:
            async with asyncio.timeout(timeout):
                value = await spec.fn(**b.get("input", {}))
            try:
                payload = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
            except (TypeError, ValueError) as exc:
                raise ToolContractError(
                    f"tool {spec.name!r} returned something that cannot be sent to a model: {exc}"
                ) from None
            payload, truncated = _truncate(payload, spec.max_result_tokens)
            if EFFECT_PROFILES[spec.effect].taints_output and self._taint.raise_taint(spec.name):
                self._bus.emit(EventKind.TAINT_RAISED, step=step, source_tool=spec.name)
            self._bus.emit(EventKind.TOOL_FINISHED, step=step, tool=spec.name, call_id=b["id"],
                           duration_ms=(time.monotonic() - t0) * 1000, is_error=False,
                           truncated=truncated)
            return {"type": "tool_result", "tool_use_id": b["id"], "content": redact(payload)}
        except asyncio.CancelledError:
            raise                                                # never a tool error
        except TimeoutError:
            reason = ("timed out: run wall-clock budget reached"
                      if timeout < spec.timeout_s else f"timed out after {spec.timeout_s}s")
            return self._tool_error(b, spec, step, reason, t0)
        except Exception as exc:
            return self._tool_error(b, spec, step, f"{type(exc).__name__}: {exc}", t0)

    def _tool_error(self, b, spec, step, msg, t0) -> dict[str, Any]:
        self._bus.emit(EventKind.ERROR_RAISED, step=step, where="tool", type=spec.name,
                       message=msg, retryable=EFFECT_PROFILES[spec.effect].retryable)
        self._bus.emit(EventKind.TOOL_FINISHED, step=step, tool=spec.name, call_id=b["id"],
                       duration_ms=(time.monotonic() - t0) * 1000, is_error=True, truncated=False)
        return _err(b["id"], msg)


def canonical_len(req) -> str:
    """The serialized request.  Its character count is a hard upper bound on the true
    input token count — no tokenizer emits more tokens than characters (ADR-026)."""
    return _canonical({"system": list(req.system), "tools": list(req.tools),
                       "messages": list(req.messages)})


def _err(call_id: str, message: str) -> dict[str, Any]:
    # Redacted here rather than only at the transcript: a tool error goes to the MODEL,
    # which is a wider audience than a log file (Round 25, RT-13).
    return {"type": "tool_result", "tool_use_id": call_id,
            "content": redact(message), "is_error": True}


#: Every provider stop reason maps to exactly one StopReason.  Unknown -> ERROR, never
#: to a success (ADR-019).  "tool_use" is absent because it continues the loop.
_MAP = {"end_turn": StopReason.COMPLETED, "max_tokens": StopReason.TRUNCATED,
        "refusal": StopReason.MODEL_REFUSAL}
