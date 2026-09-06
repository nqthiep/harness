"""Where to look, and how closely — the coarse-to-fine half of perception.

`vision_sensor.py` decides what is worth SAYING. This decides what is worth LOOKING at,
which is a different question and a much more expensive one to get wrong.

**The problem, measured.** `CameraSensor._capture` used to run every detector stage on
every glance. On this machine, `mediapipe` 1.0.1, a 640x480 photograph of a real person,
CPU, median of 12 runs:

    frame difference (8x subsample)     0.05 ms
    detect_faces                        2.59 ms
    detect_bodies                      28.35 ms      <- 60% of the total
    classify_scene                     12.97 ms      <- 28%
    embed_face (one face)               3.08 ms
    detect_hands                       22.89 ms      (added in ADR-121)
    ------------------------------------------
    all of them, every glance          69.88 ms

At `Driver.sensor_interval_s = 0.2` that is 70 ms of inference every 200 ms — **35% of a
core, burned continuously, in an empty room** — to re-derive the same answer. The frame
difference that can tell you nothing happened costs 0.05 ms, which is **1400x less**.

**So: a glance is cheap, a look is expensive, and the glance decides whether to look.**
Tier 0 is a frame difference and always runs. Tier 1 is `detect_faces`, cheap enough to
run whenever anything moved. Tier 2 is the rest, and only runs when the glance found a
reason.

**The rule that makes this safe, and it is ADR-081's blindness rule again.** A stage that
was SKIPPED produced no measurement, and no measurement is not the same as "nothing
there". Reporting an empty `bodies` because pose did not run would read downstream as
"the body vanished" and fire a change nobody made — the exact mistake as reporting an
empty room because the camera broke. So a skipped stage CARRIES FORWARD its last measured
value. `Reading` then always describes the world as currently understood, never as
partially measured.

**And the rule that keeps carrying-forward from going stale.** A value held indefinitely
is a lie with a long fuse: a posture that changes while nothing else moves would never be
seen again. Every stage therefore has an `every` — a maximum number of glances it may be
carried before it is re-measured no matter what. That number, not the trigger, is what
bounds how wrong the carried value can be.

**A table, not an if-chain**, for the same reason `Salience` is one and `EFFECT_PROFILES`
is one: every stage's policy is visible at once, and no answer depends on the order the
branches happen to be written in.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping

#: Tier 1. Always runs — see `Gaze.locate` for why it is not in the table below.
FACES = "faces"

#: The stages `Gaze` actually decides about: tier 2, the expensive ones.
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


@dataclass(frozen=True)
class Look:
    """When one expensive stage is worth running.

    Four independent reasons, OR-ed. They are separate fields rather than one predicate so
    a caller can see — and a test can pin — exactly which reason fired.
    """
    #: Run it when the frame changed. The ordinary "something is happening" trigger.
    on_motion: bool = True
    #: Run it when the set of visible faces changed. Somebody new is the one moment their
    #: pose and their name are most worth having.
    on_change: bool = True
    #: Run it when a face is present that nobody has named yet. Only `identity` uses this,
    #: and it is what stops re-embedding a person who has already been recognised and has
    #: not left.
    on_unresolved: bool = False
    #: Re-measure after this many consecutive glances of being carried forward, whatever
    #: the triggers say. This bounds staleness; without it a carried value is unbounded.
    every: int = 25

    def wants(self, *, motion: bool, changed: bool, unresolved: bool, stale: int) -> bool:
        return ((self.on_motion and motion)
                or (self.on_change and changed)
                or (self.on_unresolved and unresolved)
                or stale >= self.every)


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


@dataclass(frozen=True)
class Focus:
    """What this glance decided to look at, and why — the `reason` is for the audit trail
    and the demo, never for a downstream decision."""
    stages: frozenset[str] = frozenset()
    motion: float = 0.0
    reason: str = ""

    def __contains__(self, stage: object) -> bool:
        return stage in self.stages

    @property
    def cheap(self) -> bool:
        """Nothing beyond tier 0 ran. The state the loop should spend most of its time in."""
        return not self.stages


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


@dataclass
class Gaze:
    """The cascade's policy and its staleness counters.

    Mutable, unlike almost everything else here, because the counters ARE the state that
    makes "carried forward for N glances" answerable. `Focus` and `Look` stay frozen.
    """
    looks: Mapping[str, Look] = field(default_factory=lambda: dict(DEFAULT_LOOKS))
    motion_threshold: float = DEFAULT_MOTION_THRESHOLD
    #: Stages a caller has demanded for the next glance regardless of the table — this is
    #: "I need to read the hands now", the deliberate-attention half of the mechanism.
    #: Cleared once spent, so a demand buys one look and not a permanent cost.
    demanded: set[str] = field(default_factory=set)
    _stale: dict[str, int] = field(default_factory=dict)
    #: Counted so the saving is measurable rather than asserted.
    glances: int = 0
    looks_taken: dict[str, int] = field(default_factory=dict)

    def demand(self, *stages: str) -> None:
        """Ask for stages on the next glance. Unknown names raise, rather than being
        quietly dropped and leaving a caller convinced it asked for something."""
        unknown = set(stages) - set(STAGES)
        if unknown:
            raise ValueError(f"không có tầng nào tên {sorted(unknown)}; "
                             f"chọn trong {sorted(STAGES)}")
        self.demanded |= set(stages)

    def _decide(self, stage: str, *, motion: bool, changed: bool,
                unresolved: bool) -> bool:
        if self.glances <= 1:
            # THE FIRST GLANCE LOOKS AT EVERYTHING. Opening your eyes takes the whole
            # scene in once; after that you only check what moved.
            #
            # This is not an optimisation, it is a correctness rule, and it is ADR-081's
            # blindness rule one level down. A stage that has never run has no measured
            # value, and carrying forward from an empty `Reading` makes "never measured"
            # indistinguishable from "measured, and there was nothing". Measured before
            # this existed: `classify_scene` was skipped on a still, empty room, then ran
            # for the first time when somebody walked in — and the sensor announced "chỗ
            # này giờ trông như home office" as if the room had just changed, in the
            # middle of a test about a dropped frame. One full glance at the start costs
            # 69.88 ms once and removes the entire error class.
            return True
        if stage in self.demanded:
            return True
        look = self.looks.get(stage)
        if look is None:
            return False            # a stage with no row is a stage nobody asked for
        return look.wants(motion=motion, changed=changed, unresolved=unresolved,
                          stale=self._stale.get(stage, 0))

    def _mark(self, stage: str, ran: bool) -> None:
        if ran:
            self._stale[stage] = 0
            self.looks_taken[stage] = self.looks_taken.get(stage, 0) + 1
        else:
            self._stale[stage] = self._stale.get(stage, 0) + 1

    def locate(self, motion: float) -> Focus:
        """Tier 1. Runs face detection, unconditionally, and opens the glance.

        Separate from `detail` because face detection's RESULT is an input to every other
        stage's decision — you cannot ask "did the set of people change" before looking
        for people. Two phases is the cascade, not an implementation accident.

        **Unconditional, deliberately, and the first version of this was not.** Gating
        tier 1 on motion saves 2.59 ms out of 69.88 — 3.7% — and buys a real failure in
        exchange: a person who enters below the motion threshold, or a threshold set a
        little too high for a particular camera, is then invisible until the staleness
        clock fires, and EVERYTHING downstream keys off faces, so the whole cascade goes
        blind together. Measured while building this: with tier 1 gated, a scripted
        arrival produced no event at all. Paying 2.59 ms per glance to keep the trigger
        for everything else live is the cheap side of that trade.
        """
        self.glances += 1
        self._mark(FACES, True)
        moving = motion >= self.motion_threshold
        return Focus(stages=frozenset({FACES}), motion=motion,
                     reason="chuyển động" if moving else "cảnh tĩnh")

    def detail(self, focus: Focus, *, changed: bool, unresolved: bool) -> Focus:
        """Tier 2: decide the expensive stages, now that tier 1 has reported.

        `changed` is "the set of visible faces is not what it was"; `unresolved` is "at
        least one face present has no name". Both are computed by the caller from the
        tier-1 result, because deciding them here would mean this module knowing about
        `IdentityLedger`, which is the layering `vision_tools` exists to keep.
        """
        moving = focus.motion >= self.motion_threshold
        chosen = set(focus.stages)
        for stage in STAGES:
            run = self._decide(stage, motion=moving, changed=changed,
                               unresolved=unresolved)
            self._mark(stage, run)
            if run:
                chosen.add(stage)
        self.demanded.clear()       # a demand buys ONE glance
        extra = sorted(chosen - {FACES})
        why = focus.reason + (f"; nhìn kỹ: {', '.join(extra)}" if extra else "")
        return replace(focus, stages=frozenset(chosen), reason=why)

    def report(self) -> str:
        """One line of "what did attention actually cost", for the demo and the logs."""
        if not self.glances:
            return "chưa liếc lần nào"
        parts = ", ".join(f"{s}={self.looks_taken.get(s, 0)}"
                          for s in (FACES,) + STAGES)
        return f"{self.glances} lần liếc; số lần chạy: {parts}"
