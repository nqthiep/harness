"""Agent — the public facade.  docs/03-public-api.md, tasks T-0.6, T-1.4.

Frozen on purpose (ADR-004): immutability is what keeps the cached prefix stable, and
it is what makes an Agent safe to define at module scope and share across requests.
"""
from __future__ import annotations

import asyncio
import uuid
import warnings
from dataclasses import replace
from typing import Any, Callable, Literal, Sequence

from .budget.ledger import Budget, Ledger
from .context.assembler import ContextAssembler
from .context.linter import PrefixWatcher, check_determinism
from .errors import ConfigError, SyncInAsyncContextError, ToolContractError, UnsafeToolSetError
from .middleware import _run_scope
from .observe.console import ConsoleExporter
from .observe.events import EventBus
from .observe.transcript import TranscriptWriter, read as read_transcript
from .policy.builtin import EffectPolicy, EgressPolicy, TaintPolicy
from .policy.label import Grants
from .policy.engine import PolicyEngine
from .policy.taint import TaintTracker
from .result import Money, Result, StopReason, Usage
from .run import RunEngine
from .secrets import redaction_scope
from .tools import (EFFECT_PROFILES, Effect, ToolSpec, slug,
                    tool as _tool_decorator)
from .tools.registry import ToolSet


_MISSING: Any = object()


