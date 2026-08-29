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

| Chiều | Trọng số | Điểm | Đóng góp | Vì sao |
|---|---:|:---:|---:|---|
| Control / Safety | 15% | 4/5 | 12.0 | Taint lattice, effect class, policy lattice, từ chối lúc dựng, Secret, egress. **Trừ điểm: không có lớp Isolation nào** |
| Reliability | 12% | 3/5 | 7.2 | Trần ngân sách, checkpoint, resume. **Trừ: không có idempotency key, cancellation bị nuốt, không có failure injection** |
| Testability | 12% | 4/5 | 9.6 | 233 test, FakeModel, parity suite, property test, conformance. **Trừ: không có trajectory contract khai báo được, không có golden set** |
| Extensibility | 10% | 4/5 | 8.0 | 5 seam, đã chứng minh bằng bản cài đặt bên thứ ba. **Trừ: không có MCP** |
| Observability | 10% | 3/5 | 6.0 | 15 event kind đóng, exporter, transcript. **Trừ: không có OTel thật, envelope thiếu traceId/tenantId/schemaVersion** |
| Cost efficiency | 10% | 4/5 | 8.0 | Trần pre-flight, cache 95.3%, ADR-026. **Trừ: không đo cost/successful task** |
| Developer experience | 10% | 4/5 | 8.0 | Lỗi đọc ở lớp ≤5, progressive disclosure. **Trừ: SC-1b chưa đo** |
| Integration | 8% | 2/5 | 3.2 | **Không có MCP, không có Service API, không có connector** |
| Performance | 6% | 2/5 | 2.4 | **Chưa đo latency/throughput/concurrency lần nào** |
| Intelligence | 5% | 3/5 | 3.0 | effort, adaptive thinking, `returns=`, subagent. Không routing (ADR-006) |
| Ecosystem | 2% | 1/5 | 0.4 | Chưa có |
| **Tổng** | **100%** | | **67.8** | |

Để tham chiếu, nghiên cứu chấm LangGraph 89.4, PydanticAI 87.1, Goose 83.5, Pi 76.8.

**Kết luận trung thực: harness này mạnh ở đúng những chiều nặng ký nhất (Safety, Cost,
Testability, DX) và yếu ở Integration, Performance, Observability, Reliability.** Kế
hoạch dưới đây xếp theo `trọng số × khoảng trống`, không theo thứ tự thích làm.

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

| # | Điểm mạnh cần học | Từ đâu | Trạng thái |
|---|---|---|---|
| S-01 | **Idempotency key trên mỗi tool call** | Anti-pattern 3; OWASP duplicate-action | Chưa có |
| S-02 | **Approval là một BẢN GHI**, không phải boolean: decision id, actor, policy version, expiry, audit entry | §6 acceptance criteria | Chưa có |
| S-03 | **Principal / tenant / scopes** trong ngữ cảnh và trong tool envelope | Anti-pattern 4; tool envelope §8 | Chưa có |
| S-04 | **Lớp Isolation**: workspace-rooted, network egress mặc định chặn, secret không vào sandbox | Bảng Poka-Yoke, OpenHands/Goose | Chưa có |
| S-05 | **Trajectory contract khai báo được** (Given/When/Then: tool nào phải gọi, tool nào cấm, ≤N call, ≤T token, ≤C cost, retry không nhân đôi side effect) | §9 | Có mảnh, chưa thành contract |
| S-06 | **Cost per successful task** thay cho cost per task | §10 | Chưa đo |
| S-07 | **Canonical event model + adapter cho nhiều transport** | Phụ lục §10 | Có event model, chưa có adapter |
| S-08 | **Service API**: `POST /v1/runs`, `GET /runs/{id}`, SSE events, approvals, cancel, resume | Phụ lục §8 | Chưa có (§01 chọn library-first) |
| S-09 | **MCP làm tool boundary** (không phải toàn bộ API) | §5 protocol | Chưa có |
| S-10 | **Cancellation đúng quy ước** — huỷ giữa model call, tool call, approval, stream | §6 acceptance criteria | **Có lỗi, xem 3.2** |
| S-11 | **Failure injection** trong bộ test | §9 | Chưa có |
| S-12 | **Đo p50/p95 latency, throughput, concurrency** | §10 | Chưa đo lần nào |
| S-13 | **Pass rate kèm khoảng tin cậy 95%**, không dùng một con số đơn lẻ | §9, Terminal-Bench | Chưa có |
| S-14 | **Backpressure**: client chậm không làm đầy memory hay mất event âm thầm | §6 | Chưa xét |

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
