"""Fixes a real, measured bug in the "long, exploratory, many trial-and-error" workflow:
`dispatch.py::truncate()` keeps only the HEAD of an over-long tool result and drops the
rest — correct for most tools (a file read, a search: the interesting part is usually
near the top) and actively harmful for a test run or a build log, where the actionable
part is usually at the TAIL.

**Measured, not assumed.** 500 parametrized passing tests plus one real failure, run
through this project's own `pytest -v`, produces 41,085 characters. `truncate()`'s
default ceiling (`ToolSpec.max_result_tokens=4_000`, `dispatch.py: limit = max_tokens *
4` = 16,000 chars) keeps chars `[0:16000]` — checked directly against that output: the
`FAILURES` section is NOT in the kept portion, and neither is the failing assertion's
own message. The model would see 195 lines of `PASSED` and never see why the run
failed. `coding_profile.py`'s own prompt tells the agent to "read the actual failure
before changing anything" — this bug makes that impossible past a few hundred tests.

**Why this is a profile-layer fix, not a `dispatch.py` change.** `truncate()` is shared
by every tool in the library, and head-only IS the right default for most of them (a
`read_source` call, a `search_code` hit list). The fix belongs specifically to tools
whose output shape puts the payoff at the end — `run_tests`, `git_diff`, and the shell
tools' arbitrary build/test commands — so it is scoped to those, not applied globally.

    tools = with_smart_truncation(code.tools(), tools=("run_tests", "git_diff"))

`smart_truncate()` keeps BOTH ends and drops the middle, because either end can matter:
a build tool's fatal error is often near the TOP (a syntax error stops everything else
from running), while a test run's failure summary is usually at the BOTTOM. Head-only or
tail-only both bet on one shape; this doesn't have to bet.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from typing import Any, Final, Sequence

sys.path.insert(0, "src")

#: Matches `shell_tools.SHELL_MAX_RESULT_TOKENS` — the same class of output (a build/
#: test log), so the same budget.
SMART_MAX_RESULT_TOKENS: Final = 12_000
#: How much of the kept budget goes to the tail vs. the head. Slightly tail-heavy: a
#: test run's actionable summary is conventionally at the end (pytest, cargo test, go
#: test, jest all print the failure roll-up last), so when something has to give, keep
#: more of the end.
TAIL_FRACTION: Final = 0.6


def smart_truncate(text: str, max_tokens: int, *,
                   tail_fraction: float = TAIL_FRACTION) -> str:
    """Keep the head AND the tail, drop the middle — unlike `dispatch.py::truncate()`,
    which keeps only the head. Same char-per-token approximation
    (`max_tokens * 4`) and the same UTF-8-safe boundary trim `truncate()` uses (P-7:
    never split a multi-byte character), applied at both cut points instead of one.
    """
    limit = max_tokens * 4
    if len(text) <= limit:
        return text
    tail_chars = int(limit * tail_fraction)
    head_chars = max(0, limit - tail_chars)
    head, tail = _safe_head(text, head_chars), _safe_tail(text, tail_chars)
    omitted = len(text) - len(head) - len(tail)
    return (f"{head}\n\n[... {omitted} chars omitted from the middle — see "
           f"tail below for what usually matters (a failure summary, an exit "
           f"status) ...]\n\n{tail}")


def _safe_head(text: str, n: int) -> str:
    cut = text[:n]
    while cut and cut.encode("utf-8", "ignore").decode("utf-8", "ignore") != cut:
        cut = cut[:-1]
    return cut


def _safe_tail(text: str, n: int) -> str:
    cut = text[len(text) - n:] if n else ""
    while cut and cut.encode("utf-8", "ignore").decode("utf-8", "ignore") != cut:
        cut = cut[1:]
    return cut


def with_smart_truncation(specs: Sequence[Any], *, tools: Sequence[str],
                          max_tokens: int = SMART_MAX_RESULT_TOKENS,
                          tail_fraction: float = TAIL_FRACTION) -> list[Any]:
    """Return `specs` with the named tools' results run through `smart_truncate()` at
    `max_tokens`, and their own `max_result_tokens` raised to match — so
    `dispatch.py::truncate()`'s own pass (which still runs afterward; this does not
    disable it) sees output already under its ceiling and passes it through unchanged,
    rather than re-truncating what was just carefully shaped.

    Same composition move `coding_profile.py::with_verification` already makes:
    `dataclasses.replace(spec, fn=...)` on a frozen `ToolSpec` — the effect
    classification, timeout, and every other field are untouched, so this cannot change
    what the harness already knows and enforces about the tool.
    """
    wanted = frozenset(tools)
    out = []
    for spec in specs:
        if spec.name not in wanted:
            out.append(spec)
            continue
        inner = spec.fn

        async def shaped(_inner: Any = inner, **kwargs: Any) -> Any:
            result = await _inner(**kwargs)
            return smart_truncate(str(result), max_tokens, tail_fraction=tail_fraction)

        out.append(replace(spec, fn=shaped, max_result_tokens=max_tokens))
    return out
