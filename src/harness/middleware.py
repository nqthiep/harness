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
"""
from __future__ import annotations

import functools
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Sequence

from .models.base import ModelRequest, ModelResponse

if TYPE_CHECKING:
    from .agent import Agent
    from .observe.events import Event
    from .tools import ToolSpec

__all__ = ["Middleware", "ShortCircuit", "with_middleware"]


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

    def before_model(self, request: ModelRequest) -> ModelRequest:
        """Immediately before the provider is called. Return `request` unchanged, or a
        replacement (`dataclasses.replace(request, ...)`) — e.g. to inject content or
        swap `request.model`. Changing `system`/`tools` differently between otherwise
        identical calls trips the cache-determinism linter (`context/linter.py`,
        `NonDeterministicPromptError`); mutate `messages`, not structure, unless the
        cache break is intended.
        """
        return request

    def after_model(self, request: ModelRequest, response: ModelResponse) -> ModelResponse:
        """Immediately after the provider returns. Return `response` unchanged, or a
        replacement built with `dataclasses.replace(response, ...)`."""
        return response

    def before_tool(self, name: str, kwargs: dict) -> dict:
        """After `Policy` has already ruled ALLOW on this call — never for one it
        denied. Return `kwargs` (unchanged or modified), or raise `ShortCircuit(result)`
        to skip the tool's own function and use `result` instead."""
        return kwargs

    def after_tool(self, name: str, kwargs: dict, result: Any) -> Any:
        """After the tool's function returned (or after `before_tool` short-circuited
        it). Return `result` unchanged, or a replacement."""
        return result

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
            request = mw.before_model(request)
        response = await self._inner.complete(request, on_delta=on_delta)
        for mw in self._mws:
            response = mw.after_model(request, response)
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
                kwargs = mw.before_tool(name, kwargs)
            except ShortCircuit as sc:
                return sc.result
        result = await fn(**kwargs)
        for mw in middlewares:
            result = mw.after_tool(name, kwargs, result)
        return result

    return replace(spec, fn=wrapped)
