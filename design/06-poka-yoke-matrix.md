# Ma trận Poka-Yoke

Mỗi hàng: **một lớp lỗi có thật** → **bằng chứng nó có thật** → **cơ chế chặn** → **mức
Poka-Yoke** → **kiểm bằng cách nào**.

Ba mức, theo nghĩa chặt:

| mức | nghĩa | ví dụ đời thường |
|---|---|---|
| **1 — Cảnh báo** | tài liệu nói đừng làm; không có gì chặn | biển "cẩn thận bậc thang" |
| **2 — Phát hiện** | làm sai được, nhưng hệ thống báo ngay | máy giặt kêu khi cửa chưa đóng |
| **3 — Chặn** | **không biểu diễn được** cái sai | phích cắm ba chấu không cắm ngược được |

Luật của bản thiết kế: **một invariant chỉ được tính là đã sửa nếu đạt mức 3, hoặc mức 2
với lý do ghi rõ vì sao mức 3 bất khả thi.** Lý do có bằng chứng: 23 vòng review tìm ra
**0** lỗi bảo mật; 16 vòng *chạy* tìm ra **4** ([§00](../research/00-executive-summary.md)).
Mức 1 là review đội lốt cơ chế.

---

## A. Bảy khuyết điểm của cả ngành

| # | lớp lỗi | bằng chứng | cơ chế | mức | kiểm bằng |
|---|---|---|---|---|---|
| 1 | Approval là trạng thái quyền, không phải quyết định audit được | 30 gói, 3 ngôn ngữ, không ngoại lệ ([§09](../research/09-memory-context-multiagent-hitl.md) §14) | `Decision` append-only; `Actor` **không có** biến thể `Model`; `decided_at`/`run_id` do runtime điền | **3** | model không dựng được `Decision` — không có constructor nào nhận `Actor` từ tool |
| 1b | Grant "vĩnh viễn" (`always_approve` của openai-agents) | ([§09](../research/09-memory-context-multiagent-hitl.md) §14) | "vĩnh viễn" **không biểu diễn được**: `expires_at=None` nghĩa là *chỉ lần này*; cộng trần `max_grant_ttl` | **3** | không có giá trị nào của `expires_at` nghĩa là bất tận |
| 2 | Cơ chế an toàn tồn tại nhưng không được bật | `agent_framework.security` không được `_harness/` import ([§09](../research/09-memory-context-multiagent-hitl.md) §16bis) | R-1: budget/taint/permission nằm trên đường đi bắt buộc; plugin chỉ cho policy. PLUG-1 (K-13, đổi tên từ `P-3` gốc): tập tác dụng phụ khi có plugin ⊆ khi không có | **3** | property test PLUG-1 |
| 3 | Trạng thái chia sẻ rò rỉ giữa run đồng thời | `threading.local()` set xuyên `await`, fail-open ([§09](../research/09-memory-context-multiagent-hitl.md) §16bis) | R-4: state đã checkpoint là bộ nhớ **duy nhất**; không singleton, không `ContextVar` (nó không đi xuyên node LangGraph) | **2** | test hai run xen kẽ; mức 3 cần kiểu linear mà Python không có |
| 4 | Chặn số bước chứ không chặn tiền | không dự án nào `reserve()` trước ([§03](../research/03-safety-reliability.md) §20) | `Budget.usd` **bắt buộc, không mặc định, không `None`**; I-2: không lời gọi model nào chạy không có `Reservation` mở | **3** ở construction, **2** ở runtime | `Budget()` là `TypeError`; wrapper raise |
| 5 | Không idempotency ở mức tool call | không gói nào có; agno chỉ ở run submission ([§07](../research/07-remaining-python.md)) | effect log **luôn bật** cho `write`/`danger`; không có mức "tắt" | **3** | không có giá trị enum nào tắt được |
| 6 | Memory write không ghi provenance | không hệ thống nào ghi ([§09](../research/09-memory-context-multiagent-hitl.md) §10.2) | `provenance` là keyword **bắt buộc không mặc định** trên `Store.put`; M-1/M-2/M-3 | **3** | không gọi được `put` mà thiếu `provenance` |
| 7 | Model cầm công tắc an toàn của chính nó | `mode_set(approval_mode="never_require")` ([§09](../research/09-memory-context-multiagent-hitl.md) §14.2) | R-3; `effect` **không có default** nên công tắc không trốn trong một tham số có mặc định | **3** | quên `effect` là `TypeError` lúc định nghĩa tool |

---

## B. Lỗi tự tìm ra trong chính bản thiết kế này

Đây là phần đáng tin nhất của tài liệu, vì nó không phải lời tự khen. Hai vòng review đối
kháng tìm ra **58 phát hiện**; bảng dưới là những cái đã sửa và cơ chế thay thế.

