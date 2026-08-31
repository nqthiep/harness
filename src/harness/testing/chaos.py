"""M6/T-6.4 — failure injection, docs/17-research-alignment.md.

Five scenarios, one per line item T-6.4 names: provider timeout, tool raise, store
chết, policy raise, model trả rác. Each helper below injects exactly one of those
failures into a real `Agent`/`Store`/`PolicyEngine`, so a test can assert the harness's
actual behavior under it — "Done: mọi kịch bản có một hành vi được khẳng định, không cái
nào crash" is a property of the harness under these, not a property of this module.

These wrap real seams (`ModelProvider`, `Store`, `Policy`) rather than adding a sixth —
the three-part plugin test (docs/02-architecture.md §2.4) is about production surface,
not test doubles, but the same instinct against needless new surface applies here too.
"""
from __future__ import annotations

from typing import Any, Sequence

from ..models.fake import FakeModel
from ..policy.base import Policy, Ruling, ToolCall
from ..tools import tool


class ChaosError(RuntimeError):
    """Raised by every chaos helper's injected failure — one exception type across all
    five scenarios, so a test asserting "the harness didn't crash" can, if it wants to,
    also assert the ONE kind of thing that leaked would have been this and nothing
    stranger."""


class TimeoutProvider:
    """Wraps a real provider (default: `FakeModel`); the Nth call (default: every call)
    raises `TimeoutError` instead of completing — "provider timeout"."""

    def __init__(self, inner: Any | None = None, *, fail_calls: Sequence[int] = (0,)) -> None:
        self._inner = inner if inner is not None else FakeModel([FakeModel.text("hi")])
        self._fail_calls = set(fail_calls)
        self._n = 0

    async def complete(self, request, *, on_delta=None):
        n, self._n = self._n, self._n + 1
        if n in self._fail_calls:
            raise TimeoutError("chaos: provider timed out")
        return await self._inner.complete(request, on_delta=on_delta)

    def price(self, model: str): return self._inner.price(model)
    async def count_input_tokens(self, request) -> int:
        return await self._inner.count_input_tokens(request)
    def max_output(self, model: str) -> int: return self._inner.max_output(model)


def raising_tool(*, name: str = "chaos_tool", effect: str = "read"):
    """A tool that always raises when called — "tool raise". `effect=` lets a caller
    pick which effect class sees the failure, since T-6.3's retry behavior differs by
    class and a chaos test may want to exercise either side of that."""
    @tool(effect=effect, name=name)
    def _raises(**_: Any) -> str:
        """Một tool luôn hỏng — dùng cho kiểm thử failure injection."""
        raise ChaosError(f"chaos: tool {name!r} always raises")
    return _raises


class BrokenStore:
    """A `Store` whose `get`/`put`/`search` all raise — "store chết". Mirrors
    `test_m6_t61_idempotency.py`'s private `BrokenStore`, generalized here as the public,
    reusable chaos helper (T-6.4's own scenario, not just T-6.1's)."""

    async def get(self, key: str) -> str | None:
        raise ChaosError("chaos: store is down (get)")

    async def put(self, key: str, value: str, *, ttl_s: float | None = None) -> None:
        raise ChaosError("chaos: store is down (put)")

    async def delete(self, key: str) -> None:
        raise ChaosError("chaos: store is down (delete)")

    async def search(self, query: str, *, limit: int = 5):
        raise ChaosError("chaos: store is down (search)")

    async def close(self) -> None: ...


class RaisingPolicy(Policy):
    """A `Policy` whose `check()` always raises — "policy raise". `PolicyEngine.decide()`
    already fails closed on this (`policy/engine.py`); this helper exists so a test can
    assert that behavior explicitly instead of relying on it implicitly."""
    name = "chaos-policy"

    def check(self, call: ToolCall, ctx: Any) -> Ruling:
        raise ChaosError("chaos: policy raised")


def garbage_model(text: str = "this is not valid json at all") -> FakeModel:
    """A `FakeModel` whose only scripted reply is text that will not parse against a
    dataclass `returns=` — "model trả rác"."""
    return FakeModel([FakeModel.text(text)])


def unknown_stop_reason_model() -> FakeModel:
    """A `FakeModel` whose reply carries a `stop_reason` the harness's stop-reason table
    (`run.py::_MAP`) does not recognize — a different flavor of "model trả rác": not bad
    *content*, a provider protocol value nobody mapped."""
    return FakeModel([FakeModel.text("hi", stop="something_the_provider_never_documented")])
