"""Ears: microphone capture, a `Transcriber` seam over Gemini 3.5 Transcribe Live, and
the utterance type the sensor layer turns into events.

Same three layers as `vision_tools.py`, for the same reason — only the first two can be
tested without hardware and a network, and that is where the bugs live:

1. **Business logic, pure.** `Utterance` and `UtteranceBuffer`. No sounddevice, no
   network, no `Agent`. What counts as "still being said" versus "said", and what the
   latest state is, are decided here and tested with hand-written utterances
   (`tests/test_audio_tools.py`).
2. **Adapters.** `Transcriber` is a four-method Protocol; `GeminiTranscriber` implements
   it against the Live API, `FakeTranscriber` implements it for tests. `Microphone`
   wraps `sounddevice.RawInputStream`. Anything needing hardware or a key lives ONLY
   here.
3. **The sensor.** `audio_sensor.MicSensor` — a `harness.contrib.driver.Sensor`.

**The state/event distinction is handed to us by the API, and that is the whole reason
this is a good fit.** `vision_sensor.py`'s docstring argues at length that a state is not
an event and that a sensor must report differences. The Live API makes the same
distinction in its own wire protocol: `interim_input_transcription` arrives repeatedly
while someone is still talking (state, and it changes under you), and
`input_transcription` arrives once when the turn finalises (the event). So `MicSensor`
needs no `stable_reads` debounce at all — server-side VAD deciding "they stopped
talking" is a better version of what the camera's flicker counter approximates, and it
is decided from silence rather than from N identical guesses.

**What the model is allowed to be told, and what may set priority.** A transcript is
attacker-controlled text in the most direct way this project has yet had to handle: the
camera's threat model needs a sign held to the lens, a microphone only needs someone in
the room — or a television — to say the words out loud. `Utterance.text` therefore
reaches the model (it is the news; withholding it would make the sensor useless) but
NEVER reaches a `Priority`. `audio_sensor.Salience` is handed a `Change` carrying only
structural facts and no text at all, so rule 1 of `harness.contrib.driver` holds by
construction rather than by care.

**Measured on this environment, and it is not the failure you would guess.** With no
PortAudio installed, `import sounddevice` raises **`OSError('PortAudio library not
found')` at import time** — not `ImportError`. A `try/except ImportError` around it,
which is the obvious thing to write and what `Camera` correctly does for `cv2`, does not
catch this. That is the same shape as ADR-092 (`MediaPipeDetector.preflight` exists
because a missing system library surfaces as an `OSError` from `ctypes.CDLL`), so it is
handled the same way rather than rediscovered later.

**`GeminiTranscriber` is UNRUN CODE.** There is no API key and no microphone here, and
this container's egress proxy blocks every Google domain, so not one line of it has
executed against the real service. It was written against the installed
`google-genai` SDK's own type definitions — which is a better source than the prose
documentation, and still not a run. `ADR-077` says what this project thinks of unrun
code and `ADR-090` says what fixed it last time: a probe script that goes and measures.
`tests/audio_probe.py` is that script, and until someone runs it, treat every claim in
this class as a hypothesis. What the SDK's types DO pin down, read directly out of
`google/genai/types.py`:

    AudioTranscriptionConfig(language_codes=[], custom_vocabulary=[...],
                             word_timestamp=bool, diarization=bool, mode=VERBATIM|SMART)

and, on `mode`, a constraint no blog post mentions: *"Timestamps and diarization are
incompatible with mode `SMART`."* `SMART` is the mode that strips "ừm", resolves
self-corrections and auto-formats; `VERBATIM` is the default and the one that can also
tell you who spoke. You cannot have both, so `GeminiTranscriber` refuses the combination
at construction instead of letting the server reject it mid-conversation.

Run it — no microphone, no key, no network:

    python3 examples/audio_tools.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

#: What the Live API accepts, from the SDK's own example: raw little-endian 16-bit PCM,
#: mono, 16 kHz. Not a preference — the mime type carries the rate and the server reads
#: the bytes at that rate whatever the microphone actually sampled at.
SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2                       # int16
MIME_TYPE = f"audio/pcm;rate={SAMPLE_RATE}"

#: How much audio travels per `send_realtime_input`. 100 ms is what Google's own guidance
#: uses; it is also the granularity at which a `close()` can interrupt the feed loop.
CHUNK_MS = 100
CHUNK_FRAMES = SAMPLE_RATE * CHUNK_MS // 1000
CHUNK_BYTES = CHUNK_FRAMES * CHANNELS * SAMPLE_WIDTH

#: The model ids, spelled once. `-live` is the Live API (WebSocket, bidirectional);
#: the one without the suffix is the Interactions API for recorded files and is NOT what
#: this module talks to.
LIVE_MODEL = "gemini-3.5-transcribe-live"

#: A live session is capped by the service. Past this the socket closes and the feed has
#: to reconnect, which is why `GeminiTranscriber` owns a reconnect rather than assuming
#: one long stream.
SESSION_LIMIT_S = 10 * 60


# ── layer 1: pure ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Utterance:
    """One thing heard. Frozen for the same reason `vision_tools.Reading` is: a
    background task publishes by rebinding a reference and the reader needs no lock.

    `final` is the whole state/event distinction in one field. `False` is an interim —
    the speaker is still talking and this text will be replaced. `True` means the turn
    finalised and this text is now history. Only finals become events.

    `speaker` is the API's diarization label (`"spk_1"`) when the service supplies one.
    Documentation says diarization is a recorded-audio feature and not available over
    the Live API, while the SDK's `Transcription` type carries the field on both paths —
    so this is `None` until `tests/audio_probe.py` measures which is true. Nothing here
    may depend on it being populated.
    """
    text: str = ""
    final: bool = False
    language: str | None = None
    speaker: str | None = None
    at: float = field(default_factory=time.time)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def words(self) -> int:
        """How many words were said — a structural fact, safe for a priority table to
        read, unlike the words themselves."""
        return len(self.text.split())


class UtteranceBuffer:
    """The latest thing heard, for whoever wants the STATE rather than the events.

    The twin of `vision_tools.PerceptionBuffer`, and it exists for the same reason: one
    acquisition, several consumers. `MicSensor` publishes here on every utterance
    including interims; a caller polls `.interim` to render "…đang nghe" in a UI, or
    `.last_final` to know what the last completed sentence was, without opening a second
    microphone or a second socket.
    """

    def __init__(self) -> None:
        self.interim: Utterance | None = None
        self.last_final: Utterance | None = None

    def publish(self, utterance: Utterance) -> None:
        if utterance.final:
            self.last_final, self.interim = utterance, None
        else:
            self.interim = utterance


# ── layer 2: adapters ───────────────────────────────────────────────────────────


class Transcriber(Protocol):
    """The seam. Four methods, and the important one is `poll`.

    `poll()` MUST NOT block waiting for speech. `Driver.pump()` reads every sensor
    sequentially in one loop (`driver.py`), so a `read()` that awaits the next utterance
    would stop the camera being read at all for as long as the room is quiet. Draining
    what has already arrived is the only shape that composes with the other sensors.
    """

    async def open(self) -> None: ...
    async def push(self, pcm: bytes) -> None: ...
    async def poll(self) -> tuple[Utterance, ...]: ...
    def close(self) -> None: ...


@dataclass
class FakeTranscriber:
    """Scripted utterances, for tests and the demo — the twin of
    `vision_tools.FakeDetector`.

    `script` is consumed one *batch* per `poll()`, so a test can place an interim and its
    final in the same poll or in different ones and get deterministic behaviour either
    way. `pushed` records the audio it was fed, which is how a test asserts the feed loop
    ran without a real microphone.
    """
    script: Sequence[Sequence[Utterance]] = ()
    _i: int = 0
    opened: bool = False
    closed: bool = False
    pushed: list[bytes] = field(default_factory=list)

    async def open(self) -> None:
        self.opened = True

    async def push(self, pcm: bytes) -> None:
        self.pushed.append(pcm)

    async def poll(self) -> tuple[Utterance, ...]:
        if self._i >= len(self.script):
            return ()
        batch = tuple(self.script[self._i])
        self._i += 1
        return batch

    def close(self) -> None:
        self.closed = True


class Microphone:
    """`sounddevice.RawInputStream`, with every failure turned into a sentence.

    The same contract `vision_tools.Camera` keeps: a caller gets `(pcm, error)` and never
    an exception, because the thing reading this is a sensor inside `Driver._pump_forever`,
    which has no per-sensor `try` — a sensor that raises stops the whole pump.

    Measured here with no PortAudio and no device: `import sounddevice` raises
    **`OSError`**, not `ImportError`, so both are caught. Do not "simplify" that to one
    except clause; it is the difference between a sentence and a crash on any machine
    without the system library.
    """

    def __init__(self, *, device: "int | str | None" = None,
                 stream: Any = None) -> None:
        self.device = device
        #: An already-open stream, for tests and for a caller who owns the device.
        #: Owning it means closing it: `close()` releases only what THIS object opened,
        #: which is `Camera`'s rule.
        self._stream = stream
        self._owned = stream is None
        self._error: str | None = None

    def _open(self) -> str | None:
        if self._stream is not None:
            return None
        try:
            import sounddevice
        except (ImportError, OSError) as exc:
            # OSError is the PortAudio case and it happens at IMPORT time, which is why
            # this is not a `try: import` at module scope.
            return f"không mở được micro ({type(exc).__name__}: {exc})"
        try:
            stream = sounddevice.RawInputStream(
                samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16",
                blocksize=CHUNK_FRAMES, device=self.device)
            stream.start()
        except Exception as exc:
            return f"không mở được micro ({type(exc).__name__}: {exc})"
        self._stream = stream
        return None

    def read(self) -> "tuple[bytes | None, str | None]":
        """One chunk. SYNC on purpose — this is the body handed to a thread, and keeping
        it sync is what makes handing it over safe."""
        if self._error is not None:
            return None, self._error
        err = self._open()
        if err:
            self._error = err
            return None, err
        try:
            data, _overflowed = self._stream.read(CHUNK_FRAMES)
        except Exception as exc:
            return None, f"đọc micro lỗi ({type(exc).__name__}: {exc})"
        return bytes(data), None

    def close(self) -> None:
        if self._stream is not None and self._owned:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
        self._stream = None


class GeminiTranscriber:
    """Gemini 3.5 Transcribe Live, behind the `Transcriber` seam.

    **Unrun code.** See this module's docstring: no key, no microphone, and Google's
    domains are blocked from the machine this was written on. `tests/audio_probe.py`
    is what turns that around.

    Shape, from the SDK's own types rather than from prose:

        client.aio.live.connect(model=LIVE_MODEL, config=LiveConnectConfig(
            response_modalities=["TEXT"],
            input_audio_transcription=AudioTranscriptionConfig(...)))

    A background task drains `session.receive()` into `_heard`; `poll()` empties that
    list. The drain has to be a task because `receive()` is an async iterator that
    yields when the server feels like it, and `poll()` must return immediately.

    `custom_vocabulary` is the hook worth using: pass the names the camera already
    knows (`IdentityLedger.names()`), because Vietnamese proper nouns are the thing an
    ASR gets wrong most and the agent knows in advance who is in the room. Note what
    that means for confidentiality — those names leave the machine.
    """

    def __init__(self, *, api_key: str | None = None, model: str = LIVE_MODEL,
                 language_codes: Sequence[str] = (),
                 custom_vocabulary: Sequence[str] = (),
                 smart: bool = False, diarization: bool = False,
                 client: Any = None) -> None:
        if smart and diarization:
            # Straight out of the SDK's own field description. Refused here rather than
            # left for the server to reject halfway through a conversation.
            raise ValueError(
                "mode=SMART không đi cùng diarization được (SDK: 'Timestamps and "
                "diarization are incompatible with mode SMART') — chọn một")
        self.api_key, self.model = api_key, model
        self.language_codes = tuple(language_codes)
        self.custom_vocabulary = tuple(custom_vocabulary)
        self.smart, self.diarization = smart, diarization
        self._client = client
        self._session: Any = None
        self._cm: Any = None
        self._drain: asyncio.Task[None] | None = None
        self._heard: list[Utterance] = []
        self._closed = False

    def _config(self) -> Any:
        from google.genai import types
        audio = types.AudioTranscriptionConfig(
            language_codes=list(self.language_codes) or [],
            custom_vocabulary=list(self.custom_vocabulary) or None,
            diarization=self.diarization or None,
            mode=(types.AudioTranscriptionConfigMode.SMART if self.smart
                  else types.AudioTranscriptionConfigMode.VERBATIM))
        # `Modality.TEXT`, not the string "TEXT": the SDK types this field as an
        # enum, and the string form only survives because pydantic coerces it.
        return types.LiveConnectConfig(response_modalities=[types.Modality.TEXT],
                                       input_audio_transcription=audio)

    async def open(self) -> None:
        if self._session is not None or self._closed:
            return
        if self._client is None:
            from google import genai
            self._client = (genai.Client(api_key=self.api_key) if self.api_key
                            else genai.Client())
        self._cm = self._client.aio.live.connect(model=self.model, config=self._config())
        self._session = await self._cm.__aenter__()
        self._drain = asyncio.create_task(self._drain_forever())

    async def _drain_forever(self) -> None:
        """Turn the server's push stream into a list `poll()` can empty.

        Every exception lands in `_heard` as an `Utterance` carrying `error`, because the
        sensor above must be able to say "tôi không nghe được" rather than die. A dropped
        socket is normal operation here: the service caps a session at ten minutes.
        """
        try:
            async for response in self._session.receive():
                content = getattr(response, "server_content", None)
                if content is None:
                    continue
                for attr, final in (("interim_input_transcription", False),
                                    ("input_transcription", True)):
                    t = getattr(content, attr, None)
                    if t is None or not getattr(t, "text", None):
                        continue
                    self._heard.append(Utterance(
                        text=t.text, final=final,
                        language=getattr(t, "language_code", None),
                        speaker=getattr(t, "speaker_label", None)))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._heard.append(
                Utterance(error=f"mất kết nối phiên nghe ({type(exc).__name__}: {exc})"))

    async def push(self, pcm: bytes) -> None:
        if self._session is None or self._closed:
            return
        from google.genai import types
        try:
            await self._session.send_realtime_input(
                audio=types.Blob(data=pcm, mime_type=MIME_TYPE))
        except Exception as exc:
            self._heard.append(
                Utterance(error=f"gửi tiếng lên lỗi ({type(exc).__name__}: {exc})"))

    async def poll(self) -> tuple[Utterance, ...]:
        heard, self._heard = tuple(self._heard), []
        return heard

    def close(self) -> None:
        """Sync, because `Sensor.close()` is sync. Cancels the drain and drops the
        session; the socket's own teardown happens when the task unwinds. A caller who
        needs the close awaited should cancel the task themselves — this is the honest
        limit of a sync `close()` over an async resource, not an oversight."""
        self._closed = True
        if self._drain is not None:
            self._drain.cancel()
        self._drain = self._session = self._cm = None


# ── the demo: no microphone, no key, no network ─────────────────────────────────


def _demo() -> None:
    async def main() -> None:
        print("=" * 74)
        print("1. Layer 1 — interim là STATE, final là EVENT")
        print("=" * 74)
        buffer = UtteranceBuffer()
        for u in (Utterance("chào", False), Utterance("chào anh", False),
                  Utterance("Chào anh Thiệp.", True, language="vi-VN")):
            buffer.publish(u)
            print(f"  {'final ' if u.final else 'interim'}  {u.text!r:28} "
                  f"-> interim={buffer.interim.text if buffer.interim else None!r} "
                  f"last_final={buffer.last_final.text if buffer.last_final else None!r}")

        print()
        print("=" * 74)
        print("2. Layer 2 — Transcriber seam, trên FakeTranscriber")
        print("=" * 74)
        fake = FakeTranscriber(script=[(Utterance("hôm", False),),
                                       (Utterance("hôm nay", False),
                                        Utterance("Hôm nay trời đẹp.", True))])
        await fake.open()
        await fake.push(b"\x00" * CHUNK_BYTES)
        for n in (1, 2, 3):
            print(f"  poll {n} -> {[(u.text, u.final) for u in await fake.poll()]}")
        print(f"  đã đẩy lên {len(fake.pushed)} chunk, mỗi chunk {CHUNK_BYTES} byte "
              f"= {CHUNK_MS} ms")

        print()
        print("=" * 74)
        print("3. Không có micro — đo thật, không phải traceback")
        print("=" * 74)
        pcm, err = Microphone().read()
        print(f"  Microphone().read() -> pcm={pcm!r}, error={err!r}")

        print()
        print("=" * 74)
        print("4. SMART + diarization bị TỪ CHỐI ngay lúc dựng")
        print("=" * 74)
        try:
            GeminiTranscriber(smart=True, diarization=True)
        except ValueError as exc:
            print(f"  ValueError: {exc}")
        print("  -> nguồn: mô tả field `mode` trong google/genai/types.py, không phải blog")

    asyncio.run(main())


if __name__ == "__main__":
    _demo()
