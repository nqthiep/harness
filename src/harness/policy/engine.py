"""Policy composition — docs/04-interfaces.md §3.

max() over the Verdict lattice: a policy can only ever restrict (property P-2).
Approval is resolved here, not by a policy (ADR-021).
"""
from __future__ import annotations

import inspect
from typing import Any, Sequence

from .base import Ruling, Policy, ToolCall, Verdict
from .decision import Actor, Approval


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

    async def resolve(self, decision: Ruling, call: ToolCall, ctx: Any,
                       approve) -> "tuple[Ruling, Actor | None]":
        """Turn a surviving ASK into ALLOW/DENY.  The engine may await; a policy may not.

        Returns the actor the callback reported, if any — S-11. A plain `bool` return
        (the common case) reports nothing: the caller falls back to a generic placeholder
        actor, same as before this existed. Returning `Approval(ok, actor=...)` instead
        is how a callback that actually knows who approved (an authenticated Slack click,
        an OAuth session) gets that identity into the audit log instead of the
        placeholder every `bool`-returning callback is stuck with.
        """
        if decision.verdict is not Verdict.ASK:
            return decision, None
        if approve is None:
            from ..tools import Effect
            if ctx.safety == "strict" or call.spec.effect is Effect.DANGER:
                return Ruling(
                    Verdict.DENY,
                    f"{call.name} needs approval and no approve= callback was given",
                    "approval"), None
            return Ruling(Verdict.ALLOW, "no approval callback; allowed", "approval"), None
        out = approve(call, ctx)
        if inspect.isawaitable(out):
            out = await out
        if isinstance(out, Approval):
            ok, actor = out.ok, out.actor
        else:
            ok, actor = bool(out), None
        return Ruling(Verdict.ALLOW if ok else Verdict.DENY,
                        "approved" if ok else "declined by approver", "approval"), actor
