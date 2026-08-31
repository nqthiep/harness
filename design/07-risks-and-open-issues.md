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

**Tất cả 58 phát hiện gốc đã được xét. 9 phát hiện mới (N-1…N-9) tự bắt được trong lúc
xây M6-M10.** Còn mở thật sự, hôm nay: **6 mục** — xem `## 7`.

### Bảo mật (S-1…S-29)

| Mã | Tóm tắt | Trạng thái |
|---|---|---|
| S-1 | Nén ngữ cảnh gọi model thiếu `reserve()` | Lỗi thời — cơ chế review mô tả không tồn tại |
| S-3 | `Secret[T]` lộ qua tool result | **Đã sửa**, cả hai nguồn (`Grants.sensitive` + `.reveal()`) |
| S-4 | Idempotency ở mức MỘT lời gọi tool | **Còn mở** — cơ chế (`execute_once`) đã xây nhưng chưa gắn vào `Dispatcher` (N-8) |
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
| N-1 | LangGraph không timeout per-tool | **Còn mở** |
| N-2 | `try_run()` raise thẳng khi `returns=` sai kiểu | **Đã sửa** |
| N-3 | LangGraph không hỗ trợ `returns=` | **Còn mở** |
| N-4 | Lỗi provider crash thẳng ra ngoài, cả hai backend | **Đã sửa** |
| N-5 | Retry cấp provider đã công bố nhưng chưa cài | **Còn mở** |
| N-6 | `model.response` VÀ `run.finished` thiếu trường tài liệu đã hứa | **Còn mở** |
| N-7 | `Agent.with_()` làm mất bốn trường, mọi lần gọi | **Đã sửa** |
| N-8 | `execute_once` có caller thật nhưng chưa gắn vào tool dispatch | **Còn mở** (= S-4) |
| N-9 | `tenant_id` chưa bao giờ tới được `Policy.check()` | **Đã sửa** |

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

**N-1 — LangGraph không timeout per-tool.** `lg/runtime.py::_run_tools` không có `async
with asyncio.timeout(...)` nào bọc quanh lời gọi tool — khác `dispatch.py::_invoke`
(Round 23). Một tool `read` treo mãi mãi (HTTP call không timeout riêng) treo cả node
graph vô thời hạn; chỉ wall-clock CẤP RUN chặn được, và chỉ kiểm đầu mỗi bước. Còn mở.

**N-2 (đã sửa) — `try_run()` raise thẳng khi model trả rác khớp sai `returns=`.**
`_parse_returns()` giờ chạy TRƯỚC khi `RUN_FINISHED` phát, bắt `ToolContractError` và hạ
xuống `Result(stop_reason=ERROR)` — model trả rác là một OUTCOME, không phải crash.

**N-3 — LangGraph không hỗ trợ `returns=`.** `build_agent()` không có tham số này;
`Result.value` luôn `None` trên backend đó. Khoảng trống parity thật, không có tài liệu
nào ghi nó là "chỉ backend cổ điển". Còn mở.

**N-4 (đã sửa) — lỗi provider crash thẳng ra ngoài, cả hai backend.** MỌI lần gọi
provider thật gặp rate limit/timeout tạm thời crash chương trình gọi nó — không có
`except` nào cho `ProviderError`/`ProviderTimeout`/`ProviderRateLimited` ở bất kỳ đâu
trong `src/harness/` trước bản vá. Nghiêm trọng hơn N-2: đây là đường đi PHỔ BIẾN nhất
khi chạy với provider thật. Cả hai backend giờ bắt và hạ xuống `Result(ERROR)`.

**N-5 — retry cấp provider đã công bố nhưng chưa cài.** `docs/10-observability-ops.md §3`
hứa `ProviderRateLimited`/`ProviderUnavailable`/`ProviderTimeout` đều tự động retry — N-4
chỉ biến lỗi thành `Result(ERROR)`, không tự retry gì. Cần thiết kế riêng (đọc
`Retry-After` từ đâu — `ProviderRateLimited` chưa mang trường đó). Còn mở.

