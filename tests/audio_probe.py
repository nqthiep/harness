"""The audio probe — a manual script, never collected by the suite.

`audio_tools.GeminiTranscriber` was written against the installed `google-genai` SDK's
type definitions and has never executed a single call: there is no API key on the machine
it was written on, no microphone, and that machine's egress proxy blocks every Google
domain. Its own docstring says to treat it as unrun code (ADR-077 is where this project
last wrote that sentence about a different class). This script is what changes that
(ADR-090 is what it did last time, for MediaPipe).

What it needs, none of which the test suite may assume:

    a key        GEMINI_API_KEY or GOOGLE_API_KEY in the environment
    network      the Gemini Live API endpoint, over WebSocket
    the SDK      pip install google-genai
    audio        a 16 kHz mono 16-bit PCM or WAV file given on the command line.
                 A microphone is NOT required and deliberately not used: a file is
                 reproducible and a room is not, and the numbers below are only worth
                 anything if a second run can check them.

Run it deliberately:

    PYTHONPATH=src:examples python3 tests/audio_probe.py path/to/speech.wav

**The four questions it exists to answer**, each of which is currently a documented
guess in `audio_tools.py` or `audio_sensor.py`:

1. Does `speaker_label` ever arrive over the LIVE path? The prose documentation says
   diarization is a recorded-audio feature; the SDK's `Transcription` type carries the
   field on both. `audio_sensor.Change.known_speaker` — and therefore the only honest way
   to let one person interrupt the agent — is worthless if the answer is no.
2. Is `custom_vocabulary` accepted on a live session, and does it change anything for
   Vietnamese proper nouns? There is a public report of the documented config being
   rejected by the Interactions API, so it may well be rejected here too.
3. How long after speech does an interim arrive, and how long after silence does the
   final? Everything about pacing in `MicSensor` assumes these are small.
4. Does `mode=SMART` really refuse alongside diarization, the way the SDK's own field
   description says?

Print what it finds and quote the numbers back into the docstrings. Re-run it rather
than trusting those copies if anything here changes.
"""
import asyncio
import os
import sys
import time
import wave
from pathlib import Path

import _paths                           # standalone: `conftest.py` never applies here
sys.path.insert(0, str(_paths.SRC))
sys.path.insert(0, str(_paths.EXAMPLES))

from audio_tools import (CHUNK_BYTES, CHUNK_MS, LIVE_MODEL, SAMPLE_RATE,
                         GeminiTranscriber, Utterance)


def preflight() -> "list[str]":
    """Everything that has to be true, checked up front and named individually — a
    single "it didn't work" is what made the last unrun adapter stay unrun."""
    missing = []
    if not (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")):
        missing.append("GEMINI_API_KEY (or GOOGLE_API_KEY) is not set")
    try:
        import google.genai                                          # noqa: F401
    except ImportError:
        missing.append("google-genai is not installed (pip install google-genai)")
    return missing


def pcm_from(path: Path) -> bytes:
    """16 kHz mono 16-bit PCM bytes. A `.wav` is unwrapped and CHECKED rather than
    assumed — feeding 44.1 kHz stereo at a mime type that claims 16 kHz mono produces a
    transcript of nothing in particular, which is a confusing way to learn about a
    header."""
    if path.suffix.lower() != ".wav":
        return path.read_bytes()
    with wave.open(str(path), "rb") as w:
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        if (rate, channels, width) != (SAMPLE_RATE, 1, 2):
            raise SystemExit(
                f"{path.name} is {rate} Hz, {channels}ch, {width * 8}-bit. The Live API "
                f"wants {SAMPLE_RATE} Hz mono 16-bit.\n"
                f"  Fix: ffmpeg -i {path.name} -ar {SAMPLE_RATE} -ac 1 -sample_fmt s16 out.wav")
        return w.readframes(w.getnframes())


async def run(pcm: bytes, *, vocabulary: "tuple[str, ...]" = (),
              diarization: bool = False) -> "list[tuple[float, Utterance]]":
    """Stream `pcm` at real time and collect everything heard, each stamped with how
    long after the stream started it arrived."""
    t = GeminiTranscriber(custom_vocabulary=vocabulary, diarization=diarization)
    await t.open()
    started = time.monotonic()
    heard: "list[tuple[float, Utterance]]" = []
    try:
        for offset in range(0, len(pcm), CHUNK_BYTES):
            await t.push(pcm[offset:offset + CHUNK_BYTES])
            for u in await t.poll():
                heard.append((time.monotonic() - started, u))
            # Real time, deliberately: the service is a live endpoint and shoving a file
            # at it as fast as the socket takes it measures the socket, not the model.
            await asyncio.sleep(CHUNK_MS / 1000)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            for u in await t.poll():
                heard.append((time.monotonic() - started, u))
            await asyncio.sleep(0.1)
    finally:
        t.close()
    return heard


def report(label: str, heard: "list[tuple[float, Utterance]]") -> None:
    print(f"\n=== {label} ===")
    if not heard:
        print("  nothing came back at all")
        return
    for at, u in heard:
        if not u.ok:
            print(f"  {at:6.2f}s  ERROR  {u.error}")
            continue
        kind = "FINAL  " if u.final else "interim"
        extra = " ".join(x for x in (f"lang={u.language}" if u.language else "",
                                     f"speaker={u.speaker}" if u.speaker else "") if x)
        print(f"  {at:6.2f}s  {kind} {u.text!r} {extra}")

    first = next((at for at, u in heard if u.ok and not u.final), None)
    final = next((at for at, u in heard if u.ok and u.final), None)
    labelled = [u.speaker for _, u in heard if u.ok and u.speaker]
    print(f"  -> first interim at {first if first is not None else 'never'}")
    print(f"  -> first final   at {final if final is not None else 'never'}")
    print(f"  -> speaker_label populated on the LIVE path: "
          f"{'YES ' + str(sorted(set(labelled))) if labelled else 'NO — question 1 answered'}")


def main(path: Path) -> int:
    missing = preflight()
    if missing:
        print("  cannot run:")
        for m in missing:
            print(f"    - {m}")
        return 1

    print(f"  model {LIVE_MODEL}")
    pcm = pcm_from(path)
    print(f"  {path.name}: {len(pcm)} bytes = {len(pcm) / (SAMPLE_RATE * 2):.1f}s of audio")

    print("\n=== question 4: does SMART refuse alongside diarization, as the SDK says? ===")
    try:
        GeminiTranscriber(smart=True, diarization=True)
        print("  NO — it constructed. The SDK's field description no longer holds; fix "
              "GeminiTranscriber.__init__ and audio_tools.py's docstring.")
    except ValueError as exc:
        print(f"  yes, refused locally: {exc}")

    report("plain, automatic language detection", asyncio.run(run(pcm)))
    report("with diarization requested (question 1)",
           asyncio.run(run(pcm, diarization=True)))
    report("with custom_vocabulary (question 2)",
           asyncio.run(run(pcm, vocabulary=("Thiep", "Nghia"))))

    print("\n  Quote these numbers into audio_tools.py and audio_sensor.py, replacing "
          "the guesses they currently admit to.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.split("Run it deliberately:")[1].split("**")[0].strip())
    raise SystemExit(main(Path(sys.argv[1])))
