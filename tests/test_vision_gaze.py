"""ADR-121: the coarse-to-fine cascade — what a glance decides to look at.

Every claim here is about a call that did NOT happen, which is only checkable if
something counts the calls. `FakeDetector.calls` does, and every test that asserts a
stage was skipped is paired with one that asserts it still runs when it should. A
cascade that skips everything is maximally cheap and completely useless; these tests are
arranged so that failure mode cannot pass.
"""
import asyncio
import unittest

import numpy

from harness.memory.inmemory import InMemoryStore

from vision_gaze import (BODIES, DEFAULT_MOTION_THRESHOLD, FACES, HANDS, IDENTITY,
                         MOTION_SUBSAMPLE, SCENE, STAGES, Focus, Gaze, Look, motion_of)
from vision_sensor import GESTURE, POSTURE, PRESENCE, CameraSensor
from vision_tools import (Body, Camera, Face, FakeDetector, Hand, IdentityLedger,
                          PerceptionBuffer)

THIEP = (1.0, 0.0, 0.1)
BOX, ELSEWHERE = (10, 10, 300, 300), (320, 10, 300, 300)


class _Cap:
    """Pixels follow the script, like a real camera. `frozen=True` makes them stop, which
    is the worst case for a motion-triggered cascade and gets its own test."""

    def __init__(self, detector, frozen=False):
        self.detector, self.frozen = detector, frozen

    def isOpened(self):
        return True

    def set(self, *_a):
        return True

    def read(self):
        frame = numpy.zeros((480, 640, 3), dtype=numpy.uint8)
        if self.frozen:
            return True, frame
        for i, face in enumerate(self.detector.faces or ()):
            x, y, w, h = face.box
            frame[y:y + h, x:x + w] = 200 - 10 * i
        for i, body in enumerate(self.detector.bodies or ()):
            frame[300:450, 20 + 160 * i:170 + 160 * i] = 40 + sum(body.posture.encode()) % 200
        return True, frame

    def release(self):
        pass


def _sensor(*, frozen=False, gaze=None, attends=None, **detector_kw):
    detector = FakeDetector(scene=(("home office", 0.9),), embeddings={BOX: THIEP,
                                                                       ELSEWHERE: THIEP},
                            **detector_kw)
    ledger = IdentityLedger(InMemoryStore())
    sensor = CameraSensor(camera=Camera(capture=_Cap(detector, frozen)),
                          detector=detector, ledger=ledger,
                          buffer=PerceptionBuffer(), gaze=gaze, attends=attends,
                          use_thread=False)
    return sensor, detector, ledger


async def _enrolled(ledger):
    await ledger.enroll("Thiep", THIEP)


def run(coro):
    return asyncio.run(coro)


