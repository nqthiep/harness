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
lethal-trifecta refusal, `ToolSet`'s duplicate-name guard). A profile that adds tools
under names it has used before will hit that last guard on a second `with_profile()`
call with the same profile — there is no separate "already applied" bookkeeping here
because that mechanism already exists and already fires.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .agent import Agent

__all__ = ["Profile"]


class Profile(Protocol):
    #: Shown in `ProfileLoosenedSafetyError` when this profile's `apply()` tries to
    #: loosen a safety knob the caller already set — the diagnostic names WHICH profile,
    #: not just that something went wrong.
    name: str

    def apply(self, agent: "Agent") -> "Agent":
        """Return a new `Agent` (frozen — `with_()` builds it) with this profile's
        prompt section, tools, model choice and policies layered on. `agent.job` is
        this run's own task or mission; a profile that owns a prompt template should
        fold it in as a section rather than discard it (`CODING_AGENT_BLUEPRINT.md`'s
        note on `job=` being the WHOLE system prompt applies here unchanged)."""
        ...
