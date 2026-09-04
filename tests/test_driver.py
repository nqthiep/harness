"""`examples/driver.py` — priority-driven runtime events over the four channels the
harness actually acts on.

Every test names the mechanism it exercises, because each tier is a DIFFERENT harness
mechanism and a test that passes for the wrong reason would hide that. The four rules
from the module docstring get one test each, and the two that are enforced rather than
documented (a durable plan, the preemption cap) are asserted as refusals.

No camera, no sensor hardware, no API key: `FakeSensor` scripts the events and
`FakeModel` scripts the model.
"""
import asyncio
import unittest

from harness import Agent, Effect, ToolCall, Verdict, tool, with_middleware
from harness.errors import ConfigError
from harness.models.fake import FakeModel

from harness.contrib.driver import (Driver, Event, EventAnnouncer, EventInbox, FakeSensor,
                    INJECTED_CALL_ID, InterruptGate, Priority, WriteInFlight)


@tool(effect=Effect.READ)
async def look() -> str:
    "A read tool."
    return "nothing much"


@tool(effect=Effect.EXTERNAL)
async def look_around() -> str:
    "An external carrier: perception content is untrusted and the label must follow."
    return "nothing much"


@tool(effect=Effect.READ)
async def list_tasks() -> str:
    "Stands in for a durable plan (rule 3)."
    return "1. doing something long"


@tool(effect=Effect.WRITE)
async def slow_write() -> str:
    "A write that takes long enough to be interrupted mid-flight."
    await asyncio.sleep(0.4)
    return "written"


def _agent(script, *, tools=(look, list_tasks), policies=(), mws=()):
    agent = Agent(name="Subject", job="work and pay attention",
                  tools=list(tools), policies=list(policies),
                  provider=FakeModel(list(script)))
    return with_middleware(agent, *mws) if mws else agent


class _Spec:
    def __init__(self, effect):
        self.name, self.effect = "x", effect


def _call(name="write_source", effect=Effect.WRITE):
    return ToolCall(id="c1", name=name, arguments={}, spec=_Spec(effect))


class TheInboxThat(unittest.TestCase):
    def test_it_keeps_the_more_urgent_of_two_offers(self):
        """`Priority` is an `IntEnum` so this is a comparison, the same trick `Verdict`
        uses to make policy composition restrict-only."""
        inbox = EventInbox()
        inbox.offer(Event(Priority.LOW, "someone walked past"))
        inbox.offer(Event(Priority.CRITICAL, "the room is on fire"))
        self.assertEqual(inbox.peek().priority, Priority.CRITICAL)
        inbox.offer(Event(Priority.NORMAL, "someone sat down"))
        self.assertEqual(inbox.peek().priority, Priority.CRITICAL)

    def test_an_equal_priority_offer_replaces_the_older_one(self):
        # Size one on purpose: replaying a backlog of stale perceptions is worse than
        # saying the newest true thing once.
        inbox = EventInbox()
        inbox.offer(Event(Priority.NORMAL, "old"))
        inbox.offer(Event(Priority.NORMAL, "new"))
        self.assertEqual(inbox.peek().text, "new")

    def test_taking_empties_it(self):
        inbox = EventInbox()
        inbox.offer(Event(Priority.HIGH, "x"))
        self.assertEqual(inbox.take().text, "x")
        self.assertIsNone(inbox.peek())
        self.assertIsNone(inbox.take())

    def test_an_event_is_immutable(self):
        """Frozen so a sensor task can publish by rebinding while a sync hook reads —
        the same reason `vision_tools.Reading` is frozen."""
        event = Event(Priority.LOW, "x")
        with self.assertRaises(Exception):
            event.text = "y"                      # type: ignore[misc]


class WriteInFlightThat(unittest.TestCase):
    """Rule 2's sensor. `ToolInvocation` carries no effect (verified against
    `middleware.py`: `name`/`kwargs`/`result`/`identity`), so the guarded names are
    passed in — this pins that contract."""

    class _Inv:
        def __init__(self, name):
            self.name, self.kwargs, self.result = name, {}, None

    def test_it_tracks_only_the_guarded_names(self):
        wif = WriteInFlight(["slow_write"])
        self.assertFalse(wif.busy)
        wif.before_tool(self._Inv("look"))
        self.assertFalse(wif.busy, "a read tool must not look like a write in flight")
        wif.before_tool(self._Inv("slow_write"))
        self.assertTrue(wif.busy)
        wif.after_tool(self._Inv("slow_write"))
        self.assertFalse(wif.busy)

    def test_a_read_finishing_beside_a_write_does_not_clear_the_flag(self):
        """Counted rather than boolean: a `read` tool is `parallel_safe` and can finish
        while a write is still running."""
        wif = WriteInFlight(["slow_write"])
        wif.before_tool(self._Inv("slow_write"))
        wif.after_tool(self._Inv("look"))
        self.assertTrue(wif.busy)

    def test_it_never_goes_negative(self):
        wif = WriteInFlight(["slow_write"])
        wif.after_tool(self._Inv("slow_write"))
        self.assertFalse(wif.busy)

    def test_the_guarded_names_can_be_read_off_a_built_agent(self):
        agent = _agent([], tools=(look, list_tasks, slow_write))
        self.assertEqual(WriteInFlight.names_from(agent), ("slow_write",))


