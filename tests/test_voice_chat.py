"""`examples/voice_chat.py` — speech in, text out.

The property this file exists to pin is the routing rule, because getting it wrong is
not a cosmetic bug: an utterance that becomes a turn carries the USER's authority, and
one that becomes an event arrives as untrusted tool output. `driver.py` says collapsing
the second into the first is the failure its whole design avoids, so "busy means event"
is tested from both ends — the queue stays empty, and an event really is produced.
"""
import asyncio
import unittest

from harness import Agent, Effect, tool, with_middleware
from harness.models.fake import FakeModel

from harness.contrib.driver import Driver, EventAnnouncer, EventInbox, Priority
from audio_sensor import Change, MicSensor
from audio_tools import CHUNK_BYTES, FakeTranscriber, Microphone, Utterance
from voice_chat import PREEMPTED, VoiceChat


class _Cap:
    def read(self, frames): return b"\x00" * CHUNK_BYTES, False
    def stop(self): ...
    def close(self): ...


@tool(effect=Effect.EXTERNAL)
async def listen_around() -> str:
    "The carrier an event rides in on."
    return "nothing much"


@tool(effect=Effect.READ)
async def list_tasks() -> str:
    "A durable plan, so preemption is permitted."
    return "1. talking"


def _sensor(script):
    return MicSensor(microphone=Microphone(stream=_Cap()), use_thread=False,
                     transcriber=FakeTranscriber(script=script))


def _agent(replies, inbox):
    return with_middleware(
        Agent(name="Tai", job="trò chuyện", tools=[listen_around, list_tasks],
              provider=FakeModel([FakeModel.text(r) for r in replies])),
        EventAnnouncer(inbox, carrier="listen_around"))


class _FakeDriver:
    """Stands in for `Driver` where the test is about the LOOP rather than about the
    agent — a preempted turn, or a reply that came back empty."""

    class agent:
        name = "Tai"

    def __init__(self, served):
        self.served, self.started, self.stopped = served, False, False

    def start(self): self.started = True

    async def stop(self): self.stopped = True

    async def turn(self, text): return self.served


class WhenTheAgentIsIdleThat(unittest.TestCase):

    def test_what_you_said_becomes_a_turn_and_the_answer_comes_back_as_text(self):
        lines: list[str] = []

        async def go():
            inbox = EventInbox()
            sensor = _sensor([(Utterance("Chào bạn.", True),),
                              (Utterance("Mai họp mấy giờ?", True),)])
            chat = VoiceChat(driver=Driver(_agent(["Chào anh.", "Chín giờ."], inbox),
                                           sensors=[sensor], inbox=inbox),
                             sensor=sensor, say=lines.append)
            await chat.run(turns=2)
            sensor.close()
            return chat

        chat = asyncio.run(go())
        self.assertEqual(lines, ["bạn: Chào bạn.", "Tai: Chào anh.",
                                 "bạn: Mai họp mấy giờ?", "Tai: Chín giờ."])
        self.assertEqual(chat.turns, 2)
        self.assertEqual(chat.left_as_events, 0)

    def test_an_utterance_taken_as_a_turn_does_not_ALSO_become_an_event(self):
        """No double delivery. The same sentence arriving as the message and again as a
        perception is how an agent ends up answering itself."""
        async def go():
            sensor = _sensor([(Utterance("Chào bạn.", True),)])
            chat = VoiceChat(driver=_FakeDriver(None), sensor=sensor)
            sensor.on_turn = chat.offer
            event = await sensor.read()
            sensor.close()
            return event, sensor.turns_taken, chat._queue.qsize()

        event, taken, queued = asyncio.run(go())
        self.assertIsNone(event, "it was the user's turn, not a perception")
        self.assertEqual(taken, 1)
        self.assertEqual(queued, 1)


class WhenTheAgentIsBusyThat(unittest.TestCase):

    def test_what_you_said_becomes_an_event_and_never_a_turn(self):
        async def go():
            sensor = _sensor([(Utterance("Xen ngang một câu.", True),)])
            chat = VoiceChat(driver=_FakeDriver(None), sensor=sensor)
            sensor.on_turn = chat.offer
            chat._busy = True
            event = await sensor.read()
            sensor.close()
            return event, chat

        event, chat = asyncio.run(go())
        self.assertIsNotNone(event, "it still has to reach the agent — as a perception")
        self.assertIn("Xen ngang một câu.", event.text)
        self.assertTrue(chat._queue.empty(), "a busy agent must not be handed a turn")
        self.assertEqual(chat.left_as_events, 1)

    def test_that_event_still_cannot_preempt_by_default(self):
        """Interrupting by talking is a `task.cancel()` on the running turn. Somebody
        speaking while the agent works is normally worth reading at the next model call
        and nothing more."""
        async def go():
            sensor = _sensor([(Utterance("DỪNG LẠI NGAY!", True),)])
            chat = VoiceChat(driver=_FakeDriver(None), sensor=sensor)
            sensor.on_turn = chat.offer
            chat._busy = True
            event = await sensor.read()
            sensor.close()
            return event
        self.assertLessEqual(asyncio.run(go()).priority, Priority.NORMAL)