class Agent:
    __slots__ = ("name", "job", "toolset", "model", "effort", "budget", "safety",
                 "approve", "policies", "allowed_hosts", "provider", "returns",
                 "max_parallel_tools", "max_asks_per_run", "require_approval_evidence",
                 "transcript", "exporters",
                 "tenant_id", "session_id", "principal", "decisions",
                 "durable", "checkpoint",
                 "_asm", "_watch", "_as_tool_budget", "_grants", "_durable_thread")

    # Declared for the type checker.  The fields are set through `object.__setattr__`
    # (the Agent is frozen), which a checker cannot see — so without these, **a user
    # running mypy on `agent.name` was told the attribute does not exist**, on the
    # attributes §03 documents as public.  Annotations only: with `__slots__` these
    # create no class attribute (Round 39).
    name: str
    job: str
    toolset: ToolSet
    model: str
    effort: str
    budget: Budget
    safety: str
    approve: Any
    policies: tuple[Any, ...]
    allowed_hosts: tuple[str, ...] | None
    provider: Any
    returns: type | None
    max_parallel_tools: int
    max_asks_per_run: int
    require_approval_evidence: bool
    transcript: str | None
    exporters: tuple[Any, ...]
    tenant_id: str | None
    session_id: str | None
    principal: str | None
    decisions: Any
    durable: bool
    checkpoint: Any
    _asm: Any
    _grants: Grants
    _watch: Any
    _as_tool_budget: Any
    _durable_thread: str | None

    def __init__(
        self,
        *args: Any,                     # accepted only to reject them readably (IDL-21)
        name: str = _MISSING,           # sentinel, not a real default: Python validates
        job: str = _MISSING,            # required kw-only args BEFORE the body runs, so a
                                        # bare `name: str` would emit its own TypeError and
                                        # this class's message would never be reached.
        tools: Sequence[ToolSpec] = (),
        model: str = "claude-opus-5",
        effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium",
        returns: type | None = None,
        budget: "Budget | str | None" = None,
        safety: Literal["standard", "strict"] = "standard",
        approve: Callable[..., Any] | None = None,
        policies: Sequence[Any] = (),
        # T-7.2, docs/17-research-alignment.md M7 — BREAKING CHANGE from the pre-M7
        # default: omitting `allowed_hosts=` now means deny ALL external network hosts,
        # not allow all. `()` (the new default) reads as "the allowlist is empty" —
        # exactly what an empty allowlist should mean. `None`, passed EXPLICITLY, is
        # the escape hatch for "no restriction, on purpose" (same shape as S-20's
        # `Budget(usd=None)`: unrestricted is possible, it is never the silent default).
        allowed_hosts: Sequence[str] | None = (),
        accepts_tainted: Sequence[str] = (),
        sensitive: Sequence[str] = (),
        provider: Any | None = None,
        transcript: Any | None = None,
        exporters: Sequence[Any] = (),
        max_parallel_tools: int = 8,
        max_asks_per_run: int = 20,
        # S-11, đã sửa — off by default (a callback that never supplies `AuthEvidence`
        # keeps working exactly as before, same backward-compat discipline every other
        # opt-in gate here follows). ON, `PolicyEngine.resolve()` DENIES a `human` actor
        # reported with no evidence instead of trusting a bare self-declared name — see
        # `policy/decision.py::AuthEvidence`, `design/07-risks-and-open-issues.md` S-11.
        require_approval_evidence: bool = False,
        # T-8.1, docs/17-research-alignment.md M8 — envelope v1 metadata. No natural
        # default exists for either (no multi-tenancy, no Session resource — T-8.6 —
        # built yet): `None` unless the caller supplies one, stamped onto every Event
        # this agent's runs emit.
        tenant_id: str | None = None,
        session_id: str | None = None,
        # S-03 re-check (design/07-risks-and-open-issues.md, `tests/test_roadmap.py`) —
        # who this agent acts on behalf of. Same "no natural default, `None` unless
        # supplied" reasoning as `tenant_id`/`session_id` right above; flows into
        # `RunContext.principal` (`dispatch.py`) for a tool/policy to read, never onto
        # anything the model sees. Classic backend only for now — `durable=True` does
        # not yet thread this through to `build_agent()`.
        principal: str | None = None,
        # The approval audit book (`policy/decision.py`). `None` — the default — means a
        # fresh in-memory `DecisionLog` per run, built in `RunEngine`: an `Agent` is a
        # frozen template shared across concurrent runs, so a log held here would grow
        # for the life of the process and mix every run's rows into one object's memory.
        # Pass one explicitly (`DecisionLog(journal="approvals.jsonl")`) when you want
        # the record to outlive the run — then its lifetime is yours, not the agent's.
        # Classic backend only for now — `durable=True` does not yet thread this through
        # to `build_agent()`.
        decisions: Any | None = None,
        # Backend unification (the "2 API interfaces" complaint this closes): ONE Agent,
        # ONE set of methods, regardless of `durable`. `durable=True` runs on the
        # LangGraph engine (`harness.lg.build_agent()`) instead of the hand-written loop,
        # but nothing LangGraph-shaped — no `HumanMessage`, no raw `.invoke()`, no
        # `thread_id` config — reaches the caller: `run`/`try_run`/`arun`/`atry_run` take
        # the same `str` and return the same `Result` either way. The compiled graph
        # itself stays available directly via `harness.lg.build_agent()` for power users
        # who want it — an escape hatch, not the primary surface.
        #
        # Durability is the ONE thing `durable=True` buys that `durable=False` cannot:
        # progress survives a process restart. `session_id=` (already a field, T-8.1) IS
        # the conversation to reconnect to — pass the same one after a restart and the
        # checkpointer picks the conversation back up; omit it and each Agent instance
        # gets its own, generated once and kept only in memory.
        durable: bool = False,
        checkpoint: Any = None,
    ) -> None:
        if args:
            shown = ", ".join(repr(a) for a in args)
            labelled = ["name", "job"]
            guess = "\n".join(f"        {labelled[i]}={a!r}," if i < len(labelled)
                              else f"        {a!r},  # ← which part is this?"
                              for i, a in enumerate(args))
            raise ConfigError(
                "Agent needs you to label each part, like this:\n\n"
                f"    Agent(\n{guess}\n    )\n\n"
                f"  You wrote:  Agent({shown})\n\n"
                "  -> docs/15-first-agent.md"
            )
        missing = [k for k, v in (("name", name), ("job", job)) if v is _MISSING]
        if missing:
            raise ConfigError(
                f"Agent needs {' and '.join(missing)}.\n\n"
                '    Agent(\n        name="Helper",       # what it is called\n'
                '        job="tell jokes",   # what you want it to do\n    )\n\n'
                "  -> docs/15-first-agent.md"
            )

        toolset = ToolSet(tools)
        grants = Grants(accepts_tainted=frozenset(accepts_tainted),
                        sensitive=frozenset(sensitive))
        _check_tool_set(toolset, grants)               # T-1.4, before anything is spent
        _check_subagent_safety(toolset, safety, approve)  # §06.4, before anything is spent

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "job", job)
        object.__setattr__(self, "toolset", toolset)
        object.__setattr__(self, "_grants", grants)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "effort", effort)
        if returns is not None and not isinstance(returns, type):
            raise ConfigError(
                f"returns= needs the type itself, not one you already made.\n\n"
                f"  You wrote:  returns={type(returns).__name__}(...)\n"
                f"  Write:      returns={type(returns).__name__}\n\n"
                f"  The agent builds one for you from the model's answer.\n\n"
                f"  -> docs/03-public-api.md"
            )
        object.__setattr__(self, "returns", returns)
        object.__setattr__(self, "budget", Budget.parse(budget))
        object.__setattr__(self, "safety", safety)
        object.__setattr__(self, "approve", approve)
        object.__setattr__(self, "policies", tuple(policies))
        if allowed_hosts is None:
            # G-9, design/review-architect.md: `None` is an intentional escape hatch
            # (same shape as `Budget(usd=None)`, S-20) and stays easy to reach — a second
            # breaking change to this parameter right after T-7.2's is not warranted. But
            # "easy" must never mean "silent": an agent that can reach ANY host is a
            # meaningfully different deployment from the `()` default, and a reviewer
            # reading the call site alone (no runtime output) has no way to tell it apart
            # from a typo'd `()` -- warn loudly, every time, rather than trust the arg
            # spelling to carry that weight on its own.
            warnings.warn(
                "Agent(allowed_hosts=None) disables the egress allowlist entirely -- "
                "this agent's tools can reach ANY external host. This is deliberate and "
                "supported, not a bug, but it is easy to reach by accident (the default "
                "is allowed_hosts=(), which allows none). If this is intentional, this "
                "warning is the only cost; if it isn't, pass an explicit allowlist "
                "instead.\n\n  -> docs/17-research-alignment.md M7 / design/review-architect.md G-9",
                UserWarning, stacklevel=2)
        object.__setattr__(self, "allowed_hosts",
                           tuple(allowed_hosts) if allowed_hosts is not None else None)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "transcript", transcript)
        object.__setattr__(self, "exporters", tuple(exporters))
        object.__setattr__(self, "max_parallel_tools", max_parallel_tools)
        object.__setattr__(self, "max_asks_per_run", max_asks_per_run)
        object.__setattr__(self, "require_approval_evidence", require_approval_evidence)
        object.__setattr__(self, "tenant_id", tenant_id)
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "principal", principal)
        object.__setattr__(self, "decisions", decisions)
        # N-3, design/07-risks-and-open-issues.md (closed): the durable engine now
        # parses the final answer against `returns=` too, in `lg/runtime.py::finish()`
        # before `run.finished` fires — the same guard that used to raise `ConfigError`
        # here is gone; `durable=True` and `returns=` are no longer mutually exclusive.
        object.__setattr__(self, "durable", durable)
        object.__setattr__(self, "checkpoint", checkpoint)
        # `principal=`/`decisions=` are classic-backend-only, stated in their own
        # docstrings above — `build_agent()` has no parameter to hand either to, so a
        # `durable=True` agent given one would silently do nothing with it. Same "fail
        # visible, not silent" treatment as `on_delta=`/`.chat()`/`.resume()` under
        # `durable=True` right below: refuse at construction, loudly, rather than accept
        # a promise this backend cannot keep yet.
        if durable and principal is not None:
            raise ConfigError(
                "durable=True doesn't thread principal= through to build_agent() yet — "
                "RunContext.principal would never be set.\n\n"
                "  Drop principal=, or use durable=False.\n\n"
                "  -> design/07-risks-and-open-issues.md"
            )
        if durable and decisions is not None:
            raise ConfigError(
                "durable=True doesn't thread decisions= through to build_agent() yet — "
                "the DecisionLog you passed would never be written to.\n\n"
                "  Drop decisions=, or use durable=False.\n\n"
                "  -> design/07-risks-and-open-issues.md"
            )

        output_format = _output_format(returns) if returns is not None else None
        asm = ContextAssembler(model=model, job=job, tools=toolset, effort=effort,
                               output_format=output_format)
        check_determinism(asm)                        # T-2.3, before a token is spent
        object.__setattr__(self, "_asm", asm)
        # Spans runs, not just one run: time-based drift shows up between calls, and a
        # per-run watcher would compare a prefix only against itself (Round 24).
        object.__setattr__(self, "_watch", PrefixWatcher())
        object.__setattr__(self, "_as_tool_budget", None)
        # The conversation identity a durable run falls back to when the caller never
        # gave `session_id=` — generated lazily, on first durable use (`_durable_thread_id`).
        object.__setattr__(self, "_durable_thread", None)

    def __setattr__(self, *a: Any) -> None:
        raise AttributeError("Agent is frozen — use agent.with_(...) to make a changed copy")

    def __repr__(self) -> str:
        return f"Agent(name={self.name!r}, tools={[t.name for t in self.toolset]})"

    # -- running ----------------------------------------------------------
    async def atry_run(self, message: str, *, on_delta=None, _history=()) -> Result:
        if self.durable:
            if _history:
                # Only `Chat` passes `_history=` today, and `chat()` refuses a durable
                # Agent outright (below) — so this can't yet be reached from the public
                # surface. Kept as a loud guard rather than a silent drop: a durable
                # thread's real history lives in its checkpointer, not in a Python list,
                # so honouring `_history` here would mean two disagreeing sources of
                # truth for the same conversation.
                raise ConfigError(
                    "durable=True conversations keep their own history (the "
                    "checkpointer) — there is nothing for _history= to do here."
                )
            return await self._atry_run_durable(message, on_delta=on_delta)
        provider = _resolve_provider(self.provider)
        run_id = "r_" + uuid.uuid4().hex[:16]
        exporters = list(self.exporters)
        writer = None
        if self.transcript is not None:
            writer = TranscriptWriter(self.transcript)
            exporters.append(writer)
        if ConsoleExporter.should_attach():          # ADR-014: TTY only, stderr only
            exporters.append(ConsoleExporter(self.name))
        bus = EventBus(run_id, exporters, tenant_id=self.tenant_id,
                      session_id=self.session_id)
        ledger = Ledger(self.budget)
        taint = TaintTracker()
        # A policy given as a callable is a FACTORY: a fresh instance per run.  A
        # stateful policy shared across runs — a workflow state machine, say — would
        # otherwise carry one customer's progress into the next request, because §10.5
        # tells people to keep the Agent at module scope for caching (Round 34).
        user_policies = tuple(p() if _is_factory(p) else p for p in self.policies)
        before = _policy_state(user_policies)
        engine = PolicyEngine(
            (EffectPolicy(), TaintPolicy(self._grants), EgressPolicy(self.allowed_hosts)),
            user_policies)
        # Open for exactly the window in which a revealed secret can still be written
        # out — wide enough to redact, narrow enough not to retain (Round 25, RT-13).
        try:
            with redaction_scope(), _run_scope(run_id=run_id, session_id=self.session_id,
                                              tenant_id=self.tenant_id):
                result = await RunEngine(self, provider, ledger, engine, taint, self._asm,
                                         bus, self._watch).run(message, messages=_history,
                                                               on_delta=on_delta)
            _check_shared_policy_state(self.policies, user_policies, before)
            return result
        finally:
            if writer is not None:
                writer.close()

    async def arun(self, message: str, *, on_delta=None) -> Result:
        r = await self.atry_run(message, on_delta=on_delta)
        _raise_if_failed(r)
        return r

    # -- durable running (backend unification) -----------------------------
    async def _atry_run_durable(self, message: str, *, on_delta=None) -> Result:
        if on_delta is not None:
            # The durable engine's model call isn't streamed (`ProviderChatModel._generate`
            # calls the provider's non-streaming path) — a real, documented gap rather
            # than a silently-ignored callback.
            raise ConfigError(
                "durable=True doesn't support on_delta= yet — the model call inside a "
                "durable run isn't streamed.\n\n"
                "  Drop on_delta=, or use durable=False for streaming.\n\n"
                "  -> design/07-risks-and-open-issues.md"
            )
        from langchain_core.messages import HumanMessage

        graph, close = await self._build_durable_graph()
        try:
            thread_id = self.session_id or self._durable_thread_id()
            config = {"configurable": {"thread_id": thread_id}}
            prior = await graph.aget_state(config)
            before = len(prior.values.get("messages") or []) if prior and prior.values else 0
            with _run_scope(run_id=thread_id, session_id=self.session_id,
                            tenant_id=self.tenant_id):
                out = await graph.ainvoke({"messages": [HumanMessage(message)], "step": 0},
                                          config=config)
            return _state_to_result(out, before, thread_id, returns=self.returns)
        finally:
            await close()

    def _durable_thread_id(self) -> str:
        """The conversation identity when the caller didn't give one (`session_id=`).

        Generated once and cached on this instance (Round 34: construct the Agent once,
        keep it at module scope) — stable for calls made through THIS Python object, but
        — unlike an explicit `session_id=` — not recoverable after a real process
        restart, because nothing durable remembers it was ever generated. That is the
        one thing an explicit `session_id=` buys over leaving it out.
        """
        tid = self._durable_thread
        if tid is None:
            tid = "t_" + uuid.uuid4().hex[:16]
            object.__setattr__(self, "_durable_thread", tid)
        return tid

    async def _build_durable_graph(self):
        """Compile this Agent's LangGraph engine and open its checkpoint store — fresh,
        every call, deliberately not cached on the Agent.

        `AsyncSqliteSaver` holds a live `aiosqlite` connection, which owns a background
        thread that keeps the whole process alive until the connection is closed — so a
        default-checkpoint `durable=True` Agent that cached its graph for reuse (the same
        "build once" shape `_asm`/`_watch` use) would make `python examples/whatever.py`
        hang on exit instead of returning, the first time anyone actually tried it. Opened
        and closed around exactly this one call instead — the same shape `atry_run()`
        already uses for its own per-run `TranscriptWriter` — trades a rebuild of the
        (cheap, in-memory) graph structure on every call for an Agent that behaves the way
        every other one in this library does: it returns.

        A caller-supplied `checkpoint=` that is already a built checkpointer (the escape
        hatch — Postgres, Redis, ...) is never closed here: it is the caller's object, not
        one this method opened, so `close()` for that case is a no-op.
        """
        from .lg import build_agent
        from .lg.adapter import ProviderChatModel
        from .models import pricing

        provider = _resolve_provider(self.provider)
        model = ProviderChatModel(provider=provider, asm=self._asm,
                                  max_output=pricing.MAX_OUTPUT.get(self.model, 8_000))
        checkpointer, own_conn = await _build_checkpointer(self.checkpoint, self.name)
        exporters = list(self.exporters)
        writer = None
        if self.transcript is not None:
            writer = TranscriptWriter(self.transcript)
            exporters.append(writer)
        if ConsoleExporter.should_attach():
            exporters.append(ConsoleExporter(self.name))
        graph, _runtime = build_agent(
            model=model, tools=list(self.toolset), budget=self.budget,
            model_name=self.model, safety=self.safety, policies=self.policies,
            allowed_hosts=self.allowed_hosts,
            accepts_tainted=self._grants.accepts_tainted,
            sensitive=self._grants.sensitive, approve=self.approve,
            checkpointer=checkpointer, exporters=tuple(exporters),
            max_asks_per_run=self.max_asks_per_run, tenant_id=self.tenant_id,
            returns=self.returns,
            require_approval_evidence=self.require_approval_evidence,
        )

        async def close() -> None:
            if writer is not None:
                writer.close()
            if own_conn is not None:
                await own_conn.close()

        return graph, close

    async def stream(self, message: str, *, on_delta=None):
        """T-8.5, docs/17-research-alignment.md M8 — `async for ev in agent.stream(msg)`
        over the real event stream, on the taxonomy `docs/05-data-and-state.md §1`
        already closes over (17 kinds) and carrying envelope v1 (T-8.1: `schema_version`,
        `trace_id`, `tenant_id`, `session_id` are already on every `Event`, nothing extra
        to add here). `on_delta=` stays the separate, existing mechanism for token-level
        text streaming — this method does not multiplex deltas into the yielded stream
        as a new kind; the taxonomy is closed on purpose (docs/05 §1), and "tool-call
        delta"/"tool result"/"approval request"/"retry"/"cancellation"/"final" (the
        research-required distinctions T-8.5 names) are ALL already representable on the
        existing kinds: `tool.requested`, `tool.finished`, `policy.decided` (verdict=ASK),
        `error.raised` (retryable=True), `run.finished` (cancelled), `run.finished`
        (final) respectively — inventing a parallel event shape for streaming would be a
        second taxonomy to keep in sync with the first.

        Cancelling the iteration (breaking out of `async for`, or `aclose()`) cancels
        the underlying run — the same cancellation-propagates guarantee T-6.2 already
        gives `atry_run()`, extended through this generator rather than swallowed by it.
        """
        import contextlib

        queue: asyncio.Queue = asyncio.Queue()
        done = object()

        class _QueueExporter:
            def emit(self, event: Any) -> None:
                queue.put_nowait(event)

            def close(self) -> None: ...

        # `with_()` (ADR-004: Agent is frozen) rather than mutating anything — a
        # per-call exporter that only this one streamed call needs, layered on top of
        # whatever exporters the agent already carries (transcript, console, a user's
        # own), never replacing them.
        streaming = self.with_(exporters=tuple(self.exporters) + (_QueueExporter(),))

        async def _run() -> None:
            try:
                await streaming.atry_run(message, on_delta=on_delta)
            finally:
                queue.put_nowait(done)

        task = asyncio.ensure_future(_run())
        try:
            while True:
                item = await queue.get()
                if item is done:
                    break
                yield item
            await task           # re-raises if `_run()` itself raised (a real bug,
                                 # not a run outcome — atry_run() already never does)
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    def try_run(self, message: str, *, on_delta=None, _history=()) -> Result:
        _guard_sync()
        return asyncio.run(self.atry_run(message, on_delta=on_delta, _history=_history))

    def run(self, message: str, *, on_delta=None) -> Result:
        r = self.try_run(message, on_delta=on_delta)
        _raise_if_failed(r)
        return r

    def chat(self, *, budget: Any | None = None) -> "Chat":
        """A stateful conversation with ONE ledger for the whole session (ADR-020).

        Left undefined, `harness chat` is either unbounded across turns or dies after
        three.  The default is 10x the run budget; as it depletes, ADR-017's derived
        max_tokens shrinks, so answers shorten before the session ends.
        """
        if self.durable:
            # `Chat` keeps history in a Python list (`fork()`'s whole point is that this
            # is copyable, in-process state) — the opposite of what `durable=True` is
            # for. A durable conversation already keeps its own history, keyed by
            # `session_id`, in the checkpointer: calling `try_run()`/`run()` again with
            # the same `session_id=` **is** the multi-turn story here, and it needs no
            # extra object to hold it.
            raise ConfigError(
                "durable=True agents don't use .chat() — the checkpointer already keeps "
                "the conversation.\n\n"
                '    agent = Agent(..., durable=True, session_id="cust-42")\n'
                "    agent.run(\"first message\")\n"
                "    agent.run(\"second message\")   # same session_id -> same "
                "conversation\n\n"
                "  -> docs/03-public-api.md"
            )
        return Chat(self, budget=budget)

    def as_tool(self, *, name: str | None = None, description: str | None = None,
                budget: Any | None = None) -> ToolSpec:
        """Turn this agent into a tool another agent can call — task T-4.4.

        Subagents inherit restriction only ([§06.4](../../docs/06-safety.md#4-least-privilege)):

        * the child's effect is the **maximum** of its own tools' effects, so a parent
          cannot gain a capability by wrapping it;
        * the child's safety level cannot be lower than the parent's — that is checked
          here, at construction, not at run time;
        * the child gets an explicit task string, never the parent's transcript.
        """
        child = self
        effects = [t.effect for t in child.toolset]
        worst = max(effects, key=lambda e: _EFFECT_RANK[e]) if effects else Effect.READ

        async def call_subagent(task: str) -> str:
            # The dispatcher owns this call: it caps the child at the parent's remaining
            # budget and settles the child's spend into the parent's ledger (§06.4).
            raise AssertionError("subagent tools are dispatched, not called directly")

        # Slug the NAME alone, then prefix.  Slugging "ask <name>" let the "ask" prefix
        # survive a name written entirely in a non-Latin script, hiding the fact that the
        # name itself had been lost — so two such agents collided (Round 32).
        call_subagent.__name__ = name or f"ask_{slug(child.name, fallback='agent')}"
        call_subagent.__doc__ = (description
                                 or f"Ask {child.name} to do something. {child.job}")[:400]
        call_subagent.__annotations__ = {"task": str, "return": str}
        return _tool_decorator(effect=worst, subagent=child)(call_subagent)

    def resume(self, transcript: Any) -> Result:
        """Continue an interrupted run from its transcript — task T-3.3.

        `read`/`external` calls interrupted mid-flight are re-executed (idempotent by
        their effect class).  `write`/`danger` are **never** re-executed: a library
        cannot know whether the side effect landed, so the model is told instead
        ([§05.3](../../docs/05-data-and-state.md#3-resume-semantics)).
        """
        _guard_sync()
        return asyncio.run(self.aresume(transcript))

    async def aresume(self, transcript: Any) -> Result:
        if self.durable:
            # `resume()` replays a JSONL transcript to find what an interrupted run left
            # mid-flight — the classic backend's only record of that. A durable Agent's
            # record is its checkpointer, not a transcript file, and it is already
            # current the moment the process comes back: call `run()`/`try_run()` again
            # with the same `session_id=` and the graph continues from exactly where the
            # checkpointer last left it — no separate resume step to take.
            raise ConfigError(
                "durable=True agents don't need resume() — call run()/try_run() again "
                "with the same session_id= and the checkpointer picks up where it left "
                "off.\n\n  -> docs/03-public-api.md"
            )
        started: dict[str, str] = {}
        finished: set[str] = set()
        message = ""
        for ev in read_transcript(transcript):
            kind, data = ev["kind"], ev.get("data", {})
            if kind == "run.started":
                message = data.get("message", "")
            elif kind == "tool.started":
                started[data["call_id"]] = data["tool"]
            elif kind == "tool.finished":
                finished.add(data["call_id"])
        interrupted = {cid: name for cid, name in started.items() if cid not in finished}
        unsafe = [n for n in interrupted.values()
                  if (s := self.toolset.get(n)) and not EFFECT_PROFILES[s.effect].retryable]
        note = ""
        if unsafe:
            note = ("\n[resumed] These were interrupted and were NOT retried automatically, "
                    "because they cannot be undone: " + ", ".join(sorted(set(unsafe))) +
                    ". Check whether they took effect before relying on them.")
        return await self.atry_run((message or "continue") + note)

    def with_(self, **overrides: Any) -> "Agent":
        # N-7 (design/07-risks-and-open-issues.md §3): this base dict silently
        # dropped `transcript`, `exporters`, `accepts_tainted`, and `sensitive` —
        # every `with_()` call, not just a caller that happened to touch one of them.
        # `accepts_tainted`/`sensitive` are not readable as `self.accepts_tainted` —
        # `__init__` folds them into `self._grants` (a `Grants`, frozensets) and never
        # keeps the originals — so they come from there, not from a same-named
        # attribute like everything else in this dict.
        base = {k: getattr(self, k) for k in
                ("name", "job", "model", "effort", "returns", "budget", "safety", "approve",
                 "policies", "allowed_hosts", "provider", "max_parallel_tools",
                 "max_asks_per_run", "require_approval_evidence",
                 "tenant_id", "session_id", "principal", "decisions",
                 "transcript", "exporters",
                 "durable", "checkpoint")}
        base["tools"] = list(self.toolset)
        base["accepts_tainted"] = self._grants.accepts_tainted
        base["sensitive"] = self._grants.sensitive
        base.update(overrides)
        return Agent(**base)


