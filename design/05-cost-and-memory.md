# Harness Design — Cost và Memory

**Phạm vi:** `Ledger` (`reserve`/`hold`/`release`/`snapshot`), compaction context, và
provenance cho memory. Từ vựng theo [`00-foundation.md`](00-foundation.md) §6 —
`Ledger`, `Label(integrity, confidentiality)`, `Effect`, `Decision`, `Run`.

Ba khuyết điểm đo được mà tệp này sửa:

| khuyết điểm | bằng chứng | mục |
|---|---|---|
| Không dự án nào reserve budget **trước** khi gọi model; `max_turns` chặn số **bước** chứ không chặn **tiền** | ([§03](../research/03-safety-reliability.md) §20), ([§11](../research/11-workflow-and-dx.md) §22) | A.1 |
| Handoff **không cho isolation nào** — không fault, không privilege, không information | ([§09](../research/09-memory-context-multiagent-hitl.md) §13) | A.2 |
| **Không hệ thống memory nào ghi provenance** — khoảng trống bảo mật lớn nhất toàn nghiên cứu | ([§09](../research/09-memory-context-multiagent-hitl.md) §10.2) | C.1 |

---

# PHẦN A — COST

## A.1 `Ledger` — `reserve()` tiền-chuyến-bay

### Khuyết điểm đang sửa

Đo trên 23 gói Python: `budget/kLOC` cao nhất là pydantic-ai 3,6; LangGraph **0,0** —
không có khái niệm ngân sách nào ([§03](../research/03-safety-reliability.md) §20).
Cơ chế phổ biến duy nhất là `max_turns`/`max_iterations`. Kết luận của nghiên cứu:

> ngành có **loop limit** nhưng gần như không có **spend ceiling** — một vòng với 200k
> token input đắt gấp trăm lần một vòng ngắn, nên đếm vòng không phải kiểm soát chi phí
> ([§03](../research/03-safety-reliability.md) §20).

Hệ quả cụ thể: một run 10 bước với context khổng lồ vượt ngân sách mà `max_turns=10`
không hề chạm tới. Và §22 xác nhận từ phía developer: *"Nothing reserves budget before a
model call; `max_turns` bounds steps, not spend"*
([§11](../research/11-workflow-and-dx.md) §22).

### Học của ai

pydantic-ai (framework đa dụng duy nhất coi cost là khái niệm hạng nhất, `cost/kLOC` 1,1)
và **browser-use** — `cost_per_token`, `cost_usd`, `_usage_from_events_with_costs()`,
tính giá theo từng lời gọi tại `beta/service.py:3197`
([§07](../research/07-remaining-python.md) *Đính chính thứ hai*). Cả hai **kế toán** thật.
Không ai trong hai **chặn trước**. Harness này lấy kế toán của họ và thêm cái còn thiếu.

### Signature

```python
from decimal import Decimal
from typing import Final

from harness._value import value
from harness.models.pricing import Price
from harness.result import Money, Usage

MIN_USEFUL_OUTPUT_TOKENS: Final[int] = 256      # dưới mức này, câu trả lời cụt không phải câu trả lời
INPUT_MARGIN: Final[Decimal] = Decimal("1.15")  # bù cho việc đếm input là ƯỚC LƯỢNG


@value
class Budget:
    usd: Decimal                      # BẮT BUỘC, không mặc định, không None — 00 §1 bất biến 2
    steps: int = 20
    wall_clock_s: float = 300.0


@value
class Reservation:
    id: str
    estimate: Money
    input_tokens: int
    max_tokens: int
    exact: bool             # True khi cận trên cứng vừa ngân sách — xem B.3
    created_at: float


class Ledger:
    def __init__(self, budget: Budget, *, clock: Callable[[], float] = time.monotonic) -> None: ...

    # --- ba lời gọi bắt buộc, đúng thứ tự này, quanh MỌI lời gọi model ---
    def size_call(self, input_tokens: int, price: Price, model_max: int) -> int: ...
    def reserve(self, input_tokens: int, max_tokens: int, price: Price,
                *, hard_max_input: int | None = None) -> Reservation: ...
    def settle(self, reservation: Reservation, usage: Usage, price: Price) -> Money: ...

    # --- đọc trạng thái ---
    def remaining_usd(self) -> Money | None: ...
    def remaining_steps(self) -> int: ...
    def remaining_wall_clock(self) -> float: ...
    def tool_timeout(self, spec_timeout_s: float) -> float: ...   # clamp vào wall clock còn lại
```

### `max_tokens` được SUY RA, không phải hằng số

Đây là điểm khác biệt với mọi dự án đã đọc. `max_tokens` không do người dùng đặt và
không phải mặc định của SDK; nó là **thương của số dư**:

```
input_cost   = input_tokens × INPUT_MARGIN × calibration / 1e6 × price.input_per_mtok
affordable   = (remaining_usd − input_cost) / price.output_per_mtok × 1e6
max_tokens   = min(affordable, model_max)
```

Với hai điều kiện dừng, cả hai đều `raise BudgetExceeded` thay vì cắt ngắn im lặng:

