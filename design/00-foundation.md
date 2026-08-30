# Harness Design — Foundation

**Đây là tệp nền. Mọi tệp thiết kế khác phải dùng đúng từ vựng và mô hình ở đây.**

Bản thiết kế này được rút ra từ nghiên cứu trong [`research/`](../research/) — 30 gói,
3 hệ sinh thái, đọc source chứ không đọc docs. **Mọi quyết định thiết kế dưới đây phải
trích dẫn một phát hiện cụ thể.** Một quyết định không có trích dẫn là ý kiến, và ý kiến
không thuộc về tài liệu này.

---

## 1. Năm bất biến, và nghiên cứu nói gì về từng cái

| bất biến | nghiên cứu tìm thấy gì | hệ quả thiết kế |
|---|---|---|
| **Extensible / Pluginable** | LangChain 1.x `wrap_model_call`/`wrap_tool_call` là around-hook — mạnh nhất tìm được ([§08](../research/08-tool-mcp-plugin.md) §24). Nhưng lattice của Microsoft là middleware nên **mặc định không được cài** ([§09](../research/09-memory-context-multiagent-hitl.md) §16bis) | Plugin cho **policy**; invariant nằm trên đường đi không bypass được |
| **Cost Efficient** | Không ai reserve budget trước khi gọi model; `max_turns` chặn số bước chứ không chặn tiền ([§03](../research/03-safety-reliability.md) §20) | `reserve()` tiền-chuyến-bay, `max_tokens` suy ra từ số dư |
| **Safe by Design** | Approval ở đâu cũng là *trạng thái quyền*, không phải *quyết định* có thể audit — 30 gói, 3 ngôn ngữ ([§09](../research/09-memory-context-multiagent-hitl.md) §14, [§10](../research/10-governance-health-languages.md) §28) | `Decision` là bản ghi bất biến có actor, thời điểm, phạm vi, hạn dùng |
| **Intelligent** | Model tự cầm công tắc chế độ an toàn: `mode_set` có `approval_mode="never_require"` ([§09](../research/09-memory-context-multiagent-hitl.md) §14.2) | Model không bao giờ được cầm công tắc của chính nó |
| **Efficient** | Có topology mới cài được durability — LangGraph 51,6 checkpoint/kLOC ([§11](../research/11-workflow-and-dx.md) §12) | Runtime là graph, không phải `while` loop |

---

## 2. Effect class — một phân loại, năm hành vi

Phát hiện nền tảng: **không gì trong nghiên cứu suy ra tính an toàn song song từ effect
đã khai báo của tool** ([§08](../research/08-tool-mcp-plugin.md) §8.3). pydantic-ai gần
nhất với cờ `sequential=True`, nhưng đó là *người gọi tự khẳng định từng tool*. Hậu quả
đo được: LangChain phải viết tay guard riêng cho `write_todos` vì thiếu mô hình chung —
guard chỉ tồn tại ở chỗ ai đó đã bị cắn.

Vì vậy harness này phân loại **một lần**, và suy ra **năm** hành vi:

```
Effect = read | write | external | danger
```

| effect | song song? | model được thử lại? | runtime tự gọi lại? | làm nhiễm context? | verdict mặc định | mức audit |
|---|---|---|---|---|---|---|
| `read` | ✅ | ✅ | ✅ | không | `ALLOW` | `debug` |
| `write` | ❌ (barrier) | ✅ nếu có idempotency key | ❌ | không | `ASK` | `info` |
| `external` | ✅ | ✅ | **❌** | **có** | `ALLOW` | `info` |
| `danger` | ❌ (barrier) | ❌ | ❌ | không | `ASK` | **`audit`** |

**Vì sao hai cột retry chứ không phải một.** Bản nháp đầu của tệp này gộp chúng làm một và
ghi `external` là "retry được" — một agent viết [03](03-tools-and-mcp.md) bắt được mâu
thuẫn: nghiên cứu nói thẳng *"an `external` tool that fails may have already had an effect
and must not be retried blindly"* ([§08](../research/08-tool-mcp-plugin.md) §8.2). Hai
nghĩa khác nhau: **model được phép thử lại** (run không chết vì một lần fetch hỏng) khác
với **runtime tự gọi lại im lặng** (chỉ an toàn khi lặp lại không sinh tác dụng mới). Chỉ
`read` đúng cả hai.

