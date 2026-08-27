"""ToolSet — task T-0.3.

Backed by a sorted tuple plus a name index, never a frozenset: ToolSpec holds a
Mapping and is unhashable, and the name ordering a set would destroy is the ordering
cache determinism depends on (Round 20).
"""
from __future__ import annotations

import json
from typing import Iterator, Sequence

from ..errors import DuplicateToolError
from . import ToolSpec


class ToolSet:
    __slots__ = ("_sorted", "_by_name")

    def __init__(self, specs: Sequence[ToolSpec] = ()) -> None:
        by_name: dict[str, ToolSpec] = {}
        for s in specs:
            if s.name in by_name:
                raise DuplicateToolError(
                    f"two tools are both called {s.name!r}; the model could not tell them apart.\n\n"
                    f"    {by_name[s.name].source}\n"
                    f"    {s.source}\n\n"
                    f'  Rename one, or pass name="..." to @tool.\n\n'
                    f"  -> docs/04-interfaces.md#1-tools"
                )
            by_name[s.name] = s
        self._by_name = by_name
        self._sorted = tuple(sorted(by_name.values(), key=lambda s: s.name))

    def __iter__(self) -> Iterator[ToolSpec]: return iter(self._sorted)
    def __len__(self) -> int: return len(self._sorted)
    def __contains__(self, name: object) -> bool: return name in self._by_name
    def get(self, name: str) -> ToolSpec | None: return self._by_name.get(name)

    def to_api(self) -> list[dict]:
        """Name-sorted, canonically serializable.  Byte-identical across processes."""
        return [s.to_api() for s in self._sorted]

    def canonical(self) -> str:
        return json.dumps(self.to_api(), sort_keys=True, separators=(",", ":"))