- `affordable <= 0` → *"input alone costs more than the budget has left"*;
- `affordable < MIN_USEFUL_OUTPUT_TOKENS` → một câu trả lời bị cắt ở token thứ 40 không
  phải câu trả lời, chỉ là tiền đã tiêu mà không có kết quả.

Vì `max_tokens` suy ra từ số dư, **worst case của một reservation không bao giờ vượt
ngân sách theo định nghĩa** — không cần tin vào ước lượng để có được cận trên.

### Bất biến

- **C-1.** Không đường nào trong graph tới node `model` mà không qua `reserve()`. Chứng
  minh bằng `unguarded_paths()`, không bằng review — [`00-foundation.md`](00-foundation.md)
  §5 R-2 (23 vòng review: 0 lỗi bảo mật; 16 vòng *chạy*: 4 lỗi bảo mật).
- **C-2.** `reserve()` hết ngân sách → `StopReason.BUDGET_EXHAUSTED`, không phải exception
  rò ra ngoài và không phải một lời gọi nhỏ hơn "cho có".
- **C-3.** `settle()` dùng **bốn** mức giá (input, output, cache read, cache write). Bỏ
  cache read khỏi công thức là báo cáo thấp hơn thực tế đúng vào lúc harness đang tối ưu
  cache.
- **C-4.** Sau khi `settle()` đẩy `spent` vượt trần, ledger `_blocked` — mọi `size_call`
  sau đó raise. Trần **authorization** là chính xác; trần **spend** bị vượt tối đa một
  lời gọi, và phần vượt đó đo được qua `Ledger.overshoot`. Nói rõ con số đó thay vì nói
  "ngân sách là tuyệt đối", vì nó không tuyệt đối.

---

## A.2 `hold()` / `release()` — sub-agent rút từ một lát đã giữ

### Vì sao cần: nghiên cứu nói handoff không cho isolation nào

Đọc source `openai_agents`: một handoff **là một tool call** mà giá trị trả về là một
system prompt cộng một toolset — không process mới, không context mới, không client mới
([§09](../research/09-memory-context-multiagent-hitl.md) §13). Ba thứ chữ "isolation"
hàm ý đều không có:

- ❌ context isolation — `input_filter` mặc định `None`, agent mới thấy toàn bộ lịch sử;
- ❌ fault isolation — cùng loop, cùng process;
- ❌ privilege isolation — `_resolve_approval_key` ghép tool name + namespace + lookup key
  và **không có identity của agent trong key** (`run_context.py:168`), nên tool được duyệt
  lúc agent A chạy vẫn còn duyệt khi B gọi.

Kết luận của nghiên cứu: *"Multi-agent as implemented is a router, not an architecture"*,
và hệ quả cho harness: nếu muốn sub-agent thật sự cô lập — không tiêu được số dư của cha,
không thừa kế approval của cha — **framework sẽ không cho, nó phải là construct hạng nhất
có ledger riêng** ([§09](../research/09-memory-context-multiagent-hitl.md) §13).

### Điểm mạnh của openai-agents phải giữ

Cùng đoạn nghiên cứu tìm ra một thứ họ làm **đúng** và không hiển nhiên: `current_turn = 0`
xuất hiện **đúng một lần**, ở `run.py:762`, lúc bắt đầu run. Tại chỗ handoff (`line 2050`)
chỉ `current_agent` được gán lại. **Handoff không reset được turn budget** — cài đặt ngây
thơ (mỗi agent một `max_turns`) biến chuỗi delegation thành vòng lặp vô hạn; ba agent
handoff vòng tròn sẽ chạy mãi ([§09](../research/09-memory-context-multiagent-hitl.md) §13).

Vậy luật của harness này: **tiền được chia; bước và đồng hồ thì không.**

| tài nguyên | qua biên sub-agent | lý do |
|---|---|---|
| `usd` | **chia** — con rút từ lát đã giữ | isolation mà §13 nói framework không cho |
| `steps` | **kế thừa số còn lại**, không reset | điểm mạnh openai-agents, §13 |
| `wall_clock_s` | **kế thừa số còn lại**, không reset | cùng lý do; nếu reset, chuỗi delegation không có trần thời gian |

### Signature

```python
@value
class Hold:
    id: str
    amount: Money
    opened_at: float


class Ledger:
    def hold(self, amount: Money) -> Hold:
        """Giữ trước một lát ngân sách. `spent` tăng NGAY, trước khi con chạy."""

    def release(self, hold: Hold, actual: Money) -> None:
        """Thay lát đã giữ bằng số thật sự tiêu: spent = spent − hold.amount + actual."""

    def slice_for_child(self, hold: Hold) -> "Ledger":
        """Ledger của con: usd = hold.amount, steps và wall clock là phần CÒN LẠI của cha."""
        return Ledger(Budget(usd=hold.amount.decimal,
                             steps=self.remaining_steps(),
                             wall_clock_s=self.remaining_wall_clock()))
```

`hold()` trả về số **thật sự giữ được** (`min(amount, max(remaining, 0))`), không phải số
đã xin. Vì `spent` tăng ngay tại `hold()`, N sub-agent song song **chia** phần còn lại thay
vì mỗi đứa đọc cùng một `remaining` rồi mỗi đứa xin hết — đây đúng là TOCTOU trên ledger
mà chính repo này đã mắc ở Round 28.

