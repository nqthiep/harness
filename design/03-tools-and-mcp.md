# 03 — Tools và MCP

**Tệp này tuân theo [`00-foundation.md`](00-foundation.md).** Từ vựng, `Effect`, hai
lattice, `Decision`/`Scope` lấy nguyên ở đó, không định nghĩa lại và không đổi tên.
Mọi quyết định dưới đây trích dẫn một phát hiện cụ thể trong [`research/`](../research/).

Tệp này trả lời năm câu hỏi: một tool được khai báo thế nào, lỗi của nó đi về đâu, cái gì
được chạy song song, làm sao retry một side effect mà không nhân đôi nó, và tool của người
lạ (MCP) được phân loại ra sao.

---

## 1. `ToolSpec` — người viết tool khai đúng một thứ

### 1.1 Signature

```python
from collections.abc import Awaitable, Callable, Mapping
from enum import Enum
from typing import Any, Final, NewType

from harness._value import value
from harness.policy.base import Verdict

ToolName = NewType("ToolName", str)
ServerLabel = NewType("ServerLabel", str)
IdempotencyKey = NewType("IdempotencyKey", str)


class Effect(str, Enum):
    READ     = "read"
    WRITE    = "write"
    EXTERNAL = "external"
    DANGER   = "danger"


# Không có IdempotencyMode. Effect log LUÔN bật cho `write` và `danger` — xem §4.5.
# Thứ duy nhất còn phải khai là upstream có nhận key hay không.


@value
class ToolSpec:
    __hash__ = None                      # giữ Mapping — không hashable

    name: ToolName
    description: str
    input_schema: Mapping[str, Any]
    fn: Callable[[Mapping[str, Any], "ToolCtx"], Awaitable[Any]]

    effect: Effect                       # ← THỨ DUY NHẤT người viết tool phải nghĩ

    # Ba trường còn lại KHÔNG phải là hành vi; chúng là dữ kiện mà runtime không suy ra được.
    accepts_tainted: bool = False        # 00-foundation §3.2 — chỉ operator/tác giả local được đặt
    upstream_accepts_key: bool = False   # chỉ có nghĩa khi effect ∈ {WRITE, DANGER}
    server: ServerLabel | None = None    # None = tool local; có = tool MCP, xem §5
    timeout_s: float = 30.0
```

**Bất biến T-1: `ToolSpec` không có trường nào cho phép ghi đè năm hành vi dẫn xuất.**
Không có `parallel_safe`, không có `handle_tool_error`, không có `approval_mode`, không có
`sequential`. Muốn đổi hành vi thì đổi `effect`, và đổi `effect` là một thay đổi mà reviewer
nhìn thấy trong diff bằng một từ.

### 1.2 Bảng dẫn xuất — bản dùng được của [00-foundation §2]

```python
@value
class EffectProfile:
    parallel_safe: bool
    model_may_retry: bool        # runtime được trả lỗi về cho model để nó thử lại
    runtime_auto_retry: bool     # runtime được tự gọi lại, IM LẶNG, không hỏi model
    taints_output: bool
    default_verdict: Verdict
    audit_level: str
    on_unexpected: "ToolOutcome"  # §2


#: Hằng số module, KHÔNG phải cấu hình. Một người sửa được bảng này là một người tắt
#: được barrier và taint — 00-foundation R-3.
EFFECT_PROFILES: Final[Mapping[Effect, EffectProfile]] = {...}
```

| effect | song song | model retry | runtime tự retry | taint output | verdict mặc định | audit | exception lạ |
|---|---|---|---|---|---|---|---|
| `read` | ✅ | ✅ | ✅ (chỉ lỗi transport, ≤2) | không | `ALLOW` | `debug` | `FAILED` |
| `write` | ❌ barrier | ✅ (key luôn có) | ❌ | không | `ASK` | `info` | `FAILED` |
| `external` | ✅ | ✅ | ❌ | **có** | `ALLOW` | `info` | `FAILED` |
| `danger` | ❌ barrier | ❌ | ❌ | không | `ASK` | **`audit`** | `FATAL` |

**Một làm rõ, không phải một thay đổi.** [00-foundation §2] ghi `external` là "retry được".
Điều đó đúng ở nghĩa *model được phép thử lại* — run không chết vì một lần fetch hỏng. Nó
**không** có nghĩa runtime được tự gọi lại im lặng: nghiên cứu nói thẳng *"an `external` tool
that fails may have already had an effect and must not be retried blindly"*
([§08](../research/08-tool-mcp-plugin.md) §8.2). Vì vậy hai cột `model_may_retry` và
`runtime_auto_retry` tách ra, và chỉ `read` có cột thứ hai bằng `True`.

### 1.3 Decorator — thiếu `effect` là lỗi lúc import

```python
def tool(
    *,
    effect: Effect | str,                    # keyword-only, KHÔNG có giá trị mặc định
    name: str | None = None,
    accepts_tainted: bool = False,
    upstream_accepts_key: bool = False,
    timeout_s: float = 30.0,
) -> Callable[[Callable[..., Awaitable[Any]]], ToolSpec]: ...
```