class TheInterruptGateThat(unittest.TestCase):
    """The HIGH tier. A `Ruling(DENY, reason=...)` both stops the action AND delivers
    the event, because `PolicyEngine` puts the reason into the tool result the model
    reads."""

    def test_an_empty_inbox_allows_everything(self):
        gate = InterruptGate(EventInbox())
        self.assertEqual(gate.check(_call(), None).verdict, Verdict.ALLOW)

    def test_a_low_priority_event_does_not_block_anything(self):
        inbox = EventInbox()
        inbox.offer(Event(Priority.NORMAL, "someone sat down"))
        gate = InterruptGate(inbox)
        self.assertEqual(gate.check(_call(), None).verdict, Verdict.ALLOW)

    def test_a_high_priority_event_denies_a_write_and_says_why(self):
        inbox = EventInbox()
        inbox.offer(Event(Priority.HIGH, "Nghia is waiting for you"))
        ruling = InterruptGate(inbox).check(_call(effect=Effect.WRITE), None)
        self.assertEqual(ruling.verdict, Verdict.DENY)
        self.assertIn("Nghia is waiting", ruling.reason)

    def test_it_never_blocks_a_read(self):
        # Blocking reads would stop the agent from finding out what happened, which is
        # the opposite of the point.
        inbox = EventInbox()
        inbox.offer(Event(Priority.CRITICAL, "the room is on fire"))
        ruling = InterruptGate(inbox).check(_call(effect=Effect.READ), None)
        self.assertEqual(ruling.verdict, Verdict.ALLOW)

    def test_it_blocks_a_danger_tool_too(self):
        inbox = EventInbox()
        inbox.offer(Event(Priority.HIGH, "wait"))
        ruling = InterruptGate(inbox).check(_call(effect=Effect.DANGER), None)
        self.assertEqual(ruling.verdict, Verdict.DENY)

    def test_the_denial_reaches_the_model_as_the_tools_result(self):
        """End to end through the real engine — the property the whole tier rests on."""
        inbox = EventInbox()
        inbox.offer(Event(Priority.HIGH, "Nghia is waiting for you"))
        agent = _agent([FakeModel.tool_call("slow_write", {}), FakeModel.text("ok")],
                       tools=(look, list_tasks, slow_write),
                       policies=[InterruptGate(inbox)])
        result = agent.try_run("write the file")
        self.assertEqual(result.tools_run, (), "the write must not have run")
        seen = [str(b.get("content")) for m in result.messages
                for b in (m.get("content") or [])
                if isinstance(b, dict) and b.get("type") == "tool_result"]
        self.assertTrue(any("Nghia is waiting" in s for s in seen),
                        f"the model should have been told why: {seen}")