| mã | lỗi trong bản nháp | cơ chế thay thế | mức |
|---|---|---|---|
| S-2 | `unguarded_paths()` DFS từ `START`, mà resume vào **giữa** graph ⇒ TTL và thu hồi mất im lặng | **I-1**: gate là *tiền điều kiện tại chỗ tiêu thụ*, không phải một cạnh — node `tools` tra lại `DecisionLog` ngay trước từng call | **2** (kiểm lúc chạy) |
| S-1 | `GUARDED` canh **tên node**, nên model call của compaction đi ngoài trần | **I-2**: cưỡng chế ở **seam** — `ModelProvider` bọc một lần, raise nếu không có `Reservation` | **3** |
| S-3 | Trục confidentiality **không có nguồn phát** — nửa lattice là trang trí | Hai nguồn trên đường đi bắt buộc: `Secret[T]`, và `emits` chỉ **operator** đặt được | **3** |
| S-5 | `recall` tin `provenance` **đọc từ chính store** ⇒ dữ liệu untrusted tự khai nhãn | M-1 `recall` luôn `join(UNTRUSTED)`; M-2 provenance chỉ **nâng**; M-3 ngoại lệ cần operator opt-in **và** MAC do harness ký | **3** |
| K-25 | `idempotency=NONE` mặc định ⇒ khuyết điểm #5 chỉ sửa cho ai nhớ opt-in | effect log luôn bật; enum 3 giá trị → 1 `bool` | **3** |
| K-26 | `Workspace` bắt buộc mà **0 dòng đặc tả** | §4bis, và mục *"KHÔNG được bảo đảm"* quan trọng hơn phần bảo đảm | **2** + trung thực |
| K-16 | Ví dụ Mức 1 không chạy được theo đặc tả `fn` | `@tool` **sinh** adapter từ type hints; `ctx` là **tuỳ chọn** | **3** |
| K-20 | `ToolInputInvalid` phá được hợp đồng `HarnessError` | lớp con nhận đúng `what/got/fix/doc` bắt buộc | **3** |
| K-2 / K-5 / K-1 | ba trừu tượng **không có chỗ đáp** | cắt hẳn | — |

---

## C. Cái KHÔNG đạt mức 3, và vì sao

Mục này quan trọng hơn hai mục trên. Goose sai chính ở chỗ **hứa nhiều hơn cưỡng chế được**
([§05](../research/05-ideal-harness.md) §31-8), nên một bảo đảm mà người vận hành *tưởng*
mình có nguy hiểm hơn một bảo đảm họ biết là không có.

| cái gì | mức thật | vì sao không lên mức 3 |
|---|---|---|
| `Workspace` cách ly tool | **2** | biên **trong tiến trình**. Tool cố ý độc hại gọi thẳng `open()`/`socket()` thì không chặn được. Chống tool viết ẩu và model bị injection, **không** chống tác giả tool thù địch — muốn thế phải cắm `Sandbox` có biên tiến trình |
| `egress` allowlist | **2** | không chặn exfiltration **qua host được phép**. Việc đó thuộc trục confidentiality, không thuộc `egress` |
| Cách ly giữa tenant | **2** | R-4 là *điều kiện cần*, không phải bằng chứng đủ. Store bên ngoài chưa xác minh |
| Đếm token trước khi gửi | **2** | không ai có ngân sách token chính xác; nén theo ước lượng rồi bị provider từ chối là kịch bản thật |
| `unguarded_paths()` | **2**, miền hẹp | chỉ chứng minh về **đường đi từ `START` trong graph tĩnh**. I-1 và I-2 phủ phần còn lại, và cả hai kiểm **lúc chạy** |
| Exactly-once cho tool `write` | **2** | at-most-once là trần thật của harness một mình. Nếu tiến trình chết đúng lúc HTTP request đang bay, không bản ghi cục bộ nào phân biệt được "đã tới" với "chưa tới". Exactly-once chỉ đạt khi upstream nhận key |

---

## D. Đọc bảng này thế nào

Ba câu.

**Một cơ chế mức 3 không cần kỷ luật con người.** Đó là toàn bộ khác biệt giữa bản thiết
kế này và những gì nghiên cứu tìm thấy: `handle_tool_error` của LangChain, `approval_mode`
của Microsoft, `input_filter` của openai-agents, `start_on` của LangChain đều là **mức 1
hoặc 2 mặc định tắt** — chúng bảo vệ ai nhớ bật.

**Một cơ chế mức 2 phải nói rõ nó là mức 2.** Mục C tồn tại vì thế.

**Một cơ chế mức 1 không phải cơ chế.** Nếu chỉ có tài liệu nói đừng làm, hàng đó thuộc
[`07-risks-and-open-issues.md`](07-risks-and-open-issues.md), không thuộc đây.