`effect` không có default, nên quên nó là `TypeError` lúc định nghĩa tool — không phải một
mặc định im lặng phát hiện được sau khi triển khai. Đây chính là chỗ Microsoft trượt: quyết
định an toàn của họ nằm trong đối số decorator **có mặc định**, nên `@tool(name="mode_set",
approval_mode="never_require")` trông giống mọi tool khác trong review
([§09](../research/09-memory-context-multiagent-hitl.md) §14.2). Ở đây, công tắc đổi chế độ
an toàn không thể tồn tại: nó là `danger` theo cấu tạo, và [00-foundation §R-3] cấm mọi tool
do model gọi ghi `Decision`, nới policy hay sửa `Ledger`.

### 1.4 Một trục hợp nhất ba ý tưởng rời rạc

Nghiên cứu tìm thấy ba mảnh của cùng một khái niệm, ở ba dự án, không mảnh nào biết mảnh nào:

| mảnh rời rạc | phủ được gì | bỏ sót gì | `Effect` hợp nhất thế nào |
|---|---|---|---|
| pydantic-ai `sequential: bool` ([§08](../research/08-tool-mcp-plugin.md) §8.3) | barrier khi chạy song song — *"runs alone... other tools still run in parallel around it"* | không nói gì về approval, retry, taint; và là **caller tự khẳng định từng tool** | `parallel_safe` là cột dẫn xuất, §3 |
| pydantic-ai `ToolKind = function \| output \| external` ([§08](../research/08-tool-mcp-plugin.md) §8.3) | `external` = kết quả do dịch vụ/người ngoài sinh ra | là phân loại **đường đi của giá trị**, không phải phân loại an toàn | giữ nguyên tên `external`, gắn thêm nghĩa taint |
| MS `disable_readonly_tool_approval` / `disable_write_tool_approval` ([§09](../research/09-memory-context-multiagent-hitl.md) §14.2) | read và write có gate riêng — đúng trục, đúng chỗ | chỉ hai lớp, và chỉ dùng cho approval | `default_verdict` là cột dẫn xuất, bốn lớp |
| MCP `readOnlyHint` / `destructiveHint` ([§08](../research/08-tool-mcp-plugin.md) §9) | từ vựng mô tả tác dụng cho tool bên thứ ba | tự nhận là *hint*, "clients should never make tool use decisions based on" chúng | chỉ được làm **mặc định** cho server đã tin cậy, §5 |

Cả bốn đang mô tả cùng một câu hỏi — *tool này làm gì với thế giới* — bằng bốn từ vựng.
Harness này hỏi câu đó **một lần**, và mọi thứ khác là hệ quả.

---

## 2. Taxonomy lỗi tool — học pydantic-ai, nhưng suy ra từ effect

### 2.1 Thiết kế tốt nhất tìm được, và vì sao nó vẫn chưa đủ

pydantic-ai là dự án duy nhất trong nghiên cứu có taxonomy ba nhánh
([§08](../research/08-tool-mcp-plugin.md) §8.2):

| raise | model thấy | prompt sửa lỗi | tiêu retry budget | run tiếp |
|---|---|---|---|---|
| `ModelRetry` | có | có | **có** | có |
| `ToolFailed` | có | không | **không** | có |
| còn lại | không | — | — | **không, run kết thúc** |

Phân biệt retry budget là phần tinh tế và nó đúng: đối số sai phải đốt hạn mức sửa lỗi
(model đang đoán, phải chặn nó đoán mãi); một 404 dứt điểm thì không (retry không giúp được
gì, model cần *thích nghi*), và `ToolFailed` lặp lại bị chặn ở mức run bằng `UsageLimits`.

**Khuyết điểm còn lại: người viết tool phải tự chọn đúng nhánh.** Và nghiên cứu có bằng
chứng trực tiếp rằng lựa chọn per-tool thì người ta chọn sai: LangChain đặt
`handle_tool_error: ... = False` làm mặc định, nghĩa là **một tool ném exception giết cả
run**, và chỉ hồi phục được nếu tác giả tool nhớ opt-in cho đúng tool đó
([§08](../research/08-tool-mcp-plugin.md) §8.2). Kết luận của chính nghiên cứu:

> *"neither default is right for every tool, which means the choice belongs to the tool's
> classification, not to a flag on each tool."*

### 2.2 Thiết kế ở đây: tác giả mô tả **sự việc**, runtime chọn **chính sách**

Hai exception, và tên của chúng nói *cái gì đã xảy ra*, không nói *runtime nên làm gì*. Đây
là khác biệt so với `ModelRetry` — cái tên `ModelRetry` mời tác giả tool ra quyết định chính
sách, mà đó không phải việc của tác giả tool.

```python
class ToolInputInvalid(HarnessError):
    """Lời gọi sai và sửa được: thiếu trường, sai kiểu, giá trị ngoài miền.

    Hợp đồng: khi raise cái này, tool CHƯA gây side effect nào.
    """
    def __init__(self, message: str, *, fix: str | None = None) -> None: ...


class ToolUnavailable(HarnessError):
    """Thao tác đã kết thúc và thất bại dứt điểm: 404, quyền bị từ chối, tính năng không có.

    Retry không giúp được gì; model cần thích nghi.
    """


class ToolOutcome(Enum):
    RETRY  = "retry"   # model thấy + prompt sửa + TIÊU retry budget
    FAILED = "failed"  # model thấy, không prompt sửa, KHÔNG tiêu retry budget
    FATAL  = "fatal"   # run kết thúc
```

