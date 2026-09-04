"""`Profile` — a named, reusable way to turn one `Agent` into another. `docs/02-
architecture.md §4`'s six-seam table does not grow a seventh row for this: like
`harness.middleware`, a `Profile` is sugar built entirely from the public API that
already exists (`Agent.with_()`, `ToolSpec`, `Policy`) rather than a new thing core has
to know about. What earns it that classification is the same rule `middleware.py`
states for itself — every hook there "can only add restriction or observation, never
bypass" a decision the core loop already made. `Agent.with_profile()` (`agent.py`)
enforces the equivalent rule for a `Profile`: it may extend what an agent can do, but it
may never loosen a safety knob the constructor call already set. `_refuse_if_loosened`
right there checks it mechanically, the same way property P-2 checks a `Policy` can only
ever tighten a `Verdict`.

A `Profile` is not a place for `danger` tools, transport connections, or anything with an
async lifecycle — `Agent.with_profile()` is sync, matching the rest of Level 0-2's API
(`docs/03-public-api.md §2`), and `apply()` runs at construction time alongside every
other check `Agent.__init__` already makes (the cache-determinism linter, the
lethal-trifecta refusal, `ToolSet`'s duplicate-name guard).

`Agent.with_profile()` also refuses a SECOND `.with_profile()` call by default (measured,
not hypothetical — its own docstring has the finding: composing two profiles unions their
tools with no review, which is how "reads the untrusted world" and "writes the codebase"
tools ended up on one agent with nothing catching it). `allow_multiple=True` is the
explicit opt-in past that refusal.

**Verified only through construction on the LangGraph backend (`durable=True`).**
`with_profile()` mechanically works there too — `Agent(**base)` accepts `durable=True`
regardless of which code path called it, and a profile-wrapped `ToolSpec`
(`dataclasses.replace(spec, fn=...)`, the composition `with_verification`/
`with_smart_truncation` both use) constructs without error. What that does NOT confirm:
whether an actual RUN through `harness.lg.build_agent()`'s compiled graph invokes those
wrapped functions identically to the classic backend — that would need a real model call
to observe, and none has happened yet in this codebase's history (OI-11,
`design/07-risks-and-open-issues.md`). Treat `Profile` + `durable=True` as untested past
construction, not as confirmed working, until that run happens.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .agent import Agent

__all__ = ["Profile"]


class Profile(Protocol):
    @property
    def name(self) -> str:
        """Shown in `ProfileLoosenedSafetyError` when this profile's `apply()` tries to
        loosen a safety knob the caller already set, and in the second-profile refusal —
        the diagnostic names WHICH profile, not just that something went wrong.

        A read-only `@property` rather than `name: str`, and the difference is not
        cosmetic: a Protocol member declared as a VARIABLE must be settable, so every
        `@dataclass(frozen=True)` profile — which is the shape `docs/03-public-api.md`
        §3.7 recommends and all three real ones use — failed to satisfy this Protocol
        under mypy:

            Argument 1 to "with_profile" of "Agent" has incompatible type
            "VisionProfile"; expected "Profile"
            note: Protocol member Profile.name expected settable variable, got
            read-only attribute

        Runtime was unaffected (nothing checks this Protocol at runtime and `agent.py`
        only ever READS `profile.name`), so it went unnoticed until `examples/` was
        pointed at the type checker (ADR-082). A frozen dataclass field satisfies a
        read-only property, so this accepts strictly more than before and rejects
        nothing that used to work.
        """
        ...

    def apply(self, agent: "Agent") -> "Agent":
        """Return a new `Agent` (frozen — `with_()` builds it) with this profile's
        prompt section, tools, model choice and policies layered on. `agent.job` is
        this run's own task or mission; a profile that owns a prompt template should
        fold it in as a section rather than discard it (`CODING_AGENT_BLUEPRINT.md`'s
        note on `job=` being the WHOLE system prompt applies here unchanged)."""
        ...