Bốn thuộc tính đầu là *dẫn xuất*, không phải cấu hình. Người viết tool chỉ khai một thứ.

**Vì sao đây là điểm mạnh học từ nhiều nơi cộng lại:** pydantic-ai có `ToolKind`
(`function`/`output`/`external`) và cờ `sequential`; Microsoft tách read/write approval
riêng (`disable_readonly_tool_approval` vs `disable_write_tool_approval`); MCP có
`readOnlyHint`/`destructiveHint`. Cả ba đều *một phần* của cùng một ý tưởng. Ta hợp nhất
chúng thành một trục duy nhất.

---

## 3. Hai lattice

### 3.1 Verdict lattice — policy chỉ được thắt chặt

```
ALLOW (0)  <  ASK (1)  <  DENY (2)
```

Hợp thành bằng `max()`. Tính chất **P-2**: thêm một policy không bao giờ nới lỏng được
quyền. Chứng minh được bằng test tính chất (property-based), không phải bằng review.

### 3.2 Taint lattice — hai chiều, học từ Microsoft

Nghiên cứu tìm được **đúng một** cài đặt information-flow thật, và nó hai chiều
([§09](../research/09-memory-context-multiagent-hitl.md) §16bis):

```
integrity        : TRUSTED   <  UNTRUSTED      (Biba — chống bị điều khiển)
confidentiality  : PUBLIC    <  SECRET         (Bell-LaPadula — chống rò rỉ)
```

Hợp thành đơn điệu, không bao giờ giảm trong một run. Harness hiện tại của repo này
(ADR-011) **chỉ có trục integrity** — đây là nâng cấp có bằng chứng.

**Định nghĩa chuẩn — mọi tệp khác tham chiếu, không định nghĩa lại:**

```python
class Integrity(IntEnum):        # Biba — chống bị điều khiển
    TRUSTED = 0
    UNTRUSTED = 1

class Confidentiality(IntEnum):  # Bell-LaPadula — chống rò rỉ
    PUBLIC = 0
    SECRET = 1

@value
class Label:
    integrity: Integrity = Integrity.TRUSTED
    confidentiality: Confidentiality = Confidentiality.PUBLIC

    def join(self, other: Label) -> Label:
        """Hợp thành đơn điệu — cùng `max()` như Verdict, cùng lý do."""
        return Label(max(self.integrity, other.integrity),
                     max(self.confidentiality, other.confidentiality))
```

Luật thực thi:

- output của tool `external` → context thành `UNTRUSTED`
- context `UNTRUSTED` **không được** lái tool `danger`, trừ khi tool khai
  `accepts_tainted=True`
- context chứa dữ liệu `SECRET` **không được** gọi tool có `max_confidentiality=PUBLIC`
  (tool `external` và `write` mặc định là sink `PUBLIC`)

**Ba chỗ phải khác Microsoft:**

1. **Bật mặc định.** Của họ là submodule opt-in mà chính `_harness/` của họ không import,
   và không có trong `__init__` top-level. Của ta nằm trên đường đi bắt buộc.
2. **Không dùng `threading.local()`.** Của họ set middleware vào `threading.local()` rồi
   set xuyên qua `await` — hai tool call đồng thời đọc nhầm slot của nhau, im lặng và
   fail-open. Trạng thái taint của ta sống trong **state đã checkpoint của thread**, vì
   `ContextVar` cũng không đi xuyên node LangGraph (mỗi node chạy trong context được copy).
3. **Không có singleton toàn tiến trình.** Của họ có `_global_variable_store` và
   `_quarantine_chat_client` ở mức module; trong server đa tenant, một tenant đổi là mọi
   tenant đổi theo.

---

## 4. `Decision` — sửa phát hiện số một

Đây là khoảng trống được lặp lại nhiều nhất trong toàn nghiên cứu: **không framework nào
coi approval là một sự kiện có thể audit.** openai-agents lưu `bool | list[str]` với
`always_approve` không bao giờ hết hạn; LangGraph có id định vị *chỗ dừng* chứ không định
danh *người duyệt*; Java `ToolConfirmation` là đúng một `boolean`; và tín hiệu "audit" dày
nhất trong 23 gói Python là `decision_log` của agno — **một tool mà chính model gọi để tự
ghi về mình**, tức nhật ký audit do bên bị audit viết.

