"""Shipped, reusable, and explicitly NOT covered by the compatibility promise the rest
of `harness` makes.

Two tiers already existed and the boundary between them was in the wrong place. Core
(`src/harness`) is mechanism with a documented contract, an ADR trail and a risk
register. `examples/` is judgment — prompts, thresholds, priority tables — meant to be
COPIED and edited, because a prompt you cannot edit is worthless and a `Verifier` you
cannot tune per repository is worthless.

What did not fit either: domain-neutral mechanism that happened to grow in `examples/`.
Measured before this package existed — `examples/` held 6,780 lines with no
`__init__.py`, seven real cross-imports between its own files, and 136 `sys.path.insert`
calls across 100 files. Reuse was already happening; it was happening through a path
hack.

**The line drawn here: copy-paste what you want people to EDIT; ship what you do not
want them to RE-DERIVE.** `driver.py` carries four safety rules about preemption
(never cancel mid-write, only preempt an agent with a durable plan, priority from code
not from text, cap the preemptions). Distributing safety code by copy-paste means every
fork drifts silently, and this project has already paid for that twice: `coding_profile.py`
claimed single-file portability that had stopped being true (ADR-076), and its subagent
ran at the wrong `safety` level from its first commit until a conformance test found it
(ADR-078). Prompts should be forked. Interrupt handling should not.

**What "not covered by the compatibility promise" means, concretely.** Anything here can
change shape in a patch release. It gets the same `ruff`, `mypy` and test suite core
gets — that is the whole point of shipping it — but none of core's stability guarantees,
no entry in the six-seam table, and no re-export from `harness` itself. Importing it
says so out loud:

    from harness.contrib.driver import Driver, Priority
    from harness.contrib.sensors import ClockSensor, FileSensor
    from harness.contrib.output_shaping import with_smart_truncation

Never `from harness import Driver`. The longer path is the disclaimer.

**Admission criteria**, so this does not become a junk drawer. All four:

1. Domain-neutral — no opinion about coding, cameras, or any other subject matter.
2. No new dependency. Core's dependency budget (NFR-05) is real and `pyproject.toml`
   records it; OpenCV and MediaPipe are exactly why `vision_tools.py` stays in
   `examples/` no matter how reusable it looks.
3. More than one real consumer, or one consumer plus a safety argument for a single
   tested copy.
4. Not a seam. A seam belongs in the six-seam table and must pass
   `docs/02-architecture.md` §4's three-part plugin test. `Sensor` now passes part (c) —
   three real implementations, `examples/vision_sensor.CameraSensor` plus `FileSensor`
   and `ClockSensor` in `sensors.py` — and still is not promoted, for a reason that
   turned out to be sturdier than the head count: **core does not consume it.** A seam is
   a protocol the core is written against (`ModelProvider`, `Store`, `Policy`, …);
   `Sensor` is consumed by `Driver`, which is itself `contrib`. Part (b) of the plugin
   test — "the core can be written with zero knowledge of any concrete implementation" —
   is not merely satisfied here, it is inapplicable, and that is what settles the
   question (ADR-089, and ADR-081/082 for the earlier head-count argument).
"""
