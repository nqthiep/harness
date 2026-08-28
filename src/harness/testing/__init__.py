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

    # IDL-08: the autouse fixture that makes an accidental live call impossible.
    socket.socket = Blocked                           # type: ignore[misc]
    try:
        yield
    finally:
        socket.socket = real                          # type: ignore[misc]


def approve_all():
    def approve(call, ctx): return True
    return approve


def deny_all():
    def approve(call, ctx): return False
    return approve


def _tools_called(result) -> list[str]:
    """Tools that RAN — not tools the model asked for.

    Round 38: this read `tool_use` blocks, which are the model's *requests*.  A tool that
    policy blocked still counted as called, so `assert_no_tool` failed on exactly the
    case it exists to prove: a dangerous tool that was successfully refused.  §09 calls
    these "assertions on behaviour"; they were assertions on intent.
    """
    return list(getattr(result, "tools_run", ()) or ())


def assert_tool_called(result, name: str) -> None:
    called = _tools_called(result)
    if name not in called:
        raise AssertionError(
            f"expected {name!r} to run; what ran: {', '.join(called) or 'nothing'}"
            "\n\n  (a tool the model asked for but policy blocked did NOT run)")


def assert_no_tool(result, name: str) -> None:
    called = _tools_called(result)
    if name in called:
        raise AssertionError(f"expected {name!r} NOT to run, but it did")
