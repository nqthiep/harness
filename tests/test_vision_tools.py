"""`vision_tools.py` — layer 1 (pure business logic) and layer 2/3 (adapters, tools).

The split in that module exists so this file can exist: no camera, no `.task` model
file, no network, no GPU, and still every branch that decides an ANSWER is covered.
Identity matching, the ambiguity refusal, posture, distance, and the sentence the model
actually reads are all tested against hand-written vectors and hand-written landmarks.

What is NOT covered, stated rather than implied: `MediaPipeDetector`'s inference paths.
Its API surface was verified against the installed package (every class, option and
result field name it reads), but no `.task` model file is bundled with `mediapipe`
1.0.1 and none could be fetched here, so a real inference pass has never run. The tests
below cover its no-model-configured behaviour, which is a supported configuration, and
nothing more. See ADR-077.
"""
import asyncio
import json
import unittest

from harness import Effect
from harness.memory.inmemory import InMemoryStore

from vision_tools import (ENROLL, IDENTIFY, LOOK, Body, Camera,
                          DEFAULT_THRESHOLD, Face, FakeDetector, IdentityLedger,
                          L_HIP, L_KNEE, L_SHOULDER, MediaPipeDetector,
                          NOSE, PerceptionBuffer, R_HIP, R_KNEE, R_SHOULDER, Reading,
                          VisionTools, cosine, describe, distance_of, facing_camera,
                          posture_of, _crop)

THIEP = (1.0, 0.0, 0.10)
NGHIA = (0.0, 1.0, 0.10)
STRANGER = (0.5, 0.5, 0.90)
BOX = (100, 60, 200, 200)


def run(coro):
    return asyncio.run(coro)


class _LM:
    """A `NormalizedLandmark`, as much of one as `posture_of` reads."""

    def __init__(self, y: float, visibility: float = 1.0) -> None:
        self.x, self.y, self.z, self.visibility = 0.5, y, 0.0, visibility


def _pose(shoulder: float, hip: float, knee: float, *, visibility: float = 1.0,
          shoulder_tilt: float = 0.0, nose_visibility: float = 1.0):
    landmarks = [_LM(0.0, visibility) for _ in range(33)]
    landmarks[NOSE] = _LM(shoulder - 0.15, nose_visibility)
    landmarks[L_SHOULDER] = _LM(shoulder, visibility)
    landmarks[R_SHOULDER] = _LM(shoulder + shoulder_tilt, visibility)
    landmarks[L_HIP] = landmarks[R_HIP] = _LM(hip, visibility)
    landmarks[L_KNEE] = landmarks[R_KNEE] = _LM(knee, visibility)
    return landmarks


class _FakeCapture:
    """Stands in for `cv2.VideoCapture`."""

    def __init__(self, *, opened: bool = True, frames=None, raises: bool = False):
        self._opened, self._raises = opened, raises
        self._frames = frames
        self.released = 0

    def isOpened(self) -> bool:
        return self._opened

    def set(self, *_a) -> bool:
        return True

    def read(self):
        if self._raises:
            raise OSError("device disconnected")
        if self._frames is None:
            import numpy
            return True, numpy.zeros((480, 640, 3), dtype=numpy.uint8)
        return self._frames

    def release(self) -> None:
        self.released += 1


# ── layer 1 ─────────────────────────────────────────────────────────────────────

class CosineThat(unittest.TestCase):
    def test_an_identical_vector_scores_one_and_an_orthogonal_one_scores_zero(self):
        self.assertAlmostEqual(cosine(THIEP, THIEP), 1.0, places=6)
        self.assertAlmostEqual(cosine((1.0, 0.0), (0.0, 1.0)), 0.0, places=6)

    def test_it_is_scale_invariant_which_is_the_whole_reason_it_is_used_here(self):
        # The same face further from the lens gives a longer/shorter vector in nearly
        # the same direction; a metric that moved with magnitude would call that a
        # different person.
        self.assertAlmostEqual(cosine(THIEP, tuple(3 * x for x in THIEP)), 1.0, places=6)

    def test_a_length_mismatch_or_a_zero_vector_is_zero_not_a_crash(self):
        # Both are reachable in practice: an embedder that returned nothing, or two
        # model files with different output dimensions.
        self.assertEqual(cosine((1.0, 0.0), (1.0, 0.0, 0.0)), 0.0)
        self.assertEqual(cosine((0.0, 0.0), THIEP), 0.0)

    def test_a_small_pose_change_barely_moves_the_score(self):
        """The measurement that says a threshold cannot be picked by intuition."""
        tilted = (0.97, 0.05, 0.12)
        self.assertGreater(cosine(THIEP, tilted), 0.99)


