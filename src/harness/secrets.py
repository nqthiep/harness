"""Secret — docs/06-safety.md §5.

Unhashable on purpose (IDL-32): equal-by-value secrets hashing by name would violate
Python's hash invariant, and unhashable also stops a credential becoming a cache key.
The registry holds weak references keyed by id() (IDL-33), so the redactor's reach ends
with the secret's lifetime.  It is NOT a WeakSet: a WeakSet hashes its members, and this
type is deliberately unhashable — the two Round 19 fixes were incompatible until Round 24
executed them (ADR-024).
"""
from __future__ import annotations

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
        yield self._v


def redact(text: str) -> str:
    """Applied on transcript write, before the bytes exist."""
    for s in _live():
        with s.reveal() as v:
            if v and v in text:
                text = text.replace(v, f"<{s._name} hidden>")
    return text
