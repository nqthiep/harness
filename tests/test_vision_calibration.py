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
from vision_tools import (DEFAULT_MARGIN, DEFAULT_THRESHOLD, MIN_CALIBRATION_GAP,
                          MIN_CALIBRATION_PAIRS, NATIVE_LIBRARIES,
                          NATIVE_LIBRARY_INSTALL, Calibration, IdentityLedger,
                          LEFT_EYE_CORNER, RIGHT_EYE_CORNER,
                          MediaPipeDetector, align_landmarks, calibrate,
                          cosine)

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
        self.assertFalse(self.cal.enough_evidence)
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


def _usable(same=0.90, different=0.40, n=10):
    """A calibration with enough evidence to be built on: separable, `n` pairs a side,
    and a gap far wider than `MIN_CALIBRATION_GAP`."""
    return calibrate(same=(same,) * n, different=(different,) * n)


class TheEvidenceGate(unittest.TestCase):
    """`separable` was the only gate, and the landmark-geometry experiment walked
    straight through it: three pairs a side, gap 0.0083, reported "separable", and
    `from_calibration` would have built a ledger on it (ADR-095). Separable is
    necessary and nowhere near sufficient."""

    def test_a_wide_gap_on_enough_pairs_is_usable(self):
        cal = _usable()
        self.assertTrue(cal.separable)
        self.assertTrue(cal.enough_evidence)
        self.assertEqual(cal.complaint, "")
        self.assertIn("USABLE", str(cal))

    def test_too_few_pairs_is_separable_but_not_enough(self):
        cal = _usable(n=3)
        self.assertTrue(cal.separable, "the numbers themselves do separate")
        self.assertFalse(cal.enough_evidence)
        self.assertIn("3 pairs on the thinner side", cal.complaint)
        self.assertIn("NOT ENOUGH EVIDENCE", str(cal))

    def test_the_thinner_side_is_what_counts(self):
        """Twenty same-person pairs do not license a threshold measured against two
        strangers."""
        cal = calibrate(same=(0.9,) * 20, different=(0.4,) * 2)
        self.assertFalse(cal.enough_evidence)
        self.assertIn("2 pairs", cal.complaint)

    def test_a_gap_thinner_than_a_crop_change_is_not_enough(self):
        """Measured: widening a crop box by 20% on the IDENTICAL face moved similarity
        by 0.36. A gap of 0.01 will not survive the next crop."""
        cal = calibrate(same=(0.905,) * 10, different=(0.895,) * 10)
        self.assertTrue(cal.separable)
        self.assertFalse(cal.enough_evidence)
        self.assertIn("gap is +0.0100", cal.complaint)

    def test_the_full_frame_landmark_result_is_refused(self):
        """The experiment that made this gate necessary. Landmark geometry measured on
        FULL frames — outside the crop path this library ships — separated three pairs a
        side by +0.0083, drawn from two people, and read as `separable`. An earlier
        version of `from_calibration` would have built an identity ledger on that."""
        cal = calibrate(same=(0.9951, 0.9990, 0.9951), different=(0.9830, 0.9815, 0.9868))
        self.assertTrue(cal.separable)
        self.assertFalse(cal.enough_evidence)
        with self.assertRaises(ValueError):
            IdentityLedger.from_calibration(InMemoryStore(), cal)

    def test_the_shipped_landmark_path_does_not_separate_at_all(self):
        """And the other half of that lesson: measured through `landmark_model=`, the
        code this library actually ships, the same feature on the same photographs does
        not separate. Better than the image embedder — the overlap roughly halves,
        0.2743 -> 0.1161 — and still a refusal.

        The flattering number came from measuring outside the shipped path
        (full frames, no crop, and one photograph whose faces the landmarker missed
        entirely, which removed the pairs that break it). ADR-095.
        """
        cal = calibrate(same=(0.8772, 0.9993, 0.8897),
                        different=(0.9826, 0.8844, 0.9837, 0.9932, 0.9806, 0.8360,
                                   0.9792))
        self.assertFalse(cal.separable)
        self.assertAlmostEqual(cal.gap, -0.1160, places=3)
        image_embedder = calibrate(MEASURED_SAME, MEASURED_DIFFERENT)
        self.assertGreater(cal.gap, image_embedder.gap,
                           "geometry should at least be the better of two refusals")

    def test_a_refusal_says_where_its_numbers_came_from(self):
        """The gates are judgments measured on a two-person sample. Whoever they refuse
        should learn that from the refusal, not from an ADR they have no reason to open
        (ADR-102)."""
        thin = _usable(n=3)
        self.assertTrue(thin.defaults_used)
        self.assertIn("DEFAULTS", thin.provenance)
        self.assertIn("two-person sample", thin.provenance)
        self.assertIn("min_pairs=", thin.provenance)
        self.assertIn("DEFAULTS", str(thin))

        with self.assertRaises(ValueError) as ctx:
            IdentityLedger.from_calibration(InMemoryStore(), thin)
        self.assertIn("two-person sample", str(ctx.exception))

    def test_gates_the_caller_chose_carry_no_such_caveat(self):
        chosen = calibrate(same=(0.9,) * 3, different=(0.4,) * 3,
                           min_pairs=3, min_gap=0.1)
        self.assertFalse(chosen.defaults_used)
        self.assertEqual(chosen.provenance, "")
        self.assertNotIn("DEFAULTS", str(chosen))

    def test_the_default_gates_are_the_documented_numbers(self):
        """Pinned, because both are judgments and the reasoning for each is written
        beside it: ten pairs a side so the threshold is a property of the embedder
        rather than of the sample, and 0.05 because a 20% crop change alone moved a
        measured score by 0.36."""
        self.assertEqual(MIN_CALIBRATION_PAIRS, 10)
        self.assertEqual(MIN_CALIBRATION_GAP, 0.05)
        cal = _usable()
        self.assertEqual((cal.min_pairs, cal.min_gap),
                         (MIN_CALIBRATION_PAIRS, MIN_CALIBRATION_GAP))

    def test_the_gates_are_arguable_rather_than_buried(self):
        cal = calibrate(same=(0.9,) * 3, different=(0.4,) * 3, min_pairs=3, min_gap=0.1)
        self.assertTrue(cal.enough_evidence,
                        "a caller who can defend a looser gate can set one")


