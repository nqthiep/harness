"""Context growth policy — task T-2.6.

Editing first, then compaction. Editing is free and lossless for recent work: it blanks
the CONTENT of old tool results and keeps every message, so the `tool_use`/`tool_result`
pairing stays intact (invariant I-3).

**What "compaction" means here, and what it deliberately is not.** For two milestones
`manage()` returned `"compact_needed"` and no caller did anything with it but emit an
event — so a long run edited until there was nothing left to blank, then walked into the
provider's context limit and had the request rejected. The fix is to DROP the oldest
whole steps, not to summarize them:

* Summarizing costs a model call, out of the same budget the caller set as a ceiling, for
  a gain nobody has measured — the exact trade ADR-023 refused.
* Worse, a summary is model output derived from tool results that may be UNTRUSTED. It
  would have to carry the `join` of every label it summarizes or compaction becomes a
  perfect taint-laundering path (the S-19 class). That is a real design, not a helper.
* What actually gets dropped is the model's own earlier reasoning and the record of tools
  it already called. Since `harness.tasks.TaskLedger` keeps the plan in a `Store` rather
  than in the transcript, that is survivable: one cheap `list_tasks` call rebuilds it.

A step is dropped as a WHOLE — the assistant turn together with the user message carrying
its `tool_result` blocks — because dropping half a pair is the I-3 violation the editing
path exists to avoid. The first user message (the task itself) is never dropped.

Whether the budget or the window binds first depends on the model
([§07.3](../../../docs/07-cost.md)), so fixtures are specified per model.
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
    """Return (messages, action) where action is 'none' | 'edited' | 'compacted' |
    'compact_needed'.

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

    # Compaction is driven by the RATIO, never by "editing found nothing left to blank".
    # The first version got this wrong and the bug is worth keeping in view: every step
    # makes exactly one more tool result stale, so editing ALWAYS has something to clear,
    # so compaction would never have run at all — while the window kept growing anyway,
    # because a blanked result still costs its envelope and the assistant turns holding
    # the `tool_use` blocks are never blanked. At 80% of the window, blanking one more
    # old result is not a plan.
    if ratio >= COMPACT_AT:
        compacted, dropped = compact(out)
        if dropped:
            return compacted, "compacted"
    if cleared:
        return out, "edited"
    if ratio < COMPACT_AT:
        return list(messages), "none"
    # Nothing left to blank and nothing left to drop. Saying so is the whole value: the
    # caller stops with a reason a person can act on, instead of the provider rejecting
    # the next request for a reason it has to guess at (IDL-30).
    return list(messages), "compact_needed"


def _is_tool_result_message(m: Mapping[str, Any]) -> bool:
    c = m.get("content")
    return isinstance(c, list) and any(_is_tool_result(b) for b in c)


def compact(messages: Sequence[Mapping[str, Any]], *,
            keep_recent_steps: int = KEEP_RECENT_STEPS,
            ) -> tuple[list[Mapping[str, Any]], int]:
    """Drop the oldest whole steps. Returns `(messages, n_messages_dropped)`.

    Always keeps `messages[0]` — the original task. A "step" is an assistant message
    followed by the user message holding its `tool_result` blocks; both go or neither
    does. An assistant message with no tool results after it (the final answer, a plain
    text turn) is its own group and is dropped the same way.

    **No "[n steps were dropped]" marker is inserted.** It would have to be a message of
    some role: a second consecutive `user` message right after the task is a shape not
    every provider accepts, and editing the task itself would invalidate the cached
    prefix — the single largest cost lever in the system. The drop is recorded in the
    `context.managed` event instead, which is where a person looks for it anyway.
    """
    if len(messages) <= 1:
        return list(messages), 0
    head, rest = messages[0], list(messages[1:])
    groups: list[list[Mapping[str, Any]]] = []
    i = 0
    while i < len(rest):
        group = [rest[i]]
        if (rest[i].get("role") == "assistant" and i + 1 < len(rest)
                and _is_tool_result_message(rest[i + 1])):
            group.append(rest[i + 1])
            i += 1
        groups.append(group)
        i += 1
    if len(groups) <= keep_recent_steps:
        return list(messages), 0
    kept = groups[-keep_recent_steps:] if keep_recent_steps > 0 else []
    dropped = sum(len(g) for g in groups[:len(groups) - len(kept)])
    return [head, *[m for g in kept for m in g]], dropped
