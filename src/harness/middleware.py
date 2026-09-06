"""Middleware — compose behavior around every model/tool call, framework-managed.

Not a seventh seam and not the "middleware chain" `docs/02-architecture.md §8` names as
deliberately absent from the LOOP — the loop itself (`run.py`/`dispatch.py`/
`lg/runtime.py`) never calls into `Middleware`, never reads `Agent.exporters`/`.provider`
to decide what to do, and has no idea `with_middleware()` exists. This module is sugar,
built entirely from three seams a third party already has today (`ModelProvider`, a
tool's own plain callable, `Exporter`): `with_middleware()` wraps `agent.provider`, wraps
each `ToolSpec.fn`, and adds one more `Exporter`, then returns a new `Agent` via
`agent.with_()` (`Agent` is frozen — ADR-004). Nothing here can see a tool call `Policy`
already denied, lower a verdict, waive a budget reservation, or clear a taint label: a
`Middleware` can only add restriction or observation on top of what the six seams already
allow, never bypass them. That is what makes stacking many of these safe by construction,
unlike a framework middleware chain sitting inside the loop itself — the exact reason §8
gives for not building one there.

`Middleware` is a plain subclassable base class, not a `Protocol` like this library's
other seams. Deliberately: `Policy`/`Exporter` each have one or two tightly-coupled
methods a caller implements in full. `Middleware` has five independent, optional hooks —
almost every real one wants exactly one or two — and `Protocol` has no notion of an
optional method. A base class with no-op/pass-through defaults lets a subclass override
only what it needs; that ergonomics gap, not a change of philosophy, is the whole reason
this one seam looks different from the rest.

**One context object per phase, not a grab bag of positional arguments** — the direct
answer to a real question this design started with two mismatched shapes for
(`before_model(request)` vs. `before_tool(name, kwargs)` vs. `on_event(event)`, three
different calling conventions to remember). `ModelCall` carries `before_model`/
`after_model`'s payload; `ToolInvocation` carries `before_tool`/`after_tool`'s; `Event`
(already `run.py`/`lg/runtime.py`'s own envelope, `observe/events.py`) needed no new type.

**`.identity` on both — `run_id`/`session_id`/`tenant_id`/`step`/`call_id` (the last, tool
calls only).** A first version of this module left these out: an early, hand-rolled test
of `contextvars.ContextVar` propagation through a bare `loop.run_in_executor()` call
showed a set value NOT surviving into a worker thread, and the LangGraph engine's own
sync nodes (`lg/runtime.py::call_model`/`_run_tools`) run exactly that way — so the
conclusion was "cannot be threaded through the durable engine without touching core
files, so don't ship it half-working." That conclusion was WRONG, caught by testing
against the real dependency instead of a hand-rolled stand-in for it:
`langgraph.pregel._executor` copies the calling `contextvars.Context`
(`contextvars.copy_context()`) before dispatching a sync node to its thread, and
`asyncio.to_thread` (`tools/__init__.py`'s own sync-tool wrapper) does the same — a
`ContextVar` set before `graph.ainvoke()` reliably reaches a tool's own function body,
verified end to end against `harness.lg.build_agent()` before this was built, not assumed
from the earlier synthetic test. `_run_scope()` (whole run: `run_id`/`session_id`/
`tenant_id`) is entered once by `Agent.atry_run`/`Agent._atry_run_durable`; `_call_scope()`
(one call: `step`, and `call_id` for a tool) is entered once per model or tool call, by
`run.py`, `dispatch.py`, and `lg/runtime.py` — the three places that already know a
call_id or a step number and did not, before this, have anywhere to put it. Each is a
context-manager wrapped around an existing call site, setting nothing the loop itself
reads back — a `Middleware` that never asks for `.identity` cannot tell this exists.
"""
from __future__ import annotations

import contextlib
import contextvars
import functools
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Iterator, Mapping, Sequence

from ._value import value
from .credentials import resolve_provider
from .errors import HarnessError
from .models.base import ModelRequest, ModelResponse

if TYPE_CHECKING:
    from .agent import Agent
    from .observe.events import Event
    from .tools import ToolSpec

__all__ = ["Middleware", "MiddlewareHookError", "ModelCall", "RunIdentity",
          "ToolInvocation", "ShortCircuit", "with_middleware"]


