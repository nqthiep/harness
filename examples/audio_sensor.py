"""`MicSensor` — the microphone as a real `Sensor`: it reports what was SAID, once the
speaker has finished saying it.

This is the piece that connects `audio_tools.py` (ears) to `harness.contrib.driver`
(priority-served runtime events). It is `vision_sensor.CameraSensor`'s twin and the
differences between them are the interesting part, because two of the three are places
where copying the camera would have been wrong.

| | `CameraSensor` | `MicSensor` |
|---|---|---|
| what a `read()` costs | a grab plus a full model pass | draining a list |
| state | the current frame | `interim_input_transcription` |
| event | a diff against a committed baseline | `input_transcription`, once VAD finalises |
| debounce | `stable_reads` — N identical observations | **none needed** — VAD is the debounce |
| baseline | first observation announces nothing | **no baseline** — the first sentence IS news |
| priority from | structure of the scene | structure of the utterance, NEVER its words |

**`read()` must not block, and that is a hard constraint rather than a preference.**
`Driver.pump()` walks `self.sensors` sequentially and awaits each `read()` in turn. A
microphone sensor that waited for the next utterance would hold that loop for as long as
the room stayed quiet, and the camera beside it would not be read at all — an agent that
stops seeing whenever nobody is talking. So the socket is drained by a background task
that `audio_tools.GeminiTranscriber` owns, and `read()` only empties what has already
arrived. `tests/test_audio_sensor.py` asserts this directly rather than trusting it.

**No baseline, unlike the camera, and the asymmetry is real.** `CameraSensor`'s first
observation establishes a baseline and announces nothing, because "these people are
present" is a state and opening your eyes is not everyone arriving. Speech has no
equivalent: there is no such thing as an utterance that was already true before you
started listening. The first finalised sentence is news exactly like the tenth.

**No debounce, unlike the camera, and this is the API doing better than our workaround.**
`stable_reads` exists because a detector flickers and one dropped frame would otherwise
read as "Thiep left" followed by "Thiep arrived". The equivalent failure for speech is
cutting a sentence in half at a pause — and server-side VAD already solves it, from
silence, which is the actual evidence, rather than from N identical guesses. Adding a
counter on top would only delay every event by one poll.

**Rule 1 is enforced by the type, not by discipline.** `Salience.of()` receives a
`Change` that has no text field. It cannot read the words even by mistake. That matters
more here than anywhere else in this repository: the camera's threat model needs an
attacker to hold a sign up to the lens, while a microphone only needs someone in the
room — or a television, or a phone on speaker — to say "khẩn cấp" out loud. The words
still reach the model, because they are the news; they just never reach a `Priority`.

**An error is silence, not news.** A dropped socket or a dead microphone produces no
event, for the same reason a broken camera does not announce that everyone left: "I
cannot hear" is not "nobody spoke", and only one of those is a claim about the world this
sensor has evidence for. The error is counted and published to the buffer, where a
caller who wants to show it can find it.

Run it — no microphone, no key, no network:

    python3 examples/audio_sensor.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from harness.contrib.driver import Event, Priority
from audio_tools import (CHUNK_MS, Microphone, Transcriber, Utterance, UtteranceBuffer)

#: How long the feed loop sleeps when the microphone is broken, so a dead device does not
#: become a busy loop. Ten chunks' worth: long enough to cost nothing, short enough that
#: a device coming back is picked up within a second.
BROKEN_MIC_BACKOFF_S = CHUNK_MS * 10 / 1000


@dataclass(frozen=True)
class Change:
    """What was heard, reduced to the structural facts a priority is allowed to read.

    **There is no text field, deliberately.** `Salience` maps this to a `Priority`, so
    nothing in that chain can be steered by what somebody said — the same construction
    `vision_sensor.Change` uses, and for a sharper reason (this module's docstring).
    """
    speaker: str | None = None
    words: int = 0
    language: str | None = None
    #: True when `speaker` matches a label the operator told this sensor to trust. The
    #: only structural handle on "who", and the honest way to let the person the agent
    #: is talking to interrupt it without letting the television do the same.
    known_speaker: bool = False

    @property
    def empty(self) -> bool:
        return self.words == 0


@dataclass(frozen=True)
class Salience:
    """The priority table. Conservative by default: nothing here can preempt a turn.

    Same posture as `vision_sensor.Salience`, and it deserves restating because the
    temptation is stronger. Speech feels urgent, and CRITICAL is a `task.cancel()` on the
    running turn — the whole turn's work, gone. Somebody talking in the room while the
    agent is mid-task is normally worth reading at the next model call and nothing more.

    `promote` is the operator's escape hatch: their own callable, handed a `Change` and
    never a string, returning a `Priority` or `None`. That is how "let ME interrupt it"
    gets built — key on `known_speaker`, not on a wake word, because a wake word is text
    and a television can read it out.
    """
    speech: Priority = Priority.NORMAL
    unknown_speaker: Priority = Priority.NORMAL
    promote: Callable[[Change], Priority | None] | None = None

    def of(self, change: Change) -> Priority:
        if self.promote is not None:
            forced = self.promote(change)
            if forced is not None:
                return forced
        return self.speech if change.known_speaker else self.unknown_speaker


def render(change: Change, text: str) -> str:
    """The sentence the model eventually reads.

    Two jobs. First, deliver the words — they are the news, and a sensor that reported
    "somebody said something" without saying what would be useless. Second, frame them
    as REPORTED SPEECH rather than as instructions: the words arrive through a channel
    the model reads as a tool result, they were typed by nobody and spoken by anybody in
    earshot, and a bare sentence in that position reads like it came from the operator.
    Attribution plus quotation marks is the whole defence available at this layer; the
    real one is that `look`/`listen` content is `Integrity.UNTRUSTED` and the policy
    engine knows it.
    """
    who = change.speaker if change.speaker else "ai đó"
    return f'Nghe được qua micro — {who} vừa nói: "{text}"'


class MicSensor:
    """A `Sensor` (`harness.contrib.driver`) over a `Microphone` + a `Transcriber`.

    `buffer=` is shared the same way `CameraSensor`'s `PerceptionBuffer` is: whoever
    wants "what is being said right now" reads `.interim` off it instead of opening a
    second microphone.

    The feed task starts on the first `read()` rather than in `__init__`, because
    `Sensor` has no `start()` and creating a task needs a running loop. `read()` is
    async and is called from `Driver._pump_forever`, so by the time it runs there is
    one.
    """

    def __init__(self, *, microphone: Microphone, transcriber: Transcriber,
                 buffer: UtteranceBuffer | None = None,
                 salience: Salience | None = None,
                 known_speakers: "frozenset[str] | set[str] | tuple[str, ...]" = (),
                 use_thread: bool = True) -> None:
        self.microphone, self.transcriber = microphone, transcriber
        self.buffer = buffer if buffer is not None else UtteranceBuffer()
        self.salience = salience if salience is not None else Salience()
        self.known_speakers = frozenset(known_speakers)
        self.use_thread = use_thread
        self._feed: asyncio.Task[None] | None = None
        self.closed = False
        #: Counters a caller can assert on rather than infer.
        self.chunks_sent = 0
        self.utterances = 0
        self.errors = 0

    # -- the feed: audio in, out of band ----------------------------------------------

    async def _feed_forever(self) -> None:
        """Microphone to transcriber, forever, on its own task.

        `asyncio.to_thread` for the read: `RawInputStream.read()` blocks until the chunk
        is full, which is 100 ms of the event loop the agent's own run is using. That is
        the same trap `CameraSensor` avoids for the same reason.

        **Paced to real time, and that is not belt-and-braces.** Measured, by writing it
        without the pacing first: with a non-blocking capture (`use_thread=False` over a
        stream that returns a chunk immediately) this loop never reaches an `await` that
        actually yields — awaiting a coroutine which itself never suspends does not hand
        control back — so it spun at full speed, appended to the transcriber forever, and
        the demo was `Killed`, exit 137, out of memory. A real microphone hides the bug
        by blocking for `CHUNK_MS`; a test double does not. You cannot feed audio faster
        than it was recorded, so sleeping off the remainder of the chunk is the true
        constraint rather than a workaround, and against a real device it costs nothing
        because the read already took that long.
        """
        await self.transcriber.open()
        while not self.closed:
            started = time.monotonic()
            pcm, err = (await asyncio.to_thread(self.microphone.read) if self.use_thread
                        else self.microphone.read())
            if err or pcm is None:
                self.errors += 1
                self.buffer.publish(Utterance(error=err or "không đọc được micro"))
                await asyncio.sleep(BROKEN_MIC_BACKOFF_S)
                continue
            await self.transcriber.push(pcm)
            self.chunks_sent += 1
            await asyncio.sleep(max(0.0, CHUNK_MS / 1000 - (time.monotonic() - started)))

    def _ensure_feed(self) -> None:
        if self._feed is None and not self.closed:
            self._feed = asyncio.create_task(self._feed_forever())

    # -- the Sensor protocol ----------------------------------------------------------

    async def read(self) -> Event | None:
        """Drain what has arrived. Never waits for speech — see the module docstring."""
        if self.closed:
            return None
        self._ensure_feed()
        heard = await self.transcriber.poll()
        finals: list[Utterance] = []
        for utterance in heard:
            self.utterances += 1
            if not utterance.ok:
                # Silence, not news. Counted and published; no event.
                self.errors += 1
                self.buffer.publish(utterance)
                continue
            self.buffer.publish(utterance)
            if utterance.final and utterance.text.strip():
                finals.append(utterance)
        if not finals:
            return None
        # Several finalised utterances in one drain are JOINED rather than reduced to the
        # newest. `EventInbox` holds one event, so returning only the last would silently
        # drop something a human actually said — the one loss this sensor must not take.
        text = " ".join(u.text.strip() for u in finals)
        last = finals[-1]
        change = Change(speaker=last.speaker, words=sum(u.words for u in finals),
                        language=last.language,
                        known_speaker=bool(last.speaker
                                           and last.speaker in self.known_speakers))
        if change.empty:
            return None
        return Event(priority=self.salience.of(change), text=render(change, text),
                     at=last.at, source="mic")

    def close(self) -> None:
        self.closed = True
        if self._feed is not None:
            self._feed.cancel()
            self._feed = None
        self.transcriber.close()
        self.microphone.close()


# ── the demo: an utterance, an interim, a broken mic, and rule 1 ────────────────


def _demo() -> None:
    from audio_tools import CHUNK_BYTES, FakeTranscriber

    class Cap:
        """A microphone that always has a chunk ready."""
        def read(self, frames: int):
            return b"\x00" * CHUNK_BYTES, False

        def stop(self) -> None: ...
        def close(self) -> None: ...

    async def main() -> None:
        print("=" * 78)
        print("1. Interim KHÔNG sinh sự kiện; final thì có")
        print("=" * 78)
        script = [(Utterance("hôm", False),),
                  (Utterance("hôm nay mình", False),),
                  (Utterance("Hôm nay mình đi đâu?", True, language="vi-VN"),),
                  ()]
        sensor = MicSensor(microphone=Microphone(stream=Cap()),
                           transcriber=FakeTranscriber(script=script), use_thread=False)
        for label in ("interim 1", "interim 2", "FINAL", "im lặng"):
            event = await sensor.read()
            state = sensor.buffer.interim.text if sensor.buffer.interim else None
            print(f"  {label:12} -> event={event.text if event else None}")
            print(f"  {'':12}    buffer.interim={state!r}")

        print()
        print("=" * 78)
        print("2. Hai câu chốt trong cùng một lần drain -> GHÉP, không bỏ câu nào")
        print("=" * 78)
        two = MicSensor(microphone=Microphone(stream=Cap()), use_thread=False,
                        transcriber=FakeTranscriber(script=[(
                            Utterance("Câu một.", True), Utterance("Câu hai.", True))]))
        event = await two.read()
        print(f"  {event.text if event else None}")

        print()
        print("=" * 78)
        print("3. Mic hỏng = im lặng, KHÔNG phải 'không có ai nói'")
        print("=" * 78)
        broken = MicSensor(microphone=Microphone(),      # không có PortAudio ở máy này
                           transcriber=FakeTranscriber(script=[()]), use_thread=False)
        print(f"  read() -> {await broken.read()}")
        await asyncio.sleep(0)                            # để feed task chạy một vòng
        await asyncio.sleep(0.01)
        print(f"  buffer.interim -> {broken.buffer.interim}")
        print(f"  errors={broken.errors} (đếm được, nhưng không thành sự kiện)")
        broken.close()

        print()
        print("=" * 78)
        print("4. Luật 1 — Salience KHÔNG có chữ để mà đọc")
        print("=" * 78)
        shout = Change(speaker="spk_9", words=2, known_speaker=False)
        owner = Change(speaker="spk_1", words=2, known_speaker=True)
        only_owner = Salience(promote=lambda c: Priority.CRITICAL if c.known_speaker
                              else None)
        print(f"  mặc định        : người lạ={Salience().of(shout).name}, "
              f"chủ nhà={Salience().of(owner).name}  -> không bao giờ preempt")
        print(f"  promote của bạn : người lạ={only_owner.of(shout).name}, "
              f"chủ nhà={only_owner.of(owner).name}")
        print(f"  Change có field : {sorted(Change.__dataclass_fields__)}")
        print("  -> không có 'text'. Ai hét 'KHẨN CẤP' cũng không nâng được priority.")
        sensor.close(); two.close()

    asyncio.run(main())


if __name__ == "__main__":
    _demo()
