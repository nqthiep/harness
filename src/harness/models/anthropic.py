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

    #: Server-side refusal fallbacks.  A routine safety decline (HTTP 200,
    #: `stop_reason: "refusal"`) is re-run on a fallback model inside the same call,
    #: instead of surfacing as a dead end.  IDL-19 has said "enabled by default" since
    #: Round 7; Round 38 found the payload had never carried it — the claim stood in
    #: three documents and nothing in the code.
    #:
    #: `"default"` routes by refusal category, so there is no model list to maintain.
    #: It requires the beta messages endpoint and this exact beta id; the two must match
    #: (the older array form pairs with `-2026-06-01` and mixing them is a 400).
    FALLBACK_BETA = "server-side-fallback-2026-07-01"

    def __init__(self, *, api_key: str | None = None, fallbacks: bool = True) -> None:
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
        # Caught here rather than on the first request. Without this, a provider with no
        # resolvable credential constructs happily and dies mid-run as
        # `ProviderError: TypeError: "Could not resolve authentication method..."` — an
        # SDK internal, raised after the budget has already reserved, and NOT a
        # `ProviderAuthError`, so a caller catching the documented exception for "bad
        # credentials" misses it. Measured on the `.env` path (ADR-086). Note the SDK
        # accepts a short or malformed key without complaint — only the ABSENCE of one
        # is decidable locally; `"nope"` reaches the server and comes back 401.
        if self._client.api_key is None and getattr(self._client, "auth_token", None) is None:
            raise ProviderAuthError(
                "no Anthropic credential is configured.\n\n"
                "  Run:  harness setup\n"
                "  or:   export ANTHROPIC_API_KEY=sk-ant-...\n"
                "  or:   AnthropicProvider(api_key=...)\n\n"
                "  -> docs/15-first-agent.md"
            )
        self._counts: dict[str, int] = {}
        self._fallbacks = fallbacks

    async def acheck_credentials(self) -> tuple[bool, str]:
        """Is this credential accepted? `(True, "")` or `(False, why)`.

        `count_tokens` rather than a one-token `messages.create`: it authenticates
        against the same key and bills nothing, so validating a key before storing it
        (IDL-25) does not cost the user money to find out their key works.

        Lives here, not in the CLI, because mapping a vendor exception is exactly what
        ADR-002 keeps inside this file — the caller gets `(bool, str)` and never sees an
        `anthropic.*` type. Verified against the live endpoint with a dead key
        (`tests/live_probe.py`), which is the only half of it a probe without a funded
        key can reach.
        """
        try:
            await self._client.messages.count_tokens(
                model="claude-opus-4-5", messages=[{"role": "user", "content": "ok"}])
        except Exception as exc:
            return False, str(self._map(exc))
        return True, ""

    def check_credentials(self) -> tuple[bool, str]:
        """`acheck_credentials` for a sync caller (the `harness setup` command)."""
        import asyncio
        return asyncio.run(self.acheck_credentials())

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

        # The beta endpoint carries the fallbacks parameter; everything else is identical.
        api = self._client.messages
        if self._fallbacks:
            kwargs["betas"] = [self.FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
            api = self._client.beta.messages

        try:
            if request.stream or on_delta is not None:
                async with api.stream(**kwargs) as stream:
                    if on_delta is not None:
                        async for text in stream.text_stream:
                            on_delta(text)
                    msg = await stream.get_final_message()
            else:
                msg = await api.create(**kwargs)
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
        retry_after = _retry_after(exc)
        if status in _STATUS:
            return _STATUS[status](str(exc), retry_after_s=retry_after)
        if isinstance(status, int) and status >= 500:
            return ProviderUnavailable(str(exc), retry_after_s=retry_after)
        name = type(exc).__name__
        if "Timeout" in name or "Connection" in name:
            return ProviderTimeout(str(exc), retry_after_s=retry_after)
        return ProviderError(f"{name}: {exc}", retry_after_s=retry_after)


def _retry_after(exc: Exception) -> float | None:
    """N-5 — a real `Retry-After` header, when the vendor sent one. `APIStatusError`
    (every mapped exception above) carries the raw `httpx.Response`; anything else (a
    connection error with no response at all) has none, and that's `None`, not a guess.
    """
    response = getattr(exc, "response", None)
    value = getattr(response, "headers", {}).get("retry-after") if response else None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None                # a non-numeric Retry-After (an HTTP-date) — rare
                                    # enough, and safe enough to fall back to our own
                                    # backoff, that parsing it isn't worth the risk of
                                    # getting a date-math bug wrong under retry.py itself