@value
class RunIdentity:
    """WHICH run/call a hook is seeing — read by `ModelCall.identity`/
    `ToolInvocation.identity`. Every field is `None` when nothing set it, which happens
    outside a real run (e.g. constructing a `ModelCall` by hand in a test) — never
    guessed, never a placeholder value."""
    run_id: str | None = None
    session_id: str | None = None
    tenant_id: str | None = None
    step: int | None = None
    call_id: str | None = None            # a tool call's own id; None for a model call


_EMPTY_IDENTITY = RunIdentity()
_CURRENT: contextvars.ContextVar[RunIdentity] = contextvars.ContextVar(
    "harness_middleware_identity", default=_EMPTY_IDENTITY)


#: Where an argument rewrite gets reported. A callable rather than the `EventBus`
#: itself: this module sits above `observe` in the layering and must not learn its
#: types to make a record (the `EgressPolicy`-shaped mistake of a low layer importing
#: a high one). `Agent` sets it for the run; nothing set means nothing to tell.
_AMEND: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "harness_middleware_amend", default=None)


def _identity() -> RunIdentity:
    return _CURRENT.get()


@contextlib.contextmanager
def _run_scope(*, run_id: str, session_id: str | None,
               tenant_id: str | None, on_amend: Any = None) -> Iterator[None]:
    """Entered once, by `Agent`, around the whole run — classic (`atry_run`) or durable
    (`_atry_run_durable`). Not part of the public API."""
    token = _CURRENT.set(RunIdentity(run_id=run_id, session_id=session_id,
                                     tenant_id=tenant_id))
    amend_token = _AMEND.set(on_amend)
    try:
        yield
    finally:
        _AMEND.reset(amend_token)
        _CURRENT.reset(token)


@contextlib.contextmanager
def _call_scope(*, step: int | None, call_id: str | None = None) -> Iterator[None]:
    """Entered once per model or tool call, layered on top of the enclosing
    `_run_scope` (`run_id`/`session_id`/`tenant_id` carry through unchanged; `step`/
    `call_id` are this call's own). Not part of the public API.

    Safe under the classic backend's real parallel tool execution
    (`dispatch.py::_bounded`, `asyncio.gather`): each concurrent call is its own
    `asyncio.Task`, and a `contextvars.Context` is copied per `Task` at creation — one
    task's `_call_scope` can never leak into a sibling's.
    """
    base = _CURRENT.get()
    token = _CURRENT.set(replace(base, step=step, call_id=call_id))
    try:
        yield
    finally:
        _CURRENT.reset(token)


@value
class ModelCall:
    """`before_model`/`after_model`'s one argument. `response` is `None` in
    `before_model` (the call hasn't happened yet) and always set in `after_model`."""
    __hash__ = None                      # holds a ModelRequest, itself unhashable
    request: ModelRequest
    response: ModelResponse | None = None
    identity: RunIdentity = _EMPTY_IDENTITY


@value
class ToolInvocation:
    """`before_tool`/`after_tool`'s one argument. `result` is `None` in `before_tool`
    (the function hasn't run yet) and always set in `after_tool`.

    A different type from `harness.ToolCall` (`policy/base.py`) on purpose: that one
    carries a `ToolSpec` and exists for a `Policy` to decide ALLOW/ASK/DENY *before*
    dispatch; this one carries the actual keyword arguments and exists for a
    `Middleware` to observe or modify what happens *after* that decision already ran.
    """
    __hash__ = None                      # holds a Mapping
    name: str
    kwargs: Mapping[str, Any]
    result: Any = None
    identity: RunIdentity = _EMPTY_IDENTITY


class ShortCircuit(Exception):
    """Raise from `Middleware.before_tool` to skip the tool's own function entirely and
    use `result` as if it had run — still subject to the same result handling
    (truncation, redaction, taint labelling) a real tool result gets."""

    def __init__(self, result: Any) -> None:
        self.result = result


