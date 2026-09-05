"""Eyes: OpenCV capture, MediaPipe face/body/scene inference, face-based identity, and
the natural-language rendering that makes an agent talk as though it simply sees.

Three layers, deliberately separated, because only the first two can be tested without
a camera and a model file — and that is exactly where the bugs live:

1. **Business logic, pure.** `cosine`, `IdentityLedger`, `posture_of`, `distance_of`,
   `describe`. No OpenCV, no MediaPipe, no `Agent`. Tested with hand-written vectors and
   hand-written landmarks (`tests/test_vision_tools.py`). Threshold choice, ambiguity
   between two enrolled faces, several angles of one person, an empty ledger, a frame
   with nobody in it — all decided here.
2. **Adapters.** `Detector` is a four-method Protocol; `MediaPipeDetector` implements it
   for real, `FakeDetector` implements it for tests. `Camera` wraps
   `cv2.VideoCapture`. Anything that needs hardware or a downloaded model lives ONLY
   here, so layer 1 stays verifiable.
3. **Tools.** `VisionTools.tools()` — three `ToolSpec`s whose function bodies are five
   lines of glue over layers 1 and 2. `vision_profile.py` wires them onto an `Agent`.

**Why every tool returns TEXT.** `dispatch.py` renders a tool result as
`value if isinstance(value, str) else json.dumps(value, ...)` — there is no image or
content-block path anywhere in the tool-result pipeline. So the model never sees pixels;
inference runs locally and the agent receives a sentence. That is not a limitation
worked around, it is the design: `describe()` is where "it has eyes" is actually
implemented.

**Effect classification, and why it is not obvious.** `look` is `external`
(`tools/__init__.py`: `emits = Label(Integrity.UNTRUSTED)`) — a camera captures
uncontrolled physical-world content, and a sign, a phone screen, or a printed page held
in frame is untrusted input in exactly the sense a fetched web page is. `identify_person`
is `read`. `enroll_person` is `danger`, which is the load-bearing choice: `danger` is
the only class whose `decision_standard` is ASK (`tools/__init__.py`), so writing a
biometric record asks a human EVERY time, by mechanism rather than by a sentence in the
prompt — and `_check_tool_set`'s lethal-trifecta refusal then forces the operator to
write `accepts_tainted=["enroll_person"]` in their own `Agent(...)` call, because a
profile is mechanically forbidden from granting that to itself
(`agent.py::_refuse_if_loosened` — measured: `ProfileLoosenedSafetyError:
accepts_tainted gained ['enroll_person']`).

Run it — no camera, no model file, no API key:

    python3 examples/vision_tools.py
"""
from __future__ import annotations

import json
import math
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

sys.path.insert(0, "src")

from harness import Effect, tool
from harness.contrib.calibration import (  # noqa: F401  (re-export)
    MIN_CALIBRATION_GAP, MIN_CALIBRATION_PAIRS, Calibration, calibrate)
from harness.memory.base import read_modify_write

Vector = Sequence[float]

# ── layer 1: business logic, pure ────────────────────────────────────────────────

#: Cosine similarity between two face embeddings must clear this to count as the same
#: person. **It has now been measured, and the measurement says something worse than
#: "the number is wrong": with a generic MediaPipe `ImageEmbedder` there is no number
#: that works.** Real inference, real models, real photographs (`tests/vision_probe.py`,
#: ADR-090):
#:
#:     same person, same photo at 1/3 scale ....... 0.7455
#:     same person, photo rotated ................. 0.2870   <- LOWEST same-person
#:     two DIFFERENT people ....................... 0.4990
#:     two DIFFERENT people ....................... 0.5613   <- HIGHEST different-person
#:
#: The distributions do not merely overlap, they invert: the same person rotated scores
#: further apart than two strangers do. `calibrate()` below says so mechanically (overlap
#: 0.2743 across 10 same-person and 7 different-person pairs), and the cause is not the
#: threshold — `mediapipe.tasks.vision.ImageEmbedder` with `mobilenet_v3_small` encodes
#: general picture content (pose, light, background), not identity, and MediaPipe Tasks
#: ships no face-recognition model at all. Identity by cosine needs a face-recognition
#: embedder (ArcFace, FaceNet, or a vendor API), supplied as `embed_model=`.
#:
#: **Which way it fails matters, so here it is exactly.** On that sample, `0.80` produces
#: **0 false accepts out of 7** and **4 false rejects out of 10**. It never names the
#: wrong person; it fails to recognise the right one, and `IdentityLedger.match` then
#: answers "I don't know" — the safe direction, and the one `DEFAULT_MARGIN` was chosen
#: for. So this default is not dangerous, it is just mostly useless with this embedder,
#: and a lower number would trade the safe failure for the unsafe one.
#:
#: Kept as a constructor argument, and kept at a cautious value, so that a caller who
#: brings a real face embedder can move it with `calibrate()` rather than by intuition.
#: Do not read it as a working default.
DEFAULT_THRESHOLD = 0.80

#: Two enrolled people this close together means the frame does not distinguish them —
#: answer "I don't know" rather than pick the higher number. A confident wrong name is
#: worse than an admitted gap, and for identity it is worse by a lot.
DEFAULT_MARGIN = 0.05

#: MediaPipe Pose's landmark indices (33-point topology). Named because
#: `landmarks[24].y` in a posture heuristic is unreadable and unreviewable.
NOSE, L_SHOULDER, R_SHOULDER = 0, 11, 12
L_HIP, R_HIP, L_KNEE, R_KNEE = 23, 24, 25, 26

#: Below this, a landmark's coordinates are noise rather than a measurement.
MIN_VISIBILITY = 0.5


def cosine(a: Vector, b: Vector) -> float:
    """Similarity in [-1, 1]; `0.0` when either side has no magnitude.

    Scale-invariant by construction, which is the property that matters here: the same
    face at two distances gives two vectors of different magnitude and nearly the same
    direction. It is also why a small pose change barely moves the number — measured on
    hand-written vectors, a 15% perturbation moved similarity from 1.00 to 0.998 — so a
    threshold picked by intuition will be far too loose. See `DEFAULT_THRESHOLD`.
    """
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0






