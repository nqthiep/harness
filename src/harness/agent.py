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
from .errors import ConfigError, SyncInAsyncContextError, UnsafeToolSetError
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
                 "max_parallel_tools", "transcript", "exporters", "_asm", "_watch",
                 "_as_tool_budget", "_grants")

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
    transcript: str | None
    exporters: tuple[Any, ...]
    _asm: Any
    _grants: Grants
    _watch: Any
    _as_tool_budget: Any

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
        accepts_tainted: Sequence[str] = (),
        sensitive: Sequence[str] = (),
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
        _check_subagent_safety(toolset, safety)       # §06.4, before anything is spent

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
            with redaction_scope():
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
