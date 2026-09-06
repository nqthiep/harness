"""ADR-120: what the cheap perception loop is allowed to notice.

The two-rate structure was already there — `Driver` reads sensors on a cheap clock and
the model engages only on an `Event`. What is tested here is the GATE between the two:
which differences are big enough to spend a model call on, that each aspect settles on
its own clock, and that widening the gate did not hand a frame's own content the power
to set priority.

Every test that asserts silence is paired with something that asserts noise. A probe
that reports "no event" and cannot produce ANY event is measuring nothing, and this file
was written after exactly that mistake was caught in the probe that motivated it.
"""
import asyncio
import unittest

from harness.contrib.driver import Priority
from harness.memory.inmemory import InMemoryStore

from vision_sensor import (ATTENDABLE, CameraSensor, Change, DISTANCE, POSTURE, PRESENCE,
                           SCENE, Salience, _State, _transitions, render)
from vision_tools import (Body, Camera, DISTANCE_BANDS, Face, FakeDetector,
                          IdentityLedger, PerceptionBuffer, distance_of, nearer_than)

THIEP, NGHIA = (1.0, 0.0, 0.1), (0.0, 1.0, 0.1)
#: Boxes chosen so each lands in a DIFFERENT band of a 640px-wide frame, checked by
#: `TheBandsAreTheOnesTheTestsAssume` below rather than trusted.
FAR, TALK, NEAR = (10, 10, 60, 60), (10, 10, 130, 130), (10, 10, 300, 300)


class _Cap:
    def isOpened(self) -> bool:
        return True

    def set(self, *_a) -> bool:
        return True

    def read(self):
        import numpy
        return True, numpy.zeros((480, 640, 3), dtype=numpy.uint8)

    def release(self) -> None:
        pass


class Scene:
    """A scripted world. `at()` sets what the detector will report next; `read()` runs
    the sensor `stable_reads` times so a settled change actually commits."""

    def __init__(self, *, attends=None, salience=None, stable_reads=2, scene=(("home office", 0.9),)):
        self.ledger = IdentityLedger(InMemoryStore())
        self.detector = FakeDetector(scene=scene)
        self.sensor = CameraSensor(camera=Camera(capture=_Cap()), detector=self.detector,
                                   ledger=self.ledger, buffer=PerceptionBuffer(),
                                   salience=salience, attends=attends,
                                   stable_reads=stable_reads, use_thread=False)

    async def enrol(self):
        await self.ledger.enroll("Thiep", THIEP)
        await self.ledger.enroll("Nghia", NGHIA)

    def at(self, *people, scene=None):
        """`people` are `(box, embedding, posture)` triples."""
        self.detector.faces = tuple(Face(box=b) for b, _e, _p in people)
        self.detector.bodies = tuple(Body(p) for _b, _e, p in people)
        self.detector.embeddings = {b: e for b, e, _p in people}
        if scene is not None:
            self.detector.scene = scene

    async def read(self, times=2):
        """Every event produced across `times` observations, so a caller can assert on
        both 'exactly one fired' and 'nothing fired'."""
        return [e for e in [await self.sensor.read() for _ in range(times)]
                if e is not None]

    async def settle(self, *people, scene=None):
        """Advance to a new world and swallow whatever it produces — for establishing a
        baseline that the assertion after it can diff against."""
        self.at(*people, scene=scene)
        await self.read()


def run(coro):
    return asyncio.run(coro)


class TheBandsAreTheOnesTheTestsAssume(unittest.TestCase):
    """Fixtures above encode band boundaries. If `DISTANCE_CUTS` moves, every test in
    this file starts asserting something other than what it says — so the mapping is
    checked here rather than assumed there."""

    def test_the_three_fixtures_land_in_three_different_bands(self):
        bands = [distance_of(Face(box=b), (640, 480)) for b in (FAR, TALK, NEAR)]
        self.assertEqual(bands, list(DISTANCE_BANDS))

    def test_nearer_than_orders_them_and_refuses_to_order_the_unknown(self):
        self.assertTrue(nearer_than(DISTANCE_BANDS[2], DISTANCE_BANDS[0]))
        self.assertFalse(nearer_than(DISTANCE_BANDS[0], DISTANCE_BANDS[2]))
        self.assertFalse(nearer_than("", DISTANCE_BANDS[0]),
                         "no frame size is 'don't know', not 'furthest away'")
        self.assertFalse(nearer_than(DISTANCE_BANDS[0], ""))


