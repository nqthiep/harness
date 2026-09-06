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
from dataclasses import dataclass, fields, replace
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from harness.contrib.driver import Event, Priority
from vision_tools import (Camera, Detector, IdentityLedger, PerceptionBuffer, Reading,
                          describe, distance_of, nearer_than)

#: How many consecutive identical observations make a change real rather than a flicker.
#: 2 is the cheapest value that survives one dropped frame; raise it for a jittery
#: detector, at the cost of that much extra latency per event.
DEFAULT_STABLE_READS = 2

#: What the cheap loop is allowed to notice. Names, not booleans, so a caller reads
#: `attends={PRESENCE, DISTANCE}` and knows exactly what they turned off.
PRESENCE, POSTURE, DISTANCE, SCENE = "presence", "posture", "distance", "scene"

#: `PRESENCE` is not optional — who is in front of the camera is the sensor's whole
#: contract, and `_commit`'s blindness rule is written in terms of it. The other three
#: are.
ATTENDABLE = frozenset({POSTURE, DISTANCE, SCENE})
DEFAULT_ATTENDS = frozenset({PRESENCE}) | ATTENDABLE

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

    @property
    def empty(self) -> bool:
        return not (self.arrived or self.left or self.unknown_arrived
                    or self.unknown_left or self.postures or self.moved
                    or self.nearest or self.scene_in or self.scene_out)


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
        self._committed = _State()
        self._pending: dict[str, tuple[object, int]] = {}
        #: Counters a caller can assert on rather than infer: how many observations were
        #: taken, and how many were suppressed as flicker.
        self.observations = 0
        self.suppressed = 0

    # -- acquisition -----------------------------------------------------------------

    def _capture(self) -> Reading:
        """Grab and infer. SYNC on purpose: this is the body handed to a thread, and
        keeping it sync is what makes it safe to hand over."""
        frame, err = self.camera.grab()
        if err:
            return Reading(at=time.time(), error=err)
        try:
            faces = tuple(self.detector.detect_faces(frame))
            bodies = tuple(self.detector.detect_bodies(frame))
            scene = tuple(self.detector.classify_scene(frame))
            embeddings = tuple(tuple(self.detector.embed_face(frame, f.box))
                               for f in faces)
        except Exception as exc:
            return Reading(at=time.time(),
                           error=f"nhận diện lỗi: {type(exc).__name__}: {exc}")
        try:
            height, width = int(frame.shape[0]), int(frame.shape[1])
        except Exception:
            height = width = 0
        faces = tuple(f if f.embedding else type(f)(box=f.box, score=f.score,
                                                    embedding=vec or None)
                      for f, vec in zip(faces, embeddings))
        self.buffer.frame = frame
        return Reading(at=time.time(), faces=faces, bodies=bodies, scene=scene,
                       frame_size=(width, height))

    async def _observe(self) -> Reading:
        """One full observation, identity resolved, published to the buffer."""
        reading = (await asyncio.to_thread(self._capture) if self.use_thread
                   else self._capture())
        if not reading.ok:
            self.buffer.publish(reading, None)
            return reading
        names: list[str | None] = []
        for face in reading.faces:
            vec = face.embedding
            names.append((await self.ledger.match(vec)).name if vec else None)
        resolved = Reading(at=reading.at, faces=reading.faces, bodies=reading.bodies,
                           scene=reading.scene, frame_size=reading.frame_size,
                           names=tuple(names))
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
        return _State(known=known, unknown=unknown, blind=False,
                      postures=frozenset(postures), distances=frozenset(distances),
                      nearest=nearest if DISTANCE in self.attends else "",
                      scene=scene)

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
                      scene_out=tuple(sorted(before.scene - state.scene)))

    # -- the Sensor protocol ---------------------------------------------------------

    async def read(self) -> Event | None:
        reading = await self._observe()
        self.observations += 1
        change = self._commit(self._state_of(reading))
        if change is None or change.empty:
            return None
        change = replace(change, reading=reading)
        return Event(priority=self.salience.of(change), text=render(change),
                     at=reading.at, source="camera")

    def close(self) -> None:
        self.camera.close()


# ── the demo: arrival, departure, flicker, blindness — no camera, no model ──────

def _demo() -> None:
    from harness.memory.inmemory import InMemoryStore

    from vision_tools import Body, Face, FakeDetector

    thiep_box, nghia_box = (140, 70, 240, 240), (420, 80, 180, 180)
    thiep, nghia, stranger = (1.0, 0.0, 0.1), (0.0, 1.0, 0.1), (0.5, 0.5, 0.9)

    class Cap:
        def isOpened(self) -> bool:
            return True

        def set(self, *_a) -> bool:
            return True

        def read(self):
            import numpy
            return True, numpy.zeros((480, 640, 3), dtype=numpy.uint8)

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
        sensor = CameraSensor(camera=Camera(capture=Cap()), detector=detector,
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
        watch = CameraSensor(camera=Camera(capture=Cap()), detector=detector,
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
        quiet = CameraSensor(camera=Camera(capture=Cap()), detector=detector,
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

    asyncio.run(main())


if __name__ == "__main__":
    _demo()
