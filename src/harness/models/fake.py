"""FakeModel — task T-0.7.  Scripted responses, zero price, records every request."""
from __future__ import annotations

from typing import Any, Sequence

from ..result import Usage
from . import pricing
from .base import DeltaFn, ModelRequest, ModelResponse


class FakeModel:
    name = "fake"

    def __init__(self, script: Sequence[ModelResponse], *, input_tokens: int = 100) -> None:
        self._script = list(script)
        self._i = 0
        self._input_tokens = input_tokens
        self.calls: list[ModelRequest] = []

    # -- scripting helpers ------------------------------------------------
    @staticmethod
    def text(s: str, *, output_tokens: int = 20, stop: str = "end_turn") -> ModelResponse:
        return ModelResponse(({"type": "text", "text": s},), stop,
                             Usage(100, output_tokens), "fake")

    @staticmethod
    def tool_call(name: str, args: dict[str, Any], *, call_id: str = "c1") -> ModelResponse:
        return ModelResponse(
            ({"type": "tool_use", "id": call_id, "name": name, "input": args},),
            "tool_use", Usage(100, 15), "fake")

    # -- provider protocol ------------------------------------------------
    async def complete(self, request: ModelRequest, *, on_delta: DeltaFn | None = None) -> ModelResponse:
        self.calls.append(request)
        if self._i >= len(self._script):
            return self.text("(script exhausted)")
        r = self._script[self._i]
        self._i += 1
        if on_delta is not None:
            for block in r.content:
                if block.get("type") == "text":
                    for word in str(block["text"]).split(" "):
                        on_delta(word + " ")
        return r

    def price(self, model: str): return pricing.price("fake")
    async def count_input_tokens(self, request: ModelRequest) -> int: return self._input_tokens
    def max_output(self, model: str) -> int: return 8_000
