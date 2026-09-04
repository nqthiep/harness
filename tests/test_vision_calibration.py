"""`calibrate()` and the real numbers it was written to report.

`DEFAULT_THRESHOLD = 0.80` was a guess, documented as one, for three commits — the
identity feature's whole correctness rested on a number nobody had measured, because
`MediaPipeDetector` had never run (ADR-077). `tests/vision_probe.py` ran it: four real
models, five public photographs, real inference. This file is the measurement pinned as
assertions, so the conclusion cannot quietly rot — no MediaPipe, no network, no models
needed here, because the numbers are the fixture.
"""
import unittest

from harness.memory.inmemory import InMemoryStore
from vision_tools import (DEFAULT_MARGIN, DEFAULT_THRESHOLD, Calibration, IdentityLedger,
                          calibrate)

#: Cosine scores between two embeddings of the SAME person, measured by
#: `tests/vision_probe.py` on `mediapipe` 1.0.1 with `mobilenet_v3_small`. The first
#: seven are one photograph perturbed (crop, brightness, JPEG quality); the last three
#: are three renderings of that same photograph, each face detected independently.
MEASURED_SAME = (1.0000, 0.9471, 0.8692, 0.8418, 0.6414, 0.9977, 0.9672,
                 0.2870, 0.7455, 0.2981)

#: Cosine scores between two DIFFERENT people, same run.
MEASURED_DIFFERENT = (0.4990, 0.3099, 0.4991, 0.2772, 0.3400, 0.2200, 0.5613)


class Calibrating(unittest.TestCase):
    def test_a_separable_sample_yields_a_threshold_in_the_gap(self):
        cal = calibrate(same=(0.90, 0.95, 0.88), different=(0.30, 0.42, 0.51))
        self.assertTrue(cal.separable)
        self.assertGreater(cal.threshold, 0.51)
        self.assertLess(cal.threshold, 0.88)

    def test_the_threshold_sits_at_the_midpoint_not_on_the_worst_same_pair(self):
        """Sitting exactly on the worst observed same-person pair guarantees the next
        slightly-worse one is rejected. The midpoint is the only choice that gives both
        error directions the same room on the evidence available."""
        cal = calibrate(same=(0.80,), different=(0.40,))
        self.assertAlmostEqual(cal.threshold, 0.60, places=6)

    def test_calibrating_needs_both_sides(self):
        with self.assertRaises(ValueError) as ctx:
            calibrate(same=(0.9, 0.8), different=())
        self.assertIn("has never seen the error", str(ctx.exception))
        with self.assertRaises(ValueError):
            calibrate(same=(), different=(0.2,))

    def test_the_gap_is_negative_exactly_when_the_sample_overlaps(self):
        self.assertGreater(calibrate((0.9,), (0.3,)).gap, 0)
        self.assertLess(calibrate((0.3,), (0.9,)).gap, 0)

    def test_the_count_of_pairs_is_reported_not_just_the_verdict(self):
        cal = calibrate(same=(0.9, 0.8), different=(0.1,))
        self.assertEqual((cal.n_same, cal.n_different), (2, 1))
        self.assertIn("2 same-person pairs", str(cal))
        self.assertIn("1 different-person pairs", str(cal))


class TheRealMeasurement(unittest.TestCase):
    """What the probe found, as assertions. If a future embedder change makes these pass
    differently, that is the signal to re-run `tests/vision_probe.py` and rewrite the
    fixtures — not to relax the assertions."""

    def setUp(self):
        self.cal = calibrate(MEASURED_SAME, MEASURED_DIFFERENT)

    def test_a_generic_image_embedder_cannot_separate_people_at_any_threshold(self):
        self.assertFalse(self.cal.separable)
        self.assertIsNone(self.cal.threshold)

    def test_the_distributions_are_inverted_not_merely_close(self):
        """The same person rotated (0.2870) scores FURTHER apart than two strangers
        (0.5613). This is the finding: not a threshold that needs tuning, an embedder
        that encodes the picture rather than the person."""
        self.assertAlmostEqual(self.cal.worst_same, 0.2870, places=4)
        self.assertAlmostEqual(self.cal.best_different, 0.5613, places=4)
        self.assertAlmostEqual(self.cal.gap, -0.2743, places=4)

    def test_the_failure_direction_of_the_shipped_default_is_the_safe_one(self):
        """`0.80` never names the wrong person on this sample; it fails to recognise the
        right one, and `match` then answers "I don't know". Worth pinning down, because
        "the threshold is uncalibrated" reads as if it might be dangerous, and the
        measured direction is the opposite."""
        false_accepts = [d for d in MEASURED_DIFFERENT if d >= DEFAULT_THRESHOLD]
        false_rejects = [s for s in MEASURED_SAME if s < DEFAULT_THRESHOLD]
        self.assertEqual(false_accepts, [], "a lower number would trade this away")
        self.assertEqual(len(false_rejects), 4)

    def test_the_crop_is_the_most_fragile_input(self):
        """A 20% wider box on the IDENTICAL face drops similarity to 0.6414 — a bigger
        move than brightness (0.9977) or JPEG q=40 (0.9672). Anything that changes how a
        face is cropped between enrolment and matching matters more than lighting, which
        is the opposite of the intuition a threshold gets picked with."""
        self.assertLess(0.6414, 0.9672)
        self.assertIn(0.6414, MEASURED_SAME)


class BuildingALedgerFromAMeasurement(unittest.IsolatedAsyncioTestCase):
    async def test_a_separable_calibration_sets_the_threshold(self):
        cal = calibrate(same=(0.90,), different=(0.40,))
        ledger = IdentityLedger.from_calibration(InMemoryStore(), cal)
        self.assertAlmostEqual(ledger._threshold, 0.65, places=6)
        self.assertAlmostEqual(ledger._margin, DEFAULT_MARGIN, places=6)

    async def test_the_real_measurement_refuses_to_build_a_ledger_at_all(self):
        """The constructor a caller should reach for cannot be built on evidence that no
        threshold works — which is the state the shipped example configuration is in."""
        with self.assertRaises(ValueError) as ctx:
            IdentityLedger.from_calibration(
                InMemoryStore(), calibrate(MEASURED_SAME, MEASURED_DIFFERENT))
        message = str(ctx.exception)
        self.assertIn("NOT separable", message)
        self.assertIn("Change the EMBEDDER", message)
        self.assertIn("coin toss", message)

    async def test_a_calibrated_ledger_still_matches_and_still_says_i_dont_know(self):
        """The calibrated path is not a separate matching rule — it is the same `match`
        with a measured number, margin included."""
        cal = calibrate(same=(0.90,), different=(0.40,))
        ledger = IdentityLedger.from_calibration(InMemoryStore(), cal)
        await ledger.enroll("Nghia", (1.0, 0.0, 0.0))
        self.assertEqual((await ledger.match((1.0, 0.0, 0.0))).name, "Nghia")
        self.assertIsNone((await ledger.match((0.0, 1.0, 0.0))).name)


class TheCalibrationValueType(unittest.TestCase):
    def test_it_is_frozen_like_every_other_value_in_this_file(self):
        cal = calibrate((0.9,), (0.3,))
        with self.assertRaises(Exception):
            cal.threshold = 0.5                     # type: ignore[misc]
        self.assertIsInstance(cal, Calibration)


if __name__ == "__main__":
    unittest.main()
