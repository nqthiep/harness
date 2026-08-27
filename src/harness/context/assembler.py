"""Deterministic prefix rendering — task T-2.2.

Renders tools -> system -> messages with canonical JSON everywhere.  Byte-stability
here is what makes prompt caching work, so every cost guarantee depends on it.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from ..models.base import ModelRequest
from ..tools.registry import ToolSet

MIN_CACHEABLE_TOKENS = 1024          # below this a breakpoint only pays the write premium


def canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


class ContextAssembler:
    __slots__ = ("_model", "_system_text", "_tools", "_effort", "_output_format")

    def __init__(self, *, model: str, job: str, tools: ToolSet, effort: str,
                 output_format: Mapping[str, Any] | None = None) -> None:
        self._model = model
        self._system_text = job
        self._tools = tools
        self._effort = effort
        self._output_format = output_format

    def render_prefix(self) -> str:
        """The bytes that must not change between calls.  Used by the cache linter."""
        return canonical({"model": self._model, "tools": self._tools.to_api(),
                          "system": self._system_blocks()})

    def _system_blocks(self) -> list[dict[str, Any]]:
        block: dict[str, Any] = {"type": "text", "text": self._system_text}
        # Breakpoint only when the prefix is worth caching (IDL-18).
        if len(self._system_text) + len(self._tools.canonical()) > MIN_CACHEABLE_TOKENS * 3:
            block["cache_control"] = {"type": "ephemeral"}
        return [block]

    def build(self, messages: Sequence[Mapping[str, Any]], *, max_tokens: int,
              stream: bool = False) -> ModelRequest:
        return ModelRequest(
            model=self._model,
            system=tuple(self._system_blocks()),
            tools=tuple(self._tools.to_api()),
            messages=tuple(messages),
            max_tokens=max_tokens,
            effort=self._effort,
            stream=stream,
            output_format=self._output_format,
        )
