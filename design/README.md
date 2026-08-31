# Bản thiết kế Harness Agent

Bản thiết kế này là **hệ quả trực tiếp** của nghiên cứu trong [`research/`](../research/):
30 gói, 3 hệ sinh thái, đọc source chứ không đọc docs, và 8 con số nổi bật đã bị bác bỏ
khi đọc code đằng sau chúng.

Nguyên tắc biên tập: **mỗi quyết định thiết kế phải trích dẫn một phát hiện cụ thể.**
Một quyết định không trích dẫn được là ý kiến, và ý kiến không thuộc về tài liệu này.

> **Đây là bản nháp thiết kế, viết TRƯỚC khi có code.** Tài liệu sống, khớp với code
> thật hôm nay, nằm ở [`../docs/`](../docs/) — bắt đầu từ [`../README.md`](../README.md).
> Thư mục này giữ lại vì nó được trích dẫn trực tiếp trong hàng trăm chỗ (comment code,
> `docs/*.md`, `07`/`08` dưới đây) như bằng chứng gốc cho MỘT quyết định cụ thể — xoá nó
> sẽ làm đứt mọi trích dẫn đó. `00`–`06` là bản thiết kế ban đầu (đa số vẫn khớp code
> thật, một số chỗ đã đổi tên/tinh chỉnh khi cài đặt — `07` ghi lại đúng chỗ nào);
> `07`/`08` là nhật ký rủi ro + roadmap SỐNG, cập nhật xuyên suốt quá trình xây; hai tệp
> `review-*` là hai vòng review đối kháng độc lập, nguồn của phần lớn phát hiện `07` xử lý.

---

## Đọc theo thứ tự

| tệp | nội dung | trạng thái |
|---|---|---|
| [00 — Foundation](00-foundation.md) | **Đọc trước.** Từ vựng, `Effect` class, hai lattice, `Decision` record, bốn quy tắc kiến trúc | Bản thiết kế gốc |
| [01 — Core API](01-core-api.md) | API công khai, Zero-to-Agent, DX, plugin API | Bản thiết kế gốc |
| [02 — Safety Engine](02-safety-engine.md) | PolicyEngine, vòng đời `Decision`, audit sink, thực thi taint | Bản thiết kế gốc |
| [03 — Tools & MCP](03-tools-and-mcp.md) | `ToolSpec`, taxonomy lỗi, barrier song song, idempotency, phân loại MCP | Bản thiết kế gốc |
| [04 — Runtime & Durability](04-runtime-durability.md) | Graph, checkpoint, `unguarded_paths()`, cancel, sub-agent | Bản thiết kế gốc |
| [05 — Cost & Memory](05-cost-and-memory.md) | `Ledger`, `reserve/hold/release`, compaction, provenance bộ nhớ | Bản thiết kế gốc |
| [06 — Poka-Yoke Matrix](06-poka-yoke-matrix.md) | Mỗi lớp lỗi ↔ cơ chế chặn ↔ mức Poka-Yoke | Bản thiết kế gốc |
| [07 — Risks & Open Issues](07-risks-and-open-issues.md) | Mọi phát hiện review (S-/K-/N-series): sửa ở đâu, hoãn vì sao, còn mở gì | **Nhật ký sống** |
| [08 — Roadmap & Release Plan](08-roadmap-and-release-plan.md) | Việc còn lại, thứ tự làm, điều kiện release — M6-M10 | **Nhật ký sống, ĐÃ XONG** |
| [review-kiss.md](review-kiss.md) | Vòng review đối kháng #1 — KISS/YAGNI, cắt thừa | Review độc lập, đã xử lý ở `07` |
| [review-security.md](review-security.md) | Vòng review đối kháng #2 — tấn công bảo mật (S-1…S-29) | Review độc lập, đã xử lý ở `07` |

**"Bản thiết kế gốc" (00–06) nghĩa là gì:** viết trước khi có dòng code nào, và đa số vẫn
mô tả đúng hành vi thật — `docs/*.md` là bản kế thừa nó sau khi cài đặt, và khi hai bên
lệch nhau, **`docs/*.md` đúng, không phải các tệp này**. `07` ghi lại từng chỗ số hiệu
bất biến (`I-`, `P-`, `C-`, `R-`) va chạm giữa các tệp này và bị đổi tên (K-13).

---

## Bảy khuyết điểm đang sửa, và bằng chứng cho từng cái

Đây là lý do bản thiết kế này tồn tại. Mỗi dòng là một phát hiện **đo được**, không phải
một linh cảm.

