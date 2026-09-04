"""The vision probe — a manual script, never collected by the suite.

`MediaPipeDetector` (`examples/vision_tools.py`) was written against the installed API
surface and, for three commits, had never executed a single inference: no model file
ships with `mediapipe` 1.0.1, and there is no camera here. Its own docstring said "treat
this class as unrun code" (ADR-077). This script is what changed that (ADR-090). It
fetches four real models and a few public photographs, runs all four capabilities, and
then does the measurement the identity threshold always needed and never had.

What it needs, none of which the test suite may assume:

    network       storage.googleapis.com (models and images)
    system libs   libEGL.so.1 and libGLESv2.so.2 — a bare container has neither.
                  Checked up front by `MediaPipeDetector.preflight()`, which exists
                  because the raw failure is an OSError from ctypes.CDLL that looks
                  nothing like a missing system library (ADR-092)

Run it deliberately:

    PYTHONPATH=src:examples python3 tests/vision_probe.py

The numbers it prints are quoted in `DEFAULT_THRESHOLD`, in `MediaPipeDetector`, and in
ADR-090. Re-run it rather than trusting those copies if anything here changes.
"""
import asyncio
import itertools
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, "src")               # standalone: `conftest.py` never applies here
sys.path.insert(0, "examples")

MODELS = "https://storage.googleapis.com/mediapipe-models/"
ASSETS = "https://storage.googleapis.com/mediapipe-assets/"

WANTED = {
    "blaze_face_short_range.tflite":
        MODELS + "face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite",
    "pose_landmarker_lite.task":
        MODELS + "pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
    "efficientnet_lite0.tflite":
        MODELS + "image_classifier/efficientnet_lite0/float32/1/efficientnet_lite0.tflite",
    "mobilenet_v3_small.tflite":
        MODELS + "image_embedder/mobilenet_v3_small/float32/1/mobilenet_v3_small.tflite",
    "face_landmarker.task":
        MODELS + "face_landmarker/face_landmarker/float16/1/face_landmarker.task",
    "portrait.jpg": ASSETS + "portrait.jpg",
    "portrait_rotated.jpg": ASSETS + "portrait_rotated.jpg",
    "portrait_small.jpg": ASSETS + "portrait_small.jpg",
    "business-person.png": ASSETS + "business-person.png",
    "segmentation_input_rotation0.jpg": ASSETS + "segmentation_input_rotation0.jpg",
}

#: Which files are the same human. `portrait*` are three renderings of one photograph;
#: everything else is somebody else. Written down here rather than inferred from
#: filenames, because a labelling a script guesses is a labelling nobody checked.
ONE_PERSON = frozenset({"portrait.jpg", "portrait_rotated.jpg", "portrait_small.jpg"})


def fetch(into: Path) -> dict[str, Path]:
    into.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, url in WANTED.items():
        path = into / name
        if not path.exists() or path.stat().st_size < 1024:
            print(f"  fetching {name} ...", flush=True)
            urllib.request.urlretrieve(url, path)
        out[name] = path
    return out


