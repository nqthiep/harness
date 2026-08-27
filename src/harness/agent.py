"""Agent — the public facade.  docs/03-public-api.md, tasks T-0.6, T-1.4.

Frozen on purpose (ADR-004): immutability is what keeps the cached prefix stable, and
it is what makes an Agent safe to define at module scope and share across requests.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Callable, Literal, Sequence

from .budget.ledger import Budget, Ledger
from .context.assembler import ContextAssembler
from .context.linter import PrefixWatcher, check_determinism
from .errors import ConfigError, RunFailed, SyncInAsyncContextError, UnsafeToolSetError
from .observe.events import EventBus
from .policy.builtin import EffectPolicy, EgressPolicy, TaintPolicy
from .policy.engine import PolicyEngine
from .policy.taint import TaintTracker
from .result import Result
from .run import RunEngine
from .secrets import redaction_scope
from .tools import Effect, ToolSpec
from .tools.registry import ToolSet


_MISSING: Any = object()


class Agent:
    __slots__ = ("name", "job", "toolset", "model", "effort", "budget", "safety",
                 "approve", "policies", "allowed_hosts", "provider", "returns",
                 "max_parallel_tools", "_asm", "_watch")

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
        object.__setattr__(self, "max_parallel_tools", max_parallel_tools)

        asm = ContextAssembler(model=model, job=job, tools=toolset, effort=effort)
        check_determinism(asm)                        # T-2.3, before a token is spent
        object.__setattr__(self, "_asm", asm)
        # Spans runs, not just one run: time-based drift shows up between calls, and a
        # per-run watcher would compare a prefix only against itself (Round 24).
        object.__setattr__(self, "_watch", PrefixWatcher())

    def __setattr__(self, *a: Any) -> None:
        raise AttributeError("Agent is frozen — use agent.with_(...) to make a changed copy")

    def __repr__(self) -> str:
        return f"Agent(name={self.name!r}, tools={[t.name for t in self.toolset]})"

    # -- running ----------------------------------------------------------
    async def atry_run(self, message: str, *, on_delta=None) -> Result:
        provider = self.provider
        if provider is None:
            raise ConfigError(
                "this agent has no way to reach a model yet.\n\n"
                "  Run:  harness setup\n\n"
                "  -> docs/15-first-agent.md"
            )
        run_id = "r_" + uuid.uuid4().hex[:16]
        bus = EventBus(run_id)
        ledger = Ledger(self.budget)
        taint = TaintTracker()
        engine = PolicyEngine(
            (EffectPolicy(), TaintPolicy(), EgressPolicy(self.allowed_hosts)), self.policies)
        # Open for exactly the window in which a revealed secret can still be written
        # out — wide enough to redact, narrow enough not to retain (Round 25, RT-13).
        with redaction_scope():
            return await RunEngine(self, provider, ledger, engine, taint, self._asm, bus,
                                   self._watch).run(message, on_delta=on_delta)

    async def arun(self, message: str, *, on_delta=None) -> Result:
        r = await self.atry_run(message, on_delta=on_delta)
        r.raise_for_status()
        return r

    def try_run(self, message: str, *, on_delta=None) -> Result:
        _guard_sync()
        return asyncio.run(self.atry_run(message, on_delta=on_delta))

    def run(self, message: str, *, on_delta=None) -> Result:
        r = self.try_run(message, on_delta=on_delta)
        r.raise_for_status()
        return r

    def with_(self, **overrides: Any) -> "Agent":
        base = {k: getattr(self, k) for k in
                ("name", "job", "model", "effort", "returns", "budget", "safety", "approve",
                 "policies", "allowed_hosts", "provider", "max_parallel_tools")}
        base["tools"] = list(self.toolset)
        base.update(overrides)
        return Agent(**base)


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
