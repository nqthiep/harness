"""Built-in policies — docs/04-interfaces.md §3.

There is no ApprovalPolicy: approval is I/O and may be async, and Policy.check is
sync and pure.  The engine resolves a surviving ASK instead (ADR-021).
"""
from __future__ import annotations

from typing import Any, Sequence
from urllib.parse import urlparse

from ..tools import EFFECT_PROFILES, Effect
from .base import Decision, ToolCall, Verdict


class EffectPolicy:
    name = "effect"

    def check(self, call: ToolCall, ctx: Any) -> Decision:
        p = EFFECT_PROFILES[call.spec.effect]
        v = p.decision_strict if ctx.safety == "strict" else p.decision_standard
        return Decision(v, f"effect={call.spec.effect.value}", self.name)


class TaintPolicy:
    name = "taint"

    def check(self, call: ToolCall, ctx: Any) -> Decision:
        if ctx.tainted and call.spec.effect is Effect.DANGER and not call.spec.accepts_tainted:
            return Decision(
                Verdict.DENY,
                f"{call.name} cannot be undone, and this run has already read untrusted "
                f"content. Blocked so a web page cannot decide to run it.",
                self.name,
            )
        return Decision(Verdict.ALLOW, "", self.name)


class EgressPolicy:
    name = "egress"

    def __init__(self, allowed_hosts: Sequence[str] | None) -> None:
        self._hosts = tuple(allowed_hosts) if allowed_hosts is not None else None

    def check(self, call: ToolCall, ctx: Any) -> Decision:
        if self._hosts is None or call.spec.effect is not Effect.EXTERNAL:
            return Decision(Verdict.ALLOW, "", self.name)
        for key, value in call.arguments.items():
            if not isinstance(value, str):
                continue
            if key in ("url", "uri", "host", "hostname", "endpoint") or value.startswith("http"):
                host = urlparse(value).hostname or value
                if not any(host == h or host.endswith("." + h) for h in self._hosts):
                    return Decision(
                        Verdict.DENY,
                        f"{host!r} is not in allowed_hosts", self.name)
        return Decision(Verdict.ALLOW, "", self.name)
