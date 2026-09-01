"""`ProgressLedger` — phát hiện agent đứng yên, bằng dữ liệu harness đã có, không tốn token.

**Vấn đề.** Một phiên chạy dài hỏng theo hai kiểu rất khác nhau. Kiểu thứ nhất — hết tiền,
hết bước, hết giờ — harness đã chặn từ đầu bằng `Budget`. Kiểu thứ hai tốn kém hơn nhiều:
agent *vẫn đang chạy*, vẫn gọi tool, vẫn tiêu tiền, nhưng lặp lại đúng những gì nó vừa làm
— đọc lại file vừa đọc, chạy lại đúng lệnh test vừa chạy — cho đến khi chạm trần ngân sách
300 bước sau. Trần ngân sách bắt được nó, nhưng bắt *muộn nhất có thể* và không nói được lý
do thật.

**Vì sao bản cơ học, không phải bản gọi model.** ADR-023 đã bác "self-critique loop": trả
tiền cho một lượt gọi model để hỏi "tôi có đang bế tắc không" là đúng thứ nó từ chối. Nhưng
harness KHÔNG cần hỏi model — nó đã thấy mọi lời gọi tool: tên, tham số, thứ tự. Bế tắc có
một chữ ký cơ học đọc được miễn phí: *không có lời gọi nào MỚI trong N bước liên tiếp*.

**Tiền lệ trong chính repo này.** Đây không phải một loài cơ chế mới:

* `MAX_PAUSES` (`run.py`) — model `pause_turn` quá nhiều lần liên tiếp thì dừng,
  "stopping rather than paying for a loop". Cùng hình dạng, khác tín hiệu.
* `max_asks_per_run` (S-25) — đếm cơ học, cắt khi vượt ngưỡng.
* Dedup `tool+args` (T-2.5) — chính xác cùng chữ ký này, nhưng chỉ TRONG một bước.
  `ProgressLedger` là T-2.5 nhìn qua nhiều bước.

**Quy tắc, viết đủ ngắn để cãi lại được.** Một bước bị tính là "không tiến triển" khi nó có
ít nhất một lời gọi tool và **mọi** lời gọi trong bước đó đều đã từng xuất hiện ở bước
trước. Chỉ cần một chữ ký mới là bộ đếm về 0. Vì sao quy tắc này không giết nhầm một agent
đang làm việc thật: một vòng sửa code bình thường là `write_source(file, nội-dung-mới)` rồi
`run_tests()`. `run_tests()` lặp lại y hệt mỗi vòng — nhưng `write_source` mang nội dung
khác nên là chữ ký mới, và bộ đếm về 0 mỗi vòng. Chỉ khi agent ngừng thay đổi thế giới VÀ
ngừng đọc cái gì chưa đọc thì nó mới đếm lên. Đó đúng là định nghĩa của đứng yên.

**Chữ ký được băm, không lưu nguyên văn.** `write_source` có thể mang cả nội dung một file;
giữ nguyên văn trong state sẽ phình checkpoint và đưa nội dung người dùng vào một chỗ thứ
hai không ai chờ đợi nó ở đó. Băm rồi cắt còn 16 ký tự hex: đủ để so sánh bằng nhau, không
đủ để đọc ngược ra.
"""
from __future__ import annotations

import hashlib
from typing import Any, Final, Mapping, Sequence

from .context.assembler import canonical

#: Số bước liên tiếp không có chữ ký mới thì coi là đứng yên. Sáu, không phải hai: một
#: agent có thể đọc lại cùng một file vài lần trong lúc suy nghĩ mà vẫn đang tiến. Sáu
#: bước liên tiếp KHÔNG có gì mới thì không còn cách đọc nào khác là bế tắc — và sáu bước
#: cũng đủ rẻ để dừng sớm hơn nhiều so với trần 300 bước.
STALL_AFTER: Final = 6

