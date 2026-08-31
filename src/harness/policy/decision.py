"""Bản ghi phê duyệt — design/00-foundation.md §4, design/02-safety-engine.md.

Nghiên cứu đọc 30 gói qua 3 ngôn ngữ và không tìm được gói nào coi approval là một
SỰ KIỆN CÓ THỂ AUDIT: `_ApprovalRecord` của openai-agents là `bool | list[str]` với
`always_approve` không bao giờ hết hạn; `ToolConfirmation` của Java là đúng một boolean;
và tín hiệu "audit" dày nhất trong Python là `decision_log` của agno — một tool mà chính
model gọi để tự ghi về mình (research/09 §14).

Ở đây approval không phải trạng thái. Nó là một bản ghi bất biến, chỉ ghi thêm, mà:

  D-1  không trường nào do model sinh ra — `Actor` cố ý KHÔNG có biến thể `Model`;
  D-2  chỉ ghi thêm, không sửa, không xoá. Thu hồi = ghi một `Decision` mới có DENY.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence

from .._value import value
from .base import Verdict

#: T-8.2 — the shape of `PolicyEngine.decide()`/`.resolve()` that produced a `Decision`,
#: stamped by callers that record one. Bumped only when composition itself changes (how
#: a verdict is derived), the same role `EVENT_SCHEMA_VERSION` plays for `Event`.
POLICY_ENGINE_VERSION = "1.0"


@value
class Actor:
    """AI đã quyết định. Không có biến thể `Model` — đó chính là chỗ agno sai."""
    kind: str          # "human" | "operator" | "policy"
    id: str
    via: str | None = None

    @classmethod
    def human(cls, id: str, *, via: str) -> "Actor":
        return cls("human", id, via)

    @classmethod
    def operator(cls, id: str) -> "Actor":
        return cls("operator", id)

    @classmethod
    def policy(cls, rule: str) -> "Actor":
        return cls("policy", rule)


@value
class Approval:
    """Bọc trả về TÙY CHỌN cho callback `approve=` — S-11.

    `Actor` là lời tự khai của bên nào đang giữ callback: `Approver(fn, actor=...)` cố
    định danh tính LÚC DỰNG, còn ai thật sự bấm nút là chuyện khác — một callback trả
    thẳng `bool` không có cách nào nói CHO harness biết ai vừa duyệt. Trả `Approval` thay
    vì `bool` khi callback THẬT SỰ biết danh tính (id phiên Slack đã xác thực, user OAuth
    trả về) — `Decision.actor` ghi đúng danh tính đó thay vì placeholder chung
    `Actor.human("approver", via="callback")` mọi callback trả `bool` đều nhận.

    Vẫn chưa phải bằng chứng đã xác thực (`AuthEvidence` — chữ ký kênh, `channel_message_id`
    — mà review đề xuất là bản sửa đầy đủ): đây chỉ mở đường cho callback TỰ báo danh tính
    thật, không ép buộc nó phải chứng minh. Rẻ hơn, và đóng đúng phần "harness không có
    cách nào nhận identity" — phần "callback tự khai gian" vẫn còn nguyên, ghi lại ở
    `07-risks`.
    """
    ok: bool
    actor: "Actor | None" = None


@value
class Scope:
    """Cái gì được duyệt.

    `args` giữ nguyên ternary của Microsoft `ToolApprovalRule` — thiết kế tốt nhất tìm
    được, vì nó khoá grant theo GIÁ TRỊ THAM SỐ chứ không chỉ theo tên tool: duyệt
    `delete_file(path="/tmp/x")` không duyệt `delete_file(path="/etc/passwd")`. Mọi dự án
    khác duyệt ĐỘNG TỪ và bỏ qua TÂN NGỮ (research/09 §14).

        None  → mọi lời gọi tới tool này
        {}    → chỉ lời gọi không tham số
        {...} → chỉ lời gọi có đúng bộ tham số này
    """
    tool: str
    args: Mapping[str, Any] | None = None
    server: str | None = None
    call_id: str | None = None
    __hash__ = None                       # giữ một Mapping

    def matches(self, tool: str, args: Mapping[str, Any], *, call_id: str | None,
                server: str | None = None) -> bool:
        if self.tool != tool:
            return False
        # T-9.1, design/03 §5.3 M-4: một grant ghi cho tool local (`server=None`) không
        # bao giờ khớp một lời gọi MCP, và một grant ghi cho server A không khớp lời gọi
        # tới tool CÙNG TÊN trên server B — so khớp CHẶT, không có ký hiệu "mọi server".
        if self.server != server:
            return False
        if self.call_id is not None and self.call_id != call_id:
            return False
        if self.args is None:
            return True
        return dict(self.args) == dict(args)


class ForeverAllow(ValueError):
    """Một grant ALLOW không hạn dùng là không dựng được — xem `Decision.__post_init__`."""


@dataclass(frozen=True)
class Decision:
    """Bất biến, chỉ ghi thêm. Runtime điền `id`/`decided_at`/`run_id`/`actor`."""
    id: str
    verdict: Verdict
    scope: Scope
    actor: Actor
    decided_at: datetime
    expires_at: datetime | None
    run_id: str
    reason: str | None = None
    #: T-8.2, docs/17-research-alignment.md M8 — which shape of `PolicyEngine.decide()`
    #: produced this record. `Decision` already carried everything else
    #: `ApprovalRecord(decision_id, actor, policy_version, decided_at, expires_at,
    #: verdict, reason)` asked for (built earlier, for S-11/S-29) — this was the one
    #: field missing. Not a per-policy version (`EffectPolicy`/`TaintPolicy`/
    #: `EgressPolicy`/a user policy do not each declare one, and inventing that scheme
    #: is a bigger change than T-8.2 asks for) — a single engine-shape marker, the same
    #: role `EVENT_SCHEMA_VERSION` plays for `Event` (ADR-048): audit can tell "this
    #: ALLOW was granted under policy-engine v1.0" apart from a future v1.1 that changed
    #: how a verdict composes.
    policy_version: str | None = None

    def __post_init__(self) -> None:
        if self.verdict is Verdict.ASK:
            raise ValueError("ASK không bao giờ là kết quả cuối; chỉ ALLOW hoặc DENY")
        # "Duyệt vĩnh viễn" KHÔNG BIỂU DIỄN ĐƯỢC. `always_approve=True` của openai-agents
        # ghi một grant sống hết đời context và không ai biết ai đã cấp (research/09 §14).
        # Một DENY thì được phép vô hạn: hạn chế là vĩnh viễn, cho phép thì không.
        if (self.verdict is Verdict.ALLOW
                and self.expires_at is None
                and self.scope.call_id is None):
            raise ForeverAllow(
                "grant ALLOW phải có expires_at, hoặc scope.call_id để chỉ áp cho một "
                "lời gọi. Sửa: đặt expires_at=<thời điểm>, hoặc Scope(..., call_id=...)")

    def live_at(self, now: datetime) -> bool:
        return self.expires_at is None or now < self.expires_at


class DecisionLog:
    """Sổ chỉ ghi thêm. `lookup` fail-closed: không grant sống ⇒ ASK, không phải ALLOW."""

    def __init__(self) -> None:
        self._rows: list[Decision] = []

    def record(self, d: Decision) -> Decision:
        self._rows.append(d)              # D-2: chỉ ghi thêm
        return d

    def all(self) -> Sequence[Decision]:
        return tuple(self._rows)

    def lookup(self, tool: str, args: Mapping[str, Any], *, run_id: str,
               now: datetime, call_id: str | None = None,
               server: str | None = None) -> Verdict:
        """Hợp thành bằng `max()` — cùng phép với Verdict lattice, cùng lý do.

        DENY thắng mọi grant còn hạn mà không cần một luật ưu tiên thứ hai: thu hồi
        chỉ là ghi thêm một hàng, và `max()` lo phần còn lại.
        """
        worst = None
        for d in self._rows:
            if d.run_id != run_id or not d.live_at(now):
                continue
            if not d.scope.matches(tool, args, call_id=call_id, server=server):
                continue
            worst = d.verdict if worst is None else max(worst, d.verdict)
        return Verdict.ASK if worst is None else worst
