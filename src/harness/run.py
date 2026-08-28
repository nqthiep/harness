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
from .dispatch import Dispatcher, RunContext
from .result import Money, Result, StopReason, Usage
from .secrets import redact
from .tools import EFFECT_PROFILES, Effect, ToolSpec



class RunEngine:
    def __init__(self, agent, provider, ledger, engine, taint, assembler, bus: EventBus,
                 watcher: PrefixWatcher) -> None:
        self._a, self._p, self._l = agent, provider, ledger
        self._engine, self._taint, self._asm, self._bus = engine, taint, assembler, bus
        self._watch = watcher
        self._sem = asyncio.Semaphore(max(1, getattr(agent, "max_parallel_tools", 8)))
        self._dispatch = Dispatcher(self)

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
        pauses = 0
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

                if resp.stop_reason in CONTINUE:
                    pauses += 1
                    self._bus.emit(EventKind.STEP_FINISHED, step=step,
                                   stop_reason=resp.stop_reason, tool_calls=[])
                    if pauses > MAX_PAUSES:
                        stop = StopReason.ERROR
                        detail = (f"the model paused {pauses} times in a row without "
                                  f"finishing; stopping rather than paying for a loop")
                        break
                    step += 1
                    continue

                mapped = _MAP.get(resp.stop_reason)
                if resp.stop_reason == "tool_use":
                    results = await self._dispatch._run_tools(resp, step, run_id)
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
        value = self._parse_returns(text) if (stop is StopReason.COMPLETED
                                              and self._a.returns is not None) else None
        return Result(text, stop, step, self._l.spent, usage_total, run_id,
                      self._taint.tainted, tuple(msgs), value, detail,
                      tuple(self._dispatch.ran))

    def _parse_returns(self, text: str):
        """Turn the final answer into `Agent(returns=...)`, validated — ADR-022.

        Round 33 found `returns=` reached the request and the response was never parsed,
        so `Result.value` was always None: the parameter was accepted and half-honoured.
        A response that does not fit is an error, never a silent None.
        """
        import dataclasses
        want = self._a.returns
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ToolContractError(
                f"this agent was asked for {want.__name__}, but the model replied with "
                f"text that is not {want.__name__}:\n\n    {text[:120]!r}\n\n"
                f"  ({exc})"
            ) from None
        if not dataclasses.is_dataclass(want):
            return data
        fields = {f.name for f in dataclasses.fields(want)}
        missing = sorted(f.name for f in dataclasses.fields(want)
                         if f.name not in data
                         and f.default is dataclasses.MISSING
                         and f.default_factory is dataclasses.MISSING)
        if missing:
            raise ToolContractError(
                f"the model's answer is missing {', '.join(missing)} for "
                f"{want.__name__}.\n\n  Got: {sorted(data)}"
            )
        return want(**{k: v for k, v in data.items() if k in fields})

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


def canonical_len(req) -> str:
    """The serialized request.  Its character count is a hard upper bound on the true
    input token count — no tokenizer emits more tokens than characters (ADR-026)."""
    return _canonical({"system": list(req.system), "tools": list(req.tools),
                       "messages": list(req.messages)})


#: Every provider stop reason maps to exactly one StopReason.  Unknown -> ERROR, never
#: to a success (ADR-019).  "tool_use" is absent because it continues the loop, and so is
#: "pause_turn" — see CONTINUE below.
_MAP = {"end_turn": StopReason.COMPLETED, "max_tokens": StopReason.TRUNCATED,
        "refusal": StopReason.MODEL_REFUSAL}

#: Stop reasons that mean "not finished, send it back".  `pause_turn` is what a server
#: tool (web search, web fetch) returns when the model pauses mid-turn; T-0.4 said
#: "surface pause_turn rather than swallowing it" and the code had never heard of it, so
#: it fell through to ERROR — the API says *resumable* and the harness said *dead* (Round 38).
CONTINUE = frozenset({"pause_turn"})

#: A model that pauses forever is a loop the budget would pay for.  Bounded, and the
#: bound is loud rather than silent.
MAX_PAUSES = 5