def main(into: Path) -> int:
    import cv2

    from vision_tools import MediaPipeDetector, calibrate, cosine

    missing = MediaPipeDetector.preflight()
    if missing:
        print(f"  MediaPipe cannot load {', '.join(missing)} — nothing below will run.")
        print("  Fix: apt-get install -y --no-install-recommends libegl1 libgles2")
        return 1

    files = fetch(into)
    det = MediaPipeDetector(
        face_model=str(files["blaze_face_short_range.tflite"]),
        pose_model=str(files["pose_landmarker_lite.task"]),
        scene_model=str(files["efficientnet_lite0.tflite"]),
        embed_model=str(files["mobilenet_v3_small.tflite"]),
    )

    print("\n=== all four capabilities, on one real photograph ===")
    frame = cv2.imread(str(files["portrait.jpg"]))
    print(f"  frame {frame.shape}")
    for label, call in (("detect_faces", lambda: det.detect_faces(frame)),
                        ("detect_bodies", lambda: det.detect_bodies(frame)),
                        ("classify_scene", lambda: det.classify_scene(frame))):
        t0 = time.perf_counter()
        value = call()
        print(f"  {label:<15} ({(time.perf_counter() - t0) * 1000:6.0f} ms) {value}")

    faces = det.detect_faces(frame)
    t0 = time.perf_counter()
    vec = det.embed_face(frame, faces[0].box)
    print(f"  {'embed_face':<15} ({(time.perf_counter() - t0) * 1000:6.0f} ms) "
          f"{len(vec)} dimensions")

    print("\n=== same face, perturbed — the upper bound on how tight a threshold can be ===")
    x, y, w, h = faces[0].box
    same: list[float] = []

    def score(label: str, other_vec, *, is_same: bool = True) -> None:
        value = cosine(vec, other_vec)
        if is_same:
            same.append(value)
        print(f"  {label:<42} {value:.4f}")

    score("identical crop", det.embed_face(frame, faces[0].box))
    score("shifted 8 px", det.embed_face(frame, (x + 8, y + 8, w, h)))
    score("shifted 20 px", det.embed_face(frame, (x + 20, y + 20, w, h)))
    score("box 10% wider", det.embed_face(frame, (x, y, int(w * 1.1), int(h * 1.1))))
    score("box 20% wider", det.embed_face(frame, (x, y, int(w * 1.2), int(h * 1.2))))
    score("brightness +25",
          det.embed_face(cv2.convertScaleAbs(frame, alpha=1.0, beta=25), faces[0].box))
    q40 = cv2.imdecode(cv2.imencode(".jpg", frame,
                                    [int(cv2.IMWRITE_JPEG_QUALITY), 40])[1], 1)
    score("re-encoded at JPEG q=40", det.embed_face(q40, faces[0].box))

    print("\n=== every photograph, each face detected properly ===")
    vectors: dict[str, tuple] = {}
    for name in WANTED:
        if name.endswith((".jpg", ".png")):
            img = cv2.imread(str(files[name]))
            found = det.detect_faces(img)
            print(f"  {name:<34} {img.shape} faces={len(found)} "
                  f"{[(f.box, round(f.score, 3)) for f in found]}")
            if found:
                vectors[name] = det.embed_face(img, found[0].box)

    print("\n=== cross-pairs, labelled ===")
    different: list[float] = []
    for a, b in itertools.combinations(sorted(vectors), 2):
        value = cosine(vectors[a], vectors[b])
        is_same = {a, b} <= ONE_PERSON
        (same if is_same else different).append(value)
        print(f"  {a:<34} vs {b:<34} {value:.4f}  "
              f"({'SAME person' if is_same else 'different'})")

    print("\n=== the verdict ===")
    print("  " + str(calibrate(same, different)).replace("\n", "\n  "))

    geometry(files)
    pipeline(det, files, into)
    det.close()
    return 0


def geometry(files) -> None:
    """The other candidate embedder: landmark GEOMETRY instead of picture content.

    MediaPipe Tasks ships no face-recognition model, which is why identity does not work
    here (ADR-090). It does ship a 478-point face landmarker, and geometry can undo a
    rotation — which was the generic embedder's worst case by far. Worth measuring, and
    measured through the SAME code path the library ships (`landmark_model=`), so these
    numbers describe the shipped behaviour and not a scratch script (ADR-095).
    """
    import cv2

    from vision_tools import MediaPipeDetector, calibrate, cosine

    det = MediaPipeDetector(face_model=str(files["blaze_face_short_range.tflite"]),
                            landmark_model=str(files["face_landmarker.task"]))
    print("\n=== landmark geometry, through `landmark_model=` ===")
    vectors = {}
    for name in WANTED:
        if not name.endswith((".jpg", ".png")):
            continue
        img = cv2.imread(str(files[name]))
        faces = det.detect_faces(img)
        vec = det.embed_face(img, faces[0].box) if faces else ()
        print(f"  {name:<34} faces={len(faces)} vector={len(vec)} values")
        if vec:
            vectors[name] = vec

    same: list[float] = []
    different: list[float] = []
    for a, b in itertools.combinations(sorted(vectors), 2):
        value = cosine(vectors[a], vectors[b])
        is_same = {a, b} <= ONE_PERSON
        (same if is_same else different).append(value)
        print(f"  {a:<34} vs {b:<34} {value:+.4f}  "
              f"({'SAME person' if is_same else 'different'})")
    if same and different:
        print("  " + str(calibrate(same, different)).replace("\n", "\n  "))
    print()
    print("  Read this as a hint, not a result. Two people is not a sample, and the")
    print("  refusal above is the point: `separable` was the only gate until this")
    print("  experiment walked through it, so `enough_evidence` now also asks for")
    print("  pairs and for a gap wider than a crop change can move a score.")
    det.close()