class IdentityLedgerThat(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryStore()
        self.ledger = IdentityLedger(self.store)

    def test_an_empty_ledger_answers_unknown_and_says_so(self):
        m = run(self.ledger.match(THIEP))
        self.assertIsNone(m.name)
        self.assertIn("no one has been enrolled", m.reason)

    def test_it_recognises_someone_it_enrolled(self):
        run(self.ledger.enroll("Thiep", THIEP))
        m = run(self.ledger.match(THIEP))
        self.assertEqual(m.name, "Thiep")
        self.assertGreaterEqual(m.score, DEFAULT_THRESHOLD)

    def test_several_angles_of_one_person_collapse_to_one_name(self):
        self.assertEqual(run(self.ledger.enroll("Thiep", THIEP)), 1)
        self.assertEqual(run(self.ledger.enroll("Thiep", (0.9, 0.1, 0.2))), 2)
        self.assertEqual(run(self.ledger.names()), ("Thiep",))

    def test_a_name_matches_case_insensitively_when_enrolling_again(self):
        run(self.ledger.enroll("Thiep", THIEP))
        run(self.ledger.enroll("thiep", (0.9, 0.1, 0.2)))
        self.assertEqual(run(self.ledger.names()), ("Thiep",))

    def test_a_stranger_is_unknown_rather_than_the_nearest_name(self):
        run(self.ledger.enroll("Thiep", THIEP))
        m = run(self.ledger.match(STRANGER))
        self.assertIsNone(m.name)
        self.assertIn("below the", m.reason)

    def test_two_enrolled_people_too_close_together_is_unknown_not_a_coin_flip(self):
        """The refusal that matters most: a confident wrong name is worse than a gap."""
        loose = IdentityLedger(self.store, threshold=0.50, margin=0.50)
        run(loose.enroll("Thiep", THIEP))
        run(loose.enroll("Nghia", NGHIA))
        m = run(loose.match(STRANGER))
        self.assertIsNone(m.name)
        self.assertIn("ambiguous", m.reason)

    def test_the_margin_does_not_fire_when_one_person_is_clearly_closer(self):
        run(self.ledger.enroll("Thiep", THIEP))
        run(self.ledger.enroll("Nghia", NGHIA))
        m = run(self.ledger.match(THIEP))
        self.assertEqual(m.name, "Thiep")

    def test_forgetting_someone_actually_removes_them(self):
        run(self.ledger.enroll("Thiep", THIEP))
        self.assertTrue(run(self.ledger.forget("thiep")))
        self.assertEqual(run(self.ledger.names()), ())
        self.assertFalse(run(self.ledger.forget("nobody")))

    def test_an_enrolment_needs_both_a_name_and_an_embedding(self):
        with self.assertRaises(ValueError):
            run(self.ledger.enroll("  ", THIEP))
        with self.assertRaises(ValueError):
            run(self.ledger.enroll("Thiep", ()))

    def test_a_corrupt_ledger_refuses_loudly_instead_of_forgetting_everyone(self):
        # Silently starting from an empty book would look exactly like the feature
        # working — the same call `tasks.py` makes for its own ledger.
        run(self.store.put(IdentityLedger.KEY, "{not json"))
        with self.assertRaises(RuntimeError) as caught:
            run(self.ledger.match(THIEP))
        self.assertIn("refusing", str(caught.exception))

    def test_a_row_with_no_name_is_ignored_rather_than_crashing_the_match(self):
        run(self.store.put(IdentityLedger.KEY,
                           json.dumps([{"vectors": [list(THIEP)]},
                                       {"name": "Thiep", "vectors": [list(THIEP)]}])))
        self.assertEqual(run(self.ledger.match(THIEP)).name, "Thiep")

    def test_the_ledger_survives_a_round_trip_through_the_store(self):
        run(self.ledger.enroll("Thiep", THIEP))
        self.assertEqual(run(IdentityLedger(self.store).match(THIEP)).name, "Thiep")


class PostureThat(unittest.TestCase):
    def test_knees_well_below_the_hips_reads_as_standing(self):
        self.assertEqual(posture_of(_pose(0.20, 0.50, 0.80)), "đang đứng")

    def test_knees_near_hip_level_reads_as_sitting(self):
        self.assertEqual(posture_of(_pose(0.20, 0.50, 0.58)), "đang ngồi")

    def test_it_does_not_change_with_how_far_away_the_person_is(self):
        """Scaling the whole body keeps the ratio, which is why the ratio is taken
        against torso height rather than against the frame."""
        self.assertEqual(posture_of(_pose(0.10, 0.25, 0.40)), "đang đứng")
        self.assertEqual(posture_of(_pose(0.40, 0.55, 0.62)), "đang ngồi")

    def test_low_visibility_admits_it_does_not_know(self):
        self.assertEqual(posture_of(_pose(0.20, 0.50, 0.80, visibility=0.1)),
                         "không rõ dáng")

    def test_a_degenerate_torso_admits_it_does_not_know(self):
        # Shoulders and hips at the same height: no scale to measure against, and a
        # division that would otherwise blow up.
        self.assertEqual(posture_of(_pose(0.50, 0.50, 0.80)), "không rõ dáng")

    def test_too_few_landmarks_admits_it_does_not_know(self):
        self.assertEqual(posture_of([_LM(0.5)] * 5), "không rõ dáng")

    def test_facing_the_camera_needs_level_shoulders_and_a_visible_nose(self):
        self.assertTrue(facing_camera(_pose(0.20, 0.50, 0.80)))
        self.assertFalse(facing_camera(_pose(0.20, 0.50, 0.80, shoulder_tilt=0.30)))
        self.assertFalse(facing_camera(_pose(0.20, 0.50, 0.80, nose_visibility=0.0)))


class DistanceThat(unittest.TestCase):
    def test_it_reads_closeness_from_how_much_of_the_frame_the_face_fills(self):
        self.assertEqual(distance_of(Face(box=(0, 0, 300, 300)), (640, 480)), "rất gần")
        self.assertEqual(distance_of(Face(box=(0, 0, 130, 130)), (640, 480)),
                         "ở khoảng cách nói chuyện")
        self.assertEqual(distance_of(Face(box=(0, 0, 40, 40)), (640, 480)), "ở xa")

    def test_an_unknown_frame_size_says_nothing_rather_than_guessing(self):
        self.assertEqual(distance_of(Face(box=(0, 0, 300, 300)), (0, 0)), "")


class DescribeThat(unittest.TestCase):
    def test_a_camera_that_did_not_work_becomes_a_sentence(self):
        said = describe(Reading(at=1.0, error="không mở được camera 0"))
        self.assertIn("không nhìn thấy gì", said)
        self.assertIn("không mở được camera 0", said)

    def test_an_empty_room_says_so_and_still_describes_the_room(self):
        said = describe(Reading(at=1.0, scene=(("kitchen", 0.8),)))
        self.assertIn("không có ai", said)
        self.assertIn("kitchen", said)

    def test_a_recognised_person_is_named(self):
        said = describe(
            Reading(at=1.0, faces=(Face(box=BOX),), bodies=(Body("đang ngồi"),),
                    frame_size=(640, 480)),
            names=["Thiep"])
        self.assertIn("Thiep", said)
        self.assertIn("đang ngồi", said)

    def test_an_unrecognised_person_says_it_does_not_know_them(self):
        said = describe(Reading(at=1.0, faces=(Face(box=BOX),), frame_size=(640, 480)),
                        names=[None])
        self.assertIn("chưa biết", said)

    def test_with_no_identity_lookup_at_all_it_does_not_claim_ignorance(self):
        """`look` never asks the ledger, so its sentence must not imply it checked and
        came up empty — that is `identify_person`'s answer, not this one."""
        said = describe(Reading(at=1.0, faces=(Face(box=BOX),), frame_size=(640, 480)))
        self.assertNotIn("chưa biết", said)
        self.assertIn("một người", said)

    def test_several_people_are_listed_with_their_own_details(self):
        said = describe(
            Reading(at=1.0,
                    faces=(Face(box=(10, 10, 200, 200)), Face(box=(400, 10, 60, 60))),
                    bodies=(Body("đang ngồi"), Body("đang đứng")),
                    frame_size=(640, 480)),
            names=["Thiep", None])
        self.assertIn("Thiep", said)
        self.assertIn("đang đứng", said)
        self.assertIn("chưa biết", said)

    def test_a_body_with_no_visible_face_is_still_reported(self):
        said = describe(Reading(at=1.0, faces=(Face(box=BOX),),
                                bodies=(Body("đang ngồi"), Body("đang đứng")),
                                frame_size=(640, 480)))
        self.assertIn("1 người nữa", said)

    def test_it_never_mentions_a_tool_or_a_function(self):
        # The prompt asks the agent to speak as someone who sees; a result phrased as
        # telemetry invites it to narrate its plumbing instead.
        said = describe(Reading(at=1.0, faces=(Face(box=BOX),), frame_size=(640, 480)),
                        names=["Thiep"])
        for word in ("tool", "function", "detect", "embedding", LOOK, IDENTIFY):
            self.assertNotIn(word, said.lower())


# ── layer 2 ─────────────────────────────────────────────────────────────────────

class CameraThat(unittest.TestCase):
    def test_no_device_is_a_reason_string_not_an_exception(self):
        """Measured behaviour of `cv2.VideoCapture` with no camera present: it raises
        nothing, `isOpened()` is False, `read()` is `(False, None)`. So the honest
        return here is a pair, and 'I can't see right now' is something an agent can
        say while a traceback out of a tool is not."""
        frame, err = Camera(index=99).grab()
        self.assertIsNone(frame)
        self.assertTrue(err)

    def test_an_injected_capture_is_used_as_is(self):
        cap = _FakeCapture()
        frame, err = Camera(capture=cap).grab()
        self.assertEqual(err, "")
        self.assertEqual(frame.shape, (480, 640, 3))

    def test_a_capture_that_is_not_open_is_reported_not_read(self):
        frame, err = Camera(capture=_FakeCapture(opened=False)).grab()
        # An injected capture is trusted as-is (the caller opened it); what must not
        # happen is a crash. Either outcome is acceptable, an exception is not.
        self.assertTrue(err == "" or frame is None)

    def test_a_read_that_returns_nothing_is_reported(self):
        frame, err = Camera(capture=_FakeCapture(frames=(False, None))).grab()
        self.assertIsNone(frame)
        self.assertIn("không trả về hình", err)

    def test_a_device_that_throws_mid_read_is_reported(self):
        frame, err = Camera(capture=_FakeCapture(raises=True)).grab()
        self.assertIsNone(frame)
        self.assertIn("lỗi", err)

    def test_it_never_releases_a_capture_it_did_not_open(self):
        # The caller who passed a capture in owns its lifetime — the same rule as
        # `CodingProfile(store=...)`.
        cap = _FakeCapture()
        Camera(capture=cap).close()
        self.assertEqual(cap.released, 0)


class CropThat(unittest.TestCase):
    def _frame(self):
        import numpy
        return numpy.zeros((480, 640, 3), dtype=numpy.uint8)

    def test_a_box_inside_the_frame_comes_back_at_that_size(self):
        self.assertEqual(_crop(self._frame(), (10, 20, 100, 50)).shape[:2], (50, 100))

    def test_a_box_hanging_off_the_edge_is_clamped_not_trusted(self):
        # A detector's box can extend past the edge, and numpy would silently hand back
        # a smaller (or empty) array much further from the cause.
        cropped = _crop(self._frame(), (600, 460, 200, 200))
        self.assertEqual(cropped.shape[:2], (20, 40))

    def test_a_box_that_does_not_overlap_the_frame_is_none(self):
        self.assertIsNone(_crop(self._frame(), (700, 500, 50, 50)))

    def test_a_degenerate_box_is_none_rather_than_an_empty_array(self):
        self.assertIsNone(_crop(self._frame(), (10, 10, 0, 0)))

    def test_something_that_is_not_an_array_is_none(self):
        self.assertIsNone(_crop("not a frame", (0, 0, 10, 10)))


class MediaPipeDetectorThat(unittest.TestCase):
    """Only the no-model-configured paths. A real inference pass has never run — no
    `.task` file is bundled and none could be fetched here (ADR-077)."""

    def test_a_capability_with_no_model_returns_empty_rather_than_raising(self):
        # Face-only, or pose-only, is a supported configuration: you pass the models
        # you actually have.
        detector = MediaPipeDetector()
        self.assertEqual(detector.detect_faces(object()), ())
        self.assertEqual(detector.detect_bodies(object()), ())
        self.assertEqual(detector.embed_face(object(), BOX), ())
        self.assertEqual(detector.classify_scene(object()), ())

    def test_close_is_safe_with_nothing_ever_created(self):
        MediaPipeDetector().close()

    def test_an_unknown_task_kind_is_a_programming_error(self):
        detector = MediaPipeDetector(face_model="x.task")
        detector._paths["nonsense"] = "x.task"
        with self.assertRaises(ValueError):
            detector._task("nonsense")


class PerceptionBufferThat(unittest.TestCase):
    def test_it_starts_out_saying_it_has_not_looked(self):
        buffer = PerceptionBuffer()
        self.assertFalse(buffer.reading.ok)
        self.assertIn("chưa nhìn", buffer.reading.error)

    def test_publishing_replaces_the_reading_and_the_frame_together(self):
        buffer = PerceptionBuffer()
        reading = Reading(at=1.0, faces=(Face(box=BOX),))
        buffer.publish(reading, "FRAME")
        self.assertIs(buffer.reading, reading)
        self.assertEqual(buffer.frame, "FRAME")


# ── layer 3 ─────────────────────────────────────────────────────────────────────

def _tools(*, faces=(Face(box=BOX),), bodies=(Body("đang ngồi"),),
           scene=(("home office", 0.7),), embedding=THIEP, enrollment=True,
           store=None, camera=None):
    detector = FakeDetector(faces=faces, bodies=bodies, scene=scene,
                            embeddings={f.box: embedding for f in faces} if embedding
                            else {})
    vt = VisionTools(camera=camera or Camera(capture=_FakeCapture()), detector=detector,
                     ledger=IdentityLedger(store or InMemoryStore()),
                     buffer=PerceptionBuffer(), enable_enrollment=enrollment)
    return vt, {spec.name: spec for spec in vt.tools()}


class VisionToolsThat(unittest.TestCase):
    def test_the_three_effect_classes_are_what_the_safety_engine_needs(self):
        """Not cosmetic. `external` marks camera content untrusted, `read` keeps
        identity lookups cheap and repeatable, and `danger` is the only class whose
        `decision_standard` is ASK — which is what makes enrolment ask a human."""
        _, by_name = _tools()
        self.assertEqual(by_name[LOOK].effect, Effect.EXTERNAL)
        self.assertEqual(by_name[IDENTIFY].effect, Effect.READ)
        self.assertEqual(by_name[ENROLL].effect, Effect.DANGER)

    def test_enrolment_is_absent_entirely_when_it_is_not_enabled(self):
        _, by_name = _tools(enrollment=False)
        self.assertNotIn(ENROLL, by_name)
        self.assertIn(LOOK, by_name)

    def test_look_describes_the_scene_without_claiming_to_know_who_it_is(self):
        _, by_name = _tools()
        said = run(by_name[LOOK].fn())
        self.assertIn("một người", said)
        self.assertIn("home office", said)
        self.assertNotIn("chưa biết", said)

    def test_look_publishes_a_reading_the_other_tools_can_use(self):
        vt, by_name = _tools()
        self.assertFalse(vt.buffer.reading.ok)
        run(by_name[LOOK].fn())
        self.assertTrue(vt.buffer.reading.ok)
        self.assertEqual(len(vt.buffer.reading.faces), 1)

    def test_identify_before_any_look_asks_for_a_look_instead_of_guessing(self):
        _, by_name = _tools()
        said = run(by_name[IDENTIFY].fn())
        self.assertIn("chưa nhìn", said)
        self.assertIn(LOOK, said)

    def test_identify_names_an_enrolled_person(self):
        store = InMemoryStore()
        run(IdentityLedger(store).enroll("Thiep", THIEP))
        _, by_name = _tools(store=store)
        run(by_name[LOOK].fn())
        self.assertIn("Thiep", run(by_name[IDENTIFY].fn()))

    def test_identify_lists_who_it_does_know_when_it_does_not_know_this_face(self):
        # Useful to the model: "I know Thiep and Nghia, this isn't either of them" is
        # what lets it decide whether asking for a name makes sense.
        store = InMemoryStore()
        run(IdentityLedger(store).enroll("Nghia", NGHIA))
        _, by_name = _tools(store=store, embedding=STRANGER)
        run(by_name[LOOK].fn())
        said = run(by_name[IDENTIFY].fn())
        self.assertIn("chưa biết", said)
        self.assertIn("Nghia", said)

    def test_identify_does_not_capture_a_new_frame(self):
        """It re-reads the last `look`'s frame, which is what makes it cheap enough to
        call repeatedly — and what stops it becoming a second camera read that the
        policy engine would have to reason about."""
        cap = _FakeCapture()
        vt, by_name = _tools(camera=Camera(capture=cap))
        run(by_name[LOOK].fn())
        first = vt.buffer.reading
        run(by_name[IDENTIFY].fn())
        self.assertIs(vt.buffer.reading, first)

    def test_a_frame_with_no_faces_says_so_rather_than_erroring(self):
        _, by_name = _tools(faces=(), bodies=())
        run(by_name[LOOK].fn())
        self.assertIn("không có khuôn mặt", run(by_name[IDENTIFY].fn()))

    def test_enrolment_refuses_when_two_faces_are_in_frame(self):
        """The consent rule that IS decidable from a frame: with two faces there is no
        fact saying which one is speaking, and guessing files a biometric record
        against the wrong person."""
        _, by_name = _tools(faces=(Face(box=BOX), Face(box=(400, 60, 100, 100))),
                            bodies=(Body("đang ngồi"), Body("đang đứng")))
        run(by_name[LOOK].fn())
        said = run(by_name[ENROLL].fn(name="Ai đó"))
        self.assertIn("không ghi nhớ", said)

    def test_enrolment_refuses_before_any_look(self):
        _, by_name = _tools()
        self.assertIn("chưa nhìn", run(by_name[ENROLL].fn(name="Thiep")))

    def test_enrolment_refuses_when_no_embedding_can_be_taken(self):
        _, by_name = _tools(embedding=None)
        run(by_name[LOOK].fn())
        self.assertIn("Không lấy được", run(by_name[ENROLL].fn(name="Thiep")))

    def test_enrolment_then_identification_closes_the_loop(self):
        vt, by_name = _tools(embedding=STRANGER)
        run(by_name[LOOK].fn())
        self.assertIn("chưa biết", run(by_name[IDENTIFY].fn()))
        run(by_name[ENROLL].fn(name="Minh"))
        self.assertIn("Minh", run(by_name[IDENTIFY].fn()))
        self.assertEqual(run(vt.ledger.names()), ("Minh",))

    def test_a_broken_camera_never_raises_out_of_a_tool(self):
        _, by_name = _tools(camera=Camera(index=99))
        self.assertIn("không nhìn thấy gì", run(by_name[LOOK].fn()))
        self.assertIn("chưa nhìn", run(by_name[IDENTIFY].fn()))
        self.assertIn("chưa nhìn", run(by_name[ENROLL].fn(name="Thiep")))

    def test_a_detector_that_throws_mid_inference_becomes_a_sentence(self):
        class Exploding:
            def detect_faces(self, frame):
                raise RuntimeError("model file is corrupt")

            def detect_bodies(self, frame):
                return ()

            def embed_face(self, frame, box):
                return ()

            def classify_scene(self, frame):
                return ()

        vt = VisionTools(camera=Camera(capture=_FakeCapture()), detector=Exploding(),
                         ledger=IdentityLedger(InMemoryStore()),
                         buffer=PerceptionBuffer())
        by_name = {s.name: s for s in vt.tools()}
        said = run(by_name[LOOK].fn())
        self.assertIn("nhận diện lỗi", said)
        self.assertIn("RuntimeError", said)

    def test_every_tool_returns_a_string_because_the_pipeline_has_no_other_shape(self):
        # `dispatch.py` renders a result as `value if isinstance(value, str) else
        # json.dumps(...)`. There is no image path, so local inference plus a sentence
        # is the only way this feature can exist at all.
        _, by_name = _tools()
        for name in (LOOK, IDENTIFY):
            self.assertIsInstance(run(by_name[name].fn()), str)
        self.assertIsInstance(run(by_name[ENROLL].fn(name="Thiep")), str)


if __name__ == "__main__":
    unittest.main()
