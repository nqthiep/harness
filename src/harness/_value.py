"""`@value` — the frozen value-type decorator used throughout the package.

IDL-05 mandates `frozen=True, slots=True` on every data class.  Round 25 found what
that produces for the most likely user mistake — assigning an attribute that is not a
declared field, either a typo or an attempt to attach state:

    TypeError: super(type, obj): obj must be an instance or subtype of type

That is CPython's, not ours: `slots=True` rebuilds the class, and the generated
`__setattr__`'s zero-arg `super()` still closes over the original.  Assigning an
*existing* field raises a clean `FrozenInstanceError`; assigning a *new* name does not.

`@value` replaces `__setattr__` and `__delattr__` with messages a person can act on.

**`dataclass_transform` is not decoration.**  Without it a type checker sees the original
class body — no generated `__init__` — so every construction of every value type in the
package reports "Too many arguments", and every field read reports "has no attribute".
Round 39 measured it: 86 of 112 mypy errors, and, far worse, **a user of this library got
no type checking at all on `Money`, `Usage`, `Result`, `Ruling` or `ToolCall`** — the
types they touch most.  §II question 5 asks whether a runtime error can be made a
compile-time one; for the whole value layer the answer had been "no, silently".
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, fields
from typing import Any, TypeVar, dataclass_transform

T = TypeVar("T")


@dataclass_transform(frozen_default=True)
def value(cls: type[T]) -> type[T]:
    cls = dataclass(frozen=True, slots=True)(cls)          # type: ignore[assignment]
    names = {f.name for f in fields(cls)}                  # type: ignore[arg-type]
    label = cls.__name__

    def __setattr__(self: Any, name: str, _v: Any) -> None:
        if name in names:
            raise AttributeError(
                f"{label} is read-only; {name!r} cannot be changed after it is created.\n"
                f"  Build a new one instead of modifying this one."
            )
        near = difflib.get_close_matches(name, sorted(names), n=1, cutoff=0.6)
        hint = f"  Did you mean {near[0]!r}?\n" if near else ""
        raise AttributeError(
            f"{label} has no attribute {name!r}, and new attributes cannot be added.\n"
            f"{hint}  Fields: {', '.join(sorted(names))}"
        )

    def __delattr__(self: Any, name: str) -> None:
        raise AttributeError(f"{label} is read-only; {name!r} cannot be deleted.")

    cls.__setattr__ = __setattr__                          # type: ignore[method-assign]
    cls.__delattr__ = __delattr__                          # type: ignore[method-assign]
    return cls
