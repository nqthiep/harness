# Review đối kháng — KISS, over-engineering, DX

**Phạm vi:** toàn bộ `design/*.md` (7 tệp, 3750 dòng), đối chiếu với
`research/11-workflow-and-dx.md` §22, §45 và `research/05-ideal-harness.md` §33 (Minimal
Core), §35–36.

**Luật dùng để xử:** [`00-foundation.md`](00-foundation.md) §8.4 —
> *"KISS / NOT-OVER-ENGINEER. Nếu một cơ chế không sửa một khuyết điểm **đo được**, cắt nó."*

và yêu cầu **Extreme DX** của người dùng: mức đơn giản nhất phải đến được với người mới
hoàn toàn.

**Tệp này chỉ nhận xét. Nó không sửa tệp thiết kế nào.**

---

## Tóm tắt trong ba câu

1. **Bộ khung ý tưởng rất tốt và đã tự cắt đúng nhiều chỗ** (một trục `Effect`, 2 hook thay
   vì 6, 1 parallel mode thay vì 3, 2 compaction strategy thay vì 5, từ chối state machine
   cho business logic). Xem §6 — đừng để ai cắt nhầm những chỗ đó.
2. **Nhưng bản thiết kế đã vượt xa "minimal core" của chính nghiên cứu**: §33 liệt kê **8
   thứ trong core**; bản thiết kế định nghĩa **62 class** và **38 mã bất biến** (có 3 va
   chạm namespace). Con số "14 tên" ở [`01`](01-core-api.md) §1 sai ngay bằng ví dụ của
   chính tệp đó.
3. **Zero-to-Agent không tiệm tiến và Mức 1 không chạy được** theo đặc tả của
   [`03`](03-tools-and-mcp.md): ví dụ tool ở Mức 1 là hàm đồng bộ không có `ToolCtx`, còn
   `ToolSpec.fn` bắt buộc là `Callable[[Mapping, ToolCtx], Awaitable[Any]]`. Mức 2 nhảy 13
   khái niệm trong một bước và chứa một lỗi `input()` chặn event loop.

---

## Đếm khái niệm — con số thật

### Đo trên nguồn

| phép đo | con số | cách đo |
|---|---:|---|
| `class` được định nghĩa trong `design/*.md` | **62** | `grep -oE "class [A-Za-z_]+" design/*.md \| sort -u` (đã trừ 4 từ trong văn xuôi) |
| type alias / `NewType` công khai | 10 | `Actor`, `EndStrategy`, `ToolName`, `ServerLabel`, `IdempotencyKey`, `NodeName`, `ModelHandler`, `ToolHandler`, `DepsT`, `OutT` |
| hằng số `Final` người dùng có thể phải chỉnh | 8 | `AS_OF`, `CLEARED`, `COMPACT_AT`, `EDIT_AT`, `INPUT_MARGIN`, `KEEP_RECENT_STEPS`, `MIN_USEFUL_OUTPUT_TOKENS`, `EFFECT_PROFILES` |
| mã bất biến (`P-n`, `R-n`, `C-n`, `I-n`, `M-n`, `S-n`, `T-n`, `W-n`, `D-n`) | **38** | trong đó **3 va chạm**: `P-3`, `R-1…R-3`, `C-1…C-4` (xem K-13) |
| tên trong "API công khai tối thiểu" theo tuyên bố | 14 | [`01`](01-core-api.md) §1 |
| tên **thực sự** được `import` trong 3 ví dụ của chính tệp đó | **20** | 14 tên ví dụ + 6 tên submodule; chỉ 8/20 nằm trong danh sách 14 |

### Người dùng phải học bao nhiêu tên trước khi…

Đếm "tên" = mọi thứ phải gõ hoặc phải hiểu để đọc được đoạn code: kiểu, tham số
constructor, method, thuộc tính của `Result`, và mỗi mini-DSL dạng chuỗi.

| mốc | tên mới | cộng dồn | cái gì |
|---|---:|---:|---|
| **Mức 0** — agent chạy được | **8** | 8 | `Agent`, `name`, `job`, `model`, `budget`, DSL `"$0.05"`, `run_sync`, `.text` |
| **Mức 1** — thêm tool | **+4** | 12 | `tool`, `effect=`, `Effect` (4 giá trị), `tools=` |
| **Mức 2** — có tác dụng phụ | **+13** | **25** | `Approver`, `Answer`, `Verdict` (3 giá trị), `Human`, `Channel`, `Workspace`, DSL `egress=`, `sandbox=`, `approve=`, `req.scope` → `Scope`, `req.estimated_cost`, `Actor`, `UnsafeToolSetError` |
| **Mức 3** — "production" | **+16** | **41** | `checkpointer`/`SqliteCheckpointer`, `policies`/`Policy`/`DenyHosts`, `plugins`/`Plugin`/`Retry`/`CostReport`, `end_strategy` (3 giá trị), `stream`/`Event`, `run_id`, `resume`, `StopReason` (8 giá trị), `Result.pending`/`.cost`/`.label`/`.decisions`, `Decision`, `Label` (2 trục × 2 giá trị) |
| **+ viết tool production** ([`03`](03-tools-and-mcp.md)) | +10 | **51** | `ToolCtx`, `IdempotencyMode` (3), `ToolInputInvalid`, `ToolUnavailable`, `ToolOutcome` (3), `CancelToken`, `timeout_s`, `accepts_tainted`, `max_confidentiality`, `server` |
| **+ dùng MCP** | +6 | **57** | `McpServerPolicy`, `ServerIdentity`, `trusted`, `default_effect`, `effects`, `allow` |
| **+ dùng memory** ([`05`](05-cost-and-memory.md)) | +4 | **61** | `Provenance`, `Memo`, `Store`, luật `recall = external` |

**Kết luận về con số.** 8 tên cho "hello world" là **tốt** — ngang hoặc dưới mọi framework
trong nghiên cứu, và ba trong tám (`job`, `budget`, `.text`) tự giải thích được với một học
sinh 10 tuổi. **41 tên để tới "production"** là quá nhiều so với tuyên bố "mỗi mức mới lộ ra
đúng một khái niệm" — thực tế là 8 → 4 → **13** → **16**. Bước từ Mức 1 lên Mức 2 là bước
gãy: **13 khái niệm trong một lần**, mà tệp mô tả nó là "sandbox và approval xuất hiện".

**Đồng nghĩa trá hình đã tìm thấy (chi tiết ở K-3, K-4, K-21):**

| khái niệm | các tên đang dùng cho nó | ở đâu |
|---|---|---|
| yêu cầu gửi cho người duyệt | `ApprovalRequest` · `AskRequest` · `PauseRequest` | 01 §1.4 · 02 §2.2 · 04 §4.3 |
| câu trả lời của người duyệt | `Answer` · `AskOutcome` · (`ruling=`) | 01 §1.4 · 02 §2.2 · 01 §2 Mức 3 |
| người/kênh trả lời | `Approver` · `ApprovalProvider` | 01 §1.4 · 02 §2.2 |
| sổ ghi `Decision` | `DecisionLog` · `AuditSink` · "audit store" | 02 §2.4 · 02 §3.1 · 04 §4.3 |
| lỗi tool "sửa được" / "dứt điểm" | `Retry`/`ToolFailed` · `ToolInputInvalid`/`ToolUnavailable` | 01 §5.4 · 03 §2.2 |

