"""Context growth policy — task T-2.6.

Editing first, then compaction: editing is free and lossless for recent work; compaction
costs a summarization pass.  Whether the budget or the window binds first depends on the
model ([§07.3](../../../docs/07-cost.md)), so fixtures are specified per model.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

EDIT_AT = 0.60                # fraction of the context window
COMPACT_AT = 0.80
KEEP_RECENT_STEPS = 3         # never clear the most recent work
CLEARED = "[earlier tool result cleared to save context]"


def _is_tool_result(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") == "tool_result"


def manage(messages: Sequence[Mapping[str, Any]], *, used_tokens: int,
           context_window: int) -> tuple[list[Mapping[str, Any]], str]:
    """Return (messages, action) where action is 'none' | 'edited' | 'compact_needed'.

    Editing clears the CONTENT of old tool results, never the messages themselves: a
    tool_use without its matching tool_result is a protocol violation (invariant I-3).
    """
    ratio = used_tokens / context_window if context_window else 0.0
    if ratio < EDIT_AT:
        return list(messages), "none"

    # index of the last tool_result to protect
    result_positions = [i for i, m in enumerate(messages)
                        if isinstance(m.get("content"), list)
                        and any(_is_tool_result(b) for b in m["content"])]
    protected = set(result_positions[-KEEP_RECENT_STEPS:]) if result_positions else set()

    out: list[Mapping[str, Any]] = []
    cleared = 0
    for i, m in enumerate(messages):
        content = m.get("content")
        if i in protected or not isinstance(content, list):
            out.append(m)
            continue
        blocks = []
        for b in content:
            if _is_tool_result(b) and b.get("content") != CLEARED:
                blocks.append({**b, "content": CLEARED})
                cleared += 1
            else:
                blocks.append(b)
        out.append({**m, "content": blocks})

    if cleared:
        return out, "edited"
    return list(messages), ("compact_needed" if ratio >= COMPACT_AT else "none")