#: Chỉ nhớ ngần này chữ ký gần nhất. Một phiên 300 bước không được phép làm checkpoint
#: phình vô hạn. Hệ quả đã biết và chấp nhận: một chữ ký rơi ra khỏi cửa sổ rồi quay lại
#: sẽ được tính là "mới" — tức là cơ chế này thiên về BỎ SÓT hơn là giết nhầm, đúng hướng
#: an toàn cho một thứ có quyền dừng run của người khác.
MAX_TRACKED: Final = 512


def arguments_of(call: Mapping[str, Any]) -> Mapping[str, Any]:
    """Tham số của một lời gọi tool, dù nó đến từ backend nào.

    Hai backend gọi cùng một thứ bằng hai tên: khối `tool_use` của Anthropic dùng
    `input`, `ToolCall` của LangChain dùng `args`. Quy về một chỗ ở đây, để chữ ký của
    cùng một lời gọi không phụ thuộc vào backend đang chạy (R-17: hai bản sao của một
    định nghĩa là cách hai backend trôi khỏi nhau).
    """
    v = call.get("input")
    if v is None:
        v = call.get("args")
    return v or {}


def stall_reason(stalled_steps: int) -> str:
    """Câu giải thích, viết đúng MỘT lần cho cả hai backend — không kèm tên tool: tên đã
    nằm trong sự kiện `step.finished` và trong transcript, còn hai câu chữ khác nhau ở
    hai backend là đúng thứ `tests/test_parity.py` tồn tại để chặn."""
    return (f"đã {stalled_steps} bước liên tiếp không có lời gọi tool nào mới — dừng thay "
            f"vì trả tiền cho một vòng lặp không đi tới đâu")


def signature(name: str, arguments: Mapping[str, Any]) -> str:
    """Chữ ký của một lời gọi tool. Cùng công thức `tool+args` mà dedup T-2.5 dùng
    (`dispatch.py`), qua đúng `canonical()` mà phần còn lại của harness đã dùng để so
    sánh — nên hai chỗ không thể lệch định nghĩa "cùng một lời gọi"."""
    # Underscore-prefixed keys are stripped before a tool is invoked (`dispatch.py`,
    # `lg/runtime.py`), so two calls differing only there ARE the same call. Dropping
    # them HERE rather than at each call site is the point: counting them as different
    # would hand any caller a trivial way to look busy while standing still.
    args = {k: v for k, v in dict(arguments).items() if not str(k).startswith("_")}
    raw = f"{name}\x00{canonical(args)}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class ProgressLedger:
    """Sổ tiến triển của MỘT lượt chạy. Không giữ trạng thái nào ngoài hai thứ dưới đây,
    và cả hai đều JSON hoá được — để backend graph checkpoint được nó (IDL-47: không có
    trạng thái lượt chạy nào được sống trên `Runtime`).
    """

    def __init__(self, *, stall_after: int = STALL_AFTER,
                 seen: Sequence[str] = (), stalled_steps: int = 0) -> None:
        self._stall_after = stall_after
        self._seen: list[str] = list(seen)[-MAX_TRACKED:]
        self._stalled = stalled_steps

    @property
    def seen(self) -> list[str]:
        return list(self._seen)

    @property
    def stalled_steps(self) -> int:
        return self._stalled

    def observe(self, calls: Sequence[Mapping[str, Any]]) -> str | None:
        """Ghi nhận các lời gọi tool của một bước. Trả về `None` nếu còn tiến triển, hoặc
        một câu giải thích nếu đã đứng yên đủ lâu để nên dừng.

        Bước không gọi tool nào KHÔNG được tính: đó là model đang viết câu trả lời, và
        một lượt chạy như thế tự kết thúc ngay sau đó — đếm nó vào đây chỉ tạo dương tính
        giả.
        """
        if not calls:
            return None
        sigs = [signature(str(c.get("name", "")), arguments_of(c)) for c in calls]
        known = set(self._seen)
        if any(s not in known for s in sigs):
            self._stalled = 0
        else:
            self._stalled += 1
        for s in sigs:
            if s not in known:
                known.add(s)
                self._seen.append(s)
        del self._seen[:-MAX_TRACKED]
        if self._stalled >= self._stall_after:
            return stall_reason(self._stalled)
        return None