### Vì sao không phải context manager

Cám dỗ là `async with ledger.slice(Money("0.10")) as child:` để `release` chạy trong
`finally`. Không dùng, vì một sub-agent có thể **dừng giữa chừng** chờ `Decision` của con
người ([`00-foundation.md`](00-foundation.md) §4) và run được resume ở process khác. Khi đó
`finally` không bao giờ chạy trong process đã mở hold. Nên hold là **dữ liệu trong state đã
checkpoint**, không phải một khối `try`:

- hold chưa release sau khi restore = tiền vẫn tính là đã tiêu → **fail-safe** (thà tính
  thừa còn hơn tính thiếu);
- `Hold.id` cho phép release đúng lát sau resume.

---

## A.3 `snapshot()` / `restore()` — ledger sống trong state, không trên object

### Khuyết điểm đang sửa

[`00-foundation.md`](00-foundation.md) §5 R-4: không trạng thái chia sẻ ngoài state đã
checkpoint. Nghiên cứu tìm được phiên bản yếu hơn của chính lỗi này ở vendor lớn: module
security của Microsoft set middleware vào `threading.local()` rồi set xuyên qua `await`,
nên hai tool call đồng thời đọc nhầm slot của nhau, im lặng và **fail-open**; cộng
`_global_variable_store` và `_quarantine_chat_client` ở mức module — trong server đa tenant,
một tenant đổi là mọi tenant đổi theo
([§09](../research/09-memory-context-multiagent-hitl.md) §16bis).

Ledger là trường hợp tệ nhất của lỗi này: một ledger nằm trên object Runtime dùng chung
nghĩa là **ngân sách của khách hàng A trừ vào khách hàng B**. Và `ContextVar` cũng không
cứu được, vì mỗi node LangGraph chạy trong một context đã copy.

### Signature

```python
@value
class LedgerState:
    """Số học Decimal, mã hoá thành str để đi qua bất kỳ checkpointer JSON nào."""
    spent: str
    steps: int
    calibration: str
    overshoot: str
    holds: tuple[tuple[str, str], ...]     # (hold_id, amount) — các lát chưa release


class Ledger:
    def snapshot(self) -> LedgerState: ...

    @classmethod
    def restore(cls, budget: Budget, state: LedgerState | None,
                *, clock: Callable[[], float] = time.monotonic) -> "Ledger": ...
```

### Một luật, và nó là fail-closed

**State ledger không đọc được thì run không chạy.** Không "bắt đầu lại từ 0" — đó là
fail-open: một checkpoint hỏng biến ngân sách đã tiêu 90% thành ngân sách còn nguyên, và
chuỗi retry trên một checkpoint hỏng là cách đốt tiền nhanh nhất có thể. `restore()` gặp
state không parse được → `CorruptLedgerState`, run dừng, con người xem. Đây là chỗ khác
với cài đặt hiện có trong `src/harness/budget/ledger.py`, vốn nuốt lỗi và bắt đầu sạch.

`calibration` chỉ **tăng** khi restore (`max(1, saved)`) — under-count là hướng nguy hiểm,
over-count chỉ phí một ít headroom.

---

## A.4 Cost thật — token thành tiền

### Ai giữ bảng giá

Đúng một module: `harness.models.pricing`. Nó giữ `PRICES`, `MAX_CONTEXT`, `MAX_OUTPUT`, và
một hằng `AS_OF: Final[str]` — ngày bảng giá được xác nhận.

```python
@value
class Price:
    input_per_mtok: Decimal
    output_per_mtok: Decimal
    cache_write_per_mtok: Decimal
    cache_read_per_mtok: Decimal


AS_OF: Final[str] = "2026-06-24"

def price(model: str) -> Price:
    """Model lạ → UnknownModelError. KHÔNG BAO GIỜ trả về 0."""
```

Luật quan trọng nhất của module này là luật lỗi: **model không có giá thì từ chối gọi**.
Trả về `Price(0,0,0,0)` cho model lạ là biến trần chi phí thành vô hiệu đúng lúc người dùng
vừa đổi sang model mới. Đây là lớp lỗi mà cả `03-safety-reliability` §20 lẫn §22 mô tả —
"framework có giới hạn, không có khái niệm tiền" (MS Agent Framework: `budget/kLOC` 1,9,
`cost/kLOC` **0,0**).

### Model đổi giá

Ba quy tắc, KISS:

1. **Pin theo run.** `Price` được giải một lần lúc bắt đầu run và ghi vào state đã
   checkpoint cùng `AS_OF`. Một run bị pause hai ngày rồi resume sau khi bảng giá đổi vẫn
   tính bằng giá nó đã bắt đầu — nếu không, `spent` đã ghi và `remaining` được tính bằng
   hai bảng giá khác nhau, và không con số nào trong report còn đúng.
2. **Ghi vào audit.** Event kết thúc run mang `model`, `price_as_of`, `usage`, `cost_usd`.
   Bảng giá là input của một phép tính tiền; một phép tính tiền không truy nguyên được
   input thì không audit được.