class Chat:
    """A multi-turn session.  History lives here, never on the frozen Agent — which is
    what lets one Agent serve many concurrent conversations (§05.4)."""

    __slots__ = ("_agent", "_messages", "_spent", "_budget")

    def __init__(self, agent: "Agent", *, budget: Any | None = None) -> None:
        from decimal import Decimal
        self._agent = agent
        self._messages: list = []
        self._spent = Money.ZERO
        if budget is not None:
            self._budget = Budget.parse(budget)
        elif agent.budget.usd is not None:
            self._budget = replace(agent.budget, usd=agent.budget.usd * Decimal(10))
        else:
            self._budget = agent.budget

    @property
    def spent(self) -> Money: return self._spent
    @property
    def budget(self) -> Budget: return self._budget
    @property
    def messages(self) -> list: return list(self._messages)

    def say(self, message: str, *, on_delta=None) -> Result:
        remaining = (Money(self._budget.usd) - self._spent
                     if self._budget.usd is not None else None)
        if remaining is not None and remaining.decimal <= 0:
            return Result("", StopReason.BUDGET_EXHAUSTED, 0, self._spent, Usage(),
                          "chat", False, (), None,
                          # reachable only inside `remaining is not None`, which
                          # implies `usd is not None` two lines above.
                          f"this conversation reached its budget of "
                          f"{Money(self._budget.usd)}")  # type: ignore[arg-type]
        turn = self._agent
        if remaining is not None:
            turn = self._agent.with_(budget=replace(self._agent.budget,
                                                    usd=remaining.decimal))
        r = turn.try_run(message, _history=self._messages, on_delta=on_delta)
        self._messages = list(r.messages)
        self._spent = self._spent + r.cost
        return r

    def fork(self) -> "Chat":
        """T-8.6 — an independent copy of this conversation's history and spend so
        far: mutating the fork (further `.say()` calls) never touches the original,
        and vice versa. `Chat` is not frozen (unlike `Agent`, ADR-004) so this copies
        by value rather than needing `with_()`."""
        new = Chat(self._agent, budget=self._budget)
        new._messages = list(self._messages)
        new._spent = self._spent
        return new


