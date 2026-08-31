# §17 — Đối chiếu với nghiên cứu hợp nhất, và kế hoạch xây tiếp

> Nguồn: *"Nghiên cứu hợp nhất: Agent Framework, Agent Harness, Interface và Kiến trúc"*
> (snapshot 29/08/2026), gồm 12 framework, 9 harness, ma trận 10 nhóm tiêu chí có trọng
> số, phụ lục API interface với 5 anti-pattern và một interface đề xuất.
>
> Tài liệu này làm ba việc: **học gì**, **tránh gì**, và **xây gì tiếp**. Mọi khoảng trống
> nêu ở đây đều được **kiểm bằng cách chạy code**, không phải bằng đọc lại tài liệu của
> chính mình — vì 41 vòng vừa qua đã cho thấy đọc không tìm ra thứ chỉ có chạy mới tìm ra.

---

## 1. Tự chấm theo đúng ma trận trọng số của nghiên cứu

Đây là **tự chấm**, nên nó không so sánh được trực tiếp với điểm của nghiên cứu (khác
người chấm, và họ chấm các dự án đã trưởng thành). Giá trị của nó nằm ở **hình dạng**:
chiều nào cao, chiều nào thấp.

> **CHẤM LẠI, sau khi M6…M10 xong (design/08 §3.2).** Bảng gốc bên dưới (67.8/100) là
> snapshot TRƯỚC khi bất kỳ mục nào trong M6…M10 được xây — `design/08 §3.2` đã cảnh báo
> rõ con số đó "không dịch chuyển" qua nhóm S-1…S-29 vì đó là hai trục khác nhau. Nay
> M6…M10 đã xong cả 10/10 sub-task (idempotency, cancellation, retry, chaos; workspace,
> egress-deny, sandbox; envelope v1, OTel, cost/success; MCP, Service API, event adapter;
> trajectory contract, golden set, benchmark), phần lớn lý do trừ điểm trong bảng gốc
> không còn đúng. Chấm lại **bằng cách chạy `tests/test_roadmap.py`** (định nghĩa "xong"
> của chính tệp §17 này, không phải đọc lại văn xuôi) trước khi đổi bất kỳ số nào — đúng
> kỷ luật dòng 8 của tệp này.

| Chiều | Trọng số | Điểm cũ → mới | Đóng góp | Vì sao đổi (hoặc không) |
|---|---:|:---:|---:|---|
| Control / Safety | 15% | 4/5 → **5/5** | 15.0 | S-04 (§2.2) đóng: `workspace.py` (T-7.1, mặc định confine), `allowed_hosts=()` mặc định chặn (T-7.2), `sandbox.py` (T-7.3/7.4, seam thứ sáu). Không còn "0 lớp Isolation". |
| Reliability | 12% | 3/5 → **5/5** | 12.0 | Cancellation không còn bị nuốt (T-6.2), retry theo effect class (T-6.3), chaos harness tìm+sửa 2 lỗi thật N-2/N-4 (T-6.4). Idempotency: giao thức ba pha (`idempotency.py`, T-6.1) tồn tại và có test — nhưng `ToolCall` KHÔNG có trường `idempotency_key` theo đúng nghĩa đen (`tests/test_roadmap.py::S-01` vẫn đỏ), vì chưa có caller thật (ADR-043). Không hạ điểm cho việc này — cơ chế tồn tại và đúng chủ đích hoãn wiring, nhưng ghi rõ đây KHÔNG phải "hoàn toàn không còn gì để làm". |
| Testability | 12% | 4/5 → **5/5** | 12.0 | Trajectory contract (T-10.1, `harness.testing.Trajectory`), golden set + pass rate CI (T-10.2), failure injection (T-6.4 chaos). `tests/test_roadmap.py::S-05/S-11/S-13` xanh. 559 test (không phải 233 nữa). |
| Extensibility | 10% | 4/5 → **5/5** | 10.0 | MCP (T-9.1) không thêm seam thứ sáu — nó CHỨNG MINH seam Tool có sẵn đủ rộng cho một hệ sinh thái bên thứ ba không tin cậy, đúng luận điểm "5 seam là đủ" mạnh hơn trước. `tests/test_roadmap.py::S-09` xanh. |
| Observability | 10% | 3/5 → **4/5** | 8.0 | Canonical event adapter (T-9.3) đóng S-07 (`tests/test_roadmap.py` không track S-07 riêng, nhưng `to_canonical_json` dùng chung SSE+transcript, xem ADR-055). Service API (T-9.2) mở thêm một transport thật. **Còn mở:** S-02 (`ApprovalRecord` không export ở top-level `harness`, dù `Decision`/`Approval` trong `policy/decision.py` mang đủ trường), S-03 (`RunContext` thiếu `principal`/`tenant_id`), S-14 (backpressure chưa xét — SSE endpoint của Service API buffer toàn bộ `run.events` không giới hạn), N-5/N-6 (retry cấp provider, `model.response` thiếu `usage`/`latency_ms`) — bốn khoảng trống thật, không phải lấy trọn 5/5. |
| Cost efficiency | 10% | 4/5 → **5/5** | 10.0 | `cost_per_success` (T-8.4, trước phiên này) đã đo đúng "cost/successful task". `tests/test_roadmap.py::S-06` xanh. |
| Developer experience | 10% | 4/5 → 4/5 | 8.0 | **Không đổi — SC-1b (pilot trẻ em 10-12 tuổi) vẫn CHƯA ĐO.** Đây là số đo cần người thật, ngoài khả năng một phiên code. [Unverified/chưa đo] |
| Integration | 8% | 2/5 → **4/5** | 6.4 | MCP (S-09) và Service API (S-08, `POST /v1/runs`...) cả hai đóng, có test end-to-end thật (subprocess MCP server, Starlette `TestClient`). "connector" (kết nối SaaS cụ thể) vẫn chưa có và có lẽ ngoài phạm vi một harness (khác một platform tích hợp) — không lấy trọn 5/5 vì đó vẫn là một phần ba của deduction gốc. |
| Performance | 6% | 2/5 → **3/5** | 3.6 | T-10.3 xây HẠ TẦNG đo (p50/p95, throughput×concurrency thật qua `asyncio.Semaphore`, cold start tách warm) — nhưng KHÔNG có con số đo THẬT từ một deployment thật, chỉ có test chạy qua `FakeModel`. "Đo được" khác "đã đo" — không lấy quá 3/5 vì phần "đã đo" của deduction gốc vẫn đúng. [Inference: cải thiện thật nhưng chưa đủ để gọi là đóng] |
| Intelligence | 5% | 3/5 → 3/5 | 3.0 | Không đổi — ngoài phạm vi M6…M10. |
| Ecosystem | 2% | 1/5 → 1/5 | 0.4 | Không đổi — MCP là harness TIÊU THỤ một hệ sinh thái có sẵn, không phải hệ sinh thái được XÂY quanh harness này. Không có bằng chứng nào khác về adoption/package bên thứ ba. |
| **Tổng** | **100%** | | **88.4** (từ 67.8) | |