Hàm phân loại — toàn bộ chính sách lỗi nằm ở đây, và nó chỉ đọc `effect`:

```python
def classify(exc: BaseException, spec: ToolSpec) -> ToolOutcome:
    """Chính sách lỗi SUY RA từ effect class, không phải từ cờ per-tool."""
```

| tình huống | `read` | `external` | `write` | `danger` |
|---|---|---|---|---|
| `ToolInputInvalid` | `RETRY` | `RETRY` | `RETRY` | `RETRY` |
| `ToolUnavailable` | `FAILED` | `FAILED` | `FAILED` | `FAILED` |
| timeout (`timeout_s`) | tự retry ≤2 rồi `FAILED` | `FAILED` | `FAILED` nếu keyed, `FATAL` nếu không | `FATAL` |
| exception khác | `FAILED` | `FAILED` | `FAILED` nếu keyed, `FATAL` nếu không | `FATAL` |
| `CancelledError`, `KeyboardInterrupt` | **không bao giờ phân loại — ném tiếp**, §6 | | | |

Bốn điểm đáng nói:

1. **`ToolInputInvalid` luôn là `RETRY` với mọi effect**, kể cả `danger`, vì hợp đồng của
   nó là chưa có side effect. Với `write` dùng `KEYED`, runtime **kiểm tra được** hợp đồng
   này: nếu effect log đã có bản ghi `committed` cho key của lời gọi đó thì tool đang nói
   dối và outcome hạ xuống `FATAL` (§4).
2. **`write` không có key thì exception lạ là `FATAL`.** Lý do là điều duy nhất trung thực
   được: runtime *không biết* side effect đã xảy ra hay chưa. Trả lỗi cho model là mời nó
   gọi lại — đúng con đường sinh ra double effect mà [§03](../research/03-safety-reliability.md)
   §15 gọi là lỗ hổng của cả ngành. Đây cũng là ưu đãi thiết kế: bật idempotency thì *được*
   một run bền hơn, và cái giá của việc không bật là nhìn thấy được.
3. **`danger` là `FATAL` cho mọi bất ngờ.** Một thao tác không hoàn tác được mà lỗi bất thường
   thì thứ cần dừng là run, không phải là lượt tiếp theo.
4. **Retry budget** tiêu bởi `RETRY`, đếm theo `(run_id, tool)`, mặc định 3. `FAILED` không
   tiêu gì — nhưng mỗi tool call vẫn ghi vào `Ledger` ([00-foundation §6]), nên `FAILED` lặp
   vô hạn bị chặn ở mức run bằng cả **bước lẫn tiền**. Đây mạnh hơn `UsageLimits` của
   pydantic-ai một bậc, vì [§03](../research/03-safety-reliability.md) §20 cho thấy ngành có
   loop limit nhưng gần như không có spend ceiling, và "một vòng với 200k token input đắt gấp
   trăm lần một vòng ngắn".

**Sửa khuyết điểm gì:** không có trường `handle_tool_error` trên `ToolSpec`, nên không tồn tại
lựa chọn per-tool để chọn sai. Giữ nguyên credit cho LangChain ở chỗ họ làm đúng: họ bắt
`KeyboardInterrupt` rồi **ném lại** thay vì nuốt ([§08](../research/08-tool-mcp-plugin.md)
§8.2); ở đây điều đó thành luật, xem §6.

---

## 3. Song song và barrier — suy ra, không khai báo

### 3.1 Quy tắc

Một lượt model sinh ra `n` tool call. Executor cắt danh sách đó thành các **segment**, giữ
nguyên thứ tự model phát ra:

```python
def segments(
    calls: Sequence[ToolCall],
    specs: Mapping[ToolName, ToolSpec],
) -> list[list[ToolCall]]:
    """Segment chạy tuần tự; trong một segment thì chạy song song.

    Một call có EFFECT_PROFILES[effect].parallel_safe == False nằm MỘT MÌNH trong
    segment của nó — đó là barrier.
    """
```

Đây đúng là primitive của pydantic-ai (chế độ `'exhaustive'`: *"run every tool in parallel,
segmented only by `sequential=True` barriers"* — [§08](../research/08-tool-mcp-plugin.md)
§8.3), nhưng nguồn của barrier khác: ở đó là **cờ do người gọi tự khẳng định từng tool**, ở
đây là hệ quả của `effect`.

### 3.2 Chỉ một chế độ, và đó là chủ ý

pydantic-ai có ba chế độ run-level (`'parallel'`, `'sequential'`, `'exhaustive'`). Harness này
có **một**. Lý do: một núm chỉnh được sang `'parallel'` là một núm xoá được barrier, tức là
một công tắc an toàn — [00-foundation §R-3] cấm. Núm duy nhất còn lại là
`max_concurrency: int` cho mỗi run (lấy từ constructor của pydantic-ai,
[§02](../research/02-api-comparison.md) §6), và đó là giới hạn tài nguyên chứ không phải giới
hạn an toàn: hạ nó xuống 1 thì chạy chậm hơn, không mất bảo đảm nào.

### 3.3 Sửa khuyết điểm nào