class MiddlewareHookError(HarnessError):
    """G-17, design/review-architect.md: `_wrap_tool()`'s `before_tool`/`after_tool`
    calls are inside the SAME `try` a tool's own `fn()` runs in (`dispatch.py::_invoke`),
    so a bug in an operator's own `Middleware` subclass — a hook that raises for a
    reason that has nothing to do with the tool it wraps — used to surface exactly like
    the tool itself had failed: same retry budget (`EFFECT_PROFILES[effect].retryable`),
    same `error.raised` event shape, same message. Two problems, not one: (1) a `write`/
    `danger` tool whose `after_tool` throws AFTER `fn()` already ran and had a real side
    effect gets reported as a failed call — the caller sees "the tool failed" when it
    actually succeeded and a hook broke on the way out; (2) a hook bug is DETERMINISTIC
    (the same input raises the same way every time), so retrying it wastes the tool's
    entire retry budget on attempts that cannot possibly succeed, before the run ever
    finds out the real cause.

    `_wrap_tool()` (below) catches a non-`ShortCircuit` exception from `before_tool`/
    `after_tool` specifically — never from `fn()` itself, which keeps raising whatever
    it always raised — and re-raises this instead, chaining the original via `__cause__`
    (`raise ... from exc`, never swallowed). `dispatch.py::_invoke` catches this ahead of
    its generic `except Exception:`, skips the remaining retry attempts regardless of
    the tool's own effect class, and tags the resulting event `where="middleware"` with
    a message that says a hook broke, not that the tool did.

    It also carries WHICH hook failed, because the two answer opposite questions about
    whether the tool ran and both engines have to record the answer. `before_tool`
    raising means `fn()` never executed; `after_tool` raising means it executed and, for
    a `write`/`danger` tool, its side effect has already landed. `Result.tools_run`
    records what EXECUTED (IDL-49), so guessing here is not neutral: guess "did not run"
    and a `wipe` that ran passes `assert_no_tool`, whose whole job is to prove it did
    not.
    """

    def __init__(self, message: str, hook: str = "") -> None:
        super().__init__(message)
        #: `"before_tool"` or `"after_tool"`; empty only from an older caller.
        self.hook = hook


