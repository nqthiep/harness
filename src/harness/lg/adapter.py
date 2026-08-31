"""`Agent(durable=True)` — the LangGraph backend behind the classic backend's own model
seam, task "unify the two APIs" (see the AskUserQuestion decision this closes).

`build_agent()` (`lg/__init__.py`) needs a `langchain_core.BaseChatModel`.  Every example
that wires one up to a REAL model reaches for `langchain-anthropic` — a second client
library, with its own error mapping, calling the vendor API a second, independent way.
That is fine for the escape hatch (a power user chose it), but wrong for `durable=True`,
whose whole point is that it is NOT a second thing to learn: the same `provider=` an
`Agent` already takes (`AnthropicProvider`, `FakeModel`, `PricedFake`, ...) is wrapped
here as a `BaseChatModel`, so a durable run calls the model through the *identical*
seam — same error hierarchy (docs/10-observability-ops.md §3), same pricing table, same
`ContextAssembler` (so `job=`/tools/`effort=` reach the model exactly as they do on the
classic backend, closing a gap `build_agent()` has on its own: nothing before this file
ever gave the graph backend a system prompt at all).
"""
from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from ..models.base import ModelResponse


class ProviderChatModel(BaseChatModel):
    """A harness `ModelProvider` (async `.complete()`), worn as a LangChain chat model.

    `bind_tools()` is a no-op that returns `self`: the tool schema `build_agent()` would
    bind here is the same one `asm` (this Agent's own `ContextAssembler`) already carries
    — binding it a second time would just be a second place for the two to drift.
    """

    model_config = {"arbitrary_types_allowed": True}

    provider: Any
    asm: Any
    max_output: int

    @property
    def _llm_type(self) -> str:
        return "harness-provider"

    def bind_tools(self, tools: Any, **kw: Any) -> "ProviderChatModel":
        return self

    def _generate(self, messages: Any, stop: Any = None, run_manager: Any = None,
                  **kw: Any) -> ChatResult:
        native = _lc_to_native(messages)
        max_tokens = kw.get("max_tokens") or self.max_output
        req = self.asm.build(native, max_tokens=max_tokens, stream=False)
        resp = asyncio.run(self.provider.complete(req))
        return ChatResult(generations=[ChatGeneration(message=_to_aimessage(resp))])


def _lc_to_native(messages: Any) -> list[dict]:
    """LangChain message objects -> the native `{"role", "content"}` dicts every other
    part of the harness already speaks (`ModelRequest.messages`, `Result.messages`).

    Adjacent same-role turns are merged (I-4: "one message" per batch of tool results) —
    the graph appends one `ToolMessage` per tool call, where the classic loop appends one
    *message* per step; sending them unmerged is multiple consecutive `user` turns, which
    is not the conversation shape either backend's own history ever produces.
    """
    out: list[dict] = []
    for m in messages:
        if isinstance(m, SystemMessage):
            continue          # the system prompt comes from `asm`, never from history —
                              # putting it in state too would be a second copy to drift.
        elif isinstance(m, HumanMessage):
            role, content = "user", m.content
        elif isinstance(m, ToolMessage):
            role = "user"
            content = [{"type": "tool_result", "tool_use_id": m.tool_call_id,
                       "content": m.content if isinstance(m.content, str) else str(m.content),
                       "is_error": getattr(m, "status", "success") == "error"}]
        elif isinstance(m, AIMessage):
            blocks: list[dict] = []
            if m.content:
                blocks.append({"type": "text",
                               "text": m.content if isinstance(m.content, str) else str(m.content)})
            for tc in (m.tool_calls or []):
                blocks.append({"type": "tool_use", "id": tc.get("id"), "name": tc.get("name"),
                               "input": tc.get("args") or {}})
            if not blocks:
                continue      # an empty assistant turn is never valid on the wire
            role, content = "assistant", blocks
        else:
            continue
        if out and out[-1]["role"] == role:
            prev = out[-1]["content"]
            prev_list = prev if isinstance(prev, list) else [{"type": "text", "text": prev}]
            cur_list = content if isinstance(content, list) else [{"type": "text", "text": content}]
            out[-1]["content"] = prev_list + cur_list
        else:
            out.append({"role": role, "content": content})
    return out


def _to_aimessage(resp: ModelResponse) -> AIMessage:
    text = "".join(b.get("text", "") for b in resp.content if b.get("type") == "text")
    tool_calls = [{"name": b["name"], "args": b.get("input") or {}, "id": b.get("id", "")}
                 for b in resp.content if b.get("type") == "tool_use"]
    u = resp.usage
    return AIMessage(
        content=text, tool_calls=tool_calls,
        response_metadata={"stop_reason": resp.stop_reason},
        usage_metadata={"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                        "total_tokens": u.input_tokens + u.output_tokens,
                        "input_token_details": {"cache_read": u.cache_read_input_tokens,
                                                "cache_creation": u.cache_creation_input_tokens}},
    )