Để tham chiếu, nghiên cứu chấm LangGraph 89.4, PydanticAI 87.1, Goose 83.5, Pi 76.8 — **nhắc
lại câu đầu mục này: đây là tự chấm bằng CHÍNH thước đo và CHÍNH người chấm với harness
này, không phải cùng người chấm bốn dự án kia.** 88.4 nằm giữa PydanticAI và LangGraph
theo con số, nhưng con số đó không chứng minh được gì về so sánh trực tiếp — chỉ có giá
trị nội bộ (hình dạng đã thay đổi ra sao so với chính nó).

**Ba giới hạn của con số mới, nói thẳng để không bị hiểu lầm là "xong":**
1. **Không thay thế điều kiện v1.0 đã đặt ở `design/08 §5`.** Pilot 2-4 tuần với model/
   task/tool/policy thật vẫn là điều kiện BẮT BUỘC trước khi gắn nhãn v1.0 — không con số
   tự chấm nào, kể cả 88.4, thay được việc đó.
2. **DX (SC-1b) và Performance (con số đo thật) là hai chiều KHÔNG THỂ đóng bằng code.**
   Cả hai cần bằng chứng từ thế giới thật (trẻ em dùng thử, một deployment thật chạy qua
   benchmark) — phiên làm việc sinh ra tài liệu này chỉ xây được HẠ TẦNG cho Performance,
   không tạo ra được số đo, và không chạm được gì vào SC-1b.
3. **Bốn mục S-01/S-02/S-03/S-14 (§2.2) vẫn mở dù M6/M8 "xong".**
   "Milestone xong" nghĩa là danh sách task M6/M8 tự đặt ra đã thoả — không có nghĩa là
   MỌI mục liền kề trong `§2.2`'s bảng 14 điểm mạnh cũng đóng theo. Bảng đó được cập nhật
   ở `## 2.2` bên dưới, đúng trạng thái từng dòng.

