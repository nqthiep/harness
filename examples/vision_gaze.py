"""What a camera's cheap glance measures, and how much each of its looks is worth.

The MECHANISM moved to `harness.contrib.attention` (ADR-122) — `Look`, `Focus` and `Gaze`
are domain-neutral and shipped, because their three correctness rules are the kind a fork
gets subtly wrong in the direction that looks like success. What stays here is everything
only a camera knows: how to measure "did the picture change", what its stages are called,
and the tuning numbers, which are judgment and meant to be edited (see
`harness.contrib.__init__` for that line).

**The measurement the whole design rests on.** `mediapipe` 1.0.1, a 640x480 photograph of
a real person, CPU, median of 12 runs:

    frame difference (8x subsample)     0.05 ms
    detect_faces                        2.59 ms
    detect_bodies                      28.35 ms      <- 60% of tier 2
    classify_scene                     12.97 ms
    embed_face (one face)               3.08 ms
    detect_hands                       22.89 ms
    ------------------------------------------
    all of them, every glance          69.88 ms

At `Driver.sensor_interval_s = 0.2` that is 70 ms of inference every 200 ms — 35% of a
core, burned continuously in an empty room — to re-derive the same answer. The frame
difference that can tell you nothing happened costs 1400x less.

**Tier 1 (`detect_faces`) is unconditional.** Gating it on motion saves 2.59 ms of 69.88 —
3.7% — and buys a real failure: everything downstream keys off faces, so a person entering
below the motion threshold takes the whole cascade blind with them. Measured while
building this: with tier 1 gated, a scripted arrival produced no event at all.
"""
from __future__ import annotations

from typing import Any, Mapping

from harness.contrib.attention import Focus, Gaze, Look

#: Tier 1. Always runs — see the module docstring for the 3.7% that buys.
FACES = "faces"

#: The stages `Gaze` decides about: tier 2, the expensive ones.
BODIES, HANDS, IDENTITY, SCENE = "bodies", "hands", "identity", "scene"
STAGES: tuple[str, ...] = (BODIES, HANDS, IDENTITY, SCENE)

#: Mean absolute difference, per channel per pixel, over an 8x-subsampled frame, above
#: which the scene counts as "something moved". Sensor noise on a static webcam frame sits
#: well under 1.0; a person walking through produces tens. Between them is a wide band, so
#: the exact value matters less than that it is not 0 — and it is tunable per camera
#: because a noisy sensor moves the floor, not the signal.
DEFAULT_MOTION_THRESHOLD = 1.5

#: How much a frame is subsampled before differencing. 8 turns a 640x480 frame into
#: 80x60, which is 0.05 ms and still shows a person moving. This is a cost knob, not an
#: accuracy one.
MOTION_SUBSAMPLE = 8


#: The default policy, one row per stage. The numbers are chosen against
#: `sensor_interval_s = 0.2`, so `every=5` is "at least once a second".
DEFAULT_LOOKS: Mapping[str, Look] = {
    # The most expensive stage (28.35 ms). Worth it when somebody arrives or moves.
    BODIES:   Look(on_motion=True,  on_change=True,  every=25),
    # Nearly as expensive (22.89 ms), and a hand shape is only news while something is
    # moving — a still hand was already reported. So: no `on_change`.
    HANDS:    Look(on_motion=True,  on_change=False, every=50),
    # Not driven by motion at all: an unrecognised face is the trigger, and once someone
    # is named and has not left, re-embedding them buys nothing.
    IDENTITY: Look(on_motion=False, on_change=True,  on_unresolved=True, every=50),
    # A room's labels do not change while nobody is in it (12.97 ms).
    SCENE:    Look(on_motion=True,  on_change=True,  every=50),
}




def motion_of(frame: Any, previous: Any) -> tuple[float, Any]:
    """Tier 0: how much the frame changed, and the subsampled frame to compare next time.

    Returns `(0.0, small)` on the first frame — there is nothing to difference against,
    and calling that "no motion" is right: one frame is not a change. Any exception from
    an unexpected frame shape degrades to "assume motion", never to a crash, because this
    runs on every glance and a perception layer that raises is worse than one that looks
    too often.
    """
    try:
        small = frame[::MOTION_SUBSAMPLE, ::MOTION_SUBSAMPLE].astype("int16")
    except Exception:
        return float("inf"), previous
    if previous is None or getattr(previous, "shape", None) != getattr(small, "shape", ()):
        return 0.0, small
    try:
        import numpy
        return float(numpy.abs(small - previous).mean()), small
    except Exception:
        return float("inf"), small


def camera_gaze(**kw: Any) -> Gaze:
    """A `Gaze` wired for a camera: the vision table, the vision tier 1, the vision units.

    A named constructor rather than a subclass, because nothing about a camera changes how
    the cascade DECIDES — only what its stages are called and what its numbers mean. If
    this needed to override a method, the abstraction would be wrong.
    """
    kw.setdefault("looks", dict(DEFAULT_LOOKS))
    kw.setdefault("tier_one", FACES)
    kw.setdefault("threshold", DEFAULT_MOTION_THRESHOLD)
    return Gaze(**kw)


__all__ = ["BODIES", "DEFAULT_LOOKS", "DEFAULT_MOTION_THRESHOLD", "FACES", "Focus",
           "Gaze", "HANDS", "IDENTITY", "Look", "MOTION_SUBSAMPLE", "SCENE", "STAGES",
           "camera_gaze", "motion_of"]
