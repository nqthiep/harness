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
MAX_BREAKPOINTS = 4                  # provider limit
_CHARS_PER_TOKEN = 4                 # rough, and only used to decide "is this worth caching"


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
        # Breakpoint only when the prefix is worth caching (IDL-18): below the minimum
        # cacheable size a marker pays the write premium and never reads.
        prefix_chars = len(self._system_text) + len(self._tools.canonical())
        if prefix_chars // _CHARS_PER_TOKEN >= MIN_CACHEABLE_TOKENS:
            block["cache_control"] = {"type": "ephemeral"}
        return [block]

    def _mark_last_turn(self, messages: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
        """Breakpoint on the last content block of the most recent turn.

        Without this only the system+tools prefix is cached, so the whole conversation is
        re-billed every turn.  Measured on the 10-turn fixture: 71.8% hit rate degrading
        to 61.7% by turn 10, against SC-4's 90% floor (Round 26).  Earlier breakpoints
        stay valid, so hits accrue as the conversation grows.
        """
        if not messages:
            return tuple(messages)
        prefix_chars = len(self._system_text) + len(self._tools.canonical())
        prefix_chars += sum(len(canonical(m)) for m in messages)
        if prefix_chars // _CHARS_PER_TOKEN < MIN_CACHEABLE_TOKENS:
            return tuple(messages)          # nothing worth caching yet (IDL-18)

        # Mark the current last message AND the position the previous request marked.
        # A cache entry is only READ at a breakpoint present in the current request, so
        # marking only the newest message writes an entry nothing ever reads back
        # (Round 26).  Two message breakpoints plus the system one stays inside the
        # provider's limit of four.
        marks = {len(messages) - 1}
        if len(messages) >= 3:
            marks.add(len(messages) - 3)

        out: list[Mapping[str, Any]] = []
        for i, msg in enumerate(messages):
            if i not in marks:
                out.append(msg)
                continue
            m = dict(msg)
            content = m.get("content")
            blocks = ([dict(b) for b in content] if isinstance(content, list)
                      else [{"type": "text", "text": content}])
            if not blocks:
                out.append(msg)
                continue
            blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
            m["content"] = blocks
            out.append(m)
        return tuple(out)

    def build(self, messages: Sequence[Mapping[str, Any]], *, max_tokens: int,
              stream: bool = False) -> ModelRequest:
        return ModelRequest(
            model=self._model,
            system=tuple(self._system_blocks()),
            tools=tuple(self._tools.to_api()),
            messages=self._mark_last_turn(messages),
            max_tokens=max_tokens,
            effort=self._effort,
            stream=stream,
            output_format=self._output_format,
        )
