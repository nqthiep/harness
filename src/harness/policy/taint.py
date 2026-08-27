"""Taint tracking — ADR-011.  Sticky per run, raised by external tool output."""
from __future__ import annotations


class TaintTracker:
    __slots__ = ("_tainted", "_source")

    def __init__(self) -> None:
        self._tainted = False
        self._source = ""

    @property
    def tainted(self) -> bool: return self._tainted
    @property
    def source(self) -> str: return self._source

    def raise_taint(self, source_tool: str) -> bool:
        """Returns True the first time only, so taint.raised is emitted once."""
        if self._tainted:
            return False
        self._tainted = True
        self._source = source_tool
        return True