| # | khuyết điểm của cả ngành | bằng chứng | sửa ở đâu |
|---|---|---|---|
| 1 | **Approval là trạng thái quyền, không phải quyết định có thể audit** | 30 gói, 3 ngôn ngữ, không một ngoại lệ. `_ApprovalRecord` của openai-agents là `bool \| list[str]` với `always_approve` vĩnh viễn; `ToolConfirmation` của Java là đúng một boolean; tín hiệu audit dày nhất trong Python là một tool **model tự gọi để ghi về mình** | [00 §4](00-foundation.md) · [02](02-safety-engine.md) → `policy/decision.py::Decision` |
| 2 | **Kiến trúc an toàn tốt nhất lại không được bật** | Lattice IFC hai chiều duy nhất tìm được nằm trong `agent_framework.security`; không file nào trong `_harness/` import nó, và nó không có trong `__init__` top-level | [00 §5 R-1](00-foundation.md) · [04](04-runtime-durability.md) → `policy/label.py::Label` |
| 3 | **Trạng thái chia sẻ rò rỉ giữa các run đồng thời** | `threading.local()` được set xuyên qua `await` trong một module async — hai tool call đọc nhầm slot của nhau, im lặng và **fail-open**. Cộng thêm hai singleton mức module | [00 §5 R-4](00-foundation.md) · [04](04-runtime-durability.md) → một `Ledger`/`TaintTracker`/`EventBus`/`DecisionLog` mỗi run |
| 4 | **Không ai chặn TIỀN, chỉ chặn số bước** | Không dự án nào reserve budget trước khi gọi model; `max_turns` bounds steps, not spend | [05](05-cost-and-memory.md) → `budget/ledger.py::Ledger.reserve` |
| 5 | **Không idempotency ở mức tool call** | Không ai có; agno chỉ bảo vệ *run submission* | [03](03-tools-and-mcp.md) → `idempotency.py::execute_once` (T-6.1, xây xong, CHƯA gắn vào dispatch — N-8) |
| 6 | **Không memory write nào ghi provenance** | Một "fact" trích từ trang web độc hại được lưu y hệt câu user gõ → prompt injection sống sót qua session | [05](05-cost-and-memory.md) → `Store.put(..., provenance=)` |
| 7 | **Model cầm công tắc an toàn của chính nó** | `mode_set` có `approval_mode="never_require"`; thứ duy nhất ngăn model rời chế độ "plan" là một câu tiếng Anh nói với chính model | [00 §5 R-3](00-foundation.md) · [02](02-safety-engine.md) → `effect` không có default, `@tool(effect=...)` bắt buộc |

---

## Tám điểm mạnh đang học, và học của ai

Bản thiết kế này không phát minh lại. Nó hợp nhất những chỗ từng dự án làm đúng.

| điểm mạnh | của ai | dùng ở đâu |
|---|---|---|
| Approval khoá theo **giá trị tham số** + biên MCP server, serialise qua resume | Microsoft `ToolApprovalRule` | [00 §4.1](00-foundation.md) · `policy/decision.py::Scope` |
| Lattice thông tin **hai chiều** (integrity × confidentiality) + quarantine model | Microsoft `security.py` | [00 §3.2](00-foundation.md) · [02](02-safety-engine.md) |
| Taxonomy lỗi tool **ba nhánh**, phân biệt cái nào tiêu retry budget | pydantic-ai `ModelRetry`/`ToolFailed` | [03](03-tools-and-mcp.md) |
| **Barrier** cho tool không chạy song song được | pydantic-ai `sequential=True` | [03](03-tools-and-mcp.md) |
| **Graph → durability**: checkpoint 51,6/kLOC, tái lập ở cả TypeScript | LangGraph | [04](04-runtime-durability.md) · `src/harness/lg/` |
| **Around-hook** `wrap_model_call`/`wrap_tool_call` nhận `handler` | LangChain 1.x middleware | [01](01-core-api.md) |
| Turn budget **không reset** qua handoff | openai-agents | [04](04-runtime-durability.md) · [05](05-cost-and-memory.md) |
| Ghép cặp `tool_call`/`tool_result` được **TÍNH**, không phải tài liệu hoá | Microsoft `_unambiguous_function_call_result_pairs` | [05](05-cost-and-memory.md) |
| Default **fail-closed** khi thiếu annotation | MCP `ToolAnnotations` | [03](03-tools-and-mcp.md) · `harness/mcp/` |
| `CancellationToken` tường minh qua mọi biên API | autogen (26,9/kLOC — cao nhất nghiên cứu) | [04](04-runtime-durability.md) |
| Idempotency xử lý race bằng đọc lại, **không nuốt** `IntegrityError` | agno | [03](03-tools-and-mcp.md) |

---

## Ý tưởng trung tâm

Ba câu, và cả bản thiết kế là hệ quả của chúng.

**1. Phân loại một lần, suy ra năm hành vi.**
Người viết tool khai đúng một thứ — `effect` ∈ `read`/`write`/`external`/`danger`. Tính an
toàn song song, khả năng retry, việc làm nhiễm context, verdict mặc định và mức audit đều
là *dẫn xuất*. Nghiên cứu cho thấy cái giá của việc không có mô hình này: LangChain phải
viết tay một guard riêng cho `write_todos` vì tool đó đã hỏng — guard chỉ tồn tại ở chỗ ai
đó đã bị cắn, còn tool thứ hai mươi thì không có guard nào.

**2. Invariant nằm trên đường đi không bypass được; plugin chỉ dành cho policy.**
Vì "ship middleware nhưng harness không install" là lỗi **thật, đã quan sát được** ở một
vendor lớn, không phải giả thuyết. Budget, taint, permission check nằm trong đường đi bắt
buộc. Retry, cache, log, model fallback nằm trong plugin.

**3. Chứng minh được, không phải review được.**
23 vòng review tìm ra 20 lỗi và **0 lỗi bảo mật**. 16 vòng *chạy* tìm ra 38+ lỗi và
**4 lỗi bảo mật**. Vì vậy `unguarded_paths()` là một phép chứng minh khả đạt trên graph đã
compile, và P-2 (policy chỉ thắt chặt được) là một property-based test — không phải một
mục trong checklist review. Nguyên tắc này áp dụng lại đúng như vậy lên cả roadmap tăng
trưởng M6-M10: mỗi milestone đóng kèm test thật, và không dưới ba lỗi thật (N-2, N-4,
N-9) chỉ lộ ra lúc *viết* test/tài liệu, không phải lúc review văn xuôi.