Năm khái niệm đang mang **13 cái tên**. Đó là 8 tên có thể xoá mà không mất một bảo đảm nào.

---

## 1. Cắt ngay

### K-1 — `Quarantine` + `Quarantined[T]` (02 §5) · **cắt ngay**

**Cái gì.** Một model phụ, rẻ hơn, để suy luận trên nội dung `UNTRUSTED`; kiểu generic
`Quarantined[T]` với ràng buộc "`T` phải là kiểu đóng"; `RunConfig.quarantine`; luật
fail-closed riêng khi `quarantine=None`.

**Vì sao thừa.** Chính [`02`](02-safety-engine.md) mục *Chưa đủ evidence* viết:
> *"Mẫu dual-LLM/CaMeL chỉ có **một** cài đặt trong toàn nghiên cứu, và nó `@experimental`,
> không được wire vào, và không concurrency-safe… **Không có eval nào so sánh tỉ lệ
> prompt-injection thành công có và không có quarantine.**"*

Đây là định nghĩa chính xác của thứ mà luật §8.4 bảo phải cắt: cơ chế không sửa một khuyết
điểm **đo được**. Nó còn kéo theo: một trường trong `RunConfig`, một generic không kiểm
được bằng type checker Python (không có cách nào diễn đạt "kiểu đóng"), một nhánh
fail-closed thứ hai song song với nhánh `ASK`, và một chi phí model không lường trước tính
vào cùng `Ledger`.

**Đề xuất.** Cắt hẳn khỏi `02`. Chuyển toàn bộ mục §5 sang
`07-risks-and-open-issues.md` dưới tiêu đề *"ý tưởng có kiến trúc, chờ eval"*. Đường đi
bình thường mà chính tệp đã mô tả — `UNTRUSTED` + `danger` ⇒ `ASK` (có người) hoặc `DENY` —
đã đủ và đã có bằng chứng.

---

### K-2 — `deps_type` / `DepsT` (01 §1.1) · **cắt ngay**

**Cái gì.** `Agent` generic trên `DepsT`, tham số `deps_type: type[DepsT] = object`, tham số
`deps=` trên cả bốn cách chạy.

**Vì sao thừa.** Hai lý do độc lập, mỗi lý do đủ để cắt:

1. **Generic không có chỗ đáp.** `ToolSpec.fn` ở [`03`](03-tools-and-mcp.md) §1.1 là
   `Callable[[Mapping[str, Any], "ToolCtx"], Awaitable[Any]]`. `ToolCtx` (03 §6.2) có 6
   trường: `run_id`, `call_id`, `cancel`, `label`, `idempotency_key`, `deadline` — **không
   có `deps`**. Vậy `DepsT` đi vào constructor, đi vào `run()`, và **không tới được bất kỳ
   người dùng nào**. `01` viết `ToolSpec[DepsT]`, `03` viết `ToolSpec` không generic — hai
   tệp không đồng ý nó có tồn tại hay không.
2. **Chính tệp tự khai là chưa đo.** [`01`](01-core-api.md) *Chưa đủ evidence* #3:
   *"`deps_type` generic có thể làm dốc learning curve… Việc mức 0 không cần chạm tới
   `deps_type` là suy luận, không phải kết quả đo."* Và §22/§45 của nghiên cứu ghi rõ
   learning curve **không được đo**. Trích dẫn duy nhất là "PydanticAI có nó" — theo §8.4
   đó là ý kiến, không phải phát hiện.

**Đề xuất.** Cắt `deps_type`, `DepsT`, và `deps=` khỏi cả bốn signature. Người cần DI đóng
gói vào closure của tool — Python đã có `functools.partial`. Nếu sau này có nhu cầu đo
được, thêm lại bằng cách cho `ToolCtx` một trường `deps: Any` — một dòng, không cần generic
trên `Agent`. Giữ `OutT`/`output_type` (nó có chỗ đáp thật: `Result.output`).

---

### K-3 — Ba bộ tên cho một vòng approval (01 §1.4 · 02 §2.2 · 04 §4.3) · **cắt ngay**

**Cái gì.**

| vai | `01` | `02` | `04` |
|---|---|---|---|
| request | `ApprovalRequest(scope, reason, label, estimated_cost)` | `AskRequest(call, ruling, reason, proposed_scope, max_grant)` | `PauseRequest(call_id, tool, effect, args, reason)` |
| response | `Answer(verdict, reason, expires_at)` | `AskOutcome(verdict, actor, reason, scope, grant_for)` | `ResumeToken(decision_id)` |
| người trả lời | `Approver(fn, *, actor)` | `ApprovalProvider.ask()` | — |

**Vì sao thừa — và vì sao đây không chỉ là đổi tên.** Ba bộ **mâu thuẫn về ngữ nghĩa**, không
chỉ về chính tả:

- **`actor` đến từ đâu.** `01` gắn `actor` **lúc dựng `Approver`** và gọi đó là "cách bất
  biến D-1 được thực thi *bằng kiểu*". `02` để `AskOutcome.actor` do **provider tự khai mỗi
  lần trả lời** ("provider phải nêu tên người"). Đúng một trong hai là D-1; cái còn lại là
  đúng thứ mà thiết kế đang chê agno.
- **hạn dùng.** `01` cho người duyệt điền `expires_at` (thời điểm tuyệt đối). `02` chỉ nhận
  `grant_for: timedelta` rồi runtime `_cap()` theo `max_grant`. Với `01`, một UI hỏng cấp
  được grant 100 năm — đúng lỗ hổng `always_approve` mà `02` §2.5 nói đã bịt.
- **resume chở gì.** `01` §1.2: `resume(run_id, *, answer: Answer | None)`. `04` §4.3:
  *"Payload **DUY NHẤT** `Command(resume=…)` chấp nhận"* là `ResumeToken(decision_id)`, và
  luật fail-closed #1 nói mọi thứ không phải `ResumeToken` ⇒ `DENY`. Theo `04`, lời gọi
  `resume(answer=Answer(...))` của `01` bị **từ chối**.

**Đề xuất.** Giữ **một** bộ, và bộ của `02` là bộ đúng về mặt an toàn:

- `AskRequest` → đổi tên thành `ApprovalRequest` (tên của `01`, dễ đọc hơn), giữ trường của
  `02`.
- `AskOutcome` → đổi tên thành `Answer`, giữ `grant_for` (bỏ `expires_at`), **bỏ `actor`
  khỏi kiểu** và gắn nó ở `Approver(fn, *, actor)` theo `01`.
- `ApprovalProvider` → xoá, dùng `Approver` của `01`.
- `PauseRequest` → xoá; `04` phát chính `ApprovalRequest` đã redact.
- `ResumeToken` → giữ, vì nó là bảo đảm thật (kênh resume không chở được `bool`), nhưng
  `01` phải sửa `resume()` cho khớp: `resume(run_id, *, decision: DecisionId)`.

Xoá được **4 kiểu** và **1 protocol**.

---