3. **Provider được quyền thay bảng giá, không được quyền bỏ nó.** `Provider.price(model)
   -> Price` là seam cho khách có giá thương lượng hoặc gateway riêng. Theo
   [`00-foundation.md`](00-foundation.md) §5 R-1: **cost accounting là plugin, budget
   enforcement là đường đi bắt buộc** — người ta thay được *bảng giá*, không thay được
   *việc phải reserve*.

### Con số nào để báo cáo

Không phải cost/task. Nghiên cứu đưa công thức
([§03](../research/03-safety-reliability.md) §20):

```
Cost_successful = (LLM + Tool + Infra + Sandbox + Observability + HumanReview) / P(success)
```

Harness đo được hai số hạng đầu và mẫu số (qua `StopReason`); phần còn lại thuộc về
deployment. Ta báo cáo **`cost` và `stop_reason` theo từng run** để mẫu số tính được, thay
vì báo một con số cost trung bình che mất việc một nửa số run thất bại.

---

# PHẦN B — CONTEXT / COMPACTION

## B.1 Bảo toàn cặp `tool_call`/`tool_result` — bất biến I-3

### Ladder Poka-Yoke sạch nhất trong nghiên cứu

Nghiên cứu gọi đây là *"the cleanest Poka-Yoke ladder in the study"*
([§09](../research/09-memory-context-multiagent-hitl.md) §11):

**LangChain TÀI LIỆU HOÁ bất biến.** `langchain_core/messages/utils.py:1133`, `trim_messages`,
docstring nói *"a `ToolMessage` can only appear after an `AIMessage` that involved a tool
call. To achieve this, set `start_on='human'`."* Nhưng `_first_max_tokens` (line 1970) và
`_last_max_tokens` (line 2086) **không có logic pairing nào cả**. Cần lưu ý ba điều:

1. cần cầu là một cờ do **người gọi** truyền, mặc định `None` → đường mặc định cắt được
   giữa tool call và tool result;
2. cần cầu chỉ hoạt động nếu người gọi **đã đọc docstring**;
3. và ngay cả khi bật, `start_on='human'` "sửa" bằng cách **vứt cả cuộc trao đổi** chứ
   không giữ cặp.

Trong khi đó chính package đó có `filter_messages` làm đúng (`exclude_tool_calls` xoá
`ToolMessage` khớp, viết lại `tool_calls` trên `AIMessage`) — **năng lực có sẵn trong
package, và đường token-budget không dùng nó**.

**Microsoft TÍNH bất biến.** `agent_framework/_compaction.py:105`:

```python
def _unambiguous_function_call_result_pairs(messages: Sequence[Message]) -> list[tuple[int, int]]:
```

Duyệt transcript dựng `call_id → [chỉ số declaration]`, khớp từng `function_result` với các
declaration đang chờ rồi pop. Chữ **unambiguous** trong tên là có chủ ý: `call_id` **trùng**
được xử lý như một danh sách ứng viên chứ không giả định là duy nhất. Compaction sau đó chỉ
làm việc trên **cặp chỉ số**, nên một cặp không thể bị xoá một nửa.

### Thiết kế

Học Microsoft: **tính**, không tài liệu hoá. Và đi thêm một bước họ không đi.

```python
@value
class Pairing:
    pairs: tuple[tuple[int, int], ...]   # (chỉ số assistant-với-tool_call, chỉ số tool_result)
    orphan_calls: tuple[int, ...]
    orphan_results: tuple[int, ...]


def call_result_pairs(messages: Sequence[Mapping[str, Any]]) -> Pairing:
    """call_id trùng được khớp theo thứ tự xuất hiện (danh sách ứng viên, pop cái sớm nhất).

    Trùng call_id là chuyện thật khi merge hai nhánh hoặc replay một checkpoint; giả định
    duy nhất là chỗ Microsoft đặt chữ 'unambiguous' vào tên hàm để nhắc.
    """
```

Ba luật:

- **I-3.** Mọi biến đổi message list chỉ được đọc `Pairing.pairs`. Một cặp bị xoá nửa là
  request bị provider từ chối, tức là một lỗi runtime cho lỗi lập trình.
- **I-3a.** `orphan_*` khác rỗng → emit event và **không compact** ở lượt đó. Orphan là
  triệu chứng của bug ở nơi khác; nén tiếp lên một transcript đã hỏng là làm mất bằng chứng.
- **I-3b — chỗ đi xa hơn Microsoft.** Kiểm tra pairing là **hậu điều kiện trên đường đi bắt
  buộc**, không nằm bên trong strategy:

```python
class Compactor(Protocol):
    async def compact(self, messages: Sequence[Msg], *, target_tokens: int,
                      pairing: Pairing) -> Sequence[Msg]: ...


async def compact_checked(c: Compactor, messages: Sequence[Msg], *,
                          target_tokens: int) -> list[Msg]:
    out = list(await c.compact(messages, target_tokens=target_tokens,
                               pairing=call_result_pairs(messages)))
    after = call_result_pairs(out)
    if after.orphan_calls or after.orphan_results:
        raise CompactionBrokePairing(c, after)
    return out
```

Lý do theo [`00-foundation.md`](00-foundation.md) §5 R-1: compaction là **plugin**, bất biến
là **đường đi bắt buộc**. Microsoft đặt pairing *bên trong* module compaction của họ, nên
một strategy thứ ba do người dùng viết có thể phá cặp. Ở đây thì không thể.

