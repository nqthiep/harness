"""Coarse-to-fine attention: a cheap glance decides which expensive stage is worth running.

Shipped rather than copy-pasted, under the rule in `harness.contrib.__init__`: the tuning
numbers are judgment and belong in `examples/`, but the three rules below are the kind of
thing a fork gets subtly wrong and nobody notices, because getting them wrong makes a
sensor CHEAPER — which looks like success.

    1. A stage that did not run CARRIES FORWARD its last measured value, never empty. No
       measurement is not "nothing there". This is `examples/vision_sensor`'s blindness
       rule (ADR-081) one level down, and it is the caller's job: this module decides
       WHETHER to look, and the caller must not turn "did not look" into "saw nothing".
    2. The FIRST glance runs everything. A stage that has never run has no measured value,
       so "never measured" and "measured, and there was nothing" are indistinguishable
       downstream. Enforced here, in `_decide`.
    3. Staleness is bounded. A carried value held indefinitely is a lie with a long fuse,
       so every stage has an `every` — a maximum number of glances it may be carried
       before it is re-measured whatever the triggers say. Enforced here, in `Look.wants`.

**Domain-neutral on purpose, and that was tested rather than assumed.** It was written
for a camera (`examples/vision_gaze.py`), and building a second sense on it —
`examples/voice_sensor.py` — is what found the coupling: `Gaze.detail` iterated a
module-level `STAGES` tuple naming the camera's stages, so any other sense silently
decided nothing. The stages now come from the caller's own table, which is the only place
that knows what they are (ADR-122).

The measurement that motivates the whole thing, from the camera (ADR-121): running every
stage on every glance cost 69.88 ms against a frame difference of 0.05 ms that can tell
you nothing happened — 1400x. Voice has the same shape, with different numbers.

    from harness.contrib.attention import Gaze, Look
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping


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


@dataclass(frozen=True)
class Focus:
    """What this glance decided to look at, and why — the `reason` is for the audit trail
    and the demo, never for a downstream decision."""
    stages: frozenset[str] = frozenset()
    #: Whatever tier 0 measured. Units are the caller's; `Gaze.threshold` is compared
    #: against it and nothing else interprets it.
    signal: float = 0.0
    reason: str = ""

    def __contains__(self, stage: object) -> bool:
        return stage in self.stages

    @property
    def cheap(self) -> bool:
        """Nothing beyond tier 0 ran. The state the loop should spend most of its time in."""
        return not self.stages


@dataclass
class Gaze:
    """The cascade's policy and its staleness counters.

    Mutable, unlike almost everything else here, because the counters ARE the state that
    makes "carried forward for N glances" answerable. `Focus` and `Look` stay frozen.
    """
    #: The expensive stages and when each is worth running. Required: a `Gaze` with an
    #: empty table decides nothing, which is a silent no-op rather than an error, so the
    #: caller has to say.
    looks: Mapping[str, Look] = field(default_factory=dict)
    #: The one cheap stage that runs on EVERY glance, and whose result the rest key off.
    #: Named by the caller — "faces" for a camera, "level" for a microphone — because
    #: nothing here can know what the cheap first look at a signal is called.
    tier_one: str = "locate"
    #: Above this, `signal` counts as "something happened". Its units are the caller's:
    #: mean absolute frame difference for a camera, RMS for a microphone.
    threshold: float = 0.0
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
        unknown = set(stages) - set(self.stages)
        if unknown:
            raise ValueError(f"không có tầng nào tên {sorted(unknown)}; "
                             f"chọn trong {sorted(self.stages)}")
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

    @property
    def stages(self) -> tuple[str, ...]:
        """The stages this `Gaze` decides about — the keys of its own table.

        It used to be a module-level constant naming a camera's stages. That worked for
        exactly one sense: `detail()` iterated it, so a `Gaze` built with a voice table
        looped over `("bodies", "hands", "identity", "scene")`, found none of them in its
        own `looks`, and decided nothing at all — every voice stage skipped, forever, and
        the sensor looked wonderfully cheap. Found by building the second sense (ADR-122),
        not by reading the code.
        """
        return tuple(self.looks)

    def _mark(self, stage: str, ran: bool) -> None:
        if ran:
            self._stale[stage] = 0
            self.looks_taken[stage] = self.looks_taken.get(stage, 0) + 1
        else:
            self._stale[stage] = self._stale.get(stage, 0) + 1

    def locate(self, signal: float) -> Focus:
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
        self._mark(self.tier_one, True)
        loud = signal >= self.threshold
        return Focus(stages=frozenset({self.tier_one}), signal=signal,
                     reason="có biến động" if loud else "yên tĩnh")

    def detail(self, focus: Focus, *, changed: bool, unresolved: bool) -> Focus:
        """Tier 2: decide the expensive stages, now that tier 1 has reported.

        `changed` is "the set of visible faces is not what it was"; `unresolved` is "at
        least one face present has no name". Both are computed by the caller from the
        tier-1 result, because deciding them here would mean this module knowing about
        `IdentityLedger`, which is the layering `vision_tools` exists to keep.
        """
        loud = focus.signal >= self.threshold
        chosen = set(focus.stages)
        for stage in self.stages:
            run = self._decide(stage, motion=loud, changed=changed,
                               unresolved=unresolved)
            self._mark(stage, run)
            if run:
                chosen.add(stage)
        self.demanded.clear()       # a demand buys ONE glance
        extra = sorted(chosen - {self.tier_one})
        why = focus.reason + (f"; nhìn kỹ: {', '.join(extra)}" if extra else "")
        return replace(focus, stages=frozenset(chosen), reason=why)

    def report(self) -> str:
        """One line of "what did attention actually cost", for the demo and the logs."""
        if not self.glances:
            return "chưa liếc lần nào"
        parts = ", ".join(f"{s}={self.looks_taken.get(s, 0)}"
                          for s in (self.tier_one,) + self.stages)
        return f"{self.glances} lần liếc; số lần chạy: {parts}"