**Kết luận trung thực, cập nhật:** harness này nay mạnh ở hầu hết mọi chiều nặng ký
(Safety, Reliability, Testability, Extensibility, Cost, Integration đều ≥ 4/5) — yếu còn
lại tập trung ở ba chỗ không đóng được bằng code một mình: DX (đo người thật), Performance
(đo deployment thật), Ecosystem (đo adoption thật). Kế hoạch M6…M10 ở dưới xếp theo
`trọng số × khoảng trống` **tại thời điểm nó được viết** — giữ nguyên làm hồ sơ quyết định,
không viết lại theo thì quá khứ (xem `design/08 §4` cho cùng cách xử lý).

---

## 2. ĐIỂM MẠNH ĐỂ HỌC THEO

### 2.1 Những điều nghiên cứu nói đúng, và harness đã làm

| Nguyên tắc trong nghiên cứu | Ở đâu trong harness |
|---|---|
| *"Thiết kế harness để agent **khó làm sai**"* | 83 failure mode, xếp theo thang Impossible → Documented ([§08](08-poka-yoke.md)) |
| *"Model được quyền **đề xuất** tool call, không được tự cấp quyền thực thi"* | Policy gate là một cạnh đồ thị, chứng minh bằng `unguarded_paths()` (ADR-032) |
| Poka-Yoke lớp **Schema** | `@tool` sinh JSON Schema, `strict:true`, `additionalProperties:false` |
| Poka-Yoke lớp **Execution** | Graph có transition tường minh, bounded loop, timeout |
| Poka-Yoke lớp **Permission** | Verdict lattice compose bằng `max()` — policy chỉ thắt chặt được (P-2) |
| Poka-Yoke lớp **Human gate** | `approve=` + `interrupt()` bền vững |
| Poka-Yoke lớp **Budget** | Trần 3 trục, giữ chỗ **trước** mỗi lần gọi (ADR-017/026) |
| Poka-Yoke lớp **Audit** | 15 event kind đóng, transcript, mọi verdict được ghi |
| Anti-pattern 1 *(chỉ có `run(prompt)->str`)* | `Result` mang stop_reason, cost, usage, steps, tainted, messages, value, `tools_run` |
| Anti-pattern 2 *(final text là state duy nhất)* | Transcript + checkpoint; state là event log |
| Anti-pattern 5 *(coi protocol là security boundary)* | Ranh giới nằm ở policy engine, không ở transport |
| *"Cost/successful task, không phải cost/task"* | Công thức đúng — **chưa đo**, xem M8 |
| *"Approval không thay thế isolation"* (Cline) | Đã nêu trong [§01.5](01-requirements.md) non-goals — **nhưng đó là lảng tránh, xem M7** |

### 2.2 Những điều đáng học mà harness **chưa có** — đã kiểm bằng code

```
Event envelope hiện có : ['seq','ts','run_id','kind','step','data']
Nghiên cứu §8 đòi      : + schemaVersion, traceId, tenantId
RunContext hiện có     : ['agent_name','deadline','run_id','safety','step','tainted']
Nghiên cứu đòi         : + principal, tenantId, scopes
ToolCall hiện có       : ['id','name','arguments','spec']
Nghiên cứu đòi         : + authorization{principal,scopes}, idempotency_key,
                           budget{timeout_ms,max_retries}
grep -ril idempot src/ → chỉ một dòng comment, không có cơ chế
grep -ril sandbox src/ → KHÔNG CÓ
grep -ril mcp src/     → KHÔNG CÓ
```

