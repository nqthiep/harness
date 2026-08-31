"""Taint tracking cho backend cổ điển — ADR-011, nâng cấp lên `Label` hai trục.

Sticky per run: hợp cả integrity lẫn confidentiality vào một `Label` duy nhất, không bao
giờ tính lại theo từng message. Đây KHÔNG phải giản lược tạm thời — nó là mô hình đúng cho
backend này, vì `run.py` (IDL-13, giới hạn 250 dòng) không có khái niệm "message rời khỏi
context giữa chừng một `run()`" mà backend LangGraph có (compaction nhiều bước, resume qua
checkpoint). Chính vì không tính lại theo message, mô hình sticky-per-run MIỄN NHIỄM với
kiểu tấn công mà `lg/runtime.py` phải phòng bằng L-1/L-2/L-3
(design/00-foundation.md §3.2, design/review-security.md S-19) — không có "nhãn của từng
message" nào để rửa ở đây. Cái giá of sự miễn nhiễm đó: một run bị coi UNTRUSTED vĩnh viễn
sau một lần `external`, đúng đánh đổi mà 00-foundation gọi là lý do backend LangGraph chọn
per-message thay vì sticky.
"""
from __future__ import annotations

from .label import Label


class TaintTracker:
    __slots__ = ("_label", "_source")

    def __init__(self) -> None:
        self._label = Label()
        self._source = ""

    @property
    def label(self) -> Label:
        return self._label

    @property
    def tainted(self) -> bool:
        """Tương thích ngược: `True` khi trục integrity đã UNTRUSTED."""
        from .label import Integrity
        return self._label.integrity is Integrity.UNTRUSTED

    @property
    def source(self) -> str:
        return self._source

    def raise_from(self, emitted: Label, source_tool: str) -> bool:
        """Hợp `emitted` vào nhãn hiện tại. Trả `True` chỉ lần ĐẦU nhãn thực sự đổi, để
        `taint.raised` chỉ phát một lần — giữ đúng ngữ nghĩa cũ của `raise_taint`."""
        before = self._label
        self._label = self._label.join(emitted)
        changed = self._label != before
        if changed and not self._source:
            self._source = source_tool
        return changed

    def raise_taint(self, source_tool: str) -> bool:
        """Tương thích ngược cho chỗ gọi cũ: nâng riêng trục integrity."""
        from .label import Integrity
        return self.raise_from(Label(Integrity.UNTRUSTED), source_tool)