def _resolve_provider(provider: Any) -> Any:
    """The classic and durable run paths both need "the caller's provider, or the
    default Anthropic one if a key is configured, or a clear error" — factored out so
    the two copies of this couldn't drift (they briefly did, mid-implementation)."""
    if provider is None:
        from .cli import key_status
        if key_status()[0]:
            from .models.anthropic import AnthropicProvider
            provider = AnthropicProvider()
    if provider is None:
        raise ConfigError(
            "this agent has no way to reach a model yet.\n\n"
            "  Run:  harness setup\n\n"
            "  -> docs/15-first-agent.md"
        )
    return provider


async def _build_checkpointer(checkpoint: Any, name: str) -> Any:
    """`checkpoint=` -> a LangGraph checkpointer.

    Three shapes, in order: `None` -> a local SQLite file this creates, named after the
    agent, under `.harness/checkpoints/` (mirrors `SqliteStore`'s own file-per-store
    convention, `memory/sqlite.py`) — durable across a restart with zero configuration,
    which is the whole point of `durable=True`'s default. A `str`/`Path` -> a SQLite file
    at that exact path (or `:memory:`, for a durable-shaped run that intentionally keeps
    nothing). Anything else -> handed to `build_agent()` as-is: a caller's own
    already-built LangGraph checkpointer (Postgres, Redis, ...) — the escape hatch, same
    as the raw graph itself.

    Opened directly with `aiosqlite.connect()` rather than `AsyncSqliteSaver.
    from_conn_string()`: that factory is an async context manager, and the caller here
    (`_build_durable_graph`) already opens and closes it around exactly one call — a
    second, nested context manager would buy nothing. Returns `(checkpointer,
    connection-to-close-or-None)`: a caller-supplied checkpointer is never ours to
    close, so its second element is `None`.
    """
    from pathlib import Path

    if checkpoint is not None and not isinstance(checkpoint, (str, Path)):
        return checkpoint, None
    try:
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    except ImportError as exc:
        raise ConfigError(
            "durable=True needs the SQLite checkpoint package.\n\n"
            "  Run:  pip install 'harness[graph]'\n\n"
            "  -> docs/03-public-api.md"
        ) from exc
    if checkpoint is None:
        path = Path(".harness") / "checkpoints" / f"{slug(name, fallback='agent')}.sqlite3"
        path.parent.mkdir(parents=True, exist_ok=True)
        conn_str = str(path)
    else:
        conn_str = str(checkpoint)
    conn = await aiosqlite.connect(conn_str)
    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    return saver, conn