| # | Điểm mạnh cần học | Từ đâu | Trạng thái (chấm lại — xem `## 1`) |
|---|---|---|---|
| S-01 | **Idempotency key trên mỗi tool call** | Anti-pattern 3; OWASP duplicate-action | **XONG (ADR-058).** `ToolCall.idempotency_key` (`f"{run_id}:{call_id}"`, `idempotency.py`, T-6.1) nay được gắn ở cả ba điểm dispatch thật (`dispatch.py`, `lg/runtime.py`'s `policy_gate`/`approval_gate`/`_regate`). `tests/test_roadmap.py::S-01` xanh. Vẫn read-only — `execute_once` chưa có caller trong `Agent`/`Dispatcher` (ADR-057 giải thích vì sao đó vẫn đúng chủ đích). |
| S-02 | **Approval là một BẢN GHI**, không phải boolean: decision id, actor, policy version, expiry, audit entry | §6 acceptance criteria | **XONG (ADR-058).** `harness.ApprovalRecord = Decision` — alias, không phải type thứ hai. `tests/test_roadmap.py::S-02` xanh. |
| S-03 | **Principal / tenant / scopes** trong ngữ cảnh và trong tool envelope | Anti-pattern 4; tool envelope §8 | **XONG (ADR-058).** `RunContext.principal`/`.tenant_id` (`dispatch.py`) + `Agent(principal=...)`. Việc thêm trường này lộ ra một lỗi thật cùng lớp N-7: `Agent.with_()` sẽ đánh rơi `principal` nếu không thêm vào base dict — đã thêm, có test riêng. `tests/test_roadmap.py::S-03` xanh. |
| S-04 | **Lớp Isolation**: workspace-rooted, network egress mặc định chặn, secret không vào sandbox | Bảng Poka-Yoke, OpenHands/Goose | **XONG.** `workspace.py` (T-7.1), `allowed_hosts=()` mặc định (T-7.2), `sandbox.py` (T-7.3/7.4). `tests/test_roadmap.py::S-04` xanh. |
| S-05 | **Trajectory contract khai báo được** (Given/When/Then: tool nào phải gọi, tool nào cấm, ≤N call, ≤T token, ≤C cost, retry không nhân đôi side effect) | §9 | **XONG.** `harness.testing.Trajectory` (T-10.1) — cả 8 luật §9 liệt kê. `tests/test_roadmap.py::S-05` xanh. |
| S-06 | **Cost per successful task** thay cho cost per task | §10 | **XONG.** `harness.eval.cost_per_success` (T-8.4). `tests/test_roadmap.py::S-06` xanh. |
| S-07 | **Canonical event model + adapter cho nhiều transport** | Phụ lục §10 | **XONG.** `observe/canonical.py::to_canonical_json` (T-9.3) — SSE (`harness.server`) và `TranscriptWriter` (CLI/JSON) dùng chung một hàm; sửa luôn một lỗ hổng thật (envelope v1 bị rơi khỏi transcript JSONL trước bản vá). |
| S-08 | **Service API**: `POST /v1/runs`, `GET /runs/{id}`, SSE events, approvals, cancel, resume | Phụ lục §8 | **XONG, đúng lựa chọn library-first.** `harness.server.create_app` (T-9.2), extra `harness[server]` (Starlette, ASGI app object, không server đóng gói). `resume` trả 501 có chủ đích (chưa giữ transcript path theo run_id) — nói thẳng thay vì giả vờ. |
| S-09 | **MCP làm tool boundary** (không phải toàn bộ API) | §5 protocol | **XONG.** `harness.mcp` (T-9.1). `tests/test_roadmap.py::S-09` xanh. |
| S-10 | **Cancellation đúng quy ước** — huỷ giữa model call, tool call, approval, stream | §6 acceptance criteria | **XONG** (T-6.2, trước phiên này). `tests/test_roadmap.py::Y-01/Y-01b` xanh. |
| S-11 | **Failure injection** trong bộ test | §9 | **XONG.** `harness.testing.chaos` (T-6.4) — 5 kịch bản, tìm+sửa 2 lỗi thật (N-2, N-4). |
| S-12 | **Đo p50/p95 latency, throughput, concurrency** | §10 | **MỘT PHẦN — hạ tầng đo có, số đo thật thì không.** `harness.eval.run_latency_benchmark`/`run_throughput_benchmark` (T-10.3) đo được thật (concurrency qua `asyncio.Semaphore` thật) — nhưng chưa có kết quả benchmark từ một deployment thật, chỉ có test chạy qua `FakeModel`. |
| S-13 | **Pass rate kèm khoảng tin cậy 95%**, không dùng một con số đơn lẻ | §9, Terminal-Bench | **XONG.** `harness.eval.run_golden_set` (T-10.2), dùng lại Wilson interval của T-8.4. |
| S-14 | **Backpressure**: client chậm không làm đầy memory hay mất event âm thầm | §6 | **XONG (ADR-059).** `run.events` là `deque(maxlen=MAX_BUFFERED_EVENTS)` (5 000) thay vì `list` không giới hạn. Theo dõi theo `Event.seq` (đơn điệu suốt run), không theo chỉ số list — chỉ số sẽ sai ngay khi buffer tràn lần đầu. Một subscriber tụt lại quá xa nhận một frame `event: dropped` nêu rõ số event đã mất, thay vì một khoảng trống im lặng. `tests/test_m9_t92_t93_service.py::Backpressure`. |

---

## 3. ĐIỂM YẾU ĐỂ TRÁNH

### 3.1 Từ các dự án trong nghiên cứu

| # | Điểm yếu | Của ai | Harness tránh bằng cách nào |
|---|---|---|---|
| W-01 | **Token/call explosion, semantics mờ sau abstraction role/task** | CrewAI | Không có abstraction "role"/"crew". Subagent là một tool có ngân sách nằm trong ngân sách cha (ADR-030), đo được |
| W-02 | **"Phải tự xây permission, sandbox, MCP, subagent, plan"** | Pi | An toàn không được để lại cho người dùng: `recall` ship sẵn là `external`, tool `danger` mặc định bị từ chối |
| W-03 | **Approval bị nhầm là isolation** | Cline | Nghiên cứu nói thẳng, và harness **đang mắc đúng lỗi này** → M7 |
| W-04 | **Surface rộng, lẫn lộn agent với data abstraction** | LlamaIndex | 5 seam, không phải 9. Phép thử plugin boundary ([§02.4](02-architecture.md)) |
| W-05 | **Migration risk vì có successor** | AutoGen, Semantic Kernel | Không xây trên thứ có successor path đã công bố |
| W-06 | **Release velocity cao → regression risk**, chữ ký tool lỗi thời | OpenCode, Mastra | Pin phiên bản; parity suite chống trôi; `contract` version |
| W-07 | **Vendor coupling, alpha churn** | Codex | Provider là một seam; `AnthropicProvider` là adapter, không phải core |
| W-08 | **Operationally nặng** | OpenHands | Service API là **extra**, không phải lõi. Core vẫn 3 dependency, import 87 ms |
| W-09 | **"Stars không phải adoption"** | Toàn bộ §2 | Không lập luận từ độ phổ biến |
| W-10 | **Một pass rate đơn lẻ để tuyên bố tốt hơn** | §9 | Mọi con số đều kèm cách đo; SC-4 = 95.3% có benchmark chạy được |

### 3.2 Điểm yếu của **chính harness này**, đo được hôm nay

**Y-01 — `CancelledError` bị nuốt.** Đo:

```
t.cancel(); await t   →  trả về Result(stop_reason="cancelled")
                          KHÔNG raise CancelledError
side effect chạy ngầm →  Không (tool bị huỷ đúng) ✓
```

Phần side effect **đúng**. Nhưng nuốt `CancelledError` phá vỡ giao thức huỷ của asyncio:
một `TaskGroup` hoặc `asyncio.wait_for` bao ngoài sẽ không thấy việc huỷ đã xảy ra. Đây
là lớp lỗi đã biết, và nghiên cứu liệt kê cancellation thành một acceptance test riêng.
`try_run()` trả về `Result` là thiết kế (IDL-11) — nhưng **huỷ không phải một stop reason
bình thường**, nó là một tín hiệu điều khiển.

**Y-02 — Không có lớp Isolation.** [§01.5](01-requirements.md) ghi sandbox là non-goal vì
"cần process/WASM isolation — một sản phẩm khác". Nghiên cứu bác lại điều đó ở mức
nguyên tắc: *"approval không đồng nghĩa sandbox"*, và Isolation là một trong 8 lớp
Poka-Yoke. Không thể đóng gói container trong một thư viện, nhưng **có thể** làm ba việc
thư viện làm được: workspace root, chặn egress mặc định, và một seam để cắm sandbox thật.

**Y-03 — Envelope thiếu trường để truy vết đa tenant.** Không có `tenant_id`, `trace_id`,
`schema_version`. Nghiên cứu coi việc propagate trace/run/session/tenant id là một tiêu
chí Observability riêng.

**Y-04 — Integration 2/5.** Không MCP, không Service API. Nghiên cứu xếp Integration 8%
và nói *"MCP nên là tool boundary"*. Với một harness định vị production, đây là khoảng
trống lớn nhất theo trọng số.

**Y-05 — Performance chưa từng được đo.** 6% trọng số, và con số duy nhất từng đo là
import time. Không có p50/p95, không có throughput, không có concurrency test.

---

## 3.3 API interface: có cần thiết kế lại không?

**Không.** Kiểm theo đúng năm contract mà nghiên cứu tách ra, hai contract mạnh, một
contract có **lỗi ngữ nghĩa** (đã sửa ở vòng 43), và hai contract **thiếu hẳn** — thiếu
không phải là sai, và bổ sung không đòi hỏi phá vỡ [§04.8](04-interfaces.md).

| Contract | Trạng thái | Chi tiết |
|---|---|---|
| **1. Invocation** | **Mạnh** | `run` / `try_run` / `arun` / `atry_run` / `chat` / `resume` / `aresume` / `as_tool` / `with_`. Ngang PydanticAI về số dạng gọi; `run` raise và `try_run` trả về là một quyết định rõ ràng (IDL-11) mà nhiều thư viện không có |
| **2. Tool** | **Mạnh — có thể là phần đi trước mặt bằng chung** | Không thư viện nào trong nghiên cứu suy ra **năm hành vi từ một phân loại `effect`**: song song được, retry được, làm bẩn run, verdict mặc định, mức audit. Schema chỉ kiểm hình dạng dữ liệu; `effect` kiểm *hệ quả* |
| **3. Event / stream** | **Yếu** | Chỉ có `on_delta(str)` — một callback text. Có taxonomy 15 event kind nhưng **không có cách tiêu thụ nó như một stream**: exporter là push, không có async iterator pull |
| **4. Session / state** | **Yếu** | Nghiên cứu: *"Session ID phải được xem là resource có lifecycle"* — ownership, TTL, concurrent writer, fork, conflict. Harness có đường dẫn transcript và `thread_id` của graph, **không có đối tượng `Session`** |
| **5. Transport** | **Vắng, có chủ ý** | Library-first ([§01](01-requirements.md)). M9 mở HTTP/MCP như `extra` |

### Lỗi ngữ nghĩa đã tìm ra nhờ đọc kỹ nghiên cứu

Nghiên cứu cảnh báo một chi tiết tinh vi của PydanticAI: *"trong một số chế độ, final
output có thể kết thúc run trước khi dangling tool calls được thực thi."* Hội đồng đem
đúng câu đó ra thử harness của mình:

```
model trả:  text "Xong rồi nhé." + tool_use{ghi}   với stop_reason "end_turn"
vòng lặp :  stop_reason=completed · tool đã chạy: []      ← BỎ IM LẶNG
            tool_use trong hội thoại: ['c1']
            tool_result:              []                   ← VI PHẠM I-3
graph    :  tool đã chạy: [1] · có ToolMessage: True       ← ĐÚNG
```

Hai lỗi trong một. Tool bị bỏ, **và** hội thoại lưu lại mang một `tool_use` không có
`tool_result` — nếu phát lại hội thoại đó cho provider, nó bị từ chối thẳng.

**Luật đã sửa: tool call được chạy vì nó CÓ MẶT, không phải vì provider dán nhãn
`"tool_use"`.** Backend graph vốn đã làm đúng; lần này là vòng lặp đuổi theo — lần đầu
tiên sự thua kém đảo chiều sau bảy vòng.

### Cần học ở thư viện nào, điểm gì — cụ thể

| Thư viện | Điểm đáng học | Vì sao harness cần |
|---|---|---|
| **PydanticAI** | `run_stream_events()` và `iter()` — tiêu thụ **event** chứ không chỉ text | Đóng contract 3. Taxonomy 15 kind đã có; chỉ thiếu cửa ra kiểu pull |
| **PydanticAI** | Dependency injection (`deps_type`) tách state khỏi agent | Hiện muốn một `Agent` phục vụ nhiều tenant phải `with_()` ra bản sao. DI cho phép **một** agent, **nhiều** ngữ cảnh — và là chỗ tự nhiên để đặt `principal`/`tenant_id` (S-03) |
| **OpenAI Agents SDK** | **Run state + interruptions là first-class**: serialize được, ngắt được, resume được | `resume(transcript_path)` của harness yếu hơn: state là một file, không phải một đối tượng có kiểu |
| **LangGraph** | Stream có **version** (`astream_events(version=...)`) | Taxonomy đã đổi hai lần (vòng 27, 35) mà không có version → chính là Y-03 |
| **OpenHands** | Agent Server REST/OpenAPI + WebSocket, session API key | Hình dạng cho M9; đã có OpenAPI nghĩa là contract test được |
| **Goose** | Nhiều session đồng thời **cách ly nhau** | Contract 4. Vòng 37 đã sửa rò rỉ ngân sách/taint giữa thread, nhưng vẫn chưa có đối tượng Session |
| **Cline** | Approval là bản ghi có actor và audit | S-02 |
| **Mastra** | `.generate()` / `.stream()` trả riêng `toolCalls`/`toolResults`/`steps`/`usage` | **Không học.** `Result` gom lại một chỗ là cố ý — bốn promise rời rạc dễ bị đọc thiếu một cái |
| **CrewAI** | — | **Không học.** Nghiên cứu ghi rõ: nhiều knob trên Agent → surface lớn, semantics ẩn sau abstraction |

### Quyết định

**Không thiết kế lại. Sửa một, bổ sung hai, giữ nguyên phần còn lại.**

Contract 1 và 2 là phần mạnh nhất của gói này và đang được §04.8 bảo hành — phá chúng để
"hiện đại hoá" là đổi thứ đã chứng minh lấy thứ chưa. Contract 3 và 4 thêm vào được
**mà không đổi chữ ký nào đang có**: `Agent.stream()` là một phương thức mới,
`Session` là một đối tượng mới. Contract 5 vốn đã nằm ngoài phạm vi có chủ ý.

Việc này bổ sung hai task vào kế hoạch:

- **T-8.5 Event stream có thể tiêu thụ** — `async for ev in agent.stream(msg)` trên đúng
  taxonomy 15 kind, có `schema_version`. Nghiên cứu đòi phân biệt text delta, tool-call
  delta, tool result, approval request, retry, cancellation, final — đây là chỗ chúng
  xuất hiện.
- **T-8.6 `Session` là resource** — id, ownership, TTL, fork, resume, và ranh giới đồng
  thời. Vòng 37 đã sửa phần rò rỉ; đây là phần đặt tên cho thứ đã tồn tại ngầm.

Cả hai nằm ở M8 chứ không sớm hơn: chúng phụ thuộc envelope v1 (T-8.1), vì thêm một cửa
ra stream trước khi envelope có version là bày ra một contract rồi phải phá nó.

---

## 4. KẾ HOẠCH — M6 đến M10

Xếp theo `trọng số × khoảng trống`. Mỗi task theo đúng chín mục [§VIII của HARNESS.md](../HARNESS.md):
**What · Why · Where · How · Depends · Contract · Failure · Test · Done**.

### M6 — Reliability: idempotency, cancellation, failure injection *(12% × gap 2/5)*

| Task | Nội dung |
|---|---|
| **T-6.1 Idempotency key** | **What** Mỗi tool call mang `idempotency_key = f"{run_id}:{call_id}"`; `write`/`danger` đi qua một `IdempotencyStore` trước khi chạy. **Why** Anti-pattern 3: client timeout rồi retry có thể gửi email hai lần. **Where** `dispatch.py`, `lg/runtime.py`, seam `Store`. **How** Tra key trước khi thực thi; trúng thì trả kết quả cũ, không chạy lại. **Contract** `execute_once(key, fn) -> (result, was_replayed)`. **Failure** Store chết → fail closed với `write`/`danger`, fail open với `read`. **Test** Retry cùng key không nhân đôi; hai key khác nhau thì chạy hai lần. **Done** AC mới + một dòng parity. |
| **T-6.2 Cancellation đúng chuẩn** | **What** `CancelledError` được re-raise sau khi dọn dẹp. **Why** Y-01. **Where** `run.py`, `lg/runtime.py`. **How** Bắt để dọn, ghi `run.finished(cancelled)`, rồi `raise`. **Failure** Không được để tool chạy tiếp sau khi huỷ (hiện đã đúng). **Test** `TaskGroup` bao ngoài thấy được việc huỷ; side effect không rò. |
| **T-6.3 Retry policy theo effect class** | **What** `read`/`external` retry được; `write`/`danger` không bao giờ tự retry. **Why** Bảng effect class đã suy ra "retryable" nhưng chưa ai đọc nó để retry. **Test** Property: không có `write`/`danger` nào chạy hai lần vì retry. |
| **T-6.4 Failure injection** | **What** `harness.testing.chaos`: provider timeout, tool raise, store chết, policy raise, model trả rác. **Why** §9. **Done** Mọi kịch bản có một hành vi được khẳng định, không cái nào crash. |

### M7 — Isolation: lớp Poka-Yoke duy nhất đang trống *(15% × gap)*

| Task | Nội dung |
|---|---|
| **T-7.1 Workspace root** | **What** Tool đụng file chỉ thấy được dưới một `workspace=` đã khai. **How** Chuẩn hoá đường dẫn rồi từ chối mọi thứ thoát ra — **từ chối, không escape** (IDL-44 đã dùng đúng cách này cho key). **Test** `../../etc/passwd`, symlink, đường dẫn tuyệt đối, `..` mã hoá URL. |
| **T-7.2 Egress mặc định chặn** | **What** `allowed_hosts` mặc định là `()` — chặn tất cả — chứ không phải `None` = cho tất cả. **Why** "Default an toàn: network outbound bị giới hạn". **Failure** Đây là breaking change → cần một phiên bản deprecation. |
| **T-7.3 Seam Sandbox** | **What** Protocol `Sandbox` với `run(cmd, *, cwd, env, timeout) -> Completed`; ship `InProcess` (không cách ly, nói rõ) và `Subprocess` (env sạch, cwd = workspace, không secret). **Why** Không nhét container vào thư viện, nhưng phải có chỗ cắm. **Done** Ai đó cắm được Docker/Firecracker mà không sửa core — chứng minh bằng một bản cài đặt bên thứ ba như §I.1 của `proof.py`. |
| **T-7.4 Secret không vào sandbox** | **What** `Sandbox.run` không bao giờ nhận `Secret`; env được lọc trắng. **Test** Red-team: secret không xuất hiện trong env của tiến trình con. |

### M8 — Observability & Audit *(10% × gap)*

| Task | Nội dung |
|---|---|
| **T-8.1 Envelope v1** | `schema_version`, `trace_id`, `tenant_id`, `session_id` vào `Event`; `seq` đã có. Có version thì mới đổi được mà không phá exporter. |
| **T-8.2 Approval là bản ghi** | `ApprovalRecord(decision_id, actor, policy_version, decided_at, expires_at, verdict, reason)` thay cho `bool`. **Why** S-02. **Failure** Approval hết hạn không dùng lại được. **Test** Cùng một approval không mở khoá được lần chạy thứ hai. |
| **T-8.3 OTel exporter thật** | Hiện `[otel]` chỉ là tên extra. Map 15 event kind sang span; propagate trace id. |
| **T-8.4 Cost per successful task** | `harness.eval.cost_per_success(runs)` — `tổng chi phí / P(thành công)`, kèm khoảng tin cậy. **Why** S-06, và §10 nói thẳng cost/task là công thức sai. |

### M9 — Integration: MCP và Service API *(8% × gap 3/5 — khoảng trống lớn nhất)*

| Task | Nội dung |
|---|---|
| **T-9.1 MCP client làm tool boundary** | **What** `harness.mcp.connect(server)` trả về `ToolSpec`. **Why** MCP là nơi có sẵn cả một hệ sinh thái tool. **How** Tool từ MCP **bắt buộc** khai `effect`; không khai thì mặc định `external` (làm bẩn run) chứ không phải `read`. **Failure** Đây là điểm mấu chốt: một server MCP là bên thứ ba không đáng tin, nên nó **không phải** security boundary (anti-pattern 5) — policy engine vẫn gác. |
| **T-9.2 Service API** | `POST /v1/runs`, `GET /v1/runs/{id}`, `GET /v1/runs/{id}/events` (SSE), `POST .../approvals/{id}`, `POST .../cancel`, `POST .../resume`. Là **extra** `harness[server]`, core không đổi. |
| **T-9.3 Canonical event model + adapter** | Một event model, nhiều transport: in-process, SSE, CLI/JSON. Không transport nào có semantics riêng. |

### M10 — Evaluation *(12% Testability, phần còn thiếu)*

| Task | Nội dung |
|---|---|
| **T-10.1 Trajectory contract** | Viết được đúng như §9 của nghiên cứu: `must_call`, `must_not_call`, `requires_approval`, `max_model_calls`, `max_tokens`, `max_cost`, `output_schema`, `no_duplicate_side_effects`. Chạy được với `FakeModel`. |
| **T-10.2 Golden set + pass rate có CI** | Task set đại diện + negative case + adversarial prompt + tool failure + policy violation. Báo cáo pass rate **kèm khoảng tin cậy 95%**, tokens, cost — không bao giờ một con số trần trụi. |
| **T-10.3 Benchmark hiệu năng** | p50/p95 latency theo span, throughput với concurrency, cold start. Đóng Y-05. |

---

## 5. Thứ tự, và vì sao

```
M6 Reliability ──► M7 Isolation ──► M8 Observability ──► M9 Integration ──► M10 Eval
   idempotency        workspace         envelope v1         MCP                trajectory
   cancellation       egress deny       approval record     service API        golden set
   retry policy       sandbox seam      OTel + cost/success adapters           benchmark
   chaos              secret jail
```

M6 đi trước vì idempotency là điều kiện tiên quyết cho retry, cho service API (idempotency
key trong header) và cho M10 (contract "retry không nhân đôi side effect"). M7 trước M9 vì
mở MCP ra mà chưa có isolation là mở rộng bề mặt tấn công trước khi dựng tường.

**Một cảnh báo cho chính hội đồng.** Nghiên cứu cảnh báo về *surface rộng* (LlamaIndex) và
*operationally nặng* (OpenHands). Kế hoạch này thêm MCP, HTTP server, sandbox, eval —
đúng những thứ làm một thư viện phình ra. Ràng buộc giữ nguyên: **core vẫn 3 dependency và
import dưới 100 ms**; mọi thứ ở M7–M10 là `extra`, và phép thử plugin boundary
([§02.4](02-architecture.md)) áp cho từng seam mới. Nếu một mục nào không qua được phép
thử đó, nó không được vào.

## 6. Điều kiện hoàn thành

Kế hoạch này xong khi tự chấm lại theo cùng ma trận cho **≥ 85** với Reliability,
Observability và Integration đều ≥ 4/5 — và, quan trọng hơn con số, khi 14 mục `S-01…S-14`
đều có một test đang chạy chứng minh chúng tồn tại. Nghiên cứu nói đúng điều mà 41 vòng
vừa qua đã học được bằng cách trả giá:

> *Quyết định cuối cùng cần một pilot 2–4 tuần có cùng model, cùng task set, cùng tool set
> và cùng security policy.* Không có con số nào ở trên thay thế được việc đó.