class EachAspectSettlesOnItsOwnClock(unittest.TestCase):
    """The defect that had to be fixed before the gate could be widened at all."""

    def test_a_stable_arrival_survives_an_unrelated_fields_jitter(self):
        """Measured on the shipped code: ten observations with Thiep present in all ten,
        only the unknown count flickering 1,2,1,2, produced ZERO events. Whole-state
        debounce meant any jittery field starved every other one."""
        s = CameraSensor.__new__(CameraSensor)
        s._committed = _State(blind=False)
        s._pending, s.stable_reads, s.suppressed = {}, 2, 0
        s.attends = frozenset({PRESENCE})

        fired = [s._commit(_State(known=frozenset({"Thiep"}), unknown=u, blind=False))
                 for u in (1, 2) * 5]
        arrivals = [c for c in fired if c is not None and c.arrived]
        self.assertEqual(len(arrivals), 1, "Thiep was present in all ten observations")
        self.assertEqual(arrivals[0].arrived, ("Thiep",))

    def test_and_the_jittery_field_itself_still_never_commits(self):
        """The other half: per-field debounce must not become no debounce. The flicker
        that starved the arrival must still be suppressed on its OWN field."""
        s = CameraSensor.__new__(CameraSensor)
        s._committed = _State(blind=False)
        s._pending, s.stable_reads, s.suppressed = {}, 2, 0
        s.attends = frozenset({PRESENCE})

        for u in (1, 2) * 5:
            s._commit(_State(known=frozenset({"Thiep"}), unknown=u, blind=False))
        self.assertEqual(s._committed.unknown, 0,
                         "a value never seen twice in a row must never commit")

    def test_a_flicker_is_still_a_flicker(self):
        """ADR-081's original guarantee, re-measured through the new code path: one
        dropped frame must not read as a departure followed by an arrival."""
        w = Scene()

        async def go():
            await w.enrol()
            await w.settle()                                   # baseline: empty room
            w.at((NEAR, THIEP, "đang ngồi"))
            first = await w.sensor.read()                      # 1/2
            w.at()                                             # frame dropped
            second = await w.sensor.read()
            w.at((NEAR, THIEP, "đang ngồi"))
            third = await w.sensor.read()                      # 1/2 again
            fourth = await w.sensor.read()                     # 2/2, confirmed
            return first, second, third, fourth

        first, second, third, fourth = run(go())
        self.assertEqual((first, second, third), (None, None, None))
        self.assertIsNotNone(fourth)
        self.assertIn("vừa xuất hiện", fourth.text)


