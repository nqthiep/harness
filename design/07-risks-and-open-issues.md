# Rủi ro, đánh đổi, và vấn đề còn mở

Tệp này tồn tại vì luật §45 của nghiên cứu: **thà nói "Chưa đủ evidence" còn hơn đoán.**
Mọi phát hiện từ hai vòng review đối kháng ([review-kiss.md](review-kiss.md),
[review-security.md](review-security.md) — 58 phát hiện, `S-`/`K-`) và mọi lỗi tự bắt được
trong lúc xây roadmap M6-M10 (`N-`) đều được xét ở đây, kiểm lại trên **code thật hôm nay**
trước khi ghi "đã sửa" hay "lỗi thời" — không đoán từ văn bản review gốc.

**Kỷ luật chung:** mỗi mục dưới đây trả lời — còn đúng không? sửa được ở đâu? cái gì
KHÔNG sửa và vì sao? Chi tiết kỹ thuật sâu (code, ADR, test) nằm ở `docs/12-decision-logs.md`;
tệp này chỉ giữ đủ để hiểu quyết định, không lặp lại toàn bộ lý luận.

---

## 0. Trạng thái tóm tắt

**Tất cả 58 phát hiện gốc đã được xét. 10 phát hiện mới (N-1…N-10): N-1…N-9 tự bắt được
trong lúc xây M6-M10, N-10 đóng phản hồi trực tiếp của người dùng ("2 API interfaces gây
khó dùng") sau đó.** Còn mở thật sự, hôm nay: **2 mục** — xem `## 7`.

### Bảo mật (S-1…S-29)

| Mã | Tóm tắt | Trạng thái |
|---|---|---|
| S-1 | Nén ngữ cảnh gọi model thiếu `reserve()` | Lỗi thời — cơ chế review mô tả không tồn tại |
| S-3 | `Secret[T]` lộ qua tool result | **Đã sửa**, cả hai nguồn (`Grants.sensitive` + `.reveal()`) |
| S-4 | Idempotency ở mức MỘT lời gọi tool | **Một phần đã sửa** (N-8) — retry-mid-run cùng một call đã dedupe cả hai backend; crash-across-process vẫn là ranh giới đã biết (`docs/05 §3`'s luật resume), không tuyên bố đóng |
| S-5 | Memory provenance giả mạo được | Lỗi thời — cơ chế thật đơn giản hơn, không có lỗ hổng đó |
| S-6 | Grant cũ thắng một DENY taint mới | Đã đúng sẵn, chỉ thiếu test khoá lại |
| S-7, S-8, S-10 | `ServerIdentity`/hint hạ effect/`proposed_scope` cho MCP | **Đã sửa** — `harness.mcp` (T-9.1) |
| S-9 | Grant MCP rò giữa hai server | **Đã sửa một phần** — khác nhãn thì chặn được; MỘT nhãn bị trỏ lại endpoint khác thì chưa |
| S-11 | `Actor` là lời tự khai, không xác thực | **Sửa một phần** — kênh báo danh tính có rồi; xác thực thật (`AuthEvidence`) chưa |
| S-12 | Ba chữ ký resume mâu thuẫn | Lỗi thời — không tồn tại trong code |
| S-13 | Sub-agent không bị cap step/wall-clock cha | **Đã sửa** |
| S-14 | Reservation chồng nhau không bị chặn | **Đã sửa một phần** — race đã chặn; `Ledger.void()` chưa cần (0 caller) |
| S-15 | Policy có state bị dùng chung xuyên request | **Đã sửa** (LangGraph); backend cổ điển đã đúng sẵn, khác cơ chế có chủ ý |
| S-16 | `accepts_tainted` khai được trong `@tool` | **Đã sửa** — chỉ operator đặt được |
| S-17 | Injection qua `description` tool MCP | **Đã sửa** — cùng lúc T-9.1, đóng bằng tài liệu + fail-closed default |
| S-18 | `EgressPolicy` không chặn DNS rebinding | **Đã sửa bằng tài liệu** — không có cách sửa ở tầng policy đồng bộ |
| S-19 | Taint sticky-per-run rửa được | **Đã sửa** — nhãn per-message (LangGraph); sticky có chủ ý (backend cổ điển) |
| S-20 | `Budget(usd=None)` bỏ qua cảnh báo đã hứa | **Đã sửa** |
| S-21 | `Ledger.snapshot()` mất cờ `blocked` | **Đã sửa** |
| S-22 | Định giá thấp hơn thực tế khi có cache write | **Đã sửa** |
| S-23 | `call_key` thiếu domain separator | Lỗi thời — cơ chế review mô tả không tồn tại |
| S-24 | `EventBus`/`seq` dùng chung xuyên thread | **Đã sửa** — rộng hơn mô tả gốc |
| S-25 | Argument không escape tới approver + không trần số ASK | **Đã sửa** |
| S-26 | `Scope.args` ép kiểu về chuỗi | Lỗi thời — đã là `Any`, so khớp giữ kiểu |
| S-27 | Taint/confidentiality cũ trong cùng batch | **Đã sửa** — nhánh confidentiality có thật |
| S-28 | Sub-agent ASK không có đường tới `approve` cha | Đã đúng sẵn, chỉ thiếu tài liệu |
| S-29 | Tái dùng grant không ghi audit | **Đã sửa** — chỉ áp cho backend LangGraph |

### KISS (K-series)

| Mã | Tóm tắt | Trạng thái |
|---|---|---|
| K-6 | `Confidentiality` không có nguồn dữ liệu | Hết hiệu lực — S-3 đã cho nó nguồn |
| K-7 | `Reservation.exact` không ai đọc | Lỗi thời — **không cắt**, có consumer thật |
| K-9 | `Result.raise_for_status()` thừa | **Đã cắt** |
| K-10 | Taxonomy OTel 9 span quá nhiều | Rút gọn kế hoạch xuống 4 span — chưa có code OTel lúc đó để cắt |
| K-11 | `end_strategy` đặt tên va chạm | **Đã sửa** (đổi tên) |
| K-12 | `ServerIdentity{label,fingerprint}` chưa chốt được `fingerprint` | **Đã sửa** — hạ xuống `ServerLabel` cho v1 |
| K-13 | Va chạm số hiệu bất biến giữa các tệp `design/*.md` | **Đã sửa**, hai lượt |
| K-22 | "14 tên, một import" tự mâu thuẫn với ví dụ Mức 3 | **Đã sửa** |
| K-23 | Chín tunable quá nhiều | **Không cắt** — bảy trong chín đã lỗi thời hoặc chưa từng tồn tại |
| K-28 | Interface mẫu thiếu "năm thứ" | **Đã sửa** — sửa lại thành đúng ba, nói rõ `session` là tầng service |

### Phát hiện mới, bắt được khi xây M6-M10 (N-series)

| Mã | Tóm tắt | Trạng thái |
|---|---|---|
| N-1 | LangGraph không timeout per-tool | **Đã sửa** — `Ledger.tool_timeout()` + `_with_timeout()`, cùng logic hai thông điệp với `dispatch.py` |
| N-2 | `try_run()` raise thẳng khi `returns=` sai kiểu | **Đã sửa** |
| N-3 | LangGraph không hỗ trợ `returns=` | **Đã sửa** — `Runtime.finish()` giờ gọi `run.py::parse_returns()` trước khi phát `run.finished`, cùng implementation với backend cổ điển |
| N-4 | Lỗi provider crash thẳng ra ngoài, cả hai backend | **Đã sửa** |
| N-5 | Retry cấp provider đã công bố nhưng chưa cài | **Đã sửa** — `retry.py::with_provider_retry()`, một implementation cho cả hai backend |
| N-6 | `model.response` VÀ `run.finished` thiếu trường tài liệu đã hứa | **Đã sửa** — `usage`/`latency_ms`/`duration_s` giờ emit đầy đủ trên cả hai backend |
| N-7 | `Agent.with_()` làm mất bốn trường, mọi lần gọi | **Đã sửa** |
| N-8 | `execute_once` có caller thật nhưng chưa gắn vào tool dispatch | **Đã sửa** — gắn vào `Dispatcher._invoke`/`_run_tools`, cả hai backend; đóng đúng nửa "retry mid-run" của S-4, không phải toàn bộ |
| N-9 | `tenant_id` chưa bao giờ tới được `Policy.check()` | **Đã sửa** |
| N-10 | "2 API interfaces" (backend cổ điển vs LangGraph) | **Đã sửa** — `Agent(durable=True)` |

---

## 1. Chi tiết — bảo mật (S-series)

### Đã sửa bằng code

**S-3 — `Secret[T]` lộ qua tool result, cả hai nguồn.** `Grants.sensitive` (operator đánh
dấu tool) đi cùng `policy/label.py`'s `Label` hai trục — landing chung với S-16/S-19 (bên
dưới). Nguồn thứ nhất, `.reveal()` một `Secret` rồi giá trị xuất hiện nguyên văn trong
payload trả về: `secrets.contains_live_secret()` dò đúng phép so khớp `redact()` dùng
(không tự `.reveal()`); `emits_of()` nâng nhãn message đó lên `SECRET` khi phát hiện, dù
`redact()` đã xoá token khỏi bytes model thấy — phần còn lại của message vẫn cần nhãn để
chặn nó rời qua sink `PUBLIC`. `tests/test_attack_s3.py`, mutation-tested, cả hai backend.

**S-13 — sub-agent không bị cap `steps`/`wall_clock_s` của cha.** `Ledger.hold_steps()`/
`release_steps()` áp đúng lý luận TOCTOU của `hold()` (trục tiền) sang trục step;
`child_wall_clock()` cắt trần thời gian con xuống đúng số cha còn lại lúc spawn.
`tests/test_attack_s13.py`.

**S-14 (một phần) — reservation chồng nhau đọc cùng ngân sách "còn trống".**
`Ledger._committed()` cộng mọi reservation đang mở vào `remaining_usd()` và cả hai nhánh
kiểm ngân sách của `reserve()`. `Ledger.void()` — kiểm kỹ, không có `try/except` nào ở cả
hai backend nằm giữa `reserve()` và `settle()` mà cần huỷ một reservation giữa chừng; 0
caller hôm nay, để dành cho khi có plugin `Retry` cấp model-call thật.
`tests/test_attack_s14.py`.

**S-15 — policy có state bị mọi thread dùng chung (backend LangGraph).**
`build_agent()` giờ từ chối construction bất kỳ policy nào không phải factory
(`ConfigError`); `Runtime._engine_for(run_id)` dựng một `PolicyEngine` riêng mỗi thread.
Backend cổ điển đã có cơ chế ĐÚNG kiểu khác từ trước (`_check_shared_policy_state`, dò
sau khi chạy — vì có ranh giới run() rõ, graph thì không) — không áp luật "chỉ nhận
factory" sang đó, sẽ phá một ca dùng hợp lệ (policy cấu hình thuần, không state).
`tests/test_attack_s15.py`.

**S-16/S-19/S-3 nguồn hai — mô hình taint hai trục.** `policy/label.py` (canonical
`Integrity` × `Confidentiality` × `Label`, `Grants`), `policy/builtin.py` (`check_flow`,
`emits_of`), nhãn PER-MESSAGE trên backend LangGraph (chống taint bị "rửa" khi compaction
xoá nội dung nhưng không xoá nhãn). Backend cổ điển giữ sticky-per-run có chủ ý — nó không
tính lại theo message nên miễn nhiễm với đúng kiểu rửa taint per-message phải phòng.
`tests/test_attack_s19.py`, 11 test.

**S-20 — `Budget(usd=None)` bỏ qua cảnh báo đã hứa.** `usd=None` VẪN được phép (escape
hatch có chủ đích, provider miễn phí) — cái thiếu là phần hai của lời hứa tài liệu:
`EventKind.BUDGET_UNLIMITED` phát đúng một lần mỗi run/thread ngay sau `RUN_STARTED`.
Phát hiện "chặn phát hành" duy nhất còn sống trong 58 phát hiện. `tests/test_attack_s20.py`.

**S-21 — `Ledger.snapshot()` mất cờ `blocked`.** Thêm vào cả `snapshot()`/`restore()`.

**S-22 — định giá thấp hơn thực tế khi ghi cache thật.** `size_call()`/`reserve()` định
giá lại theo mức TỆ NHẤT (`cache_write_per_mtok`), không phải `input_per_mtok`.

**S-24 — `EventBus`/`seq` dùng chung xuyên thread, rộng hơn mô tả gốc.** Lỗi Round 37 đã
sửa cho `Ledger`/`TaintTracker`, S-15 sửa cho `PolicyEngine`, lần thứ tư: `Runtime._bus_cache`
giữ một `EventBus` riêng mỗi thread, dựng lười.

**S-25 — argument không escape tới approver + không trần số `ASK`.**
`secrets.safe_for_display()` escape ký tự không in được + digest giá trị dài; trần
`max_asks_per_run` (mặc định 20) — approval fatigue là một kênh model điều khiển được.

**S-27 — taint/confidentiality cũ trong cùng batch tool call.** Kịch bản gốc
(external+danger cùng lượt) đã bị `_check_tool_set` chặn lúc dựng; nhánh CÒN SỐNG là
confidentiality (SECRET vào sink PUBLIC). Cả hai backend recheck `check_flow` ngay trước
mỗi lời gọi serial, dùng nhãn SỐNG thay vì nhãn đầu-batch.

**S-29 — tái dùng grant không ghi audit (backend LangGraph).** `_regate` giờ ghi một
`Decision` thứ hai (`id`-`reuse`) mỗi lần một grant còn sống được tái dùng — chỉ áp cho
LangGraph, vì backend cổ điển không có `DecisionLog`.

### Sửa được một phần, phần còn lại cần thiết kế riêng

**S-9 — grant MCP rò giữa hai server.** `Scope.server` (T-9.1) chặn được va chạm giữa
HAI NHÃN khác nhau. Chưa chặn được: một nhãn bị trỏ lại sang endpoint khác trong khi giữ
nguyên tên — cần `ServerIdentity`+`fingerprint` (K-12's lý do hoãn: định dạng
`fingerprint` chưa chốt cho MCP stdio), chờ tới khi quan sát được một lần re-pointing thật.

**S-11 — `Actor` là lời tự khai, không có xác thực.** `Approval(ok, actor=...)` mở kênh
TUỲ CHỌN cho `approve=` báo danh tính thật (phiên Slack đã xác thực, OAuth) thay vì
placeholder chung — nhưng không chặn được một callback CỐ TÌNH khai gian. Cần
`AuthEvidence` (chữ ký kênh, `channel_message_id`) — thiết kế riêng, chưa bắt đầu, xem `## 7`.

### Đã sửa bằng tài liệu (không có cách sửa ở tầng code)

**S-18 — `EgressPolicy` không chặn DNS rebinding.** `Policy.check` bắt buộc thuần/đồng bộ
(POL-4) khiến nó chỉ so khớp CHUỖI hostname, không resolve DNS. Không có cách sửa ở tầng
`Policy` — cần một tầng mạng thật (egress proxy). Sửa bằng cách nói thẳng giới hạn trong
docstring + `docs/06-safety.md`.

**S-17 — injection qua `description` tool MCP.** Đóng cùng lúc T-9.1: description tới
model trước lời gọi tool đầu tiên là bản chất giao thức tool-calling, không chặn được mà
không phá giao thức — `default_effect=DANGER` cho server chưa duyệt là hàng rào thật.

### Đã kiểm — lỗi thời hoặc đã đúng sẵn, không cần sửa

S-1 (cơ chế nén review mô tả không tồn tại), S-5 (provenance giả mạo — cơ chế thật đơn
giản hơn, không có lỗ hổng), S-6 (composition đã đúng, chỉ thiếu test), S-12 (ba chữ ký
resume không tồn tại trong code), S-23 (idempotency `call_key` review mô tả không tồn
tại), S-26 (`Scope.args` đã là `Any`, không ép kiểu), S-28 (sub-agent ASK đã có đường
đóng qua luật "không có `approve=`" sẵn có, chỉ thiếu tài liệu). Mỗi mã có test khoá lại
hành vi thật trong `tests/test_attack_*.py`.

---

## 2. Chi tiết — KISS (K-series)

**Đã cắt:** K-9 (`Result.raise_for_status()` — không consumer nào khác `run()`).

**Đã sửa (đổi tên/nói rõ hơn):** K-11 (`end_strategy`'s giá trị thứ ba đổi tên tránh va
chạm), K-12 (`ServerIdentity` hạ xuống `ServerLabel` cho v1), K-13 (namespace bất biến —
xem `08-poka-yoke-matrix.md`), K-22 ("14 tên" sửa thành đúng phạm vi Mức 0-2), K-28
(interface mẫu sửa lại đúng ba thứ, `session` là tầng service).

**Không cắt — lý do đã lỗi thời:** K-7 (`Reservation.exact` CÓ consumer thật —
`run.py` đọc nó cho sự kiện `BUDGET_RESERVED`); K-23 (bảy trong chín tunable review liệt
kê hoặc đã lỗi thời hoặc chưa từng được xây — không có gì để cắt).

**Không có code để cắt lúc review viết:** K-10 (taxonomy OTel — chưa có tích hợp OTel nào
tồn tại khi đó). Rút gọn kế hoạch thẳng trong `design/04-runtime-durability.md §8.2`
xuống bốn span, để lúc OTel thật được xây (T-8.3), xây đúng bốn ngay từ đầu — và đúng như
vậy.

---

## 3. Phát hiện mới, bắt được khi xây roadmap M6-M10

Không thuộc hai vòng review gốc — đánh số riêng `N-` để không va chạm với `S-`/`K-` đã có
(đúng bài học K-13).

**N-1 (đã sửa) — LangGraph không timeout per-tool.** `lg/runtime.py::_run_tools` không có
`async with asyncio.timeout(...)` nào bọc quanh lời gọi tool — khác `dispatch.py::_invoke`
(Round 23). Một tool `read` treo mãi mãi (HTTP call không timeout riêng) treo cả node
graph vô thời hạn; chỉ wall-clock CẤP RUN chặn được, và chỉ kiểm đầu mỗi bước. **Từ N-10
(`Agent(durable=True)`): gap này tới được từ mặt API chính, không chỉ từ `build_agent()`
— cùng một `Runtime`, không phải hai bản** — nên bản vá này đóng gap trên cả hai đường
vào cùng lúc.

Sửa: `Ledger.tool_timeout(spec.timeout_s)` — helper clamp-về-wall-clock-còn-lại,
`dispatch.py::_invoke` đã dùng sẵn cho backend cổ điển — tính LẠI mỗi lần thử (mỗi
`attempt`, không phải một lần đầu batch), rồi bọc lời gọi tool bằng nó. Vướng một chỗ:
`_run_tools` gọi tool qua `asyncio.run(spec.fn(**args))` trần — `asyncio.timeout()` cần
`async with` bên trong một coroutine, không có chỗ nào để đưa nó vào một lệnh gọi
`asyncio.run()` trực tiếp. Giải: một coroutine wrapper nhỏ,
`_with_timeout(coro, timeout)`, làm đúng một việc — `async with asyncio.timeout(timeout):
return await coro` — rồi `asyncio.run(_with_timeout(spec.fn(**args), timeout))` thay
chỗ gọi trần. `except TimeoutError:` tách khỏi `except Exception as exc:` chung, với
đúng logic hai thông điệp `dispatch.py` đã có: `"timed out: run wall-clock budget
reached"` khi timeout bị clamp bởi ngân sách CẤP RUN (`timeout < spec.timeout_s`), hay
`f"timed out after {spec.timeout_s}s"` khi chính trần của tool là cái chạm trước —
retry (`read`/`external`) vẫn áp dụng như một lỗi tool bình thường. `asyncio.CancelledError`
vẫn `raise` thẳng, không bao giờ thành lỗi tool — không đổi so với trước bản vá.
`tests/test_n1_graph_tool_timeout.py`: một tool treo 5s với `timeout_s=0.05` bị cắt
trong dưới 2s (không phải 5s) và run vẫn kết thúc `ok=True`; một tool khác, `timeout_s`
riêng rộng (30s) nhưng ngân sách CẤP RUN hẹp hơn (`wall_clock_s=0.05`), tạo đúng thông
điệp thứ hai — xác nhận nhánh clamp-bởi-run, không chỉ nhánh clamp-bởi-tool.

**N-2 (đã sửa) — `try_run()` raise thẳng khi model trả rác khớp sai `returns=`.**
`_parse_returns()` giờ chạy TRƯỚC khi `RUN_FINISHED` phát, bắt `ToolContractError` và hạ
xuống `Result(stop_reason=ERROR)` — model trả rác là một OUTCOME, không phải crash.

**N-3 (đã sửa) — LangGraph không hỗ trợ `returns=`.** `build_agent()` không có tham số
này; `Result.value` luôn `None` trên backend đó. N-10 (`Agent(durable=True)`) đã đóng
phần "âm thầm" trước — `Agent(durable=True, returns=...)` raise `ConfigError` ngay lúc
dựng thay vì để `Result.value` lặng lẽ luôn `None` — bản vá này đóng nốt bản thân khoảng
trống, gỡ luôn `ConfigError` đó.

Hoá ra MỘT nửa đã có sẵn, không cần sửa: `_output_format(returns)` (yêu cầu model trả
đúng hình dạng) đi qua `Agent._asm` — CÙNG `ContextAssembler` cả hai backend dùng chung
(`lg/adapter.py::ProviderChatModel._generate()` build request từ `self.asm`, không dựng
request riêng) — nên phía GỬI ĐI đã đúng từ trước, không có gì để sửa ở đó. Nửa thiếu
thật là phía ĐỌC VỀ: không nơi nào trên backend durable từng gọi
`run.py::_parse_returns()` để parse câu trả lời cuối.

Sửa: chuyển `_parse_returns` từ method riêng của `RunEngine` thành hàm module-level
`run.py::parse_returns(want, text)` (không đổi logic, chỉ đổi chỗ ở — `agent.py` VÀ
`lg/runtime.py` đều đã import từ `run.py`, không có import vòng); `build_agent()` nhận
thêm `returns=`, thread xuống `Runtime._returns`; `Runtime.finish()` gọi `parse_returns`
NGAY TRƯỚC khi phát `run.finished` — parity đúng với T-6.4's bản vá cho backend cổ điển
(nếu parse SAU khi graph đã trả về, `run.finished` sẽ báo `completed` cho một câu trả
lời `returns=` từ chối, đúng lỗi T-6.4 đã sửa). `agent.py::_state_to_result()` parse LẠI
(cùng `text`, cùng `returns`, xác định) để dựng `Result.value` thật — giá trị đó không
bao giờ đi qua state đã checkpoint, vì state phải giữ JSON-checkpointable còn một
dataclass instance thì không. `test_durable_agent.py` (2 test thay chỗ test cũ khẳng
định `ConfigError`), `tests/test_n3_durable_returns.py` (3: `run.finished` báo đúng
`error` cho câu trả lời hỏng thay vì `completed` cũ rồi mới sửa; báo đúng `completed`
cho câu trả lời tốt; escape hatch `build_agent()` thô cũng parse đúng).

**N-4 (đã sửa) — lỗi provider crash thẳng ra ngoài, cả hai backend.** MỌI lần gọi
provider thật gặp rate limit/timeout tạm thời crash chương trình gọi nó — không có
`except` nào cho `ProviderError`/`ProviderTimeout`/`ProviderRateLimited` ở bất kỳ đâu
trong `src/harness/` trước bản vá. Nghiêm trọng hơn N-2: đây là đường đi PHỔ BIẾN nhất
khi chạy với provider thật. Cả hai backend giờ bắt và hạ xuống `Result(ERROR)`.

**N-5 (đã sửa) — retry cấp provider đã công bố nhưng chưa cài.**
`docs/10-observability-ops.md §3` hứa `ProviderRateLimited`/`ProviderUnavailable`/
`ProviderTimeout` đều tự động retry — N-4 chỉ biến lỗi thành `Result(ERROR)`, không tự
retry gì. `src/harness/retry.py::with_provider_retry()` — MỘT implementation cho cả hai
backend (`run.py`'s `self._p.complete(...)`, `lg/adapter.py::ProviderChatModel.
_generate()`'s `self.provider.complete(...)`), y hệt lý do `dispatch.py` là nơi DUY NHẤT
tool retry (T-6.3) sống, không phải hai bản tay viết có thể lệch (R-17).
`ProviderError` giờ mang `retry_after_s` (`errors.py`); `models/anthropic.py::_map()`
đọc header `Retry-After` thật từ `httpx.Response` khi vendor gửi (`_retry_after()`).
Backoff mũ + jitter khi vendor không gửi header. Bị chặn bởi `deadline_s` — ngân sách
wall-clock CÒN LẠI của run (`Ledger.remaining_wall_clock()`) — không phải chỉ đếm số
lần thử: retry không bao giờ sống lâu hơn ngân sách, đúng lời hứa của docs/10 §3.

Một cạm bẫy suýt gây lỗi thật: bản đầu định truyền `deadline_s`/`on_retry` qua
`.invoke()`'s `**kwargs` — giống hệt cách `max_tokens` đã truyền an toàn. Kiểm trực tiếp
`langchain_anthropic.ChatAnthropic._get_request_payload` (đã cài trong sandbox) mới lộ
ra: nó merge MỌI kwarg không nhận diện được thẳng vào payload gửi API
(`{**self.model_kwargs, **kwargs}`) — `max_tokens` là trường Anthropic thật nên an toàn,
`deadline_s`/`on_retry` thì không, sẽ làm API 400 ngay khi ai dùng escape hatch với model
thật. Sửa bằng `retry.retry_scope()` — một `contextvars.ContextVar` riêng, `call_model`
đặt quanh đúng lời gọi `.invoke()`, `_generate()` đọc lại trong CÙNG call stack (không
qua thread nào, nên không cần cơ chế copy-context của `middleware.py`) — không kwarg lạ
nào chạm tới `.invoke()` nữa ngoài `max_tokens`.

**N-6 (đã sửa) — `model.response` VÀ `run.finished` thiếu trường tài liệu đã hứa.**
`docs/05-data-and-state.md §1` hứa `model.response` mang `usage{in,out,cache_read,
cache_write}`/`latency_ms`, `run.finished` mang `usage`/`duration_s` — cả hai backend chỉ
emit `stop_reason`/`cost_usd`/`steps`/`tainted`. Đã emit đủ trên cả hai: `run.py` đo
`latency_ms` quanh `with_provider_retry(...)`, cộng dồn `usage_total`, đo `duration_s` từ
`run_t0`. `lg/runtime.py` cần state MỚI (`lg/state.py`): `turn_started_at` (đồng hồ
`duration_s`, reset đúng điểm `asks` đã reset — một turn, không phải một thread, vì
`RUN_FINISHED` ở backend này vốn đã bắn mỗi turn, không phải mỗi thread) và `turn_usage`
(cộng dồn bởi `call_model`, `dataclasses.asdict(Usage(...))` — JSON-checkpointable, cùng
quy ước với `ledger`). `latency_ms` đo trong `lg/adapter.py::_generate()` (tổng thời gian
CẢ retry, không chỉ lần thử cuối) rồi gửi qua `AIMessage.response_metadata` cho
`call_model` đọc lại.

Phần `step=` (bất đối xứng backend, phát hiện lúc review `middleware.py`, N-10): 11 điểm
`_emit` trong `lg/runtime.py` thiếu `step=`, `Event.step` luôn `None` ở đó dù backend cổ
điển luôn có. Đã thêm `step=state.get("step", 0)` vào cả 11; verify bằng in trực tiếp
`event.step` qua một run `durable=True` thật — không còn `None` nào ngoài `run.started`/
`run.finished` (đúng như backend cổ điển).

Verify cả hai phần: `tests/test_n5_n6_retry_and_usage.py` (8 test — retry thành công sau
N lần lỗi tạm thời trên cả hai backend, lỗi KHÔNG tạm thời không bao giờ retry, hết
`MAX_ATTEMPTS` vẫn hạ cánh mềm thành `Result(ERROR)` chứ không crash, retry không sống
lâu hơn ngân sách wall-clock, cả hai event mang đủ trường, `turn_usage` reset đúng theo
turn chứ không cộng dồn qua các lượt `try_run()` khác nhau trên cùng một thread).

**N-7 (đã sửa) — `Agent.with_()` làm mất bốn trường, MỌI lần gọi.**
`transcript`/`exporters`/`accepts_tainted`/`sensitive` biến mất khỏi agent phái sinh —
không phải lỗi riêng của tính năng nào đang xây, ảnh hưởng mọi caller của `with_()`.
`tests/test_n7_with_preserves_fields.py`.

**N-8 (đã sửa, một phần rõ ràng) — `execute_once` giờ có caller ở CẢ hai mức.** `POST
/v1/runs`'s `Idempotency-Key` (T-9.2) dedupe một REQUEST KHỞI ĐỘNG RUN — "caller thật đầu
tiên" `idempotency.py` tự đặt điều kiện. `Dispatcher._invoke` (backend cổ điển) và
`lg/runtime.py::_run_tools` (backend durable) giờ là caller thứ hai, ở MỨC TOOL CALL:
`execute_once` bọc quanh chính lời gọi `spec.fn()`, khoá bằng
`idempotency_key(f"{run_id}:{step}", call_id)` — gộp thêm `step` vào nửa run_id của khoá
(không đổi chữ ký hàm `idempotency_key` chính nó, `tests/test_m6_t61_idempotency.py` khoá
sẵn hình dạng `f"{run_id}:{call_id}"`) vì `FakeModel.tool_call()`'s mặc định tiện dụng
(`call_id="c1"`) không duy nhất qua các bước, và hàng chục test có sẵn dựa vào việc đó vô
hại.

Đóng đúng nửa nào: một call `retryable=True` mà `spec.fn()` THÀNH CÔNG nhưng bước
NGAY SAU nó (json.dumps/`truncate`/kiểm taint) mới raise — trước bản vá, dispatch loop
coi cả lượt thử là lỗi và gọi lại TOÀN BỘ, kể cả `spec.fn()` đã chạy xong — giờ lượt thử
thứ hai là cache hit, `spec.fn()` không chạy lại; `tool.finished`'s `replayed=True` xác
nhận. `tests/test_n8_idempotent_tool_calls.py`: backend cổ điển ép lỗi thật (monkeypatch
`truncate` raise ở lần đầu) rồi kiểm side-effect chỉ ghi nhận đúng một lần; backend
durable là test white-box — gọi thẳng `Runtime._run_tools(state)` hai lần với CÙNG
`run_id`/`step`/`call_id` (hình dạng một node bị gọi lại đúng ở checkpoint TRƯỚC nó,
với `_pending` đã checkpoint không đổi) và kiểm side-effect cũng chỉ một lần.

Nửa KHÔNG đóng, và không tuyên bố đóng: một crash NGANG QUÁ TRÌNH (upstream đã nhận
side-effect, tiến trình chết trước khi `execute_once`'s `store.put()` kịp ghi) — cả hai
store đều in-memory (`Dispatcher.__init__`'s `InMemoryStore` mới mỗi run;
`Runtime._idem_for()`'s cache theo `run_id`, cùng dạng `_bus_cache`/`_policy_cache`,
R-4-an toàn vì không phải nguồn sự thật) nên chết cùng tiến trình. Nửa đó vẫn đứng nguyên
chỗ nó đã đứng: `docs/05-data-and-state.md §3`'s luật resume (`write`/`danger` không bao
giờ chạy lại khi resume) — S-4's mô tả gốc chính xác là kịch bản crash-across-process
này, và nó KHÔNG đóng bằng bản vá này, cũng không được tuyên bố đóng.

**N-9 (đã sửa) — `tenant_id` chưa bao giờ tới được `Policy.check()`.** `Agent.tenant_id`
chỉ từng chảy tới `EventBus` (telemetry), không bao giờ tới `RunContext`/`_Ctx` — một
`Policy` không đọc được nó. Nối `tenant_id` vào cả hai kiểu context, cả hai backend.
`tests/test_n9_tenant_in_context.py`. Bắt được khi chạy lại `tests/test_roadmap.py`
(bộ test tự đăng ký "định nghĩa xong" của M6-M10, viết trước khi các milestone tồn tại)
ngay trước lượt dọn tài liệu này — hai check ĐỎ khác của cùng file hoá ra chỉ đoán sai
TÊN (`ApprovalRecord`→`Decision`, `harness.testing`→`harness.eval`), sửa test chứ không
sửa code; `tenant_id` là gap chức năng thật duy nhất trong năm check ban đầu đỏ.

**N-10 (đã sửa) — "2 API interfaces" (backend cổ điển vs LangGraph) gây khó cho người
dùng.** Phản hồi trực tiếp từ người dùng, không phải phát hiện tự động: có hai backend
nghĩa là ai muốn durability phải học từ vựng LangChain (`HumanMessage`, `.invoke()`,
`thread_id` trong config) bên cạnh `Agent`. Đóng bằng `Agent(durable=True)`
(`docs/03-public-api.md §3.5`, `agent.py::_atry_run_durable`): CÙNG một `run`/`try_run`/
`arun`/`atry_run`/`with_`, chạy trên `harness.lg.build_agent()` ở dưới, không có gì thuộc
LangChain/LangGraph lọt ra ngoài. `ProviderChatModel` (`lg/adapter.py`) là seam làm được
điều đó mà không cần một thư viện gọi model thứ hai: nó bọc `provider=` CỦA CHÍNH Agent
đó (cùng `AnthropicProvider`/`FakeModel` backend cổ điển gọi) thành một LangChain chat
model, nên `durable=True` gọi model qua đúng một đường error-mapping/pricing, và qua
CÙNG `ContextAssembler` — đóng luôn một khoảng trống riêng của `build_agent()` (nó chưa
bao giờ tự gửi system prompt/`job=` tới model). `checkpoint=` mặc định là file SQLite cục
bộ, tự tạo, không cần hạ tầng ngoài (`_build_checkpointer`) — theo đúng trải nghiệm
`SqliteStore` đã có sẵn cho memory.

Ba khoảng trống MỚI, không âm thầm — mỗi cái raise `ConfigError` rõ ràng thay vì bỏ qua
lặng lẽ: `durable=True` + `.chat()`, + `.resume(transcript)`, + `on_delta=` đều bị từ
chối (checkpointer đã giữ lịch sử hội thoại, nên `.chat()`/`.resume()` là cơ chế trùng
việc; model call trong `_generate` chưa stream). `Session`/Service API (T-8.6/T-9.2) vẫn
CHỈ backend cổ điển — quyết định có mở rộng cho `durable=True` hay không CHƯA đưa ra,
ghi ở §7 dưới.

Một đánh đổi kiến trúc có chủ đích, không phải sơ suất: đồ thị được BIÊN DỊCH LẠI mỗi lần
gọi (`_build_durable_graph`), không cache trên `Agent` như `_asm`/`_watch`. Lý do:
`AsyncSqliteSaver` giữ một connection `aiosqlite` sở hữu một thread nền KHÔNG phải daemon
— một `Agent` durable cache đồ thị (và connection) suốt vòng đời sẽ khiến một script gọi
xong `run()` rồi thoát bình thường TREO LUÔN, thay vì return — bug thật, bắt được bằng
tay lúc build tính năng này (không phải lý thuyết: `tests/test_durable_agent.py::
DurableProcessExits` là regression test cho đúng bug đó, chạy script con qua
`subprocess` với timeout cứng). Mở/đóng connection quanh đúng một lần gọi — cùng hình
dạng `atry_run()` đã dùng cho `TranscriptWriter` của riêng nó — đổi lấy việc Agent này cư
xử giống mọi Agent khác trong thư viện: nó return. `tests/test_durable_agent.py` (14
test) và `tests/test_parity.py` (mở rộng thành BA call shape — loop, graph thô,
`durable=True`) phủ tính năng.

---

## 4. Ý tưởng có kiến trúc, chờ eval

Cắt khỏi đường đi bắt buộc theo luật §8.4, **không vứt đi**. Nếu có ngày đo được, đây là
chỗ lấy lại.

### Quarantine model (dual-LLM / CaMeL)

**Cái Microsoft làm đúng, và chỗ đặt sai.** `set_quarantine_client` cung cấp một model
riêng, rẻ hơn, để suy luận trên nội dung không tin cậy — mẫu dual-LLM / CaMeL, thứ
**không gói nào khác trong 28 gói Python + TypeScript có**
([§09](../research/09-memory-context-multiagent-hitl.md) §16bis). Ý tưởng đúng; chỗ đặt
sai: `_quarantine_chat_client` là biến **mức module**, gán qua `global`.

**Nó thuộc về đâu trong đời một `Run`.** Quarantine không phải cấu hình tiến trình — nó
là một seam của `Run`:

```python
@value
class RunConfig:
    quarantine: ModelProvider | None = None      # None = fail closed
    max_grant_ttl: timedelta = timedelta(hours=1)

@value
class Quarantined[T]:
    """Kết quả rút trích từ nội dung UNTRUSTED. Chỉ dữ liệu có schema, không văn xuôi."""
    value: T
    label: Label                # luôn Integrity.UNTRUSTED — không hạ được
    source_call_id: CallId
```

Bốn luật: (1) vòng đời = vòng đời `Run`, chốt lúc khởi tạo, không setter/`global`;
(2) chỉ dữ liệu có schema quay lại context chính — văn xuôi tự do không bao giờ merge
vào `messages` của run chính; (3) `Quarantined.label` không có API nào hạ được xuống
`TRUSTED`; (4) không cấu hình ⇒ `DENY`, không phải im lặng đi tiếp (đối lập Microsoft).
Chi phí model quarantine tính vào cùng `Ledger` của run — một cơ chế an toàn có ngân sách
riêng là một cơ chế an toàn không đếm được.

**Vì sao cắt.** Đúng MỘT cài đặt trong toàn nghiên cứu, `@experimental`, không wire vào
harness của chính nó, không concurrency-safe. Không có eval nào so sánh tỉ lệ
prompt-injection thành công có/không có nó ([review-kiss.md](review-kiss.md) K-1).

**Điều kiện lấy lại.** Một eval trên tập prompt-injection thật, đo tỉ lệ thành công có và
không có quarantine, trên cùng bộ tool. Chênh lệch không có ý nghĩa thống kê → giữ cắt.

---

## 5. Đánh đổi đã chọn, và cái giá của từng cái

| chọn | được | mất |
|---|---|---|
| Graph thay vì loop | durability, chứng minh được, vẽ được | phụ thuộc LangGraph; người dùng phải hiểu khái niệm node |
| Effect class suy ra 5 hành vi | tác giả tool khai **một** thứ; không guard viết tay per-tool | 4 lớp là thô — tool vừa đọc nhạy cảm vừa ghi không xếp gọn |
| Invariant trên đường bắt buộc, plugin cho policy | "không cài" không còn là mặc định không an toàn | plugin không chặn được permission check |
| `Decision` append-only | audit thật, thu hồi bằng `max()` | sổ chỉ lớn lên; chính sách lưu trữ chưa viết |
| Effect log luôn bật cho `write` | khuyết điểm #5 (`design/README.md`) sửa theo mặc định | một round-trip DB thêm mỗi `write` call — chưa benchmark |
| At-most-once thay vì exactly-once | trung thực về cái harness một mình làm được | người dùng muốn exactly-once phải có upstream nhận key |

---

## 6. Chưa đủ evidence — hợp nhất

- **Chi phí effect log** trên mỗi `write`. Không đo được từ source người khác.
- **`fingerprint` cho MCP stdio.** TLS SPKI pin đúng cho HTTP; không có tương đương hiển
  nhiên cho tiến trình con.
- **Biên checkpoint chính xác của LangGraph** giữa chừng một node.
- **Isolation đa tenant ở tầng store.**
- **Số bậc của trục confidentiality.** Hai bậc là suy luận, không phải kết quả đo.
- **TTL mặc định cho grant `danger`.** Không có bằng chứng về con số đúng.
- **Chính sách hết hạn memory.** Một memo `UNTRUSTED` sống mãi là rủi ro thật, nhưng
  không có bằng chứng về chính sách đúng.
- **Learning curve.** Không được đo trong nghiên cứu gốc; mọi tuyên bố DX ở đây dựa trên
  thứ đếm được, không dựa trên người dùng thật — SC-1b (`HARNESS.md`) vẫn mở vì lý do này.

---

## 7. Còn mở hôm nay — nói thẳng, không giấu

Hai mục, không hơn không kém (N-1/N-3/N-5/N-6/N-8 vừa đóng — xem `## 3`):

1. **`AuthEvidence` cho S-11** — mô hình xác thực người duyệt thật (chữ ký kênh,
   `channel_message_id`), chặn một callback CỐ TÌNH khai gian danh tính. Thiết kế riêng,
   chưa bắt đầu. Không bắt buộc cho v1.0 trừ khi deployment cần audit trail chịu được
   kiểm toán bên ngoài.
2. **S-9 phần re-pointing-nhãn** — cần `ServerIdentity`+`fingerprint`, hoãn tới khi quan
   sát được một lần re-pointing MCP thật (cùng lý do K-12).

**S-4 không còn nằm trong danh sách này** — không vì đóng hoàn toàn, mà vì phần còn lại
của nó không phải một việc CÒN PHẢI LÀM: N-8 (xem `## 3`) đóng đúng nửa engineering thật
sự mở (một call retry giữa lúc run đang chạy); nửa còn lại (crash NGANG QUÁ TRÌNH, giữa
lúc upstream nhận side-effect và checkpoint kịp ghi) là ranh giới đã CHẤP NHẬN, đã tài
liệu hoá từ trước (`docs/05-data-and-state.md §3`'s luật resume, chính là "ranh giới
trung thực của một giải pháp cấp thư viện" — exactly-once qua một lần crash cần một
durable execution engine, một stated non-goal), không phải một gap đang chờ code.

Không mục nào ở trên chặn v1.0 (xem `design/08-roadmap-and-release-plan.md §3` cho điều
kiện release) — mỗi mục đã có lý do hoãn cụ thể, không phải bị bỏ quên.