### Và ta không vứt cả cuộc trao đổi

Cách sửa của LangChain (`start_on='human'`) giữ đúng bất biến bằng cách **bỏ luôn cả đoạn
hội thoại**. Harness này xoá **nội dung** của `tool_result` cũ và giữ nguyên **cả hai
message**:

```python
CLEARED: Final[str] = "[earlier tool result cleared to save context]"
```

Cặp còn nguyên, cấu trúc còn nguyên, model vẫn thấy nó *đã* gọi tool gì — chỉ mất phần
40 kB. Đây là cách rẻ hơn và ít mất mát hơn cùng một lúc.

---

## B.2 Chiến lược compaction — lấy thứ tự ưu tiên của Microsoft, lấy **hai** thay vì năm

### Học của ai

Quanh `_unambiguous_function_call_result_pairs` là **năm** strategy:
`SelectiveToolCallCompactionStrategy`, `ToolResultCompactionStrategy`,
`SummarizationStrategy`, `ContextWindowCompactionStrategy`, và một `CompactionStrategy`
Protocol. **Hai trong năm nhắm riêng tool result**, và nghiên cứu gọi đó là *ưu tiên đúng*:

> in a tool-using agent, tool output is where the tokens actually accumulate, and it is
> also the most compressible (a 40 kB HTML fetch is worth two sentences on the next turn).
> No other framework surveyed separates tool-result compaction from message-history
> compaction ([§09](../research/09-memory-context-multiagent-hitl.md) §11).

### Sửa khuyết điểm nào: năm chiến lược là năm chiến lược phải bật

Bài học kèm theo, từ cùng vendor: kiến trúc an toàn tốt nhất trong nghiên cứu là **opt-in,
experimental, và chính harness của họ không import nó**
([§09](../research/09-memory-context-multiagent-hitl.md) §16bis). Một mặt cấu hình rộng là
một mặt cấu hình mặc định tắt. Nên KISS: giữ **thứ tự ưu tiên** của Microsoft, cắt số lượng.

**Hai chiến lược, chạy theo bậc thang:**

| ngưỡng | chiến lược | giá | mất mát |
|---|---|---|---|
| `used/window ≥ 0.60` | **`ClearToolResults`** — xoá nội dung `tool_result` cũ, giữ `KEEP_RECENT_STEPS = 3` bước gần nhất nguyên vẹn | 0 token, xác định, không gọi model | chỉ mất nội dung tool cũ |
| `used/window ≥ 0.80` | **`SummarizeOldPrefix`** — một lần gọi model tóm tắt phần đầu, giữ đuôi nguyên vẹn | một model call, phải `reserve()` như mọi call khác | mất chi tiết phần đầu |

```python
EDIT_AT: Final[float] = 0.60
COMPACT_AT: Final[float] = 0.80
KEEP_RECENT_STEPS: Final[int] = 3

def manage(messages: Sequence[Msg], *, used_tokens: int, context_window: int
           ) -> tuple[list[Msg], Literal["none", "edited", "compact_needed"]]: ...
```

Editing trước, summarization sau: editing **miễn phí và không mất gì với công việc gần
đây**; summarization tốn một lời gọi model — và lời gọi đó cũng phải qua `reserve()` (§A.1),
nên nén context **cũng là chi tiêu** và cũng nằm dưới trần.

Ba chiến lược còn lại của Microsoft bị cắt vì: `ContextWindow` là chính ngưỡng ở trên;
`SelectiveToolCall` là cùng một trục với `ClearToolResults` ở độ chi tiết cao hơn mà không
có bằng chứng nào trong nghiên cứu nói độ chi tiết đó đáng giá; `Protocol` thì ta giữ — nó
là seam plugin, và là thứ duy nhất trong năm cái không phải là một chính sách.

### Compaction không được rửa taint

Điểm không framework nào kiểm tra, và nó rơi thẳng vào lattice ở
[`00-foundation.md`](00-foundation.md) §3.2:

> **Bản tóm tắt của nội dung `UNTRUSTED` là `UNTRUSTED`.** Bản tóm tắt của context chứa
> `SECRET` là `SECRET`.

`Label` của message tổng hợp = `join` của `Label` mọi message bị nó thay thế. Nếu không có
luật này, summarization trở thành đường **rửa nhãn** hoàn hảo: nội dung web độc hại đi vào
như `UNTRUSTED`, đi ra thành một đoạn văn do model viết trông y hệt nội dung tin cậy, và
lattice mất hiệu lực đúng lúc context dài nhất. Hợp thành đơn điệu — không bao giờ giảm
trong một run.

---

## B.3 Đếm token — không ai chính xác, nên nói rõ xử lý sai số thế nào

### Bằng chứng

> **Nobody has a token-exact budget.** `count_tokens_approximately`
> (`langchain_core/messages/utils.py:2244`) is named honestly. The `ctxwindow` column is
> near-zero everywhere (max 1.5, llama-index). Frameworks compact against an estimate and
> discover the real number when the provider rejects the request
> ([§09](../research/09-memory-context-multiagent-hitl.md) §11).

Cái tên trung thực đó là điểm mạnh của LangChain và ta giữ: **hàm đếm mang chữ
`approximately` trong tên**, để không ai ở call site nhầm nó với sự thật.

