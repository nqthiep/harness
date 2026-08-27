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
import hmac
import weakref
from contextlib import contextmanager
from typing import Iterator

#: id(secret) -> weakref.  The finalizer callback removes the entry, so a secret that
#: goes out of scope stops being retained.  Never hashes the Secret itself.
_REGISTRY: "dict[int, weakref.ref[Secret]]" = {}


def _register(s: "Secret") -> None:
    key = id(s)
    _REGISTRY[key] = weakref.ref(s, lambda _ref, k=key: _REGISTRY.pop(k, None))


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
    try:
        yield
    finally:
        values = _run_values.get()
        if values is not None:
            values.clear()
        _run_values.reset(token)


def _live() -> "list[Secret]":
    out = []
    for ref in list(_REGISTRY.values()):
        s = ref()
        if s is not None:
            out.append(s)
    return out


class Secret:
    __slots__ = ("_v", "_name", "__weakref__")
    __hash__ = None                         # IDL-32

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


def redact(text: str) -> str:
    """Applied on every write-out, before the bytes exist."""
    for s in _live():
        if s._v and s._v in text:                 # no reveal(): redaction must not register
            text = text.replace(s._v, f"<{s._name} hidden>")
    for v in (_run_values.get() or ()):
        if v and v in text:
            text = text.replace(v, "<secret hidden>")
    return text
