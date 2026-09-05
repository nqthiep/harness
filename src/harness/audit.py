"""The audit trail — the one place a verdict becomes an artifact.

Split out of `dispatch.py` under IDL-13's own rule: the loop was at 244 of its 250
code lines and recording refusals pushed it over, so the signal was taken rather than
the cap raised (the same call that produced `stop.py`, and `dispatch.py` itself).

The seam is real, not a filing cabinet.  Both engines answered the same two questions
in two hand-written copies:

  * what does an operator SEE about this decision      -> `POLICY_DECIDED`
  * what does an auditor still have after the process exits -> a `Decision` row

`dispatch.py` and `lg/runtime.py` each built the `Decision(...)`/`Scope(...)`/actor
fallback by hand at their own call sites, which is how they came to disagree about
which verdicts get recorded at all: the classic loop recorded a row only inside its
`ASK` branch, so a DENY from `EffectPolicy`/`TaintPolicy`/`EgressPolicy`/
`RequireBeforePolicy` or any user policy left the approval book empty (measured:
`decision rows: []` on a run whose taint policy refused a write).  `decision.py`'s own
argument is the opposite one — "an audit log that records only what was permitted
cannot answer 'what did we refuse, and why'" — and `Decision.__post_init__` already
permits an unbounded DENY precisely so a refusal can be written without a TTL.

So the rule lives here, once, and both engines call it:

    every DENY is recorded, unless this run's book already holds the identical
    refusal; an ALLOW is recorded when it resolved an ASK.

An ASK is never recorded, and never emitted either.  Both halves are deliberate.
`Decision.__post_init__` refuses to store an ASK at all ("ASK is never a final
outcome"), so a `policy.decided` event reporting one would announce as a decision the
thing the durable record declines to keep; the row that matters is whatever it resolved
INTO, which arrives here one call later.  What an ASK *does* carry that its resolution
does not is WHICH policy raised the question — `EffectPolicy` says `effect=danger`, and
the resolved row says only `approval` — so that name travels on the resolved event as
`asked_by`, and an auditor can still answer "why was a person asked about this call".

**Why a DENY is deduped and an ALLOW is not.**  S-29 (`lg/runtime.py::_regate`) settled
that a live grant reused N times must leave N rows: a `Decision` is permission, one row
per grant answers "who allowed this" but not "what happened under that authority", and
`tests/test_attack_s29.py` pins it.  A refusal is the opposite shape — it authorises
nothing, and the same refusal of the same call in the same run is one fact.  Writing it
twice happens for exactly one reason: the verdict was READ BACK OUT of the book
(`DecisionLog.lookup` composing a revocation with `max()`), so the row is already there
and a second copy would make the audit log cite itself.  A refusal with no matching row
— a policy denying, the ask-cap, an approver declining, a grant that expired — is always
recorded.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from .observe.events import EventKind
from .policy.base import Ruling, Verdict
from .policy.decision import (POLICY_ENGINE_VERSION, Actor, AuthEvidence, Decision,
                              Scope, actor_json, evidence_json)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def decided(bus, decisions, ruling: Ruling, *, run_id: str, step: int, tool: str,
            call_id: str, args: Mapping[str, Any] | None = None,
            server: str | None = None, actor: Actor | None = None,
            evidence: AuthEvidence | None = None, resolved_ask: bool = False,
            asked_by: str | None = None, row_id: str | None = None) -> None:
    """Emit `policy.decided`, and durably record the verdict when it is a refusal.

    `resolved_ask=True` at the two approval sites: an ALLOW that came out of an ASK is
    a grant somebody issued, and D-2 says a grant is a row.  A plain ALLOW from the
    policy engine is not — it is the default, it is on the event stream, and writing
    one row per permitted call would turn an approval book into a second transcript.

    `asked_by` is the name of the policy whose ASK this resolves, carried onto the
    resolved event because the resolution itself does not name it (see the module
    docstring).  `row_id` distinguishes two rows about the same `call_id` (the S-29
    reuse row, the re-gate's refusal of a call an earlier gate had allowed); it
    defaults to the call.
    """
    bus.emit(EventKind.POLICY_DECIDED, step=step, tool=tool, call_id=call_id,
             verdict=ruling.verdict.name, reason=ruling.reason, policy=ruling.policy,
             asked_by=asked_by, actor=actor_json(actor), evidence=evidence_json(evidence))
    if ruling.verdict is Verdict.DENY:
        if not _already_refused(decisions, ruling, run_id, tool, call_id, args, server):
            record(decisions, ruling, run_id=run_id, tool=tool, call_id=call_id,
                   args=args, server=server, actor=actor, evidence=evidence,
                   row_id=row_id)
    elif resolved_ask and ruling.verdict is Verdict.ALLOW:
        record(decisions, ruling, run_id=run_id, tool=tool, call_id=call_id, args=args,
               server=server, actor=actor, evidence=evidence, row_id=row_id)


def _already_refused(decisions, ruling: Ruling, run_id: str, tool: str, call_id: str,
                     args: Mapping[str, Any] | None, server: str | None) -> bool:
    """Is this exact refusal, of this exact call, already in this run's book?

    Compared on the SCOPE and the verdict, not on the reason or the actor: two rows
    saying "this call is refused" are one fact however differently they phrase it, and
    the only way to reach a second one is to have read the first back out of the book.
    See the module docstring for why an ALLOW gets the opposite treatment.
    """
    want = Scope(tool=tool, args=dict(args or {}), server=server, call_id=call_id)
    return any(row.run_id == run_id and row.verdict is ruling.verdict
               and row.scope.tool == want.tool and row.scope.server == want.server
               and row.scope.call_id == want.call_id
               and (row.scope.args is None
                    or dict(row.scope.args) == dict(want.args or {}))
               for row in decisions.all())


def record(decisions, ruling: Ruling, *, run_id: str, tool: str, call_id: str,
           args: Mapping[str, Any] | None = None, server: str | None = None,
           actor: Actor | None = None, evidence: AuthEvidence | None = None,
           row_id: str | None = None) -> Decision:
    """One `Decision`, scoped to THIS call.

    `scope.call_id` is always set, so a row can never widen into a standing grant:
    `Decision.__post_init__` is what forbids the unbounded ALLOW, and this keeps every
    caller on the side of that rule without each of them having to remember it.

    The actor falls back to `Actor.policy(ruling.policy)` — the rule that decided,
    named.  Never a person: D-1 says a `Decision` records who decided, and a policy
    denying on its own is not a human doing so (the ask-cap regression `dispatch.py`
    already documents, generalised to every site).
    """
    return decisions.record(Decision(
        id=f"dec-{row_id or call_id}", verdict=ruling.verdict,
        scope=Scope(tool=tool, args=dict(args or {}), server=server, call_id=call_id),
        actor=actor if actor is not None else Actor.policy(ruling.policy),
        decided_at=utcnow(), expires_at=None, run_id=run_id, reason=ruling.reason,
        policy_version=POLICY_ENGINE_VERSION, evidence=evidence))