def pipeline(det, files, into: Path) -> None:
    """The whole perception pipeline on real decoded frames.

    There is no camera here, and there is no way to fake one that proves anything —
    `FakeDetector` and a stub `capture` object are what the unit tests already use, and
    they exercise the sensor's logic, not the pipeline. `cv2.VideoCapture` reads a FILE
    through the same interface it reads a device through, so a scripted clip gets
    everything except the hardware: real H.264 decoding, real frames of real people,
    real inference per frame, and `CameraSensor`'s debounce and baseline running over
    the result (ADR-094).

    The clip is empty -> person A -> person B -> empty, four frames each, so every
    transition the sensor claims to detect has a moment to happen in.
    """
    import cv2
    import numpy as np

    from harness.memory.inmemory import InMemoryStore
    from vision_sensor import CameraSensor
    from vision_tools import Camera, IdentityLedger, PerceptionBuffer

    size, per = (640, 480), 4
    blocks = [
        np.full((size[1], size[0], 3), 90, dtype=np.uint8),
        cv2.resize(cv2.imread(str(files["portrait.jpg"])), size),
        cv2.resize(cv2.imread(str(files["business-person.png"])), size),
        np.full((size[1], size[0], 3), 90, dtype=np.uint8),
    ]
    clip = into / "scripted.mp4"
    writer = cv2.VideoWriter(str(clip), cv2.VideoWriter_fourcc(*"mp4v"), 10, size)
    for block in blocks:
        for _ in range(per):
            writer.write(block)
    writer.release()
    total = len(blocks) * per

    print(f"\n=== the whole pipeline over {total} decoded frames ===")
    capture = cv2.VideoCapture(str(clip))
    print(f"  cv2.VideoCapture(a file).isOpened() = {capture.isOpened()}")

    sensor = CameraSensor(camera=Camera(capture=capture), detector=det,
                          ledger=IdentityLedger(InMemoryStore()),
                          buffer=PerceptionBuffer(), stable_reads=2)

    async def drive() -> None:
        for i in range(total + 3):          # +3 to run past the end of the clip
            t0 = time.perf_counter()
            event = await sensor.read()
            ms = (time.perf_counter() - t0) * 1000
            reading = sensor.buffer.reading
            state = (f"error: {reading.error}" if reading.error
                     else f"faces={len(reading.faces)}")
            print(f"  read {i:2d} ({ms:5.0f} ms) {state:<40} "
                  + (f"{event.priority.name}: {event.text}" if event else "-"))

    asyncio.run(drive())
    print(f"  observations={sensor.observations} suppressed={sensor.suppressed}")
    print()
    print("  Note what did NOT fire: the A -> B swap around read 8. Both are ONE")
    print("  unknown face, so the state does not change and the sensor is right to")
    print("  stay quiet — it reports differences, and 'one stranger' is the same")
    print("  difference. Telling them apart is identity, which ADR-090 measured as")
    print("  unusable with a generic image embedder. Same limitation, seen from the")
    print("  other side.")
    sensor.close()


if __name__ == "__main__":
    where = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/harness-vision-probe")
    raise SystemExit(main(where))
