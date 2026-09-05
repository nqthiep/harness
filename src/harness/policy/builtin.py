"""Built-in policies — docs/04-interfaces.md §3.

There is no ApprovalPolicy: approval is I/O and may be async, and Policy.check is
sync and pure.  The engine resolves a surviving ASK instead (ADR-021).
"""
from __future__ import annotations

from typing import Any, Sequence
from urllib.parse import urlparse

from ..secrets import contains_live_secret
from ..tools import EFFECT_PROFILES, Effect, ToolSpec
from .base import Ruling, ToolCall, Verdict
from .label import Confidentiality, Grants, Integrity, Label


class EffectPolicy:
    name = "effect"

    def check(self, call: ToolCall, ctx: Any) -> Ruling:
        p = EFFECT_PROFILES[call.spec.effect]
        v = p.decision_strict if ctx.safety == "strict" else p.decision_standard
        return Ruling(v, f"effect={call.spec.effect.value}", self.name)


def emits_of(spec: ToolSpec, grants: Grants, payload: str | None = None) -> Label:
    """Nhãn mà KẾT QUẢ của tool này mang — L-1, design/00-foundation.md §3.2.

    `sensitive` chỉ nâng CONFIDENTIALITY (dữ liệu nhạy cảm, không phải dữ liệu đáng ngờ);
    nó không đổi integrity — một tool đọc bảng lương nội bộ không vì thế mà trở thành
    "untrusted", nó chỉ trở thành "secret" (design/00-foundation §3.2, S-3).

    `payload`, khi có, là kết quả THÔ (trước `redact()`) của chính lời gọi này — nguồn
    nâng confidentiality THỨ NHẤT của S-3: `Secret[T]` người dùng đưa vào. Không có cơ
    chế deps riêng ở harness này, nên đường đi thật của nó là tool tự `.reveal()` một
    `Secret` (vd. để ký request) rồi vô tình (hoặc cố ý) trả nguyên giá trị đó về —
    đúng khoảnh khắc `redact()` đã canh sẵn để chặn trước khi bytes tới model. Gắn
    `contains_live_secret` vào đúng chỗ đó, dùng đúng phép so khớp `redact()` dùng, thay
    vì dựng thêm một cơ chế deps mới: một `Secret` không bao giờ redact-nhưng-quên-gắn-nhãn,
    hay ngược lại.
    """
    base = EFFECT_PROFILES[spec.effect].emits
    secret = spec.name in grants.sensitive or (payload is not None and contains_live_secret(payload))
    if secret:
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
    nên `check()` vẫn thuần (POL-4: không I/O, không phụ thuộc thời gian gọi)."""
    name = "taint"

    def __init__(self, grants: Grants = Grants()) -> None:
        self._grants = grants

    def check(self, call: ToolCall, ctx: Any) -> Ruling:
        return check_flow(ctx.label, call.spec, self._grants)


class RequireBeforePolicy:
    """A tool may not be ATTEMPTED until another tool has already been called earlier in
    this run — "consult the advisor before touching this" as a structural gate, not a
    prompt the model can forget or talk itself past.

    **What this is not.** It never GRANTS anything — it can only turn ALLOW into DENY
    (P-2: a policy only ever restricts, `PolicyEngine.decide()`'s `max()` composition).
    The tool it requires (an "advisor" subagent, most naturally) is read for its
    OPINION, exactly like `search` or `fetch` — never for a verdict. Design/00-foundation
    §4.2's invariant D-1 is why: `Actor` deliberately has no `Model` variant, so a model
    — however much stronger, however framed as "the advisor" — can never be the one who
    grants a `Decision`. Only a human or an operator can, through `approve=`, exactly as
    before this policy existed; this policy only makes an attempt WITHOUT that first step
    impossible, never approves the attempt itself.

    `ctx.tools_called` (`dispatch.py::RunContext`/`lg/runtime.py::_Ctx`) is the source of
    truth — tool NAMES only, populated from completed calls earlier in the run, never
    arguments or results (IDL-15).
    """

    def __init__(self, *, tool: str, requires: str, reason: str | None = None) -> None:
        self._tool, self._requires = tool, requires
        self._reason = reason or (
            f"{tool!r} needs {requires!r} to have been called earlier in this run first")
        self.name = f"require-before:{tool}"

    def check(self, call: ToolCall, ctx: Any) -> Ruling:
        if call.name != self._tool:
            return Ruling(Verdict.ALLOW, "", self.name)
        if self._requires in getattr(ctx, "tools_called", frozenset()):
            return Ruling(Verdict.ALLOW, "", self.name)
        return Ruling(Verdict.DENY, self._reason, self.name)


class EgressPolicy:
    """Advisory, không phải kiểm soát mạng thật — design/review-security.md S-18.

    `Policy.check` bắt buộc thuần + đồng bộ (POL-4: không I/O, không DNS), nên chỗ này chỉ
    so khớp CHUỖI hostname model đưa ra với allowlist. Nó KHÔNG resolve DNS: một
    `fetch_page(url="http://look-alike.attacker.example/")` qua được đúng phép kiểm mà
    `fetch_page(url="http://docs.python.org/")` qua, nếu chuỗi host tự nó nằm trong
    allowlist hoặc phép kiểm lỏng — DNS rebinding trỏ nó về IP nội bộ chỉ lộ ra lúc THẬT
    SỰ gọi, không phải lúc policy kiểm. Kiểm soát mạng thật (egress proxy, network policy
    ở tầng container) là thứ duy nhất đóng được lỗ đó; đây chỉ chặn trường hợp RÕ RÀNG
    (host không hề có trong danh sách), không hơn.
    """
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


def builtins_for(grants: "Grants", allowed_hosts: "Sequence[str] | None") -> tuple:
    """The three policies EVERY engine runs, built in one place.

    Both engines used to construct this tuple themselves — `agent.py` for the classic
    loop, `lg/__init__.py` for the durable graph — which made "do both backends enforce
    the same builtin rules?" a question you answered by reading two files and hoping.
    A fourth policy added to one and not the other would be invisible: no test fails when
    a rule is merely absent somewhere.

    Order matters and is asserted by `PolicyEngine`'s `max()` composition (P-2, a policy
    can only restrict), so it is fixed here rather than repeated: effect first (what the
    tool IS), then flow (what has happened to the run), then egress (where it may reach).

    `tests/test_parity.py` asserts neither engine builds the tuple itself (ADR-099).
    """
    return (EffectPolicy(), TaintPolicy(grants), EgressPolicy(allowed_hosts))
