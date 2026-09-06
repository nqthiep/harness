"""Policy composition — docs/04-interfaces.md §3.

max() over the Verdict lattice: a policy can only ever restrict (property P-2).
Approval is resolved here, not by a policy (ADR-021).
"""
from __future__ import annotations

import inspect
from typing import Any, Sequence

from .base import Ruling, Policy, ToolCall, Verdict
from .decision import Actor, Approval, AuthEvidence


class PolicyEngine:
    def __init__(self, builtins: Sequence[Policy], user: Sequence[Policy] = ()) -> None:
        self._policies = tuple(builtins) + tuple(user)   # built-ins first, unremovable
        #: Which NAMES came from the `user=` slot. `Ruling.policy` already carries the
        #: name of whichever policy produced a verdict, so this is enough to tell "ASK
        #: from the built-in effect ladder" from "ASK a user deliberately wrote" at the
        #: point of decision — without widening the `Policy` Protocol (a seam), and
        #: without `decide()` having to return the winning policy OBJECT (its return
        #: type is public). See `resolve()`'s `approve is None` branch.
        #:
        #: A user policy that shadows a built-in name lands in both tuples and is
        #: treated as a user policy here: the failure that direction is a LOUD deny on
        #: an effect-ladder ASK, not a silent allow on a user ASK.
        self._user_names = frozenset(getattr(p, "name", "") for p in user)

    def decide(self, call: ToolCall, ctx: Any) -> Ruling:
        worst = Ruling(Verdict.ALLOW, "", "default")
        for p in self._policies:
            try:
                d = p.check(call, ctx)
            except Exception as exc:                      # fail closed
                d = Ruling(Verdict.DENY, f"policy {p.name!r} raised: {exc}", p.name)
            if d.verdict > worst.verdict:
                worst = d
            if worst.verdict is Verdict.DENY:
                break
        return worst

    async def resolve(self, decision: Ruling, call: ToolCall, ctx: Any, approve,
                       *, require_evidence: bool = False,
                       ) -> "tuple[Ruling, Actor | None, AuthEvidence | None]":
        """Turn a surviving ASK into ALLOW/DENY.  The engine may await; a policy may not.

        Returns the actor AND evidence the callback reported, if any — S-11. A plain
        `bool` return (the common case) reports neither: the caller falls back to a
        generic placeholder actor, same as before either existed. Returning
        `Approval(ok, actor=..., evidence=...)` instead is how a callback that actually
        knows who approved (an authenticated Slack click, an OAuth session) gets that
        identity — and, when the channel itself returned proof, real `AuthEvidence` —
        into the audit log instead of the placeholder every `bool`-returning callback is
        stuck with.

        `require_evidence=True` (S-11, đã sửa — `Agent(require_approval_evidence=True)`)
        is the deployment-level backstop: an approval with no `evidence` gets DENIED here,
        fail-closed, rather than silently trusted — closing exactly the "callback CỐ TÌNH
        khai gian danh tính" scenario `07-risks` names. Off by default: with the flag off
        nothing changes for anyone, the same backward-compat discipline every other opt-in
        gate in this codebase follows (S-25(b)'s `max_asks_per_run`, S-20's
        `budget.usd=None`, ...).

        The gate covers `actor=None` — a bare `bool` return — as well as a reported
        `human`. It has to: `dispatch.py::_run_batch` and `lg/runtime.py::_gate` both
        record an actor-less callback approval as `Actor.human("approver", via="callback")`,
        so exempting it wrote exactly the row the flag exists to forbid — an ALLOW
        attributed to a human, with no evidence — and punished disclosure while doing it
        (name your approver and you were denied; say nothing and you were let through).
        An `operator`/`policy` actor is still exempt: that is not a person claiming to
        have clicked something, so there is no click to prove.
        """
        if decision.verdict is not Verdict.ASK:
            return decision, None, None
        if approve is None:
            from ..tools import Effect
            if ctx.safety == "strict" or call.spec.effect is Effect.DANGER:
                return Ruling(
                    Verdict.DENY,
                    f"{call.name} needs approval and no approve= callback was given",
                    "approval"), None, None
            if decision.policy in self._user_names:
                return Ruling(
                    Verdict.DENY,
                    f"{call.name} needs approval — your policy {decision.policy!r} "
                    f"returned ASK — and no approve= callback was given",
                    "approval"), None, None
            return Ruling(Verdict.ALLOW, "no approval callback; allowed",
                          "approval"), None, None
        out = approve(call, ctx)
        if inspect.isawaitable(out):
            out = await out
        if isinstance(out, Approval):
            ok, actor, evidence = out.ok, out.actor, out.evidence
        else:
            ok, actor, evidence = bool(out), None, None
        if (ok and require_evidence and evidence is None
                and (actor is None or actor.kind == "human")):
            who = (f"a human actor ({actor.id!r})" if actor is not None else
                   "a callback that named no actor — which both engines record as the "
                   "generic human Actor.human('approver', via='callback') —")
            return Ruling(
                Verdict.DENY,
                f"{call.name} approved by {who} with no AuthEvidence, and this "
                f"deployment requires it (require_approval_evidence=True)",
                "approval"), actor, None
        return Ruling(Verdict.ALLOW if ok else Verdict.DENY,
                        "approved" if ok else "declined by approver",
                        "approval"), actor, evidence
