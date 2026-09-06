"""`CameraSensor` — the camera as a real `Sensor`: it reports what CHANGED, at a
priority decided in code.

This is the piece that connects `vision_tools.py` (eyes) to `harness.contrib.driver`
(priority-served runtime events). `Camera` on its own returns `(frame, error)` — a *state*, and states are
not events. An event is a difference: "Thiep just walked in" is actionable, "Thiep is
present" repeated thirty times a second is noise the context window pays for.

**Where each responsibility lives, and why it has to be here.**

* **Identity is resolved HERE, out of band.** `IdentityLedger.match` is async and
  `Policy.check` is sync and pure (POL-4), so a policy can never look a face up itself.
  This sensor writes the resolved names into `Reading.names`, which is the only way
  identity reaches synchronously-readable state — that is what lets an `InterruptGate`
  or any other policy react to WHO is in the room rather than to how many faces there
  are.
* **The expensive work runs on a thread.** `asyncio.to_thread` for the grab and the
  inference: MediaPipe is CPU-bound C++ and OpenCV's `read()` blocks. Doing it inline
  would stall the event loop the agent's own run is using, which is the exact trap that
  makes every middleware hook unsuitable for acquisition.
* **Priority is a TABLE, filled in by code.** Rule 1 of `harness.contrib.driver`: a camera is
  `effect="external"` and its content is untrusted, so if event TEXT could set priority,
  anyone holding up a sign reading "URGENT" could preempt the agent. `Salience` maps a
  structural `Change` — who arrived, who left, how many unrecognised faces, who changed
  posture or distance band, which scene labels came and went — to a `Priority`. Nothing
  reads the priority out of a string. The scene row is where this was easiest to lose:
  every label carries the SAME tier, so a picture of a fire held up to the lens buys no
  more urgency than a picture of a cat. An operator who wants otherwise writes
  `promote=`, which is their code reading a `Change` (ADR-120).

**The defaults never preempt, on purpose.** `arrival`/`unknown_arrival` are `NORMAL` and
`departure` is `LOW`, so out of the box a camera can put something in front of the model
at the next model call and can never cancel a turn. Cancelling a build because someone
walked past the lens is the wrong trade by default; an operator who genuinely wants it
supplies `promote=`, which is their own code, not the model's judgement and not text
from the frame.

**The first observation establishes a BASELINE and announces nothing.** Opening your
eyes is not everyone in the room arriving. So a `CameraSensor` that starts up with
someone already present emits no event for them — their presence is STATE, available
through the buffer and through `identify_person`/`look`, and the prompt already tells the
agent to look at the start of a conversation. Events are differences; the first
observation has nothing to differ from.

**Debounced, because a detector flickers.** One dropped frame would otherwise read as
"Thiep left" immediately followed by "Thiep arrived". A new state must be observed
`stable_reads` times in a row before it commits and produces a `Change`. Measured
against a `FakeDetector` scripted to flicker (`tests/test_vision_sensor.py`).

Run it — no camera, no model file, no API key:

    python3 examples/vision_sensor.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
import time
from dataclasses import dataclass, fields
from dataclasses import replace as _replace
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from harness.contrib.driver import Event, Priority
from vision_gaze import (BODIES as LOOK_BODIES, HANDS as LOOK_HANDS,
                         IDENTITY as LOOK_IDENTITY, SCENE as LOOK_SCENE,
                         FACES as LOOK_FACES, Focus, Gaze, camera_gaze,
                         motion_of)
from vision_tools import (UNKNOWN_GESTURE, Camera, Detector, IdentityLedger,
                          PerceptionBuffer, Reading, describe, distance_of,
                          nearer_than)

#: How many consecutive identical observations make a change real rather than a flicker.
#: 2 is the cheapest value that survives one dropped frame; raise it for a jittery
#: detector, at the cost of that much extra latency per event.
DEFAULT_STABLE_READS = 2

#: What the cheap loop is allowed to notice. Names, not booleans, so a caller reads
#: `attends={PRESENCE, DISTANCE}` and knows exactly what they turned off.
PRESENCE, POSTURE, DISTANCE = "presence", "posture", "distance"
SCENE, GESTURE = "scene", "gesture"

#: `PRESENCE` is not optional — who is in front of the camera is the sensor's whole
#: contract, and `_commit`'s blindness rule is written in terms of it. The others are.
ATTENDABLE = frozenset({POSTURE, DISTANCE, SCENE, GESTURE})
DEFAULT_ATTENDS = frozenset({PRESENCE}) | ATTENDABLE

#: Which `Gaze` stage feeds which attended aspect (ADR-121). An aspect nobody attends to
#: can produce no event, so paying for its detector would be buying an answer with no
#: reader — measured: `classify_scene` is 12.97 ms per glance for a field that would be
#: thrown away. A stage with no entry here (`faces`, `identity`) serves `PRESENCE`, which
#: is always attended.
STAGE_FOR: dict[str, str] = {POSTURE: LOOK_BODIES, GESTURE: LOOK_HANDS,
                             SCENE: LOOK_SCENE}

#: A scene classifier returns a long tail of low-confidence guesses that churn frame to
#: frame. Both of these exist to keep that churn out of the committed state; without
#: them "the scene changed" fires continuously and means nothing.
MIN_SCENE_CONFIDENCE = 0.5
SCENE_LABELS_TRACKED = 3


@dataclass(frozen=True)
class Change:
    """What moved between two committed observations — the structural facts a priority
    is allowed to be computed from.

    Deliberately not a string: `Salience` maps THIS to a `Priority`, so nothing in the
    chain can be steered by what a frame happens to contain (rule 1).
    """
    arrived: tuple[str, ...] = ()
    left: tuple[str, ...] = ()
    unknown_arrived: int = 0
    unknown_left: int = 0
    reading: Reading | None = None
    #: (who, before, after) for a settled posture change — someone who was sitting and
    #: is now standing. Only for people already present in both observations: a posture
    #: that appears because its owner just walked in is not news on top of the arrival.
    postures: tuple[tuple[str, str, str], ...] = ()
    #: (who, before, after) for a settled distance change, same restriction. Direction
    #: is not encoded here; `nearer_than` reads it back off the two bands, so there is
    #: one definition of "nearer" rather than a flag that can disagree with the ladder.
    moved: tuple[tuple[str, str, str], ...] = ()
    #: The nearest-band change over all faces, `(before, after)`, or `None`.
    nearest: tuple[str, str] | None = None
    #: Scene labels that appeared and disappeared. Labels, not a priority input — see
    #: `Salience.scene`.
    scene_in: tuple[str, ...] = ()
    scene_out: tuple[str, ...] = ()
    #: Hand shapes that appeared. A hand shape ENDING is not reported: putting your hand
    #: down is not a second event, and reporting it would double every gesture.
    gestures: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not (self.arrived or self.left or self.unknown_arrived
                    or self.unknown_left or self.postures or self.moved
                    or self.nearest or self.scene_in or self.scene_out
                    or self.gestures)


@dataclass(frozen=True)
class Salience:
    """The priority table. Conservative by default: nothing here can preempt a turn.

    `promote` is the operator's escape hatch and it takes a `Change`, never text — a
    callable they write, returning a `Priority` to override the table or `None` to keep
    it. That keeps rule 1 true even when someone does want `CRITICAL`: the decision is
    still code reading structure.
    """
    arrival: Priority = Priority.NORMAL
    unknown_arrival: Priority = Priority.NORMAL
    departure: Priority = Priority.LOW
    #: Someone changed posture. LOW: standing up is worth mentioning at the next model
    #: call, never worth interrupting for.
    posture: Priority = Priority.LOW
    #: Someone moved NEARER. The one new row that is not LOW, because approaching the
    #: camera is the ordinary way a person starts a conversation — the same tier as
    #: walking in, which it usually follows.
    approach: Priority = Priority.NORMAL
    #: Someone moved further away. Departure's tier, for the same reason.
    retreat: Priority = Priority.LOW
    #: The scene labels changed. LOW, and flat across every label ON PURPOSE — see the
    #: note on rule 1 below.
    scene: Priority = Priority.LOW
    #: A hand shape appeared. NORMAL: a raised or pointing hand is usually addressed AT
    #: someone, which is the same "a person is starting an interaction" signal `approach`
    #: carries. Flat across every gesture, for the same reason `scene` is.
    gesture: Priority = Priority.NORMAL
    promote: Callable[[Change], Priority | None] | None = None

    def of(self, change: Change) -> Priority:
        """The HIGHEST tier among the things that actually changed.

        `max`, not a first-match ladder. One observation can settle several differences
        at once — Thiep walks in and the room lights come on — and a ladder makes the
        answer depend on the order the rows happen to be written in. Taking the max is
        the same trick `Priority` was ordered for and `EventInbox.offer` already uses.

        **Rule 1 note.** Every row is keyed on the SHAPE of the change, never on its
        content: `scene` is one tier for any label, so a picture of a fire held up to
        the lens raises no priority a picture of a cat would not. That is deliberate.
        Wiring "fire ⇒ CRITICAL" here would put a `effect="external"` channel's own text
        in charge of preemption, which is exactly the foot-gun rule 1 names. An operator
        who wants it writes `promote=` — their code, reading a `Change`.
        """
        if self.promote is not None:
            forced = self.promote(change)
            if forced is not None:
                return forced
        tiers: list[Priority] = []
        if change.arrived:
            tiers.append(self.arrival)
        if change.unknown_arrived:
            tiers.append(self.unknown_arrival)
        if change.left or change.unknown_left:
            tiers.append(self.departure)
        if change.postures:
            tiers.append(self.posture)
        if change.scene_in or change.scene_out:
            tiers.append(self.scene)
        if change.gestures:
            tiers.append(self.gesture)
        for _who, before, after in change.moved:
            tiers.append(self.approach if nearer_than(after, before) else self.retreat)
        if change.nearest is not None:
            before, after = change.nearest
            tiers.append(self.approach if nearer_than(after, before) else self.retreat)
        # An empty `Change` never reaches here (`read()` drops it), but `Salience.of` is
        # public and a caller may hand one over; `departure` keeps the old answer.
        return max(tiers) if tiers else self.departure


def render(change: Change) -> str:
    """The sentence the model eventually reads: the CHANGE first, then enough of the
    scene to act on it.

    Change first because that is the part that is news. `describe()` supplies the
    context, and it reads identity straight out of `Reading.names`, which this sensor
    filled in.
    """
    parts: list[str] = []
    if change.arrived:
        parts.append(f"{', '.join(change.arrived)} vừa xuất hiện trước mặt bạn")
    if change.unknown_arrived == 1:
        parts.append("một người bạn chưa biết vừa xuất hiện")
    elif change.unknown_arrived > 1:
        parts.append(f"{change.unknown_arrived} người bạn chưa biết vừa xuất hiện")
    if change.left:
        parts.append(f"{', '.join(change.left)} vừa đi khỏi")
    if change.unknown_left == 1:
        parts.append("một người bạn chưa biết vừa đi khỏi")
    elif change.unknown_left > 1:
        parts.append(f"{change.unknown_left} người bạn chưa biết vừa đi khỏi")
    for who, was, now in change.postures:
        parts.append(f"{who} chuyển từ {was} sang {now}")
    for who, was, now in change.moved:
        verb = "tiến lại gần" if nearer_than(now, was) else "lùi ra xa"
        parts.append(f"{who} {verb} ({was} -> {now})")
    if change.nearest is not None and not change.moved:
        # Only when no named person explains it: otherwise this repeats what the line
        # above already said, in vaguer words.
        was, now = change.nearest
        verb = "tiến lại gần hơn" if nearer_than(now, was) else "lùi ra xa hơn"
        parts.append(f"người gần nhất {verb} ({was} -> {now})")
    if change.gestures:
        parts.append("tôi thấy " + ", ".join(change.gestures))
    if change.scene_in or change.scene_out:
        # Labels are reported as OBSERVATION, never as a conclusion: the classifier's
        # output is untrusted content (rule 1) and it decided no priority to get here.
        went = f", không còn thấy {', '.join(change.scene_out)}" if change.scene_out \
            else ""
        came = f"giờ trông như {', '.join(change.scene_in)}" if change.scene_in \
            else "khung cảnh đổi"
        parts.append(f"chỗ này {came}{went}")

    headline = "; ".join(parts) if parts else "cảnh trước mặt vừa thay đổi"
    if change.reading is not None and change.reading.ok:
        return f"{headline[0].upper()}{headline[1:]}. {describe(change.reading)}"
    return f"{headline[0].upper()}{headline[1:]}."


@dataclass(frozen=True)
class _State:
    """A committed observation, reduced to what a `Change` is computed from.

    Every field here is QUANTISED — a small set of code-produced literals, never a
    measurement. That is what makes the cheap loop cheap: a continuous field (pixel box
    width, a confidence float) differs on essentially every frame, so every frame would
    commit a change and the expensive rate would collapse into the cheap one. Adding a
    field to this class means first deciding what its bands are.

    Fields not in `attends` are left at their default, so an unattended aspect cannot
    produce a change and costs no debounce.
    """
    known: frozenset[str] = frozenset()
    unknown: int = 0
    blind: bool = True          # no working camera yet; suppresses a fake "departure"
    #: (name, posture) for people the ledger could name. Named people only: an unknown
    #: face has no stable key across frames — the detector's ordering is not an
    #: identity — so "#0 stood up" would fire every time detection order shuffled.
    postures: frozenset[tuple[str, str]] = frozenset()
    #: (name, band) from `distance_of`, same restriction and the same reason.
    distances: frozenset[tuple[str, str]] = frozenset()
    #: The nearest band over ALL faces, named or not. This is the one aspect an unknown
    #: person still contributes to, because "somebody is now very close" is worth waking
    #: for whether or not you know who they are, and it needs no per-face identity.
    nearest: str = ""
    #: Scene labels above `MIN_SCENE_CONFIDENCE`, top `SCENE_LABELS_TRACKED`.
    scene: frozenset[str] = frozenset()
    #: The hand shapes visible, as a SET rather than per-person. `Hand` carries no
    #: identity and the detector's ordering is not one, so "Thiep is pointing" would be a
    #: guess dressed as a fact — the same refusal `postures` makes for unknown faces.
    #: `UNKNOWN_GESTURE` is dropped: "I could not read that hand" is not a hand shape,
    #: and letting it in would make a hand drifting in and out of readability look like
    #: a gesture being made and unmade.
    gestures: frozenset[str] = frozenset()


#: The field names `_settle` debounces, taken from the dataclass rather than restated,
#: so adding a field to `_State` cannot silently skip its debounce.
_FIELDS: tuple[str, ...] = tuple(f.name for f in fields(_State))


def _transitions(before: frozenset[tuple[str, str]],
                 after: frozenset[tuple[str, str]]) -> tuple[tuple[str, str, str], ...]:
    """`(who, before, after)` for everyone whose value changed BETWEEN two observations.

    The INTERSECTION of the two key sets is what does the work, and it is why a person
    walking in reports an arrival and not also "Thiep chuyển từ (không có) sang đang
    ngồi": a name only enters one of these maps by being present in that observation, so
    an arrival has no `before` entry and produces no transition.

    This took a third argument, `stayed` — the people present in both committed states —
    and it was removed as unreachable rather than left in looking load-bearing. Mutation
    M3 (ADR-120) deleted the restriction and no test could tell: `postures` keys are a
    subset of `known` by construction in `_state_of`, so the intersection above already
    excludes everyone `stayed` would have. Per-field settling can leave a stale name in
    the committed `postures` after its owner has left `known`, which is the one way the
    two can disagree — but a name stale on ONE side is absent from the other, so the
    intersection drops it anyway. A parameter that cannot change an answer is worse than
    no parameter: it invites the next reader to trust it for a guarantee it never gave.
    """
    old = dict(before)
    new = dict(after)
    return tuple(sorted((who, old[who], new[who]) for who in old.keys() & new.keys()
                        if old[who] != new[who]))


class CameraSensor:
    """A `Sensor` (`harness.contrib.driver`) over a `Camera` + `Detector` +
    `IdentityLedger`.

    `buffer=` is shared with whatever `VisionProfile` built, so the agent's own
    `identify_person` reads the same `Reading` this sensor published — one perception,
    two consumers, no second capture. Pass the SAME `PerceptionBuffer` to both.

    Owns nothing it did not open: `close()` releases the camera only if this sensor was
    the thing that opened it, which is `Camera`'s own rule.
    """

    def __init__(self, *, camera: Camera, detector: Detector, ledger: IdentityLedger,
                 buffer: PerceptionBuffer | None = None,
                 salience: Salience | None = None,
                 stable_reads: int = DEFAULT_STABLE_READS,
                 attends: frozenset[str] | None = None,
                 gaze: Gaze | None = None,
                 use_thread: bool = True) -> None:
        self.camera, self.detector, self.ledger = camera, detector, ledger
        self.buffer = buffer if buffer is not None else PerceptionBuffer()
        self.salience = salience if salience is not None else Salience()
        self.stable_reads = max(1, stable_reads)
        #: Which aspects the cheap loop notices. `PRESENCE` is always in, whatever the
        #: caller passes: dropping it would leave a camera sensor that cannot report
        #: that somebody walked in, and `_commit`'s blindness rule is written in terms
        #: of it. An unrecognised name is a typo the caller wants to hear about now
        #: rather than as silence at 3am.
        wanted = frozenset(attends) if attends is not None else DEFAULT_ATTENDS
        unknown_aspects = wanted - DEFAULT_ATTENDS
        if unknown_aspects:
            raise ValueError(
                f"attends={sorted(unknown_aspects)} không phải là mặt nào cả; "
                f"chọn trong {sorted(ATTENDABLE)} (PRESENCE luôn bật)")
        self.attends = wanted | {PRESENCE}
        self.use_thread = use_thread
        #: The cascade (ADR-121). Decides which detector stages this glance pays for.
        self.gaze = gaze if gaze is not None else camera_gaze()
        #: What the last glance actually looked at — readable so a caller, the demo and
        #: the tests can see the decision rather than infer it from timings.
        self.focus = Focus()
        #: The last resolved observation, used to CARRY FORWARD every stage that did not
        #: run. Starts empty, which is correct: before the first glance nothing has been
        #: measured, and `blind` covers that case downstream.
        self._held = Reading(at=0.0)
        self._small: Any = None            # previous subsampled frame, for tier 0
        self._committed = _State()
        self._pending: dict[str, tuple[object, int]] = {}
        #: Counters a caller can assert on rather than infer: how many observations were
        #: taken, and how many were suppressed as flicker.
        self.observations = 0
        self.suppressed = 0

    # -- acquisition -----------------------------------------------------------------

    def _worth(self, stage: str, focus: Focus) -> bool:
        """Did `Gaze` choose this stage, AND is anyone attending to what it produces?

        Two independent gates, and the second one is not redundant: `Gaze` decides
        whether the answer would be FRESH, `attends` decides whether the answer would be
        READ. Running `classify_scene` for 12.97 ms to fill a field that `_state_of`
        discards is a cost with no reader.
        """
        if stage not in focus:
            return False
        aspect = next((a for a, st in STAGE_FOR.items() if st == stage), None)
        return aspect is None or aspect in self.attends

    def _capture(self) -> Reading:
        """Glance, then decide what to look at, then look. SYNC on purpose: this is the
        body handed to a thread, and keeping it sync is what makes it safe to hand over.

        Coarse to fine (ADR-121). Tier 0 is a frame difference at 0.05 ms and always
        runs. Tier 1 is `detect_faces` at 2.59 ms. Tier 2 — pose, hands, identity, scene,
        69.88 ms all told — runs only where `Gaze` found a reason. A stage that does not
        run CARRIES FORWARD its last measured value rather than reporting empty, because
        a skipped stage produced no measurement and "no measurement" is not "nothing
        there"; that is ADR-081's blindness rule applied one level down.
        """
        frame, err = self.camera.grab()
        if err:
            return Reading(at=time.time(), error=err)
        motion, self._small = motion_of(frame, self._small)
        held = self._held
        try:
            focus = self.gaze.locate(motion)
            faces = (tuple(self.detector.detect_faces(frame)) if LOOK_FACES in focus
                     else held.faces)
            changed = tuple(f.box for f in faces) != tuple(f.box for f in held.faces)
            # "Somebody here has no name yet" — computed from the CARRIED names, so a
            # person already recognised does not re-trigger identity every glance.
            unresolved = len(faces) > sum(1 for n in held.names if n)
            focus = self.gaze.detail(focus, changed=changed, unresolved=unresolved)

            bodies = (tuple(self.detector.detect_bodies(frame))
                      if self._worth(LOOK_BODIES, focus) else held.bodies)
            hands = (tuple(self.detector.detect_hands(frame))
                     if self._worth(LOOK_HANDS, focus) else held.hands)
            scene = (tuple(self.detector.classify_scene(frame))
                     if self._worth(LOOK_SCENE, focus) else held.scene)
            if LOOK_IDENTITY in focus:
                faces = tuple(
                    f if f.embedding else _replace(
                        f, embedding=tuple(self.detector.embed_face(frame, f.box)) or None)
                    for f in faces)
        except Exception as exc:
            return Reading(at=time.time(),
                           error=f"nhận diện lỗi: {type(exc).__name__}: {exc}")
        try:
            height, width = int(frame.shape[0]), int(frame.shape[1])
        except Exception:
            height = width = 0
        self.buffer.frame = frame
        self.focus = focus
        return Reading(at=time.time(), faces=faces, bodies=bodies, scene=scene,
                       hands=hands, frame_size=(width, height))

    async def _observe(self) -> Reading:
        """One observation, identity resolved, published to the buffer.

        Identity is carried forward exactly like every other skipped stage, and getting
        this wrong is the sharpest edge in the cascade: re-deriving names from embeddings
        that were not computed this glance would turn every recognised person into an
        unknown one the moment `Gaze` decided not to re-embed them, and the sensor would
        announce "Thiep đi khỏi, một người lạ xuất hiện" about a man sitting perfectly
        still. So when `IDENTITY` did not run, the held names stand — which is sound
        because the one thing that invalidates them, the set of faces changing, is itself
        an `on_change` trigger for `IDENTITY`.
        """
        reading = (await asyncio.to_thread(self._capture) if self.use_thread
                   else self._capture())
        if not reading.ok:
            self.buffer.publish(reading, None)
            return reading
        if LOOK_IDENTITY in self.focus:
            names: list[str | None] = []
            for face in reading.faces:
                vec = face.embedding
                names.append((await self.ledger.match(vec)).name if vec else None)
            resolved = _replace(reading, names=tuple(names))
        else:
            resolved = _replace(reading, names=self._held.names[:len(reading.faces)])
        self._held = resolved
        self.buffer.publish(resolved, self.buffer.frame)
        return resolved

    # -- change detection ------------------------------------------------------------

    def _state_of(self, reading: Reading) -> _State:
        if not reading.ok:
            return _State(blind=True)
        known = frozenset(n for n in reading.names if n)
        unknown = sum(1 for n in reading.names if n is None)
        # A face detected without an embedding is still a person present, just an
        # unidentifiable one — counting it as absent would announce a departure that did
        # not happen.
        unknown += max(0, len(reading.faces) - len(reading.names))

        postures: set[tuple[str, str]] = set()
        distances: set[tuple[str, str]] = set()
        nearest = ""
        for i, face in enumerate(reading.faces):
            band = distance_of(face, reading.frame_size)
            if nearer_than(band, nearest) or not nearest:
                nearest = band
            who = reading.names[i] if i < len(reading.names) else None
            if not who:
                continue                       # no stable key; see `_State.postures`
            if POSTURE in self.attends and i < len(reading.bodies):
                postures.add((who, reading.bodies[i].posture))
            if DISTANCE in self.attends and band:
                distances.add((who, band))
        scene = frozenset(
            label for label, score in reading.scene[:SCENE_LABELS_TRACKED]
            if score >= MIN_SCENE_CONFIDENCE) if SCENE in self.attends else frozenset()
        gestures = frozenset(
            h.gesture for h in reading.hands
            if h.gesture and h.gesture != UNKNOWN_GESTURE
        ) if GESTURE in self.attends else frozenset()
        return _State(known=known, unknown=unknown, blind=False,
                      postures=frozenset(postures), distances=frozenset(distances),
                      nearest=nearest if DISTANCE in self.attends else "",
                      scene=scene, gestures=gestures)

    def _settle(self, state: _State) -> _State:
        """Debounce each field on its OWN clock, and return what has settled.

        This used to debounce the whole `_State` as one value, which coupled every
        aspect to every other: measured, a state whose `unknown` count flickered
        1,2,1,2 never reached `stable_reads`, so ten observations with the same person
        present throughout produced zero events — the arrival was perfectly stable and
        was swallowed by an unrelated field's jitter.

        That was survivable with two fields. It is not survivable with posture,
        distance and scene, which jitter by nature: one twitchy scene classifier would
        have blinded the sensor to people walking in. So each field carries its own
        `(value, streak)`, and a field that has not settled keeps its committed value —
        the sensor reports what it is currently sure of, aspect by aspect.
        """
        settled: dict = {}
        for name in _FIELDS:
            seen = getattr(state, name)
            held, streak = self._pending.get(name, (None, 0))
            streak = streak + 1 if held == seen and streak else 1
            self._pending[name] = (seen, streak)
            settled[name] = seen if streak >= self.stable_reads else getattr(
                self._committed, name)
        return _State(**settled)

    def _commit(self, state: _State) -> Change | None:
        """Debounce, then diff. Returns `None` while a change is still unconfirmed."""
        if state != self._committed:
            self.suppressed += 1
        state = self._settle(state)

        before, self._committed = self._committed, state
        if before == state:
            return None
        self.suppressed -= 1        # that observation was a real change, not flicker
        if before.blind or state.blind:
            # Two cases, both silent, both deliberate:
            #   `state.blind`  — the camera stopped working. That is "I can't see", not
            #                    "everyone left"; the second would be a claim about the
            #                    world that this sensor has no evidence for.
            #   `before.blind` — the FIRST successful observation. Opening your eyes is
            #                    not everyone arriving, so this only establishes the
            #                    baseline that later changes diff against.
            return None
        return Change(arrived=tuple(sorted(state.known - before.known)),
                      left=tuple(sorted(before.known - state.known)),
                      unknown_arrived=max(0, state.unknown - before.unknown),
                      unknown_left=max(0, before.unknown - state.unknown),
                      postures=_transitions(before.postures, state.postures),
                      moved=_transitions(before.distances, state.distances),
                      # `nearest` is the band of WHOEVER is closest, so it is only a
                      # movement while the population holds still. When somebody arrives
                      # or leaves, the two bands belong to two different people and
                      # "lùi ra xa hơn" would assert a motion nobody made — measured in
                      # the demo: "Thiep vừa đi khỏi; người gần nhất lùi ra xa hơn",
                      # where Thiep left and Nghia never moved. Silent instead, and the
                      # departure carries the news.
                      nearest=((before.nearest, state.nearest)
                               if before.nearest != state.nearest
                               and before.nearest and state.nearest
                               and before.known == state.known
                               and before.unknown == state.unknown else None),
                      scene_in=tuple(sorted(state.scene - before.scene)),
                      scene_out=tuple(sorted(before.scene - state.scene)),
                      gestures=tuple(sorted(state.gestures - before.gestures)))

    # -- the Sensor protocol ---------------------------------------------------------

    async def read(self) -> Event | None:
        reading = await self._observe()
        self.observations += 1
        change = self._commit(self._state_of(reading))
        if change is None or change.empty:
            return None
        change = _replace(change, reading=reading)
        return Event(priority=self.salience.of(change), text=render(change),
                     at=reading.at, source="camera")

    def close(self) -> None:
        self.camera.close()


# ── the demo: arrival, departure, flicker, blindness — no camera, no model ──────

def _demo() -> None:
    from harness.memory.inmemory import InMemoryStore

    from vision_tools import Body, Face, FakeDetector, Hand

    thiep_box, nghia_box = (140, 70, 240, 240), (420, 80, 180, 180)
    thiep, nghia, stranger = (1.0, 0.0, 0.1), (0.0, 1.0, 0.1), (0.5, 0.5, 0.9)

    class Cap:
        """A fake capture whose PIXELS follow the script, because `Gaze` reads the frame
        difference and a constant frame is a world where nothing ever moves (ADR-121)."""

        def __init__(self, detector=None):
            self.detector = detector

        def isOpened(self) -> bool:
            return True

        def set(self, *_a) -> bool:
            return True

        def read(self):
            import numpy
            frame = numpy.zeros((480, 640, 3), dtype=numpy.uint8)
            d = self.detector
            if d is None:
                return True, frame
            for i, face in enumerate(d.faces or ()):
                x, y, w, h = face.box
                frame[y:y + h, x:x + w] = 200 - 10 * i
            for i, body in enumerate(d.bodies or ()):
                frame[300:450, 20 + 160 * i:170 + 160 * i] = (
                    40 + sum(body.posture.encode()) % 200)
            for i, hand in enumerate(d.hands or ()):
                frame[180:290, 20 + 120 * i:130 + 120 * i] = (
                    40 + sum(hand.gesture.encode()) % 200)
            for i, (label, _s) in enumerate(d.scene or ()):
                frame[0:60, 210 * i:210 * i + 200] = 40 + sum(label.encode()) % 200
            return True, frame

        def release(self) -> None:
            pass

    async def main() -> None:
        store = InMemoryStore()
        ledger = IdentityLedger(store)
        await ledger.enroll("Thiep", thiep)
        await ledger.enroll("Nghia", nghia)

        detector = FakeDetector(bodies=(Body("đang ngồi"),),
                                scene=(("home office", 0.7),),
                                embeddings={thiep_box: thiep, nghia_box: nghia})
        buffer = PerceptionBuffer()
        sensor = CameraSensor(camera=Camera(capture=Cap(detector)), detector=detector,
                              ledger=ledger, buffer=buffer, use_thread=False)

        async def frames(label: str, faces, times: int = 2):
            detector.faces = faces
            detector.bodies = tuple(Body("đang ngồi") for _ in faces)
            events = [await sensor.read() for _ in range(times)]
            fired = [e for e in events if e is not None]
            shown = (f"{fired[0].priority.name:<8} {fired[0].text}" if fired
                     else "(không có sự kiện)")
            print(f"  {label:<26} {shown}")

        print("=" * 78)
        print("1. Phòng trống -> Thiep bước vào -> Nghia vào thêm -> cả hai đi khỏi")
        print("=" * 78)
        await frames("trống", ())
        await frames("Thiep vào", (Face(box=thiep_box),))
        await frames("Nghia vào thêm", (Face(box=thiep_box), Face(box=nghia_box)))
        await frames("Thiep đi", (Face(box=nghia_box),))
        await frames("trống lại", ())

        print()
        print("=" * 78)
        print("2. Người lạ — chưa enroll thì vẫn là sự kiện, chỉ không có tên")
        print("=" * 78)
        detector.embeddings = {thiep_box: stranger}
        await frames("người lạ vào", (Face(box=thiep_box),))

        print()
        print("=" * 78)
        print("3. Nhiễu một frame — KHÔNG được sinh 'đi khỏi' rồi 'vào lại'")
        print("=" * 78)
        detector.embeddings = {thiep_box: thiep, nghia_box: nghia}
        detector.faces = ()
        await sensor.read()                       # ổn định về trống
        await sensor.read()
        detector.faces = (Face(box=thiep_box),)
        detector.bodies = (Body("đang ngồi"),)
        first = await sensor.read()               # 1/2 — chưa xác nhận
        detector.faces = ()                       # frame bị mất
        second = await sensor.read()
        detector.faces = (Face(box=thiep_box),)
        third = await sensor.read()
        fourth = await sensor.read()
        print(f"  read 1 (mới thấy)          : {first}")
        print(f"  read 2 (frame mất)         : {second}")
        print(f"  read 3 (thấy lại, 1/2)     : {third}")
        print(f"  read 4 (xác nhận, 2/2)     : "
              f"{fourth.text if fourth else None}")
        print(f"  -> suppressed = {sensor.suppressed} lần nhiễu bị chặn, "
              f"observations = {sensor.observations}")

        print()
        print("=" * 78)
        print("4. Mất camera giữa đường — KHÔNG được báo 'mọi người đã đi khỏi'")
        print("=" * 78)
        blind = CameraSensor(camera=Camera(index=99), detector=detector, ledger=ledger,
                             buffer=PerceptionBuffer(), use_thread=False)
        print(f"  read lần 1                 : {await blind.read()}")
        print(f"  read lần 2                 : {await blind.read()}")
        print("  -> camera hỏng là 'không thấy gì', không phải 'phòng đã trống'")

        print()
        print("=" * 78)
        print("5. Mặc định KHÔNG bao giờ preempt; muốn CRITICAL thì phải tự viết code")
        print("=" * 78)
        print(f"  Salience mặc định          : arrival={Salience().arrival.name}, "
              f"unknown={Salience().unknown_arrival.name}, "
              f"departure={Salience().departure.name}")

        def urgent_if_boss_arrives(change: Change) -> Priority | None:
            return Priority.CRITICAL if "Thiep" in change.arrived else None

        table = Salience(promote=urgent_if_boss_arrives)
        print(f"  với promote= của operator  : "
              f"{table.of(Change(arrived=('Thiep',))).name} khi Thiep vào, "
              f"{table.of(Change(arrived=('Nghia',))).name} khi Nghia vào")
        print("  -> promote nhận `Change` (cấu trúc), KHÔNG nhận text (luật 1)")

        print()
        print("=" * 78)
        print("6. HAI TỐC ĐỘ: vòng rẻ chạy liên tục, model chỉ thức khi có KHÁC BIỆT")
        print("=" * 78)
        watch = CameraSensor(camera=Camera(capture=Cap(detector)), detector=detector,
                             ledger=ledger, buffer=PerceptionBuffer(), use_thread=False)
        near_box, far_box = (10, 10, 300, 300), (10, 10, 60, 60)
        detector.embeddings = {near_box: thiep, far_box: thiep}
        script = [
            ("Thiep ngồi yên (nền)",        far_box,  "đang ngồi", (("home office", 0.9),)),
            ("... vẫn ngồi yên",            far_box,  "đang ngồi", (("home office", 0.9),)),
            ("... vẫn ngồi yên",            far_box,  "đang ngồi", (("home office", 0.9),)),
            ("Thiep ĐỨNG DẬY",              far_box,  "đang đứng", (("home office", 0.9),)),
            ("... vẫn đứng",                far_box,  "đang đứng", (("home office", 0.9),)),
            ("Thiep TIẾN LẠI GẦN",          near_box, "đang đứng", (("home office", 0.9),)),
            ("... vẫn đứng gần",            near_box, "đang đứng", (("home office", 0.9),)),
            ("CẢNH ĐỔI thành bếp",          near_box, "đang đứng", (("kitchen", 0.9),)),
            ("... vẫn là bếp",              near_box, "đang đứng", (("kitchen", 0.9),)),
        ]
        woke = 0
        for label, box, posture, scene in script:
            detector.faces = (Face(box=box),)
            detector.bodies = (Body(posture),)
            detector.scene = scene
            events = [await watch.read() for _ in range(2)]
            fired = [e for e in events if e is not None]
            woke += len(fired)
            head = fired[0].text.split(".")[0] if fired else "(không gọi model)"
            print(f"  {label:<24} {head}")
        print(f"  -> {watch.observations} lần quan sát rẻ, {woke} lần đánh thức model "
              f"({woke / watch.observations:.0%})")
        print("  -> mắt vẫn mở suốt; chỉ KHÁC BIỆT mới đáng một lần gọi model")

        print()
        print("=" * 78)
        print("7. attends= — tắt bớt mặt không quan tâm thì nó không tốn gì cả")
        print("=" * 78)
        quiet = CameraSensor(camera=Camera(capture=Cap(detector)), detector=detector,
                             ledger=ledger, buffer=PerceptionBuffer(),
                             attends=frozenset({PRESENCE}), use_thread=False)
        seen = 0
        for _label, box, posture, scene in script:
            detector.faces = (Face(box=box),)
            detector.bodies = (Body(posture),)
            detector.scene = scene
            for _ in range(2):
                seen += (await quiet.read()) is not None
        print(f"  attends={{PRESENCE}}          : {seen} sự kiện trên "
              f"{quiet.observations} quan sát")
        print("  -> dáng/khoảng cách/cảnh không vào `_State`, nên không sinh sự kiện")
        print("     và cũng không tốn một nhịp debounce nào")

        print()
        print("=" * 78)
        print("8. NHÌN TỔNG THỂ TRƯỚC: liếc rẻ quyết định có đáng nhìn kỹ hay không")
        print("=" * 78)
        # Cost per stage, measured on real MediaPipe (ADR-121). Costing the SKIPS is the
        # only way to state a saving; counting calls alone says nothing about ms.
        cost = {"faces": 2.59, "bodies": 28.35, "hands": 22.89, "embed": 3.08,
                "scene": 12.97}
        det2 = FakeDetector(scene=(("home office", 0.9),), embeddings={thiep_box: thiep},
                            hands=(Hand("bàn tay mở", "Right"),))
        watch = CameraSensor(camera=Camera(capture=Cap(det2)), detector=det2,
                             ledger=ledger, buffer=PerceptionBuffer(), use_thread=False)
        det2.faces, det2.bodies = (), ()
        for _ in range(4):
            await watch.read()                       # phòng trống
        det2.faces = (Face(box=thiep_box),)
        det2.bodies = (Body("đang ngồi"),)
        arrived = [e for e in [await watch.read() for _ in range(4)] if e]
        for _ in range(92):
            await watch.read()                       # ...rồi ngồi yên
        naive = watch.observations * sum(cost.values())
        real = sum(cost[k] * v for k, v in det2.calls.items() if k in cost)
        print(f"  {watch.gaze.report()}")
        print(f"  chạy hết mọi tầng mỗi lần : {naive / 1000:6.2f} s CPU")
        print(f"  cascade                   : {real / 1000:6.2f} s CPU"
              f"  ({real / naive:.1%} — rẻ hơn {naive / real:.1f}x)")
        print()
        print("  ĐỐI CHỨNG (rẻ mà mù thì vô dụng):")
        print(f"    Thiep vào                 -> "
              f"{arrived[0].text.split('.')[0] if arrived else 'IM LẶNG — HỎNG'}")
        det2.bodies = (Body("đang đứng"),)
        for i in range(1, 40):
            ev = await watch.read()
            if ev:
                print(f"    đứng dậy                  -> sau {i} lần liếc: "
                      f"{ev.text.split('.')[0]}")
                break
        else:
            print("    đứng dậy                  -> KHÔNG BAO GIỜ THẤY")
        watch.gaze.demand("hands")
        print("    gaze.demand('hands')      -> tầng hands chạy thêm 1 lần theo yêu cầu")
        before = det2.calls.get("hands", 0)
        await watch.read()
        print(f"       hands: {before} -> {det2.calls.get('hands', 0)} "
              f"(và lệnh tự xoá, không thành chi phí vĩnh viễn)")

    asyncio.run(main())


if __name__ == "__main__":
    _demo()