class BuildingALedgerFromAMeasurement(unittest.IsolatedAsyncioTestCase):
    async def test_a_separable_calibration_sets_the_threshold(self):
        ledger = IdentityLedger.from_calibration(InMemoryStore(), _usable())
        self.assertAlmostEqual(ledger._threshold, 0.65, places=6)
        self.assertAlmostEqual(ledger._margin, DEFAULT_MARGIN, places=6)

    async def test_thin_evidence_can_be_waived_but_never_silently(self):
        thin = _usable(n=3)
        with self.assertRaises(ValueError) as ctx:
            IdentityLedger.from_calibration(InMemoryStore(), thin)
        self.assertIn("accept_thin_evidence=True", str(ctx.exception))
        ledger = IdentityLedger.from_calibration(InMemoryStore(), thin,
                                                 accept_thin_evidence=True)
        self.assertAlmostEqual(ledger._threshold, 0.65, places=6)

    async def test_the_waiver_cannot_conjure_a_threshold_that_does_not_exist(self):
        """`accept_thin_evidence=` accepts a thin separation. It cannot accept an
        overlap, because there is no number to accept."""
        overlapping = calibrate(MEASURED_SAME, MEASURED_DIFFERENT)
        with self.assertRaises(ValueError) as ctx:
            IdentityLedger.from_calibration(InMemoryStore(), overlapping,
                                            accept_thin_evidence=True)
        self.assertIn("nothing to accept", str(ctx.exception))

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
        ledger = IdentityLedger.from_calibration(InMemoryStore(), _usable())
        await ledger.enroll("Nghia", (1.0, 0.0, 0.0))
        self.assertEqual((await ledger.match((1.0, 0.0, 0.0))).name, "Nghia")
        self.assertIsNone((await ledger.match((0.0, 1.0, 0.0))).name)


def _face(n=300, *, scale=1.0, angle=0.0, shift=(0.0, 0.0)):
    """A synthetic landmark set: `n` points on a deliberately ASYMMETRIC curve, with
    indices 33 and 263 placed as the eye corners. Asymmetric so a rotation is actually
    detectable — on a symmetric shape every alignment would look correct."""
    import math as _m
    pts = []
    for i in range(n):
        t = i / n
        pts.append([_m.cos(t * 5.0) + 0.3 * t, _m.sin(t * 3.0) - 0.7 * t * t, 0.1 * t])
    pts[LEFT_EYE_CORNER] = [-1.0, 0.0, 0.0]
    pts[RIGHT_EYE_CORNER] = [1.0, 0.0, 0.0]

    out = []
    c, sn = _m.cos(angle), _m.sin(angle)
    for x, y, z in pts:
        x, y = x * scale, y * scale
        out.append([x * c - y * sn + shift[0], x * sn + y * c + shift[1], z * scale])
    return out