class TierZeroCostsAlmostNothing(unittest.TestCase):
    def test_the_first_frame_is_not_a_change(self):
        """There is nothing to difference against, and calling that "motion" would make
        every sensor's first glance look like something happening."""
        frame = numpy.zeros((480, 640, 3), dtype=numpy.uint8)
        motion, small = motion_of(frame, None)
        self.assertEqual(motion, 0.0)
        self.assertEqual(small.shape, (480 // MOTION_SUBSAMPLE, 640 // MOTION_SUBSAMPLE, 3))

    def test_an_identical_frame_is_no_motion_and_a_different_one_is(self):
        a = numpy.zeros((480, 640, 3), dtype=numpy.uint8)
        b = a.copy()
        b[100:400, 100:500] = 255
        _m, small = motion_of(a, None)
        still, small = motion_of(a, small)
        moved, _small = motion_of(b, small)
        self.assertEqual(still, 0.0)
        self.assertGreater(moved, DEFAULT_MOTION_THRESHOLD,
                           "a person-sized patch must clear the threshold")

    def test_a_frame_it_cannot_difference_means_LOOK_not_crash(self):
        """This runs on every glance. A perception layer that raises is worse than one
        that looks too often, so an unusable frame degrades to "assume motion"."""
        motion, _ = motion_of(object(), None)
        self.assertEqual(motion, float("inf"))
        mismatched, _ = motion_of(numpy.zeros((480, 640, 3), dtype=numpy.uint8),
                                  numpy.zeros((10, 10, 3), dtype=numpy.int16))
        self.assertEqual(mismatched, 0.0, "a shape change re-baselines rather than firing")


class TheTriggerTableDecides(unittest.TestCase):
    """`Look.wants` in isolation — four independent reasons, OR-ed."""

    def test_each_reason_fires_on_its_own(self):
        motion_only = Look(on_motion=True, on_change=False, every=99)
        self.assertTrue(motion_only.wants(motion=True, changed=False, unresolved=False, stale=0))
        self.assertFalse(motion_only.wants(motion=False, changed=True, unresolved=True, stale=0))

        change_only = Look(on_motion=False, on_change=True, every=99)
        self.assertTrue(change_only.wants(motion=False, changed=True, unresolved=False, stale=0))
        self.assertFalse(change_only.wants(motion=True, changed=False, unresolved=False, stale=0))

        unresolved_only = Look(on_motion=False, on_change=False, on_unresolved=True, every=99)
        self.assertTrue(unresolved_only.wants(motion=True, changed=True, unresolved=True, stale=0))
        self.assertFalse(unresolved_only.wants(motion=True, changed=True, unresolved=False, stale=0))

    def test_staleness_fires_when_nothing_else_does(self):
        """The bound on how wrong a carried value can be. Without it a stage that nothing
        triggers is never measured again."""
        never = Look(on_motion=False, on_change=False, every=3)
        self.assertFalse(never.wants(motion=True, changed=True, unresolved=True, stale=2))
        self.assertTrue(never.wants(motion=False, changed=False, unresolved=False, stale=3))


class TheFirstGlanceLooksAtEverything(unittest.TestCase):
    def test_every_stage_runs_once_before_anything_is_carried(self):
        """Not an optimisation — a correctness rule. A stage that never ran has no
        measured value, and carrying forward from an empty `Reading` makes "never
        measured" look identical to "measured, and there was nothing"."""
        sensor, detector, ledger = _sensor(frozen=True, hands=(Hand("bàn tay mở"),))

        async def go():
            await _enrolled(ledger)
            await sensor.read()
            return dict(detector.calls)

        calls = run(go())
        for stage in ("faces", "bodies", "hands", "scene"):
            self.assertEqual(calls.get(stage), 1, f"{stage} must run on the first glance")

    def test_a_stage_first_measured_later_would_read_as_a_change(self):
        """The defect the rule above prevents, reproduced by removing it.

        Measured before the rule existed: `classify_scene` was skipped on a still, empty
        room, ran for the first time when somebody walked in, and the sensor announced
        "chỗ này giờ trông như home office" as if the room had just changed.
        """
        gaze = Gaze()
        gaze.glances = 5                       # pretend the first glance is long past
        sensor, detector, ledger = _sensor(frozen=True, gaze=gaze)

        async def go():
            await _enrolled(ledger)
            await sensor.read()
            return dict(detector.calls)

        calls = run(go())
        self.assertIsNone(calls.get("scene"),
                          "this is the state the rule exists to make unreachable")


class AStageThatDidNotRunKeepsItsLastAnswer(unittest.TestCase):
    def test_a_man_sitting_perfectly_still_does_not_become_a_stranger(self):
        """The sharpest edge in the whole cascade.

        Identity is not re-derived every glance. If the names were recomputed from
        embeddings that were not measured, every recognised person would flip to unknown
        the moment `Gaze` skipped their embedding, and the sensor would announce "Thiep
        đi khỏi, một người lạ xuất hiện" about a man who had not moved.
        """
        sensor, detector, ledger = _sensor(frozen=True, faces=(Face(box=BOX),),
                                           bodies=(Body("đang ngồi"),))

        async def go():
            await _enrolled(ledger)
            events = [await sensor.read() for _ in range(12)]
            return [e for e in events if e is not None], dict(detector.calls)

        fired, calls = run(go())
        self.assertLess(calls.get("embed", 0), 12, "identity must not run every glance")
        self.assertEqual([e.text for e in fired if "đi khỏi" in e.text], [],
                         "nobody left")
        self.assertEqual(sensor._held.names, ("Thiep",), "the name was carried, not lost")

    def test_a_skipped_pose_does_not_read_as_a_body_that_vanished(self):
        sensor, detector, ledger = _sensor(frozen=True, faces=(Face(box=BOX),),
                                           bodies=(Body("đang ngồi"),))

        async def go():
            await _enrolled(ledger)
            for _ in range(6):
                await sensor.read()
            return sensor._held

        held = run(go())
        self.assertEqual(held.bodies, (Body("đang ngồi"),),
                         "the last measured value stands; empty would be a lie")


class ItStillSees(unittest.TestCase):
    """The control. Every cheapness claim above is worthless without these."""

    def test_an_arrival_still_fires(self):
        sensor, detector, ledger = _sensor()

        async def go():
            await _enrolled(ledger)
            for _ in range(4):
                await sensor.read()                       # empty room, settled
            detector.faces = (Face(box=BOX),)
            detector.bodies = (Body("đang ngồi"),)
            return [e for e in [await sensor.read() for _ in range(4)] if e]

        fired = run(go())
        self.assertTrue(fired, "the cascade went blind")
        self.assertIn("vừa xuất hiện", fired[0].text)

    def test_a_posture_change_that_moves_the_pixels_is_seen_promptly(self):
        sensor, detector, ledger = _sensor(faces=(Face(box=BOX),),
                                           bodies=(Body("đang ngồi"),))

        async def go():
            await _enrolled(ledger)
            for _ in range(4):
                await sensor.read()
            detector.bodies = (Body("đang đứng"),)
            for i in range(1, 12):
                if await sensor.read():
                    return i
            return None

        glances = run(go())
        self.assertIsNotNone(glances)
        self.assertLessEqual(glances, 4, "motion should not need the staleness clock")

    def test_and_the_staleness_bound_catches_it_even_with_the_pixels_frozen(self):
        """The worst case, stated as a number rather than left to be discovered: a world
        that changes without moving a pixel is seen when `Look.every` fires, not before.
        Measured ~24 glances, which at `sensor_interval_s=0.2` is ~4.8 s."""
        sensor, detector, ledger = _sensor(frozen=True, faces=(Face(box=BOX),),
                                           bodies=(Body("đang ngồi"),))

        async def go():
            await _enrolled(ledger)
            for _ in range(4):
                await sensor.read()
            detector.bodies = (Body("đang đứng"),)
            for i in range(1, 80):
                if await sensor.read():
                    return i
            return None

        glances = run(go())
        self.assertIsNotNone(glances, "a carried value must not be carried forever")
        self.assertLessEqual(glances, Gaze().looks[BODIES].every + 4,
                             "later than the staleness bound means the bound is not the bound")


class WhatItSkipsIsTheWholePoint(unittest.TestCase):
    def test_a_still_room_costs_tier_one_and_almost_nothing_else(self):
        """Measured over 100 glances of a realistic room: 13.6x cheaper in CPU. Asserted
        here as call counts, which is the part that does not depend on this machine."""
        sensor, detector, ledger = _sensor(faces=(Face(box=BOX),),
                                           bodies=(Body("đang ngồi"),),
                                           hands=(Hand("bàn tay mở"),))

        async def go():
            await _enrolled(ledger)
            for _ in range(60):
                await sensor.read()
            return dict(detector.calls)

        calls = run(go())
        self.assertEqual(calls["faces"], 60, "tier 1 runs every glance, by design")
        for stage in ("bodies", "hands", "scene"):
            self.assertLess(calls.get(stage, 0), 12,
                            f"{stage} ran {calls.get(stage, 0)}/60 times in a still room")

    def test_an_unattended_aspect_does_not_even_run_its_detector(self):
        """`Gaze` decides whether the answer would be FRESH; `attends` decides whether it
        would be READ. Paying 12.97 ms for a field `_state_of` discards is a cost with no
        reader."""
        sensor, detector, ledger = _sensor(attends=frozenset({PRESENCE}),
                                           faces=(Face(box=BOX),),
                                           bodies=(Body("đang ngồi"),),
                                           hands=(Hand("bàn tay mở"),))

        async def go():
            await _enrolled(ledger)
            for _ in range(20):
                await sensor.read()
            return dict(detector.calls)

        calls = run(go())
        self.assertGreater(calls["faces"], 0, "presence is always attended")
        for stage in ("bodies", "hands", "scene"):
            self.assertIsNone(calls.get(stage),
                              f"nobody reads {stage}, so nothing should pay for it")

    def test_attending_to_one_aspect_pays_for_exactly_that_one(self):
        sensor, detector, ledger = _sensor(attends=frozenset({PRESENCE, POSTURE}),
                                           faces=(Face(box=BOX),),
                                           bodies=(Body("đang ngồi"),),
                                           hands=(Hand("bàn tay mở"),))

        async def go():
            await _enrolled(ledger)
            for _ in range(20):
                await sensor.read()
            return dict(detector.calls)

        calls = run(go())
        self.assertGreater(calls.get("bodies", 0), 0, "POSTURE is attended")
        self.assertIsNone(calls.get("hands"))
        self.assertIsNone(calls.get("scene"))


class DeliberateAttention(unittest.TestCase):
    """The other half of the user's description: not only "something drew my eye", but
    "I need to read that now"."""

    def test_a_demand_buys_a_look_the_table_would_not_have_taken(self):
        gaze = Gaze()
        sensor, detector, ledger = _sensor(frozen=True, gaze=gaze,
                                           faces=(Face(box=BOX),),
                                           bodies=(Body("đang ngồi"),),
                                           hands=(Hand("bàn tay mở"),))

        async def go():
            await _enrolled(ledger)
            for _ in range(6):
                await sensor.read()
            before = detector.calls.get("hands", 0)
            gaze.demand(HANDS)
            await sensor.read()
            return before, detector.calls.get("hands", 0)

        before, after = run(go())
        self.assertEqual(after, before + 1, "a demand must be honoured")

    def test_a_demand_buys_ONE_look_and_not_a_permanent_cost(self):
        gaze = Gaze()
        sensor, detector, ledger = _sensor(frozen=True, gaze=gaze,
                                           faces=(Face(box=BOX),),
                                           hands=(Hand("bàn tay mở"),))

        async def go():
            await _enrolled(ledger)
            for _ in range(6):
                await sensor.read()
            gaze.demand(HANDS)
            await sensor.read()
            spent = detector.calls.get("hands", 0)
            for _ in range(3):
                await sensor.read()
            return spent, detector.calls.get("hands", 0)

        spent, later = run(go())
        self.assertEqual(later, spent, "the demand should have been cleared once spent")
        self.assertEqual(gaze.demanded, set())

    def test_a_misspelt_stage_is_refused_rather_than_dropped(self):
        gaze = Gaze()
        with self.assertRaises(ValueError) as e:
            gaze.demand("body")
        self.assertIn("body", str(e.exception))
        self.assertIn(BODIES, str(e.exception))


class TheDecisionIsVisible(unittest.TestCase):
    def test_focus_says_what_ran_and_report_says_what_it_cost(self):
        """A cascade whose decisions cannot be inspected cannot be tuned, and every
        number in ADR-121 came out of these two."""
        sensor, detector, ledger = _sensor(faces=(Face(box=BOX),),
                                           bodies=(Body("đang ngồi"),))

        async def go():
            await _enrolled(ledger)
            for _ in range(5):
                await sensor.read()
            return sensor.focus, sensor.gaze.report()

        focus, report = run(go())
        self.assertIn(FACES, focus)
        self.assertIn("lần liếc", report)
        self.assertIn("faces=5", report)

    def test_a_glance_that_looked_at_nothing_extra_says_so(self):
        self.assertTrue(Focus(stages=frozenset()).cheap)
        self.assertFalse(Focus(stages=frozenset({BODIES})).cheap)

    def test_the_table_covers_every_stage_it_claims_to(self):
        """A stage with no row is never run by the table. If `STAGES` gains a member and
        `DEFAULT_LOOKS` does not, that stage silently never runs — which would look like
        the cascade working very well."""
        self.assertEqual(set(Gaze().looks), set(STAGES))
        self.assertEqual(set(STAGES), {BODIES, HANDS, IDENTITY, SCENE})
        self.assertNotIn(FACES, STAGES, "tier 1 is unconditional, not table-driven")


class GesturesReachTheModel(unittest.TestCase):
    def test_a_hand_shape_appearing_is_an_event(self):
        sensor, detector, ledger = _sensor(faces=(Face(box=BOX),),
                                           bodies=(Body("đang đứng"),))

        async def go():
            await _enrolled(ledger)
            for _ in range(4):
                await sensor.read()
            detector.hands = (Hand("đang chỉ", "Right"),)
            sensor.gaze.demand(HANDS)
            return [e for e in [await sensor.read() for _ in range(4)] if e]

        fired = run(go())
        self.assertTrue(fired)
        self.assertIn("đang chỉ", fired[0].text)

    def test_an_unreadable_hand_is_not_a_gesture(self):
        """`UNKNOWN_GESTURE` means "I could not read that hand". Letting it into the
        state would make a hand drifting in and out of readability look like a gesture
        being made and unmade, forever."""
        from vision_tools import UNKNOWN_GESTURE
        sensor, detector, ledger = _sensor(faces=(Face(box=BOX),))

        async def go():
            await _enrolled(ledger)
            for _ in range(4):
                await sensor.read()
            detector.hands = (Hand(UNKNOWN_GESTURE),)
            sensor.gaze.demand(HANDS)
            return [e for e in [await sensor.read() for _ in range(4)] if e]

        self.assertEqual(run(go()), [])

    def test_gestures_can_be_switched_off_entirely(self):
        sensor, _detector, ledger = _sensor(attends=frozenset({PRESENCE}))
        self.assertNotIn(GESTURE, sensor.attends)


if __name__ == "__main__":
    unittest.main()