### Bốn lớp phòng thủ

```python
def count_tokens_approximately(request: ModelRequest) -> int:
    """ƯỚC LƯỢNG. Tên nói vậy vì nó là vậy."""

def hard_max_input_tokens(request: ModelRequest) -> int:
    """CẬN TRÊN CỨNG: số ký tự của request đã render.

    Không tokenizer nào sinh nhiều token hơn số ký tự, nên đây là cận trên tính được
    tại chỗ, không cần gọi provider.
    """
```

1. **Biên + hiệu chỉnh.** Mọi reservation nhân input đã đếm với `INPUT_MARGIN = 1.15` và
   với `calibration` — tỉ số `billed_input / counted_input` quan sát được ở `settle()`,
   **chỉ tăng, không giảm**. Đếm thiếu là hướng nguy hiểm; đếm thừa chỉ phí headroom.
2. **Cận trên cứng khi nó vừa.** Nếu tính bằng `hard_max_input_tokens` mà *vẫn* nằm trong
   ngân sách, dùng luôn số đó → reservation là **chính xác** chứ không phải ước lượng, và
   `Reservation.exact = True`. Khi nào ngân sách rộng, ta không cần tin ước lượng chút nào.
3. **Nén tới `0.80 × window`, không tới `window`.** Khoảng đệm 20% là chỗ cho sai số đếm.
   Nén sát mép rồi bị provider từ chối là kịch bản thật mà §11 mô tả.
4. **Một lần thử lại, xác định, rồi dừng.** Provider trả lỗi context-length:
   - `reserve` của lời gọi bị từ chối được **huỷ, không `settle`** — request bị từ chối
     không tốn tiền, và ledger không được tính nó là đã tiêu;
   - `calibration` được nâng theo tỉ lệ vượt quan sát được;
   - nén lại **một lần** ở mục tiêu chặt hơn (`0.60 × window`);
   - lần thứ hai thất bại → `StopReason.TRUNCATED` với `detail` nói rõ đã ước lượng bao
     nhiêu và provider nói bao nhiêu. **Không vòng lặp thử lại** — một vòng lặp co dần
     chạy trên một ước lượng sai là cách tiêu hết ngân sách trong sáu lời gọi.

---

# PHẦN C — MEMORY

## C.1 Provenance cho MỌI memory write

### Khoảng trống lớn nhất nghiên cứu tìm được

Tìm `taint|provenance|untrusted` trên cả 23 gói
([§09](../research/09-memory-context-multiagent-hitl.md) §10.2):

| gói | số lần | ghi chú |
|---|---:|---|
| letta-client | **0** | |
| langmem | 5 | |
| mem0ai | 7 | |
| `agent_framework/_harness/_memory.py` | **0** | |
| agno | 100 — **không liên quan** | `stamp_schedule_provenance`, routing trong `agno/team/_run.py`; không phải information flow |

Kết luận nguyên văn:

> **No memory system surveyed records where a memory came from.** A fact extracted from an
> attacker-controlled page is stored identically to one the user typed. Retrieval cannot
> distinguish them, so the injection survives the session that carried it. […] this is the
> largest unaddressed security gap found in the study.

Cộng với §16bis: cài đặt information-flow thật duy nhất (`agent_framework/security.py`)
**không được nối vào memory subsystem của chính framework đó**.

Đây là chỗ prompt injection trở thành **thường trú**: agent đọc một trang web thù địch,
trích một "fact", ghi vào long-term memory, và fact đó được recall và tin ở mọi session sau
— kể cả session của người dùng khác, nếu store dùng chung.

### Thiết kế: mỗi bản ghi mang một `Label`

Dùng đúng lattice hai chiều ở [`00-foundation.md`](00-foundation.md) §3.2, không phát minh
trục mới.

```python
# Integrity / Confidentiality / Label: định nghĩa chuẩn ở 00-foundation.md §3.2.
# Ở đây chỉ dùng, không định nghĩa lại.
from harness import Integrity, Confidentiality, Label
```


```python
@value
class Provenance:
    run_id: str
    step: int
    source: str            # "human" | tên tool | tên agent — do runtime điền
    label: Label
    written_at: datetime


@value
class Memo:
    key: str
    value: str
    score: float
    updated_at: float
    provenance: Provenance          # KHÔNG có giá trị mặc định — xem W-3


class Store(Protocol):
    async def get(self, key: str) -> Memo | None: ...
    async def put(self, key: str, value: str, *, provenance: Provenance,
                  ttl_s: float | None = None) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def search(self, query: str, *, limit: int = 5) -> Sequence[Memo]: ...
    async def close(self) -> None: ...
```

`provenance` là **keyword bắt buộc, không mặc định**. Không viết được một memory mà không
nói nó từ đâu tới — type checker chặn ở compile time, không phải review chặn ở PR. Đây là
Poka-Yoke đúng nghĩa theo [`00-foundation.md`](00-foundation.md) §8 luật 3.

### Luật ghi

- **W-1 (nhãn suy ra, không khai báo).** `provenance.label` = `join` của `Label` mọi thứ
  đang có trong context tại bước đó. Model đọc một trang web rồi gọi `remember(...)` →
  memory đó là `UNTRUSTED`, vì output của tool `external` làm context `UNTRUSTED`
  ([`00-foundation.md`](00-foundation.md) §3.2) và nhãn không bao giờ giảm.
