"""Policy composition — docs/04-interfaces.md §3.

max() over the Verdict lattice: a policy can only ever restrict (property P-2).
Approval is resolved here, not by a policy (ADR-021).
"""
from __future__ import annotations

import inspect
from typing import Any, Sequence

from .base import Ruling, Policy, ToolCall, Verdict


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

    async def resolve(self, decision: Ruling, call: ToolCall, ctx: Any, approve) -> Ruling:
        """Turn a surviving ASK into ALLOW/DENY.  The engine may await; a policy may not."""
        if decision.verdict is not Verdict.ASK:
            return decision
        if approve is None:
            from ..tools import Effect
            if ctx.safety == "strict" or call.spec.effect is Effect.DANGER:
                return Ruling(
                    Verdict.DENY,
                    f"{call.name} needs approval and no approve= callback was given",
                    "approval")
            return Ruling(Verdict.ALLOW, "no approval callback; allowed", "approval")
        out = approve(call, ctx)
        if inspect.isawaitable(out):
            out = await out
        return Ruling(Verdict.ALLOW if out else Verdict.DENY,
                        "approved" if out else "declined by approver", "approval")