class ThePhenomenaTheLoopNowWakesFor(unittest.TestCase):
    """The four measured silences from ADR-120's table, each now an event — and each
    paired with the steady state that must stay silent."""

    def test_standing_up_is_an_event_and_sitting_still_is_not(self):
        w = Scene()

        async def go():
            await w.enrol()
            await w.settle((NEAR, THIEP, "đang ngồi"))
            quiet = await w.read()
            w.at((NEAR, THIEP, "đang đứng"))
            return quiet, await w.read()

        quiet, moved = run(go())
        self.assertEqual(quiet, [], "an unchanged scene must cost nothing")
        self.assertEqual(len(moved), 1)
        self.assertIn("Thiep chuyển từ đang ngồi sang đang đứng", moved[0].text)

    def test_crossing_a_distance_band_is_an_event_with_a_direction(self):
        w = Scene()

        async def go():
            await w.enrol()
            await w.settle((FAR, THIEP, "đang đứng"))
            w.at((NEAR, THIEP, "đang đứng"))
            closer = await w.read()
            w.at((FAR, THIEP, "đang đứng"))
            return closer, await w.read()

        closer, further = run(go())
        self.assertEqual(len(closer), 1)
        self.assertIn("Thiep tiến lại gần", closer[0].text)
        self.assertEqual(len(further), 1)
        self.assertIn("Thiep lùi ra xa", further[0].text)

    def test_a_shuffle_inside_one_band_is_not_movement(self):
        """The quantisation earning its keep: without bands, every frame's pixel jitter
        would be an approach and the expensive rate would never be idle."""
        w = Scene()
        wider = (NEAR[0] + 5, NEAR[1] + 5, NEAR[2] + 20, NEAR[3] + 20)
        self.assertEqual(distance_of(Face(box=NEAR), (640, 480)),
                         distance_of(Face(box=wider), (640, 480)), "same band, by design")

        async def go():
            await w.enrol()
            await w.settle((NEAR, THIEP, "đang đứng"))
            w.at((wider, THIEP, "đang đứng"))
            return await w.read()

        self.assertEqual(run(go()), [])

    def test_the_scene_changing_is_an_event(self):
        w = Scene()

        async def go():
            await w.enrol()
            await w.settle((NEAR, THIEP, "đang đứng"))
            w.at((NEAR, THIEP, "đang đứng"), scene=(("fire", 0.9), ("smoke", 0.8)))
            return await w.read()

        fired = run(go())
        self.assertEqual(len(fired), 1)
        self.assertIn("fire", fired[0].text)
        self.assertIn("không còn thấy home office", fired[0].text)

    def test_a_low_confidence_label_is_below_the_floor_and_says_nothing(self):
        """A classifier's long tail churns frame to frame. Without the floor, 'the scene
        changed' fires continuously and means nothing."""
        w = Scene()

        async def go():
            await w.enrol()
            await w.settle((NEAR, THIEP, "đang đứng"))
            w.at((NEAR, THIEP, "đang đứng"),
                 scene=(("home office", 0.9), ("cat", 0.2), ("bicycle", 0.1)))
            return await w.read()

        self.assertEqual(run(go()), [])

    def test_an_unknown_person_moving_closer_still_wakes_the_loop(self):
        """`postures`/`distances` need a name to key on, so an unknown face contributes
        to `nearest` instead — 'somebody is now very close' is worth waking for whether
        or not you know who they are."""
        w = Scene()
        stranger = (0.5, 0.5, 0.9)

        async def go():
            await w.enrol()
            await w.settle((FAR, stranger, "đang đứng"))
            w.at((NEAR, stranger, "đang đứng"))
            return await w.read()

        fired = run(go())
        self.assertEqual(len(fired), 1)
        self.assertIn("gần nhất tiến lại gần hơn", fired[0].text)