LangChain phải viết tay một guard riêng cho đúng một tool —
`middleware/todo.py:289`, *"Check for parallel `write_todos` tool calls and return errors if
detected"* ([§08](../research/08-tool-mcp-plugin.md) §8.3). Nghiên cứu nói rõ cái giá:
*"the guard exists only where someone was bitten, and the twentieth tool has no guard at all."*

Ở đây `write_todos` là `effect="write"`, nên nó là barrier từ dòng khai báo, và tool thứ hai
mươi cũng vậy — không ai phải bị cắn trước.

### 3.4 Song song và taint

Nhiều call `external` trong cùng một segment cùng làm nhiễm context. Nhãn hợp thành đơn điệu
([00-foundation §3.2]) nên thứ tự hoàn thành không đổi kết quả. Điều khiến việc này an toàn
dưới concurrency là chỗ **cất** nhãn: state đã checkpoint của thread, không phải
`threading.local()`. Microsoft đặt middleware vào `threading.local()` rồi set xuyên qua
`await`, và hai tool call đồng thời đọc nhầm slot của nhau — im lặng, và fail-open
([§09](../research/09-memory-context-multiagent-hitl.md) §16bis). Đó là một defect *của
concurrency*, nên nó thuộc về mục này chứ không chỉ thuộc về mục taint.

---

## 4. Idempotency ở mức tool call — chỗ không ai có

### 4.1 Trạng thái nghiên cứu

- **Không gói nào trong 16 gói có idempotency ở mức tool call** — exactly-once cho một tool
  đã gửi email hoặc đã ghi database ([§07](../research/07-remaining-python.md) §ĐÍNH CHÍNH).
- Cái gần nhất là `CachePolicy` của LangGraph, và *caching không phải exactly-once*
  ([§03](../research/03-safety-reliability.md) §15).
- **agno có, nhưng ở mức run submission**, tầng platform: partial-unique index trong DB, bắt
  `IntegrityError` rồi **đọc lại để trả về bên thắng**, và cố ý **không nuốt** lỗi thật —
  *"Swallowing it as 'duplicate' would 202 a run that was never enqueued"*
  ([§07](../research/07-remaining-python.md) §ĐÍNH CHÍNH).

Thiết kế dưới đây lấy nguyên ba chi tiết kỹ thuật của agno và nâng phạm vi từ *run* xuống
*tool call*.

### 4.2 Ai sinh key

**Runtime, không phải model, không phải tác giả tool.** Model sinh key sẽ tự cấp cho mình
quyền "gọi lại như thể chưa gọi" (đặt key mới) hoặc quyền "chặn một lời gọi hợp lệ" (dùng lại
key cũ) — cả hai đều là công tắc an toàn trong tay model, [00-foundation §R-3].

```python
def call_key(
    run_id: RunId,
    call_id: CallId,
    tool: ToolName,
    args: Mapping[str, Any],
) -> IdempotencyKey:
    """blake2b(run_id ‖ call_id ‖ tool ‖ canonical_json(args)) → 128-bit hex.

    `call_id` do runtime gán và nằm trong state đã checkpoint, nên nó ỔN ĐỊNH qua resume:
    replay cùng một checkpoint sinh lại đúng cùng một key.  `args` nằm trong key để một
    replay bị sửa đối số không im lặng nhận kết quả của lời gọi cũ.
    """
```

`canonical_json` = khoá sắp xếp, không khoảng trắng, số chuẩn hoá. Không dùng `pickle` — đó
là cách LangGraph băm cache key, và nó có hàm ý bảo mật riêng khi input đến từ nguồn không tin
cậy ([§03](../research/03-safety-reliability.md) §15).

### 4.3 Lưu ở đâu

Một bảng `effect_log`, **cùng store với checkpoint** — để nó có đúng bảo đảm bền vững ấy, và
để resume đọc được nó bằng cùng một kết nối:

```sql
CREATE TABLE effect_log (
    key         TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    tool        TEXT NOT NULL,
    state       TEXT NOT NULL,          -- 'in_flight' | 'committed' | 'failed'
    result      BLOB,                   -- chỉ khi state='committed'
    error       TEXT,
    started_at  TEXT NOT NULL,
    ended_at    TEXT
);

-- Partial-unique index — học agno.  Một key chỉ được "sống" một lần; bản ghi 'failed'
-- (tool chưa gây side effect) trả key về cho lần thử hợp lệ tiếp theo.
CREATE UNIQUE INDEX effect_log_live ON effect_log(key) WHERE state <> 'failed';
```

### 4.4 Giao thức ba pha

```python
async def call_with_effect_log(
    spec: ToolSpec, args: Mapping[str, Any], ctx: "ToolCtx", log: "EffectLog",
) -> Any: ...
```

1. **Claim.** `INSERT (key, state='in_flight')`.
   - `IntegrityError` → **không nuốt**; đọc lại hàng và trả về bên thắng, đúng cách agno xử
     lý race ([§07](../research/07-remaining-python.md) §ĐÍNH CHÍNH):
     - bên thắng `committed` → **không gọi `fn`**, trả kết quả đã lưu, ghi audit
       `duplicate_suppressed`.
     - bên thắng `in_flight` → có một lần thực thi đang treo hoặc đã chết giữa chừng. Runtime
       *không biết* side effect đã xảy ra chưa, nên nó **không chạy lại**: raise
       `AmbiguousEffect` → `FATAL`, ghi audit mức `audit`, và để nguyên hàng `in_flight` cho
       con người xử lý. Fail-closed.
