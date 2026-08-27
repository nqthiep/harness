"""@tool, Effect, ToolSpec — docs/04-interfaces.md §1, tasks T-0.2 and T-1.1."""
from __future__ import annotations

from .._value import value

import asyncio
import difflib
import functools
import inspect
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Final, Mapping

from ..errors import MissingEffectError, ToolSchemaError
from ..policy.base import Verdict
from . import schema as _schema

_NAME_RE = r"^[a-z][a-z0-9_]{0,63}$"


class Effect(str, Enum):
    READ     = "read"
    WRITE    = "write"
    EXTERNAL = "external"
    DANGER   = "danger"


@value
class EffectProfile:
    parallel_safe: bool
    retryable: bool
    taints_output: bool
    decision_standard: Verdict
    decision_strict: Verdict
    audit_level: str


#: Single source of truth for tool handling.  A module constant, not configuration:
#: a user who could edit it could disable the taint rule (IDL-14).
EFFECT_PROFILES: Final[Mapping[Effect, EffectProfile]] = {
    Effect.READ:     EffectProfile(True,  True,  False, Verdict.ALLOW, Verdict.ALLOW, "debug"),
    Effect.WRITE:    EffectProfile(False, False, False, Verdict.ALLOW, Verdict.ASK,   "info"),
    Effect.EXTERNAL: EffectProfile(True,  True,  True,  Verdict.ALLOW, Verdict.ASK,   "info"),
    Effect.DANGER:   EffectProfile(False, False, False, Verdict.ASK,   Verdict.ASK,   "warning"),
}

_EFFECT_HELP = (
    "  read      only looks at things          (parallel, retryable, auto-allowed)\n"
    "  write     changes something reversibly  (serial, not retried)\n"
    "  external  brings in outside content     (output treated as untrusted)\n"
    "  danger    cannot be undone              (always asks; blocked after untrusted input)\n"
)

_DANGER_WORDS = ("send", "delete", "remove", "drop", "pay", "charge", "post", "publish",
                 "email", "deploy", "shutdown", "kill", "execute", "run_command")
_EXTERNAL_WORDS = ("search", "fetch", "browse", "download", "crawl", "http", "web")
_WRITE_WORDS = ("write", "save", "update", "set", "create", "insert", "store", "append")


def _guess(name: str) -> str:
    low = name.lower()
    for words, eff in ((_DANGER_WORDS, "danger"), (_EXTERNAL_WORDS, "external"),
                       (_WRITE_WORDS, "write")):
        if any(w in low for w in words):
            return eff
    return "read"


@value
class ToolSpec:
    __hash__ = None                     # holds a Mapping — docs/04 §0 Hashability
    name: str
    description: str
    input_schema: Mapping[str, Any]
    effect: Effect
    fn: Callable[..., Any]              # always awaitable
    accepts_tainted: bool = False
    timeout_s: float = 30.0
    max_result_tokens: int = 4_000
    source: str = ""
    subagent: Any = None            # the child Agent, when this tool wraps one

    def to_api(self) -> dict[str, Any]:
        """Provider tool definition.  strict:true — ADR-022."""
        return {"name": self.name, "description": self.description,
                "input_schema": dict(self.input_schema), "strict": True}


def tool(
    *,
    effect: "Effect | str | None" = None,
    subagent: Any = None,
    name: str | None = None,
    accepts_tainted: bool = False,
    timeout_s: float = 30.0,
    max_result_tokens: int = 4_000,
) -> Callable[[Callable[..., Any]], ToolSpec]:
    """Turn a function into a tool.  `effect` is required — docs/06-safety.md#effects."""

    def decorate(fn: Callable[..., Any]) -> ToolSpec:
        fname = name or fn.__name__

        if effect is None:
            raise MissingEffectError(
                f"tool {fn.__name__!r} must declare what it does to the world.\n\n"
                f'    @tool(effect="{_guess(fn.__name__)}")     '
                f"← likely, based on the name\n"
                f"    def {fn.__name__}(...):\n\n"
                f"{_EFFECT_HELP}\n"
                f"  -> docs/06-safety.md#effects"
            )
        try:
            eff = Effect(effect)
        except ValueError:
            close = difflib.get_close_matches(str(effect), [e.value for e in Effect], n=1, cutoff=0.4)
            hint = f' Did you mean "{close[0]}"?' if close else ""
            raise MissingEffectError(
                f"{effect!r} is not one of the four choices.{hint}\n\n{_EFFECT_HELP}\n"
                f"  -> docs/06-safety.md#effects"
            ) from None

        import re
        if not re.match(_NAME_RE, fname):
            raise ToolSchemaError(
                f"tool name {fname!r} is not usable: it must be lowercase letters, digits\n"
                f"and underscores, starting with a letter, at most 64 characters.\n\n"
                f"  -> docs/04-interfaces.md#1-tools"
            )

        description, input_schema = _schema.build(fn)

        if inspect.iscoroutinefunction(fn):
            afn = fn
        else:
            @functools.wraps(fn)
            async def afn(*a: Any, **k: Any) -> Any:          # IDL-06: wrap once, at import
                return await asyncio.to_thread(fn, *a, **k)

        try:
            src = f"{inspect.getsourcefile(fn)}:{inspect.getsourcelines(fn)[1]}"
        except Exception:                                     # pragma: no cover
            src = "<unknown>"

        return ToolSpec(fname, description, input_schema, eff, afn,
                        accepts_tainted, timeout_s, max_result_tokens, src, subagent)

    return decorate