class AnArrivalIsNotAlsoAPostureChange(unittest.TestCase):
    def test_walking_in_reports_the_arrival_only(self):
        """A posture that appears because its owner did is part of the arrival, not a
        second event on top of it."""
        w = Scene()

        async def go():
            await w.enrol()
            await w.settle()
            w.at((NEAR, THIEP, "đang đứng"))
            return await w.read()

        fired = run(go())
        self.assertEqual(len(fired), 1)
        self.assertIn("Thiep vừa xuất hiện", fired[0].text)
        self.assertNotIn("chuyển từ", fired[0].text)
        self.assertEqual(fired[0].priority, Priority.NORMAL, "arrival's tier, not LOW")

    def test_a_departure_does_not_also_claim_the_nearest_person_moved(self):
        """`nearest` is the band of WHOEVER is closest, so it only means "movement"
        while the population holds still.

        Measured in the demo before this was fixed: Thiep (rất gần) leaves, Nghia (ở
        khoảng cách nói chuyện) stays exactly where she is, and the sensor reported
        "Thiep vừa đi khỏi; người gần nhất lùi ra xa hơn" — a motion nobody made. Two
        different people's bands compared as if they were one person's.
        """
        w = Scene()

        async def go():
            await w.enrol()
            await w.settle((NEAR, THIEP, "đang ngồi"), (TALK, NGHIA, "đang ngồi"))
            w.at((TALK, NGHIA, "đang ngồi"))               # Thiep goes; Nghia does not
            return await w.read()

        fired = run(go())
        self.assertEqual(len(fired), 1)
        self.assertIn("Thiep vừa đi khỏi", fired[0].text)
        self.assertNotIn("người gần nhất", fired[0].text)
        self.assertNotIn("lùi ra xa", fired[0].text)

    def test_transitions_needs_a_name_on_BOTH_sides(self):
        """The rule stated directly, away from the sensor: the key INTERSECTION is what
        keeps an arrival from doubling as a change.

        `_transitions` used to take a third argument restricting it to people present in
        both committed states. Mutation M3 removed that restriction and nothing failed,
        because the intersection here already excludes everyone it would have — so the
        argument went (ADR-120) and this test pins the mechanism that was actually doing
        the work, rather than the one the docstring claimed was.
        """
        before = frozenset({("Thiep", "đang ngồi")})
        after = frozenset({("Thiep", "đang đứng"), ("Nghia", "đang đứng")})
        self.assertEqual(_transitions(before, after),
                         (("Thiep", "đang ngồi", "đang đứng"),),
                         "Nghia has no 'before'; there is no transition to report")
        self.assertEqual(_transitions(after, before),
                         (("Thiep", "đang đứng", "đang ngồi"),),
                         "and symmetrically, no 'after' is no transition either")
        self.assertEqual(_transitions(before, before), (),
                         "an unchanged value is not a transition")

    def test_a_stale_name_left_by_per_field_settling_reports_nothing(self):
        """The one way `postures` and `known` can disagree, checked rather than argued.

        Per-field settling can leave a name in the committed `postures` after its owner
        has left `known` — its posture never settled, so the old value is held. That
        name is then absent from the NEXT observation's map, so the intersection drops
        it and no phantom transition is reported for someone who is not there.
        """
        stale = frozenset({("Thiep", "đang ngồi"), ("Nghia", "đang ngồi")})
        now = frozenset({("Thiep", "đang đứng")})            # Nghia has gone
        self.assertEqual(_transitions(stale, now),
                         (("Thiep", "đang ngồi", "đang đứng"),))


class PriorityStillComesFromStructureNotFromContent(unittest.TestCase):
    """Rule 1 (`harness.contrib.driver`), at the point it was easiest to lose."""

    def test_every_scene_label_carries_the_same_tier(self):
        """The refused design is `"fire" in labels => CRITICAL`. A camera is
        `effect="external"`; wiring a label to a priority puts a picture held up to the
        lens in charge of preemption."""
        table = Salience()
        for label in ("fire", "smoke", "explosion", "URGENT", "CRITICAL", "cat"):
            self.assertEqual(table.of(Change(scene_in=(label,))), table.scene,
                             f"{label!r} bought a different tier than a cat")

    def test_the_default_table_can_never_preempt(self):
        table = Salience()
        for change in (Change(arrived=("Thiep",)), Change(unknown_arrived=1),
                       Change(left=("Thiep",)), Change(scene_in=("fire",)),
                       Change(postures=(("Thiep", "đang ngồi", "đang đứng"),)),
                       Change(moved=(("Thiep", DISTANCE_BANDS[0], DISTANCE_BANDS[2]),)),
                       Change(nearest=(DISTANCE_BANDS[0], DISTANCE_BANDS[2]))):
            self.assertLess(table.of(change), Priority.HIGH,
                            f"{change} could cancel a turn out of the box")

    def test_one_observation_settling_several_changes_takes_the_highest_tier(self):
        """`max`, not a first-match ladder: with a ladder the answer depends on the
        order the rows happen to be written in."""
        table = Salience()
        both = Change(left=("Nghia",), arrived=("Thiep",))
        self.assertEqual(table.of(both), table.arrival)
        self.assertGreater(table.arrival, table.departure, "the premise of the above")
        scene_and_approach = Change(scene_in=("fire",),
                                    moved=(("Thiep", DISTANCE_BANDS[0],
                                            DISTANCE_BANDS[2]),))
        self.assertEqual(table.of(scene_and_approach), table.approach)

    def test_direction_decides_approach_versus_retreat(self):
        table = Salience(approach=Priority.HIGH, retreat=Priority.LOW)
        near_from_far = Change(moved=(("Thiep", DISTANCE_BANDS[0], DISTANCE_BANDS[2]),))
        far_from_near = Change(moved=(("Thiep", DISTANCE_BANDS[2], DISTANCE_BANDS[0]),))
        self.assertEqual(table.of(near_from_far), Priority.HIGH)
        self.assertEqual(table.of(far_from_near), Priority.LOW)

    def test_promote_is_how_an_operator_gets_fire_to_preempt(self):
        """Their code, reading a `Change`. The escape hatch exists precisely so the
        default can stay flat."""
        def burn(change: Change):
            return Priority.CRITICAL if "fire" in change.scene_in else None

        table = Salience(promote=burn)
        self.assertEqual(table.of(Change(scene_in=("fire",))), Priority.CRITICAL)
        self.assertEqual(table.of(Change(scene_in=("cat",))), table.scene)


