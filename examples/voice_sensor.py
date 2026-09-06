"""A microphone as a `Sensor`: the same two-rate, coarse-to-fine shape as the camera.

**This file exists to test a claim.** ADR-120 and ADR-121 built a perception design around
a camera and asserted it was general. Nothing had tested that. This is the second sense,
built to find out — and what it found is in ADR-122: one real coupling in the shipped
mechanism (`Gaze.detail` iterated a camera-specific stage list, so a voice `Gaze` decided
NOTHING and looked wonderfully cheap), and one thing that did not generalise at all and
should not have (see `_State` below).

**What was reused, unchanged:** `harness.contrib.attention` (`Look`, `Focus`, `Gaze` — the
trigger table, the first-glance rule, the staleness bound), `harness.contrib.driver`
(`Sensor`, `Event`, `Priority`, rule 1), and the whole shape: tier 0 cheap always, tier 1
cheap always, tier 2 on demand; a quantised `_State`; per-field debounce; a `Salience`
table keyed on structure.

**What is genuinely new, and had to be:** the tiers themselves. For a camera the cheap
question is "did the picture change" and the expensive one is "what is in it". For a
microphone the cheap question is "is there sound" and the expensive one is "what was
said". Those are different measurements of different things; sharing them would have been
a fake abstraction.

**Rule 1, restated for audio, because it is sharper here.** A microphone is
`effect="external"` and its content is trivially attacker-controlled: anyone within
earshot can SAY "URGENT, cancel everything". So priority comes from the TABLE, keyed on
the structure of the change — speech started, the speaker changed, it got louder — and
never from the transcript. `Salience.speech` is one tier for every sentence. An operator
who wants a wake word writes `promote=`, which is their code, reading a `Change`.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, fields
from dataclasses import replace as _replace
from typing import Any, Callable

from harness.contrib.attention import Focus, Gaze, Look
from harness.contrib.driver import Event, Priority

from voice_tools import (DEFAULT_SILENCE_FLOOR, Hearing, Listener, Microphone,
                         Utterance, describe, loudness_band, louder_than, speech_of)

#: Tier 1: "is there sound at all". The microphone's `detect_faces` — cheap, unconditional,
#: and the thing every other stage keys off.
SOUND = "sound"

#: Tier 2, the expensive stages.
SPEECH, TRANSCRIPT, SPEAKER = "speech", "transcript", "speaker"
STAGES: tuple[str, ...] = (SPEECH, TRANSCRIPT, SPEAKER)

#: The tuning, and it is TUNING — judgment meant to be edited, which is why it lives in
#: `examples/` and not beside the mechanism. `every` is in glances; at
#: `Driver.sensor_interval_s = 0.2` that makes `every=50` about ten seconds.
DEFAULT_LOOKS = {
    # A VAD pass. Cheap-ish, and only interesting when there is sound to classify.
    SPEECH:     Look(on_motion=True, on_change=False, every=25),
    # The expensive one. Transcription is the single most costly thing this sensor can
    # do, and words only exist while somebody is speaking.
    TRANSCRIPT: Look(on_motion=True, on_change=True, every=50),
    # Who is talking. Like vision's `identity`: not driven by loudness, driven by there
    # being someone unidentified — re-embedding a voice already recognised buys nothing.
    SPEAKER:    Look(on_motion=False, on_change=True, on_unresolved=True, every=50),
}

#: How many consecutive listens make a change real rather than a cough. Same role and same
#: default as the camera's, and the same trade: raise it for a noisy room, at the cost of
#: that much latency per event.
DEFAULT_STABLE_READS = 2


def voice_gaze(**kw: Any) -> Gaze:
    """A `Gaze` wired for a microphone. The camera has `camera_gaze`; this is the whole
    difference between the two senses, as far as the cascade is concerned — a table, a
    tier-1 name, and what the threshold's units mean."""
    kw.setdefault("looks", dict(DEFAULT_LOOKS))
    kw.setdefault("tier_one", SOUND)
    kw.setdefault("threshold", DEFAULT_SILENCE_FLOOR)
    return Gaze(**kw)


