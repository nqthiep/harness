"""Agent — the public facade.  docs/03-public-api.md, tasks T-0.6, T-1.4.

Frozen on purpose (ADR-004): immutability is what keeps the cached prefix stable, and
it is what makes an Agent safe to define at module scope and share across requests.
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from typing import Any, Callable, Literal, Sequence

from .budget.ledger import Budget, Ledger
from .context.assembler import ContextAssembler
from .context.linter import PrefixWatcher, check_determinism
from .errors import ConfigError, RunFailed, SyncInAsyncContextError, UnsafeToolSetError
from .observe.console import ConsoleExporter
from .observe.events import EventBus
from .observe.transcript import TranscriptWriter, read as read_transcript
from .policy.builtin import EffectPolicy, EgressPolicy, TaintPolicy
from .policy.engine import PolicyEngine
from .policy.taint import TaintTracker
from .result import Money, Result, StopReason, Usage
from .run import RunEngine
from .secrets import redaction_scope
from .tools import EFFECT_PROFILES, Effect, ToolSpec, tool as _tool_decorator
from .tools.registry import ToolSet


_MISSING: Any = object()


class Agent:
    __slots__ = ("name", "job", "toolset", "model", "effort", "budget", "safety",
                 "approve", "policies", "allowed_hosts", "provider", "returns",
                 "max_parallel_tools", "transcript", "exporters", "_asm", "_watch",
                 "_as_tool_budget")

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
        allowed_hosts: Sequence[str] | None = None,
        provider: Any | None = None,
        transcript: Any | None = None,
        exporters: Sequence[Any] = (),
        max_parallel_tools: int = 8,
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
                "  -> docs/03-public-api.md#3-agent--the-complete-signature"
            )
        missing = [k for k, v in (("name", name), ("job", job)) if v is _MISSING]
        if missing:
            raise ConfigError(
                f"Agent needs {' and '.join(missing)}.\n\n"
                '    Agent(\n        name="Helper",       # what it is called\n'
                '        job="tell jokes",   # what you want it to do\n    )\n\n'
                "  -> docs/03-public-api.md#3-agent--the-complete-signature"
            )

        toolset = ToolSet(tools)
        _check_tool_set(toolset)                      # T-1.4, before anything is spent
        _check_subagent_safety(toolset, safety)       # §06.4, before anything is spent

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "job", job)
        object.__setattr__(self, "toolset", toolset)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "effort", effort)
        object.__setattr__(self, "returns", returns)
        object.__setattr__(self, "budget", Budget.parse(budget))
        object.__setattr__(self, "safety", safety)
        object.__setattr__(self, "approve", approve)
        object.__setattr__(self, "policies", tuple(policies))
        object.__setattr__(self, "allowed_hosts",
                           tuple(allowed_hosts) if allowed_hosts is not None else None)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "transcript", transcript)
        object.__setattr__(self, "exporters", tuple(exporters))
        object.__setattr__(self, "max_parallel_tools", max_parallel_tools)

        output_format = _output_format(returns) if returns is not None else None
        asm = ContextAssembler(model=model, job=job, tools=toolset, effort=effort,
                               output_format=output_format)
        check_determinism(asm)                        # T-2.3, before a token is spent
        object.__setattr__(self, "_asm", asm)
        # Spans runs, not just one run: time-based drift shows up between calls, and a
        # per-run watcher would compare a prefix only against itself (Round 24).
        object.__setattr__(self, "_watch", PrefixWatcher())
        object.__setattr__(self, "_as_tool_budget", None)

    def __setattr__(self, *a: Any) -> None:
        raise AttributeError("Agent is frozen — use agent.with_(...) to make a changed copy")

    def __repr__(self) -> str:
        return f"Agent(name={self.name!r}, tools={[t.name for t in self.toolset]})"

    # -- running ----------------------------------------------------------
    async def atry_run(self, message: str, *, on_delta=None, _history=()) -> Result:
        provider = self.provider
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
        run_id = "r_" + uuid.uuid4().hex[:16]
        exporters = list(self.exporters)
        writer = None
        if self.transcript is not None:
            writer = TranscriptWriter(self.transcript)
            exporters.append(writer)
        if ConsoleExporter.should_attach():          # ADR-014: TTY only, stderr only
            exporters.append(ConsoleExporter(self.name))
        bus = EventBus(run_id, exporters)
        ledger = Ledger(self.budget)
        taint = TaintTracker()
        engine = PolicyEngine(
            (EffectPolicy(), TaintPolicy(), EgressPolicy(self.allowed_hosts)), self.policies)
        # Open for exactly the window in which a revealed secret can still be written
        # out — wide enough to redact, narrow enough not to retain (Round 25, RT-13).
        try:
            with redaction_scope():
                return await RunEngine(self, provider, ledger, engine, taint, self._asm, bus,
                                       self._watch).run(message, messages=_history,
                                                        on_delta=on_delta)
        finally:
            if writer is not None:
                writer.close()

    async def arun(self, message: str, *, on_delta=None) -> Result:
        r = await self.atry_run(message, on_delta=on_delta)
        r.raise_for_status()
        return r

    def try_run(self, message: str, *, on_delta=None, _history=()) -> Result:
        _guard_sync()
        return asyncio.run(self.atry_run(message, on_delta=on_delta, _history=_history))

    def run(self, message: str, *, on_delta=None) -> Result:
        r = self.try_run(message, on_delta=on_delta)
        r.raise_for_status()
        return r

    def chat(self, *, budget: Any | None = None) -> "Chat":
        """A stateful conversation with ONE ledger for the whole session (ADR-020).

        Left undefined, `harness chat` is either unbounded across turns or dies after
        three.  The default is 10x the run budget; as it depletes, ADR-017's derived
        max_tokens shrinks, so answers shorten before the session ends.
        """
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

        call_subagent.__name__ = name or f"ask_{child.name.lower().replace(' ', '_')}"
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
        from .tools import EFFECT_PROFILES
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
        base = {k: getattr(self, k) for k in
                ("name", "job", "model", "effort", "returns", "budget", "safety", "approve",
                 "policies", "allowed_hosts", "provider", "max_parallel_tools")}
        base["tools"] = list(self.toolset)
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
                          f"this conversation reached its budget of {Money(self._budget.usd)}")
        turn = self._agent
        if remaining is not None:
            turn = self._agent.with_(budget=replace(self._agent.budget,
                                                    usd=remaining.decimal))
        r = turn.try_run(message, _history=self._messages, on_delta=on_delta)
        self._messages = list(r.messages)
        self._spent = self._spent + r.cost
        return r


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
        "  -> docs/03-public-api.md#3-agent--the-complete-signature"
    )


_SAFETY_RANK = {"standard": 0, "strict": 1}


def _check_subagent_safety(toolset: ToolSet, parent_safety: str) -> None:
    """A subagent inherits restriction only: it may never be laxer than its parent.

    Documented in §06.4 since Round 7 and unenforced until Round 28 executed it — a
    strict parent could delegate to a standard child and silently drop the safety level
    for exactly the work it delegated.
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


def _check_tool_set(toolset: ToolSet) -> None:
    """The external+danger combination, caught at construction rather than mid-run (F9.1)."""
    external = [t for t in toolset if t.effect is Effect.EXTERNAL]
    danger = [t for t in toolset if t.effect is Effect.DANGER and not t.accepts_tainted]
    if not (external and danger):
        return
    e, d = external[0].name, danger[0].name
    raise UnsafeToolSetError(
        "this agent can read untrusted content AND take an action it cannot undo.\n\n"
        f"  external: {e}  → can pull in text an attacker controls\n"
        f"  danger:   {d}  → cannot be undone\n\n"
        f"  A page {e!r} reads could tell the agent to run {d!r} on your data.\n\n"
        "  Pick one:\n"
        "    1. Remove one of them, or split into two agents (recommended).\n"
        f"    2. If {d} is genuinely safe to run on untrusted input, say so at the tool:\n"
        f'         @tool(effect="danger", accepts_tainted=True)\n'
        f"         def {d}(...):\n\n"
        "  -> docs/06-safety.md#3-the-taint-lattice--the-designs-central-safety-idea"
    )