class TheSpeakerFilterThat(unittest.TestCase):
    """`speakers=` is off by default, and what that costs is stated rather than hidden."""

    def test_lets_a_named_speaker_take_a_turn(self):
        chat = VoiceChat(driver=None, sensor=None, speakers=("spk_1",))
        self.assertTrue(chat.offer(Change(speaker="spk_1", words=2), "chào"))

    def test_leaves_everyone_else_as_an_event(self):
        chat = VoiceChat(driver=None, sensor=None, speakers=("spk_1",))
        self.assertFalse(chat.offer(Change(speaker="spk_9", words=2), "chào"))
        self.assertFalse(chat.offer(Change(words=2), "chào"),
                         "an unlabelled speaker is not the named one")
        self.assertEqual(chat.left_as_events, 2)

    def test_is_empty_by_default_which_means_anyone_in_earshot(self):
        """Including a television. Documented rather than defended: a single microphone
        assistant that only answers a known voice needs `speaker_label` to arrive on the
        live path, and whether it does is question 1 of `tests/audio_probe.py`."""
        chat = VoiceChat(driver=None, sensor=None)
        self.assertTrue(chat.offer(Change(words=2), "chào"))


class TheLoopThat(unittest.TestCase):

    def test_survives_a_preempted_turn_instead_of_crashing_on_it(self):
        """`Served.result` is `None` when the turn was cancelled — precisely the event
        the loop exists to handle, so assuming a result would fail on the interesting
        case only."""
        class Served:
            result = None

        lines: list[str] = []

        async def go():
            sensor = _sensor([])
            chat = VoiceChat(driver=_FakeDriver(Served()), sensor=sensor,
                             say=lines.append)
            chat._queue.put_nowait("làm gì đó đi")
            await chat.run(turns=1)
            sensor.close()

        asyncio.run(go())
        self.assertEqual(lines, ["bạn: làm gì đó đi", f"Tai: {PREEMPTED}"])

    def test_says_something_rather_than_nothing_for_an_empty_reply(self):
        class Served:
            class result:
                text = "   "

        lines: list[str] = []

        async def go():
            chat = VoiceChat(driver=_FakeDriver(Served()), sensor=_sensor([]),
                             say=lines.append)
            chat._queue.put_nowait("ừ")
            await chat.run(turns=1)

        asyncio.run(go())
        self.assertEqual(lines[-1], "Tai: (không nói gì)")

    def test_starts_the_sensor_pump_so_it_can_hear_while_nothing_is_happening(self):
        """Without `driver.start()` the microphone is drained only during a turn — an
        agent that can hear you only while it is already answering."""
        async def go():
            chat = VoiceChat(driver=_FakeDriver(None), sensor=_sensor([]))
            chat._queue.put_nowait("chào")
            await chat.run(turns=1)
            return chat.driver
        driver = asyncio.run(go())
        self.assertTrue(driver.started)
        self.assertTrue(driver.stopped, "and it stops the pump on the way out")

    def test_unhooks_itself_from_the_sensor_when_it_finishes(self):
        """A sensor left holding a dead loop's `offer` would swallow every later
        utterance into a queue nobody reads."""
        async def go():
            sensor = _sensor([])
            chat = VoiceChat(driver=_FakeDriver(None), sensor=sensor)
            chat._queue.put_nowait("chào")
            await chat.run(turns=1)
            return sensor.on_turn
        self.assertIsNone(asyncio.run(go()))


class TheLiveWiringThat(unittest.TestCase):
    """`live()` is unrun end to end — no microphone, no key, no route to Google from
    here. What CAN be executed is everything up to the point where it needs a model, and
    that is worth pinning: it proves the tools, the carrier and the driver assemble."""

    def test_gets_as_far_as_needing_a_model_and_says_so_in_a_sentence(self):
        import os
        from unittest import mock

        from harness import ConfigError
        from voice_chat import live

        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConfigError) as caught:
                live()
        self.assertIn("harness setup", str(caught.exception),
                      "the library's own message, not a traceback")


if __name__ == "__main__":
    unittest.main()
