"""Built-in policies — docs/04-interfaces.md §3.

There is no ApprovalPolicy: approval is I/O and may be async, and Policy.check is
sync and pure.  The engine resolves a surviving ASK instead (ADR-021).
"""
from __future__ import annotations

from typing import Any, Sequence
from urllib.parse import urlparse

from ..tools import EFFECT_PROFILES, Effect, ToolSpec
from .base import Ruling, ToolCall, Verdict
from .label import Confidentiality, Grants, Integrity, Label


class EffectPolicy:
    name = "effect"

    def check(self, call: ToolCall, ctx: Any) -> Ruling:
        p = EFFECT_PROFILES[call.spec.effect]
        v = p.decision_strict if ctx.safety == "strict" else p.decision_standard
        return Ruling(v, f"effect={call.spec.effect.value}", self.name)


def emits_of(spec: ToolSpec, grants: Grants) -> Label:
    """Nhãn mà KẾT QUẢ của tool này mang — L-1, design/00-foundation.md §3.2.

    `sensitive` chỉ nâng CONFIDENTIALITY (dữ liệu nhạy cảm, không phải dữ liệu đáng ngờ);
    nó không đổi integrity — một tool đọc bảng lương nội bộ không vì thế mà trở thành
    "untrusted", nó chỉ trở thành "secret" (design/00-foundation §3.2, S-3).
    """
    base = EFFECT_PROFILES[spec.effect].emits
    if spec.name in grants.sensitive:
        return Label(base.integrity, Confidentiality.SECRET)
    return base


def check_flow(label: Label, spec: ToolSpec, grants: Grants) -> Ruling:
    """design/02-safety-engine.md §4.1 — hai nhánh DENY, một cho mỗi trục của `Label`.

    `read` không bị nhánh confidentiality chạm tới vì nó không phải sink — nhận định của
    Microsoft, chép lại có ghi công: read-only tool "safe to call even when the agent
    context is tainted — it cannot exfiltrate" (research/09 §16bis).
    """
    profile = EFFECT_PROFILES[spec.effect]
    if (label.integrity is Integrity.UNTRUSTED and spec.effect is Effect.DANGER
            and spec.name not in grants.accepts_tainted):
        return Ruling(
            Verdict.DENY,
            f"{spec.name} cannot be undone, and this run has already read untrusted "
            f"content. Blocked so a web page cannot decide to run it.",
            "taint",
        )
    if (label.confidentiality is Confidentiality.SECRET
            and profile.max_confidentiality is Confidentiality.PUBLIC):
        return Ruling(
            Verdict.DENY,
            f"{spec.name} can only send information onward, and this run has read "
            f"something marked secret. It could leak.",
            "taint",
        )
    return Ruling(Verdict.ALLOW, "", "taint")


class TaintPolicy:
    """Config lấy lúc construction — cùng mẫu với `EgressPolicy(allowed_hosts)` bên dưới,
    nên `check()` vẫn thuần (P-4: không I/O, không phụ thuộc thời gian gọi)."""
    name = "taint"

    def __init__(self, grants: Grants = Grants()) -> None:
        self._grants = grants

    def check(self, call: ToolCall, ctx: Any) -> Ruling:
        return check_flow(ctx.label, call.spec, self._grants)


class EgressPolicy:
    name = "egress"

    def __init__(self, allowed_hosts: Sequence[str] | None) -> None:
        self._hosts = tuple(allowed_hosts) if allowed_hosts is not None else None

    def check(self, call: ToolCall, ctx: Any) -> Ruling:
        if self._hosts is None or call.spec.effect is not Effect.EXTERNAL:
            return Ruling(Verdict.ALLOW, "", self.name)
        for key, value in call.arguments.items():
            if not isinstance(value, str):
                continue
            if key in ("url", "uri", "host", "hostname", "endpoint") or value.startswith("http"):
                host = urlparse(value).hostname or value
                if not any(host == h or host.endswith("." + h) for h in self._hosts):
                    return Ruling(
                        Verdict.DENY,
                        f"{host!r} is not in allowed_hosts", self.name)
        return Ruling(Verdict.ALLOW, "", self.name)