#: The two landmark indices `align_landmarks` measures scale and rotation from — the
#: outer eye corners in MediaPipe's canonical 478-point face mesh. The eye line is the
#: right choice for both because it is the longest roughly-rigid feature on a face: it
#: barely moves with expression, unlike the mouth or the jaw.
LEFT_EYE_CORNER, RIGHT_EYE_CORNER = 33, 263

#: How much to widen a face box before handing the crop to the LANDMARKER, as a fraction
#: of the box. Not a taste setting — measured. A face detector's box is tight, and the
#: landmarker needs surrounding context to find anything in it: on a rotated portrait,
#: `0.0` and `0.2` found NO face in the crop while the same landmarker found one in the
#: full frame, and `0.4` fixed it. Rotation is the case landmark geometry exists to
#: handle, so silently losing it to a tight crop would have made the whole approach look
#: like it does not work (ADR-095). The `ImageEmbedder` path does not pad: it wants the
#: face and not the room.
LANDMARK_CROP_PAD = 0.4


def align_landmarks(points: Sequence[Sequence[float]]) -> Vector:
    """Face landmarks -> a comparable vector: centred, scaled, and rotated eye-line flat.

    Pure arithmetic on purpose, like everything else in this layer — no numpy, no
    MediaPipe — so it can be tested on six hand-written points instead of on a model.

    **Rotation alignment is the whole point, and it is measured, not assumed.** On real
    landmarks from a real photograph and the same photograph rotated:

        generic image embedder ............................ 0.2870
        landmark geometry, NOT rotation-aligned ........... 0.1570
        landmark geometry, rotation-aligned ............... 0.9961

    That one case is what destroyed the image embedder — the same person scoring further
    apart than two strangers (0.5613) — and a rotation is exactly what geometry can undo
    and a picture-content embedding cannot. It is not enough to make identity work; see
    `MediaPipeDetector.embed_face` for the population result, which is still a refusal
    (ADR-095).

    Returns `()` when the eye corners coincide, which is what a degenerate or
    partly-occluded detection looks like: no magnitude, so no direction to compare.
    """
    if len(points) <= max(LEFT_EYE_CORNER, RIGHT_EYE_CORNER):
        return ()
    dims = min(len(p) for p in points)
    if dims < 2:
        return ()
    centre = [sum(p[d] for p in points) / len(points) for d in range(dims)]
    centred = [[p[d] - centre[d] for d in range(dims)] for p in points]

    left, right = centred[LEFT_EYE_CORNER], centred[RIGHT_EYE_CORNER]
    span = math.sqrt(sum((right[d] - left[d]) ** 2 for d in range(dims)))
    if span == 0:
        return ()
    scaled = [[c / span for c in p] for p in centred]

    # Rotate in the image plane so the eye line is horizontal. Only x and y turn; a `z`
    # from a landmarker is depth and is left alone.
    left, right = scaled[LEFT_EYE_CORNER], scaled[RIGHT_EYE_CORNER]
    angle = -math.atan2(right[1] - left[1], right[0] - left[0])
    cos_a, sin_a = math.cos(angle), math.sin(angle)
    out: list[float] = []
    for p in scaled:
        out.append(p[0] * cos_a - p[1] * sin_a)
        out.append(p[0] * sin_a + p[1] * cos_a)
        out.extend(p[2:])
    return tuple(out)




@dataclass(frozen=True)
class Match:
    """Why the ledger answered the way it did. `name is None` is a real answer, not a
    failure, and `reason` is what makes it debuggable instead of mysterious."""
    name: str | None
    score: float
    reason: str


@dataclass(frozen=True)
class Face:
    """One detected face. `box` is `(x, y, w, h)` in pixels; `embedding` is present only
    once someone has asked for identity, since embedding costs an inference pass."""
    box: tuple[int, int, int, int]
    score: float = 1.0
    embedding: tuple[float, ...] | None = None


@dataclass(frozen=True)
class Body:
    """One detected body, already reduced to the two things worth saying out loud."""
    posture: str
    facing_camera: bool = True


