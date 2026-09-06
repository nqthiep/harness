"""Stop reasons, the ceiling on pauses, and the `returns=` parser — the vocabulary BOTH
engines speak.

It lived in `run.py`, so `lg/runtime.py` did `from ..run import CONTINUE, _MAP,
parse_returns`: the durable backend importing the classic loop's module to borrow a table.
Not a cycle, but the same shape as the construction guards (ADR-098) — shared vocabulary
living inside one of its two consumers.

Moving it out also fixed a duplication that shape had already produced: **`MAX_PAUSES = 5`
was defined twice**, in `run.py` and again in `lg/graph.py`. `_classify`'s own docstring
says the stop-reason table is "imported rather than re-listed, because two copies of one
table is how the two backends drift" — and the constant next to it had been re-listed
anyway. One definition now, and `tests/test_parity.py` asserts both engines read it
(ADR-099).

And it is what brought `run.py` back under its 250-line ceiling. IDL-13 says an overrun
means splitting the file, never raising the cap; two `ERROR_RAISED` emits pushed it to
256, and this is the seam that was already there.
"""
from __future__ import annotations

import json

from .errors import ToolContractError
from .result import StopReason


#: Every provider stop reason maps to exactly one StopReason.  Unknown -> ERROR, never
#: to a success (ADR-019).  "tool_use" is absent because it continues the loop, and so is
#: "pause_turn" — see CONTINUE below.
_MAP = {"end_turn": StopReason.COMPLETED, "max_tokens": StopReason.TRUNCATED,
        "refusal": StopReason.MODEL_REFUSAL}

#: Stop reasons that mean "not finished, send it back".  `pause_turn` is what a server
#: tool (web search, web fetch) returns when the model pauses mid-turn; T-0.4 said
#: "surface pause_turn rather than swallowing it" and the code had never heard of it, so
#: it fell through to ERROR — the API says *resumable* and the harness said *dead* (Round 38).
CONTINUE = frozenset({"pause_turn"})

#: A model that pauses forever is a loop the budget would pay for.  Bounded, and the
#: bound is loud rather than silent.
MAX_PAUSES = 5

def parse_returns(want: type, text: str):
    """Turn a final answer into `Agent(returns=...)`, validated — ADR-022.

    Module-level (N-3) so `lg/runtime.py::finish()` can call the SAME parse the classic
    loop always has, rather than growing a second implementation that could drift.
    Round 33 found `returns=` reached the request and the response was never parsed, so
    `Result.value` was always None: the parameter was accepted and half-honoured. A
    response that does not fit is an error, never a silent None.
    """
    import dataclasses
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ToolContractError(
            f"this agent was asked for {want.__name__}, but the model replied with "
            f"text that is not {want.__name__}:\n\n    {text[:120]!r}\n\n"
            f"  ({exc})"
        ) from None
    if not (isinstance(want, type) and dataclasses.is_dataclass(want)):
        return data
    fields = {f.name for f in dataclasses.fields(want)}
    missing = sorted(f.name for f in dataclasses.fields(want)
                     if f.name not in data
                     and f.default is dataclasses.MISSING
                     and f.default_factory is dataclasses.MISSING)
    if missing:
        raise ToolContractError(
            f"the model's answer is missing {', '.join(missing)} for "
            f"{want.__name__}.\n\n  Got: {sorted(data)}"
        )
    return want(**{k: v for k, v in data.items() if k in fields})
