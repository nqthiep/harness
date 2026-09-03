"""`DeepSeekProvider` — a second `ModelProvider`, written outside `src/harness/`, to
actually run this repository's coding-agent example against a REAL model.

Why this file exists: every claim this repository makes about how a real agent behaves
has an open item attached — OI-11, `design/07-risks-and-open-issues.md` — because
`AnthropicProvider` has never been run against a live API in this project's own history.
`docs/02-architecture.md §4` lists `ModelProvider` as a seam with "two genuinely
different implementations *today*" as its evidence (Anthropic / Bedrock / Vertex);
`examples/proof.py` already demonstrates a second one can be written with no inheritance,
protocol only. This is that seam used for its actual purpose: the user testing this
harness has a DeepSeek key, not an Anthropic one, and `ModelProvider` is precisely the
extension point that should not care.

**What has to be translated, and why it's mechanical rather than hard.** `ModelRequest`/
`ModelResponse` (`models/base.py`) and every message the loop builds
(`run.py:57,153,178`) are shaped for Anthropic's Messages API — `content` is a list of
typed blocks (`text`/`tool_use`/`tool_result`), not the OpenAI-compatible
`tool_calls`/`role:"tool"` shape DeepSeek's `/chat/completions` endpoint speaks. Nothing
about the AGENT LOOP cares which shape a provider speaks on the wire — `run.py`/
`dispatch.py` only ever construct and read the Anthropic-shaped `ModelRequest`/
`ModelResponse`, per the `ModelProvider` protocol. So the entire adapter is: translate
inbound to OpenAI's shape, call the API, translate outbound back. `_to_openai_messages`/
`_from_openai_response` below are that translation, and nothing else in this file (or
anywhere else) needs to change for the loop to run unmodified.

**What is knowingly approximate, said plainly rather than silently:**

* `count_input_tokens` — DeepSeek has no dedicated counting endpoint the way Anthropic's
  `messages.count_tokens` is. Falls back to the same character-count over-estimate
  `AnthropicProvider` itself uses when ITS real count fails (ADR-026: a hard upper bound
  on tokens, safe for a budget CEILING even though it is not exact).
* `MAX_CONTEXT`/pricing — `run.py:282` reads `models.pricing.MAX_CONTEXT` directly (a
  core-level global, not something a `ModelProvider` supplies), so a model name this
  table doesn't know falls back to its own 200k default rather than DeepSeek's real
  context window. Harmless here (it only changes WHEN compaction starts, not whether the
  run is correct) but worth knowing before trusting the ratio for a much longer session.
* The prices below are **[Unverified against a live source in this session]** — entered
  from general knowledge of DeepSeek's public pricing, not fetched fresh. They make the
  budget ceiling exercise real arithmetic instead of reporting `$0.0000` for every call;
  they are not something to bill a customer against. Check
  https://api-docs.deepseek.com/quick_start/pricing before relying on the number.

Usage:

    export DEEPSEEK_API_KEY=sk-...
    python3 examples/deepseek_probe.py          # one real call, proves the wiring works
"""
from __future__ import annotations

import json
import os
from typing import Any, Mapping

import httpx

from harness.errors import (ProviderAuthError, ProviderBadRequest, ProviderError,
                            ProviderRateLimited, ProviderTimeout, ProviderUnavailable)
from harness.models.base import DeltaFn, ModelRequest, ModelResponse
from harness.models.pricing import Price
from harness.result import Usage

DEFAULT_BASE_URL = "https://api.deepseek.com"

#: [Unverified against a live source in this session] — see module docstring §"knowingly
#: approximate". Good enough to make the budget ceiling do real arithmetic; not a
#: billing source.
def _price_table() -> dict[str, Price]:
    from decimal import Decimal
    return {
        "deepseek-chat": Price(Decimal("0.27"), Decimal("1.10"),
                               Decimal("0.27"), Decimal("0.07")),
        "deepseek-reasoner": Price(Decimal("0.55"), Decimal("2.19"),
                                   Decimal("0.55"), Decimal("0.14")),
    }