Trong harness này, approval **không phải** trạng thái. Nó là một bản ghi bất biến:

```python
@value
class Decision:
    id: DecisionId              # ULID — sắp xếp được theo thời gian
    verdict: Verdict            # ALLOW | DENY  (ASK không bao giờ là kết quả cuối)
    scope: Scope                # cái gì được duyệt — xem §4.1
    actor: Actor                # AI KHÔNG BAO GIỜ để trống
    decided_at: datetime        # runtime ghi, không phải model
    expires_at: datetime | None # None = chỉ lần gọi này
    reason: str | None          # vì sao — hai chiều, học từ Vercel
    run_id: RunId               # thuộc run nào
```

**Bất biến D-1:** không trường nào của `Decision` do model sinh ra. Runtime điền `id`,
`decided_at`, `run_id`, `actor`; con người điền `verdict` và `reason`; policy điền `scope`.

**Bất biến D-2:** một `Decision` chỉ được ghi thêm (append-only), không sửa, không xoá.
Thu hồi là ghi một `Decision` mới có `verdict=DENY`.

### 4.1 `Scope` — học điểm mạnh nhất của Microsoft

`ToolApprovalRule` của Microsoft là thiết kế tốt nhất tìm được vì nó khoá grant theo
**giá trị tham số**, không chỉ theo tên tool: duyệt `delete_file(path="/tmp/x")` không
duyệt `delete_file(path="/etc/passwd")`. Mọi dự án khác duyệt *động từ* và bỏ qua *tân ngữ*.
Nó còn có `server_label` — grant cho một MCP server không chuyển sang server khác cùng tên
tool, phòng thủ confused-deputy duy nhất tìm được.

```python
@value
class Scope:
    tool: ToolName
    args: Mapping[str, str] | None   # None = mọi lời gọi; {} = chỉ lời gọi không tham số
    server: ServerLabel | None       # biên tin cậy của MCP server
    call_id: CallId | None           # None = quy tắc thường trực; có = chỉ lần này
```

Phân biệt `None` vs `{}` giữ nguyên của Microsoft — nó là Poka-Yoke thật và đã được
tài liệu hoá rõ ràng ở nguồn.

### 4.2 `Actor` — trường không nơi nào có

```python
Actor = Human(id: str, via: Channel) | Policy(rule: str) | Operator(id: str)
```

Không có biến thể `Model`. **Model không bao giờ là actor của một `Decision`** — đó chính
là chỗ agno sai.

---

## 5. Bốn quy tắc kiến trúc, mỗi cái sửa một khuyết điểm đo được

**R-1. Invariant nằm trên đường đi bắt buộc; plugin chỉ dành cho policy.**
Vì "ship middleware nhưng harness không install" là lỗi thật đã quan sát được ở vendor
lớn. Budget, taint, permission check → đường đi bắt buộc. Retry, cache, log, model
fallback, cost accounting → plugin.

**R-2. Chứng minh được, không phải review được.**
`unguarded_paths()` duyệt graph đã compile và chứng minh không đường nào tới `model` mà
không qua `budget`, tới `tools` mà không qua `policy`, tới `END` mà không qua `finish`.
Lý do: 23 vòng review tìm ra 20 lỗi và **0 lỗi bảo mật**; 16 vòng *chạy* tìm ra 38+ lỗi
và **4 lỗi bảo mật**. Đọc không tìm ra cái mà chạy tìm ra.

**R-3. Model không cầm công tắc an toàn nào.**
Không tool nào do model gọi được phép đổi chế độ an toàn, nới policy, ghi `Decision`, hay
sửa budget. Microsoft vi phạm điều này với `mode_set(approval_mode="never_require")`.

**R-4. Không trạng thái chia sẻ ngoài state đã checkpoint.**
Không singleton mức module, không `threading.local()`, không `ContextVar` xuyên node.
Đây là lỗi đã tự mắc ở Round 34, sửa sai ở Round 37, sửa đúng ở Round 41 — và Microsoft
đang mắc phiên bản yếu hơn của nó.

---

## 5bis. Event kind — một bảng duy nhất, có version

