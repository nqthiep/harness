"""Tool dispatch — split out of run.py in Round 28.

IDL-13 caps this file at 257 code lines (examples/proof.py SIII) — 250 originally, +2 G-17,
+4 H-7, +1 H-2 (all design/review-architect-round3.md, this loop's own dispatch-decision
logic) — and calls an overrun a design signal rather than something to refactor around.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .context.assembler import canonical as _canonical
from .errors import ToolContractError
from .idempotency import execute_once, idempotency_key
from .memory.inmemory import InMemoryStore
from .middleware import MiddlewareHookError, _call_scope
from .observe.events import EventKind
from .policy.base import Ruling, ToolCall, Verdict
from .policy.builtin import check_flow, emits_of
from .policy.decision import (POLICY_ENGINE_VERSION, Actor, Decision, Scope,
                              actor_json, evidence_json)
from .policy.label import Integrity, Label
from .secrets import redact
from .tools import EFFECT_PROFILES, ToolSpec

#: T-6.3, docs/17-research-alignment.md — `EFFECT_PROFILES[...].retryable` was already
#: derived per effect class (ADR-003: read/external True, write/danger False) but nothing
#: read it to actually retry anything; it only ever reached an `ERROR_RAISED` event's
#: `retryable=` field and a resume-time "was this safe to skip re-running" check
#: (`Agent.aresume`). `MAX_ATTEMPTS` total tries (1 + this many retries), backed off —
#: never applied to `write`/`danger`, which always get exactly one attempt.
MAX_ATTEMPTS = 3
RETRY_BACKOFF_S = 0.05
RETRY_BACKOFF_MAX_S = 1.0


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
    #: T-8.1 gave `Agent`/`Event`/`EventBus` a `tenant_id` for telemetry, but never
    #: threaded it down to the object a `Policy.check(call, ctx)` actually receives —
    #: found while writing `tests/test_roadmap.py`'s own pre-registered "definition of
    #: done" (S-03): a multi-tenant deployment could STAMP events with a tenant but
    #: could not WRITE A POLICY that decides differently per tenant. Appended, not
    #: inserted, so every existing positional `RunContext(run_id, name, step, label,
    #: safety, deadline)` construction keeps working.
    tenant_id: str | None = None
    #: S-03 re-check (design/07-risks-and-open-issues.md, `tests/test_roadmap.py`) —
    #: who this run acts on behalf of, distinct from `tenant_id`. `None` unless the
    #: caller supplies `Agent(principal=...)` — a tool/policy reads `ctx.principal`,
    #: never the model.
    principal: str | None = None
    #: Names only, of tools that COMPLETED earlier in this run — never arguments, never
    #: results (same IDL-15 reasoning the comment above states: a `Policy` gets no
    #: message history, and a tool name alone is not content). Built for
    #: `RequireBeforePolicy` (policy/builtin.py — "the model must have consulted X
    #: before Y is even offered to an approver"), general enough for any policy that
    #: only needs "was tool T already run this run". Appended, same backward-compat
    #: shape as `tenant_id`/`principal` above.
    tools_called: "frozenset[str]" = frozenset()

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
        # S-4/N-8: a fresh, in-memory store per `Dispatcher` — and a `Dispatcher` is
        # built fresh per `RunEngine` per `atry_run()` (this file's own module docstring
        # + `run.py`'s `self._dispatch = Dispatcher(self)`), so this never leaks across
        # runs and needs no persistence. It closes the narrower half of S-4 execute_once
        # (T-6.1) was always meant for — see `_invoke`'s comment at the call site.
        self._idem = InMemoryStore()

    async def _run_tools(self, resp, step: int, run_id: str) -> list[dict[str, Any]]:
        calls = [b for b in resp.content if b.get("type") == "tool_use"]
        ctx = RunContext(run_id, self._e._a.name, step, self._e._taint.label,
                         self._e._a.safety, self._e._l.remaining_wall_clock(),
                         tenant_id=self._e._a.tenant_id, principal=self._e._a.principal,
                         tools_called=frozenset(self.ran))
        planned: list[tuple[dict, ToolSpec | None, Ruling | None]] = []

        for b in calls:
            spec = self._e._a.toolset.get(b["name"])
            self._e._bus.emit(EventKind.TOOL_REQUESTED, step=step, tool=b["name"],
                           call_id=b["id"], arguments=b.get("input", {}))
            if spec is None:
                planned.append((b, None, None)); continue
            call = ToolCall(b["id"], b["name"], b.get("input", {}), spec,
                           idempotency_key(run_id, b["id"]))
            d = self._e._engine.decide(call, ctx)
            if d.verdict is Verdict.ASK:
                self._e._asks += 1
            actor = evidence = None
            if d.verdict is Verdict.ASK:
                # S-25(b): approval fatigue is a channel the model controls — injected
                # content can make it call a `write` tool 40 times with slightly
                # different args, 40 ASKs later the 41st gets approved on reflex. A cap
                # that DENIES once crossed, rather than silently auto-approving, is the
                # fail-closed direction.
                if self._e._asks > self._e._a.max_asks_per_run:
                    d = Ruling(Verdict.DENY,
                              f"more than {self._e._a.max_asks_per_run} approval requests "
                              f"in this run — refusing rather than risk reflex-approval "
                              f"fatigue", "ask-cap")
                    # The callback is never called on this branch — the cap denies
                    # before `resolve()` runs. Leaving `actor` as `None` would fall
                    # through to `Actor.human("approver", via="callback")` below
                    # whenever an `approve=` callback happens to be configured, which
                    # records a human as having denied a call nobody ever asked —
                    # exactly the self-declared-identity problem D-1/S-11 exist to
                    # prevent, on a call this harness's own policy made unilaterally
                    # (self-review finding, verified as a real regression by reverting).
                    actor = Actor.policy("ask-cap")
                else:
                    # A grant already recorded for THIS run answers without asking a
                    # person the same question twice, and a DENY recorded later revokes
                    # it — `lookup` composes with max(), so revocation needs no second
                    # rule. Fail-closed: no matching live row means ASK, which falls
                    # through to the callback exactly as before.
                    prior = self._e._decisions.lookup(
                        b["name"], b.get("input", {}), run_id=run_id, now=_utcnow(),
                        call_id=b["id"], server=spec.server)
                    if prior is Verdict.ASK:
                        d, actor, evidence = await self._e._engine.resolve(
                            d, call, ctx, self._e._a.approve,
                            require_evidence=self._e._a.require_approval_evidence)
                    else:
                        d = Ruling(prior, "a live row in the decision log answers this",
                                  "decision-log")
                        actor = Actor.policy("decision-log-reuse")
                # Every resolved ASK becomes a row — including the ask-cap denial. An
                # audit log that records only what was permitted cannot answer "what did
                # we refuse, and why" (docs/05 §1, the same rule `policy.decided` follows
                # by being emitted for ALLOW as well as DENY). Scoped to THIS call_id, so
                # a grant here never silently covers the next call: `Decision` refuses to
                # be constructed any other way without an `expires_at` (ForeverAllow).
                self._e._decisions.record(Decision(
                    id=f"dec-{b['id']}", verdict=d.verdict,
                    scope=Scope(tool=b["name"], args=dict(b.get("input", {})),
                                server=spec.server, call_id=b["id"]),
                    actor=(actor if actor is not None else
                           (Actor.human("approver", via="callback")
                            if self._e._a.approve is not None else Actor.policy(d.policy))),
                    decided_at=_utcnow(), expires_at=None, run_id=run_id, reason=d.reason,
                    policy_version=POLICY_ENGINE_VERSION, evidence=evidence))
            self._e._bus.emit(EventKind.POLICY_DECIDED, step=step, tool=b["name"],
                           call_id=b["id"], verdict=d.verdict.name, reason=d.reason,
                           policy=d.policy,
                           actor=actor_json(actor), evidence=evidence_json(evidence))
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
        # Neither bucket is recorded here anymore: both `parallel` (H-7 below) and
        # `serial` (S-27, right below) can still turn a planned call into a DENY, so
        # each is only added to `self.ran` once its own re-check actually lets it run.

        if parallel:
            # H-7, design/review-architect-round3.md: `EXTERNAL` is `parallel_safe` AND
            # a confidentiality sink (`max_confidentiality=PUBLIC`, same as `WRITE`) —
            # but only `serial` (below) got S-27's re-check. Two `external` calls in one
            # batch: the first raises the label to SECRET, the second — already
            # scheduled into this SAME `gather` — ran anyway, un-gated, and could return
            # exactly what the first one just read. `_bounded` now re-checks
            # `check_flow` itself, right before its own `_invoke` — the parallel
            # equivalent of `serial`'s re-check, reading `self._e._taint.label` live at
            # the moment of the check as `serial` already does, so ordering inside the
            # batch resolves itself the same way. `self.ran` is only extended from
            # `zip(parallel, done)` below — `asyncio.gather` returns results in the
            # ORDER ITS AWAITABLES WERE PASSED, not completion order, so "in order"
            # (`Result.tools_run`'s own contract, IDL-49) still holds even though the
            # calls themselves may finish out of order.
            done = await asyncio.gather(
                *(self._bounded(b, spec, step) for _, b, spec in parallel),
                return_exceptions=False)
            for (i, _, spec), (r, executed) in zip(parallel, done):
                out[i] = r
                if executed: self.ran.append(spec.name)
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

    async def _bounded(self, b: Mapping[str, Any], spec: ToolSpec,
                       step: int) -> "tuple[dict[str, Any], bool]":
        """NFR-09: parallelism is bounded, so a fan-out cannot fork-bomb a downstream
        service.  The semaphore was specified and the parameter stored, but nothing read
        it until Round 26 measured peak concurrency at 30 against a limit of 4."""
        # H-7, design/review-architect-round3.md: the parallel-batch equivalent of
        # `serial`'s S-27 re-check below — `check_flow` re-read against the LIVE
        # `self._e._taint.label` right here, at the moment this call actually runs, not
        # the pre-batch snapshot. `(result, executed)`: the caller decides `self.ran`
        # membership from this return value rather than from `_bounded` mutating shared
        # state itself, which N concurrent tasks doing so would make order-dependent.
        async with self._e._sem:
            gate = check_flow(self._e._taint.label, spec, self._e._a._grants)
            if gate.verdict is Verdict.DENY:
                return err(b["id"], f"denied by policy: {gate.reason}"), False
            return await self._invoke(b, spec, step), True

    async def _invoke(self, b: Mapping[str, Any], spec: ToolSpec, step: int) -> dict[str, Any]:
        # T-6.3: attempts is 1 for write/danger — always exactly one try, ever. Retrying
        # a call whose outcome is unknown (did the write land before it raised?) is
        # exactly the double-effect class S-4/idempotency exists to guard against.
        retryable = EFFECT_PROFILES[spec.effect].retryable
        attempts = MAX_ATTEMPTS if retryable else 1
        # S-4/N-8: `execute_once` (T-6.1) wraps the call itself, keyed on THIS call_id —
        # stable across every attempt below. Without it, a `read`/`external` tool whose
        # fn() call actually SUCCEEDED but whose json.dumps/truncate/taint-check step
        # AFTER it then raised (a non-serializable return, an unrelated local bug) would
        # retry the WHOLE attempt, silently re-running fn() a second time — the exact
        # "single tool call retried mid-run" gap N-8 named. The closure below encodes
        # `value` to its final `payload` string BEFORE handing it to `execute_once`, so
        # what gets cached (and JSON-round-tripped by `execute_once` itself) is always a
        # plain str — never the raw tool return, which may not be JSON-serializable at
        # all (`ToolContractError` below still fires exactly once, from inside the real
        # call, never from a replay). On a retry this is a cache hit: fn() does not run
        # again. Scoped in-process/in-memory on purpose (`Dispatcher.__init__`) — it
        # dedupes retries WITHIN this run, not across a process crash; that half of S-4
        # stays exactly where it already was, closed by `docs/05 §3`'s resume rule
        # (write/danger tool results are never re-executed on resume, only replayed
        # this same way when a checkpoint already recorded one).
        # `step` folded into the run-id half of the key, not the call_id half — a real
        # provider's tool_use `id` is unique per call, but `FakeModel.tool_call()`'s
        # convenience default (`call_id="c1"`) is not, and dozens of existing tests rely
        # on that being inert across separate steps. Same key shape, one more separator.
        key = idempotency_key(f"{self._e._bus._run_id}:{step}", b["id"])

        async def _call() -> str:
            if spec.subagent is not None:
                value = await self._run_subagent(spec, kwargs)
            else:
                with _call_scope(step=step, call_id=b["id"]):
                    value = await spec.fn(**kwargs)
            try:
                return value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
            except (TypeError, ValueError) as exc:
                raise ToolContractError(
                    f"tool {spec.name!r} returned something that cannot be sent to a model: {exc}"
                ) from None

        reason, t0 = "", time.monotonic()
        for attempt in range(attempts):
            self._e._bus.emit(EventKind.TOOL_STARTED, step=step, tool=spec.name,
                              call_id=b["id"], attempt=attempt)
            t0 = time.monotonic()
            timeout = self._e._l.tool_timeout(spec.timeout_s)      # Round 23: clamped
            try:
                # Model-supplied arguments can never contain an internal name: the schema
                # excludes "_"-prefixed parameters, and strict:true rejects anything extra.
                kwargs = {k: v for k, v in b.get("input", {}).items() if not k.startswith("_")}
                async with asyncio.timeout(timeout):
                    payload, replayed = await execute_once(self._idem, key, _call)
                payload, truncated = truncate(payload, spec.max_result_tokens)
                if self._e._taint.raise_from(emits_of(spec, self._e._a._grants, payload), spec.name):
                    self._e._bus.emit(EventKind.TAINT_RAISED, step=step, source_tool=spec.name)
                self._e._bus.emit(EventKind.TOOL_FINISHED, step=step, tool=spec.name, call_id=b["id"],
                               duration_ms=(time.monotonic() - t0) * 1000, is_error=False,
                               truncated=truncated, replayed=replayed,
                               isolation=spec.isolation)          # H-2
                return {"type": "tool_result", "tool_use_id": b["id"], "content": redact(payload)}
            except asyncio.CancelledError:
                raise                                            # never a tool error
            except MiddlewareHookError as exc:
                # G-17, design/review-architect.md: a bug in the OPERATOR's own
                # before_tool/after_tool, not in the tool — retrying it wastes the
                # tool's whole retry budget on a failure that is deterministic (the
                # same hook bug raises the same way every attempt), and for a
                # write/danger tool whose fn() already ran, letting this fall through
                # to the generic branch below would report a SUCCEEDED call as failed.
                # Stop immediately, regardless of `attempts`/the tool's own effect
                # class, and tag it distinctly (`mw=True`) so the message says a hook
                # broke, not that the tool did.
                return self._tool_error(b, spec, step, str(exc), t0, mw=True)
            except TimeoutError:
                reason = ("timed out: run wall-clock budget reached"
                          if timeout < spec.timeout_s else f"timed out after {spec.timeout_s}s")
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
            more_attempts_left = attempt + 1 < attempts
            time_left = self._e._l.remaining_wall_clock() > 0
            if more_attempts_left and time_left:
                self._e._bus.emit(EventKind.ERROR_RAISED, step=step, where="tool",
                                  type="retrying", message=reason, retryable=True,
                                  attempt=attempt)
                await asyncio.sleep(min(RETRY_BACKOFF_S * (2 ** attempt), RETRY_BACKOFF_MAX_S))
        return self._tool_error(b, spec, step, reason, t0)

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

    def _tool_error(self, b, spec, step, msg, t0, *, mw: bool = False) -> dict[str, Any]:
        # `mw=True`: G-17 — the failure is a middleware hook's, not the tool's.
        self._e._bus.emit(EventKind.ERROR_RAISED, step=step, type=spec.name, message=msg,
                       where=("middleware" if mw else "tool"),
                       retryable=(False if mw else EFFECT_PROFILES[spec.effect].retryable))
        self._e._bus.emit(EventKind.TOOL_FINISHED, step=step, tool=spec.name, call_id=b["id"],
                       duration_ms=(time.monotonic() - t0) * 1000, is_error=True,
                       truncated=False, isolation=spec.isolation)          # H-2
        return err(b["id"], msg)


def _utcnow():
    return datetime.now(timezone.utc)


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


