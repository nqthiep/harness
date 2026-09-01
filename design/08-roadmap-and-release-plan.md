# 08 — Roadmap và release plan

Tệp này gộp hai backlog từng sống song song trong repo này — `07-risks-and-open-issues.md`
(phát hiện từ hai vòng review đối kháng, S-/K-/N-series) và `docs/17-research-alignment.md`
(khoảng trống so với nghiên cứu hợp nhất, M6-M10) — thành một roadmap, và ghi lại việc đã
làm theo đúng thứ tự nó chạy.

**Trạng thái: TOÀN BỘ ROADMAP ĐÃ XONG.** `S-20 → M6 → M7 → M8 → K-13 → M9 → M10` — sáu
milestone, tất cả 20 sub-task, mỗi cái có test + ADR. 599 test xanh, ruff/mypy sạch, mọi
`examples/*.py` chạy được. Việc còn lại trước v1.0 không còn là code — xem `## 3`.

---

## 1. Cái gì đã xong, theo thứ tự nó chạy

```
S-20 → M6 (Reliability) → M7 (Isolation) → M8 (Observability) → K-13 → M9 (Integration) → M10 (Evaluation)
 1/1        4/4                 4/4                 6/6           dọn nợ       3/3               3/3
```

**Vì thứ tự này.** M6 trước M7 vì idempotency là điều kiện tiên quyết cho retry và cho
Service API. M7 trước M9 vì mở MCP ra (hệ sinh thái tool bên thứ ba không đáng tin) trước
khi có Isolation là mở rộng bề mặt tấn công trước khi dựng tường. K-13's phần namespace
chen vào trước M9 vì MCP cần đặt tên invariant mới của riêng nó, dọn trước tránh chồng
thêm một namespace va chạm. M10 sau cùng vì trajectory contract cần một bề mặt đã ổn định
(MCP, idempotency, sandbox) để `must_call`/`must_not_call` có ý nghĩa. Đầy đủ lập luận
gốc: xem lịch sử git của tệp này.

