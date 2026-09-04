"""`Chat.asay()` — the async twin `Chat` was missing, and the accounting hole that
missing twin was hiding (ADR-088).

Before this, a caller inside a running event loop could not use `Chat` at all: `say()`
goes through `try_run`, whose `_guard_sync` raises rather than deadlocking. The one such
caller in this repository — `harness.contrib.driver`, which cancels a turn to serve an
urgent event — drove `agent.atry_run(text, _history=...)` instead, reimplementing this
class's bookkeeping around a PRIVATE keyword argument. It got the bookkeeping wrong in
the way reimplemented bookkeeping does: a cancelled turn's tokens are billed and reached
no ledger.
"""
import asyncio
import unittest

from harness import Agent, StopReason, tool
from harness.errors import SyncInAsyncContextError
from harness.models import pricing
from harness.models.fake import FakeModel


class Priced(FakeModel):
    """`FakeModel` bills zero, so every cost assertion below would pass vacuously
    against it. Priced like a real model instead — the same trick `contrib.driver`'s demo
    needs for the same reason."""

    def price(self, model):
        return pricing.price("claude-opus-5")


@tool(effect="read")
async def slow() -> str:
    """Long enough to be cancelled in the middle of."""
    await asyncio.sleep(5)
    return "done"


def _script():
    return [FakeModel.tool_call("slow", {}), FakeModel.text("finished")]


def _agent(script=None, **kw):
    return Agent(name="A", job="hi", tools=[slow],
                 provider=Priced(script if script is not None else _script()),
                 budget=kw.pop("budget", "$1"), **kw)


async def _cancel_mid_turn(chat, message="go", after_s=0.25):
    """Start a turn, let it reach the slow tool, cancel it. Returns nothing — the point
    is the state left behind on `chat`."""
    task = asyncio.create_task(chat.asay(message))
    await asyncio.sleep(after_s)
    task.cancel()
    with unittest.TestCase().assertRaises(asyncio.CancelledError):
        await task


class AsayIsTheAsyncTwin(unittest.IsolatedAsyncioTestCase):
    async def test_a_turn_returns_a_result_and_advances_the_conversation(self):
        chat = _agent([FakeModel.text("hello")]).chat()
        r = await chat.asay("hi")
        self.assertEqual(r.text, "hello")
        self.assertEqual(len(chat.messages), 2)
        self.assertGreater(chat.spent.decimal, 0)

    async def test_two_turns_accumulate_history_and_spend(self):
        chat = _agent([FakeModel.text("one"), FakeModel.text("two")]).chat()
        await chat.asay("first")
        after_one = chat.spent
        await chat.asay("second")
        self.assertEqual(len(chat.messages), 4)
        self.assertGreater(chat.spent.decimal, after_one.decimal)

    async def test_say_still_refuses_inside_a_running_loop(self):
        """Which is the whole reason `asay` exists — `say()` is not merely inconvenient
        here, it raises."""
        chat = _agent([FakeModel.text("hello")]).chat()
        with self.assertRaises(SyncInAsyncContextError):
            chat.say("hi")

    async def test_the_conversation_budget_still_ends_the_conversation(self):
        """`_turn()` is shared by both twins, so the budget check must fire through
        `asay` too, not only through `say`."""
        chat = _agent([FakeModel.text("one"), FakeModel.text("two")]).chat(
            budget="$0.0000001")
        r = await chat.asay("hi")
        self.assertIs(r.stop_reason, StopReason.BUDGET_EXHAUSTED)


class ACancelledTurnIsStillBilled(unittest.IsolatedAsyncioTestCase):
    """The defect the twin exposed. Measured before the fix: a turn cancelled mid-tool
    emits `RUN_FINISHED stop_reason='cancelled' cost_usd='$0.0009'` — the model call that
    produced the tool request was already paid for — and every line after
    `await atry_run(...)` is skipped, because `run.py` re-raises `CancelledError` instead
    of returning a `Result` (T-6.2/Y-01: swallowing it broke asyncio's cancellation
    protocol). So the conversation ledger saw nothing.
    """

    async def test_cancelling_re_raises(self):
        chat = _agent().chat()
        await _cancel_mid_turn(chat)            # asserts the raise itself

    async def test_the_spend_reaches_the_conversation_ledger(self):
        chat = _agent().chat()
        await _cancel_mid_turn(chat)
        self.assertGreater(chat.spent.decimal, 0,
                           "tokens were billed for the model call that requested the "
                           "tool; the conversation must see them")

    async def test_the_history_is_not_advanced(self):
        """Deliberate, and not the same question as the spend. There is no assistant
        reply to record, and appending the user message alone would leave two user turns
        back to back — a shape this library has never sent to a real provider."""
        chat = _agent().chat()
        await _cancel_mid_turn(chat)
        self.assertEqual(chat.messages, [])

    async def test_a_cancelled_turn_reduces_what_is_left_to_spend(self):
        """The consequence: `_turn()` sizes the next turn from
        `budget - spent`, so a cancelled turn that never reaches `spent` is a turn a
        preempting caller can repeat forever without the conversation budget ever
        binding — which is exactly what `contrib.driver` does for a living.

        Asserted as the arithmetic rather than as a `BUDGET_EXHAUSTED` stop reason:
        pushing `spent` past the ceiling needs a budget small enough that the run stops
        on its own before reaching the tool, so there is nothing left to cancel. That the
        ceiling itself fires through `asay` is
        `test_the_conversation_budget_still_ends_the_conversation` above.
        """
        chat = _agent().chat(budget="$1")
        before = chat.budget.usd - chat.spent.decimal
        await _cancel_mid_turn(chat)
        after = chat.budget.usd - chat.spent.decimal
        self.assertLess(after, before)

    async def test_an_outer_cancellation_is_not_swallowed(self):
        """The spend is recorded on the way past, not instead of the exception."""
        chat = _agent().chat()
        task = asyncio.create_task(chat.asay("go"))
        await asyncio.sleep(0.25)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(task.cancelled())


if __name__ == "__main__":
    unittest.main()
