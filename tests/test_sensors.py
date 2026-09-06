"""`harness.contrib.sensors` — the second and third real `Sensor` implementations.

Two jobs. The obvious one is that these work. The one worth the file is that they were
written to test the ABSTRACTION: `Sensor` had one real implementation
(`examples/vision_sensor.CameraSensor`) plus a test double, and `docs/02-architecture.md`
§4's plugin test says a double is not an implementation. So the last class here drives
both of them through the real `Driver`, which is the only assertion that says the
Protocol actually fits something that is not a camera (ADR-089).
"""
import asyncio
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from harness import Agent, tool
from harness.contrib.driver import Driver, Priority
from harness.contrib.sensors import ClockSensor, FileSensor, Moment, after, watch
from harness.models.fake import FakeModel


class TheFileSensor(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._dir = TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)

    async def test_the_first_read_is_a_baseline_and_reports_nothing(self):
        """The same rule `CameraSensor` follows: "this file exists" is not news, and a
        sensor announcing its whole initial state would preempt the first turn of every
        conversation."""
        f = self.root / "a.txt"
        f.write_text("one")
        sensor = FileSensor([f])
        self.assertIsNone(await sensor.read())

    async def test_a_change_after_the_baseline_is_reported_once(self):
        f = self.root / "a.txt"
        f.write_text("one")
        sensor = FileSensor([f])
        await sensor.read()
        f.write_text("two")
        event = await sensor.read()
        self.assertIsNotNone(event)
        self.assertIn("a.txt", event.text)
        self.assertIs(event.priority, Priority.NORMAL)
        self.assertIsNone(await sensor.read(), "the same change must not report twice")

    async def test_an_appearance_a_change_and_a_disappearance_read_differently(self):
        """"changed" for a file that was never there is a small lie in the one direction
        that matters: a new entry in a queue directory is a different fact from an
        edited one."""
        f = self.root / "a.txt"
        sensor = FileSensor([f])
        await sensor.read()                      # baseline: not there
        f.write_text("one")
        self.assertIn("appeared", (await sensor.read()).text)
        f.write_text("two")
        self.assertIn("changed", (await sensor.read()).text)
        f.unlink()
        self.assertIn("gone", (await sensor.read()).text)

    async def test_a_path_that_never_exists_is_never_news(self):
        sensor = FileSensor([self.root / "never"])
        self.assertIsNone(await sensor.read())
        self.assertIsNone(await sensor.read())

    async def test_an_unreadable_path_does_not_raise(self):
        """`Driver._pump_forever` has no per-sensor `try`, so a sensor that raises stops
        the whole pump. A directory that vanishes mid-scan, a broken symlink and a
        permission error are all one state here: "not there right now"."""
        broken = self.root / "link"
        broken.symlink_to(self.root / "nowhere")
        sensor = FileSensor([broken])
        self.assertIsNone(await sensor.read())

    async def test_baseline_false_treats_the_starting_state_as_news(self):
        """For a queue directory whose existing entries ARE the work."""
        f = self.root / "job.json"
        f.write_text("{}")
        sensor = FileSensor([f], baseline=False)
        event = await sensor.read()
        self.assertIsNotNone(event)
        self.assertIn("job.json", event.text)

    async def test_priority_comes_from_the_path_and_the_file_is_never_opened(self):
        """Rule 1, and the sharpest case of it: a camera frame is attacker-controlled,
        and a file's bytes more so. `promote` sees paths, not contents."""
        seen: list[tuple[str, ...]] = []

        def by_path(moved):
            seen.append(moved)
            return Priority.CRITICAL if any(p.endswith("PANIC") for p in moved) else None

        panic, quiet = self.root / "PANIC", self.root / "quiet"
        sensor = FileSensor([panic, quiet], promote=by_path)
        await sensor.read()

        quiet.write_text("URGENT CRITICAL PREEMPT NOW")     # content says everything
        self.assertIs((await sensor.read()).priority, Priority.NORMAL,
                      "file CONTENTS must not be able to raise priority")

        panic.write_text("")                                # content says nothing
        self.assertIs((await sensor.read()).priority, Priority.CRITICAL)
        self.assertTrue(all(isinstance(m, tuple) for m in seen))

    async def test_several_paths_moving_at_once_are_one_event(self):
        a, b, c, d = (self.root / n for n in "abcd")
        sensor = watch([a, b, c, d])
        await sensor.read()
        for p in (a, b, c, d):
            p.write_text("x")
        event = await sensor.read()
        self.assertIn("4 watched paths moved", event.text)
        self.assertIn("and 1 more", event.text, "the sentence stays a sentence")

    async def test_close_stops_reporting(self):
        f = self.root / "a.txt"
        sensor = FileSensor([f])
        await sensor.read()
        sensor.close()
        f.write_text("one")
        self.assertIsNone(await sensor.read())