def _state_to_result(state: Any, before: int, run_id: str,
                     *, returns: type | None = None) -> Result:
    """Graph state -> the same `Result` the classic backend returns — the point at
    which every LangChain type this run touched stops existing for the caller."""
    from langchain_core.messages import AIMessage, ToolMessage

    from .lg.adapter import _lc_to_native

    msgs = state.get("messages") or []
    new = msgs[before:]
    stop = StopReason(state.get("stop_reason") or "completed")
    detail = state.get("detail", "")
    cost = Money(state.get("spent_usd") or "0")
    steps = state.get("step", 0)
    tainted = bool(state.get("tainted", False))
    text = ""
    usage = Usage()
    tools_run: list[str] = []
    call_names: dict[str, str] = {}
    for m in new:
        if isinstance(m, AIMessage):
            if isinstance(m.content, str) and m.content:
                text = m.content
            u = m.usage_metadata or {}
            details = u.get("input_token_details", {}) or {}
            usage = usage + Usage(u.get("input_tokens", 0), u.get("output_tokens", 0),
                                  details.get("cache_read", 0), details.get("cache_creation", 0))
            for tc in (m.tool_calls or []):
                call_names[tc.get("id")] = tc.get("name")
        elif isinstance(m, ToolMessage):
            called = call_names.get(m.tool_call_id)
            if called and getattr(m, "status", "success") != "error":
                tools_run.append(called)
    # N-3: `lg/runtime.py::finish()` already validated this (and downgraded `stop`/
    # `detail` above if it didn't fit) BEFORE `run.finished` fired — this re-parse just
    # builds the actual `Result.value` object, which never travels through checkpointed
    # graph state (state must stay JSON-checkpointable; an arbitrary dataclass instance
    # is not). `try_run()`/`atry_run()` never raise for a run outcome (IDL-11): if this
    # ever disagreed with `finish()`'s own check, the disagreement becomes ERROR here
    # too rather than an unhandled exception out of a method documented not to raise.
    value = None
    if stop is StopReason.COMPLETED and returns is not None:
        from .run import parse_returns
        try:
            value = parse_returns(returns, text)
        except ToolContractError as exc:
            stop, detail = StopReason.ERROR, str(exc)
    return Result(text, stop, steps, cost, usage, run_id, tainted,
                 tuple(_lc_to_native(msgs)), value, detail, tuple(tools_run))


