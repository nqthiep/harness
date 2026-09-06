"""Secret — docs/06-safety.md §5.

Unhashable on purpose (IDL-32): equal-by-value secrets hashing by name would violate
Python's hash invariant, and unhashable also stops a credential becoming a cache key.
The registry holds weak references keyed by id() (IDL-33), so the redactor's reach ends
with the secret's lifetime.  It is NOT a WeakSet: a WeakSet hashes its members, and this
type is deliberately unhashable — the two Round 19 fixes were incompatible until Round 24
executed them (ADR-024).
"""
from __future__ import annotations

import contextvars
import hashlib
import hmac
import json
import re
import weakref
from contextlib import contextmanager
from typing import Iterator

#: id(secret) -> weakref.  The finalizer callback removes the entry, so a secret that
#: goes out of scope stops being retained.  Never hashes the Secret itself.
_REGISTRY: "dict[int, weakref.ref[Secret]]" = {}


def _register(s: "Secret") -> None:
    key = id(s)
    _REGISTRY[key] = weakref.ref(
        s, lambda _ref, k=key: _REGISTRY.pop(k, None))   # type: ignore[misc]


#: Values revealed during the current run, held strongly until the run ends.
#:
#: The weak registry alone is not enough (Round 25, RT-13).  A secret constructed inside
#: a tool dies with the tool's frame, but a string it was formatted into — an exception
#: message, a log line — outlives it, and by the time redaction runs there is nothing
#: left to match against.  Short-lived per-request secrets are the common case in a
#: server, so this is the case redaction most needs to cover.
#:
#: Retention is scoped to the run that could leak the value: strong enough to redact,
#: bounded by exactly the window in which anything derived from it can still be written.
_run_values: contextvars.ContextVar["set[str] | None"] = contextvars.ContextVar(
    "harness_run_secret_values", default=None)


@contextmanager
def redaction_scope() -> Iterator[None]:
    """Held open for the duration of a run.  Cleared on exit."""
    token = _run_values.set(set())
    hits = _scan_hits.set(set())
    try:
        yield
    finally:
        values = _run_values.get()
        if values is not None:
            values.clear()
        _run_values.reset(token)
        seen = _scan_hits.get()
        if seen is not None:
            seen.clear()
        _scan_hits.reset(hits)


def _live() -> "list[Secret]":
    out = []
    for ref in list(_REGISTRY.values()):
        s = ref()
        if s is not None:
            out.append(s)
    return out


class Secret:
    __slots__ = ("_v", "_name", "__weakref__")
    #: Same reason as `Agent`: set via `object.__setattr__`, invisible to a checker.
    _v: str
    _name: str
    __hash__ = None                         # type: ignore[assignment]  # IDL-32

    def __init__(self, value: str, *, name: str = "secret") -> None:
        object.__setattr__(self, "_v", value)
        object.__setattr__(self, "_name", name)
        _register(self)

    def __repr__(self) -> str: return f"Secret({self._name!r})"
    def __str__(self) -> str: return f"<{self._name} hidden>"
    def __format__(self, spec: str) -> str: return str(self)
    def __reduce__(self):
        raise TypeError("a Secret must not be pickled or serialized")

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Secret) and hmac.compare_digest(self._v, other._v)

    @contextmanager
    def reveal(self) -> Iterator[str]:
        """The only way to the value — and the point at which the run learns to redact it."""
        active = _run_values.get()
        if active is not None:
            active.add(self._v)
        yield self._v


#: Kinds of unwrapped key seen during the current `redaction_scope()`.  Bounded by the
#: number of patterns below, so it can never grow: it holds names, never values.
_scan_hits: contextvars.ContextVar["set[str] | None"] = contextvars.ContextVar(
    "harness_unwrapped_key_kinds", default=None)