_STATUS = {401: ProviderAuthError, 403: ProviderAuthError,
          400: ProviderBadRequest, 404: ProviderBadRequest,
          408: ProviderTimeout, 429: ProviderRateLimited}

#: DeepSeek's `finish_reason` -> the vocabulary `run.py::_MAP` (Anthropic's own stop
#: reasons) already knows. Anything not listed here passes through UNCHANGED, which
#: `run.py` turns into `StopReason.ERROR: unknown stop reason ...` rather than guessing
#: — the same fail-visible discipline (IDL-30) the rest of this library uses throughout,
#: not a gap special to this adapter.
_FINISH_REASON = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens",
                  "content_filter": "refusal"}


def _system_text(system: tuple[Any, ...]) -> str:
    return "\n\n".join(b.get("text", "") for b in system if b.get("type") == "text")


def _to_openai_messages(system: tuple[Any, ...],
                        messages: tuple[Any, ...]) -> list[dict[str, Any]]:
    """Anthropic-shaped `ModelRequest.messages` -> OpenAI-compatible chat messages.

    Three shapes reach here, and only three (`run.py:57,153,178` are the only three
    places any message is ever constructed): a plain-string user turn (the task, or a
    tool's own follow-up text — rare), an assistant turn whose `content` is a list of
    `text`/`tool_use` blocks, and a user turn whose `content` is a list of `tool_result`
    blocks (I-3/I-4: one message per step, one block per call that step made).
    """
    out: list[dict[str, Any]] = []
    sys_text = _system_text(system)
    if sys_text:
        out.append({"role": "system", "content": sys_text})

    for m in messages:
        role, content = m["role"], m.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        blocks = list(content or [])
        if role == "assistant":
            text = "\n".join(b["text"] for b in blocks if b.get("type") == "text")
            calls = [b for b in blocks if b.get("type") == "tool_use"]
            msg: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = [
                    {"id": b["id"], "type": "function",
                     "function": {"name": b["name"], "arguments": json.dumps(b["input"])}}
                    for b in calls
                ]
            out.append(msg)
            continue

        # role == "user": tool_result blocks become one "tool" message EACH — OpenAI's
        # shape has no "several results in one turn" grouping the way Anthropic's does.
        results = [b for b in blocks if b.get("type") == "tool_result"]
        if results:
            for r in results:
                out.append({"role": "tool", "tool_call_id": r["tool_use_id"],
                           "content": str(r.get("content", ""))})
            continue

        text = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        out.append({"role": "user", "content": text})
    return out


def _to_openai_tools(tools: tuple[Any, ...]) -> list[dict[str, Any]]:
    """`ToolSpec.to_api()`'s `{name, description, input_schema, strict}` (Anthropic
    shape, `tools/__init__.py`) -> OpenAI's `{type: function, function: {...}}`. The
    JSON Schema in `input_schema` is passed through unchanged as `parameters` — both
    vendors consume plain JSON Schema, so nothing in the schema itself needs translating,
    only the envelope around it.
    """
    return [{"type": "function",
            "function": {"name": t["name"], "description": t["description"],
                        "parameters": t["input_schema"]}}
           for t in tools]


def _from_openai_response(data: Mapping[str, Any], model: str) -> ModelResponse:
    choice = data["choices"][0]
    msg = choice["message"]
    content: list[dict[str, Any]] = []
    if msg.get("content"):
        content.append({"type": "text", "text": msg["content"]})
    for call in msg.get("tool_calls") or []:
        raw_args = call["function"].get("arguments") or "{}"
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            # Malformed JSON from the model itself — not this adapter's to fix by
            # guessing. The tool's own argument validation reports it, fail-visible
            # (IDL-30), the same as any other badly-shaped tool call.
            args = {"_malformed_arguments": raw_args}
        content.append({"type": "tool_use", "id": call["id"],
                        "name": call["function"]["name"], "input": args})

    u = data.get("usage") or {}
    finish = choice.get("finish_reason") or "stop"
    return ModelResponse(
        content=tuple(content),
        stop_reason=_FINISH_REASON.get(finish, finish),
        usage=Usage(
            input_tokens=int(u.get("prompt_tokens", 0)),
            output_tokens=int(u.get("completion_tokens", 0)),
            # DeepSeek's own prompt-caching fields (disk-based context cache) — read
            # defensively; absent on an account/plan without it, not an error.
            cache_read_input_tokens=int(u.get("prompt_cache_hit_tokens", 0) or 0),
            cache_creation_input_tokens=0,
        ),
        model=data.get("model", model),
        raw_id=data.get("id", ""),
    )