### K-4 — `DecisionLog` và `AuditSink` là một sổ mang hai protocol (02 §2.4, §3.1) · **cắt ngay**

**Cái gì.** `DecisionLog` (`append` / `lookup` / `since`) và `AuditSink` (`commit` / `emit`).
`Decision` đi qua **cả hai**: §2.1 bước (5) `AuditSink.commit(decision)`, §2.4
`DecisionLog.append(decision)`.

**Vì sao thừa.** Cả hai là append-only, cả hai durable, cả hai khoá theo `run_id`, cả hai
chứa `Decision`. Bằng chứng rằng chính bản thiết kế coi chúng là một: [`04`](04-runtime-durability.md)
§4.3 viết *"Đổi `ResumeToken` lấy `Decision` từ **audit store**"* — tức tra `lookup` trên cái
mà `02` gọi là `AuditSink`. Hai protocol cho một store nghĩa là hai cài đặt phải giữ đồng
bộ, và câu hỏi "ghi vào cái nào trước" không có câu trả lời ở đâu cả.

**Đề xuất.** Gộp thành **một** `AuditSink` với bốn method: `commit` (durable, cho
`Decision`), `emit` (best-effort, cho event), `lookup`, `since`. `DecisionLog` biến mất.
Ranh giới durable/best-effort là ranh giới **method**, không phải ranh giới **class** — và
`02` §3.1 đã lập luận đúng điều đó cho `commit` vs `emit`.

---

### K-5 — `Snapshottable` Protocol (04 §5.2): **không implementer nào khớp** · **cắt ngay**

**Cái gì.**
```python
class Snapshottable(Protocol):
    def snapshot(self) -> Mapping[str, Any]: ...
    @classmethod
    def restore(cls, snap: Mapping[str, Any] | None, /) -> Self: ...
```
`04` khai ba implementer: `Ledger`, `Label`, tập `DecisionId`.

**Vì sao thừa.** Không cái nào cài được protocol này:

- `Ledger.snapshot()` ở [`05`](05-cost-and-memory.md) §A.3 trả `LedgerState`, **không phải**
  `Mapping[str, Any]`; và `Ledger.restore(cls, budget, state, *, clock)` có **ba** tham số,
  không phải một positional-only.
- `Label` ([`00`](00-foundation.md) §3.2) không có `snapshot`/`restore`, và `04` §4.3 nói
  nhãn đi vào state dưới dạng **hai chuỗi rời** (`label_integrity`, `label_confidentiality`),
  tức không đi qua protocol này.
- "tập `DecisionId`" là `list[str]` — một builtin không cài protocol nào.

Một Protocol với **0 implementer đúng** là trừu tượng thuần tuý. Nó cũng là loại lỗi mà `04`
§3.2 tự cảnh báo: *"một checker luôn trả rỗng cũng pass"*.

**Đề xuất.** Cắt `Snapshottable`. Giữ luật S-1/S-2/S-3 (chúng là những thứ có giá trị thật:
round-trip, JSON-serialisable, tiền là `str` của `Decimal`) và phát biểu chúng như **test
tính chất trên state dict**, không như một protocol. `Ledger.snapshot()/restore()` giữ
nguyên signature của `05` — nó là cái duy nhất có cài đặt thật.

---

## 2. Nên cắt

### K-6 — Trục `Confidentiality` không có nguồn phát · **nên cắt (hoặc bổ sung nguồn)**

**Cái gì.** Nửa thứ hai của lattice hai chiều: `PUBLIC < SECRET`, `spec.max_confidentiality`,
luật "context `SECRET` không gọi được sink `PUBLIC`", `Store.max_confidentiality`, W-2, R-3.

**Vì sao đáng ngờ.** Đi ngược từ đích: **không có gì trong toàn bộ thiết kế đặt nhãn
`SECRET` lên bất cứ thứ gì.**

- `label_after(spec, current) = current.join(spec.emits)` (02 §4.1) — nhưng `ToolSpec` ở
  [`03`](03-tools-and-mcp.md) §1.1 **không có trường `emits`**.
- Không tool nào, không input nào, không cấu hình nào trong 5 tệp sinh ra
  `Confidentiality.SECRET`.
- `max_confidentiality` cũng không có trong `ToolSpec` của `03` (chỉ có trong `@tool` của
  `01` — xem K-15).

Kết quả: `confidentiality` luôn bằng `PUBLIC`, `check_flow` nhánh thứ hai không bao giờ
chạy, W-2/R-3 là luật về một trạng thái không đạt tới được. Và `02` *Chưa đủ evidence* tự
ghi: *"Không gói Python nào trong nghiên cứu có kiểu `Secret` chuyên dụng… **không có bằng
chứng** về việc hai bậc là đủ hay thiếu."*

**Đề xuất.** Chọn một:

- **(a) Cắt** — `Label` thành một trục `Integrity` (đúng ADR-011 hiện có), xoá
  `max_confidentiality`, W-2, R-3, và nhánh thứ hai của `check_flow`. Xoá ~6 khái niệm.
- **(b) Giữ nhưng phải có nguồn phát** — thêm `emits: Label` và `max_confidentiality` vào
  `ToolSpec` của `03`, và nêu **ít nhất một** đường mặc định sinh `SECRET` (ví dụ: giá trị
  đọc từ secret manager, hoặc `deps` được đánh dấu).

Không được giữ nguyên hiện trạng: một nửa lattice vừa tốn khái niệm vừa tạo cảm giác an
toàn sai.

---

### K-7 — Bốn lớp phòng thủ cho một sai số chưa đo (05 §B.3) · **nên cắt lớp 2**

**Cái gì.** `INPUT_MARGIN = 1.15`, `calibration` (tỉ số tự học, chỉ tăng),
`hard_max_input_tokens()` + `Reservation.exact`, đệm 20% (`COMPACT_AT = 0.80`), và một vòng
thử lại xác định. Năm cơ chế cho một bài toán.

**Vì sao thừa.** `Reservation.exact` là lớp không mua được gì quan sát được: khi ngân sách
rộng, ước lượng nhân 1.15 **cũng đã** nằm trong ngân sách, nên `exact=True` chỉ đổi một
`bool` mà không đổi hành vi nào ở đâu — không mục nào trong 5 tệp đọc `Reservation.exact`.
Và `05` tự khai: *"`INPUT_MARGIN = 1.15` là con số kinh nghiệm… chỉ mới quan sát trên một
họ model."*

**Đề xuất.** Cắt `hard_max_input_tokens()` và `Reservation.exact`. Giữ `INPUT_MARGIN` +
`calibration` + đệm 20% + một-lần-thử-lại (bốn thứ này có vai rõ ràng và không chồng nhau).
Ghi lại `hard_max_input_tokens` trong `07-risks` như một lựa chọn nếu số đo cho thấy margin
không đủ.

---

### K-8 — `IdempotencyMode` ba giá trị (03 §1.1) · **nên cắt xuống hai**

**Cái gì.** `NONE` / `KEYED` / `NATIVE`.