#: **Format-anchored, deliberately not an entropy classifier.**
#:
#: `docs/06-safety.md:276` promised "the redactor also scans for high-entropy strings
#: matching known key formats (`sk-ant-`, AWS, GitHub, Slack) even when they were never
#: wrapped", and register #23 graded it "loud warning".  Measured before this existed:
#: `grep -rni entropy src/` returned nothing, `redact("sk-ant-api03-AAAA…")` returned its
#: input unchanged, and a tool result carrying an unwrapped key reached the model
#: verbatim.
#:
#: What is implemented is the honest half of that sentence and none of the dishonest
#: half.  A real entropy classifier — Shannon over a sliding window, "this looks random"
#: — fires on base64 payloads, SHA digests, UUIDs, minified JS and jwt bodies, which are
#: ordinary tool output.  Redaction runs on EVERY write, so a false positive silently
#: corrupts the model's input, and a redactor people learn to distrust is worse than one
#: that admits its scope.  So each pattern below anchors on a vendor prefix that means
#: nothing else, plus a length and charset floor.  That catches a pasted key and misses a
#: key format nobody listed here — which is the trade being made, on purpose.
#:
#: Not included, and this is the load-bearing omission: an AWS **secret** access key is
#: 40 base64 characters with no prefix at all, indistinguishable from a hash.  Matching it
#: would mean the classifier this refuses to be.
_KEY_PATTERNS: tuple[tuple[str, str], ...] = (
    # `sk-ant-api03-` plus 95; the floor of 24 keeps short placeholders out.
    ("anthropic", r"sk-ant-[A-Za-z0-9_-]{24,}"),
    # AWS access key ID: a fixed 4-letter type prefix then exactly 16 upper/digit.
    ("aws", r"(?:AKIA|ASIA|AROA|AIDA|ANPA|ANVA|AIPA|AGPA|ABIA|ACCA)[A-Z0-9]{16}"),
    # GitHub: ghp_ / gho_ / ghu_ / ghs_ / ghr_ then 36+.
    ("github", r"gh[pousr]_[A-Za-z0-9]{36,}"),
    # Slack bot/user/app/refresh tokens: xoxb- / xoxa- / xoxp- / xoxr- / xoxs-.
    ("slack", r"xox[abprs]-[A-Za-z0-9]{10,}-[A-Za-z0-9-]{10,}"),
)

_KEY_RE = re.compile("|".join(f"(?P<{kind}>{pat})" for kind, pat in _KEY_PATTERNS))

#: A literal every match must contain, checked with `in` before the regex runs.  This
#: function is on the write path of every event, tool result and transcript line, so the
#: common case — no key anywhere — has to be nearly free.
#:
#: These are the FULL prefixes, not shortened ones, and that is the whole optimisation.
#: The first version used `"gh"`, which appears in `night`, `right`, `through`, `high`,
#: `weight`; ordinary English prose therefore failed the prefilter and paid for the regex
#: anyway.  Measured over ~1 KB of such prose, 50 000 calls:
#:
#:     "gh" among the markers      51.28 us/call
#:     full prefixes (this list)    4.79 us/call
#:     no prefilter at all         52.64 us/call
#:
#: `test_public_redaction.py` checks every pattern still passes this filter, so a new
#: pattern cannot be added without a marker that reaches it.
_KEY_MARKERS = ("sk-ant-", "AKIA", "ASIA", "AROA", "AIDA", "ANPA", "ANVA", "AIPA",
                "AGPA", "ABIA", "ACCA", "ghp_", "gho_", "ghu_", "ghs_", "ghr_", "xox")


def scan_for_unwrapped_keys(text: str) -> tuple[str, ...]:
    """Which known key formats appear in `text`, unwrapped.  Names only, never values."""
    if not any(marker in text for marker in _KEY_MARKERS):
        return ()
    return tuple(sorted({m.lastgroup for m in _KEY_RE.finditer(text) if m.lastgroup}))