class Middleware:
    """Subclass this and override only the hooks you need.

    Each hook mirrors a point the classic and durable engines both already pass through
    on every run — a `Middleware`, once wired in with `with_middleware()`, runs there on
    either backend without needing to know which one is underneath.
    """

    def before_model(self, call: ModelCall) -> ModelRequest:
        """Immediately before the provider is called. Return `call.request` unchanged,
        or a replacement (`dataclasses.replace(call.request, ...)`) — e.g. to inject
        content or swap the model. Mutate `messages`, not structure.

        **Nothing here is checked, and an earlier version of this docstring said
        otherwise.** It claimed that varying `system`/`tools` between otherwise
        identical calls "trips the cache-determinism linter
        (`context/linter.py`, `NonDeterministicPromptError`)". It does not, and the
        claim was measured false: a middleware rewriting `request.system` on every call
        completes a run with no error at all. `PrefixWatcher.observe` is called from
        exactly one site (`run.py`, once per step) with the ASSEMBLER's own
        `render_prefix()` — bytes that never pass through a middleware — so the linter
        cannot see anything a hook here does. A control that is specified and never
        executed is not a control (R-16), so it is described as absent rather than
        implied.

        What that leaves you responsible for, all of it real:

        * **Prompt caching.** Varying `system`/`tools` per call invalidates the cached
          prefix silently — no error, just a bill that stops falling.
        * **The budget CEILING, though not the accounting.** The pre-flight count runs
          on a probe the assembler builds (`run.py`), and `reserve()`'s `hard_max_input`
          comes from that same pre-middleware request, while `_MiddlewareProvider.
          count_input_tokens` delegates straight through without running any hook. So
          content injected here is unseen by both — measured once at 33 chars counted
          against 5064 the provider received. `settle()` still bills `resp.usage`, the
          provider's real numbers, so spend is not hidden; what breaks is the ceiling.
          Sharper still: when `hard_max_input` fits, `reserve()` records `exact=True` and
          the run emits `BUDGET_RESERVED(exact=True)` — an injection makes the transcript
          positively assert a bound that is false. Keep anything added here small and
          bounded, and put anything large in a tool result, which is counted normally.
        * **Window management.** `context/window.manage` measures `msgs`, which never
          contains what a hook added.

        Making the linter cover this is not a docstring away: `_MiddlewareProvider` is
        built once in `with_middleware()` and stored on a frozen `Agent` that is shared
        across concurrent runs, so it has nowhere per-run to keep a watcher — the same
        constraint that makes `PrefixWatcher`, `Ledger` and `TaintTracker` per-run
        objects (ADR-079).
        """
        return call.request

    def after_model(self, call: ModelCall) -> ModelResponse:
        """Immediately after the provider returns (`call.response` is always set here).
        Return it unchanged, or a replacement built with
        `dataclasses.replace(call.response, ...)`."""
        assert call.response is not None
        return call.response

    def before_tool(self, call: ToolInvocation) -> Mapping[str, Any]:
        """After `Policy` has already ruled ALLOW on this call — never for one it
        denied. Return `call.kwargs` (unchanged or modified), or raise
        `ShortCircuit(result)` to skip the tool's own function and use `result`
        instead.

        **Returning modified kwargs changes the call the verdict was about.** The hook
        cannot reach a call `Policy` denied, so it cannot bypass a verdict — but it can
        change the subject of one, and that is a trust boundary rather than a detail:
        a middleware is as privileged as the policy set. Every rewrite emits
        `tool.arguments_amended` naming this class, so "what was approved" and "what ran"
        stay two comparable facts in the transcript instead of one that quietly changed.
        docs/02 §4 said stacking hooks "can only add restriction or observation, never
        bypass one"; the parenthetical was right and the conclusion was not.

        Fires once per RETRY attempt, not once per logical call: a `read`/`external`
        tool gets up to `MAX_ATTEMPTS` tries on failure (`dispatch.py`/`lg/runtime.py`,
        T-6.3), and every attempt re-enters this hook with the SAME `call.identity.
        call_id` — verified: a `before_tool` that keeps raising is retried exactly as
        many times as the tool's own effect class allows. Side effects here (a counter,
        a quota decrement) should key off `call_id` if they need to fire once per
        logical call rather than once per attempt.

        Never called at all for a subagent tool (`agent.as_tool()`) — a subagent call is
        dispatched through `_run_subagent`, which never touches `ToolSpec.fn`, so there
        is nothing here to wrap.
        """
        return call.kwargs

    def after_tool(self, call: ToolInvocation) -> Any:
        """After the tool's function returned (or after `before_tool` short-circuited
        it) — `call.result` holds it. Return it unchanged, or a replacement.

        Same retry and subagent notes as `before_tool` apply here.
        """
        return call.result

    def on_event(self, event: "Event") -> None:
        """Every `Event` this run emits (`docs/05-data-and-state.md §1`) — the same
        stream an `Exporter` sees. Observation only: an exception here is caught and
        this hook is disabled for the rest of the run, the same rule every `Exporter`
        follows (`docs/10-observability-ops.md §1`) — it never stops the run."""
        return None


def with_middleware(agent: "Agent", *middlewares: Middleware) -> "Agent":
    """A new `Agent` (frozen — ADR-004) with every one of `middlewares` wired onto
    `provider=`/`tools=`/`exporters=`. Hooks run in the order given, on both the classic
    and `durable=True` engines alike (both call the same `provider=`).

    A tool built from another `Agent` (`agent.as_tool()`) is wrapped too, but its
    `before_tool`/`after_tool` never actually fire: dispatching a subagent call goes
    through `_run_subagent`/`_run_tools`'s subagent branch, which calls the CHILD
    Agent's own `atry_run()` directly and never touches `ToolSpec.fn` — wrap the CHILD
    Agent with its own `with_middleware()` to see inside its calls.
    """
    if not middlewares:
        return agent
    # `resolve_provider`, not a fourth copy of "build the default provider". It exists
    # so the classic and durable paths could not drift; this was a third site that had
    # drifted anyway, and the drift was visible: with no key configured,
    # `with_middleware()` produced a live agent that failed mid-run with
    # `ProviderError: TypeError: "Could not resolve authentication method..."`, where
    # the same agent unwrapped raises `ConfigError: ... Run: harness setup` (ADR-086).
    # Imported at MODULE level now: it lives in `credentials`, so reaching it no longer
    # means reaching back into `agent.py`, which imports this module (ADR-098).
    provider = resolve_provider(agent.provider)
    return agent.with_(
        provider=_MiddlewareProvider(provider, middlewares),
        tools=[_wrap_tool(t, middlewares) for t in agent.toolset],
        exporters=tuple(agent.exporters) + (_MiddlewareExporter(middlewares),),
    )