**Vì sao thừa.** `NATIVE` khác `KEYED` ở **đúng một** hành vi: `ctx.idempotency_key` được
truyền cho `fn` để tool gắn lên upstream. Nhưng runtime **luôn** sinh key (03 §4.2) — không
có lý do gì để giấu nó với tool ở chế độ `KEYED`. Nếu key luôn có mặt trong `ToolCtx`, tác
giả tool dùng hay không dùng là việc của tác giả, không phải một giá trị enum.

Và `NONE` là vấn đề riêng, xem **K-25**.

**Đề xuất.** `ToolSpec.idempotent: bool` (mặc định `True` cho `write`/`danger` — xem K-25).
`ctx.idempotency_key` luôn khác `None`. Xoá một enum ba giá trị khỏi bề mặt tác giả tool;
bảng "harness đảm bảo gì" ở §4.5 vẫn phát biểu được bằng văn xuôi.

---

### K-9 — `run()` / `try_run()` / `Result.raise_for_status()` — ba cách nói một điều · **nên cắt một**

**Cái gì.** `run()` raise, `try_run()` không raise, và `Result` có **cả** `.ok` **lẫn**
`.raise_for_status()`. `RunFailed` lại mang `result: Result[Any]`.

**Vì sao thừa.** Bốn cách viết cùng một chương trình:
`run()` ≡ `try_run().raise_for_status()` ≡ `try_run()` + `if not r.ok` ≡
`try_run()` + `match r.stop_reason`. Và `01` tự khai: *"`run` async + `run_sync` là quy ước,
**không phải phát hiện**."* Không có phát hiện nghiên cứu nào đòi cả bốn.

**Đề xuất.** Giữ `run()` (raise — đường của người mới, ném `RunFailed` mang `.result`) và
`try_run()` (đường của người viết service). **Cắt `raise_for_status()`** — nó là `run()` viết
lại. Giữ `.ok` (một property rẻ, đọc được). Nếu phải cắt sâu hơn: cắt `try_run()`, vì
`except RunFailed as e: e.result` đã cho đúng điều đó.

---

### K-10 — Taxonomy OTel 9 span × ~50 attribute (04 §8.2) · **nên cắt xuống 4**

**Cái gì.** `harness.run` / `.step` / `.budget` / `.model` / `.policy` / `.approve` /
`.tool` / `.subagent` / `.finish`, tổng cộng khoảng 50 attribute đã liệt kê tên.

**Vì sao đáng ngờ.** Bằng chứng được viện dẫn là **mật độ mã** của google-adk-java (11,4
otel/kLOC). Mật độ mã của người khác không phải một khuyết điểm đo được của ta. Khuyết điểm
đo được là *"observability genuine only in google-adk and thin elsewhere"* — nó đòi **có**
observability, không đòi **chín loại span**. Và `04` tự khai: *"nghiên cứu **không** đo
overhead runtime của mật độ đó. Sampling rate mặc định chưa có cơ sở."*

`harness.step` mang đúng một attribute (`step`) — một span cho một số nguyên. `harness.budget`
và `harness.finish` mang thông tin đã có trên `harness.run`.

**Đề xuất.** v1 giữ **4 span**: `harness.run` (root), `harness.model`, `harness.policy`,
`harness.tool`. Bốn quy tắc nội dung (§8.2 điểm 1–4: `effect` trên mọi span tool,
`decision_id` chứ không `approved=true`, `actor_kind` chứ không `actor_id`, chỉ
`args_sha256`) là phần **thật sự có giá trị** và phải giữ nguyên — chúng sửa khuyết điểm đo
được. Năm span còn lại chuyển sang `07-risks` như "mở rộng khi có nhu cầu".

---

## 3. Cân nhắc

### K-11 — `end_strategy` ở constructor Mức 3 · **cân nhắc**

Bằng chứng là thật (nghiên cứu §31 bài học 1, và PydanticAI đã đổi mặc định `early` →
`graceful` vì mặc định cũ sai). Nhưng đây là **tham số duy nhất** trong 13 tham số không gắn
với một chế độ hỏng nào của harness này, và nó thêm một `Literal` ba giá trị mà không mục
nào trong 5 tệp mô tả ba giá trị đó làm gì khác nhau **ở đây**. Thêm nữa, từ `"exhaustive"`
đang mang hai nghĩa khác nhau trong cùng bản thiết kế: giá trị của `EndStrategy` (01 §1.1)
và tên một parallel mode của pydantic-ai (03 §3.2).

**Đề xuất.** Giữ tham số (bằng chứng đủ), nhưng: (a) chốt mặc định `"graceful"` và **không
đưa nó vào ví dụ Mức 3** — nó không phải một trong "bốn chỗ trống của cả ngành"; (b) đổi tên
giá trị thứ ba để không đụng từ vựng parallel của `03`.

### K-12 — `ServerIdentity.fingerprint` (03 §5.2) · **cân nhắc**

`03` tự khai hai chỗ chưa đủ evidence: rug-pull qua `tools/list` *"không được quan sát trực
tiếp trong bất kỳ source nào đã đọc"*, và *"Định dạng `fingerprint`… chưa đủ evidence để
chốt"* (không có tương đương SPKI cho stdio). Một trường không chốt được định dạng là một
trường chưa nên vào kiểu công khai.

**Đề xuất.** v1 dùng `ServerLabel` (chuỗi) như Microsoft — đó là phần **có** bằng chứng và
là phòng thủ confused-deputy duy nhất tìm được. Ghi `fingerprint` vào `07-risks` với điều
kiện kích hoạt: "khi quan sát được một lần label bị trỏ lại". Xoá một `@value` khỏi bề mặt.

### K-13 — 38 mã bất biến, 3 va chạm namespace · **cân nhắc (nhưng sửa sớm)**

Đo được:

| mã | nghĩa 1 | nghĩa 2 |
|---|---|---|
| `P-3` | plugin chỉ làm yếu đi (01 §4.2) | policy ném lỗi ⇒ fail closed (02 §1.2) |
| `R-1` / `R-2` / `R-3` | bốn quy tắc kiến trúc (00 §5) | ba luật **đọc memory** (05 §C.1) |
| `C-1` … `C-4` | bốn luật **cancel** (03 §6.3) | bốn bất biến **cost** (05 §A.1) |

Thêm: `I-1` ở 03 §4.4, `I-3`/`I-3a`/`I-3b` ở 05 §B.1 — **không có `I-2`**, tức hệ đánh số
giả vờ là một namespace chung nhưng không phải.

Hệ quả DX rất cụ thể: một thông báo lỗi hoặc một comment ghi "vi phạm C-2" là **không giải
mã được** nếu không biết đang ở tệp nào.

**Đề xuất.** Rẻ nhất: gắn tiền tố tệp — `POL-1…4`, `COST-1…4`, `CAN-1…4`, `MEM-R1…R3`, giữ
`R-1…R-4` và `D-1/D-2` cho `00` (chúng là luật toàn cục). Hoặc mạnh hơn theo tinh thần KISS:
**bỏ mã cho những luật chỉ được nhắc đúng một lần** — trong 38 mã, chỉ 9 mã được tham chiếu
chéo từ tệp khác; 29 mã còn lại là chú thích đánh số cho chính đoạn văn ngay bên cạnh.

---

## 4. Mâu thuẫn nội bộ còn lại