`AuditEvent` (02 §3.1) và `Event` của `stream()` (01) **cùng dùng bảng này**. Bản nháp đầu có
hai `Literal` đóng khác nhau và ít nhất 5 event được phát mà không nằm trong bảng nào
([review-kiss.md](review-kiss.md) K-19). Nghiên cứu cũng đòi envelope có version trong minimal
core ([§05](../research/05-ideal-harness.md) §33).

```python
SCHEMA_VERSION = 1                       # tăng khi đổi nghĩa một kind, không khi thêm kind

EventKind = Literal[
    # vòng đời run
    "run.started", "run.finished", "run.cancelled",
    # model
    "model.called", "model.returned", "model.refused",
    # tool
    "tool.called", "tool.returned", "tool.failed", "duplicate_suppressed",
    # quyết định và từ chối
    "decision", "policy.allowed", "policy.denied", "flow.denied", "budget.denied",
    # luồng thông tin
    "taint.raised",
]

@value
class Event:
    v: int                # = SCHEMA_VERSION — người đọc log cũ biết mình đang đọc gì
    kind: EventKind
    run_id: RunId
    at: datetime
    level: Literal["debug", "info", "audit"]
    data: Mapping[str, Any]
```

17 kind. `AuditEvent` là `Event` có `level == "audit"`, không phải một kiểu thứ hai —
**một sổ, một protocol** (K-4).

## 6. Từ vựng bắt buộc

Dùng đúng các tên này. Không đặt tên đồng nghĩa.

| khái niệm | tên | không dùng |
|---|---|---|
| phân loại tác dụng của tool | `Effect` | `kind`, `type`, `category` |
| phán quyết policy | `Verdict` | `permission`, `allowed` |
| bản ghi phê duyệt | `Decision` | `Approval`, `ApprovalRecord` |
| ai quyết định | `Actor` | `user`, `approver` |
| sổ chi phí + bước | `Ledger` | `budget`, `usage` |
| nhãn hai chiều | `Label(integrity, confidentiality)` | `taint`, `level` |
| một lần chạy | `Run` | `session`, `thread` |
| tool bên ngoài | `ToolSpec` | `Tool`, `FunctionTool` |

---

## 7. Bản đồ tệp và người sở hữu

| tệp | nội dung | trạng thái |
|---|---|---|
| `00-foundation.md` | tệp này — từ vựng, effect, lattice, `Decision` | ✅ |
| `01-core-api.md` | API công khai, zero-to-agent, DX | agent A |
| `02-safety-engine.md` | policy, `Decision` lifecycle, taint enforcement, audit sink | agent B |
| `03-tools-and-mcp.md` | `ToolSpec`, taxonomy lỗi, song song, phân loại MCP | agent C |
| `04-runtime-durability.md` | graph, checkpoint, resume, `unguarded_paths`, cancel | agent D |
| `05-cost-and-memory.md` | `Ledger`, `reserve/hold/release`, compaction, provenance bộ nhớ | agent E |
| `06-poka-yoke-matrix.md` | mỗi lớp lỗi ↔ cơ chế chặn ↔ mức Poka-Yoke | tổng hợp |
| `07-risks-and-open-issues.md` | rủi ro, đánh đổi, cái chưa đủ bằng chứng | tổng hợp |

---

## 8. Luật viết cho mọi agent

1. **Mỗi quyết định phải trích dẫn** một phát hiện trong `research/` theo dạng
   `([§09](../research/09-...md) §14)`. Không trích dẫn = không đưa vào.
2. **Chỉ ra rõ điểm mạnh học của ai** và **khuyết điểm nào đang sửa**. Đây là yêu cầu
   trung tâm của người dùng.
3. **Code trong tài liệu phải là signature thật**, chạy được về mặt kiểu — không giả mã.
4. **KISS / NOT-OVER-ENGINEER.** Nếu một cơ chế không sửa một khuyết điểm *đo được*, cắt
   nó. Nghiên cứu đã cho thấy cái giá của việc thêm tính năng không ai bật.
5. **Ghi rõ cái mình không chắc** trong mục `## Chưa đủ evidence` cuối tệp — đừng đoán.
6. Viết tiếng Việt, thuật ngữ kỹ thuật giữ tiếng Anh.