class _MiddlewareProvider:
    """`ModelProvider`-shaped — wraps the real one, hooking `complete()` only. Pricing,
    output ceiling, and token counting stay the inner provider's own, unmodified: a
    middleware that wants to change what a model costs belongs in `before_model`/
    `after_model`, not by lying about the price table."""

    name = "middleware"

    def __init__(self, inner: Any, middlewares: Sequence[Middleware]) -> None:
        self._inner, self._mws = inner, tuple(middlewares)

    def price(self, model: str) -> Any:
        return self._inner.price(model)

    def max_output(self, model: str) -> int:
        return self._inner.max_output(model)

    async def count_input_tokens(self, request: ModelRequest) -> int:
        return await self._inner.count_input_tokens(request)

    async def complete(self, request: ModelRequest, *, on_delta=None) -> ModelResponse:
        identity = _identity()
        for mw in self._mws:
            request = mw.before_model(ModelCall(request, identity=identity))
        response = await self._inner.complete(request, on_delta=on_delta)
        for mw in self._mws:
            response = mw.after_model(ModelCall(request, response, identity=identity))
        return response


class _MiddlewareExporter:
    """`Exporter`-shaped. `close()` is a no-op: nothing here owns a resource to release
    — each `Middleware.on_event` decides for itself what a `close`-shaped signal means,
    if anything, by reading `run.finished`/`error.raised` off the same stream."""

    def __init__(self, middlewares: Sequence[Middleware]) -> None:
        self._mws = tuple(middlewares)

    def emit(self, event: "Event") -> None:
        for mw in self._mws:
            mw.on_event(event)

    def close(self) -> None:
        return None


def _wrap_tool(spec: "ToolSpec", middlewares: Sequence[Middleware]) -> "ToolSpec":
    """`ToolSpec.fn` is always awaitable by the time it reaches here (`tools/__init__.py`
    normalizes a sync function at `@tool` decoration time) — this wrapper doesn't need to
    care whether the original was `def` or `async def`."""
    fn, name = spec.fn, spec.name

    @functools.wraps(fn)
    async def wrapped(**kwargs: Any) -> Any:
        identity = _identity()
        amend = _AMEND.get()
        for mw in middlewares:
            try:
                before = kwargs
                kwargs = dict(mw.before_tool(ToolInvocation(name, kwargs, identity=identity)))
            except ShortCircuit as sc:
                return sc.result
            except Exception as exc:
                # G-17: a bug in the OPERATOR's own before_tool, not the tool — never
                # let it look like fn() failed. fn() hasn't even run yet this attempt.
                raise MiddlewareHookError(
                    f"{type(mw).__name__}.before_tool raised for tool {name!r}: {exc}",
                    hook="before_tool") from exc
            # ADR-109. A hook cannot bypass the VERDICT — it never sees a call `Policy`
            # denied — but it can change the call the verdict was about, and until this
            # existed the transcript then positively asserted an argument set that never
            # ran. Measured: `policy.decided fetch ALLOW` for
            # `{"url": "http://docs.python.org/x"}` while the tool was called with
            # `http://evil.example/exfil`. Reporting it does not stop it; a rewrite is a
            # documented, useful power (redaction, defaulting). Silence was the defect.
            #
            # Reached only when `before_tool` RETURNED. G-17's wrapper above re-raises,
            # so a hook that threw never gets here — which is right: it amended nothing.
            if amend is not None and kwargs != before:
                amend(name, type(mw).__name__, before, kwargs)
        result = await fn(**kwargs)
        for mw in middlewares:
            try:
                result = mw.after_tool(ToolInvocation(name, kwargs, result, identity=identity))
            except Exception as exc:
                # Same reasoning, after fn() already ran (and, for a write/danger tool,
                # already had its real side effect) — the failure is the HOOK's, not a
                # reason to treat this call as though the tool itself failed.
                raise MiddlewareHookError(
                    f"{type(mw).__name__}.after_tool raised for tool {name!r}: {exc}",
                    hook="after_tool") from exc
        return result

    return replace(spec, fn=wrapped)