@dataclass(frozen=True)
class Reading:
    """One glance, immutable. Immutable because a sensor thread publishes these by
    swapping a reference while the tool side reads it — an atomic rebind needs no lock,
    a mutated object would need one (`PerceptionBuffer`)."""
    at: float
    faces: tuple[Face, ...] = ()
    bodies: tuple[Body, ...] = ()
    scene: tuple[tuple[str, float], ...] = ()
    error: str = ""
    frame_size: tuple[int, int] = (0, 0)
    #: Identity already RESOLVED, aligned with `faces` — `None` where the ledger did not
    #: recognise that face. Appended last so every existing positional construction
    #: keeps working.
    #:
    #: Load-bearing for anything that reacts to WHO is present rather than to how many:
    #: `Policy.check` is sync and pure (POL-4) and `IdentityLedger.match` is async, so a
    #: policy can never look identity up itself. Whoever fills this in — a `CameraSensor`
    #: out of band, or `identify_person` inside a run — is the only place identity can
    #: enter synchronously readable state.
    names: tuple[str | None, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.error


class IdentityLedger:
    """Enrolled faces, in a `Store` — same shape as `TaskLedger`/`FindingsLog`.

    A `Store` rather than a file so the same code works against `InMemoryStore` in a
    test, `SqliteStore` on a device, and anything else someone implements. Several
    vectors per person on purpose: one enrolment is one angle in one light, and matching
    takes the best of them.

    **Single-writer, checked rather than argued.** `Store` is whole-blob get/put with no
    compare-and-swap (`memory/base.py`), so a read-modify-write here used to be safe only
    because the tools that mutate it are `effect="danger"`, which is not `parallel_safe`
    (`EFFECT_PROFILES[Effect.DANGER].parallel_safe is False`) and is therefore serialised
    within a step. That argument was true and its own last clause named its expiry date —
    "it fails the moment a background thread also writes" — which `harness.contrib.Driver`
    then became. `enroll` and `forget` now go through `read_modify_write`, which detects a
    lost update instead of trusting the argument (ADR-100). A perception loop still must
    NOT cache recognitions in here; it publishes `Reading`s and nothing else.
    """

    KEY = "harness:identities"

    def __init__(self, store: Any, *, threshold: float = DEFAULT_THRESHOLD,
                 margin: float = DEFAULT_MARGIN, key: str = KEY) -> None:
        self._store, self._key = store, key
        self._threshold, self._margin = threshold, margin

    @classmethod
    def from_calibration(cls, store: Any, calibration: Calibration, *,
                         key: str = KEY,
                         accept_thin_evidence: bool = False) -> "IdentityLedger":
        """A ledger whose threshold came from a measurement, and which refuses to exist
        when the measurement says no threshold works.

        This is the constructor to use. The plain one takes `DEFAULT_THRESHOLD`, which is
        a cautious guess and is documented as one; this one refuses a measurement that
        does not support a threshold — which is the state the shipped example
        configuration is actually in (ADR-090).

        "Does not support" is three separate failures, and `Calibration.complaint` names
        which: the distributions overlap, or there are too few pairs, or the gap is
        thinner than a crop change can move a score. The second and third exist because
        the first is not enough: the landmark-geometry experiment produced a `separable`
        calibration from three pairs a side with a gap of 0.0083, and an earlier version
        of this method would have built a ledger on it (ADR-095).

        `accept_thin_evidence=True` is the explicit waiver, the same shape
        `accepts_tainted=` and `allowed_hosts=None` use elsewhere: possible, never
        silent.
        """
        if not calibration.enough_evidence and not accept_thin_evidence:
            raise ValueError(
                "refusing to build an identity ledger on this measurement: "
                f"{calibration.complaint}.\n\n"
                f"  {calibration}\n\n"
                "  Naming a person on these embeddings would be a coin toss wearing a "
                "number.\n\n"
                "  If you have measured this sample and accept it anyway, say so:\n"
                "      IdentityLedger.from_calibration(store, cal, "
                "accept_thin_evidence=True)\n\n"
                "  -> examples/vision_tools.py::DEFAULT_THRESHOLD"
            )
        if calibration.threshold is None:
            raise ValueError(
                "there is no threshold to waive: the distributions overlap, so "
                "`accept_thin_evidence=` has nothing to accept.\n\n"
                f"  {calibration}")
        return cls(store, threshold=calibration.threshold,
                   margin=calibration.margin, key=key)

    async def _rows(self) -> list[dict[str, Any]]:
        return self._parse(await self._store.get(self._key))

    def _parse(self, raw: str | None) -> list[dict[str, Any]]:
        """Sync, and separate from the read, so `read_modify_write` can call it on the
        raw string it already holds — the mutating half must not re-read the store, or
        the lost-update check would be comparing against its own second read (ADR-100).
        """
        if not raw:
            return []
        try:
            rows = json.loads(raw)
        except json.JSONDecodeError:
            # Same call `tasks.py` makes for a corrupt ledger: refuse loudly rather than
            # silently starting from an empty book. Quietly forgetting who someone is
            # would look like the feature working.
            raise RuntimeError(
                f"identity ledger at key {self._key!r} is not readable JSON — refusing "
                f"to start from an empty ledger, which would silently forget everyone "
                f"already enrolled."
            ) from None
        return [r for r in rows if isinstance(r, dict) and r.get("name")]

    async def names(self) -> tuple[str, ...]:
        return tuple(sorted(r["name"] for r in await self._rows()))

    async def enroll(self, name: str, vec: Vector) -> int:
        """Add one angle of `name`. Returns how many angles are now on file."""
        name = name.strip()
        if not name:
            raise ValueError("an enrolled identity needs a name")
        if not vec:
            raise ValueError("an enrolled identity needs a face embedding")
        total = 0

        def mutate(raw: str | None) -> str:
            nonlocal total
            rows = self._parse(raw)
            for row in rows:
                if row["name"].casefold() == name.casefold():
                    row["vectors"].append([float(x) for x in vec])
                    total = len(row["vectors"])
                    break
            else:
                rows.append({"name": name, "vectors": [[float(x) for x in vec]]})
                total = 1
            return json.dumps(rows, ensure_ascii=False)

        await read_modify_write(self._store, self._key, mutate,
                                what="the enrolled identities")
        return total

    async def forget(self, name: str) -> bool:
        """Remove someone entirely. Present because a biometric record somebody can
        never delete is a different product than the one this is meant to be."""
        removed = False

        def mutate(raw: str | None) -> str | None:
            nonlocal removed
            rows = self._parse(raw)
            keep = [r for r in rows if r["name"].casefold() != name.strip().casefold()]
            removed = len(keep) != len(rows)
            return json.dumps(keep, ensure_ascii=False) if removed else None

        await read_modify_write(self._store, self._key, mutate,
                                what="the enrolled identities")
        return removed

    async def match(self, vec: Vector) -> Match:
        rows = await self._rows()
        if not rows:
            return Match(None, 0.0, "no one has been enrolled yet")
        scored = sorted(
            ((max(cosine(vec, v) for v in r["vectors"]), r["name"]) for r in rows),
            key=lambda pair: pair[0], reverse=True)
        top_score, top_name = scored[0]
        if top_score < self._threshold:
            return Match(None, top_score,
                         f"closest is {top_name} at {top_score:.2f}, below the "
                         f"{self._threshold:.2f} threshold")
        if len(scored) > 1 and top_score - scored[1][0] < self._margin:
            return Match(None, top_score,
                         f"ambiguous: {top_name} {top_score:.2f} vs {scored[1][1]} "
                         f"{scored[1][0]:.2f}, closer together than the "
                         f"{self._margin:.2f} margin")
        return Match(top_name, top_score, "clear match")


def _visible(landmarks: Sequence[Any], *idx: int) -> bool:
    return all(
        i < len(landmarks) and getattr(landmarks[i], "visibility", 1.0) >= MIN_VISIBILITY
        for i in idx)


def posture_of(landmarks: Sequence[Any]) -> str:
    """`"đang đứng"` / `"đang ngồi"` / `"không rõ dáng"` from 33 normalised landmarks.

    Image coordinates: `y` grows DOWNWARD, so a knee below a hip has the LARGER `y`.
    Standing puts the knees far below the hips relative to the torso's own height;
    sitting folds the thighs toward horizontal and brings the knees up near hip level.
    The ratio is taken against torso height rather than against the frame so it does not
    change with how far away the person is.

    A heuristic, and only ever three words to the model — reporting joint angles the
    model cannot act on would be tokens spent to sound precise.
    """
    if not _visible(landmarks, L_SHOULDER, R_SHOULDER, L_HIP, R_HIP, L_KNEE, R_KNEE):
        return "không rõ dáng"
    shoulder_y = (landmarks[L_SHOULDER].y + landmarks[R_SHOULDER].y) / 2
    hip_y = (landmarks[L_HIP].y + landmarks[R_HIP].y) / 2
    knee_y = (landmarks[L_KNEE].y + landmarks[R_KNEE].y) / 2
    torso = hip_y - shoulder_y
    if torso <= 1e-6:
        return "không rõ dáng"
    return "đang đứng" if (knee_y - hip_y) / torso >= 0.75 else "đang ngồi"


def facing_camera(landmarks: Sequence[Any]) -> bool:
    """Shoulders roughly level and a visible nose reads as turned toward the lens."""
    if not _visible(landmarks, NOSE, L_SHOULDER, R_SHOULDER):
        return False
    return abs(landmarks[L_SHOULDER].y - landmarks[R_SHOULDER].y) < 0.12


def distance_of(face: Face, frame_size: tuple[int, int]) -> str:
    """How close someone is, from how much of the frame their face fills.

    Crude on purpose. The alternative — a real distance estimate — needs a calibrated
    camera, and "đang ở rất gần" is all the model can act on anyway.
    """
    width = frame_size[0]
    if not width:
        return ""
    share = face.box[2] / width
    if share >= 0.35:
        return "rất gần"
    if share >= 0.15:
        return "ở khoảng cách nói chuyện"
    return "ở xa"


def describe(reading: Reading, names: Sequence[str | None] = ()) -> str:
    """One or two sentences of plain observation — this function IS "it has eyes".

    Written as observation, never as a report about a tool call ("tôi thấy…", not "hàm
    nhận diện trả về…"), because the prompt asks the agent to speak naturally and a
    result phrased as telemetry invites it to narrate its own plumbing instead. Kept
    short for the same reason `Verifier` returns `""` when nothing is wrong: this text
    is paid for on every subsequent model call in the run.

    `names[i]` pairs with `reading.faces[i]`: a string when the ledger recognised that
    face, `None` when it did not, and an empty sequence when nobody asked about
    identity — the three cases produce genuinely different sentences. Defaults to
    `reading.names`, so a `Reading` that already carries resolved identity describes
    itself without the caller restating it.
    """
    names = names or reading.names
    if not reading.ok:
        return f"Tôi không nhìn thấy gì lúc này ({reading.error})."

    scene = ""
    if reading.scene:
        top = ", ".join(label for label, _ in reading.scene[:2])
        scene = f" Chỗ này trông như {top}."

    if not reading.faces and not reading.bodies:
        return f"Trước mặt tôi không có ai.{scene}"

    parts: list[str] = []
    for i, face in enumerate(reading.faces):
        who = names[i] if i < len(names) else ""
        posture = reading.bodies[i].posture if i < len(reading.bodies) else ""
        how_far = distance_of(face, reading.frame_size)
        if who:
            subject = who
        elif names:
            subject = "một người tôi chưa biết là ai"
        else:
            subject = "một người"
        detail = ", ".join(x for x in (posture, how_far) if x)
        parts.append(f"{subject} ({detail})" if detail else subject)

    # A body with no face is a real and useful observation: someone turned away, or at
    # the edge of the frame. Saying so is better than pretending the room is empty.
    extra = len(reading.bodies) - len(reading.faces)
    if extra > 0:
        parts.append(f"{extra} người nữa mà tôi không thấy rõ mặt")

    if len(parts) == 1:
        return f"Tôi thấy {parts[0]}.{scene}"
    return f"Tôi thấy {'; '.join(parts[:-1])}; và {parts[-1]}.{scene}"


# ── layer 2: adapters — the only place hardware or a model file appears ──────────

class Detector(Protocol):
    """What layers 1 and 3 need from a vision backend, and nothing more.

    Four methods, so `FakeDetector` in a test is a dozen lines and every branch of the
    business logic above is reachable without a camera, a `.task` file, or a GPU. The
    same reason `harness.models.fake.FakeModel` exists one level up.
    """

    def detect_faces(self, frame: Any) -> Sequence[Face]: ...
    def detect_bodies(self, frame: Any) -> Sequence[Body]: ...
    def embed_face(self, frame: Any, box: tuple[int, int, int, int]) -> Vector: ...
    def classify_scene(self, frame: Any) -> Sequence[tuple[str, float]]: ...


@dataclass
class FakeDetector:
    """Scripted perception. What the tests and the demo run against."""
    faces: Sequence[Face] = ()
    bodies: Sequence[Body] = ()
    scene: Sequence[tuple[str, float]] = ()
    embeddings: Mapping[tuple[int, int, int, int], Vector] = field(default_factory=dict)

    def detect_faces(self, frame: Any) -> Sequence[Face]:
        return tuple(self.faces)

    def detect_bodies(self, frame: Any) -> Sequence[Body]:
        return tuple(self.bodies)

    def embed_face(self, frame: Any, box: tuple[int, int, int, int]) -> Vector:
        return self.embeddings.get(box, ())

    def classify_scene(self, frame: Any) -> Sequence[tuple[str, float]]:
        return tuple(self.scene)


class MediaPipeDetector:
    """`Detector` over MediaPipe Tasks. **All four capabilities have now run real
    inference against real models and real photographs** (ADR-090) — the earlier version
    of this docstring said "treat this as unrun code", and that is no longer true.

    Measured, on `mediapipe` 1.0.1, `blaze_face_short_range` + `pose_landmarker_lite` +
    `efficientnet_lite0` + `mobilenet_v3_small`, against a 1024x820 portrait
    (`tests/vision_probe.py` reproduces it):

        detect_faces   ( 4426 ms first call): 1 face, score 0.922, box (283,115,234,234)
        detect_bodies  (  292 ms): 1 body, facing_camera=True, posture "không rõ dáng"
        classify_scene (  243 ms): ('suit', 0.592), ('groom', 0.176)
        embed_face     (   71 ms): 1024 dimensions

    The first `detect` pays for `create_from_options` (model load and graph build), which
    is why tasks are created lazily and CACHED, why `close()` exists, and why the CALLER
    owns it — the same rule as `CodingProfile(store=...)`. `posture_of` returning "không
    rõ dáng" on a head-and-shoulders portrait is correct, not a failure: hip landmarks
    are not visible, and the fallback path is what ran.

    **Identity does not work with a generic image embedder, and that is now measured
    rather than suspected.** `ImageEmbedder` + `mobilenet_v3_small` puts the same person
    rotated at 0.2870 and two different people at 0.5613 — inverted, not merely
    overlapping. MediaPipe Tasks ships no face-recognition model; `embed_model=` needs a
    real one (ArcFace, FaceNet, a vendor API). See `DEFAULT_THRESHOLD` and `calibrate()`.

    **The model files are still not bundled and must be supplied.** `mediapipe` 1.0.1
    ships no `.task` or `.tflite` anywhere in the package, `mediapipe.solutions` no longer
    exists (the legacy API is gone), and the namespace that works is
    `mediapipe.tasks.python.vision`. Each capability is independent: pass only the models
    you have and the corresponding method returns empty rather than raising, so a
    face-only setup is a supported configuration.

    **The runtime also needs `libEGL.so.1` and `libGLESv2.so.2`, which are system
    libraries `pip` does not install.** Call `MediaPipeDetector.preflight()` at startup —
    it needs no model, no camera and no frame, and returns the names of whatever cannot
    be loaded. Skip it and the first `detect` raises with the same advice instead
    (ADR-092).

    What remains unverified: a live camera (`cv2.VideoCapture(0)` has no device here), and
    accuracy on faces other than the handful of public test photographs above.
    """

    def __init__(self, *, face_model: str | None = None, pose_model: str | None = None,
                 scene_model: str | None = None, embed_model: str | None = None,
                 landmark_model: str | None = None,
                 min_face_confidence: float = 0.5, max_scene_labels: int = 3,
                 scene_score_threshold: float = 0.15) -> None:
        #: `landmark_model` wins over `embed_model` for `embed_face`, when both are
        #: given. An `ImageEmbedder` encodes the PICTURE (measured unusable for
        #: identity, ADR-090); the face landmarker encodes the GEOMETRY, which is at
        #: least the right kind of thing — see `embed_face` for exactly how far that
        #: has been measured, which is not far (ADR-095).
        self._paths = {"face": face_model, "pose": pose_model,
                       "scene": scene_model, "embed": embed_model,
                       "landmark": landmark_model}
        self._min_face_confidence = min_face_confidence
        self._max_scene_labels = max_scene_labels
        self._scene_score_threshold = scene_score_threshold
        self._tasks: dict[str, Any] = {}

    # -- construction of the four tasks, each optional --------------------------------

    def _mp(self) -> tuple[Any, Any, Any]:
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python import vision as mpv
        return mp, BaseOptions, mpv

    def _task(self, kind: str) -> Any:
        if kind in self._tasks:
            return self._tasks[kind]
        path = self._paths.get(kind)
        if not path:
            return None
        try:
            return self._build(kind, path)
        except OSError as exc:
            # MediaPipe 1.0.1 `dlopen`s its C bindings when the FIRST task is created,
            # and the failure surfaces as an `OSError` from `ctypes.CDLL` naming a
            # library — which reads like a broken install or a bad model path and is
            # neither. This whole capability sat parked behind that diagnosis for three
            # commits (ADR-092).
            raise RuntimeError(_native_library_advice(exc)) from exc

    def _build(self, kind: str, path: str) -> Any:
        mp, BaseOptions, mpv = self._mp()
        base = BaseOptions(model_asset_path=path)
        if kind == "face":
            built = mpv.FaceDetector.create_from_options(mpv.FaceDetectorOptions(
                base_options=base, min_detection_confidence=self._min_face_confidence))
        elif kind == "pose":
            built = mpv.PoseLandmarker.create_from_options(mpv.PoseLandmarkerOptions(
                base_options=base, num_poses=4))
        elif kind == "scene":
            built = mpv.ImageClassifier.create_from_options(mpv.ImageClassifierOptions(
                base_options=base, max_results=self._max_scene_labels,
                score_threshold=self._scene_score_threshold))
        elif kind == "landmark":
            built = mpv.FaceLandmarker.create_from_options(mpv.FaceLandmarkerOptions(
                base_options=base, num_faces=1,
                min_face_detection_confidence=self._min_face_confidence))
        elif kind == "embed":
            # l2_normalize so `cosine()` compares directions on a common scale, which is
            # the assumption `DEFAULT_THRESHOLD` is stated against.
            built = mpv.ImageEmbedder.create_from_options(mpv.ImageEmbedderOptions(
                base_options=base, l2_normalize=True))
        else:
            raise ValueError(f"unknown task {kind!r}")
        self._tasks[kind] = built
        return built

    @staticmethod
    def preflight() -> tuple[str, ...]:
        """Which of MediaPipe's native libraries cannot be loaded here — `()` when all
        of them can.

        Callable before any model file exists, so a program can fail at startup with an
        actionable message instead of at the first frame with an opaque one.
        """
        return missing_native_libraries()

    @staticmethod
    def _image(frame: Any) -> Any:
        """BGR ndarray (what OpenCV hands back) -> `mp.Image` in SRGB."""
        import cv2
        import mediapipe as mp
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        return mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

    # -- the Detector protocol --------------------------------------------------------

    def detect_faces(self, frame: Any) -> Sequence[Face]:
        task = self._task("face")
        if task is None:
            return ()
        out = []
        for det in task.detect(self._image(frame)).detections:
            b = det.bounding_box
            score = det.categories[0].score if det.categories else 1.0
            out.append(Face(box=(int(b.origin_x), int(b.origin_y),
                                 int(b.width), int(b.height)), score=float(score)))
        # Left to right, so `faces[i]` and a caller's `names[i]` stay aligned across
        # frames instead of reshuffling with detector confidence.
        return tuple(sorted(out, key=lambda f: f.box[0]))

    def detect_bodies(self, frame: Any) -> Sequence[Body]:
        task = self._task("pose")
        if task is None:
            return ()
        result = task.detect(self._image(frame))
        return tuple(Body(posture=posture_of(lms), facing_camera=facing_camera(lms))
                     for lms in result.pose_landmarks)

    def embed_face(self, frame: Any, box: tuple[int, int, int, int]) -> Vector:
        """A vector for one face. Geometry when `landmark_model` was given, otherwise
        picture content from `embed_model`.

        **Neither is a face-recognition model, and the difference between them is
        measured (ADR-090, ADR-095).** `ImageEmbedder` encodes pose, light and
        background: the same person photographed rotated scored 0.2870 while two
        strangers scored 0.5613 — inverted, so no threshold works. Landmark geometry
        fixes exactly that case, because a rotation is something geometry can undo, and
        `align_landmarks` undoes it.

        **Landmark geometry is better and still not usable.** Measured through this
        method, on the photographs available here (3 same-person and 7 different-person
        pairs, from two people):

            image embedder .... worst same 0.2870, best different 0.5613, overlap 0.2743
            landmark geometry . worst same 0.8772, best different 0.9932, overlap 0.1161

        The overlap roughly halves and does not close. `calibrate()` refuses a threshold
        for either, which is the honest answer: a face-recognition embedder (ArcFace,
        FaceNet, a vendor API) is what this argument is missing, not a better number.

        Two people is not a sample, and every figure above should be re-measured on
        faces that matter to you with `tests/vision_probe.py`.
        """
        landmarker = self._task("landmark")
        if landmarker is not None:
            crop = _crop(frame, _pad_box(box, LANDMARK_CROP_PAD))
            if crop is None:
                return ()
            result = landmarker.detect(self._image(crop))
            if not result.face_landmarks:
                return ()
            height, width = crop.shape[0], crop.shape[1]
            # Back to pixels before aligning: MediaPipe returns x/y normalised to the
            # image, so a non-square crop would otherwise arrive pre-distorted and the
            # eye-line rotation would be measured on the wrong triangle.
            return align_landmarks([(p.x * width, p.y * height, p.z * width)
                                    for p in result.face_landmarks[0]])

        task = self._task("embed")
        if task is None:
            return ()
        crop = _crop(frame, box)
        if crop is None:
            return ()
        result = task.embed(self._image(crop))
        if not result.embeddings:
            return ()
        return tuple(float(x) for x in result.embeddings[0].embedding)

    def classify_scene(self, frame: Any) -> Sequence[tuple[str, float]]:
        task = self._task("scene")
        if task is None:
            return ()
        result = task.classify(self._image(frame))
        if not result.classifications:
            return ()
        return tuple((c.category_name or c.display_name or "?", float(c.score))
                     for c in result.classifications[0].categories)

    def close(self) -> None:
        for task in self._tasks.values():
            closer = getattr(task, "close", None)
            if closer is not None:
                closer()
        self._tasks.clear()


#: The shared libraries MediaPipe's C bindings `dlopen`. Not Python packages: `pip
#: install mediapipe` brings neither, and a slim container image has neither. On
#: Debian/Ubuntu they are `libegl1` and `libgles2` (`libgles2-mesa` and `libglesv2` are
#: both wrong names on noble — measured while getting this to run).
NATIVE_LIBRARIES = ("libEGL.so.1", "libGLESv2.so.2")

#: Per-distribution install command. Only the one family is listed, because it is the
#: only one that has been tried here; a guess for the others would be exactly the kind of
#: confident wrong answer the message exists to replace.
NATIVE_LIBRARY_INSTALL = "apt-get install -y --no-install-recommends libegl1 libgles2"


def missing_native_libraries() -> tuple[str, ...]:
    """Which of `NATIVE_LIBRARIES` cannot be loaded, in order. `()` when all can.

    Loads them directly rather than asking a package manager: what matters is whether
    THIS process can `dlopen` them, which is the same question MediaPipe asks.
    """
    import ctypes
    missing = []
    for name in NATIVE_LIBRARIES:
        try:
            ctypes.CDLL(name)
        except OSError:
            missing.append(name)
    return tuple(missing)


def _native_library_advice(exc: OSError) -> str:
    missing = missing_native_libraries()
    named = ", ".join(missing) if missing else str(exc)
    return (
        f"MediaPipe could not load its native libraries: {named}\n\n"
        f"  These are system shared libraries, not Python packages — `pip install "
        f"mediapipe`\n  does not bring them, and a slim container image has neither. "
        f"Nothing is wrong\n  with your model file or your code.\n\n"
        f"  Fix (Debian/Ubuntu):  {NATIVE_LIBRARY_INSTALL}\n\n"
        f"  Check before you run: MediaPipeDetector.preflight() -> "
        f"{missing or ()}\n\n"
        f"  Original error: {exc}"
    )


def _pad_box(box: tuple[int, int, int, int],
             fraction: float) -> tuple[int, int, int, int]:
    """Grow a box about its centre. `_crop` clamps it to the frame afterwards, so a box
    that grows past an edge is not this function's problem."""
    x, y, w, h = box
    dx, dy = int(w * fraction / 2), int(h * fraction / 2)
    return (x - dx, y - dy, w + 2 * dx, h + 2 * dy)


def _crop(frame: Any, box: tuple[int, int, int, int]) -> Any:
    """Clamped crop, `None` when the box does not overlap the frame.

    Clamped rather than trusted: a detector's box can extend past the edge, and numpy
    slicing past an edge silently yields a smaller — or empty — array, which would reach
    the embedder as a shape error much further from the cause.
    """
    try:
        height, width = frame.shape[0], frame.shape[1]
    except Exception:
        return None
    x, y, w, h = box
    x0, y0 = max(0, int(x)), max(0, int(y))
    x1, y1 = min(int(width), x0 + max(0, int(w))), min(int(height), y0 + max(0, int(h)))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return frame[y0:y1, x0:x1]


class Camera:
    """`cv2.VideoCapture`, with every failure turned into a sentence.

    Measured on this environment with no camera present: `VideoCapture(0)` raises
    nothing, `isOpened()` returns `False`, and `read()` returns `(False, None)` — the
    backends log to stderr from C++ and Python sees a clean negative. So the honest
    shape here is a `(frame, error)` pair, never an exception: "I can't see anything
    right now" is something the agent can say, and a traceback out of a tool is not.

    Opened lazily on first `grab()`, so constructing a profile does not seize the
    device, and `close()` is the caller's to call — `Agent` is frozen with no lifecycle
    (`agent.py`), so nothing downstream can release this for you.
    """

    def __init__(self, index: "int | str" = 0, *, capture: Any = None,
                 width: int | None = None, height: int | None = None) -> None:
        #: A device index, or a PATH — `cv2.VideoCapture` takes either, and a recorded
        #: clip is how the rest of this pipeline gets exercised where no device exists:
        #: real decoding, real frames, real inference, everything but the hardware
        #: (`tests/vision_probe.py`, ADR-094). Typed `int | str` because it always
        #: accepted both and only the annotation said otherwise.
        self.index = index
        self._cap = capture
        self._own = capture is None
        self._size = (width, height)

    def _open(self) -> tuple[Any, str]:
        if self._cap is not None:
            return self._cap, ""
        try:
            import cv2
        except ImportError:
            return None, "opencv chưa được cài (pip install opencv-python)"
        cap = cv2.VideoCapture(self.index)
        if not cap.isOpened():
            cap.release()
            return None, f"không mở được camera {self.index}"
        width, height = self._size
        if width:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        if height:
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        self._cap = cap
        return cap, ""

    def grab(self) -> tuple[Any, str]:
        """`(frame, "")` or `(None, reason)`. Never raises."""
        cap, err = self._open()
        if err:
            return None, err
        try:
            ok, frame = cap.read()
        except Exception as exc:                       # a disconnected USB camera
            return None, f"đọc camera lỗi: {type(exc).__name__}"
        if not ok or frame is None:
            # `self.index` is meaningless when a `capture=` was injected, and naming
            # "camera 0" for an exhausted video file sent the reader looking at the
            # wrong thing — measured while running the pipeline over a clip (ADR-094).
            return None, f"{self._source()} không trả về hình"
        return frame, ""

    def _source(self) -> str:
        if self._own:
            return f"camera {self.index}"
        return "nguồn hình được truyền vào"

    def close(self) -> None:
        if self._cap is not None and self._own:
            release = getattr(self._cap, "release", None)
            if release is not None:
                release()
        self._cap = None


class PerceptionBuffer:
    """The latest `Reading` and the frame it came from, published by rebinding.

    Sized at one because that is what is needed and no more: a `look` refreshes it, and
    `identify_person` reads it without paying for another capture. Rebinding an
    immutable `Reading` is atomic under the GIL, so a future sensor thread can publish
    here while a tool reads — which is the whole reason `Reading` is frozen.
    """

    __slots__ = ("reading", "frame")

    def __init__(self) -> None:
        self.reading: Reading = Reading(at=0.0, error="chưa nhìn lần nào")
        self.frame: Any = None

    def publish(self, reading: Reading, frame: Any = None) -> None:
        self.reading, self.frame = reading, frame


# ── layer 3: the tools — glue, and only glue ────────────────────────────────────

#: `look` refreshes perception; `identify_person` re-reads the same frame. Named here so
#: `vision_profile.py` can declare them `sensitive=` without restating string literals.
LOOK, IDENTIFY, ENROLL = "look", "identify_person", "enroll_person"


class VisionTools:
    def __init__(self, *, camera: Camera, detector: Detector,
                 ledger: IdentityLedger, buffer: PerceptionBuffer,
                 enable_enrollment: bool = False) -> None:
        self.camera, self.detector = camera, detector
        self.ledger, self.buffer = ledger, buffer
        self.enable_enrollment = enable_enrollment

    def observe(self) -> Reading:
        """Capture and run inference once; publish and return the `Reading`.

        Sync, and called from inside an async tool body on purpose: MediaPipe inference
        is CPU-bound C++ that releases nothing an event loop can use, so wrapping it in
        a coroutine would add a hop and no concurrency. It DOES block the loop for the
        duration of one inference pass, which is the honest cost of doing this in-process
        — a deployment that cannot afford that should move perception to the sensor
        thread the buffer is already designed for.
        """
        frame, err = self.camera.grab()
        if err:
            reading = Reading(at=time.time(), error=err)
            self.buffer.publish(reading, None)
            return reading
        try:
            faces = tuple(self.detector.detect_faces(frame))
            bodies = tuple(self.detector.detect_bodies(frame))
            scene = tuple(self.detector.classify_scene(frame))
        except Exception as exc:
            # A model that fails mid-inference is a thing the agent should be able to
            # say, not a traceback out of a tool call.
            reading = Reading(at=time.time(),
                              error=f"nhận diện lỗi: {type(exc).__name__}: {exc}")
            self.buffer.publish(reading, frame)
            return reading
        try:
            height, width = int(frame.shape[0]), int(frame.shape[1])
        except Exception:
            height = width = 0
        reading = Reading(at=time.time(), faces=faces, bodies=bodies, scene=scene,
                          frame_size=(width, height))
        self.buffer.publish(reading, frame)
        return reading

    async def _identify_all(self, reading: Reading, frame: Any) -> list[str | None]:
        out: list[str | None] = []
        for face in reading.faces:
            vec = face.embedding or self.detector.embed_face(frame, face.box)
            out.append((await self.ledger.match(vec)).name if vec else None)
        return out

    def tools(self) -> list[Any]:
        vt = self

        @tool(effect=Effect.EXTERNAL)
        async def look() -> str:
            """Nhìn một lần qua camera và cho biết bạn thấy gì: có ai trước mặt, họ
            đang đứng hay ngồi, ở gần hay xa, và chỗ này trông như thế nào. Gọi khi
            bạn cần biết hiện tại trước mặt có gì; muốn biết TÊN của người đang thấy
            thì gọi `identify_person`."""
            return describe(vt.observe())

        @tool(effect=Effect.READ)
        async def identify_person() -> str:
            """Cho biết người đang thấy là ai, nếu bạn đã từng ghi nhớ họ. Dùng lại
            hình của lần `look` gần nhất, không chụp lại. Khi trả về "chưa biết là ai"
            và bạn đang nói chuyện trực tiếp với người đó, hãy hỏi tên họ."""
            reading = vt.buffer.reading
            if not reading.ok:
                return f"Tôi chưa nhìn thấy gì ({reading.error}) — hãy gọi `look` trước."
            if not reading.faces:
                return "Lần nhìn gần nhất không có khuôn mặt nào."
            names = await vt._identify_all(reading, vt.buffer.frame)
            known = await vt.ledger.names()
            said = describe(reading, names)
            if any(n is None for n in names) and known:
                said += f" (Tôi đang nhớ mặt: {', '.join(known)}.)"
            return said

        specs = [look, identify_person]
        if not vt.enable_enrollment:
            return specs

        @tool(effect=Effect.DANGER)
        async def enroll_person(name: str) -> str:
            """Ghi nhớ khuôn mặt đang thấy dưới tên này, để lần sau nhận ra. Chỉ gọi
            sau khi CHÍNH người đó vừa nói tên cho bạn trong cuộc trò chuyện này —
            không bao giờ đoán tên, và không bao giờ ghi nhớ người chỉ tình cờ đi qua
            khung hình. Mỗi lần gọi sẽ cần một người xác nhận."""
            reading = vt.buffer.reading
            if not reading.ok:
                return f"Tôi chưa nhìn thấy gì ({reading.error}) — hãy gọi `look` trước."
            # The consent rule that IS enforceable here: with two faces in frame there
            # is no fact in the reading saying which one is speaking, so guessing would
            # file a biometric record against the wrong person. The rest of the rule
            # (that they actually said their name) is not decidable from a frame — which
            # is why this tool is `danger`, so a human confirms every call.
            if len(reading.faces) != 1:
                return (f"Đang thấy {len(reading.faces)} khuôn mặt — tôi không ghi nhớ "
                        f"khi chưa chắc mặt nào là của người đang nói.")
            vec = vt.detector.embed_face(vt.buffer.frame, reading.faces[0].box)
            if not vec:
                return "Không lấy được đặc trưng khuôn mặt từ hình này."
            total = await vt.ledger.enroll(name, vec)
            return f"Đã ghi nhớ {name} ({total} góc mặt trên hồ sơ)."

        return [*specs, enroll_person]


# ── the demo: every layer, running, no camera and no model file ─────────────────

def _demo() -> None:
    import asyncio

    from harness.memory.inmemory import InMemoryStore

    thiep = (1.0, 0.0, 0.10)
    nghia = (0.0, 1.0, 0.10)
    stranger = (0.5, 0.5, 0.90)
    thiep_tilted = (0.97, 0.05, 0.12)

    async def main() -> None:
        print("=" * 72)
        print("1. Layer 1 — identity logic, hand-written vectors, no camera")
        print("=" * 72)
        store = InMemoryStore()
        ledger = IdentityLedger(store)
        await ledger.enroll("Thiep", thiep)
        await ledger.enroll("Thiep", thiep_tilted)     # a second angle
        await ledger.enroll("Nghia", nghia)
        print(f"  enrolled        : {', '.join(await ledger.names())}")
        for label, vec in (("Thiep, head on", thiep), ("Thiep, tilted", thiep_tilted),
                           ("someone else", stranger)):
            m = await ledger.match(vec)
            print(f"  {label:<16} -> {str(m.name):<8} {m.score:.2f}  ({m.reason})")
        loose = IdentityLedger(store, threshold=0.50, margin=0.50)
        m = await loose.match(stranger)
        print(f"  {'ambiguous':<16} -> {str(m.name):<8} {m.score:.2f}  ({m.reason})")

        print()
        print("=" * 72)
        print("2. Layer 1 — posture from hand-written landmarks")
        print("=" * 72)

        class LM:
            def __init__(self, y: float, vis: float = 1.0) -> None:
                self.x, self.y, self.z, self.visibility = 0.5, y, 0.0, vis

        def pose(shoulder: float, hip: float, knee: float, vis: float = 1.0):
            lms = [LM(0.0, vis)] * 33
            lms[L_SHOULDER] = lms[R_SHOULDER] = LM(shoulder, vis)
            lms[L_HIP] = lms[R_HIP] = LM(hip, vis)
            lms[L_KNEE] = lms[R_KNEE] = LM(knee, vis)
            return lms

        for label, lms in (("standing", pose(0.20, 0.50, 0.80)),
                           ("sitting", pose(0.20, 0.50, 0.58)),
                           ("occluded", pose(0.20, 0.50, 0.80, vis=0.1))):
            print(f"  {label:<10} -> {posture_of(lms)}")

        print()
        print("=" * 72)
        print("3. Layer 2+3 — the tools, on a FakeDetector and a fake capture")
        print("=" * 72)
        box = (100, 60, 220, 220)
        detector = FakeDetector(
            faces=(Face(box=box, score=0.97),),
            bodies=(Body(posture="đang ngồi"),),
            scene=(("home office", 0.71), ("desk", 0.44)),
            embeddings={box: thiep})

        class FakeCapture:
            """Stands in for cv2.VideoCapture: a 480x640 BGR-shaped frame."""
            def isOpened(self) -> bool:
                return True

            def read(self):
                import numpy
                return True, numpy.zeros((480, 640, 3), dtype=numpy.uint8)

            def release(self) -> None:
                pass

        buffer = PerceptionBuffer()
        tools = VisionTools(camera=Camera(capture=FakeCapture()), detector=detector,
                            ledger=ledger, buffer=buffer, enable_enrollment=True)
        by_name = {t.name: t for t in tools.tools()}
        for name, spec in by_name.items():
            print(f"  {spec.effect.value:<9} {name}")
        print()
        print(f"  look()            -> {await by_name[LOOK].fn()}")
        print(f"  identify_person() -> {await by_name[IDENTIFY].fn()}")

        print()
        print("=" * 72)
        print("4. The same frame with somebody the ledger has never seen")
        print("=" * 72)
        detector.embeddings = {box: stranger}
        await by_name[LOOK].fn()
        print(f"  identify_person() -> {await by_name[IDENTIFY].fn()}")
        print(f"  enroll_person()   -> {await by_name[ENROLL].fn(name='Minh')}")
        print(f"  identify_person() -> {await by_name[IDENTIFY].fn()}")

        print()
        print("=" * 72)
        print("5. Two faces — enrolment refuses rather than guess")
        print("=" * 72)
        detector.faces = (Face(box=box), Face(box=(400, 60, 180, 180)))
        detector.bodies = (Body(posture="đang ngồi"), Body(posture="đang đứng"))
        await by_name[LOOK].fn()
        print(f"  look()            -> {describe(buffer.reading)}")
        print(f"  enroll_person()   -> {await by_name[ENROLL].fn(name='Ai đó')}")

        print()
        print("=" * 72)
        print("6. No camera at all — measured behaviour, not a traceback")
        print("=" * 72)
        blind = VisionTools(camera=Camera(index=99), detector=detector, ledger=ledger,
                            buffer=PerceptionBuffer(), enable_enrollment=True)
        blind_tools = {t.name: t for t in blind.tools()}
        print(f"  look()            -> {await blind_tools[LOOK].fn()}")
        print(f"  identify_person() -> {await blind_tools[IDENTIFY].fn()}")

    asyncio.run(main())


if __name__ == "__main__":
    _demo()
