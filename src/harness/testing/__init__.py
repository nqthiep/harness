"""Test helpers — task T-0.7.

`no_network` is the highest-value Poka-Yoke in the suite (register #34): a contributor
cannot bill themselves by mistake, even by writing the test wrong.
"""
from __future__ import annotations

import socket
from contextlib import contextmanager
from typing import Iterator

from ..models.fake import FakeModel

__all__ = ["FakeModel", "no_network", "NetworkAccessInTest",
           "approve_all", "deny_all", "assert_tool_called", "assert_no_tool"]


class NetworkAccessInTest(RuntimeError):
    pass


@contextmanager
def no_network() -> Iterator[None]:
    """Make any socket use raise.  Register as an autouse fixture (IDL-08)."""
    real = socket.socket

    class Blocked(real):                              # type: ignore[misc,valid-type]
        def __init__(self, *a, **k):
            raise NetworkAccessInTest(
                "a test tried to open a network connection.\n"
                "  Tests run offline and free — use harness.testing.FakeModel.\n"
                "  -> docs/09-testing.md#3-harnesstesting"
            )

    socket.socket = Blocked                           # type: ignore[assignment]
    try:
        yield
    finally:
        socket.socket = real                          # type: ignore[assignment]


def approve_all():
    def approve(call, ctx): return True
    return approve


def deny_all():
    def approve(call, ctx): return False
    return approve


def _tools_called(result) -> list[str]:
    out = []
    for m in result.messages:
        content = m.get("content") if isinstance(m, dict) else None
        if isinstance(content, list):
            out += [b.get("name") for b in content
                    if isinstance(b, dict) and b.get("type") == "tool_use"]
    return [n for n in out if n]


def assert_tool_called(result, name: str) -> None:
    called = _tools_called(result)
    if name not in called:
        raise AssertionError(f"expected {name!r} to be called; called: {called or 'nothing'}")


def assert_no_tool(result, name: str) -> None:
    called = _tools_called(result)
    if name in called:
        raise AssertionError(f"expected {name!r} NOT to be called, but it was")