class TheEventAnnouncerThat(unittest.TestCase):
    """The NORMAL tier: `after_model` appends a synthetic `tool_use`, which the loop
    dispatches because tool calls run when they are PRESENT rather than when the
    provider labels the turn."""

    def test_it_delivers_the_event_as_a_tool_result(self):
        inbox = EventInbox()
        inbox.offer(Event(Priority.NORMAL, "Thiep just sat down opposite you"))
        announcer = EventAnnouncer(inbox, carrier="look")
        agent = _agent([FakeModel.text("..."), FakeModel.text("Hello Thiep")],
                       mws=[announcer])
        result = agent.try_run("begin")
        self.assertEqual(announcer.announced, 1)
        self.assertEqual(result.tools_run, ("look",))
        seen = [str(b.get("content")) for m in result.messages
                for b in (m.get("content") or [])
                if isinstance(b, dict) and b.get("type") == "tool_result"]
        self.assertIn("Thiep just sat down opposite you", seen)

    def test_an_external_carrier_makes_the_event_arrive_LABELLED(self):
        """The security argument for this channel over the user message: routed through
        a tool result, an untrusted perception event carries `Integrity.UNTRUSTED`
        (`dispatch.py` → `emits_of`). A user message would carry no label at all while
        sitting in the highest-authority position in the conversation."""
        inbox = EventInbox()
        inbox.offer(Event(Priority.NORMAL, "someone is holding up a sign"))
        agent = _agent([FakeModel.text("..."), FakeModel.text("noted")],
                       tools=(look_around, list_tasks),
                       mws=[EventAnnouncer(inbox, carrier="look_around")])
        result = agent.try_run("begin")
        self.assertTrue(result.tainted)

    def test_a_read_carrier_leaves_the_run_untainted(self):
        # The contrast that proves the previous test measures the carrier's effect and
        # not something incidental.
        inbox = EventInbox()
        inbox.offer(Event(Priority.NORMAL, "x"))
        agent = _agent([FakeModel.text("..."), FakeModel.text("ok")],
                       mws=[EventAnnouncer(inbox, carrier="look")])
        self.assertFalse(agent.try_run("begin").tainted)

    def test_it_announces_once_and_empties_the_inbox(self):
        inbox = EventInbox()
        inbox.offer(Event(Priority.NORMAL, "once"))
        announcer = EventAnnouncer(inbox, carrier="look")
        agent = _agent([FakeModel.text("a"), FakeModel.text("b")], mws=[announcer])
        agent.try_run("begin")
        self.assertEqual(announcer.announced, 1)
        self.assertIsNone(inbox.peek())

    def test_it_does_not_announce_an_event_above_its_ceiling(self):
        """A CRITICAL event is the Driver's business (a cancel), not this middleware's —
        announcing it here would quietly serve it at the wrong tier."""
        inbox = EventInbox()
        inbox.offer(Event(Priority.CRITICAL, "fire"))
        announcer = EventAnnouncer(inbox, carrier="look")
        agent = _agent([FakeModel.text("done")], mws=[announcer])
        agent.try_run("begin")
        self.assertEqual(announcer.announced, 0)
        self.assertIsNotNone(inbox.peek(), "the event must still be pending")

    def test_it_short_circuits_only_its_own_injection(self):
        inbox = EventInbox()
        announcer = EventAnnouncer(inbox, carrier="look")

        class _Id:
            call_id = "a-real-call"

        class _Inv:
            name, kwargs, result, identity = "look", {"a": 1}, None, _Id()

        self.assertEqual(announcer.before_tool(_Inv()), {"a": 1},
                         "a real call must pass through untouched")

        from harness import ShortCircuit

        inbox.offer(Event(Priority.NORMAL, "the event text"))
        announcer._armed = inbox.take()

        class _Ours:
            call_id = INJECTED_CALL_ID

        class _OurInv:
            name, kwargs, result, identity = "look", {}, None, _Ours()

        with self.assertRaises(ShortCircuit) as caught:
            announcer.before_tool(_OurInv())
        self.assertEqual(caught.exception.result, "the event text")