(4 mâu thuẫn `Ruling`/`Answer`/`Decision`, cột retry, `Label`, `CancelToken` đã được sửa
trước. Đây là những cái **còn lại**, tất cả đều kiểm được bằng `grep`.)

### K-14 — `resume(ruling=…)` còn sót ba chỗ · **cắt ngay (sửa)**

Signature ở [`01`](01-core-api.md) §1.2 là `resume(self, run_id, *, answer: Answer | None)`.
Nhưng `ruling=` vẫn còn ở:

- `01` §2 Mức 3, dòng 276: `await agent.resume("r-42", ruling=await ask_terminal_for(r.pending))`
- `01` §2, dòng 285: `approval round-trip qua AWAITING_DECISION → resume(ruling=…)`
- `01` §5.2 bảng: `✅ resume(ruling=…)`
- `01` §6 bảng: `resume(run_id, ruling=…)`

Bốn chỗ, không phải ba. Và cả bốn **đều sai theo `04`** — xem K-3: payload duy nhất được
chấp nhận là `ResumeToken(decision_id)`.

### K-15 — `@tool` có hai signature khác nhau; `ToolSpec` thiếu hai trường đang được dùng · **cắt ngay (sửa)**

| | `01` §1.3 | `03` §1.3 |
|---|---|---|
| `effect` | ✅ | ✅ |
| `name` | ✅ | ✅ |
| `accepts_tainted` | ✅ | ✅ |
| `max_confidentiality` | ✅ | ❌ **thiếu** |
| `idempotency` | ❌ **thiếu** | ✅ |
| `timeout_s` | ❌ **thiếu** | ✅ |
| trả về | `ToolSpec[DepsT]` (generic) | `ToolSpec` (không generic) |
| overload `NoReturn` cho `@tool` trần | ✅ | ❌ |

Và `ToolSpec` của `03` §1.1 **không có** `max_confidentiality` lẫn `emits`, trong khi
[`02`](02-safety-engine.md) §4.1 đọc cả hai (`spec.max_confidentiality`, `spec.emits`). Đây
là nguyên nhân gốc của K-6.

### K-16 — Ví dụ Mức 1 không hợp lệ theo `03` · **cắt ngay (sửa)** — đây là lỗi DX nặng nhất

```python
@tool(effect=Effect.READ)
def word_count(text: str) -> int:      # đồng bộ, tham số là str, không có ToolCtx
    return len(text.split())
```
vs. [`03`](03-tools-and-mcp.md) §1.1 và §6.2:
```python
fn: Callable[[Mapping[str, Any], "ToolCtx"], Awaitable[Any]]
# "ToolCtx là tham số bắt buộc trong signature của fn"
```

Ba khác biệt: đồng bộ vs `Awaitable`; tham số đặt tên vs một `Mapping`; không `ToolCtx` vs
`ToolCtx` bắt buộc.

**Đây là chỗ "một học sinh 10 tuổi hiểu được" sống hay chết.** Ví dụ Mức 1 là đúng — nó là
lý do bản thiết kế này đáng tồn tại. Đặc tả của `03` mới là cái phải sửa: decorator `@tool`
phải **sinh** ra adapter `(Mapping, ToolCtx) -> Awaitable` từ một hàm thường có type hints,
và `ToolCtx` phải là **tuỳ chọn** (chỉ tiêm khi tác giả khai một tham số tên `ctx`). Nếu
không, Mức 1 không chạy và cả thang Zero-to-Agent sụp từ bậc hai.

### K-17 — `Budget` có mặc định và cho phép `usd=None` · **cắt ngay (sửa)**

[`05`](05-cost-and-memory.md) §A.1:
```python
@value
class Budget:
    usd: Decimal | None = Decimal("0.50")
    steps: int = 20
    wall_clock_s: float = 300.0
```
`Budget()` dựng được không tham số, và `Budget(usd=None)` dựng được một budget **không có
trục tiền**. Điều đó phá:
- `01` §1.1: *"`budget` **bắt buộc**, phải có trục tiền"*;
- `01` §3.4 bảng Poka-Yoke: *"`budget` thiếu trục tiền → chặn ở **construction**"*;
- và bất biến thứ hai của `00` §1.

**Đề xuất.** `usd: Decimal` (không `None`, không mặc định). Ba trục còn lại giữ mặc định.

### K-18 — `StopReason` thiếu hai giá trị đang được dùng · **cắt ngay (sửa)**

`01` §5.2 liệt 8 giá trị. Nhưng:
- `04` §4.5: `stop_reason="graph_changed"`
- `05` §B.3: `StopReason.TRUNCATED`

Không cái nào trong enum. Enum đóng + hai người dùng ngoài enum = lỗi runtime.

### K-19 — `AuditEvent.type` là `Literal` đóng, thiếu ít nhất 5 loại event đang được phát · **cắt ngay (sửa)**

`02` §3.1: `Literal["decision", "policy.denied", "flow.denied", "budget.denied", "tool.called"]`.

Đang được phát ở nơi khác nhưng không có trong Literal: `policy.allowed` (02 §2.2),
`duplicate_suppressed` (03 §4.4), `run.started` (04 §5.4), `run.finished` (04 §2.2, §6.3),
`taint.raised` (05 §C.1). Và `04` §2.2 nhắc "graph chỉ phát 9/15 event kind" như một lỗi đã
gặp — nhưng bản thiết kế **không có** danh sách 15 event kind ở đâu cả.

**Đề xuất.** Đây cũng là chỗ nghiên cứu §33 đòi *"Event envelope có version"* trong minimal
core. Một bảng event kind duy nhất, ở `00-foundation.md`, và cả `AuditEvent` lẫn `Event` của
`stream()` cùng dùng nó. Xem thêm **K-27**.

### K-20 — `ToolInputInvalid.__init__` không tuân hợp đồng `HarnessError` · **cắt ngay (sửa)**

`01` §3.2 tuyên bố: *"`fix` và `doc` là **keyword bắt buộc**. Không có cách nào raise một lỗi
của harness mà không nói phải làm gì. Tỉ lệ error-context là 100% **theo cấu trúc**."*

`03` §2.2:
```python
class ToolInputInvalid(HarnessError):
    def __init__(self, message: str, *, fix: str | None = None) -> None: ...
```
`message` positional, `fix` **tuỳ chọn**, `got` và `doc` biến mất. Một lớp con phá được hợp
đồng thì hợp đồng không phải "theo cấu trúc" — nó lại thành kỷ luật, đúng thứ §3.2 nói đang
tránh.

### K-21 — Hai tên cho taxonomy lỗi tool, và `Retry` va chạm với chính nó · **cắt ngay (sửa)**

- `01` §5.4: *"Tác giả tool vẫn raise `Retry`/`ToolFailed` tường minh khi biết rõ hơn."*
- `03` §2.2: `ToolInputInvalid` / `ToolUnavailable` + enum `ToolOutcome(RETRY/FAILED/FATAL)`.

Ngoài chuyện hai bộ tên, `Retry` trong `01` là **cả một exception** (§5.4) **và một plugin**
(§2 Mức 3: `plugins=[Retry(on=(…), attempts=3)]`) — cùng một tên, hai thứ, trong cùng một
tệp.