@dataclass(frozen=True)
class _State:
    """A committed listen, reduced to what a `Change` is computed from.

    **This is the piece that did NOT generalise, and deliberately so.** The camera's
    `_State` is about who is PRESENT — a set that persists between observations. Audio has
    no such thing: speech is an event with a beginning and an end, and there is no "still
    saying it" the way there is a "still standing there". So this state is about whether
    somebody is speaking, who, and how loudly — and `Change` reports a sentence ONCE, at
    the moment it is heard, rather than diffing a set. Forcing audio into the camera's
    presence-set shape would have produced a sensor that re-announced the same sentence on
    every listen until the speaker stopped.

    Quantised on the same rule as vision: `loud` is a band, never the raw RMS, because a
    continuous value here means every block is a change and the two rates collapse into
    one.
    """
    speaking: bool = False
    speakers: frozenset[str] = frozenset()
    unknown_voices: int = 0
    loud: str = ""
    deaf: bool = True          # no working microphone yet; suppresses a fake "went quiet"


_FIELDS: tuple[str, ...] = tuple(f.name for f in fields(_State))


@dataclass(frozen=True)
class Change:
    """What is different since the last committed listen. Structure, never text."""
    started: bool = False
    stopped: bool = False
    said: tuple[Utterance, ...] = ()
    joined: tuple[str, ...] = ()
    left: tuple[str, ...] = ()
    loudness: tuple[str, str] | None = None
    hearing: Hearing | None = None

    @property
    def empty(self) -> bool:
        return not (self.started or self.stopped or self.said or self.joined
                    or self.left or self.loudness)


@dataclass(frozen=True)
class Salience:
    """`Change` → `Priority`, by TABLE. Rule 1 of `harness.contrib.driver`.

    Every row is keyed on the SHAPE of the change, never its content. `speech` is one tier
    for every sentence, so "URGENT, cancel everything" said out loud buys exactly what
    "good morning" buys. That is the entire point: a microphone is `effect="external"` and
    anyone within earshot can say anything.
    """
    #: Somebody started talking. NORMAL — worth putting in front of the model at its next
    #: call, never worth cancelling a turn for.
    started: Priority = Priority.NORMAL
    #: Words were heard. NORMAL, flat across every sentence. See the class docstring.
    speech: Priority = Priority.NORMAL
    #: A new voice, or one that stopped. Departure's tier, as in vision.
    speaker_changed: Priority = Priority.LOW
    #: The room went quiet.
    stopped: Priority = Priority.LOW
    #: Somebody raised or lowered their voice.
    loudness: Priority = Priority.LOW
    promote: Callable[[Change], Priority | None] | None = None

    def of(self, change: Change) -> Priority:
        """The HIGHEST tier among the things that actually changed — `max`, not a
        first-match ladder, for the reason `vision_sensor.Salience.of` gives."""
        if self.promote is not None:
            forced = self.promote(change)
            if forced is not None:
                return forced
        tiers: list[Priority] = []
        if change.started:
            tiers.append(self.started)
        if change.said:
            tiers.append(self.speech)
        if change.joined or change.left:
            tiers.append(self.speaker_changed)
        if change.stopped:
            tiers.append(self.stopped)
        if change.loudness is not None:
            tiers.append(self.loudness)
        return max(tiers) if tiers else self.stopped


