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

import difflib
from typing import TYPE_CHECKING

from .errors import ConfigError, ProfileLoosenedSafetyError, UnsafeToolSetError
from .policy.label import Grants
from .tools import EFFECT_PROFILES, Effect, ToolSpec
from .tools.registry import ToolSet

if TYPE_CHECKING:
    from .agent import Agent


_SAFETY_RANK = {"standard": 0, "strict": 1}


def _is_factory(p: object) -> bool:
    """A Policy *instance* carries `check` as a bound method; a class or a lambda does
    not.  Testing `hasattr(p, "check")` treats the class itself as an instance, because a
    class has the attribute too — the first version of this check did exactly that.

    Defined once, here, because both engines need it and both had their own copy:
    `agent.py` and `lg/__init__.py` carried byte-identical definitions, which is the
    shape `test_parity.py::test_one_definition_of_every_shared_constant` exists to catch
    and did not, because its list was written by hand. `guards.py` is where the two
    engines already meet (ADR-098).
    """
    import inspect
    return not inspect.ismethod(getattr(p, "check", None))

_SAFETY_HELP = (
    "  standard  the everyday level\n"
    "  strict    also asks before writing or fetching\n"
)


def check_safety(value: object, *, where: str = "Agent(safety=...)") -> str:
    """`safety=` is one of two words, checked here rather than trusted.

    `Literal["standard", "strict"]` is an ANNOTATION: nothing at run time compares the
    string to anything, and the one consumer that reads it —
    `policy/builtin.py::EffectPolicy.check`, `ctx.safety == "strict"` — treats every
    other spelling as `standard`. Measured on an agent with one `write` tool and no
    `approve=`: `safety="strict"` ran no tools, and `safety="stict"`, `"STRICT"` and
    `"Strict"` each ran the write. No exception, no event, nothing in the transcript
    said the level asked for was not the level applied.

    Same shape and tone as `tools/__init__.py`'s `effect=` typo message (`_guess` and
    the `difflib` hint beside it), because it is the same mistake one field over: a
    short closed vocabulary, spelled by hand, where a near-miss is silent. Matched
    case-insensitively for the hint only — `"STRICT"` shares no characters with
    `"strict"` as far as `difflib` is concerned, so a case typo would otherwise get the
    refusal without the answer.

    Returns the value so a caller can use it in place, which keeps the "validated" and
    "stored" spellings from drifting apart.
    """
    if isinstance(value, str) and value in _SAFETY_RANK:
        return value
    close = difflib.get_close_matches(str(value).lower(), sorted(_SAFETY_RANK), n=1,
                                      cutoff=0.4)
    hint = f' Did you mean "{close[0]}"?' if close else ""
    raise ConfigError(
        f"{value!r} is not one of the two safety levels.{hint}\n\n"
        f"{_SAFETY_HELP}\n"
        f"  You wrote:  {where.replace('...', repr(value))}\n\n"
        f"  -> docs/06-safety.md#4-least-privilege"
    )


def _rank(safety: str, *, whose: str) -> int:
    """`_SAFETY_RANK[...]`, but it says what went wrong when the key is not there.

    `check_safety` runs in `Agent.__init__` and so covers every agent this package
    builds, but these two functions are the layer BELOW that — `harness.lg`'s
    `build_agent()` reaches them with its own `safety=` string, and a duck-typed
    stand-in (which this module is written to accept, see the module docstring) can
    carry any value at all. The bare lookup turned the input this module exists to
    refuse into `KeyError: 'stict'` — an internal error from the one function whose
    entire job is a readable construction-time refusal.
    """
    if safety not in _SAFETY_RANK:
        check_safety(safety, where=f"{whose} safety=...")
    return _SAFETY_RANK[safety]


def check_grant_names(toolset: ToolSet, grants: Grants) -> None:
    """`accepts_tainted=` and `sensitive=` name TOOLS. A name that names none does nothing.

    Both are matched by name and nowhere else: `policy/builtin.py::check_flow` reads
    `spec.name not in grants.accepts_tainted`, and `emits_of` reads
    `spec.name in grants.sensitive`. So a misspelling is not a smaller grant — it is no
    grant, silently. Measured on an agent with a `payroll` (read) tool and a `publish`
    (write) sink: `sensitive=["payroll"]` ran `('payroll',)` — the publish refused by the
    confidentiality branch — and `sensitive=["payrol"]` ran `('payroll', 'publish')`.
    Construction raised nothing either way.

    `accepts_tainted=` looks like it is already checked, and is not: a typo there leaves
    the `external`+`danger` pair unmatched, so `_check_tool_set` below refuses the SET.
    That is an accident of the trifecta rule, not a check — remove the `external` tool
    and the same typo goes through in silence (measured: `accepts_tainted=["wipe_"]`
    with `fetch`+`wipe` raises `UnsafeToolSetError`; with `payroll`+`wipe` it does not).

    **An agent with no tools at all is exempt**, deliberately and narrowly: it can run
    nothing, so no grant on it can matter, and there is no candidate to suggest. It is
    also the shape a profile expects — `examples/vision_profile.py`'s caller writes
    `Agent(..., accepts_tainted=[ENROLL])` with an empty `tools=` and lets the profile
    supply the tool. `with_()` rebuilds the whole `Agent`, so the moment that toolset
    stops being empty this check runs against the real one.
    """
    if not len(toolset):
        return
    known = sorted(spec.name for spec in toolset)
    for field, names in (("sensitive", grants.sensitive),
                         ("accepts_tainted", grants.accepts_tainted)):
        for name in sorted(names):
            if toolset.get(name) is not None:
                continue
            close = difflib.get_close_matches(name, known, n=1, cutoff=0.4)
            hint = f" Did you mean {close[0]!r}?" if close else ""
            raise ConfigError(
                f"{field}={[name]!r} names a tool this helper does not have.{hint}\n\n"
                f"  {field}= is matched to your tools by name, so a name that matches "
                f"none of them\n  does nothing at all — and nothing says so.\n\n"
                f"  This helper's tools: {', '.join(known)}\n\n"
                f"  -> docs/06-safety.md#4-least-privilege"
            )


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
        if (_rank(child.safety, whose=f"Agent(name={child.name!r}, ...)")
                < _rank(parent_safety, whose="Agent(...)")):
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

    if (_rank(after.safety, whose=f"the profile {profile_name!r}'s")
            < _rank(before.safety, whose="Agent(...)")):
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
