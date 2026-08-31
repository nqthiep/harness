"""T-9.3, docs/17-research-alignment.md M9 — một event model, nhiều transport.

"Không transport nào có semantics riêng": in-process (`Agent.stream()`, T-8.5) đã yield
`Event` thật. CLI/JSON (`TranscriptWriter`, JSONL) và SSE (`harness.server`, T-9.2) không
được tự xây hình dạng JSON riêng — cả hai gọi ĐÚNG hàm này.

Trước bản vá này, `TranscriptWriter.emit()` tự dựng `{"seq", "ts", "run_id", "kind",
"step", "data"}` — bốn trường envelope v1 (`schema_version`/`trace_id`/`tenant_id`/
`session_id`, T-8.1, ADR-048) bị ĐÁNH RƠI khi ghi transcript, dù `Event` mang chúng từ
lâu. Đúng loại lỗi T-9.3 tồn tại để bắt: một transport có semantics riêng của chính nó.
"""
from __future__ import annotations

from typing import Any

from .events import Event


def to_canonical_json(event: Event) -> dict[str, Any]:
    """Hình dạng JSON DUY NHẤT cho `Event` — không transport nào được thêm/bớt trường ở
    tầng NÀY. Một transport CÓ THỂ biến đổi `data` sau khi gọi hàm này (vd.
    `TranscriptWriter` thay `arguments` bằng `arguments_digest` — một phép redaction có
    chủ đích, register #24), nhưng envelope (`schema_version`…`step`) không đổi.
    """
    return {
        "schema_version": event.schema_version,
        "seq": event.seq,
        "ts": round(event.ts, 6),
        "trace_id": event.trace_id,
        "tenant_id": event.tenant_id,
        "session_id": event.session_id,
        "run_id": event.run_id,
        "kind": event.kind.value,
        "step": event.step,
        "data": dict(event.data),
    }