- **W-2 (không ghi lên trên tầm store).** Store khai `max_confidentiality`. Ghi một memo
  `SECRET` vào store có `max_confidentiality=PUBLIC` → từ chối. Đây chính là kịch bản
  "shared store" mà §10.2 nêu: một store dùng chung giữa tenant là một sink `PUBLIC`.
- **W-3 (model không điền provenance).** `run_id`, `step`, `source`, `label`, `written_at`
  do runtime điền. Model chỉ cấp `value`. Song song trực tiếp với bất biến **D-1** của
  `Decision` ([`00-foundation.md`](00-foundation.md) §4) và cùng một lý do agno sai: nhật ký
  do bên bị audit viết thì không phải nhật ký.

### Luật đọc

- **R-1 (recall làm nhãn tăng).** Sau `recall`, `Label` của run = `join` của nhãn hiện tại
  với nhãn **từng memo** được nạp vào. Đơn điệu.
- **R-2 (không cơ chế mới).** Memo `UNTRUSTED` trong context không lái được tool `danger`
  trừ khi tool khai `accepts_tainted=True` — **đúng luật đã có** cho output tool `external`
  ([`00-foundation.md`](00-foundation.md) §3.2). KISS: memory không được có luật riêng.
- **R-3 (không rò).** Context chứa memo `SECRET` không gọi được tool có
  `max_confidentiality=PUBLIC` (mặc định của `external` và `write`). Đây là thứ chặn kịch
  bản "recall một bí mật rồi POST nó lên webhook".

### Điều gì xảy ra khi memory `UNTRUSTED` được recall vào context

Cụ thể, vì đây là câu hỏi trung tâm:

1. `recall` trả về memo có `provenance.label.integrity == UNTRUSTED`.
2. Nhãn của run tăng lên `UNTRUSTED`. Event `taint.raised` được ghi với **nguồn là
   `provenance`** — key của memo, `run_id` và `step` đã ghi nó, tool nào là `source`. Đây
   là thứ không hệ nào có: **truy được ngược tới cái run đã trồng fact đó**.
3. Run **vẫn chạy tiếp**. Từ chối đọc memory `UNTRUSTED` thì memory thành vô dụng — hầu như
   mọi long-term memory hữu ích đều dẫn xuất từ nội dung bên ngoài. Cái bị chặn là **hành
   động**, không phải việc đọc.
4. Nếu model sau đó muốn gọi một tool `danger`, policy `DENY`/`ASK` theo R-2. Con đường duy
   nhất đi tiếp là một `Decision` của một `Actor` là người
   ([`00-foundation.md`](00-foundation.md) §4.2 — không có biến thể `Model`), và `Scope`
   của `Decision` đó ghi kèm provenance của memo đã gây taint, nên người duyệt thấy **fact
   này đến từ đâu** chứ không chỉ thấy tên tool.
5. Đánh dấu trong prompt (bọc memo trong delimiter, ghi nhãn) là **advisory** — nó giúp
   model, nó **không phải** cơ chế thực thi. Thực thi nằm ở lattice. Nói rõ điều này vì mọi
   thiết kế lẫn hai thứ đó đều kết thúc bằng một cơ chế an toàn mà model có thể nói vòng qua.

---

## C.2 `recall` là effect gì?

**`external`. Không phải `read`.**

Lý giải theo bảng Effect ở [`00-foundation.md`](00-foundation.md) §2: `external` là lớp
**duy nhất** có `taints_output = True`. Một context database ăn vào nội dung web (OpenViking
có `ov add-resource https://…`), nên bất cứ thứ gì nó trả về **có thể do kẻ tấn công viết**.
Nếu `recall` là `read`, một memory bị đầu độc mua được quyền gọi tool `danger` và lattice
thủng đúng bằng kích thước hệ memory. Cài đặt hiện có trong repo đã chọn đúng —
`src/harness/memory/viking.py` khai `@tool(effect="external")` cho `recall`.

Bốn hành vi còn lại **suy ra**, tác giả tool không khai gì thêm:

| thuộc tính | giá trị | vì sao đúng cho `recall` |
|---|---|---|
| song song | ✅ | recall là truy vấn đọc; nhiều recall song song không tranh chấp |
| retry được | ✅ | không side effect |
| làm nhiễm context | ✅ | đây là cả lý do phân loại |
| verdict mặc định | `ALLOW` | chặn recall thì agent vô dụng; cái bị chặn là *hành động* sau đó |
| mức audit | `info` | |

**Effect là trần tĩnh; `Label` là độ chính xác động.** Phân loại `external` nói *điều tệ
nhất* một store có thể trả. `Provenance` trên từng `Memo` cho biết *thực tế* lần này: một
recall chỉ trả về memo `TRUSTED` (do người gõ) join vào context mà không nâng nhãn. Đây
không phải ngoại lệ của luật "`external` → `UNTRUSTED`" mà là cùng luật ở dạng tổng quát:
`join` nhãn từng bản ghi. Với một `web_fetch`, không có nhãn từng bản ghi nào để join, nên
nó luôn `UNTRUSTED` — trường hợp riêng. **Đây là phần thưởng của provenance:** không có nó,
mọi recall đều phải giả định điều tệ nhất mãi mãi.

