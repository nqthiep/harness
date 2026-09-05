"""Construction-time guards — the rules that decide whether an `Agent` may exist at all.

They lived in `agent.py`, and that made `harness.lg` import the facade to reach them
(`from ..agent import _check_subagent_safety, _check_tool_set`) while `agent.py` deferred
its own import of `lg` into a function body to dodge the resulting cycle. Measured: it was
the **only** runtime import cycle left in core once `TYPE_CHECKING` edges are discounted,
and it existed because the safety engine was living inside the thing it guards (ADR-098).

Both engines now import this downward. Nothing here imports `Agent` at runtime — every
function reads its arguments by attribute, so a duck-typed stand-in works and
`tests/test_layering.py` can assert the direction.

Why these four and not the rest of `agent.py`'s free functions: these are the ones that
decide REFUSAL. `_output_format`, `_guard_sync`, `_policy_state` and friends are plumbing
for one facade; a second backend has no use for them.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .errors import ProfileLoosenedSafetyError, UnsafeToolSetError
from .policy.label import Grants
from .tools import EFFECT_PROFILES, Effect, ToolSpec
from .tools.registry import ToolSet

if TYPE_CHECKING:
    from .agent import Agent


_SAFETY_RANK = {"standard": 0, "strict": 1}


def _check_subagent_safety(toolset: ToolSet, parent_safety: str) -> None:
    """A subagent inherits restriction only: it may never be laxer than its parent.

    Documented in §06.4 since Round 7 and unenforced until Round 28 executed it — a
    strict parent could delegate to a standard child and silently drop the safety level
    for exactly the work it delegated.
    """
    for spec in toolset:
        child = spec.subagent
        if child is None:
            continue
        if _SAFETY_RANK[child.safety] < _SAFETY_RANK[parent_safety]:
            raise UnsafeToolSetError(
                f"{child.name!r} runs at safety={child.safety!r} but you are wrapping it "
                f"in an agent at safety={parent_safety!r}.\n\n"
                f"  A subagent can only ever be MORE restricted than its parent, never "
                f"less —\n  otherwise delegating work is a way to escape the safety "
                f"level you chose.\n\n"
                f'  Fix: Agent(name={child.name!r}, ..., safety="{parent_safety}")\n\n'
                f"  -> docs/06-safety.md#4-least-privilege"
            )


def _effect_loosenings(before: ToolSpec, after: ToolSpec, safety: str) -> list[str]:
    """Which of the behaviours `effect` DECIDES got less restrictive, for one tool name.

    A tool's `effect` is not a label — it is the whole of what `EFFECT_PROFILES`
    (`tools/__init__.py`, ADR-003) derives: whether the tool may run in parallel, be
    retried, whether its result arms the taint checks, what confidentiality may flow
    into it, and whether it needs an approval. So re-declaring an existing tool NAME
    under a different effect silently rewrites five rules at once, and `with_(tools=)`
    REPLACES the tool list rather than unioning it — the same shape of hole
    `dropped_sensitive` closed one field over (ADR-079).

    Measured, not hypothetical. An agent with `deploy` at `effect="danger"` and an
    `approve=` callback asked **1** time before a profile and **0** times after one
    that passed a same-named tool declared `effect="read"`; `with_profile` raised
    nothing. Separately, a `fetch` re-declared from `external` to `read` went from
    `Label(UNTRUSTED, PUBLIC)` to `Label(TRUSTED, PUBLIC)` — the untrusted mark that
    is the entire input to `TaintPolicy`, gone (ADR-084).

    Compared field by field rather than by ranking the four effects, because the four
    do not form a chain: `read` is the most permissive on approval yet accepts SECRET
    inflow, where `write` asks under `strict` yet is a PUBLIC-only sink. Any rank would
    have to pick one dimension and lose the others.
    """
    b, a = EFFECT_PROFILES[before.effect], EFFECT_PROFILES[after.effect]
    out: list[str] = []
    if not b.parallel_safe and a.parallel_safe:
        out.append("it may now run in parallel with other tools")
    if not b.retryable and a.retryable:
        out.append("it may now be retried after a failure, which double-applies "
                   "anything it does not do idempotently")
    if b.emits.integrity > a.emits.integrity:
        out.append("its result is no longer marked untrusted, so it stops arming "
                   "`TaintPolicy` for the rest of the run")
    if a.max_confidentiality > b.max_confidentiality:
        out.append(f"data up to {a.max_confidentiality.name} may now flow INTO it "
                   f"(was {b.max_confidentiality.name}-only)")
    # At the level this agent actually runs at. `after.safety` is never below
    # `before.safety` — the check above this one refuses that — so reading it here is
    # reading the stricter of the two.
    field = "decision_strict" if safety == "strict" else "decision_standard"
    bv, av = getattr(b, field), getattr(a, field)
    if av < bv:
        out.append(f"the approval gate went {bv.name} -> {av.name} at safety={safety!r}")
    return out


def _refuse_if_loosened(before: "Agent", after: "Agent", profile_name: str) -> None:
    """`Agent.with_profile()`'s enforcement half — checked BEFORE `after` is handed
    back to the caller, so a profile that loosens a safety knob never produces a live
    agent, the same "caught at construction, not mid-run" discipline `_check_tool_set`
    already applies to the lethal-trifecta combination.

    Deliberately narrow: only knobs where "did this get LESS restrictive" is decidable
    by comparing two values, not by judging intent. Swapping `approve=` for a
    DIFFERENT callback is not checked — whether the new one is stricter is a question
    about what that callback does, the same trust boundary as passing `approve=`
    directly to `Agent(...)`; only the mechanical case of DROPPING the gate entirely
    (a real callback replaced by `None`) is decidable and checked here.
    """
    culprits: list[str] = []

    if _SAFETY_RANK[after.safety] < _SAFETY_RANK[before.safety]:
        culprits.append(f"safety: {before.safety!r} -> {after.safety!r}")

    added_tainted = after._grants.accepts_tainted - before._grants.accepts_tainted
    if added_tainted:
        culprits.append(f"accepts_tainted gained {sorted(added_tainted)!r}")

    # `sensitive` is a safety knob in the same sense the rest of this list is: it is
    # what `emits_of` (policy/builtin.py) reads to raise a tool's result to
    # `Confidentiality.SECRET`, which is what makes `check_flow` DENY every PUBLIC-max
    # sink for the rest of the run. LOSING an entry is therefore the unsafe direction —
    # gaining one only tightens, so it is not checked.
    #
    # This is silent without the check, because `with_()` REPLACES rather than unions:
    # `base["sensitive"] = self._grants.sensitive` is overwritten wholesale by an
    # `overrides` entry, so a profile passing `sensitive=[...]` that omits a name the
    # caller declared simply drops it. Measured on an agent declaring a camera tool
    # `sensitive`: after a profile passing `sensitive=[]`, `emits_of` went from
    # `Label(UNTRUSTED, SECRET)` to `Label(UNTRUSTED, PUBLIC)` with nothing raised —
    # every write and fetch sink re-opened to camera content. Found while writing down
    # the profile conventions this rule belongs to (ADR-079), and material now that
    # `examples/vision_profile.py`'s `private=True` rests its entire guarantee on it.
    dropped_sensitive = before._grants.sensitive - after._grants.sensitive
    if dropped_sensitive:
        culprits.append(f"sensitive lost {sorted(dropped_sensitive)!r} (the "
                        f"confidentiality label that keeps their results out of "
                        f"PUBLIC sinks)")

    if before.allowed_hosts is not None:
        if after.allowed_hosts is None:
            culprits.append("allowed_hosts: a host allowlist -> unrestricted (None)")
        elif set(after.allowed_hosts) - set(before.allowed_hosts):
            new = sorted(set(after.allowed_hosts) - set(before.allowed_hosts))
            culprits.append(f"allowed_hosts gained {new!r}")

    if before.require_approval_evidence and not after.require_approval_evidence:
        culprits.append("require_approval_evidence: True -> False")

    if after.max_asks_per_run > before.max_asks_per_run:
        culprits.append(f"max_asks_per_run: {before.max_asks_per_run} -> "
                        f"{after.max_asks_per_run} (the approval-fatigue cap, S-25)")

    if before.approve is not None and after.approve is None:
        culprits.append("approve: a callback -> None (no approval gate at all)")

    dropped_policies = [p for p in before.policies if p not in after.policies]
    if dropped_policies:
        names = [getattr(p, "__name__", None) or type(p).__name__ for p in dropped_policies]
        culprits.append(f"policies dropped: {names!r}")

    # Tools, by NAME. `with_()` replaces the list wholesale, so a profile that rebuilds
    # it — `[t for t in agent.toolset if t.name != "deploy"] + [my_deploy]`, or simply a
    # fresh list — can re-declare a name the caller already declared, under a different
    # effect. The additive pattern the conventions ask for (`[*agent.toolset, *mine]`)
    # cannot reach this: `ToolSet.__init__` raises `DuplicateToolError` on a collision
    # inside one list. What is NOT checked is a same-name, same-effect swap whose
    # function body does something else entirely — that is not decidable by comparing
    # two values, and it is the same trust boundary as handing a profile your `approve=`
    # callback (see this function's docstring).
    for after_spec in after.toolset:
        before_spec = before.toolset.get(after_spec.name)
        if before_spec is None or before_spec.effect is after_spec.effect:
            continue
        why = _effect_loosenings(before_spec, after_spec, after.safety)
        if why:
            culprits.append(
                f"tool {after_spec.name!r} re-declared {before_spec.effect.value!r} -> "
                f"{after_spec.effect.value!r}: " + "; ".join(why))

    if not culprits:
        return
    raise ProfileLoosenedSafetyError(
        f"the profile {profile_name!r} would make this agent LESS safe than you already "
        f"configured it to be.\n\n"
        + "\n".join(f"  - {c}" for c in culprits) +
        "\n\n"
        "  A profile may add tools, prompt content, or policies — it may never loosen "
        "a\n  safety setting the constructor call already made. If this profile's "
        "author intends\n  it to run at a different safety level, that is a choice "
        "you make explicitly at\n  `Agent(...)`, not one a profile makes for you "
        "silently.\n\n"
        "  -> docs/06-safety.md#4-least-privilege"
    )


def _check_tool_set(toolset: ToolSet, grants: Grants) -> None:
    """The external+danger combination, caught at construction rather than mid-run (F9.1).

    Kiểm theo TÊN qua `grants`, không theo một trường trên `ToolSpec` — `accepts_tainted`
    không còn là thứ tác giả tool khai được (design/03-tools-and-mcp.md §1.1bis, S-16).
    """
    external = [t for t in toolset if t.effect is Effect.EXTERNAL]
    danger = [t for t in toolset if t.effect is Effect.DANGER
             and t.name not in grants.accepts_tainted]
    if not (external and danger):
        return
    e, d = external[0].name, danger[0].name
    raise UnsafeToolSetError(
        "This helper can read things from the internet AND do something it can't undo.\n\n"
        f"  {e:<11} can bring in words from a website\n"
        f"  {d:<11} can't be undone\n\n"
        f"  A website could trick your helper into using {d} on your stuff.\n\n"
        "  Pick one:\n"
        "    1. Take one of them out, or make two separate helpers.  ← easiest\n"
        f"    2. If {d} really is safe, an OPERATOR says so — not the tool's own code:\n"
        f'         Agent(..., accepts_tainted=["{d}"])\n\n'
        "  -> docs/15-first-agent.md"
    )