Bảng mặc định cũng khác nhau: `01` §5.4 nói `write` → `ToolFailed` và `danger` → `ToolFailed`;
`03` §2.2 nói `write` → `FAILED` **nếu keyed**, `FATAL` nếu không, và `danger` → `FATAL` luôn.

**Đề xuất.** Tên của `03` đúng hơn (tên mô tả *sự việc*, không mô tả *chính sách* — đó là
lập luận hay nhất của §2.2). `01` §5.4 phải viết lại theo `03`, và plugin đổi tên
`RetryPlugin` hoặc `Backoff`.

### K-22 — "Toàn bộ bề mặt là 14 tên. Một `import`." · **nên cắt (sửa tuyên bố)**

Ví dụ trong cùng tệp `import` **20 tên** từ **4 module**:

- không nằm trong danh sách 14: `Channel`, `Human`, `Approver`(có), `SqliteCheckpointer`,
  `Retry`, `CostReport`, `DenyHosts` — và `from harness.checkpoint`, `harness.plugins`,
  `harness.policy` là ba `import` nữa.
- và trong 14 tên đó, **`Workspace` không được định nghĩa ở bất kỳ đâu** trong 3750 dòng
  (xem K-26), `Result.cost: Money` và `Result.usage: Usage` tham chiếu hai kiểu cũng không
  được định nghĩa, `stream()` trả `AsyncIterator[Event]` với `Event` không được định nghĩa.

**Đề xuất.** Hoặc sửa con số cho đúng (nói "20 tên, 1 import gốc + 3 submodule"), hoặc kéo
`Human`/`Channel`/`Retry`/`CostReport`/`DenyHosts`/`SqliteCheckpointer` lên `harness` top-level
và giữ lời hứa một `import`. Cách thứ hai tốt hơn cho DX; cách thứ nhất trung thực hơn. Điều
không được làm là giữ nguyên — một tuyên bố sai ở dòng 15 của tệp DX là lỗi DX.

### K-23 — Chín tham số vận hành không có đường từ API công khai · **nên cắt (sửa)**

| tunable | ở đâu | tới được từ `Agent(...)`? |
|---|---|---|
| `max_concurrency` | 03 §3.2 ("núm duy nhất còn lại") | ❌ |
| `cancel_grace = 30s` | 04 §6.3 | ❌ |
| `max_grant_ttl = 1h` | 02 §2.5 (`RunConfig`) | ❌ |
| `quarantine` | 02 §5.2 (`RunConfig`) | ❌ |
| `depth` cap = 3 | 04 §7.2 | ❌ |
| retry budget = 3 | 03 §2.2 điểm 4 | ❌ |
| `EDIT_AT` / `COMPACT_AT` / `KEEP_RECENT_STEPS` | 05 §B.2 | ❌ (module const) |
| `INPUT_MARGIN` / `MIN_USEFUL_OUTPUT_TOKENS` | 05 §A.1 | ❌ (module const) |
| `durability` | 04 §4.2 | suy ra tự động ✅ |

`RunConfig` xuất hiện ở `02` với hai trường, không có constructor, không có đường từ `Agent`.
Ba mặt cấu hình song song (tham số `Agent`, `RunConfig`, hằng số module) là chính xác thứ
mà `05` §B.2 gọi là *"một mặt cấu hình rộng là một mặt cấu hình mặc định tắt"*.

**Đề xuất.** Một chỗ duy nhất. Đề nghị: bốn thứ có ý nghĩa vận hành (`max_concurrency`,
`cancel_grace`, `max_grant_ttl`, `depth`) gộp vào một tham số `Agent(limits=Limits(...))`
với mặc định đủ tốt; phần còn lại là hằng số module **không cấu hình được** và nói rõ như
vậy (giống `EFFECT_PROFILES` — `03` §1.2 đã làm đúng cách này).

### K-24 — Vụn vặt nhưng sửa được trong một phút · **nên cắt (sửa)**

- `03` §6.2: block code hỏng — `from harness import CancelToken` rồi ngay sau đó là thân
  class thụt lề mồ côi (`async def wait(...)`, `@property cancelled`, …). Và `wait()` **không
  có** trong định nghĩa chuẩn ở `04` §6.1.
- `04` §6.1: `@value class CancelToken` (bất biến) có method `cancel(self, reason) -> None`
  làm biến đổi trạng thái. `@value` + mutation là mâu thuẫn kiểu.
- `00` §4: `Decision.verdict: Verdict` (đủ 3 giá trị) với comment `# ALLOW | DENY`, trong khi
  `01`/`02` dùng `Literal[Verdict.ALLOW, Verdict.DENY]`. Dùng `Literal` ở cả ba chỗ.
- `00` §4.1: `Scope.args: Mapping[str, str] | None`, nhưng `02` §2.6 so nó với
  `canonical_args(call.arguments)` trên `Mapping[str, Any]`, và `04` §4.3
  `PauseRequest.args: Mapping[str, Any]`. Chốt một kiểu.
- `Verdict` được `import` hai đường: `from harness import Verdict` (01) và
  `from harness.policy.base import Verdict` (03).

---

## 5. Cái đáng lẽ phải có mà thiếu

Đối chiếu bảng "7 khuyết điểm đang sửa" ở [`README.md`](README.md) và Minimal Core §33.

### K-25 — Khuyết điểm #5 (idempotency) **chưa được sửa ở mặc định** · **cắt ngay (sửa)**

`README` hàng 5: *"Không idempotency ở mức tool call — Không ai có"*.
`01` §3.4: *"tool call thiếu idempotency key → **không thể xảy ra** — gateway sinh key, API
không nhận."*
Nghiên cứu §32 điểm 2: *"**Idempotency key là bắt buộc trên mỗi tool call, không phải tuỳ
chọn.**"*

Nhưng `03` §1.1: `idempotency: IdempotencyMode = IdempotencyMode.NONE`, và §4.5:
`NONE` → *"harness đảm bảo: **không gì**"*, và `ToolCtx.idempotency_key: IdempotencyKey | None`.

Nghĩa là: một tool `write` viết theo mặc định **không** có key, **không** có effect log, và
theo §2.2 điểm 2 một exception lạ của nó là `FATAL`. Khuyết điểm số 5 chỉ được sửa cho những
ai nhớ opt-in — **đúng lớp lỗi mà cả bản thiết kế đang chê LangChain** (`handle_tool_error`
per-tool opt-in) và Microsoft (module tốt không được wire vào).

**Đề xuất.** `write` và `danger` **luôn** đi qua effect log; runtime luôn sinh key;
`ToolCtx.idempotency_key` không bao giờ `None`. `read`/`external` không cần. Kết hợp với
K-8: bỏ enum, còn một `bool` cho "upstream nhận key hay không". Đây là thay đổi làm bản
thiết kế **đơn giản hơn** và **đúng lời hứa hơn** cùng lúc.

### K-26 — `Workspace` / `Sandbox`: không có đặc tả · **cắt ngay (sửa)**

