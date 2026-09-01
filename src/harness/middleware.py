"""Middleware — compose behavior around every model/tool call, framework-managed.

Not a seventh seam and not the "middleware chain" `docs/02-architecture.md §8` names as
deliberately absent from the LOOP — this module adds nothing to `run.py`/`lg/runtime.py`.
It is sugar, built entirely from three seams a third party already has today
(`ModelProvider`, a tool's own plain callable, `Exporter`): `with_middleware()` wraps
`agent.provider`, wraps each `ToolSpec.fn`, and adds one more `Exporter`, then returns a
new `Agent` via `agent.with_()` (`Agent` is frozen — ADR-004). Nothing here can see a
tool call `Policy` already denied, lower a verdict, waive a budget reservation, or clear
a taint label: a `Middleware` can only add restriction or observation on top of what the
six seams already allow, never bypass them. That is what makes stacking many of these
safe by construction, unlike a framework middleware chain sitting inside the loop
itself — the exact reason §8 gives for not building one there.

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
(already `run.py`/`lg/runtime.py`'s own envelope, `observe/events.py`) needed no new
type, since it was already exactly this pattern.

**What did NOT make it into either context object, and why: identity.** `run_id`/
`session_id`/`tenant_id`/`step`/a tool call's own `call_id` would make both types
genuinely uniform with `Event` — and are deliberately left out rather than added
unreliably. Verified, not assumed: a `contextvars.ContextVar` set before a run starts
does not survive the durable engine's own tool/model execution path, because
`lg/runtime.py` runs its sync nodes through `loop.run_in_executor()`, which does not
copy the calling context into the worker thread (plain `asyncio.run()` inside that
thread starts a fresh one). Threading identity down correctly means changing the call
sites in `run.py`/`dispatch.py`/`lg/runtime.py` themselves — the one thing every
docstring in this file promises this module does not do. Left open rather than shipped
half-working on one engine and silently not on the other.
"""
from __future__ import annotations

import functools
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from ._value import value
from .models.base import ModelRequest, ModelResponse

if TYPE_CHECKING:
    from .agent import Agent
    from .observe.events import Event
    from .tools import ToolSpec

__all__ = ["Middleware", "ModelCall", "ToolInvocation", "ShortCircuit", "with_middleware"]


@value
class ModelCall:
    """`before_model`/`after_model`'s one argument. `response` is `None` in
    `before_model` (the call hasn't happened yet) and always set in `after_model`."""
    __hash__ = None                      # holds a ModelRequest, itself unhashable
    request: ModelRequest
    response: ModelResponse | None = None


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


class ShortCircuit(Exception):
    """Raise from `Middleware.before_tool` to skip the tool's own function entirely and
    use `result` as if it had run — still subject to the same result handling
    (truncation, redaction, taint labelling) a real tool result gets."""

    def __init__(self, result: Any) -> None:
        self.result = result


class Middleware:
    """Subclass this and override only the hooks you need.

    Each hook mirrors a point the classic and durable engines both already pass through
    on every run — a `Middleware`, once wired in with `with_middleware()`, runs there on
    either backend without needing to know which one is underneath.
    """

    def before_model(self, call: ModelCall) -> ModelRequest:
        """Immediately before the provider is called. Return `call.request` unchanged,
        or a replacement (`dataclasses.replace(call.request, ...)`) — e.g. to inject
        content or swap the model. Changing `system`/`tools` differently between
        otherwise identical calls trips the cache-determinism linter
        (`context/linter.py`, `NonDeterministicPromptError`); mutate `messages`, not
        structure, unless the cache break is intended.
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
        instead."""
        return call.kwargs

    def after_tool(self, call: ToolInvocation) -> Any:
        """After the tool's function returned (or after `before_tool` short-circuited
        it) — `call.result` holds it. Return it unchanged, or a replacement."""
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
    """
    if not middlewares:
        return agent
    provider = agent.provider
    if provider is None:
        from .models.anthropic import AnthropicProvider
        provider = AnthropicProvider()
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
        for mw in self._mws:
            request = mw.before_model(ModelCall(request))
        response = await self._inner.complete(request, on_delta=on_delta)
        for mw in self._mws:
            response = mw.after_model(ModelCall(request, response))
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
        for mw in middlewares:
            try:
                kwargs = dict(mw.before_tool(ToolInvocation(name, kwargs)))
            except ShortCircuit as sc:
                return sc.result
        result = await fn(**kwargs)
        for mw in middlewares:
            result = mw.after_tool(ToolInvocation(name, kwargs, result))
        return result

    return replace(spec, fn=wrapped)