class DeepSeekProvider:
    """`ModelProvider` (`harness.models.base`) speaking DeepSeek's OpenAI-compatible
    `/chat/completions` endpoint. No inheritance — this satisfies the `Protocol`
    structurally, the same way `examples/proof.py`'s own second provider does.

    **You own closing this — `ModelProvider` has no lifecycle hook.** Checked directly
    against `models/base.py`: the `Protocol` declares `complete`/`price`/
    `count_input_tokens`/`max_output` and nothing else — no `close`, no `__del__` call,
    no `Agent`/`RunEngine` code path that would call one if it existed. `httpx.
    AsyncClient` holds a real connection pool, so a `DeepSeekProvider()` an `Agent` is
    handed and never explicitly closed leaks that pool for the life of the process —
    real for a short script that exits anyway, real for a long-running service that
    creates one per request and does not. Two ways to close it correctly:

        async with DeepSeekProvider() as provider:      # preferred — closes on exit,
            agent = Agent(..., provider=provider)        # including when run() raises
            agent.run("...")

        provider = DeepSeekProvider()                     # or, when an async-with block
        try:                                               # doesn't fit the caller's
            ...                                            # shape (e.g. a provider that
        finally:                                            # outlives one Agent call)
            await provider.aclose()
    """
    name = "deepseek"

    def __init__(self, *, api_key: str | None = None, base_url: str = DEFAULT_BASE_URL,
                timeout_s: float = 120.0) -> None:
        key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise ProviderError(
                "no DeepSeek API key found.\n\n"
                "  Set it:  export DEEPSEEK_API_KEY=sk-...\n"
                "  or pass it directly:  DeepSeekProvider(api_key=\"sk-...\")"
            )
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=timeout_s,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        self._prices = _price_table()

    # -- protocol -----------------------------------------------------------
    def price(self, model: str) -> Price:
        return self._prices.get(model, self._prices["deepseek-chat"])

    def max_output(self, model: str) -> int:
        return 8_000                      # DeepSeek's documented per-request output cap

    async def count_input_tokens(self, request: ModelRequest) -> int:
        # No first-party counting endpoint — character-count upper bound (ADR-026's
        # fallback, applied here as the PRIMARY method rather than a fallback for one).
        return (sum(len(str(m)) for m in request.messages)
               + len(_system_text(request.system)))

    async def complete(self, request: ModelRequest, *,
                       on_delta: DeltaFn | None = None) -> ModelResponse:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": _to_openai_messages(request.system, request.messages),
            "max_tokens": request.max_tokens,
        }
        if request.tools:
            body["tools"] = _to_openai_tools(request.tools)

        try:
            resp = await self._client.post("/chat/completions", json=body)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise self._map(exc.response.status_code, exc) from exc
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"{type(exc).__name__}: {exc}") from exc

        return _from_openai_response(resp.json(), request.model)

    def _map(self, status: int, exc: Exception) -> ProviderError:
        retry_after = None
        ra = exc.response.headers.get("retry-after") if hasattr(exc, "response") else None
        if ra is not None:
            try:
                retry_after = float(ra)
            except ValueError:
                retry_after = None
        if status in _STATUS:
            return _STATUS[status](str(exc), retry_after_s=retry_after)
        if status >= 500:
            return ProviderUnavailable(str(exc), retry_after_s=retry_after)
        return ProviderError(f"HTTP {status}: {exc}", retry_after_s=retry_after)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "DeepSeekProvider":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
