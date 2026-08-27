"""A scripted LangChain chat model — no network, no key."""
from __future__ import annotations

from typing import Any, Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult


class FakeChat(BaseChatModel):
    script: list = []
    i: int = 0
    seen: list = []
    input_tokens: int = 100
    output_tokens: int = 50

    @property
    def _llm_type(self) -> str: return "fake"

    def bind_tools(self, tools, **kw): return self

    def _generate(self, messages, stop=None, run_manager=None, **kw) -> ChatResult:
        object.__setattr__(self, "seen", list(self.seen) + [list(messages)])
        i = self.i
        object.__setattr__(self, "i", i + 1)
        msg = self.script[i] if i < len(self.script) else AIMessage(content="(hết kịch bản)")
        msg = AIMessage(content=msg.content, tool_calls=list(getattr(msg, "tool_calls", []) or []),
                        usage_metadata={"input_tokens": self.input_tokens,
                                        "output_tokens": self.output_tokens,
                                        "total_tokens": self.input_tokens + self.output_tokens})
        return ChatResult(generations=[ChatGeneration(message=msg)])

    @staticmethod
    def text(s: str) -> AIMessage: return AIMessage(content=s)

    @staticmethod
    def call(name: str, args: dict, cid: str = "c1") -> AIMessage:
        return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": cid}])
