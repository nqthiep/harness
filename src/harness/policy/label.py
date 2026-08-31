"""Label — nhãn hai chiều, design/00-foundation.md §3.2, design/02-safety-engine.md §4.1.

Nghiên cứu tìm được đúng MỘT cài đặt information-flow thật trong 30 gói, và nó hai chiều:
integrity (Biba — chống bị điều khiển) × confidentiality (Bell-LaPadula — chống rò rỉ).
Cài đặt đó — `agent_framework.security` của Microsoft — không được chính harness của họ
import (research/09 §16bis). Ở đây nó nằm trên đường đi bắt buộc.

`ADR-011` cũ của repo này chỉ có một trục (integrity, tên là `TaintTracker.tainted`, một
bool sticky suốt run). Vẫn còn ở `taint.py` cho backend cổ điển — sticky là ĐÚNG cho backend
đó vì nó không có khái niệm "message rời khỏi context" giữa chừng một `run()`. Backend
LangGraph thì có (compaction), nên nó cần nhãn PER-MESSAGE — xem `lg/runtime.py`.
"""
from __future__ import annotations

from enum import IntEnum

from .._value import value


class Integrity(IntEnum):
    """Biba — chống bị điều khiển."""
    TRUSTED = 0
    UNTRUSTED = 1


class Confidentiality(IntEnum):
    """Bell-LaPadula — chống rò rỉ."""
    PUBLIC = 0
    SECRET = 1


@value
class Label:
    """Bắt đầu TRUSTED + PUBLIC, chỉ tăng trong một lần `join`."""
    integrity: Integrity = Integrity.TRUSTED
    confidentiality: Confidentiality = Confidentiality.PUBLIC

    def join(self, other: "Label") -> "Label":
        """`max()` từng trục — cùng phép hợp thành với `Verdict` (policy/base.py). Đơn
        điệu TRONG một lần tính; nhãn hiệu dụng của một context vẫn có thể giảm khi tính
        lại trên một tập message khác (L-3, 00-foundation §3.2)."""
        return Label(Integrity(max(self.integrity, other.integrity)),
                     Confidentiality(max(self.confidentiality, other.confidentiality)))


@value
class Grants:
    """Cấu hình CHỈ operator đặt được — không bao giờ từ `@tool()`.

    Bản nháp đầu cho tác giả tool tự đặt `accepts_tainted=True` ngay trong decorator: một
    reviewer chỉ ra đó là đúng hình dạng công tắc `mode_set(approval_mode="never_require")`
    mà thiết kế này phê phán — diff một dòng, trong một tệp tool, tắt được nhánh DENY nguy
    hiểm nhất (design/review-security.md S-16). Ở đây hai trường này chỉ đến từ
    `Agent(...)`/`build_agent(...)`, keyed theo TÊN tool, resolve lúc bind chứ không lúc
    định nghĩa.

    `accepts_tainted`: tool `danger` này được phép chạy dù context đang UNTRUSTED.
    `sensitive`: output của tool này luôn coi là SECRET — nguồn thứ hai nâng trục
        confidentiality (nguồn thứ nhất, `Secret[T]` do người dùng đưa vào, chưa cài —
        xem design/07-risks-and-open-issues.md).
    """
    accepts_tainted: frozenset[str] = frozenset()
    sensitive: frozenset[str] = frozenset()