`Workspace` là 1 trong 14 tên công khai, xuất hiện trong mọi ví dụ từ Mức 2, mang một
mini-DSL `egress="deny"` / `egress="allowlist:docs.python.org"`, và là chỗ neo cho bài học
§31-8 *"Approval không phải isolation"* (Goose: 4 mode, sandbox đã gỡ, tool chạy quyền
user). **Không có một dòng đặc tả nào cho nó trong 3750 dòng.** `Sandbox` được nhắc 2 lần,
cũng không định nghĩa.

Đây là lỗ nghiêm trọng hơn mọi over-engineering trong danh sách trên: cơ chế bị bắt buộc ở
constructor (`UnsafeToolSetError` khi thiếu) mà không ai biết nó làm gì.

**Đề xuất.** Một mục ngắn — có thể ở `03` — trả lời ba câu: workspace root cưỡng chế thế
nào, `egress` cưỡng chế ở tầng nào (process? DNS? HTTP client?), và **cái gì KHÔNG được bảo
đảm**. Câu thứ ba quan trọng nhất, vì Goose sai chính ở chỗ hứa nhiều hơn cưỡng chế.

### K-27 — `Event` envelope có version: trong Minimal Core, không có trong thiết kế · **nên bổ sung**

§33 liệt "Event envelope có version" là 1 trong 8 thứ **phải** ở core, với lý do *"không có
version thì không đổi được taxonomy mà không phá exporter"*. `01` §1.2 trả
`AsyncIterator[Event]` và ví dụ Mức 3 đọc `ev.type`, `ev.sequence`. **`Event` không được
định nghĩa ở đâu.** Chỉ `AuditEvent` (02 §3.1) có `schema_version` — và nó là kiểu khác, với
`seq` chứ không `sequence`.

**Đề xuất.** Định nghĩa `Event` một lần ở `00-foundation.md` với `schema_version`,
`run_id`, `sequence`, `type`, `payload` — và cho `AuditEvent` là **cùng kiểu đó** với thêm
`at`/durability guarantee. Gộp luôn với K-19 (một bảng event kind duy nhất).

### K-28 — Session / tenant identity: §33 đòi ở core, thiết kế không có · **nên bổ sung hoặc nói rõ là không có**

§33 core item: *"Session/Run identity — tenant/owner/TTL là ranh giới cách ly"*.
§35–36: interface mẫu thiếu 5 thứ, trong đó có **session**.
`01` §1.2 khẳng định: *"Bốn cái đầu ở trên"* — tức nhận là đã có streaming, cancellation,
**session**, approval round-trip.

Nhưng không có `Session` ở đâu. Chỉ có `run_id`. Và multi-tenancy bị đẩy sang *Chưa đủ
evidence* ở **ba** tệp (`02`, `04`, và gián tiếp `05`) với cùng một câu.

**Đề xuất.** Trung thực là đủ và rẻ nhất: sửa `01` §1.2 thành *"ba cái đầu ở trên; session
là phạm vi của tầng service, xem 07-risks"*, và thêm một dòng vào `07-risks`. Không cần thêm
kiểu — chỉ cần đừng tuyên bố có cái không có.

### K-29 — `redact()` được ba nơi dùng, không nơi nào định nghĩa · **nên bổ sung**

`04` §2 (node `tools`: "redaction scope"), §4.3 (`PauseRequest.args # đã qua redact()`),
§8.2 điểm 4 (*"cùng hàm mà node `tools` dùng, không phải bản sao thứ hai"*). Ba tham chiếu
tới một hàm không tồn tại. Đây cũng chính là cơ chế duy nhất chống lại điều `03`
§16 gọi là "không gói Python nào có kiểu `Secret`".

---

## 6. Chỗ đã cân bằng tốt — **ĐỪNG CẮT**

Ghi lại để vòng review sau không cắt nhầm. Mỗi mục dưới đây **đã** là kết quả của một lần
cắt đúng, và bằng chứng cho nó là cụ thể.

| # | cái gì | vì sao đừng cắt |
|---|---|---|
| **G-1** | **Một trục `Effect` → 5 hành vi dẫn xuất** (00 §2, 03 §1.2) | Đây là ý tưởng trung tâm và nó **giảm** khái niệm chứ không thêm: hợp nhất `ToolKind` + `sequential` + `disable_*_tool_approval` + `readOnlyHint`/`destructiveHint` thành một câu hỏi. Người viết tool khai **một** thứ. Bất biến T-1 (không có trường ghi đè) là thứ giữ nó không phình lại. |
| **G-2** | **Hai around-hook, không phải sáu** (01 §4.1) | Lập luận đã đúng và đã viết ra: bốn hook kia là dòng đầu/dòng cuối của một around-hook. "KISS: cắt bốn, giữ hai" — giữ nguyên. |
| **G-3** | **Một parallel mode, không phải ba** (03 §3.2) | Lý do là an toàn, không phải khẩu vị: một núm chỉnh sang `'parallel'` là một núm xoá barrier. `max_concurrency` là giới hạn tài nguyên, không phải giới hạn an toàn — phân biệt này đúng. |
| **G-4** | **Hai compaction strategy, không phải năm** (05 §B.2) | Giữ **thứ tự ưu tiên** của Microsoft, cắt số lượng, và nêu lý do cắt từng cái trong ba cái bỏ đi. Đây là mẫu mực của cách áp §8.4. |
| **G-5** | **Từ chối state machine cho business logic** (04 §2.3) | Nghiên cứu ghi "nobody has state machines" — và bản thiết kế **không** coi đó là cơ hội. Đúng: khuyết điểm đo được là *enforcement bị vòng qua*, không phải *thiếu DSL trạng thái*. |
| **G-6** | **`GUARDED` là dữ liệu, `unguarded_paths()` đọc chính bảng đó** (04 §2.1, §3) | Ba dòng dữ liệu thay cho ba trang văn xuôi, cộng `witness` để lỗi sửa được trong 30 giây, cộng mutation test cho chính checker. Rẻ và đúng. |
| **G-7** | **`max()` dùng cho cả `Verdict`, `Label.join`, và `DecisionLog.lookup`** | Một phép toán cho ba thứ. "Một dòng `max()` cho ba tính chất" (02 §2.4) là câu đúng nhất trong bản thiết kế. Đừng thêm luật ưu tiên thứ hai. |
| **G-8** | **"Vĩnh viễn" không có giá trị biểu diễn được** (02 §2.5) | Poka-Yoke ở tầng kiểu, không tầng review. Cái không dựng được thì không cần canh. |
| **G-9** | **`Actor` không có biến thể `Model`** (00 §4.2) | Một dòng type định nghĩa xoá cả một lớp lỗi (agno `decision_log`). Và có test cho nó (02 §6.2). |
| **G-10** | **`Ruling` ≠ `Decision`** (02 §0) | Đây **không** phải khái niệm thừa: `Ruling` không có actor, không sống qua lời gọi, không vào audit. Tách đúng. (Cái thừa là `AskOutcome` — xem K-3 — không phải `Ruling`.) |
| **G-11** | **`Policy.check` thuần + đồng bộ** (02 §1.2 P-4) | Hai lý do đo được, cả hai đúng: enumerate được ⇒ chứng minh bằng hypothesis; và policy `async` có thể gọi model ⇒ model ảnh hưởng quyền của chính nó. |
| **G-12** | **effect log: partial-unique index, không nuốt `IntegrityError`, `in_flight` không tự giải phóng** (03 §4) | Chép nguyên ba chi tiết kỹ thuật của agno và nói **thật** về việc đạt được gì (§4.5 bảng ba mode). Sự trung thực đó đáng giá hơn một lời hứa exactly-once. |
| **G-13** | **`count_tokens_approximately` giữ tên trung thực** (05 §B.3) | Một quyết định đặt tên chống được cả lớp hiểu nhầm. Giữ. |
| **G-14** | **Provenance cho memory write, `provenance` không có mặc định** (05 §C.1) | Sửa khoảng trống bảo mật lớn nhất nghiên cứu tìm được, bằng đúng lattice đã có (R-2: "memory không được có luật riêng"). Và luật W-3 song song với D-1 — cùng một ý tưởng, không phải ý tưởng thứ hai. |
| **G-15** | **Mọi tệp có mục `## Chưa đủ evidence`** | 7 tệp, ~40 mục. Đây là thứ hiếm và nó chính là công cụ đã giúp tìm ra K-1, K-2, K-6, K-7, K-12 trong review này. Đừng bỏ. |
| **G-16** | **Mức 0 = 5 dòng, 8 tên, `budget` là dòng bắt buộc duy nhất** | Đúng "học sinh 10 tuổi": `name`, `job`, `model`, `budget`, in ra `.text`. Và việc thứ duy nhất bắt gõ thêm lại chính là thứ cả ngành không có — đó là một quyết định DX xuất sắc. Bảo vệ nó bằng cách sửa K-16, đừng bảo vệ bằng cách nới nó ra. |

