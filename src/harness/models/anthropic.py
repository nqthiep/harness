"""The Anthropic provider — task T-0.4.

Round 30 found this file did not exist: the library ran entirely on FakeModel, so a real
cold start could not have worked.  The SDK is imported lazily inside the constructor so
`import harness` stays under 200 ms (NFR-01, IDL-07).

Nothing outside this file knows a vendor exception type; that is what keeps the provider
seam real (ADR-002).
"""
from __future__ import annotations

from typing import Any

from ..errors import (ProviderAuthError, ProviderBadRequest, ProviderError,
                      ProviderRateLimited, ProviderTimeout, ProviderUnavailable)
from ..result import Usage
from . import pricing
from .base import DeltaFn, ModelRequest, ModelResponse

#: Errors this adapter maps.  Anything unmapped becomes ProviderError, never a success.
_STATUS = {401: ProviderAuthError, 403: ProviderAuthError,
           400: ProviderBadRequest, 404: ProviderBadRequest,
           408: ProviderTimeout, 429: ProviderRateLimited}


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, *, api_key: str | None = None) -> None:
        try:
            import anthropic                       # lazy: NFR-01
        except ImportError as exc:                 # pragma: no cover
            raise ProviderError(
                "the anthropic package is not installed.\n\n"
                "  Run:  pip install harness\n\n"
                "  -> docs/15-first-agent.md"
            ) from exc
        self._sdk = anthropic
        self._client = anthropic.AsyncAnthropic(**({"api_key": api_key} if api_key else {}))
        self._counts: dict[str, int] = {}

    # -- protocol ---------------------------------------------------------
    def price(self, model: str): return pricing.price(model)
    def max_output(self, model: str) -> int: return pricing.MAX_OUTPUT.get(model, 8_000)

    async def count_input_tokens(self, request: ModelRequest) -> int:
        key = request.cache_key()                  # unhashable object; canonical bytes
        if key in self._counts:
            return self._counts[key]
        try:
            r = await self._client.messages.count_tokens(
                model=request.model, system=list(request.system),
                tools=list(request.tools), messages=list(request.messages))
            n = int(r.input_tokens)
        except Exception:
            # Never fail a run on a counting call.  Over-estimate from characters, which
            # is a hard upper bound on tokens (ADR-026), so the ceiling stays safe.
            n = sum(len(str(m)) for m in request.messages) + sum(
                len(str(s)) for s in request.system)
        self._counts[key] = n
        return n

    async def complete(self, request: ModelRequest, *,
                       on_delta: DeltaFn | None = None) -> ModelResponse:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "system": list(request.system),
            "messages": list(request.messages),
            "thinking": {"type": "adaptive"},          # never budget_tokens (400 on 4.7+)
            "output_config": {"effort": request.effort},
        }
        if request.tools:
            kwargs["tools"] = list(request.tools)
        if request.output_format:
            kwargs["output_config"]["format"] = dict(request.output_format)

        try:
            if request.stream or on_delta is not None:
                async with self._client.messages.stream(**kwargs) as stream:
                    if on_delta is not None:
                        async for text in stream.text_stream:
                            on_delta(text)
                    msg = await stream.get_final_message()
            else:
                msg = await self._client.messages.create(**kwargs)
        except Exception as exc:
            raise self._map(exc) from exc

        u = msg.usage
        return ModelResponse(
            content=tuple(b.model_dump() if hasattr(b, "model_dump") else dict(b)
                          for b in msg.content),
            stop_reason=msg.stop_reason or "end_turn",
            usage=Usage(getattr(u, "input_tokens", 0), getattr(u, "output_tokens", 0),
                        getattr(u, "cache_read_input_tokens", 0) or 0,
                        getattr(u, "cache_creation_input_tokens", 0) or 0),
            model=msg.model,
            raw_id=getattr(msg, "id", ""),
            stop_details=getattr(msg, "stop_details", None),
        )

    def _map(self, exc: Exception) -> ProviderError:
        """Vendor exception -> harness hierarchy.  A refusal is NOT an error: it arrives
        as HTTP 200 with stop_reason='refusal' and is handled by the loop (ADR-019)."""
        status = getattr(exc, "status_code", None)
        if status in _STATUS:
            return _STATUS[status](str(exc))
        if isinstance(status, int) and status >= 500:
            return ProviderUnavailable(str(exc))
        name = type(exc).__name__
        if "Timeout" in name or "Connection" in name:
            return ProviderTimeout(str(exc))
        return ProviderError(f"{name}: {exc}")