| Milestone | Sub-task | Cơ chế chính | ADR | Test |
|---|---|---|---|---|
| **S-20** | Budget cảnh báo unlimited | `EventKind.BUDGET_UNLIMITED`, phát mỗi run khi `usd is None` | ADR-041 | `test_attack_s20.py` |
| **M6 — Reliability** | T-6.1 Idempotency | `idempotency.py::execute_once` — contract đầy đủ, CHỦ Ý chưa gắn vào `Dispatcher` (chưa có caller thật lúc đó — xem M9) | ADR-043 | `test_m6_t61_idempotency.py` |
| | T-6.2 Cancellation | `CancelledError` propagate đúng chuẩn, không bị nuốt, cả hai backend | — | `test_m6_t62_cancellation.py` |
| | T-6.3 Retry theo effect class | `read`/`external` tự retry có backoff; `write`/`danger` đúng một lần | ADR-042 | `test_m6_t63_retry.py` |
| | T-6.4 Chaos testing | `harness.testing.chaos` — 5 kịch bản; lộ ra N-2, N-4 (sửa ngay) | ADR-044 | `test_m6_t64_chaos.py` |
| **M7 — Isolation** | T-7.1 Workspace confinement | `workspace.py::confine` | ADR-045 | `test_m7_t71_workspace.py` |
| | T-7.2 Egress mặc định CHẶN | `allowed_hosts` mặc định `()`, `None` là escape hatch tường minh — breaking change có chủ đích | ADR-046 | `test_m7_t72_egress_default.py` |
| | T-7.3/7.4 Seam `Sandbox` | `sandbox.py` — `InProcess`/`Subprocess`, seam thứ SÁU; secret bị từ chối tường minh trong env | ADR-047 | `test_m7_t73_t74_sandbox.py` |
| **M8 — Observability** | T-8.1 Envelope v1 | `Event` +`schema_version`/`trace_id`/`tenant_id`/`session_id` | ADR-048 | `test_m8_t81_envelope.py` |
| | T-8.2 Approval là bản ghi | `Decision` +`policy_version` — hoá ra đã đủ trường từ trước | ADR-049 | `test_m8_t82_approval_record.py` |
| | T-8.3 OTel exporter thật | `observe/otel.py::OtelExporter` theo đúng mapping `docs/10 §2` đã công bố sẵn | ADR-050 | `test_m8_t83_otel.py` |
| | T-8.4 Cost/successful-task | `harness.eval.cost_per_success` — Wilson-scored CI | ADR-051 | `test_m8_t84_cost_per_success.py` |
| | T-8.5 Event stream | `Agent.stream()` — async generator, `Event` thật | ADR-052 | `test_m8_t85_stream.py` |
| | T-8.6 `Session` resource | `session.py::Session` — bọc `Chat`, backend cổ điển only | ADR-053 | `test_m8_t86_session.py` |
| **K-13 (phần còn lại)** | Va chạm namespace `P-`/`I-` | `POL-`/`PLUG-`/`IDEM-` — xem `07-risks-and-open-issues.md §2` | — | — |
| **M9 — Integration** | T-9.1 MCP client | `harness/mcp/` — `classify_mcp_tool()`, `Scope.server`. Đóng S-7/S-8/S-10/S-17, một phần S-9 | ADR-054 | `test_m9_t91_mcp.py` |
| | T-9.2 Service API | `harness/server/` — `POST /v1/runs`+`Idempotency-Key`, SSE, approvals qua HTTP. Caller thật đầu tiên của `execute_once`, ở MỨC RUN (không đóng S-4 — xem N-8) | ADR-055 | `test_m9_t92_service_api.py` |
| | T-9.3 Canonical event | `observe/events.py::to_dict()` — một hình dạng, ba transport (in-process/SSE/CLI `--json`) | ADR-056 | `test_m9_t93_canonical_events.py` |
| **M10 — Evaluation** | T-10.1 Trajectory contract | `harness/eval/trajectory.py::check_trajectory()` — 8 tiêu chí, hàm thuần | ADR-057 | `test_m10_t101_trajectory.py` |
| | T-10.2 Golden set | `harness/eval/golden.py::run_golden_set()` — pass rate + CI, dùng lại Wilson interval của T-8.4 | ADR-058 | `test_m10_t102_golden.py` |
| | T-10.3 Benchmark | `harness/eval/benchmark.py::benchmark()` + `import_cold_start_ms()` — đóng Y-05 | ADR-059 | `test_m10_t103_benchmark.py` |
| **N-9** (dọn nợ sau M10) | `tenant_id` chưa tới `Policy.check()` | `RunContext.tenant_id`/`_Ctx.tenant_id`, nối cả hai backend | ADR-060 | `test_n9_tenant_in_context.py` |
| **N-10** (phản hồi người dùng sau M10) | "2 API interfaces" gây khó dùng | `Agent(durable=True)` — chạy trên `harness.lg.build_agent()`, cùng method như backend cổ điển; `lg/adapter.py::ProviderChatModel` bọc `provider=` thành LangChain model (một seam gọi model, không phải hai); checkpoint SQLite mặc định, tự tạo | `docs/03-public-api.md §3.5`, `docs/02-architecture.md §3.1` | `test_durable_agent.py` (14), `test_parity.py` (mở rộng BA call shape) |
| (phản hồi người dùng sau N-10) | "compose 4 pattern thành middleware kiểu LangChain được không", "chuẩn hoá tham số các hook lại", rồi "đầu tư tiếp để có run_id/call_id thật" | `harness/middleware.py::Middleware` + `with_middleware()` — sugar dựng từ ba seam có sẵn (`ModelProvider`/tool callable/`Exporter`), KHÔNG phải seam thứ bảy. Mỗi hook nhận ĐÚNG MỘT object (`ModelCall`/`ToolInvocation`/`Event`) mang `.identity: RunIdentity` (`run_id`/`session_id`/`tenant_id`/`step`/`call_id`) — `contextvars.ContextVar` đặt MỘT LẦN mỗi run bởi `Agent`, layer thêm mỗi lời gọi bởi `run.py`/`dispatch.py`/`lg/runtime.py`. Lượt đầu kết luận SAI ("contextvars không sống sót qua backend graph") dựa trên một test tự dựng (`loop.run_in_executor()` trần) — verify lại với `langgraph` thật (`pregel/_executor.py` dùng `contextvars.copy_context()`) mới lộ ra: verify SAI trước, verify ĐÚNG sau khi bị hỏi tiếp — bài học K-13's "verify trước khi kết luận" áp cho cả một kết luận "không làm được" | `docs/03-public-api.md §3.6`, `docs/02-architecture.md §4` | `test_middleware.py` (21, gồm `IdentityThreading` — cả hai backend, test tường minh không cross-talk giữa các tool call chạy song song) |

