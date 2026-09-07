"""`examples/seeing_hearing.py` — eyes and ears on one `Driver`.

Every test here pins something that was MEASURED while wiring the two halves together
and that neither half could have told you on its own. Three of them contradict what
looked obvious beforehand, which is exactly why they are tests: a future change to
either side must break something visible rather than quietly flip a trade-off.
"""
import asyncio
import unittest

from harness import Effect
from harness.memory.inmemory import InMemoryStore
from harness.models.fake import FakeModel

from harness.contrib.driver import Event, EventAnnouncer, EventInbox, Priority
from audio_tools import CHUNK_BYTES, FakeTranscriber, Microphone, Utterance
from seeing_hearing import CARRIER, HEARING, build
from vision_tools import Body, Camera, Face, FakeDetector, IdentityLedger

THIEP = (140, 70, 240, 240)


class _Lens:
    def isOpened(self): return True
    def set(self, *a): return True
    def read(self):
        import numpy
        return True, numpy.zeros((480, 640, 3), dtype=numpy.uint8)
    def release(self): ...


class _Mic:
    def read(self, frames): return b"\x00" * CHUNK_BYTES, False
    def stop(self): ...
    def close(self): ...


def _detector():
    return FakeDetector(faces=(Face(box=THIEP),), bodies=(Body("đang ngồi"),),
                        scene=(("home office", 0.7),),
                        embeddings={THIEP: (1.0, 0.0, 0.1)})


def _build(script, say=None, **kw):
    return build(detector=_detector(), store=kw.pop("store", InMemoryStore()),
                 camera=Camera(capture=_Lens()), microphone=Microphone(stream=_Mic()),
                 transcriber=FakeTranscriber(script=kw.pop("heard", [])),
                 provider=FakeModel(script), say=say or (lambda _l: None), **kw)


class TheWholeCompanionThat(unittest.TestCase):

    def test_answers_a_spoken_question_using_its_eyes(self):
        """The point of the whole file: speech becomes the turn, the agent looks, and
        the answer comes back as text."""
        lines: list[str] = []

        async def go():
            store = InMemoryStore()
            await IdentityLedger(store).enroll("Thiep", (1.0, 0.0, 0.1))
            chat = _build([FakeModel.tool_call("look", {}),
                           FakeModel.text("Là anh Thiep, đang ngồi đối diện tôi.")],
                          say=lines.append, store=store,
                          heard=[(Utterance("Trước mặt bạn là ai?", True),)])
            await chat.run(turns=1)
            chat.sensor.close()
            chat.driver.close()
            return chat

        chat = asyncio.run(go())
        self.assertEqual(lines, ["bạn: Trước mặt bạn là ai?",
                                 "Bạn đồng hành: Là anh Thiep, đang ngồi đối diện tôi."])
        self.assertEqual(chat.turns, 1)