def _output_format(returns: type) -> dict:
    """Build the response schema from `returns=`, using the SAME generator as tools so
    "Python type -> schema" has one definition in the system (ADR-022, AC-24)."""
    import dataclasses
    from .tools.schema import _schema_for

    if dataclasses.is_dataclass(returns):
        props, required = {}, []
        import typing
        hints = typing.get_type_hints(returns)
        for f in dataclasses.fields(returns):
            props[f.name] = _schema_for(hints[f.name], fn_name=returns.__name__,
                                        param=f.name)
            if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
                required.append(f.name)
        schema = {"type": "object", "properties": props, "required": required,
                  "additionalProperties": False}
    else:
        schema = _schema_for(returns, fn_name="returns", param="value")
    return {"type": "json_schema", "schema": schema, "name": getattr(returns, "__name__", "answer")}


_EFFECT_RANK = {Effect.READ: 0, Effect.WRITE: 1, Effect.EXTERNAL: 2, Effect.DANGER: 3}


def _guard_sync() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise SyncInAsyncContextError(
        "you called .run() from inside async code, which would deadlock.\n\n"
        "    result = await agent.arun(...)     ← use this instead\n\n"
        "  -> docs/15-first-agent.md"
    )


def _raise_if_failed(r: Result) -> None:
    """`run()`/`arun()` — the raising half of the pair. review-kiss.md K-9: this used to
    be `Result.raise_for_status()`, a public method that was `run()` rewritten and read
    nowhere else (`try_run()` never calls it — its whole contract is that it never
    raises). Moved off the public surface, kept as the one place the message is built,
    so `run()` and `arun()` can't drift apart on wording."""
    if not r.ok:
        from .errors import RunFailed
        raise RunFailed(r.detail or f"run stopped: {r.stop_reason.value}", r)


