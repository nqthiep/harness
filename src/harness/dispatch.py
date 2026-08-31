"""Tool dispatch — split out of run.py in Round 28.

IDL-13 caps run.py at 250 code lines and calls an overrun a design signal rather than
something to refactor around.  Subagent budget binding pushed it over, so the signal was
taken: run.py is now the state machine, and everything about executing a tool call —
policy resolution, scheduling, timeouts, truncation, taint, subagent binding — lives here.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Mapping

from .context.assembler import canonical as _canonical
from .errors import ToolContractError
from .observe.events import EventKind
from .policy.base import Ruling, ToolCall, Verdict
from .policy.builtin import check_flow, emits_of
from .policy.label import Integrity, Label
from .secrets import redact
from .tools import EFFECT_PROFILES, ToolSpec


@dataclass(slots=True)
class RunContext:
    run_id: str
    agent_name: str
    step: int
    label: Label
    safety: str
    deadline: float
    # Deliberately no message history: a tool that could read the transcript could
    # exfiltrate the whole conversation (IDL-15).

    @property
    def tainted(self) -> bool:
        """Tương thích ngược: `True` khi trục integrity đã UNTRUSTED. Chỉ `label` là
        nguồn sự thật; giữ `.tainted` vì đây là type công khai và một `approve=`
        callback có thể đã đọc nó (S-19 chuyển sang `Label` hai trục)."""
        return self.label.integrity is Integrity.UNTRUSTED


def truncate(text: str, max_tokens: int) -> tuple[str, bool]:
    limit = max_tokens * 4
    if len(text) <= limit:
        return text, False
    cut = text[:limit]
    while cut and cut.encode("utf-8", "ignore").decode("utf-8", "ignore") != cut:
        cut = cut[:-1]                         # never split a UTF-8 character (P-7)
    return cut + f"\n[truncated: ~{max_tokens} of ~{len(text)//4} tokens shown]", True


class Dispatcher:
    def __init__(self, engine) -> None:
        self._e = engine            # the RunEngine, for bus/ledger/policy/taint/agent
        self.ran: list[str] = []

    async def _run_tools(self, resp, step: int, run_id: str) -> list[dict[str, Any]]:
        calls = [b for b in resp.content if b.get("type") == "tool_use"]
        ctx = RunContext(run_id, self._e._a.name, step, self._e._taint.label,
                         self._e._a.safety, self._e._l.remaining_wall_clock())
        planned: list[tuple[dict, ToolSpec | None, Ruling | None]] = []

        for b in calls:
            spec = self._e._a.toolset.get(b["name"])
            self._e._bus.emit(EventKind.TOOL_REQUESTED, step=step, tool=b["name"],
                           call_id=b["id"], arguments=b.get("input", {}))
            if spec is None:
                planned.append((b, None, None)); continue
            call = ToolCall(b["id"], b["name"], b.get("input", {}), spec)
            d = self._e._engine.decide(call, ctx)
            if d.verdict is Verdict.ASK:
                self._e._asks += 1
            # S-25(b): approval fatigue is a channel the model controls — injected content
            # can make it call a `write` tool 40 times with slightly different args, 40
            # ASKs later the 41st gets approved on reflex. A cap that DENIES once crossed,
            # rather than silently auto-approving, is the fail-closed direction.
            if d.verdict is Verdict.ASK and self._e._asks > self._e._a.max_asks_per_run:
                d = Ruling(Verdict.DENY,
                          f"more than {self._e._a.max_asks_per_run} approval requests in "
                          f"this run — refusing rather than risk reflex-approval fatigue",
                          "ask-cap")
            else:
                d = await self._e._engine.resolve(d, call, ctx, self._e._a.approve)
            self._e._bus.emit(EventKind.POLICY_DECIDED, step=step, tool=b["name"],
                           call_id=b["id"], verdict=d.verdict.name, reason=d.reason,
                           policy=d.policy)
            planned.append((b, spec, d))

        # I-3: every tool_use gets exactly one tool_result, in the model's call order.
        out: list[dict[str, Any]] = [None] * len(planned)      # type: ignore[list-item]
        parallel: list[tuple[int, Mapping[str, Any], ToolSpec]] = []
        serial: list[tuple[int, Mapping[str, Any], ToolSpec]] = []
        seen: dict[str, int] = {}          # (name, canonical args) -> index that runs it
        dupes: list[tuple[int, int]] = []  # (duplicate index, original index)
        for i, (b, spec, d) in enumerate(planned):
            if spec is None:
                out[i] = err(b["id"], f"no tool called {b['name']!r} is available"); continue
            if d is None or d.verdict is Verdict.DENY:
                out[i] = err(b["id"], f"denied by policy: {d.reason if d else 'unknown'}"); continue
            key = f"{b['name']}:{_canonical(b.get('input', {}))}"
            if key in seen:
                # T-2.5: run it once, but still return a result per tool_use (I-3).
                dupes.append((i, seen[key]))
                self._e._bus.emit(EventKind.TOOL_REQUESTED, step=step, tool=b["name"],
                               call_id=b["id"], duplicate_of=planned[seen[key]][0]["id"])
                continue
            seen[key] = i
            (parallel if EFFECT_PROFILES[spec.effect].parallel_safe else serial).append((i, b, spec))

        # The executed set, recorded where execution is actually decided.  Anything that
        # `continue`d above — unknown tool, DENY, duplicate — never reaches here (Round 38).
        # `serial` is added to below, per call, once the S-27 recheck confirms it will
        # actually run — not here, since that recheck can now still turn one into a DENY.
        self.ran.extend(spec.name for _, _, spec in parallel)

        if parallel:
            done = await asyncio.gather(
                *(self._bounded(b, spec, step) for _, b, spec in parallel),
                return_exceptions=False)
            for (i, _, _), r in zip(parallel, done):
                out[i] = r
        for i, b, spec in serial:
            # S-27: `d` above was decided against the label from BEFORE this batch ran —
            # a fixed snapshot taken once, at the top of this function. The parallel
            # batch just above (all `read`/`external`) can raise taint or confidentiality
            # mid-batch, and so can an earlier SERIAL call in this very loop, but nothing
            # re-checked `check_flow` before this call actually executes — the exact
            # same-batch staleness the review's `fetch_url` (external, taints) +
            # `run_shell` (danger, already-granted) scenario describes, except it turns
            # out to reach every DENY branch of `check_flow`, not just the integrity one:
            # a `write` newly blocked by SECRET rising mid-batch was just as unchecked.
            # `_invoke`/`self._e._taint` are the live, mutating state — re-reading
            # `.label` right here is the tools-execution-loop equivalent of `_regate`
            # (lg/runtime.py, I-1/S-2): a gate re-checked at the point of consumption.
            gate = check_flow(self._e._taint.label, spec, self._e._a._grants)
            if gate.verdict is Verdict.DENY:
                out[i] = err(b["id"], f"denied by policy: {gate.reason}")
                continue
            self.ran.append(spec.name)
            out[i] = await self._invoke(b, spec, step)
        for i, origin in dupes:
            out[i] = {**out[origin], "tool_use_id": planned[i][0]["id"]}
        return out

    async def _bounded(self, b: Mapping[str, Any], spec: ToolSpec, step: int) -> dict[str, Any]:
        """NFR-09: parallelism is bounded, so a fan-out cannot fork-bomb a downstream
        service.  The semaphore was specified and the parameter stored, but nothing read
        it until Round 26 measured peak concurrency at 30 against a limit of 4."""
        async with self._e._sem:
            return await self._invoke(b, spec, step)

    async def _invoke(self, b: Mapping[str, Any], spec: ToolSpec, step: int) -> dict[str, Any]:
        self._e._bus.emit(EventKind.TOOL_STARTED, step=step, tool=spec.name, call_id=b["id"])
        t0 = time.monotonic()
        timeout = self._e._l.tool_timeout(spec.timeout_s)          # Round 23: clamped
        try:
            # Model-supplied arguments can never contain an internal name: the schema
            # excludes "_"-prefixed parameters, and strict:true rejects anything extra.
            kwargs = {k: v for k, v in b.get("input", {}).items() if not k.startswith("_")}
            async with asyncio.timeout(timeout):
                if spec.subagent is not None:
                    value = await self._run_subagent(spec, kwargs)
                else:
                    value = await spec.fn(**kwargs)
            try:
                payload = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                raise ToolContractError(
                    f"tool {spec.name!r} returned something that cannot be sent to a model: {exc}"
                ) from None
            payload, truncated = truncate(payload, spec.max_result_tokens)
            if self._e._taint.raise_from(emits_of(spec, self._e._a._grants, payload), spec.name):
                self._e._bus.emit(EventKind.TAINT_RAISED, step=step, source_tool=spec.name)
            self._e._bus.emit(EventKind.TOOL_FINISHED, step=step, tool=spec.name, call_id=b["id"],
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

    async def _run_subagent(self, spec: ToolSpec, kwargs: dict) -> str:
        """§06.4: a subagent is capped by the parent's REMAINING budget, and its spend
        settles into the parent's ledger.

        Round 28 found both documented and unenforced.  Each child kept an independent
        ledger, so a $0.10 parent spent $30 through six children while reporting $0.0000 —
        SC-2a's ceiling leaking entirely through a documented feature.

        S-13: that fix covered `usd` only. `steps` and `wall_clock_s` used to come
        straight from the child's own declared `Budget`, untouched — four subagents
        spawned in one turn, each declaring `steps=20`, could burn 80 steps against a
        parent whose own ceiling was 20. `hold_steps()`/`release_steps()` apply the same
        TOCTOU fix `hold()` already has for money to the step axis; `wall_clock_s` needs
        no hold/release (it is not a pooled resource — two children running concurrently
        do not add up to twice the elapsed time), just a cap to what the parent actually
        has left at spawn time (`child_wall_clock`).
        """
        from dataclasses import replace as _replace

        from .result import Money
        child = spec.subagent
        remaining = self._e._l.remaining_usd()
        want = Money(child.budget.usd) if child.budget.usd is not None else None
        held = self._e._l.hold(want) if remaining is not None and want is not None else None
        held_steps = self._e._l.hold_steps(child.budget.steps)
        child_wc = self._e._l.child_wall_clock(child.budget.wall_clock_s)
        run_child = child.with_(budget=_replace(
            child.budget, usd=(held.decimal if held is not None else child.budget.usd),
            steps=held_steps, wall_clock_s=child_wc))
        r = await run_child.atry_run(kwargs.get("task", ""))
        if held is not None:
            self._e._l.release(held, r.cost)           # settle into the PARENT ledger
        else:
            self._e._l.charge(r.cost)
        self._e._l.release_steps(held_steps, r.steps)
        return r.text if r.ok else f"{child.name} stopped: {r.stop_reason.value}. {r.text}"

    def _tool_error(self, b, spec, step, msg, t0) -> dict[str, Any]:
        self._e._bus.emit(EventKind.ERROR_RAISED, step=step, where="tool", type=spec.name,
                       message=msg, retryable=EFFECT_PROFILES[spec.effect].retryable)
        self._e._bus.emit(EventKind.TOOL_FINISHED, step=step, tool=spec.name, call_id=b["id"],
                       duration_ms=(time.monotonic() - t0) * 1000, is_error=True, truncated=False)
        return err(b["id"], msg)


def canonical_len(req) -> str:
    """The serialized request.  Its character count is a hard upper bound on the true
    input token count — no tokenizer emits more tokens than characters (ADR-026)."""
    return _canonical({"system": list(req.system), "tools": list(req.tools),
                       "messages": list(req.messages)})


def err(call_id: str, message: str) -> dict[str, Any]:
    # Redacted here rather than only at the transcript: a tool error goes to the MODEL,
    # which is a wider audience than a log file (Round 25, RT-13).
    return {"type": "tool_result", "tool_use_id": call_id,
            "content": redact(message), "is_error": True}


