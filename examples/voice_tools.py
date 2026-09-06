"""Hearing, in the same three layers vision uses — pure logic, an adapter, then glue.

Written to answer a design question with evidence rather than a claim: *is the vision
code actually shaped so another sense is easy to add?* Everything here that could have
been shared with `vision_tools.py` was, everything that could not is named, and the
places where the vision design did NOT generalise are recorded in ADR-122 rather than
smoothed over.

**Layer 1, pure.** `speech_of`, `loudness_band`, `Utterance` — arithmetic on numbers, no
microphone, no model. Testable to the digit.

**Layer 2, the adapter.** `Listener` is a Protocol, mirroring `vision_tools.Detector`:
four methods, so a `FakeListener` in a test is a dozen lines and every branch above is
reachable without a soundcard.

**Layer 3, glue.** `Microphone` mirrors `Camera` — it owns the device, degrades to a
diagnosis instead of an exception, and is the only thing here that touches hardware.

**What is deliberately NOT here.** No transcription model ships with this file and none
is written against: `FakeListener` is the only implementation that runs. A real one is a
`Listener` and nothing else changes — that is the whole point of the Protocol — but this
module makes no claim about any particular ASR backend, because none has been run here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

#: RMS amplitude, 0..1, above which a block counts as sound rather than room tone. Same
#: role as vision's `DEFAULT_MOTION_THRESHOLD` and the same caveat: it is the noise floor
#: of a particular room, so it is a knob, not a constant of nature.
DEFAULT_SILENCE_FLOOR = 0.02

#: How loud, in bands, ORDERED quiet → loud. The order is the point, exactly as with
#: `vision_tools.DISTANCE_BANDS`: "vừa to hẳn lên" is a comparison between two bands, so
#: something has to say which of two strings is louder.
LOUDNESS_BANDS: tuple[str, ...] = ("thì thầm", "nói bình thường", "nói to")
LOUDNESS_CUTS: tuple[float, ...] = (0.08, 0.25)

#: Below this, a transcript is a guess and gets reported as unheard rather than as words.
MIN_TRANSCRIPT_CONFIDENCE = 0.5

UNKNOWN_SPEECH = "không nghe rõ"


def rms(samples: Sequence[float]) -> float:
    """Root mean square of a block of samples in -1..1. The cheapest question you can ask
    of audio — the microphone's answer to vision's frame difference.

    `0.0` for an empty block: no samples is not silence, but it is also not evidence of
    sound, and the caller's `blind`-equivalent is what distinguishes those.
    """
    if not samples:
        return 0.0
    return math.sqrt(sum(float(s) * float(s) for s in samples) / len(samples))


def loudness_band(level: float) -> str:
    """Which band a level falls in. QUANTISED for the same reason `distance_of` is: a
    continuous value in a sensor's committed state differs on every block, so every block
    would be a change and the expensive rate would collapse into the cheap one."""
    band = LOUDNESS_BANDS[0]
    for cut, name in zip(LOUDNESS_CUTS, LOUDNESS_BANDS[1:]):
        if level >= cut:
            band = name
    return band


def louder_than(a: str, b: str) -> bool:
    """Is band `a` louder than band `b`? Unknown bands compare as neither — mirrors
    `vision_tools.nearer_than`, including the reason: `""` is "don't know", not "quietest",
    and a move to or from it is not a change in volume."""
    if a not in LOUDNESS_BANDS or b not in LOUDNESS_BANDS:
        return False
    return LOUDNESS_BANDS.index(a) > LOUDNESS_BANDS.index(b)


@dataclass(frozen=True)
class Utterance:
    """One stretch of speech, reduced to what is worth saying out loud.

    `speaker` is `None` when nobody has been identified — the same three-state convention
    as `Reading.names`, and for the same reason: "nobody asked", "asked and did not
    recognise" and "recognised" are genuinely different situations and collapsing them
    loses the one that matters.
    """
    text: str = ""
    confidence: float = 0.0
    speaker: str | None = None

    @property
    def heard(self) -> bool:
        return bool(self.text) and self.confidence >= MIN_TRANSCRIPT_CONFIDENCE


@dataclass(frozen=True)
class Hearing:
    """One listen, immutable — the audio counterpart of `vision_tools.Reading`, frozen for
    the same reason: a capture thread publishes by rebinding a reference, and an atomic
    rebind needs no lock where a mutated object would."""
    at: float
    level: float = 0.0
    speech: bool = False
    utterances: tuple[Utterance, ...] = ()
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def speech_of(level: float, floor: float = DEFAULT_SILENCE_FLOOR) -> bool:
    """Is this block sound, or room tone? The tier-1 question, and as crude as
    `detect_faces` is cheap.

    Deliberately NOT "is this a human voice" — that needs a VAD model, which is a
    `Listener`, not this. Calling a crude energy gate "voice detection" would be the same
    overclaim `posture_of` refuses when it returns "không rõ dáng".
    """
    return level >= floor


def describe(hearing: Hearing) -> str:
    """One or two sentences of plain observation — this function IS "it has ears".

    Written as observation rather than as telemetry, for the reason `vision_tools.describe`
    gives: a result phrased as plumbing invites the agent to narrate its plumbing.
    """
    if not hearing.ok:
        return f"Tôi không nghe được gì lúc này ({hearing.error})."
    if not hearing.speech:
        return "Xung quanh đang yên tĩnh."
    heard = [u for u in hearing.utterances if u.heard]
    if not heard:
        return f"Có tiếng động ({loudness_band(hearing.level)}) nhưng tôi không nghe rõ lời."
    parts = []
    for u in heard:
        who = u.speaker or "ai đó"
        parts.append(f'{who} nói: "{u.text}"')
    return ". ".join(parts) + "."


class Listener(Protocol):
    """What layers 1 and 3 need from an audio backend, and nothing more.

    Four methods, mirroring `vision_tools.Detector`, and split along the same cost seam:
    `level` is the cheap one that runs always, `transcribe` is the expensive one that runs
    when something happened.
    """

    def level(self, block: Any) -> float: ...
    def detect_speech(self, block: Any) -> bool: ...
    def transcribe(self, block: Any) -> Sequence[Utterance]: ...
    def embed_voice(self, block: Any) -> tuple[float, ...]: ...


@dataclass
class FakeListener:
    """Scripted hearing. What the tests and the demo run against — and, today, the only
    `Listener` that exists, which this docstring says out loud rather than implying a
    backend that has not been run."""
    samples: Sequence[float] = ()
    speech: bool = False
    utterances: Sequence[Utterance] = ()
    voice: tuple[float, ...] = ()
    #: Counted for the same reason `FakeDetector.calls` is: a cascade's whole claim is
    #: about the calls it does NOT make, and that is only checkable if something counts.
    calls: dict[str, int] = field(default_factory=dict)

    def _called(self, kind: str) -> None:
        self.calls[kind] = self.calls.get(kind, 0) + 1

    def level(self, block: Any) -> float:
        self._called("level")
        return rms(self.samples)

    def detect_speech(self, block: Any) -> bool:
        self._called("speech")
        return self.speech

    def transcribe(self, block: Any) -> Sequence[Utterance]:
        self._called("transcribe")
        return tuple(self.utterances)

    def embed_voice(self, block: Any) -> tuple[float, ...]:
        self._called("embed")
        return tuple(self.voice)


class Microphone:
    """Owns the device and turns every failure into a DIAGNOSIS, never an exception.

    Mirrors `vision_tools.Camera`, including the rule that matters: a sensor that raises
    stops `Driver._pump_forever` for every sensor, so this returns `(None, "why")` and
    lets the caller decide that "I cannot hear" is not "the room is silent".
    """

    def __init__(self, source: Any = None, *, name: str = "micro") -> None:
        self.source, self.name = source, name
        self._closed = False

    def grab(self) -> tuple[Any, str]:
        if self._closed:
            return None, f"{self.name} đã đóng"
        if self.source is None:
            return None, (f"{self.name} chưa được nối vào thiết bị nào. "
                          "Truyền `source=` một đối tượng có `.read()` trả về block mẫu.")
        try:
            block = self.source.read()
        except Exception as exc:
            return None, f"{self.name} lỗi khi đọc: {type(exc).__name__}: {exc}"
        if block is None:
            return None, f"{self.name} không trả về mẫu nào"
        return block, ""

    def close(self) -> None:
        self._closed = True
        closer = getattr(self.source, "close", None)
        if callable(closer):
            closer()