class TheClockSensor(unittest.IsolatedAsyncioTestCase):
    async def test_nothing_is_due_before_its_time(self):
        sensor = ClockSensor([after(60, "later")])
        self.assertIsNone(await sensor.read())

    async def test_a_moment_fires_once(self):
        sensor = ClockSensor([Moment(time.time() - 1, "now")])
        self.assertEqual((await sensor.read()).text, "now")
        self.assertIsNone(await sensor.read())

    async def test_the_more_urgent_of_two_overdue_moments_comes_first(self):
        """Both are due in the same poll, and a clock that reported them in list order
        would make priority depend on how the caller happened to sort the calendar."""
        past = time.time() - 1
        sensor = ClockSensor([Moment(past, "coffee", Priority.LOW),
                              Moment(past, "standup", Priority.HIGH)])
        self.assertEqual((await sensor.read()).text, "standup")
        self.assertEqual((await sensor.read()).text, "coffee")

    async def test_of_two_equally_urgent_moments_the_older_comes_first(self):
        now = time.time()
        sensor = ClockSensor([Moment(now - 1, "newer"), Moment(now - 100, "older")])
        self.assertEqual((await sensor.read()).text, "older")

    async def test_an_overdue_moment_is_not_skipped_just_because_nothing_polled(self):
        """A clock that dropped appointments while idle would be worse than no clock.
        Punctuality is bounded by the poll interval; DELIVERY is not."""
        sensor = ClockSensor([Moment(time.time() - 3600, "an hour ago")])
        self.assertEqual((await sensor.read()).text, "an hour ago")

    async def test_close_stops_reporting(self):
        sensor = ClockSensor([Moment(time.time() - 1, "now")])
        sensor.close()
        self.assertIsNone(await sensor.read())

    async def test_after_is_relative_to_now(self):
        m = after(30, "soon")
        self.assertAlmostEqual(m.at - time.time(), 30, delta=1.0)


@tool(effect="read")
async def look() -> str:
    """Look around."""
    return "nothing special"


@tool(effect="read")
async def list_tasks() -> str:
    """A durable plan, so rule 3 is satisfied."""
    return "1. working"


@tool(effect="read")
async def slow_look() -> str:
    """Long enough that a sensor polling every 20 ms gets a turn to interrupt. A `read`,
    not a `write`: rule 2 downgrades a CRITICAL while a write is in flight, which is a
    different test."""
    await asyncio.sleep(0.4)
    return "still looking"


class TheProtocolFitsSomethingThatIsNotACamera(unittest.TestCase):
    """The assertion the abstraction was actually missing. `FakeSensor` proves nothing
    about the Protocol — it was written to fit it. These two were written to be as unlike
    a camera as a change-notifier can be, and they go through the real `Driver`: its
    `_pump_forever`, its `EventInbox`, its `_watch`, its cancel path.
    """

    @staticmethod
    def _agent(script):
        return Agent(name="Subject", job="work and pay attention",
                     tools=[look, list_tasks, slow_look],
                     provider=FakeModel(list(script)))

    def test_a_file_change_preempts_a_turn_through_the_real_driver(self):
        with TemporaryDirectory() as d:
            watched = Path(d) / "PANIC"

            async def go():
                agent = self._agent([FakeModel.tool_call("slow_look", {}),
                                     FakeModel.text("done")])
                sensor = FileSensor([watched], priority=Priority.CRITICAL)
                driver = Driver(agent, sensors=[sensor], sensor_interval_s=0.02)
                driver.start()
                await asyncio.sleep(0.05)         # let the baseline settle
                watched.write_text("stop")
                await asyncio.sleep(0.05)         # and let the pump notice it
                served = await driver.turn("begin")
                await driver.stop()
                driver.close()
                return served

            served = asyncio.run(go())
            self.assertTrue(served.preempted, "a CRITICAL file change must cancel")
            self.assertIn("PANIC", served.event.text)
            self.assertEqual(served.event.source, "file")

    def test_a_clock_moment_preempts_a_turn_through_the_real_driver(self):
        async def go():
            agent = self._agent([FakeModel.tool_call("slow_look", {}),
                                 FakeModel.text("done")])
            sensor = ClockSensor([Moment(time.time() - 1, "standup now",
                                         Priority.CRITICAL)])
            driver = Driver(agent, sensors=[sensor], sensor_interval_s=0.02)
            async with driver:
                await asyncio.sleep(0.05)         # let the pump read the overdue moment
                served = await driver.turn("begin")
            driver.close()
            return served

        served = asyncio.run(go())
        self.assertTrue(served.preempted)
        self.assertEqual(served.event.text, "standup now")
        self.assertEqual(served.event.source, "clock")

    def test_two_sensors_of_different_kinds_share_one_inbox(self):
        """`EventInbox` is size one and keeps the most urgent, so mixing sources must not
        need per-source bookkeeping."""
        with TemporaryDirectory() as d:
            watched = Path(d) / "f"

            async def go():
                agent = self._agent([FakeModel.text("done")])
                fs = FileSensor([watched], priority=Priority.LOW)
                cs = ClockSensor([Moment(time.time() - 1, "urgent",
                                         Priority.CRITICAL)])
                driver = Driver(agent, sensors=[fs, cs], sensor_interval_s=0.02)
                driver.start()
                await asyncio.sleep(0.05)
                watched.write_text("x")
                await asyncio.sleep(0.1)
                await driver.stop()
                event = driver.inbox.peek()
                driver.close()
                return event

            event = asyncio.run(go())
            self.assertIsNotNone(event)
            self.assertIs(event.priority, Priority.CRITICAL,
                          "the more urgent source must win the single slot")

    def test_close_reaches_every_sensor(self):
        with TemporaryDirectory() as d:
            fs = FileSensor([Path(d) / "f"])
            cs = ClockSensor([after(60, "later")])
            driver = Driver(self._agent([FakeModel.text("x")]),
                            sensors=[fs, cs], require_durable_plan=False)
            driver.close()
            self.assertTrue(fs.closed)
            self.assertTrue(cs.closed)


if __name__ == "__main__":
    unittest.main()