2. **Execute.** Gọi `fn`. `ctx.idempotency_key` luôn có mặt cho `write`/`danger`; khi
   `upstream_accepts_key=True`, tool có nghĩa vụ gắn nó lên upstream (`Idempotency-Key`
   header, `client_reference_id`, …).
3. **Commit.** `UPDATE state='committed', result=...`, **trước khi** checkpoint ghi tool
   result.
   - Nếu `fn` raise `ToolInputInvalid` (hợp đồng: chưa có side effect) → `UPDATE
     state='failed'`, key được giải phóng.
   - **Mọi exception khác giữ nguyên `in_flight`.** Đây là điểm quan trọng nhất của mục này:
     không biết thì không giải phóng.

**Bất biến I-1 (thứ tự):** effect log ghi **trước** khi tool chạy; checkpoint ghi **sau** khi
tool xong. Nên mọi cửa sổ hỏng đều nghiêng về phía an toàn: crash sau khi commit effect log mà
trước khi checkpoint → resume sinh lại đúng key, gặp `committed`, trả kết quả đã lưu, không
chạy lại. Đảo thứ tự là mất tính chất đó.

### 4.5 Nói thật về việc đạt được gì

| effect | harness đảm bảo | cần gì từ upstream |
|---|---|---|
| `read`, `external` | — (lặp lại không sinh tác dụng mới, hoặc tác dụng nằm ngoài tầm) | — |
| `write`, `danger` mặc định | **at-most-once**: không lần retry nào của harness làm side effect lần hai | không gì |
| `write`, `danger` + `upstream_accepts_key=True` | **exactly-once**, trong phạm vi upstream tôn trọng key | upstream nhận idempotency key |

**Vì sao không có mức "tắt".** Bản nháp đầu có `IdempotencyMode.NONE` làm mặc định, và một
reviewer chỉ ra đó chính là lớp lỗi mà bản thiết kế này đang chê ở nơi khác: khuyết điểm #5
chỉ được sửa cho ai nhớ opt-in, y như `handle_tool_error` per-tool của LangChain và module
security không được wire của Microsoft ([review-kiss.md](review-kiss.md) K-25). Effect log
giờ luôn bật cho `write`/`danger`; enum ba giá trị rút còn một `bool`. Thay đổi này làm thiết
kế **vừa đơn giản hơn vừa đúng lời hứa hơn**.

Harness một mình không thể hứa exactly-once: nếu tiến trình chết đúng giữa lúc HTTP request
đang bay, không tồn tại bản ghi cục bộ nào phân biệt được "đã tới" với "chưa tới". `KEYED`
biến điều đó thành một run dừng lại và một hàng `in_flight` nhìn thấy được, thay vì một email
gửi hai lần. Đó là toàn bộ lời hứa, và nó vẫn nhiều hơn cái mà 16 gói đã đọc cung cấp
([§07](../research/07-remaining-python.md) §ĐÍNH CHÍNH).

---

## 4bis. `Sandbox` và `Workspace` — cưỡng chế được cái gì, và KHÔNG cưỡng chế được cái gì

`Workspace` xuất hiện trong mọi ví dụ từ Mức 2 và là tham số bắt buộc khi bộ tool có
`write`/`external`/`danger`. Bản nháp đầu bắt buộc nó mà không đặc tả nó một dòng nào
([review-kiss.md](review-kiss.md) K-26) — tức là hứa cách ly mà không nói cách ly cái gì.
Đó chính là lỗi của Goose: bốn permission mode, sandbox seatbelt đã bị gỡ, và tool vẫn chạy
với quyền của user ([§05](../research/05-ideal-harness.md) §31-8).

```python
class Sandbox(Protocol):
    """Biên cưỡng chế cho tool local. Tool MCP không đi qua đây — xem §5."""
    async def open(self, run_id: RunId) -> SandboxHandle: ...

@value
class Workspace(Sandbox):
    root: Path                      # thư mục duy nhất tool được đọc/ghi
    egress: Egress = Egress.DENY    # DENY | Allowlist(hosts)
    max_bytes: int = 256 * 1024 * 1024
    max_procs: int = 0              # 0 = không cho spawn tiến trình con
```

### Cưỡng chế ở đâu

| bảo đảm | cưỡng chế bằng | mức |
|---|---|---|
| tool không đọc/ghi ngoài `root` | resolve path rồi so `is_relative_to(root)`; từ chối segment `.`/`..`; từ chối symlink/reparse point **trước khi mở** | **Prevent** |
| tool không mở kết nối ra ngoài `egress` | HTTP client tiêm sẵn trong `ToolCtx`, bind vào allowlist; DNS phân giải trước rồi pin IP | **Prevent** cho tool dùng client được tiêm |
| tool không spawn tiến trình | `max_procs=0` cưỡng chế ở tầng tiến trình khi có (`RLIMIT_NPROC`, job object) | **Detect** ở nơi không có |
| tool không ghi quá `max_bytes` | kiểm tra sau mỗi `write`, huỷ run khi vượt | **Detect** |

Luật path học đúng chỗ Microsoft làm đúng: từ chối segment `.`/`..` **và** kiểm tra
symlink/junction trước khi mở, chứ không chỉ `resolve()` một lần
([§09](../research/09-memory-context-multiagent-hitl.md) §14.2).

