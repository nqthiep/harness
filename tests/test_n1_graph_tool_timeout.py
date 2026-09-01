"""N-1 — design/07-risks-and-open-issues.md: the LangGraph/durable backend's
`_run_tools()` used to call a tool with no timeout of its own at all (a bare
`asyncio.run(spec.fn(**args))`), so a tool with a slow or hanging effect (an HTTP call
that never returns, a bug in a subprocess call) could hang that node — and the whole
run behind it — forever. The classic backend (`dispatch.py::_invoke`) already wrapped
every tool call in `asyncio.timeout(...)`, clamped by `Ledger.tool_timeout()` to the
run's own remaining wall clock; this is the same fix, ported to the durable backend
via a small `_with_timeout()` wrapper coroutine (`asyncio.timeout()` needs an
`async with` inside a coroutine, and `_run_tools` only had a bare `asyncio.run(coro)`
to hand it one through).
"""
import asyncio
import sys
import tempfile
import unittest
from decimal import Decimal

sys.path.insert(0, "src")

from harness import Agent, Budget, Effect, tool
from harness.models.fake import FakeModel


@tool(effect=Effect.READ, timeout_s=0.05)
async def hangs_forever(x: int) -> str:
    """A read tool whose own `timeout_s` ceiling is hit long before it'd ever return."""
    await asyncio.sleep(5)
    return "done"


class Recorder:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append((event.kind.value, dict(event.data)))

    def close(self): ...

    def of(self, kind):
        return [d for k, d in self.events if k == kind]


class GraphToolTimeoutTests(unittest.TestCase):
    def test_a_hanging_tool_times_out_instead_of_hanging_the_run(self):
        script = [FakeModel.tool_call("hangs_forever", {"x": 1}), FakeModel.text("ok")]
        db = tempfile.mktemp(suffix=".sqlite3")
        rec = Recorder()
        agent = Agent(
            name="p", job="x", provider=FakeModel(script), tools=[hangs_forever],
            durable=True, checkpoint=db, allowed_hosts=None,
            approve=lambda c, ctx: True, exporters=[rec],
        )
        import time
        t0 = time.monotonic()
        result = agent.try_run("go")
        elapsed = time.monotonic() - t0

        # The tool's own ceiling is 0.05s; the run must not hang for the 5s sleep.
        self.assertLess(elapsed, 2.0,
            f"a hanging tool must be cut off by its own timeout_s, took {elapsed}s")
        self.assertTrue(result.ok, "the run itself still finishes gracefully")

        # tool.finished isn't emitted on failure (only ERROR_RAISED + the ToolMessage);
        # what matters is the error.raised event and the message text reaching the model.
        errors = rec.of("error.raised")
        timeout_errors = [d for d in errors if d.get("where") == "tool"
                          and "timed out after 0.05s" in d.get("message", "")]
        self.assertTrue(timeout_errors,
            f"expected a tool timeout error.raised event, got: {errors}")

    def test_timeout_message_names_run_budget_when_that_is_the_tighter_clamp(self):
        # A run wall-clock budget tighter than the tool's own timeout_s must produce
        # the OTHER message — "timed out: run wall-clock budget reached" — exactly as
        # dispatch.py::_invoke does on the classic backend (S-2/N-1 parity).
        @tool(effect=Effect.READ, timeout_s=30.0)
        async def slow_read(x: int) -> str:
            """A read that would happily finish in time, if the run budget let it."""
            await asyncio.sleep(5)
            return "done"

        script = [FakeModel.tool_call("slow_read", {"x": 1}), FakeModel.text("ok")]
        db = tempfile.mktemp(suffix=".sqlite3")
        rec = Recorder()
        agent = Agent(
            name="p", job="x", provider=FakeModel(script), tools=[slow_read],
            durable=True, checkpoint=db, allowed_hosts=None,
            approve=lambda c, ctx: True, exporters=[rec],
            budget=Budget(usd=Decimal("10.0"), wall_clock_s=0.05),
        )
        result = agent.try_run("go")
        self.assertTrue(result.ok)

        errors = rec.of("error.raised")
        budget_errors = [d for d in errors if d.get("where") == "tool"
                         and "run wall-clock budget reached" in d.get("message", "")]
        self.assertTrue(budget_errors,
            f"expected the run-budget timeout message, got: {errors}")


if __name__ == "__main__":
    unittest.main()