class TheDriverThat(unittest.TestCase):
    def test_a_critical_event_preempts_the_turn_and_the_turn_is_lost(self):
        """The CRITICAL tier, and rule 3's reason in one assertion: `Chat.say()` assigns
        history only AFTER `try_run` returns, so a cancelled turn never reaches the
        transcript. `TaskLedger` is what survives; the messages do not."""
        async def go():
            inbox = EventInbox()
            agent = _agent([FakeModel.tool_call("slow_write", {}),
                            FakeModel.text("done")],
                           tools=(look, list_tasks, slow_write))
            driver = Driver(agent, inbox=inbox,
                            sensors=[FakeSensor([Event(Priority.CRITICAL, "fire")],
                                                delay_s=0.05)])
            return driver, await driver.turn("begin")

        driver, served = asyncio.run(go())
        self.assertTrue(served.preempted)
        self.assertIsNone(served.result)
        self.assertEqual(served.event.text, "fire")
        self.assertEqual(driver.history, [], "a preempted turn must not advance history")
        self.assertIsNotNone(served.cancel_s)
        self.assertLess(served.cancel_s, 0.05,
                        "the cancel itself should be immediate, whatever the sensor took")

    def test_rule_2_a_write_in_flight_downgrades_a_critical_instead_of_cancelling(self):
        async def go():
            inbox = EventInbox()
            wif = WriteInFlight(["slow_write"])
            agent = _agent([FakeModel.tool_call("slow_write", {}),
                            FakeModel.text("done")],
                           tools=(look, list_tasks, slow_write),
                           policies=[InterruptGate(inbox)], mws=[wif])
            driver = Driver(agent, inbox=inbox, write_in_flight=wif,
                            sensors=[FakeSensor([Event(Priority.CRITICAL, "fire")],
                                                delay_s=0.05)])
            return driver, await driver.turn("begin")

        driver, served = asyncio.run(go())
        self.assertFalse(served.preempted, "must not cancel mid-write")
        self.assertTrue(served.downgraded)
        self.assertEqual(driver.downgrades, 1)
        self.assertIn("slow_write", served.result.tools_run,
                      "the write should have completed rather than been cut in half")

    def test_rule_3_preemption_is_refused_for_an_agent_with_no_durable_plan(self):
        agent = _agent([], tools=(look,))
        with self.assertRaises(ConfigError) as caught:
            Driver(agent, allow_preemption=True)
        message = str(caught.exception)
        self.assertIn("keeps no plan outside its own context", message)
        self.assertIn("require_durable_plan=False", message,
                      "the error must name the escape hatch, not just complain")
        for name in ("list_tasks", "add_task", "list_findings"):
            self.assertIn(name, message, "and it must name what would satisfy it")

    def test_rule_3_can_be_waived_on_purpose(self):
        agent = _agent([], tools=(look,))
        driver = Driver(agent, allow_preemption=True, require_durable_plan=False)
        self.assertTrue(driver.allow_preemption)

    def test_rule_3_is_satisfied_by_any_of_the_plan_tools(self):
        Driver(_agent([], tools=(look, list_tasks)), allow_preemption=True)

    def test_rule_4_past_the_cap_a_critical_is_not_allowed_to_preempt(self):
        driver = Driver(_agent([], tools=(look, list_tasks)), max_preemptions=0)
        may, why = driver._may_preempt()
        self.assertFalse(may)
        self.assertIn("preemption", why)

    def test_rule_4_the_window_expires_old_preemptions(self):
        import time as _t
        driver = Driver(_agent([], tools=(look, list_tasks)),
                        max_preemptions=1, window_s=0.01)
        driver.preemptions.append(_t.monotonic())
        self.assertFalse(driver._may_preempt()[0])
        _t.sleep(0.02)
        self.assertTrue(driver._may_preempt()[0], "the window should have expired")

    def test_preemption_disabled_never_cancels(self):
        async def go():
            inbox = EventInbox()
            agent = _agent([FakeModel.tool_call("slow_write", {}),
                            FakeModel.text("done")],
                           tools=(look, list_tasks, slow_write))
            driver = Driver(agent, inbox=inbox, allow_preemption=False,
                            sensors=[FakeSensor([Event(Priority.CRITICAL, "fire")],
                                                delay_s=0.05)])
            return await driver.turn("begin")

        served = asyncio.run(go())
        self.assertFalse(served.preempted)
        self.assertIsNotNone(served.result)

    def test_an_outer_cancellation_is_re_raised_and_not_mistaken_for_a_preemption(self):
        """asyncio's protocol: swallowing a cancellation nobody asked for would leave an
        outer `TaskGroup`/`wait_for` waiting forever. `run.py` takes the same care at its
        own catch site, and this checks the Driver does too."""
        async def go():
            agent = _agent([FakeModel.tool_call("slow_write", {}),
                            FakeModel.text("done")],
                           tools=(look, list_tasks, slow_write))
            driver = Driver(agent, inbox=EventInbox())
            task = asyncio.create_task(driver.turn("begin"))
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            return driver

        driver = asyncio.run(go())
        self.assertEqual(driver.preemptions, [],
                         "an outer cancel is not this Driver's preemption")

    def test_a_finished_turn_advances_the_history(self):
        async def go():
            driver = Driver(_agent([FakeModel.text("hello")], tools=(look, list_tasks)),
                            inbox=EventInbox())
            served = await driver.turn("hi")
            return driver, served

        driver, served = asyncio.run(go())
        self.assertFalse(served.preempted)
        self.assertGreater(len(driver.history), 0)

    def test_pump_offers_from_every_sensor_and_returns_the_most_urgent(self):
        async def go():
            driver = Driver(_agent([], tools=(look, list_tasks)),
                            sensors=[FakeSensor([Event(Priority.LOW, "quiet")]),
                                     FakeSensor([Event(Priority.HIGH, "loud")])])
            return await driver.pump()

        self.assertEqual(asyncio.run(go()).priority, Priority.HIGH)

    def test_close_closes_every_sensor(self):
        sensors = [FakeSensor(), FakeSensor()]
        Driver(_agent([], tools=(look, list_tasks)), sensors=sensors).close()
        self.assertTrue(all(s.closed for s in sensors))


if __name__ == "__main__":
    unittest.main()