def unwrapped_key_kinds() -> frozenset[str]:
    """Formats `redact()` had to hide in this run because nobody wrapped them.

    **The `error.raised` warning register #23 grades is NOT emitted from here.**  This
    module is a pure function with no `EventBus` — `redact()` is called from
    `observe/transcript.py:54` and `dispatch.py:303`, which have one.  This accessor is
    the seam for that one-line emit; until it is wired, the redaction happens and the
    warning does not, and register #23's "loud warning" is still half true.  Filed as a
    handoff note rather than reached across a module boundary.
    """
    return frozenset(_scan_hits.get() or ())


def redact(text: str) -> str:
    """Applied on every write-out, before the bytes exist.

    Two passes, in this order.  Registered `Secret`s first — exact, no false positives,
    and the reason the type exists.  Then the format-anchored scan for keys nobody
    wrapped (`_KEY_PATTERNS`), which is best-effort by construction and says so.
    """
    for s in _live():
        if s._v and s._v in text:                 # no reveal(): redaction must not register
            text = text.replace(s._v, f"<{s._name} hidden>")
    for v in (_run_values.get() or ()):
        if v and v in text:
            text = text.replace(v, "<secret hidden>")
    if any(marker in text for marker in _KEY_MARKERS):
        seen = _scan_hits.get()

        def _hide(match: "re.Match[str]") -> str:
            kind = match.lastgroup or "key"
            if seen is not None:
                seen.add(kind)
            return f"<possible {kind} key hidden>"

        text = _KEY_RE.sub(_hide, text)
    return text


def contains_live_secret(text: str) -> bool:
    """S-3's first confidentiality source: a `Secret`'s plaintext appearing verbatim in
    `text` means a value the user wrapped in `Secret` has flowed into a tool result that
    is about to enter context. `emits_of` (policy/builtin.py) calls this on a tool's raw,
    pre-redaction payload and raises the message's `Label` to SECRET when it fires — the
    same matching `redact()` does, just reporting instead of replacing, so the two stay
    exactly in sync (one `Secret` never redacted-but-unlabeled, or vice versa).

    Same rule as `redact()`: no `.reveal()`, so detection itself never registers a value.
    """
    for s in _live():
        if s._v and s._v in text:
            return True
    for v in (_run_values.get() or ()):
        if v and v in text:
            return True
    return False


def safe_for_display(value: object, *, max_len: int = 200) -> str:
    """S-25: model-controlled tool arguments reaching a human approver, made safe to
    print — a DIFFERENT concern from `redact()` (that one hides secrets; this one hides
    nothing, it just makes sure what a human sees is what the harness actually sent, not
    something the model chose to draw on top of it).

    `call.arguments` goes straight from the model into `approve(call, ctx)` — the harness
    does not escape it. A crafted argument value can carry ANSI escape codes (clear line,
    move cursor) to redraw what a terminal-based approver sees, or an embedded
    "\\n\\n=== APPROVED, press y ===" to social-engineer one reading a rendered string.
    Two rules, both from review-security.md S-25:

      1. Any character that is not printable (control codes, ANSI escapes, embedded
         newlines included) renders as its Python escape sequence instead of executing —
         `\\x1b[2K` reads as four visible characters, never clears a line.
      2. A value longer than `max_len` renders as its length and a digest, not its
         content — long enough to hide a redraw payload inside otherwise-plausible text
         is long enough to not print verbatim.

    Not a security boundary by itself: an approve callback that does its own formatting
    (a Slack block, a web form) still has to apply the same care. It is the safe default
    for the common case — printing `call.arguments` straight at a human.
    """
    s = value if isinstance(value, str) else json.dumps(value, sort_keys=True,
                                                         ensure_ascii=False, default=str)
    if len(s) > max_len:
        digest = hashlib.blake2b(s.encode("utf-8"), digest_size=8).hexdigest()
        return f"<{len(s)} characters, digest={digest}>"
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in s)