### KHÔNG được bảo đảm — đọc kỹ mục này

Đây là mục quan trọng nhất, vì Goose sai chính ở chỗ hứa nhiều hơn cưỡng chế được.

1. **`Workspace` không phải biên bảo mật ở mức OS.** Nó là biên trong tiến trình. Một tool
   Python cố ý độc hại gọi thẳng `open()` hay `socket()` thay vì client được tiêm thì
   `Workspace` **không chặn được**. Nó chống *tool viết ẩu* và *model bị injection*, không
   chống *tác giả tool thù địch*.
2. **Muốn chống tác giả tool thù địch thì cần biên tiến trình** — container, gVisor, seccomp,
   hoặc Firecracker. Harness định nghĩa `Sandbox` là `Protocol` đúng để cắm cái đó vào;
   `Workspace` là cài đặt mặc định, không phải cài đặt duy nhất.
3. **`egress` không chặn được exfiltration qua tool được phép.** Nếu allowlist cho phép
   `docs.python.org`, một tool có thể nhét dữ liệu vào query string tới host đó. Chống rò rỉ
   là việc của trục `confidentiality` trong lattice ([00 §3.2](00-foundation.md)), không phải
   của `egress`. Hai cơ chế khác nhau, đừng nhầm cái này bảo vệ cái kia.
4. **Tool MCP không đi qua `Workspace`** — chúng chạy ở tiến trình/host khác. Biên cho chúng
   là `ServerIdentity` + policy của operator ở §5.

Nói thẳng bốn điều này quan trọng hơn thêm cơ chế thứ năm: một bảo đảm mà người dùng *tưởng*
mình có là nguy hiểm hơn một bảo đảm họ biết là không có.

## 5. MCP — phân loại tool của người lạ

### 5.1 Ràng buộc từ chính giao thức

`ToolAnnotations` của `mcp_types` 2.1.1 tự nói về mình
([§08](../research/08-tool-mcp-plugin.md) §9):

> *"all properties in ToolAnnotations are **hints**. They are not guaranteed to provide a
> faithful description of tool behavior... Clients should never make tool use decisions based
> on ToolAnnotations received from untrusted servers."*

Một annotation là **lời tự khai chưa kiểm chứng của chính bên đang bị quản lý**. Kết luận của
nghiên cứu là mệnh lệnh cho mục này:

> *"A harness must classify third-party tools itself — from an operator-controlled policy keyed
> to the server's identity, with the MCP hints used at most as a default for servers already
> trusted, and never as the decision."*

### 5.2 Policy do operator giữ, khoá theo **danh tính** server

```python
@value
class ServerIdentity:
    """Danh tính, không phải cái tên.

    Microsoft có `server_label` — phòng thủ confused-deputy duy nhất tìm thấy trong cả
    nghiên cứu ([§09] §14).  Nhưng label là một chuỗi: trỏ lại cùng một label sang endpoint
    khác thì mọi grant cũ đi theo.  `fingerprint` khoá điều đó lại.
    """
    label: ServerLabel
    fingerprint: str          # sha256(transport ‖ URL chuẩn hoá) hoặc TLS SPKI pin


@value
class McpServerPolicy:
    identity: ServerIdentity
    trusted: bool = False                               # hint có được dùng làm mặc định không
    default_effect: Effect = Effect.DANGER              # dùng khi không tin, hoặc không có hint
    effects: Mapping[ToolName, Effect] = MappingProxyType({})     # operator ghi đè từng tool
    accepts_tainted: frozenset[ToolName] = frozenset()  # CHỈ operator — không bao giờ từ hint
    allow: frozenset[ToolName] | None = None            # None = mọi tool server công bố


def classify_mcp_tool(tool: "McpTool", policy: McpServerPolicy) -> ToolSpec:
    """Sinh ToolSpec cho một tool bên thứ ba.  Thứ tự ưu tiên: policy > hint > default."""
```

### 5.3 Bốn luật

**M-1. Server không nằm trong danh sách tin cậy của operator → annotation không tham gia phân
loại.** Đọc để ghi log, hết. `effect = policy.effects.get(name, policy.default_effect)`, mặc
định `DANGER`. Đây đúng là polarity mà chính spec MCP chọn — thiếu annotation nghĩa là
*destructive*, *open-world*, *non-idempotent* — và nghiên cứu ghi nhận nó đáng chép lại:
*"the spec fails closed on every axis... that is the opposite of how most optional metadata is
designed"* ([§08](../research/08-tool-mcp-plugin.md) §9). Cái giá về ergonomics là thật, và
lối thoát là `policy.effects`: operator hạ từng tool xuống, bằng tay, một lần.

**M-2. Server tin cậy → hint làm MẶC ĐỊNH, ánh xạ fail-closed.** Ghi đè trong `policy.effects`
luôn thắng hint.

```python
def _effect_from_hints(ann: "ToolAnnotations | None") -> Effect:
    """Fail-closed như Microsoft `_map_mcp_annotations_to_labels`."""
    if ann is None:
        return Effect.DANGER
    if ann.read_only_hint is True:                       # CHỈ True, không phải "khác False"
        return Effect.READ if ann.open_world_hint is False else Effect.EXTERNAL
    if ann.destructive_hint is False:
        return Effect.WRITE
    return Effect.DANGER
```