def _is_factory(p) -> bool:
    """A Policy *instance* carries `check` as a bound method; a class or a lambda does
    not.  Testing `hasattr(p, "check")` treats the class itself as an instance, because a
    class has the attribute too — the first version of this check did exactly that."""
    import inspect
    return not inspect.ismethod(getattr(p, "check", None))


def _policy_state(policies) -> dict[int, str]:
    """A cheap snapshot of each policy's mutable attributes."""
    out = {}
    for p in policies:
        d = getattr(p, "__dict__", None)
        if d is None:
            d = {s: getattr(p, s, None) for s in getattr(type(p), "__slots__", ())}
        out[id(p)] = repr(sorted((k, repr(v)) for k, v in d.items()))
    return out


def _check_shared_policy_state(declared, used, before) -> None:
    """A shared policy that mutated during a run carries state into the next one.

    Detected here rather than guessed at construction: `EgressPolicy` holds configuration
    and would trip any static "has attributes" heuristic, while a genuine state machine
    only reveals itself by changing.  The first run that mutates one says so (Round 34).
    """
    after = _policy_state(used)
    for original, live in zip(declared, used):
        if _is_factory(original):
            continue                      # a factory: fresh each run, nothing to share
        if before.get(id(live)) != after.get(id(live)):
            raise ConfigError(
                f"the policy {type(live).__name__!r} changed while it ran, and this agent "
                f"reuses the same instance on every run.\n\n"
                f"  The next request would inherit this one's progress — one customer's "
                f"workflow\n  state leaking into another's.\n\n"
                f"  Pass the class instead of an instance, so each run gets a fresh one:\n\n"
                f"      policies=[{type(live).__name__}]        ← not "
                f"{type(live).__name__}()\n\n"
                f"  -> docs/06-safety.md#4-least-privilege"
            )