def render(change: Change) -> str:
    """The sentence the model reads. Observation, never a conclusion, and never a claim
    the sensor cannot support."""
    parts: list[str] = []
    for who in change.joined:
        parts.append(f"{who} bắt đầu nói")
    if change.started and not change.joined:
        parts.append("có người bắt đầu nói")
    for u in change.said:
        who = u.speaker or "ai đó"
        parts.append(f'{who} nói: "{u.text}"')
    for who in change.left:
        parts.append(f"{who} thôi không nói nữa")
    if change.loudness is not None:
        was, now = change.loudness
        verb = "to hẳn lên" if louder_than(now, was) else "nhỏ đi"
        parts.append(f"giọng {verb} ({was} -> {now})")
    if change.stopped and not parts:
        parts.append("xung quanh im lặng trở lại")
    head = "; ".join(parts) if parts else "có gì đó thay đổi"
    head = head[0].upper() + head[1:] + "."
    # The full description is appended only when it ADDS something. For a camera the
    # change ("Thiep vừa xuất hiện") and the description ("Tôi thấy Thiep, đang ngồi")
    # are complementary; for audio the change IS the sentence, so `describe` repeated it
    # word for word — measured: 'Thiep nói: "chào buổi sáng". Thiep nói: "chào buổi
    # sáng".' Every duplicated token is paid for on every subsequent model call in the
    # run, which is the same reason `Verifier` returns "" when nothing is wrong.
    if change.hearing is None or change.said:
        return head
    return f"{head} {describe(change.hearing)}"