class TheWiringThat(unittest.TestCase):

    def test_puts_the_microphone_LAST_so_speech_wins_a_tie(self):
        """`EventInbox` holds one event and an equal-priority offer replaces the older
        one, while `Driver.pump()` reads sensors in list order — so the last sensor wins
        a tie. Both `Salience` tables default to NORMAL, which makes every camera-versus-
        microphone collision a tie. Somebody speaking beats somebody walking past."""
        chat = _build([FakeModel.text("...")])
        self.assertEqual([type(s).__name__ for s in chat.driver.sensors],
                         ["CameraSensor", "MicSensor"])
        chat.sensor.close()
        chat.driver.close()

    def test_the_tie_break_it_relies_on_really_works_that_way(self):
        """The ordering above is only correct because of this. Pinned separately so a
        change to `EventInbox.offer` breaks here, where the reason is written down."""
        for order in (("camera", "mic"), ("mic", "camera")):
            inbox = EventInbox()
            for who in order:
                inbox.offer(Event(Priority.NORMAL, who))
            self.assertEqual(inbox.peek().text, order[-1],
                             "the LAST equal-priority offer must win")

    def test_reuses_the_eyes_own_external_tool_as_the_event_carrier(self):
        """Adding ears needed no new tool and no new grant: `look` is already `external`,
        which is what makes an event arrive as `Integrity.UNTRUSTED` rather than as an
        instruction."""
        chat = _build([FakeModel.text("...")])
        carrier = next(t for t in chat.driver.agent.toolset if t.name == CARRIER)
        self.assertIs(carrier.effect, Effect.EXTERNAL)
        chat.sensor.close()
        chat.driver.close()

    def test_gives_the_agent_a_plan_tool_that_is_not_a_write_sink(self):
        """Rule 3 wants a durable plan; `VisionProfile._refuse_unreviewed_sinks` wants no
        unreviewed sink beside a camera. `TaskLedger`'s `list_tasks` is `read` and
        satisfies both — its siblings are `write` and would have tripped the refusal."""
        chat = _build([FakeModel.text("...")])
        by_name = {t.name: t for t in chat.driver.agent.toolset}
        self.assertIn("list_tasks", by_name)
        self.assertIs(by_name["list_tasks"].effect, Effect.READ)
        self.assertNotIn("add_task", by_name)
        chat.sensor.close()
        chat.driver.close()

    def test_tells_the_agent_it_can_hear_with_a_constant_string(self):
        """`extra_instructions` lands in `job=`, the cache-linted prefix, and `Chat`
        rebuilds the agent every turn — anything varying there raises
        `NonDeterministicPromptError` mid-conversation rather than at startup."""
        chat = _build([FakeModel.text("...")])
        self.assertIn("Nghe được qua micro", chat.driver.agent.job)
        self.assertIn(HEARING.strip().splitlines()[0], chat.driver.agent.job)
        chat.sensor.close()
        chat.driver.close()


class ThePrivacyTradeThat(unittest.TestCase):
    """The measurement that cost this design its intended default.

    Going in, the plan was that a room with a camera AND a microphone is more
    confidential than either alone, so `private=True` should become the default. It
    cannot be, and these two tests are why.
    """

    @staticmethod
    async def _event_reaches_the_model(private):
        from harness import Agent
        from vision_profile import VisionProfile

        inbox = EventInbox()
        agent = Agent(name="X", job="j",
                      provider=FakeModel([FakeModel.tool_call("look", {}),
                                          FakeModel.text("a"),
                                          FakeModel.text("b")])).with_profile(
            VisionProfile(detector=_detector(), store=InMemoryStore(), private=private,
                          camera=Camera(capture=_Lens()),
                          extra_middleware=[EventAnnouncer(inbox, carrier=CARRIER)]))
        inbox.offer(Event(Priority.NORMAL, "somebody just spoke"))
        result = await agent.atry_run("look then answer")
        seen = [str(b.get("content")) for m in result.messages
                for b in (m.get("content") or [])
                if isinstance(b, dict) and b.get("type") == "tool_result"]
        return any("somebody just spoke" in s for s in seen), seen

    def test_events_arrive_while_the_camera_is_not_declared_sensitive(self):
        got, _ = asyncio.run(self._event_reaches_the_model(False))
        self.assertTrue(got)

    def test_private_true_stops_them_arriving_at_all_once_it_has_looked(self):
        """`private=True` puts `look` in `sensitive=`, so the first real look raises the
        run to SECRET; `external`'s own `max_confidentiality` is PUBLIC, so `check_flow`
        then denies EVERY external call — the injected carrier included. Not a bug in
        either half: the flow lattice doing its job, and an operator has to choose."""
        got, seen = asyncio.run(self._event_reaches_the_model(True))
        self.assertFalse(got, "if this passes, the trade-off changed — update the docs")
        self.assertTrue(any("denied by policy" in s and "secret" in s for s in seen),
                        f"expected a flow denial, got {seen}")


class TheNameFeedbackThat(unittest.TestCase):
    """What the eyes know making the ears better — the one thing this combination buys
    that neither half could."""

    def test_the_ledger_hands_over_exactly_what_seeds_the_asr(self):
        async def go():
            store = InMemoryStore()
            ledger = IdentityLedger(store)
            await ledger.enroll("Thiep", (1.0, 0.0, 0.1))
            await ledger.enroll("Nghia", (0.0, 1.0, 0.1))
            return await ledger.names()

        self.assertEqual(asyncio.run(go()), ("Nghia", "Thiep"))


if __name__ == "__main__":
    unittest.main()