_SAFETY_RANK = {"standard": 0, "strict": 1}


def _check_subagent_safety(toolset: ToolSet, parent_safety: str, parent_approve: Any) -> None:
    """A subagent inherits restriction only: it may never be laxer than its parent.

    Documented in §06.4 since Round 7 and unenforced until Round 28 executed it — a
    strict parent could delegate to a standard child and silently drop the safety level
    for exactly the work it delegated.

    G-15, design/review-architect.md: `safety` was the only axis this ever checked.
    `dispatch.py::_run_subagent` runs the CHILD's OWN `atry_run` — its OWN `approve=`,
    never the parent's (`run_child = child.with_(budget=...)` only ever replaces
    `budget`). `PolicyEngine.resolve()` auto-`ALLOW`s a surviving `ASK` when `approve is
    None` and the call is neither `safety="strict"` nor `Effect.DANGER` — so a child
    built with no `approve=` at all, wrapped inside a parent that DOES have a real
    human-approval callback, silently auto-approves exactly the WRITE-effect-under-
    `standard`-safety decisions the parent's `approve=` exists to gate. Delegating work
    to a subagent would be a way to escape not just a safety LEVEL (already checked
    above) but the approval callback itself. Same restriction-only framing as the
    safety-rank check: a child needs an `approve=` of its OWN once its parent has one —
    this does not compare them for equality, only that one exists, matching how
    `safety` is only ever rank-compared, not required to be identical.
    """
    for spec in toolset:
        child = spec.subagent
        if child is None:
            continue
        if _SAFETY_RANK[child.safety] < _SAFETY_RANK[parent_safety]:
            raise UnsafeToolSetError(
                f"{child.name!r} runs at safety={child.safety!r} but you are wrapping it "
                f"in an agent at safety={parent_safety!r}.\n\n"
                f"  A subagent can only ever be MORE restricted than its parent, never "
                f"less —\n  otherwise delegating work is a way to escape the safety "
                f"level you chose.\n\n"
                f'  Fix: Agent(name={child.name!r}, ..., safety="{parent_safety}")\n\n'
                f"  -> docs/06-safety.md#4-least-privilege"
            )
        if parent_approve is not None and child.approve is None:
            raise UnsafeToolSetError(
                f"{child.name!r} has no approve= callback, but you are wrapping it in an "
                f"agent that does.\n\n"
                f"  A subagent runs its OWN atry_run() with its OWN approve= — not the "
                f"parent's. Without one, a call inside {child.name!r} that needs approval "
                f"is silently ALLOWED (no human in the loop), even though the parent "
                f"clearly wants one for exactly this kind of decision.\n\n"
                f"  Delegating work to a subagent would otherwise be a way to escape the "
                f"approval callback you set, the same way it could escape a safety "
                f"level (checked above).\n\n"
                f"  Fix: Agent(name={child.name!r}, ..., approve=<the same callback>)\n\n"
                f"  -> docs/06-safety.md#4-least-privilege"
            )


def _check_tool_set(toolset: ToolSet, grants: Grants) -> None:
    """The external+danger combination, caught at construction rather than mid-run (F9.1).

    Kiểm theo TÊN qua `grants`, không theo một trường trên `ToolSpec` — `accepts_tainted`
    không còn là thứ tác giả tool khai được (design/03-tools-and-mcp.md §1.1bis, S-16).
    """
    external = [t for t in toolset if t.effect is Effect.EXTERNAL]
    danger = [t for t in toolset if t.effect is Effect.DANGER
             and t.name not in grants.accepts_tainted]
    if not (external and danger):
        return
    e, d = external[0].name, danger[0].name
    raise UnsafeToolSetError(
        "This helper can read things from the internet AND do something it can't undo.\n\n"
        f"  {e:<11} can bring in words from a website\n"
        f"  {d:<11} can't be undone\n\n"
        f"  A website could trick your helper into using {d} on your stuff.\n\n"
        "  Pick one:\n"
        "    1. Take one of them out, or make two separate helpers.  ← easiest\n"
        f"    2. If {d} really is safe, an OPERATOR says so — not the tool's own code:\n"
        f'         Agent(..., accepts_tainted=["{d}"])\n\n'
        "  -> docs/15-first-agent.md"
    )