class AttendsIsTheOffSwitch(unittest.TestCase):
    def test_an_unattended_aspect_produces_no_event(self):
        w = Scene(attends=frozenset({PRESENCE}))

        async def go():
            await w.enrol()
            await w.settle((FAR, THIEP, "đang ngồi"))
            w.at((NEAR, THIEP, "đang đứng"), scene=(("fire", 0.9),))
            quiet = await w.read()
            w.at()                                   # ...but presence still works
            return quiet, await w.read()

        quiet, gone = run(go())
        self.assertEqual(quiet, [], "posture, distance and scene were all switched off")
        self.assertEqual(len(gone), 1, "and presence is still attended")

    def test_an_unattended_aspect_is_not_even_computed(self):
        """Not merely filtered at the end: an aspect that is off must not enter `_State`,
        or it still costs debounce and still couples to everything else."""
        w = Scene(attends=frozenset({PRESENCE, POSTURE}))
        reading_state = w.sensor._state_of

        async def go():
            await w.enrol()
            w.at((NEAR, THIEP, "đang ngồi"))
            return reading_state(await w.sensor._observe())

        state = run(go())
        self.assertEqual(state.postures, frozenset({("Thiep", "đang ngồi")}))
        self.assertEqual(state.distances, frozenset(), "DISTANCE is off")
        self.assertEqual(state.nearest, "")
        self.assertEqual(state.scene, frozenset(), "SCENE is off")

    def test_presence_cannot_be_switched_off(self):
        w = Scene(attends=frozenset())
        self.assertIn(PRESENCE, w.sensor.attends)

    def test_a_misspelt_aspect_is_refused_at_construction(self):
        """Silence at 3am is the worst way to learn about a typo in a frozenset."""
        with self.assertRaises(ValueError) as e:
            Scene(attends=frozenset({PRESENCE, "postures"}))
        self.assertIn("postures", str(e.exception))
        self.assertIn(sorted(ATTENDABLE)[0], str(e.exception),
                      "the message must say what the valid names are")

    def test_the_default_attends_to_everything(self):
        w = Scene()
        self.assertEqual(w.sensor.attends, frozenset({PRESENCE, POSTURE, DISTANCE, SCENE}))


class TheSentenceTheModelReads(unittest.TestCase):
    def test_a_bare_nearest_change_is_dropped_when_a_named_move_explains_it(self):
        """Otherwise the same movement is announced twice, the second time in vaguer
        words that add nothing."""
        text = render(Change(moved=(("Thiep", DISTANCE_BANDS[0], DISTANCE_BANDS[2]),),
                             nearest=(DISTANCE_BANDS[0], DISTANCE_BANDS[2])))
        self.assertIn("Thiep tiến lại gần", text)
        self.assertNotIn("người gần nhất", text)

    def test_a_scene_change_is_phrased_as_observation_not_as_a_conclusion(self):
        """The labels are untrusted content that decided no priority to get here, so the
        sentence must not read like a finding."""
        text = render(Change(scene_in=("fire",), scene_out=("home office",)))
        self.assertIn("trông như fire", text)
        self.assertIn("không còn thấy home office", text)


if __name__ == "__main__":
    unittest.main()