class AligningLandmarks(unittest.TestCase):
    """`align_landmarks` is the reason landmark geometry is worth trying at all: a
    rotation is something geometry can undo, and rotation is exactly what destroyed the
    generic image embedder (the same person, photograph rotated, 0.2870 — further apart
    than two strangers). Tested on hand-written points, so no model is involved."""

    def test_a_rotated_face_aligns_to_the_same_vector(self):
        import math as _m
        upright = align_landmarks(_face())
        for degrees in (5, 30, 90, 179):
            with self.subTest(degrees=degrees):
                turned = align_landmarks(_face(angle=_m.radians(degrees)))
                self.assertGreater(cosine(upright, turned), 0.999)

    def test_scale_and_position_do_not_matter(self):
        upright = align_landmarks(_face())
        moved = align_landmarks(_face(scale=7.5, shift=(120.0, -40.0)))
        self.assertGreater(cosine(upright, moved), 0.999)

    def test_a_different_shape_is_a_different_vector(self):
        """Otherwise the alignment would be throwing away the signal along with the
        pose."""
        other = _face()
        other[100] = [5.0, -5.0, 0.0]
        self.assertLess(cosine(align_landmarks(_face()), align_landmarks(other)), 0.999)

    def test_coincident_eye_corners_yield_nothing(self):
        """What a degenerate or half-occluded detection looks like: no magnitude, so no
        direction to compare — the same `()` every other empty vector in this file
        uses."""
        flat = _face()
        flat[RIGHT_EYE_CORNER] = list(flat[LEFT_EYE_CORNER])
        self.assertEqual(align_landmarks(flat), ())

    def test_too_few_points_yield_nothing(self):
        self.assertEqual(align_landmarks([[0.0, 0.0, 0.0]] * 10), ())

    def test_the_eye_line_ends_up_horizontal(self):
        """The mechanism, asserted directly rather than only through its consequence."""
        import math as _m
        flat = align_landmarks(_face(angle=_m.radians(37)))
        left_y = flat[LEFT_EYE_CORNER * 3 + 1]
        right_y = flat[RIGHT_EYE_CORNER * 3 + 1]
        self.assertAlmostEqual(left_y, right_y, places=9)


class TheNativeLibraryDiagnosis(unittest.TestCase):
    """MediaPipe `dlopen`s its C bindings when the first task is created, so a missing
    `libEGL.so.1` surfaces as an `OSError` from `ctypes.CDLL` naming a library — which
    reads like a broken install or a bad model path and is neither. That diagnosis is
    what this whole capability was parked behind for three commits (ADR-092).
    """

    def test_preflight_answers_before_any_model_file_exists(self):
        """The point of it: callable at startup, with no model, no camera, no frame."""
        missing = MediaPipeDetector.preflight()
        self.assertIsInstance(missing, tuple)
        self.assertLessEqual(set(missing), set(NATIVE_LIBRARIES),
                             "preflight must only report libraries it actually checks")

    def test_an_oserror_at_task_construction_becomes_actionable(self):
        """Asserted by making construction fail the way a slim container makes it fail,
        rather than by uninstalling a system library in a test."""
        class Slim(MediaPipeDetector):
            def _build(self, kind, path):
                raise OSError("libEGL.so.1: cannot open shared object file: "
                              "No such file or directory")

        det = Slim(face_model="/nonexistent/face.tflite")
        with self.assertRaises(RuntimeError) as ctx:
            det.detect_faces(object())
        message = str(ctx.exception)
        self.assertIn("native libraries", message)
        self.assertIn(NATIVE_LIBRARY_INSTALL, message)
        self.assertIn("not Python packages", message)
        # The labelled line, not just the substring: when the libraries ARE loadable —
        # which is the state of this machine, and the reason a test cannot reach the
        # other one — the summary falls back to the exception text and would satisfy a
        # bare `assertIn` on its own, leaving this line untested.
        self.assertIn("Original error: libEGL.so.1: cannot open shared object file",
                      message)

    def test_a_real_error_is_not_dressed_up_as_a_missing_library(self):
        """`_task` catches `OSError` only. A `ValueError` from an unknown task kind, or
        anything else MediaPipe raises, must still arrive as itself."""
        class Broken(MediaPipeDetector):
            def _build(self, kind, path):
                raise ValueError("model file is corrupt")

        det = Broken(face_model="/nonexistent/face.tflite")
        with self.assertRaises(ValueError):
            det.detect_faces(object())


class TheCalibrationValueType(unittest.TestCase):
    def test_it_is_frozen_like_every_other_value_in_this_file(self):
        cal = calibrate((0.9,), (0.3,))
        with self.assertRaises(Exception):
            cal.threshold = 0.5                     # type: ignore[misc]
        self.assertIsInstance(cal, Calibration)


if __name__ == "__main__":
    unittest.main()
