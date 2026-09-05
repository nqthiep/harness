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

**Chữ ký chỉ tính trên tham số TOOL ĐÃ KHAI, không phải mọi khoá model gửi lên — sửa sau
review đối kháng (G-6, `design/review-architect.md`).** Bản gốc chỉ lọc khoá bắt đầu bằng
`_`; bất kỳ khoá KHÁC nào model tự thêm (một timestamp, một số đếm tăng dần) vẫn lọt vào
`canonical(args)`, nên MỖI lời gọi hash khác nhau dù cùng tool cùng ý định — bộ đếm không
bao giờ tăng, kể cả khi không có tiến triển thật. Không cần model "cố tình lách": chính
`dispatch.py` đã tự bỏ mọi khoá tool KHÔNG khai trước khi gọi `spec.fn(**kwargs)`, nên một
khoá lạ khiến MỌI lời gọi đó THẤT BẠI (`TypeError`) — đúng trạng thái bế tắc rõ ràng nhất
lại là trạng thái bộ đếm này đọc thành "đang tiến triển". Chữ ký giờ lọc xuống đúng tập
khoá `ToolSpec.input_schema["properties"]` đã khai cho tool đó — một khoá model tự thêm mà
tool không khai không còn đổi được chữ ký.
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


def schema_of(toolset: "Any") -> "dict[str, frozenset[str]]":
    """`{tool name -> declared parameter names}`, dùng làm `schema_of` cho `observe()`
    (G-6). Một hàm dùng chung thay vì để `run.py`/`lg/runtime.py` mỗi bên tự viết lại —
    cùng lý do `signature()`/`canonical()` đã dùng chung: hai chỗ không thể lệch định
    nghĩa. Rẻ để gọi lại mỗi bước (`toolset` thường vài chục tool là cùng); không cache ở
    đây để không thêm một trường trạng thái nào phải theo dõi vòng đời của chính nó."""
    return {t.name: frozenset(t.input_schema.get("properties", {})) for t in toolset}


def signature(name: str, arguments: Mapping[str, Any], *,
             declared_keys: "frozenset[str] | None" = None) -> str:
    """Chữ ký của một lời gọi tool. Cùng công thức `tool+args` mà dedup T-2.5 dùng
    (`dispatch.py`), qua đúng `canonical()` mà phần còn lại của harness đã dùng để so
    sánh — nên hai chỗ không thể lệch định nghĩa "cùng một lời gọi".

    `declared_keys`, khi có (G-6): chỉ giữ lại khoá tool ĐÃ KHAI trong
    `ToolSpec.input_schema["properties"]` — một khoá model tự thêm mà tool không khai
    (`dispatch.py` cũng bỏ nó trước khi gọi `spec.fn`, nên nó không bao giờ ảnh hưởng kết
    quả thật) không còn đổi được chữ ký. `None` (tool không xác định được, ví dụ tên tool
    lạ) giữ hành vi cũ: dùng mọi khoá — bỏ sót còn hơn giết nhầm, đúng hướng an toàn
    module này đã chọn cho `MAX_TRACKED`.
    """
    # Underscore-prefixed keys are stripped before a tool is invoked (`dispatch.py`,
    # `lg/runtime.py`), so two calls differing only there ARE the same call. Dropping
    # them HERE rather than at each call site is the point: counting them as different
    # would hand any caller a trivial way to look busy while standing still.
    args = {k: v for k, v in dict(arguments).items() if not str(k).startswith("_")}
    if declared_keys is not None:
        args = {k: v for k, v in args.items() if k in declared_keys}
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

    def observe(self, calls: Sequence[Mapping[str, Any]],
               schema_of: "Mapping[str, frozenset[str]] | None" = None) -> str | None:
        """Ghi nhận các lời gọi tool của một bước. Trả về `None` nếu còn tiến triển, hoặc
        một câu giải thích nếu đã đứng yên đủ lâu để nên dừng.

        Bước không gọi tool nào KHÔNG được tính: đó là model đang viết câu trả lời, và
        một lượt chạy như thế tự kết thúc ngay sau đó — đếm nó vào đây chỉ tạo dương tính
        giả.

        `schema_of` (G-6): tên tool -> tập khoá tham số nó đã khai
        (`ToolSpec.input_schema["properties"]`). Tool không có trong bảng (tên lạ, hoặc
        gọi tại chỗ chưa xây được bảng) dùng mọi khoá — hành vi cũ, an toàn theo hướng bỏ
        sót hơn giết nhầm.
        """
        if not calls:
            return None
        sigs = [signature(str(c.get("name", "")), arguments_of(c),
                          declared_keys=(schema_of or {}).get(str(c.get("name", ""))))
               for c in calls]
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