Chỗ `is True` là bài học đắt của Microsoft, chép lại nguyên: *"real-world servers (e.g.
GitHub's MCP) declare `readOnlyHint=True` on read tools but leave the field unset on write
tools, so a strict `readOnlyHint=False` check would miss them"*
([§08](../research/08-tool-mcp-plugin.md) §9). Mọi giá trị **khác `True`** — false *hoặc
thiếu* — là write sink.

Một chỗ đi xa hơn Microsoft: `read_only_hint=True` + `open_world_hint` không phải `False` →
`external`, nghĩa là **output làm nhiễm context**. Microsoft miễn taint cho read-only tool
(*"they are safe to call even when the agent context is tainted — it cannot exfiltrate"*,
[§09](../research/09-memory-context-multiagent-hitl.md) §16bis). Điều đó đúng theo chiều
exfiltration nhưng bỏ chiều còn lại: một read-only tool đọc từ internet là nguồn prompt
injection hạng nhất.

**M-3. `accepts_tainted` và trần confidentiality KHÔNG BAO GIỜ đến từ hint.** Chỉ
`policy.accepts_tainted`, do operator viết. Đây là chỗ vá lỗ hổng còn lại của Microsoft, và
nghiên cứu chỉ đích danh nó: mapping của họ biến `readOnlyHint=True` thành
`accepts_untrusted=True`, nên *"the mapping is conservative about a server that stays silent
and trusting of a server that speaks"* ([§08](../research/08-tool-mcp-plugin.md) §9). Một
server độc chỉ cần khai `readOnlyHint=true` là được chạy trong context đã nhiễm. Nghiên cứu
cũng chỉ ra vì sao lỗ hổng ấy vô lý: *"It takes no server-trust parameter — only
`default_integrity` — even though the same codebase has a `server_label` trust boundary in
`_tool_approval.py`"*. Hai thứ đó nằm trong cùng một codebase và không được nối với nhau.
`classify_mcp_tool` nhận `McpServerPolicy` chính là mối nối đó.

**M-4. Grant khoá theo danh tính server.** `Scope.server` của [00-foundation §4.1] so khớp với
`ServerIdentity`, không với chuỗi label. Một `Decision` duyệt `search(query="x")` trên server
A không duyệt tool cùng tên trên server B, và cũng không duyệt nó sau khi label A bị trỏ sang
endpoint khác.

### 5.4 Rug-pull: phân loại được chốt tại thời điểm bind

`tools/list` có thể trả kết quả khác giữa hai lần gọi. Vì vậy `ToolSpec` sinh ra từ MCP được
**chốt vào state đã checkpoint của run**. Nếu một lần re-list làm đổi `input_schema` hoặc
annotation của một tool đang dùng, nó được coi là **tool mới**: phân loại lại theo M-1…M-3, và
mọi `Decision` cũ không chuyển sang, vì `Scope` khoá theo `args` chứ không chỉ theo tên
([00-foundation §4.1]).

---

## 6. Cancel — học kỷ luật của autogen

### 6.1 Bằng chứng

autogen-agentchat có mật độ cancellation **26,9/kLOC**, gấp ba dự án đứng thứ hai
(pydantic-ai 9,1), và không phải nhiễu: 183 lần `cancellation_token` là **tham số tường minh
trên API**, cộng 84 lần `CancellationToken`. Nghiên cứu kết luận đây là mẫu actor/.NET —
*"huỷ được truyền tay qua từng biên, không dựa vào cơ chế ngầm của runtime... đắt hơn về mặt
API surface, nhưng là cách duy nhất khiến việc huỷ trở nên kiểm tra được"*
([§07](../research/07-remaining-python.md) §Phát hiện mới).

### 6.2 Thiết kế

```python
# Định nghĩa chuẩn ở 04-runtime-durability.md §6.1 — ở đây chỉ dùng.
from harness import CancelToken
    async def wait(self) -> str: ...          # trả về reason, để race với công việc

    @property
    def cancelled(self) -> bool: ...
    @property
    def reason(self) -> str | None: ...


@value
class ToolCtx:
    """Đối số thứ hai của mọi tool fn.  Mọi thứ một tool cần mà nó không được tự lấy."""
    run_id: RunId
    call_id: CallId
    cancel: CancelToken
    label: "Label"                            # 00-foundation §3.2
    idempotency_key: IdempotencyKey | None    # LUÔN khác None khi effect ∈ {WRITE, DANGER}
    deadline: datetime
```

`ToolCtx` là tham số bắt buộc trong signature của `fn`, nên token đi qua từng biên bằng kiểu,
không bằng quy ước — đúng điều autogen làm, và ngược với mọi dự án dùng `task.cancel()` ngầm.

### 6.3 Bốn luật

**C-1. `CancelledError` và `KeyboardInterrupt` không bao giờ trở thành tool result.** Chúng
không đi qua `classify()` (§2) và được ném tiếp nguyên trạng. LangChain làm đúng chỗ này —
họ bắt `KeyboardInterrupt` rồi re-raise thay vì nuốt, và nghiên cứu ghi nhận *"a bare
`except Exception` here would have made Ctrl-C unreliable"*
([§08](../research/08-tool-mcp-plugin.md) §8.2).

