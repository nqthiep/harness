"""Built-in policies — docs/04-interfaces.md §3.

There is no ApprovalPolicy: approval is I/O and may be async, and Policy.check is
sync and pure.  The engine resolves a surviving ASK instead (ADR-021).
"""
from __future__ import annotations

from collections import deque
from typing import Any, Mapping, Sequence
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


#: Argument names whose VALUE is treated as a host even without a scheme.  A bare host
#: under any OTHER name (`target="evil.example"`) is not detected — see `EgressPolicy`'s
#: "Cái nó KHÔNG bắt".
_EGRESS_HOST_KEYS = frozenset({"url", "uri", "host", "hostname", "endpoint"})

#: How many values to look at inside one call's `arguments` before giving up.  A bound,
#: not a belief about real payloads: `arguments` is model-authored, so a self-referential
#: or absurdly nested value must cost O(1) here rather than hang or raise inside a policy
#: — `PolicyEngine.decide()` would (correctly) turn a `RecursionError` into a DENY of an
#: innocent call.  A NODE budget rather than a DEPTH cap, walked breadth-first, on
#: purpose: a depth cap is evadable by nesting one level deeper than the cap, which is
#: exactly the shape of evasion this policy is here to make boring.
_EGRESS_MAX_NODES = 512


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

    **Cái nó BẮT, và tại sao đúng ba thứ này.** "Trường hợp RÕ RÀNG" ở trên là lời hứa,
    và trước bản vá này ba hình dạng đối số làm nó SAI ngay trong chính định nghĩa của nó:

    * `HTTP://evil.example/` — vẫn là trường hợp rõ ràng, chỉ viết hoa; `str.startswith`
      phân biệt hoa thường nên nó lọt. Giờ so khớp scheme không phân biệt hoa thường, và
      host lấy ra cũng hạ về chữ thường trước khi đối chiếu (hostname vốn không phân biệt
      hoa thường — RFC 4343), nên `HTTP://DOCS.PYTHON.ORG/` vừa không lọt oan vừa không
      bị chặn oan.
    * `urls=["http://evil.example/"]`, `req={"url": ...}` — `isinstance(value, str)` bỏ
      qua sạch. Một tool nhận danh sách URL (batch fetch) là hình dạng bình thường, không
      phải trò lách; bỏ qua nó biến allowlist thành thứ chỉ cần đổi kiểu đối số là qua.
      Giờ duyệt cả `list`/`tuple`/`Mapping`, theo bề rộng, tối đa `_EGRESS_MAX_NODES` giá
      trị mỗi lời gọi.
    * `effect="danger"` có đối số URL — trước đây chỉ `EXTERNAL` bị soi. Nhưng effect nói
      về TÍNH ĐẢO NGƯỢC, không nói về việc có chạm mạng hay không, và `tools/_guess()` gán
      effect theo TÊN: một tool tên `send_report(url=...)` được đoán thành `danger` vì chữ
      "send", và bằng đúng cái đoán đó rơi ra khỏi allowlist. Một tool KHÔNG đảo ngược
      được mà lại chạm mạng chính là tool đáng soi nhất, nên giờ soi cả hai effect. Đây là
      siết chứ không nới (P-2) và không ảnh hưởng deployment nào truyền
      `allowed_hosts=None`.

    **Cái nó KHÔNG bắt, nói thẳng thay vì để người đọc tự phát hiện.** Một host TRẦN
    (không scheme) dưới một tên đối số ngoài `_EGRESS_HOST_KEYS` — `target="evil.example"`
    — vẫn lọt: bắt nó nghĩa là đoán "chuỗi này có phải hostname không" cho MỌI chuỗi, và
    `"notes.txt"`, `"v1.2"`, một câu tiếng Anh có dấu chấm đều sẽ thành DENY oan. `read`/
    `write` cũng không bị soi: một tool chạm mạng mà khai `read` là khai sai effect, và
    chỗ sửa là khai đúng, không phải quét mọi đối số của mọi tool. Cả hai đều là RANH GIỚI
    ĐÃ BIẾT của một phép kiểm advisory, không phải chỗ chưa kịp làm.
    """
    name = "egress"

    #: `EXTERNAL` (chạm mạng theo định nghĩa) và `DANGER` (không đảo ngược được — nếu nó
    #: chạm mạng thì đó là lần chạm đáng soi nhất).  Xem docstring.
    _INSPECTED_EFFECTS = frozenset({Effect.EXTERNAL, Effect.DANGER})

    def __init__(self, allowed_hosts: Sequence[str] | None) -> None:
        self._hosts = (tuple(h.lower() for h in allowed_hosts)
                       if allowed_hosts is not None else None)

    def check(self, call: ToolCall, ctx: Any) -> Ruling:
        hosts = self._hosts
        if hosts is None or call.spec.effect not in self._INSPECTED_EFFECTS:
            return Ruling(Verdict.ALLOW, "", self.name)
        # A container inherits the key of the argument it came from, so
        # `urls=["evil.example"]` is read exactly the way `url="evil.example"` is; a
        # nested Mapping brings its own keys instead.
        queue: deque[tuple[str, Any]] = deque(call.arguments.items())
        for _ in range(_EGRESS_MAX_NODES):
            if not queue:
                break
            key, value = queue.popleft()
            if isinstance(value, str):
                if key not in _EGRESS_HOST_KEYS and value[:4].lower() != "http":
                    continue
                # `hostname` is already lowercased by `urlparse`; the `or value` fallback
                # (a bare host under a host-shaped key) is not, and hostnames are
                # case-insensitive (RFC 4343) — so normalise both sides, once.
                host = (urlparse(value).hostname or value).lower()
                if not any(host == h or host.endswith("." + h) for h in hosts):
                    return Ruling(
                        Verdict.DENY,
                        f"{host!r} is not in allowed_hosts", self.name)
            elif isinstance(value, Mapping):
                queue.extend((str(k), v) for k, v in value.items())
            elif isinstance(value, (list, tuple)):
                queue.extend((key, v) for v in value)
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