**`remember` là `write`, không phải `danger`**: ghi memory là thứ hoàn tác được. Điều kiện
để nói vậy là store **không cầm quyền `rm`** — `viking.py` cố tình không đưa `rm` vào
`ALLOWED_CALLS` và `delete()` ghi giá trị rỗng, để lịch sử của database còn phục hồi được.
Nếu một backend không có tính chất đó thì `remember` trên backend ấy là `danger`, không phải
`write`.

---

## Bảng tổng: học của ai / sửa khuyết điểm nào

| mục | học điểm mạnh của | sửa khuyết điểm |
|---|---|---|
| A.1 `reserve()` tiền-chuyến-bay | pydantic-ai + browser-use (kế toán cost thật) | không ai reserve trước; `max_turns` chặn bước không chặn tiền ([§03](../research/03-safety-reliability.md) §20) |
| A.2 `hold()`/`release()` | openai-agents: turn budget không reset qua handoff | handoff không cho isolation nào ([§09](../research/09-memory-context-multiagent-hitl.md) §13) |
| A.3 `snapshot()`/`restore()` | — | `threading.local()` + singleton mức module ([§09](../research/09-memory-context-multiagent-hitl.md) §16bis); R-4 |
| A.4 bảng giá | browser-use `_usage_from_events_with_costs()` | "có giới hạn, không có khái niệm tiền" ([§03](../research/03-safety-reliability.md) §20) |
| B.1 pairing | Microsoft `_unambiguous_function_call_result_pairs` | LangChain chỉ tài liệu hoá; `start_on` mặc định `None` và "sửa" bằng cách vứt cả trao đổi ([§09](../research/09-memory-context-multiagent-hitl.md) §11) |
| B.2 hai chiến lược | Microsoft: 2/5 nhắm riêng tool result | 5 chiến lược = 5 thứ phải bật; xem §16bis |
| B.3 đếm token | LangChain đặt tên `count_tokens_approximately` trung thực | không ai có ngân sách token chính xác ([§09](../research/09-memory-context-multiagent-hitl.md) §11) |
| C.1 provenance | — (không ai có) | khoảng trống bảo mật lớn nhất nghiên cứu tìm được ([§09](../research/09-memory-context-multiagent-hitl.md) §10.2) |
| C.2 `recall` = `external` | MCP `readOnlyHint`, pydantic-ai `ToolKind` | không hệ memory nào phân biệt nguồn khi recall (§10.2) |

---

## Chưa đủ evidence

- **Nội bộ Letta.** `letta-client` là generated API client, không phải server; mật độ
  summarise 12,0 mô tả bề mặt API chứ không mô tả cách Letta cài memory
  ([§09](../research/09-memory-context-multiagent-hitl.md) §45). Mọi nhận định về kiến trúc
  memory của Letta: chưa đủ evidence.
- **5 hit `taint|provenance|untrusted` của langmem và 7 của mem0ai chưa được đọc từng
  dòng.** Bài học từ agno (100 hit `provenance` hoá ra là schedule provenance) nói rằng
  chúng có thể cũng không liên quan — hoặc có thể là một mẩu thật. Chưa xác minh.
- **Ngưỡng 0,60 / 0,80 và `KEEP_RECENT_STEPS = 3` chưa được đo.** Chúng là điểm khởi đầu
  hợp lý, không phải kết quả tối ưu hoá. Điểm hoà vốn thật (token tiết kiệm được trừ đi chi
  phí của chính lời gọi summarization) chưa đo trên workload nào.
- **Phân bố sai số của `count_tokens_approximately` chỉ mới quan sát trên một họ model.**
  `INPUT_MARGIN = 1.15` là con số kinh nghiệm; với model hoặc ngôn ngữ khác (tiếng Việt có
  tỉ lệ token/ký tự khác tiếng Anh) nó có thể quá chặt hoặc quá lỏng.
- **Mã lỗi context-length của provider có ổn định và phân loại được không** — luật "một lần
  thử lại rồi dừng" ở B.3 giả định phân loại được lỗi này khác với lỗi transient. Chưa
  kiểm chứng qua nhiều provider.
- **Tần suất đổi giá model.** Luật pin-theo-run ở A.4 là fail-safe, nhưng không có dữ liệu
  nói `AS_OF` bao lâu thì được coi là cũ.
- **Isolation đa tenant ở tầng server OpenViking** chưa xác minh. Luật W-2 chặn ghi
  `SECRET` vào store `PUBLIC` ở phía harness; nó không chứng minh được server không rò giữa
  namespace.
- **Chi phí lưu trữ của provenance.** Mỗi `Memo` mang thêm một `Provenance`; với store hàng
  triệu bản ghi, tỉ lệ overhead chưa đo.
- **TTL / hết hạn memory không nằm trong thiết kế này.** Một memory `UNTRUSTED` sống mãi là
  một rủi ro thật, nhưng chưa có bằng chứng nào trong nghiên cứu về chính sách hết hạn nào
  là đúng, nên không phát minh ra một chính sách.
