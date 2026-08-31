"""`AuthEvidence` — S-11's remaining gap, design/07-risks-and-open-issues.md.

`Approval(ok, actor=...)` (T-8.2/S-11, `decision.py`) opened a channel for an `approve=`
callback to report a real identity instead of the generic `Actor.human("approver",
via="callback")` placeholder every plain-`bool`-returning callback gets. What it never
closed: **nothing stops the callback from self-declaring a fake identity.** `Actor` was,
and without this module still is, a claim — a Slack bot that always reports
`Actor.human("alice", via="slack")` regardless of who actually clicked "approve" would be
indistinguishable from one that checked.

**Scope, deliberately narrow.** This module gives an operator a tested, correct,
channel-agnostic primitive — HMAC-SHA256 with a constant-time compare and a replay
window — instead of asking them to hand-roll signature verification (the single most
common way a project rolls its own crypto badly: non-constant-time `==`, no replay
window, or both). It does **not** wire itself into `PolicyEngine.resolve()`, and does not
add a secret-management parameter to `Agent`/`build_agent()`. Verification happens INSIDE
the callback the operator already writes — the callback is the thing that received the
Slack webhook / OAuth token / whatever channel it is, so it is the only party that can
hold that channel's secret and know its shape. Wiring verification into the harness
itself would mean inventing a channel abstraction for channels this codebase has zero of
today (no Slack integration, no OAuth integration exist in `src/harness/`) — exactly the
"0 implementer" abstraction K-5 (review-kiss.md) already rejected once for this project.
The harness's job ends at: give `Actor` a place to report `verified=True` truthfully, and
give the operator a correct tool to decide whether it is.
"""
from __future__ import annotations

import hashlib
import hmac
import time

from .._value import value


@value
class AuthEvidence:
    """Bằng chứng gắn với MỘT quyết định phê duyệt — không phải một token phiên chung
    chung. `channel_message_id` neo nó vào đúng bản ghi phía kênh (tin nhắn Slack, sự kiện
    webhook) để một audit sau này truy lại được nguồn, đúng như review đề xuất.
    """
    channel: str                 # "slack" | "oauth" | tên kênh operator tự đặt
    channel_message_id: str      # id bản ghi phía kênh — để audit truy lại
    signed_payload: str          # chuỗi đã ký — xem `sign_evidence`
    signature: str                # hex HMAC-SHA256(secret, signed_payload)
    timestamp: float             # epoch giây lúc ký — cho cửa sổ chống replay


def _canonical_payload(channel: str, channel_message_id: str, actor_id: str,
                       timestamp: float) -> str:
    """Một định dạng DUY NHẤT cho `signed_payload` — `sign_evidence`/`verify_auth_evidence`
    đều gọi hàm này, không phải tự ráp chuỗi hai nơi khác nhau có thể lệch nhau."""
    return f"{channel}:{channel_message_id}:{actor_id}:{timestamp:.6f}"


def sign_evidence(secret: str, *, channel: str, channel_message_id: str, actor_id: str,
                  timestamp: float | None = None) -> AuthEvidence:
    """Dựng một `AuthEvidence` đã ký — dùng phía HỆ THỐNG TẠO ra sự kiện cần duyệt (vd.
    lúc gửi một tin nhắn Slack tương tác), để phía callback verify lại lúc người dùng bấm
    nút. `secret` không bao giờ đi vào `AuthEvidence` — chỉ chữ ký (một chiều).
    """
    ts = time.time() if timestamp is None else timestamp
    payload = _canonical_payload(channel, channel_message_id, actor_id, ts)
    sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"),
                   hashlib.sha256).hexdigest()
    return AuthEvidence(channel, channel_message_id, payload, sig, ts)


def verify_auth_evidence(evidence: AuthEvidence, *, secret: str, actor_id: str,
                         max_age_s: float = 300.0, now: float | None = None) -> bool:
    """`True` chỉ khi CẢ BA đúng: (1) `signed_payload` thật sự khớp
    `channel`/`channel_message_id`/`actor_id`/`timestamp` của chính `evidence` đó — chặn
    một `evidence` hợp lệ cho actor A bị dùng lại để "chứng minh" actor B; (2) HMAC khớp,
    so bằng `hmac.compare_digest` (hằng thời gian — so `==` thường rò thời gian xử lý,
    kênh side-channel kinh điển cho việc giả mạo chữ ký); (3) chưa quá `max_age_s` kể từ
    lúc ký — không có cửa sổ này, MỘT chữ ký hợp lệ từng lộ ra (log, network capture) dùng
    lại được mãi mãi (replay).
    """
    expected_payload = _canonical_payload(evidence.channel, evidence.channel_message_id,
                                          actor_id, evidence.timestamp)
    if not hmac.compare_digest(expected_payload, evidence.signed_payload):
        return False
    expected_sig = hmac.new(secret.encode("utf-8"), evidence.signed_payload.encode("utf-8"),
                            hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, evidence.signature):
        return False
    now = time.time() if now is None else now
    age = now - evidence.timestamp
    return 0 <= age <= max_age_s