**Ràng buộc xuyên suốt, giữ nguyên suốt roadmap:** core vẫn 3 dependency, import ~80ms.
Mọi thứ M6 trở đi là `extra` (`graph`/`viking`/`otel`/`mcp`/`server`; `eval` không cần
extra — chỉ dùng `jsonschema`, đã core). Mỗi seam mới qua đúng phép thử ranh giới plugin
ba phần (`docs/02-architecture.md §4`).

## 2. Tự chấm lại — đối chiếu, không bịa số mới

Điểm tự chấm GỐC (`docs/17-research-alignment.md §1`, trước M6-M10): **67.8/100**. Số đó
giờ lỗi thời, nhưng tự chấm một điểm CHÍNH XÁC mới đòi đúng sự nghiêm ngặt bản gốc có (so
với 12 framework + 9 harness thật, người chấm khác) mà một phiên tự động không tái tạo
được — bịa một con số mới có vẻ chính xác sẽ chính là "trả lời tự tin nhưng rỗng" luật §45
cấm. Thay vào đó, đối chiếu từng lý do TRỪ ĐIỂM gốc với code hôm nay:

| Chiều | Lý do trừ điểm GỐC | Sau M6-M10 |
|---|---|---|
| Control/Safety | Không có lớp Isolation nào | Một phần đóng — `Sandbox` + `Workspace` + egress-deny-default, nhưng KHÔNG phải namespace/cgroup isolation thật (ADR-047 nói thẳng) |
| Reliability | Không idempotency key, cancellation bị nuốt, không failure injection | Đóng hết (`execute_once` dù chưa gắn dispatch — N-8; cancellation đúng chuẩn; `chaos` 5 kịch bản) |
| Testability | Không trajectory contract, không golden set | Đóng hết (M10) |
| Extensibility | Không có MCP | Đóng (M9/T-9.1) |
| Observability | Không OTel thật, envelope thiếu trace/tenant/schema | Đóng hết (M8) |
| Integration | Không MCP, không Service API, không connector | MCP + Service API đóng; connector (ngoài MCP) vẫn không có |
| Performance | Chưa đo latency/throughput/concurrency | Đóng (M10/T-10.3) |
| Ecosystem | Chưa có | Không đổi — ngoài phạm vi M6-M10 |

Việc cần làm trước khi gắn một con số mới: một người (không phải phiên tự động) chấm lại
theo đúng phương pháp `docs/17 §1` — cùng thang 1-5, có so sánh với corpus thật.

## 3. Còn lại trước v1.0

Không còn là việc CODE. Theo `docs/17 §6`'s tiêu chí:

1. **Tự chấm lại bằng người thật** — xem `## 2`.
2. **Pilot 2-4 tuần** — cùng model, cùng task set, cùng tool set, cùng security policy.
   Không con số tự chấm nào thay thế được việc này; golden set (M10) là công cụ ĐO pilot,
   không phải thứ thay thế nó.
3. **Sáu mục kỹ thuật còn mở**, không mục nào chặn release — `07-risks-and-open-issues.md §7`
   liệt kê đầy đủ (`AuthEvidence` cho S-11, S-4/N-8's tool-call-level idempotency, S-9's
   re-pointing MCP, N-1/N-3 LangGraph parity, N-5/N-6 provider retry & usage telemetry).

**Sau v1.0:** mở rộng theo nhu cầu đo được, không theo lịch định sẵn (luật §8.4 "cắt khỏi
đường đi bắt buộc, không vứt đi", áp dụng nhất quán suốt roadmap này) — `AuthEvidence` là
ứng viên tự nhiên nhất nếu pilot cho thấy audit trail cần chịu được kiểm toán bên ngoài.