**C-2. Timeout là cancel, không phải cơ chế thứ hai.** `spec.timeout_s` hết hạn → runtime gọi
`token.cancel("timeout")`. Một con đường, một chỗ để test.

**C-3. Cancel không rollback.** Một `write` bị huỷ giữa chừng để lại hàng `in_flight` trong
effect log và §4.4 xử lý phần còn lại. Huỷ là *dừng làm thêm*, không phải *hoàn tác*, và giả
vờ ngược lại là cách sinh ra double effect.

**C-4. Biên segment là điểm huỷ sạch.** Vì `write`/`danger` là barrier (§3), giữa hai segment
không có tool call nào đang bay, nên huỷ ở đó để lại trạng thái không mơ hồ. Barrier trả về
một tính chất thứ hai ngoài tính đúng đắn của concurrency.

---

## 7. Bảng tổng kết — học của ai, sửa gì

| quyết định | học của ai | sửa khuyết điểm nào |
|---|---|---|
| một `effect`, năm hành vi dẫn xuất | pydantic-ai `ToolKind`+`sequential`, MS tách read/write approval, MCP hints ([§08](../research/08-tool-mcp-plugin.md) §8.3, §9; [§09](../research/09-memory-context-multiagent-hitl.md) §14.2) | ba từ vựng rời rạc cho cùng một khái niệm; và enforcement per-tool trượt ở tool thứ hai mươi |
| `effect` không có giá trị mặc định | — | `@tool(approval_mode="never_require")` của `mode_set` trông giống mọi tool khác |
| ba nhánh outcome `RETRY`/`FAILED`/`FATAL` | pydantic-ai `ModelRetry`/`ToolFailed` ([§08](../research/08-tool-mcp-plugin.md) §8.2) | pydantic-ai bắt tác giả tool tự chọn nhánh |
| chính sách lỗi suy ra từ effect | kết luận của chính [§08](../research/08-tool-mcp-plugin.md) §8.2 | LangChain `handle_tool_error=False` — một tool ném exception giết cả run, và opt-in là per-tool |
| barrier suy ra từ effect | pydantic-ai `'exhaustive'` ([§08](../research/08-tool-mcp-plugin.md) §8.3) | LangChain phải viết guard tay cho riêng `write_todos` |
| effect log + partial-unique index + đọc lại bên thắng | agno ([§07](../research/07-remaining-python.md) §ĐÍNH CHÍNH) | agno chỉ có ở mức run submission; không ai có ở mức tool call ([§03](../research/03-safety-reliability.md) §15) |
| policy MCP khoá theo `ServerIdentity` | MS `server_label` ([§09](../research/09-memory-context-multiagent-hitl.md) §14) | label là chuỗi; trỏ lại endpoint thì grant đi theo |
| ánh xạ hint fail-closed, `is True` | MS `_map_mcp_annotations_to_labels` ([§08](../research/08-tool-mcp-plugin.md) §9) | — (chép nguyên, nó đúng) |
| `accepts_tainted` không bao giờ từ hint | — | MS thận trọng với server im lặng nhưng tin server nói dối, và không nhận tham số tin cậy server |
| `CancelToken` tường minh trên mọi biên | autogen 26,9/kLOC ([§07](../research/07-remaining-python.md) §Phát hiện mới) | huỷ ngầm không kiểm tra được |

---

## Chưa đủ evidence

- **Chi phí ergonomics của `default_effect = DANGER` cho server MCP không tin cậy.** Polarity
  là đúng theo spec MCP, nhưng không có dữ liệu về việc bao nhiêu operator sẽ hạ hàng loạt
  xuống `read` chỉ để cho xong — tức là biến một mặc định an toàn thành một nghi thức. Cần đo
  bằng người dùng thật, không suy ra được từ source.
- **Reconciliation cho hàng `in_flight`.** §4.4 dừng run và để hàng lại cho con người. Việc hỏi
  ngược upstream *"lệnh này đã ghi chưa"* cần một giao thức mà không API nào trong nghiên cứu
  cung cấp thống nhất, nên nó nằm ngoài phạm vi tệp này.
- **Ngưỡng retry budget = 3.** Con số này không có bằng chứng trong nghiên cứu; nó là mặc định
  chọn theo cảm tính, có thể cấu hình, và cần eval để chỉnh.
- **Rug-pull qua `tools/list` (§5.4).** Nghiên cứu chứng minh annotation là lời tự khai chưa
  kiểm chứng ([§08](../research/08-tool-mcp-plugin.md) §9); việc *đổi* lời khai giữa hai lần
  list là hệ quả tự nhiên nhưng **không được quan sát trực tiếp** trong bất kỳ source nào đã
  đọc. Luật M-4/§5.4 là phòng ngừa, không phải phản ứng với một sự cố đã đo được.
- **Chi phí ghi effect log trên mỗi `write` call.** Một round-trip DB thêm cho mỗi tool call
  có side effect. Không đo được từ source người khác; cần benchmark của chính repo này.
- **Định dạng `fingerprint` cho `ServerIdentity`.** TLS SPKI pin đúng cho transport HTTP nhưng
  không có tương đương hiển nhiên cho MCP stdio (tiến trình con). Chưa đủ evidence để chốt.