**N-6 — `model.response` VÀ `run.finished` thiếu trường tài liệu đã hứa.**
`docs/05-data-and-state.md §1` hứa `model.response` mang `usage{in,out,cache_read,
cache_write}`/`latency_ms` — code chỉ emit `stop_reason`/`cost_usd`, khiến một phần
mapping của `OtelExporter` (T-8.3) không có dữ liệu để đọc. Phát hiện thêm khi soát tài
liệu lượt này (không phải lúc T-8.3 viết): `run.finished` có cùng khoảng lệch —
`docs/05` hứa `usage`/`duration_s`, code (`run.py`, `lg/runtime.py`) chỉ emit
`stop_reason`/`steps`/`cost_usd`/`tainted`. Cùng một lớp gap (usage/timing chưa wire vào
event emission), hai điểm emit. Còn mở.

**N-7 (đã sửa) — `Agent.with_()` làm mất bốn trường, MỌI lần gọi.**
`transcript`/`exporters`/`accepts_tainted`/`sensitive` biến mất khỏi agent phái sinh —
không phải lỗi riêng của tính năng nào đang xây, ảnh hưởng mọi caller của `with_()`.
`tests/test_n7_with_preserves_fields.py`.

**N-8 — `execute_once` có caller thật (T-9.2) nhưng vẫn KHÔNG đóng S-4.** `POST /v1/runs`'s
`Idempotency-Key` dedupe một REQUEST KHỞI ĐỘNG RUN — đúng "caller thật đầu tiên"
`idempotency.py` tự đặt điều kiện, chứng minh cơ chế dùng tốt. Nhưng đó là idempotency ở
MỨC RUN; S-4 cần MỨC TOOL CALL (một `write`/`danger` tool bị model/lỗi mạng gọi lại giữa
một run đang chạy) — `Dispatcher._invoke` chưa gắn `execute_once`. = S-4, còn mở.

**N-9 (đã sửa) — `tenant_id` chưa bao giờ tới được `Policy.check()`.** `Agent.tenant_id`
chỉ từng chảy tới `EventBus` (telemetry), không bao giờ tới `RunContext`/`_Ctx` — một
`Policy` không đọc được nó. Nối `tenant_id` vào cả hai kiểu context, cả hai backend.
`tests/test_n9_tenant_in_context.py`. Bắt được khi chạy lại `tests/test_roadmap.py`
(bộ test tự đăng ký "định nghĩa xong" của M6-M10, viết trước khi các milestone tồn tại)
ngay trước lượt dọn tài liệu này — hai check ĐỎ khác của cùng file hoá ra chỉ đoán sai
TÊN (`ApprovalRecord`→`Decision`, `harness.testing`→`harness.eval`), sửa test chứ không
sửa code; `tenant_id` là gap chức năng thật duy nhất trong năm check ban đầu đỏ.

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

Sáu mục, không hơn không kém:

1. **`AuthEvidence` cho S-11** — mô hình xác thực người duyệt thật (chữ ký kênh,
   `channel_message_id`), chặn một callback CỐ TÌNH khai gian danh tính. Thiết kế riêng,
   chưa bắt đầu. Không bắt buộc cho v1.0 trừ khi deployment cần audit trail chịu được
   kiểm toán bên ngoài.
2. **S-4 / N-8 — idempotency ở mức MỘT lời gọi tool.** `execute_once` (T-6.1) đã có caller
   thật ở mức RUN (T-9.2); gắn nó vào `Dispatcher._invoke` là việc tiếp theo tự nhiên
   nhất nếu có nhu cầu thật (một `write`/`danger` tool trên upstream không tự idempotent).
3. **S-9 phần re-pointing-nhãn** — cần `ServerIdentity`+`fingerprint`, hoãn tới khi quan
   sát được một lần re-pointing MCP thật (cùng lý do K-12).
4. **N-1 — LangGraph không timeout per-tool.**
5. **N-3 — LangGraph không hỗ trợ `returns=`.**
6. **N-5 / N-6 — retry cấp provider chưa cài; `model.response` thiếu `usage`/`latency_ms`.**

Không mục nào ở trên chặn v1.0 (xem `design/08-roadmap-and-release-plan.md §3` cho điều
kiện release) — mỗi mục đã có lý do hoãn cụ thể, không phải bị bỏ quên.