class VoiceSensor:
    """A microphone as a `Sensor`. Structurally the camera's twin; see the module docstring
    for what that cost and what it bought."""

    def __init__(self, *, microphone: Microphone, listener: Listener,
                 salience: Salience | None = None,
                 stable_reads: int = DEFAULT_STABLE_READS,
                 gaze: Gaze | None = None,
                 known_voices: dict[str, tuple[float, ...]] | None = None,
                 use_thread: bool = True) -> None:
        self.microphone, self.listener = microphone, listener
        self.salience = salience if salience is not None else Salience()
        self.stable_reads = max(1, stable_reads)
        self.gaze = gaze if gaze is not None else voice_gaze()
        self.known_voices = dict(known_voices or {})
        self.focus = Focus()
        self.use_thread = use_thread
        self._held = Hearing(at=0.0)
        self._speaker: str | None = None
        self._committed = _State()
        self._pending: dict[str, tuple[object, int]] = {}
        self.observations = 0
        self.suppressed = 0

    # -- acquisition -------------------------------------------------------------------

    def _listen(self) -> Hearing:
        """Glance, decide, then listen closely. Sync, so it is safe to hand to a thread."""
        block, err = self.microphone.grab()
        if err:
            return Hearing(at=time.time(), error=err)
        held = self._held
        try:
            level = self.listener.level(block)
            focus = self.gaze.locate(level)
            # `changed` for audio is "the speaking/not-speaking state flipped", which is
            # the audio analogue of "the set of faces changed" and is knowable from tier 1
            # alone.
            rough = speech_of(level, self.gaze.threshold)
            changed = rough != held.speech
            unresolved = any(u.speaker is None for u in held.utterances)
            focus = self.gaze.detail(focus, changed=changed, unresolved=unresolved)

            # When the VAD did not run, fall back to tier 1's own coarse answer rather
            # than to the last VAD result. Carrying the stale one produced a state that
            # never existed: `speaking` from three seconds ago combined with `level` from
            # now, so a room going quiet was reported as "giọng nhỏ đi (nói to -> thì
            # thầm)" instead of "im lặng trở lại" — measured. The right carry-forward for
            # a field is sometimes a cheaper FRESH estimate, not the stale precise one,
            # and that is only true where tier 1 measures the same question more coarsely.
            speech = (self.listener.detect_speech(block) if SPEECH in focus else rough)
            utterances = (tuple(self.listener.transcribe(block)) if TRANSCRIPT in focus
                          else held.utterances)
            if not rough:
                self._speaker = None        # a speech run ended; the name expires with it
            if SPEAKER in focus and utterances:
                self._speaker = self._name_for(self.listener.embed_voice(block))
            # Carry the identified speaker across a run, so a fresh transcript does not
            # arrive anonymous just because identity was not re-run for it. Without this
            # the sensor said "ai đó nói" about a person it had recognised one listen
            # earlier — the same defect as vision's "a man sitting still becomes a
            # stranger", found again in the second sense despite being documented in the
            # first.
            #
            # It assumes ONE speaker per unbroken run, which is what `SPEAKER`'s
            # `on_change=True` already assumed; a second person interjecting mid-run is
            # misattributed until `Look.every` forces a re-identification (~10 s at
            # `sensor_interval_s=0.2`). Real diarisation is a `Listener`, not this.
            if self._speaker is not None:
                utterances = tuple(_replace(u, speaker=u.speaker or self._speaker)
                                   for u in utterances)
        except Exception as exc:
            return Hearing(at=time.time(),
                           error=f"nghe lỗi: {type(exc).__name__}: {exc}")
        self.focus = focus
        return Hearing(at=time.time(), level=level, speech=speech,
                       utterances=utterances)

    def _name_for(self, vector: tuple[float, ...]) -> str | None:
        """Nearest enrolled voice, or `None`.

        Cosine over whatever `embed_voice` returns, with NO threshold defended by
        measurement — `vision_tools.IdentityLedger` earned its `DEFAULT_THRESHOLD` from
        measured same/different overlap (ADR-090, ADR-095) and nothing comparable has been
        measured for voices here. So this is an exact-match lookup, which is honest, and a
        real implementation replaces it along with the `Listener` that produces vectors.
        """
        if not vector:
            return None
        for name, known in self.known_voices.items():
            if known == vector:
                return name
        return None

    async def _observe(self) -> Hearing:
        hearing = (await asyncio.to_thread(self._listen) if self.use_thread
                   else self._listen())
        if hearing.ok:
            self._held = hearing
        return hearing

    # -- change detection --------------------------------------------------------------

    def _state_of(self, hearing: Hearing) -> _State:
        if not hearing.ok:
            return _State(deaf=True)
        named = frozenset(u.speaker for u in hearing.utterances
                          if u.heard and u.speaker)
        unknown = sum(1 for u in hearing.utterances if u.heard and not u.speaker)
        return _State(speaking=hearing.speech, speakers=named, unknown_voices=unknown,
                      loud=loudness_band(hearing.level) if hearing.speech else "",
                      deaf=False)

    def _settle(self, state: _State) -> _State:
        """Debounce each field on its OWN clock — the camera's `_settle`, and the same
        measured reason (ADR-120): one whole-state streak lets any jittery field starve
        every other, and for audio the jittery one is loudness, which moves constantly
        while somebody talks."""
        settled: dict = {}
        for name in _FIELDS:
            seen = getattr(state, name)
            held, streak = self._pending.get(name, (None, 0))
            streak = streak + 1 if held == seen and streak else 1
            self._pending[name] = (seen, streak)
            settled[name] = seen if streak >= self.stable_reads else getattr(
                self._committed, name)
        return _State(**settled)

    def _commit(self, state: _State, hearing: Hearing) -> Change | None:
        if state != self._committed:
            self.suppressed += 1
        state = self._settle(state)
        before, self._committed = self._committed, state
        if before == state:
            return None
        self.suppressed -= 1
        if before.deaf or state.deaf:
            # A broken microphone is "I cannot hear", not "the room went silent", and the
            # first successful listen is a baseline, not everyone starting to talk. Same
            # two cases, same silence, as `vision_sensor`'s `blind`.
            return None
        # Words are reported ONCE, when heard. Not a set difference: a sentence does not
        # persist between listens the way a face does, so diffing would either repeat it
        # every block or lose it entirely.
        said = tuple(u for u in (hearing.utterances if hearing.ok else ())
                     if u.heard) if state.speaking else ()
        return Change(started=state.speaking and not before.speaking,
                      stopped=before.speaking and not state.speaking,
                      said=said,
                      joined=tuple(sorted(state.speakers - before.speakers)),
                      left=tuple(sorted(before.speakers - state.speakers)),
                      loudness=((before.loud, state.loud)
                                if before.loud != state.loud
                                and before.loud and state.loud else None))

    # -- the Sensor protocol -----------------------------------------------------------

    async def read(self) -> Event | None:
        hearing = await self._observe()
        self.observations += 1
        change = self._commit(self._state_of(hearing), hearing)
        if change is None or change.empty:
            return None
        change = _replace(change, hearing=hearing)
        return Event(priority=self.salience.of(change), text=render(change),
                     at=hearing.at, source="micro")

    def close(self) -> None:
        self.microphone.close()
