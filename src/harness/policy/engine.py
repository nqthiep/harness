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
        is the deployment-level backstop: a callback that reports a `human` actor with no
        `evidence` gets DENIED here, fail-closed, rather than silently trusted — closing
        exactly the "callback CỐ TÌNH khai gian danh tính" scenario `07-risks` names. Off
        by default; a callback that never supplies evidence keeps working exactly as
        before, same backward-compat discipline every other opt-in gate in this codebase
        follows (S-25(b)'s `max_asks_per_run`, S-20's `budget.usd=None`, ...).
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
            return Ruling(Verdict.ALLOW, "no approval callback; allowed",
                          "approval"), None, None
        out = approve(call, ctx)
        if inspect.isawaitable(out):
            out = await out
        if isinstance(out, Approval):
            ok, actor, evidence = out.ok, out.actor, out.evidence
        else:
            ok, actor, evidence = bool(out), None, None
        if (ok and require_evidence and actor is not None
                and actor.kind == "human" and evidence is None):
            return Ruling(
                Verdict.DENY,
                f"{call.name} approved by a human actor ({actor.id!r}) with no "
                f"AuthEvidence, and this deployment requires it "
                f"(require_approval_evidence=True)",
                "approval"), actor, None
        return Ruling(Verdict.ALLOW if ok else Verdict.DENY,
                        "approved" if ok else "declined by approver",
                        "approval"), actor, evidence
