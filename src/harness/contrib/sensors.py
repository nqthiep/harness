"""Two more `Sensor` implementations, so the abstraction is tested by more than a camera.

`Sensor` (`driver.py`) is one method — `async read() -> Event | None` — plus `close()`.
It was written with exactly one real implementation in view
(`examples/vision_sensor.CameraSensor`) and a test double beside it, and
`docs/02-architecture.md` §4's plugin test is explicit that a test double is not an
implementation: it wants "two genuinely different implementations **today** — not
hypothetically". Until this file, `Sensor` did not have them, which is written down as an
open item rather than glossed (ADR-089).

The two here are chosen to pull the Protocol in opposite directions from a camera:

| | `CameraSensor` | `FileSensor` | `ClockSensor` |
|---|---|---|---|
| where the news comes from | a frame + inference | `os.stat` on watched paths | nothing outside itself |
| cost of a `read()` | a grab plus a full model pass | a handful of `stat` calls | an integer comparison |
| blocking? | yes — `asyncio.to_thread` | yes — `asyncio.to_thread` | no |
| what "changed" means | a difference against a baseline | mtime/size/existence moved | a moment arrived |
| how priority is decided | structure of the scene | which PATH moved | how the caller labelled the moment |

**Rule 1 holds in all three, and it is the reason this file is `contrib` and not
`examples`.** Priority is computed by CODE and never read out of content a stranger can
write. For a camera that means a sign held to the lens cannot say "URGENT". For a file it
means the same thing more sharply: file CONTENTS are the easiest thing in the world for
another process to control, so `FileSensor` decides priority from the path that moved and
never opens the file. A copy-pasted sensor is a place for that rule to quietly disappear
(ADR-082).

Run the demo — no camera, no clock skew, no API key:

    PYTHONPATH=src python3 -m harness.contrib.sensors
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .driver import Event, Priority

def _stamp(path: Path) -> "tuple[int, int] | None":
    """What `FileSensor` remembers about a path: `(mtime_ns, size)`, or `None` for "not
    there". Both, not just mtime — a write that lands inside the same filesystem
    timestamp tick still moves the size in most real cases, and comparing a tuple costs
    the same as comparing one number.
    """
    try:
        st = path.stat()
    except OSError:
        # Missing, unreadable, a broken symlink, a directory that vanished mid-scan —
        # all one state: "not there right now". A sensor that raises stops the whole
        # pump task (`Driver._pump_forever` has no per-sensor try), so this must not.
        return None
    return (st.st_mtime_ns, st.st_size)


@dataclass
class FileSensor:
    """Reports that watched paths changed, appeared, or disappeared.

    Real uses this exists for: a CI run wrote its results file; a human edited the file
    the agent is in the middle of refactoring; a queue directory got a new job.

    **The first `read()` establishes the baseline and reports nothing** — the same rule
    `CameraSensor` follows, for the same reason: "this file exists" is not news, and a
    sensor that announced its whole initial state would preempt the first turn of every
    conversation. Pass `baseline=False` to treat the starting state as already-changed
    (a queue directory whose existing entries ARE the work).

    **Priority comes from the path, never from the contents.** `promote` is handed the
    tuple of paths that moved and returns a `Priority` or `None`; the file is not opened
    here at all. Rule 1 in `driver.py` exists because a camera's frame is attacker-
    controlled; a file's bytes are more so.
    """

    paths: Sequence["str | Path"]
    priority: Priority = Priority.NORMAL
    #: Decides priority from WHICH paths moved. `None` -> `self.priority` for any change.
    promote: "Callable[[tuple[str, ...]], Priority | None] | None" = None
    baseline: bool = True
    _seen: "dict[str, tuple[int, int] | None]" = field(default_factory=dict)
    _started: bool = False
    closed: bool = False

    def _watched(self) -> "list[Path]":
        return [Path(p) for p in self.paths]

    def _scan(self) -> "dict[str, tuple[int, int] | None]":
        """Blocking: one `stat` per path. Called through `asyncio.to_thread`."""
        return {str(p): _stamp(p) for p in self._watched()}

    async def read(self) -> Event | None:
        if self.closed:
            return None
        now = await asyncio.to_thread(self._scan)
        if not self._started:
            # No early `return None` here, deliberately: seeding `_seen` with the current
            # state IS the baseline, so the diff below comes out empty on its own. A
            # `return` as well would be redundant — found by mutation, when deleting it
            # changed no test result (ADR-089), which is the signal that a line is not
            # doing work.
            self._started = True
            self._seen = now if self.baseline else {k: None for k in now}
        moved = tuple(sorted(k for k, v in now.items() if self._seen.get(k, None) != v))
        before, self._seen = self._seen, now
        if not moved:
            return None
        priority = self.priority
        if self.promote is not None:
            promoted = self.promote(moved)
            if promoted is not None:
                priority = promoted
        return Event(priority, _describe(moved, before, now), source="file")

    def close(self) -> None:
        self.closed = True


def _describe(moved: "tuple[str, ...]",
              before: "dict[str, tuple[int, int] | None]",
              now: "dict[str, tuple[int, int] | None]") -> str:
    """A sentence, because `Event.text` is what the MODEL reads and `dispatch.py` renders
    a tool result as `str` or `json.dumps` — there is no structured channel to use.

    Three words, not two: a path that was not there and now is has APPEARED, and telling
    the model it "changed" would be a small lie in the one direction that matters (a new
    file in a queue directory is a different fact from an edited one).
    """
    def one(p: str) -> str:
        if now.get(p) is None:
            return f"{p} (gone)"
        return f"{p} ({'appeared' if before.get(p) is None else 'changed'})"

    if len(moved) == 1:
        return f"{one(moved[0])} on disk"
    head = ", ".join(one(p) for p in moved[:3])
    rest = f" and {len(moved) - 3} more" if len(moved) > 3 else ""
    return f"{len(moved)} watched paths moved: {head}{rest}"


@dataclass(frozen=True)
class Moment:
    """A time worth mentioning, and how urgent mentioning it is.

    `priority` is a field here rather than something `ClockSensor` derives from how close
    the deadline is: "5 minutes out is HIGH" is a policy about the caller's calendar, not
    a fact about clocks, and rule 1 says the code that KNOWS decides.
    """
    at: float
    text: str
    priority: Priority = Priority.NORMAL


@dataclass
class ClockSensor:
    """Reports that a scheduled moment has arrived. No I/O, no external state.

    **It cannot wake anybody.** `Sensor` is a PULL interface: `Driver` reads it every
    `sensor_interval_s` (0.2 s by default), so a moment is noticed up to that late and no
    sooner — and not at all while nothing is pumping. That is a real property of the
    Protocol, surfaced by writing the one implementation that has an opinion about
    *when*, and it is the argument for keeping `sensor_interval_s` small enough for the
    shortest deadline you care about rather than for adding a push path (ADR-089).

    Each moment fires at most once. Overdue moments fire on the first read after their
    time, most urgent first, then oldest first — a sensor that skipped a moment merely
    because nothing pumped through it would be a clock that loses appointments.
    """

    moments: Sequence[Moment] = ()
    _fired: "set[int]" = field(default_factory=set)
    closed: bool = False

    async def read(self) -> Event | None:
        if self.closed:
            return None
        now = time.time()
        due = [(i, m) for i, m in enumerate(self.moments)
               if i not in self._fired and m.at <= now]
        if not due:
            return None
        i, moment = max(due, key=lambda pair: (pair[1].priority, -pair[1].at))
        self._fired.add(i)
        return Event(moment.priority, moment.text, source="clock")

    def close(self) -> None:
        self.closed = True


def after(seconds: float, text: str, priority: Priority = Priority.NORMAL) -> Moment:
    """`Moment` relative to now — the form nearly every caller wants, and the one that
    does not invite an accidental `at=5.0` meaning 1970."""
    return Moment(time.time() + seconds, text, priority)


def watch(paths: Iterable["str | Path"], **kw: object) -> FileSensor:
    """`FileSensor` from any iterable, since a caller usually has a generator or a
    `Path.glob`."""
    return FileSensor(list(paths), **kw)  # type: ignore[arg-type]


def _demo() -> None:
    import tempfile

    async def main() -> None:
        root = Path(tempfile.mkdtemp(prefix="harness-sensors-"))
        watched, urgent = root / "results.json", root / "PANIC"

        def shown(e: "Event | None") -> str:
            return f"{e.priority.name:8} {e.text}" if e is not None else "(nothing)"

        def by_path(moved: "tuple[str, ...]") -> "Priority | None":
            return Priority.CRITICAL if str(urgent) in moved else None

        fs = FileSensor([watched, urgent], promote=by_path)

        print("=" * 74)
        print("FileSensor — the first read is a BASELINE, not news")
        print("=" * 74)
        print(f"  read 1: {shown(await fs.read())}   <- 'this file exists' is not news")

        watched.write_text('{"passed": 3}')
        print(f"  read 2: {shown(await fs.read())}")
        print(f"  read 3: {shown(await fs.read())}   <- unchanged since, so nothing again")

        urgent.write_text("stop")
        print(f"  read 4: {shown(await fs.read())}")
        print("           ^ CRITICAL because of WHICH path moved. The file's CONTENTS")
        print("             are never read here — rule 1: a stranger writes those.")

        watched.unlink()
        print(f"  read 5: {shown(await fs.read())}")
        fs.close()

        print()
        print("=" * 74)
        print("ClockSensor — it answers when asked; it cannot wake anyone")
        print("=" * 74)
        cs = ClockSensor([after(0.15, "standup starts", Priority.HIGH),
                          after(0.15, "coffee", Priority.LOW)])
        print(f"  immediately   : {shown(await cs.read())}   <- nothing is due yet")
        await asyncio.sleep(0.2)
        print(f"  after 0.2s    : {shown(await cs.read())}")
        print(f"  then          : {shown(await cs.read())}")
        print(f"  and then      : {shown(await cs.read())}   <- each moment fires once")
        print()
        print("  Both moments came due in the SAME 0.2s gap and the more urgent one was")
        print("  reported first. Punctuality is bounded by Driver.sensor_interval_s")
        print("  (0.2s by default): a pull interface cannot be earlier than its poll.")
        cs.close()

    asyncio.run(main())


if __name__ == "__main__":
    _demo()
