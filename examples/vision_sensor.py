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
  structural `Change` — who arrived, who left, how many unrecognised faces — to a
  `Priority`. Nothing reads the priority out of a string.

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
import time
from dataclasses import dataclass
from typing import Callable

sys.path.insert(0, "src")
sys.path.insert(0, "examples")

from harness.contrib.driver import Event, Priority
from vision_tools import (Camera, Detector, IdentityLedger, PerceptionBuffer, Reading,
                          describe)

#: How many consecutive identical observations make a change real rather than a flicker.
#: 2 is the cheapest value that survives one dropped frame; raise it for a jittery
#: detector, at the cost of that much extra latency per event.
DEFAULT_STABLE_READS = 2


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

    @property
    def empty(self) -> bool:
        return not (self.arrived or self.left or self.unknown_arrived
                    or self.unknown_left)


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
    promote: Callable[[Change], Priority | None] | None = None

    def of(self, change: Change) -> Priority:
        if self.promote is not None:
            forced = self.promote(change)
            if forced is not None:
                return forced
        if change.arrived:
            return self.arrival
        if change.unknown_arrived:
            return self.unknown_arrival
        return self.departure


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

    headline = "; ".join(parts) if parts else "cảnh trước mặt vừa thay đổi"
    if change.reading is not None and change.reading.ok:
        return f"{headline[0].upper()}{headline[1:]}. {describe(change.reading)}"
    return f"{headline[0].upper()}{headline[1:]}."


@dataclass(frozen=True)
class _State:
    """A committed observation, reduced to what a `Change` is computed from."""
    known: frozenset[str] = frozenset()
    unknown: int = 0
    blind: bool = True          # no working camera yet; suppresses a fake "departure"


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
                 use_thread: bool = True) -> None:
        self.camera, self.detector, self.ledger = camera, detector, ledger
        self.buffer = buffer if buffer is not None else PerceptionBuffer()
        self.salience = salience if salience is not None else Salience()
        self.stable_reads = max(1, stable_reads)
        self.use_thread = use_thread
        self._committed = _State()
        self._pending: _State | None = None
        self._streak = 0
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

    @staticmethod
    def _state_of(reading: Reading) -> _State:
        if not reading.ok:
            return _State(blind=True)
        known = frozenset(n for n in reading.names if n)
        unknown = sum(1 for n in reading.names if n is None)
        # A face detected without an embedding is still a person present, just an
        # unidentifiable one — counting it as absent would announce a departure that did
        # not happen.
        unknown += max(0, len(reading.faces) - len(reading.names))
        return _State(known=known, unknown=unknown, blind=False)

    def _commit(self, state: _State) -> Change | None:
        """Debounce, then diff. Returns `None` while a change is still unconfirmed."""
        if state != self._pending:
            self._pending, self._streak = state, 1
        else:
            self._streak += 1
        if self._streak < self.stable_reads:
            if state != self._committed:
                self.suppressed += 1
            return None

        before, self._committed = self._committed, state
        if before == state:
            return None
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
                      unknown_left=max(0, before.unknown - state.unknown))

    # -- the Sensor protocol ---------------------------------------------------------

    async def read(self) -> Event | None:
        reading = await self._observe()
        self.observations += 1
        change = self._commit(self._state_of(reading))
        if change is None or change.empty:
            return None
        change = Change(change.arrived, change.left, change.unknown_arrived,
                        change.unknown_left, reading)
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

    asyncio.run(main())


if __name__ == "__main__":
    _demo()
