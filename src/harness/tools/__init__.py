"""@tool, Effect, ToolSpec — docs/04-interfaces.md §1, tasks T-0.2 and T-1.1."""
from __future__ import annotations

from .._value import value

import asyncio
import difflib
import functools
import inspect
from enum import Enum
from typing import Any, Callable, Final, Mapping

import re

from ..errors import MissingEffectError, ToolSchemaError
from ..policy.base import Verdict
from ..policy.label import Confidentiality, Integrity, Label
from . import schema as _schema

_NAME_RE = r"^[a-z][a-z0-9_]{0,63}$"


def slug(text: str, *, fallback: str = "tool") -> str:
    """Turn any human name into a valid tool name.

    Round 32: `as_tool()` built names by lowercasing the agent's name, so any agent named
    in Vietnamese, Chinese, Japanese or Arabic produced an invalid tool name and the
    library was unusable outside ASCII.  NFKD decomposition separates a Latin letter from
    its diacritics, so "Chuyên gia" becomes "chuyen_gia" rather than being discarded.
    """
    import unicodedata
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    out = "".join(c if c.isascii() and (c.isalnum() or c == "_") else "_"
                  for c in ascii_only.lower())
    out = re.sub(r"_+", "_", out).strip("_")

    # A name written entirely in a non-Latin script (Chinese, Arabic, Thai) leaves
    # nothing behind.  Two such agents would then collide on the same tool name, so a
    # short deterministic digest of the ORIGINAL name keeps them distinct (Round 32).
    if not out or out == fallback:
        import hashlib
        digest = hashlib.blake2b(text.encode("utf-8"), digest_size=3).hexdigest()
        return f"{fallback}_{digest}"[:64]
    if not out[0].isalpha():
        out = f"{fallback}_{out}"
    return out[:64]


class Effect(str, Enum):
    READ     = "read"
    WRITE    = "write"
    EXTERNAL = "external"
    DANGER   = "danger"


@value
class EffectProfile:
    parallel_safe: bool
    retryable: bool
    #: Nhãn mà kết quả của một tool thuộc effect này mang — L-1, design/00-foundation §3.2.
    #: `external` là nguồn UNTRUSTED duy nhất; các effect còn lại không tự làm nhiễm.
    emits: Label
    #: Nhãn confidentiality tối đa được phép CHẢY VÀO tool này. `write`/`external` là
    #: sink PUBLIC (dữ liệu SECRET không được reach chúng, trừ khi operator không đặt gì
    #: khác — không có cờ nới ở đây, chỉ có thể thắt); `read`/`danger` không hạn chế vì
    #: chúng không phải kênh xuất — "safe to call even when the context is tainted, it
    #: cannot exfiltrate" (Microsoft, chép lại có ghi công, research/09 §16bis).
    max_confidentiality: Confidentiality
    decision_standard: Verdict
    decision_strict: Verdict
    audit_level: str


#: Single source of truth for tool handling.  A module constant, not configuration:
#: a user who could edit it could disable the taint rule (IDL-14).
EFFECT_PROFILES: Final[Mapping[Effect, EffectProfile]] = {
    Effect.READ:     EffectProfile(True,  True,  Label(), Confidentiality.SECRET,
                                   Verdict.ALLOW, Verdict.ALLOW, "debug"),
    Effect.WRITE:    EffectProfile(False, False, Label(), Confidentiality.PUBLIC,
                                   Verdict.ALLOW, Verdict.ASK,   "info"),
    Effect.EXTERNAL: EffectProfile(True,  True,  Label(Integrity.UNTRUSTED), Confidentiality.PUBLIC,
                                   Verdict.ALLOW, Verdict.ASK,   "info"),
    Effect.DANGER:   EffectProfile(False, False, Label(), Confidentiality.SECRET,
                                   Verdict.ASK,   Verdict.ASK,   "warning"),
}

#: The exact text §15 shows a child.  The parentheticals this used to carry — "parallel,
#: retryable, auto-allowed" — re-exposed the five behaviours ADR-003 derives so the author
#: never has to think about them, and pushed the message to reading grade 12.5 (Round 31).
_EFFECT_HELP = (
    "  read      only looks at things\n"
    "  write     changes something you could undo\n"
    "  external  brings in stuff from the internet\n"
    "  danger    does something you can't undo\n"
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
    timeout_s: float = 30.0
    max_result_tokens: int = 4_000
    source: str = ""
    subagent: Any = None            # the child Agent, when this tool wraps one
    #: T-9.1, design/03-tools-and-mcp.md §5 — `None` for a local tool; a `ServerLabel`
    #: (opaque string, v1 — no `fingerprint`, K-12) for a tool `harness.mcp.connect()`
    #: classified from a third-party server. `policy/decision.py`'s `Scope.server` keys
    #: a grant to this same string, so approving `search(query="x")` on one server never
    #: approves the same-named tool on another (design/03 §5.3 M-4).
    server: str | None = None

    def to_api(self) -> dict[str, Any]:
        """Provider tool definition.  strict:true — ADR-022."""
        return {"name": self.name, "description": self.description,
                "input_schema": dict(self.input_schema), "strict": True}


def tool(
    *,
    effect: "Effect | str | None" = None,
    subagent: Any = None,
    name: str | None = None,
    timeout_s: float = 30.0,
    max_result_tokens: int = 4_000,
) -> Callable[[Callable[..., Any]], ToolSpec]:
    """Turn a function into a tool.  `effect` is required — docs/06-safety.md#effects.

    KHÔNG có `accepts_tainted=` ở đây. Nó khác chỗ trong bản nháp đầu, và một reviewer
    chỉ ra vì sao: một đối số decorator có mặc định là đúng hình dạng công tắc an toàn mà
    thiết kế này phê phán ở Microsoft (design/03-tools-and-mcp.md §1.1bis,
    design/review-security.md S-16). `accepts_tainted` chỉ đến từ
    `Agent(accepts_tainted={...})` / `build_agent(accepts_tainted={...})`, do OPERATOR
    đặt lúc bind, không do tác giả tool đặt lúc định nghĩa.
    """

    def decorate(fn: Callable[..., Any]) -> ToolSpec:
        fname = name or fn.__name__

        if effect is None:
            raise MissingEffectError(
                "Your tool needs to say what it does in the world.\n\n"
                f'    @tool(effect="{_guess(fn.__name__)}")     '
                "← probably this one, from the name\n"
                f"    def {fn.__name__}(...):\n\n"
                f"{_EFFECT_HELP}\n"
                "  -> docs/15-first-agent.md"
            )
        try:
            eff = Effect(effect)
        except ValueError:
            close = difflib.get_close_matches(str(effect), [e.value for e in Effect], n=1, cutoff=0.4)
            hint = f' Did you mean "{close[0]}"?' if close else ""
            raise MissingEffectError(
                f"{effect!r} is not one of the four choices.{hint}\n\n{_EFFECT_HELP}\n"
                "  -> docs/15-first-agent.md"
            ) from None

        if not re.match(_NAME_RE, fname):
            raise ToolSchemaError(
                f"{fname!r} does not work as a tool name.\n\n"
                "  Use small letters, numbers and _ , starting with a letter.\n"
                f"  Try: {slug(fname, fallback='my_tool')}\n\n"
                "  -> docs/15-first-agent.md"
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
                        timeout_s, max_result_tokens, src, subagent)

    return decorate