---

## 7. Bảng tổng kết

| mã | mức độ | một dòng |
|---|---|---|
| **K-1** | cắt ngay | `Quarantine` + `Quarantined[T]` — chính tệp khai không có eval nào; §8.4 nói cắt |
| **K-2** | cắt ngay | `deps_type`/`DepsT` — generic không tới được tool nào vì `ToolCtx` không có `deps` |
| **K-3** | cắt ngay | Ba bộ tên cho một vòng approval, mâu thuẫn về `actor` và về hạn dùng — gộp còn một |
| **K-4** | cắt ngay | `DecisionLog` và `AuditSink` là một sổ mang hai protocol — gộp |
| **K-5** | cắt ngay | `Snapshottable` Protocol có **0** implementer khớp signature |
| **K-6** | nên cắt | Trục `Confidentiality` không có nguồn phát — nửa lattice không đạt tới được |
| **K-7** | nên cắt | `Reservation.exact` + `hard_max_input_tokens` — lớp phòng thủ không ai đọc |
| **K-8** | nên cắt | `IdempotencyMode` 3 giá trị → 1 `bool`; key luôn được sinh |
| **K-9** | nên cắt | `run` / `try_run` / `raise_for_status` / `.ok` — bốn cách nói một điều |
| **K-10** | nên cắt | OTel 9 span × ~50 attribute → 4 span; bốn quy tắc nội dung giữ nguyên |
| **K-11** | cân nhắc | `end_strategy` — bằng chứng đủ nhưng không thuộc ví dụ Mức 3; `"exhaustive"` đụng từ vựng của `03` |
| **K-12** | cân nhắc | `ServerIdentity.fingerprint` — định dạng chưa chốt được, rug-pull chưa quan sát |
| **K-13** | cân nhắc | 38 mã bất biến, va chạm `P-3`, `R-1…R-3`, `C-1…C-4`; 29/38 mã không được tham chiếu chéo |
| **K-14** | cắt ngay | `resume(ruling=…)` còn sót **4** chỗ, và cả 4 mâu thuẫn với `ResumeToken` của `04` |
| **K-15** | cắt ngay | `@tool` có hai signature; `ToolSpec` thiếu `max_confidentiality` và `emits` mà `02` đang đọc |
| **K-16** | cắt ngay | **Ví dụ Mức 1 không chạy được** theo đặc tả `fn` của `03` — lỗi DX nặng nhất |
| **K-17** | cắt ngay | `Budget()` dựng được rỗng và `usd=None` hợp lệ — phá "budget bắt buộc có trục tiền" |
| **K-18** | cắt ngay | `StopReason` thiếu `graph_changed` và `TRUNCATED` |
| **K-19** | cắt ngay | `AuditEvent.type` Literal đóng, thiếu ≥5 loại event đang được phát |
| **K-20** | cắt ngay | `ToolInputInvalid.__init__` phá hợp đồng `what/got/fix/doc` của `HarnessError` |
| **K-21** | cắt ngay | Hai bộ tên taxonomy lỗi tool; `Retry` vừa là exception vừa là plugin |
| **K-22** | nên cắt | "14 tên, một `import`" sai bằng chính ví dụ của tệp (20 tên, 4 module) |
| **K-23** | nên cắt | 9 tunable không có đường từ API công khai; 3 mặt cấu hình song song |
| **K-24** | nên cắt | Vụn: block code hỏng ở `03` §6.2, `@value CancelToken` có mutation, `Scope.args` hai kiểu, `Verdict` hai đường import |
| **K-25** | cắt ngay | **Khuyết điểm #5 chưa được sửa ở mặc định** — `idempotency=NONE` là mặc định, đúng lớp lỗi opt-in đang bị chê |
| **K-26** | cắt ngay | `Workspace`/`Sandbox` bắt buộc ở constructor nhưng **không có một dòng đặc tả nào** |
| **K-27** | nên bổ sung | `Event` envelope có version — Minimal Core §33 đòi, thiết kế không định nghĩa |
| **K-28** | nên bổ sung | Session/tenant identity — §33 đòi ở core; `01` tuyên bố đã có, thực tế không có |
| **K-29** | nên bổ sung | `redact()` được 3 nơi dùng, 0 nơi định nghĩa |

**Ước lượng nếu áp K-1…K-10 và K-25:** 62 class → khoảng **48**; đường Zero-to-Agent tới
production 41 tên → khoảng **33**; và ba lời hứa lớn nhất của bản thiết kế (idempotency mặc
định, "14 tên", "mỗi mức một khái niệm") trở thành **đúng** thay vì gần đúng.

---

## Phụ lục — cách kiểm lại từng phát hiện

```bash
# K-3, K-14: ba bộ tên approval + resume còn sót
grep -n "ApprovalRequest\|AskRequest\|PauseRequest\|AskOutcome\|Answer\|ruling=" design/*.md

# K-13: va chạm mã bất biến
for c in P-3 R-1 R-2 R-3 C-1 C-4; do echo "== $c"; grep -n "\*\*$c" design/*.md; done

# K-15, K-6: trường ToolSpec bị dùng mà không tồn tại
grep -n "spec.emits\|max_confidentiality" design/*.md

# K-18, K-19: giá trị enum ngoài enum
grep -n "graph_changed\|TRUNCATED\|taint.raised\|duplicate_suppressed\|run.finished\|policy.allowed" design/*.md

# K-22, K-26, K-27, K-29: tên công khai không có định nghĩa
for n in Workspace Sandbox Event Money Usage redact Channel Human; do
  echo "== $n"; grep -n "class $n\|^$n *=\|def $n" design/*.md; done
```
