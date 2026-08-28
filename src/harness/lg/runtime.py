"""Node implementations — Round 35.

Every invariant the hand-written loop enforced is preserved, in the same order, but as
graph nodes: the 87% of the package that is enforcement ports unchanged (Ledger,
PolicyEngine, TaintTracker, Secret, effect classes), and only the 13% that was the loop
is replaced.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import interrupt

from ..budget.ledger import Ledger
from ..errors import BudgetExceeded
from ..observe.events import EventKind
from ..policy.base import Decision, ToolCall, Verdict
from ..policy.taint import TaintTracker
from ..result import Money, Usage
from ..secrets import redact, redaction_scope
from ..context.window import CLEARED, EDIT_AT, KEEP_RECENT_STEPS
from ..models.pricing import MAX_CONTEXT
from ..tools import EFFECT_PROFILES
from .graph import BUDGET, INTERRUPT, MODEL, POLICY, TOOLS


class Runtime:
    def __init__(self, *, model, toolset, ledger: Ledger, engine, taint, price,
                 max_output: int, model_name: str = "claude-opus-5",
                 bus=None, approve=None) -> None:
        self._model, self._tools = model, toolset
        self._model_name, self._started = model_name, False
        self._budget = ledger.budget          # the spec; the spend lives per turn
        self._engine = engine
        self._price, self._max_output = price, max_output
        self._bus, self._approve = bus, approve

    # ── everything mutable is derived from graph state ───────────────────────
    #
    # Nothing about a run may live on this object.  LangGraph runs every node in its own
    # copied context, so an attribute set in one node is not there in the next; and a
    # Runtime is built once per compiled graph, so anything it does hold is shared by
    # every conversation that graph serves.  Round 37 found all three consequences at
    # once: a shared ledger billed customer B for customer A's tokens, a shared taint
    # tracker leaked A's taint to B and lost it across a restart, and a checkpointed
    # `stop_reason` made every turn after the first do nothing at all.
    #
    # The rule this replaced them with: **the thread's state is the only memory.**
    def _ledger(self, state) -> Ledger:
        """This conversation's ledger, rebuilt from state on every node.

        Parity with `Chat` (docs/03): USD accumulates across the conversation, the step
        ceiling is per turn — accumulating steps too would kill a long conversation
        permanently, every later turn starting already over the limit.
        """
        snap = dict(state.get("ledger") or {})
        if _is_new_turn(state):
            snap["steps"] = 0
        return Ledger(self._budget).restore(snap)

    def _tainter(self, state) -> TaintTracker:
        t = TaintTracker()
        if state.get("tainted"):
            t.raise_taint("restored from checkpoint")
        return t

    # ── gate 1: nothing reaches the model without a reservation ──────────────
    def budget_gate(self, state) -> dict:
        led = self._ledger(state)
        if state.get("step", 0) == 0 and not self._started:
            self._started = True
            self._emit(EventKind.RUN_STARTED, model=self._model_name,
                       tool_names=[t.name for t in self._tools],
                       safety=self._safety(state))
        self._emit(EventKind.STEP_STARTED, step=state.get("step", 0))
        if led.remaining_steps() <= 0:
            return {"stop_reason": "step_limit", "detail": "reached the step limit",
                    "ledger": led.snapshot()}
        if led.remaining_wall_clock() <= 0:
            return {"stop_reason": "timeout", "detail": "ran out of time",
                    "ledger": led.snapshot()}
        text = json.dumps([m.content for m in state["messages"]],
                          ensure_ascii=False, default=str)
        input_tokens = max(1, len(text) // 4)
        try:
            max_tokens = led.size_call(input_tokens, self._price, self._max_output)
            res = led.reserve(input_tokens, max_tokens, self._price,
                              hard_max_input=len(text))
        except BudgetExceeded as exc:
            self._emit(EventKind.BUDGET_EXHAUSTED, axis="usd", spent=str(led.spent))
            return {"stop_reason": "budget_exhausted", "detail": str(exc),
                    "ledger": led.snapshot()}
        self._emit(EventKind.BUDGET_RESERVED, estimate_usd=str(res.estimate),
                   spent_usd=str(led.spent))
        # `stop_reason` is cleared here, and only here.  It is checkpointed like every
        # other state key, so a thread that finished a turn came back carrying
        # "completed" — and `_after_budget` routed the next turn straight to `finish`.
        # Multi-turn was silently dead: the model was called once per thread, ever, and
        # the caller got their own message echoed back (Round 37).
        return {"spent_usd": str(led.spent.decimal), "ledger": led.snapshot(),
                "max_tokens": max_tokens, "stop_reason": None, "detail": ""}

    def call_model(self, state) -> dict:
        led = self._ledger(state)
        self._emit(EventKind.MODEL_REQUEST, max_tokens=state.get("max_tokens", 0))
        msg = self._model.invoke(state["messages"])
        led.settle(_RESERVED(state.get("max_tokens", 0)), _usage_of(msg), self._price)
        led.count_step()
        self._emit(EventKind.MODEL_RESPONSE, cost_usd=str(led.spent))
        return {"messages": [msg], "step": state.get("step", 0) + 1,
                "spent_usd": str(led.spent.decimal), "ledger": led.snapshot()}

    # ── gate 2: nothing reaches a tool without a verdict ─────────────────────
    def policy_gate(self, state) -> dict:
        calls = getattr(state["messages"][-1], "tool_calls", []) or []
        ctx = _Ctx(tainted=self._tainter(state).tainted, safety=self._safety(state))
        pending, denied = [], []
        for c in calls:
            self._emit(EventKind.TOOL_REQUESTED, tool=c["name"], call_id=c["id"])
            spec = self._tools.get(c["name"])
            if spec is None:
                denied.append(ToolMessage(
                    content=f"no tool called {c['name']!r} is available",
                    tool_call_id=c["id"], status="error"))
                continue
            d = self._engine.decide(ToolCall(c["id"], c["name"], c.get("args", {}), spec), ctx)
            self._emit(EventKind.POLICY_DECIDED, tool=c["name"], call_id=c["id"],
                       verdict=d.verdict.name, reason=d.reason, policy=d.policy)
            if d.verdict is Verdict.DENY:
                denied.append(ToolMessage(content=f"denied by policy: {d.reason}",
                                          tool_call_id=c["id"], status="error"))
            else:
                # The name, never the ToolSpec: everything in graph state is
                # checkpointed, and a ToolSpec holds a callable that no serializer can
                # write.  The spec is runtime configuration, looked up on use (Round 35).
                pending.append({"call": c, "tool": c["name"],
                                "verdict": int(d.verdict), "reason": d.reason})
        return {"_pending": pending, "messages": denied}

    def approval_gate(self, state) -> dict:
        """A surviving ASK becomes ALLOW or DENY — resolved by the same engine the
        hand-written loop uses (ADR-021), never by a second copy of the rule.

        Round 35's parity suite caught two forks here: this node called
        ``approve(question)`` while the documented contract is
        ``approve(ToolCall, RunContext)``, and with no callback it blocked on
        ``interrupt()`` where the loop applies the safety rule.  `approve=INTERRUPT`
        is the one thing only this backend offers: a durable wait that survives a
        process restart.  It is an extra mode, not a different rule.
        """
        out = []
        for p in state.get("_pending", []):
            if Verdict(p["verdict"]) is not Verdict.ASK:
                out.append(p); continue
            spec = self._tools.get(p["tool"])
            call = ToolCall(p["call"]["id"], p["tool"], p["call"].get("args", {}), spec)
            ctx = _Ctx(tainted=self._tainter(state).tainted, safety=self._safety(state))
            if self._approve is INTERRUPT:
                ok = bool(interrupt({"tool": p["tool"],
                                     "arguments": p["call"].get("args", {}),
                                     "reason": p["reason"]}))
                d = Decision(Verdict.ALLOW if ok else Verdict.DENY,
                             "approved" if ok else "declined by approver", "approval")
            else:
                d = asyncio.run(self._engine.resolve(
                    Decision(Verdict.ASK, p["reason"], "policy"), call, ctx, self._approve))
            self._emit(EventKind.POLICY_DECIDED, tool=p["tool"], call_id=p["call"]["id"],
                       verdict=d.verdict.name, reason=d.reason, policy=d.policy)
            out.append({**p, "verdict": int(d.verdict), "reason": d.reason})
        denied = [ToolMessage(content=f"declined: {p['call']['name']}",
                              tool_call_id=p["call"]["id"], status="error")
                  for p in out if Verdict(p["verdict"]) is Verdict.DENY]
        return {"_pending": [p for p in out if Verdict(p["verdict"]) is Verdict.ALLOW],
                "messages": denied}

    def run_tools(self, state) -> dict:
        """The one place tool output becomes bytes — so the one place redaction must hold.

        Round 35: the port dropped `redaction_scope()` and RT-13 came back.  A tool that
        builds a short-lived Secret, reveals it, and returns a string derived from it
        sent that string to the model in cleartext: the weak registry had already lost
        the Secret by the time `redact()` ran.  The scope is opened here rather than
        around the whole graph because a caller invokes the compiled graph directly —
        a guarantee that depends on the caller remembering something is not a guarantee.
        """
        with redaction_scope():
            return self._run_tools(state)

    def _run_tools(self, state) -> dict:
        tainter = self._tainter(state)
        msgs, tainted = [], False
        for p in state.get("_pending", []):
            call = p["call"]
            spec = self._tools.get(p["tool"])
            if spec is None:                    # tool set changed under a resumed run
                msgs.append(ToolMessage(content=f"tool {p['tool']!r} is no longer available",
                                        tool_call_id=call["id"], status="error"))
                continue
            self._emit(EventKind.TOOL_STARTED, tool=spec.name, call_id=call["id"])
            try:
                value = asyncio.run(_invoke(spec, call.get("args", {})))
                payload = value if isinstance(value, str) else json.dumps(
                    value, sort_keys=True, ensure_ascii=False, default=str)
                limit = spec.max_result_tokens * 4
                if len(payload) > limit:
                    payload = payload[:limit] + "\n[truncated]"
                if EFFECT_PROFILES[spec.effect].taints_output and tainter.raise_taint(spec.name):
                    tainted = True
                    self._emit(EventKind.TAINT_RAISED, source_tool=spec.name)
                msgs.append(ToolMessage(content=redact(payload), tool_call_id=call["id"]))
                self._emit(EventKind.TOOL_FINISHED, tool=spec.name, call_id=call["id"],
                           is_error=False)
            except Exception as exc:
                self._emit(EventKind.ERROR_RAISED, where="tool", type=spec.name,
                           message=str(exc), retryable=EFFECT_PROFILES[spec.effect].retryable)
                msgs.append(ToolMessage(content=redact(f"{type(exc).__name__}: {exc}"),
                                        tool_call_id=call["id"], status="error"))
        self._emit(EventKind.STEP_FINISHED, step=state.get("step", 0),
                   stop_reason="tool_use", tool_calls=[p["tool"] for p in state.get("_pending", [])])
        return {"messages": msgs + self._manage(state["messages"] + msgs, state),
                "_pending": [], "tainted": state.get("tainted", False) or tainted}

    def finish(self, state) -> dict:
        """The single exit.  Every path out of the graph passes here, so `run.finished`
        cannot be forgotten by a branch (Round 35; the same rule as the two gates)."""
        stop = state.get("stop_reason") or "completed"
        self._emit(EventKind.STEP_FINISHED, step=state.get("step", 0),
                   stop_reason=stop, tool_calls=[])
        led = self._ledger(state)
        self._emit(EventKind.RUN_FINISHED, stop_reason=stop, steps=state.get("step", 0),
                   cost_usd=str(led.spent), tainted=bool(state.get("tainted")))
        return {"stop_reason": stop, "spent_usd": str(led.spent.decimal)}

    def _manage(self, messages, state) -> list:
        """Context growth, ported from T-2.6 (docs/07-cost.md §3).

        Without this a long run walks into the model's context window and the provider
        rejects the request — the cost invariant, not a nicety.  `add_messages` replaces
        a message whose id it already holds, so clearing an old tool result is expressed
        as re-emitting that same message with emptied content: no rewrite of the list,
        and the tool_use/tool_result pairing stays intact (invariant I-3).
        """
        window = MAX_CONTEXT.get(self._model_name, 200_000)
        used = sum(len(str(m.content)) for m in messages) // 4
        if used / window < EDIT_AT:
            return []
        results = [m for m in messages if isinstance(m, ToolMessage)]
        stale = results[:-KEEP_RECENT_STEPS] if len(results) > KEEP_RECENT_STEPS else []
        edited = [ToolMessage(content=CLEARED, tool_call_id=m.tool_call_id, id=m.id)
                  for m in stale if m.content != CLEARED and m.id]
        if not edited:
            return []
        self._emit(EventKind.CONTEXT_MANAGED, step=state.get("step", 0), strategy="edited",
                   tokens_before=used, messages=len(messages))
        return edited

    # ── helpers ──────────────────────────────────────────────────────────────
    def _safety(self, state) -> str:
        return state.get("workflow", {}).get("safety", "standard")

    def _emit(self, kind, **data) -> None:
        if self._bus is not None:
            self._bus.emit(kind, **data)


def _is_new_turn(state) -> bool:
    """True when the newest message came from the caller rather than from the loop."""
    msgs = state.get("messages") or []
    return bool(msgs) and type(msgs[-1]).__name__ == "HumanMessage"


def _RESERVED(max_tokens: int):
    """`settle` needs a reservation's id only, to close it on the ledger it was opened
    on.  Here every node builds its own ledger, so the open set is always empty and the
    id is free — the accounting that matters is the snapshot in state."""
    from ..budget.ledger import Reservation
    from ..result import Money as _M
    return Reservation("state", _M.ZERO, 0, max_tokens, 0.0)


class _Ctx:
    __slots__ = ("tainted", "safety")
    def __init__(self, *, tainted: bool, safety: str) -> None:
        self.tainted, self.safety = tainted, safety


async def _invoke(spec, args):
    return await spec.fn(**{k: v for k, v in args.items() if not k.startswith("_")})


def _usage_of(msg) -> Usage:
    u = getattr(msg, "usage_metadata", None) or {}
    details = u.get("input_token_details", {}) or {}
    return Usage(u.get("input_tokens", 0), u.get("output_tokens", 0),
                 details.get("cache_read", 0), details.get("cache_creation", 0))
