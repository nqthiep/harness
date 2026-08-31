# 08 — Roadmap và release plan

Tệp này trả lời ba câu: **còn gì chưa xong**, **làm theo thứ tự nào**, và **cái gì phải
xong trước khi gắn nhãn "v1"**. Nó gộp hai backlog đang sống song song trong repo này —
`07-risks-and-open-issues.md` (phát hiện từ hai vòng review đối kháng, S-1…S-29/K-1…K-29)
và `docs/17-research-alignment.md` (khoảng trống so với nghiên cứu hợp nhất, M6…M10) —
thành một danh sách, vì tới hôm nay không tệp nào đọc chung cả hai.

---

## 0. Tình trạng hiện tại, tóm tắt trung thực

*(Cập nhật sau khi M6, M7, M8, và K-13's phần còn lại đều đã XONG — xem `## 3` cho việc còn
lại, `## 4`/`## 5` cho thứ tự và điều kiện release.)*

- **502 test xanh**, `ruff check .` sạch, mypy sạch (qua `test_conformance.py`), mọi
  `examples/*.py` chạy được — kiểm lại lần cuối cùng lượt K-13.
- **Toàn bộ 58 phát hiện của hai vòng review (S-1…S-29, K-1…K-29) đã được xét qua** —
  không có nghĩa "đã sửa hết". Phân loại thật:
  - **Đã sửa bằng code, có test + mutation test:** phần lớn S-2…S-29 còn lại
    (S-3/S-6/S-11/S-13/S-14/S-15/S-16/S-19/S-20/S-21/S-22/S-24/S-25/S-27/S-29), K-9.
  - **Đã kiểm, xác nhận lỗi thời hoặc đã đúng sẵn — không cần sửa:** S-1, S-5, S-6, S-7…
    S-10 (hoãn, không phải lỗi thời), S-12, S-15 (backend cổ điển), S-17 (hoãn), S-23,
    S-26, S-28, K-7, K-23. Tất cả đều được xác nhận **bằng cách đọc code hôm nay**, không
    phải đoán — nhiều mô tả gốc của review khớp với một bản nháp thiết kế đã bị thay bằng
    một cơ chế khác, đơn giản hơn, khi thật sự cài đặt.
  - **Đã sửa bằng tài liệu, không phải code (không có cách sửa ở tầng đang xét):** S-18,
    S-28's phần tài liệu.
  - **Sửa được một phần, phần còn lại cần thiết kế mới:** S-11 (kênh `Approval` xong,
    `AuthEvidence` thật thì chưa).
  - **CÒN SỐNG, chưa sửa:** S-4 (xem `## 2`) — `execute_once` (M6/T-6.1) nay có caller
    thật (M9/T-9.2), nhưng ở mức RUN, không phải mức TOOL CALL S-4 cần; xem `## 3.1`, N-8.
  - **S-7…S-10, S-17 — ĐÃ SỬA**, landing cùng `harness.mcp` (T-9.1, ADR-054). S-9's phần
    re-pointing-nhãn còn hoãn, cùng lý do K-12.
  - **K-13's va chạm `P-`/`I-` — ĐÃ SỬA XONG (hai lượt)**, xem `07 §1.3`. Không còn mục nào
    của K-13 ở trạng thái hoãn.
- **M6 (Reliability), M7 (Isolation), M8 (Observability) đều ĐÃ XONG** (4/4, 4/4, 6/6
  sub-task) — xem `## 3.2`. Bốn phát hiện phụ tự bắt lúc làm các milestone này vẫn CHƯA
  sửa, ghi lại làm N-series, độc lập với milestone đã đóng: **N-1** (LangGraph không có
  timeout per-tool), **N-3** (LangGraph không hỗ trợ `returns=`), **N-5** (retry cấp
  provider công bố ở docs/10 §3 nhưng chưa cài), **N-6** (`model.response` thiếu
  `usage`/`latency_ms`).
- **M9 — 2/3 sub-task xong (T-9.1 MCP client, T-9.2 Service API).** T-9.3 (canonical
  event adapter) chưa có dòng code nào. **M10 (Evaluation) chưa có DÒNG CODE NÀO** — kiểm
  lại bằng `grep` hôm nay, không phải bằng đọc lại tài liệu cũ (xem `## 3.2`). Đây là việc
  lớn còn lại trước khi có thể tự chấm lại theo `docs/17`.

---

## 1. S-20 — ĐÃ SỬA. `Budget(usd=None)` vòng qua "budget bắt buộc có trục tiền"

> **Trạng thái: ĐÃ SỬA, có test + mutation test, cả hai backend.** `EventKind.BUDGET_UNLIMITED`
> (`"budget.unlimited"`, ADR-041 ở `docs/12-decision-logs.md`) phát đúng một lần mỗi
> run/thread ngay sau `RUN_STARTED`, khi `budget.usd is None` — `src/harness/run.py`
> (classic loop) và `src/harness/lg/runtime.py::budget_gate` (LangGraph, cùng chỗ
> `RUN_STARTED` đã dùng để phát một lần mỗi thread, không phải một lần mỗi compiled
> graph). Khoá bằng `tests/test_attack_s20.py` (6 test, gồm một mutation test xác nhận bỏ
> nhánh check thì cảnh báo biến mất). `usd: Decimal | None` không đổi kiểu, construction
> không bị chặn — xem lý do bên dưới.

Kiểm S-20 khi soát lại toàn bộ danh sách cho tệp này (S-1…S-29 chưa từng được đối chiếu
đầy đủ với code cho tới hôm nay) và thấy nó **vẫn còn sống, y hệt mô tả gốc** (trước khi
sửa):

```python
>>> from harness import Agent
>>> from harness.budget.ledger import Budget
>>> from harness.models.fake import FakeModel
>>> b = Budget(usd=None, steps=100, wall_clock_s=3600)     # KHÔNG qua Budget.parse()
>>> Agent(name="A", job="j", model="claude-opus-5", provider=FakeModel([]), budget=b)
<Agent ...>          # ← không lỗi nào. Dựng được.
```

`Budget.usd: Decimal | None` (`budget/ledger.py`) vẫn cho `None`. Việc bắt buộc "phải có
trục tiền" chỉ nằm trong `Budget.parse()` — **con đường qua chuỗi** (`budget="$0.05"`).
Không gì ngăn người dùng dựng `Budget(...)` trực tiếp với `usd=None` rồi truyền thẳng cho
`Agent(budget=...)`. Hậu quả đúng như S-20 mô tả:

- `Ledger.size_call()`: `if self._b.usd is None ...: return model_max` — không trần nào.
- `Ledger.reserve()`: cả hai nhánh kiểm ngân sách đều `if self._b.usd is not None and
  ...` — `usd=None` ⇒ không nhánh nào chạy, `reserve()` không bao giờ raise
  `BudgetExceeded`.

Với một provider THẬT (không phải `FakeModel`), đây là "khuyết điểm #4 của README"
(loop limit mà không có spend ceiling) đạt được bằng một keyword argument, đúng câu review
gốc dùng.

**Vì sao `usd=None` tồn tại — không xoá mù quáng.** `size_call()`'s comment nói rõ: "A
free provider (FakeModel, a local model) has nothing to divide by and nothing to spend"
— Round 24 cần trường hợp này để không phá vỡ testing free của SC-5. `usd=None` không
phải lỗi đánh máy; nó phục vụ một ca dùng thật (`FakeModel`/model local không tính phí).
Vấn đề không phải giá trị `None` tồn tại — là **không có gì phân biệt "tôi cố ý dùng
provider miễn phí" với "tôi (hoặc một dependency) vô tình bỏ trục tiền."**

**SỬA LẠI đề xuất ban đầu ở đây — không phải hard-reject.** Bản nháp đầu của mục này từng
đề xuất bắt `Agent.__init__`/`build_agent()` từ chối construction khi `budget.usd is
None`. Kiểm lại với thiết kế đã CÔNG BỐ trước khi viết code thì thấy đề xuất đó **sai** —
`docs/04-interfaces.md:451-452` và `docs/07-cost.md:74-75` đã quyết định rõ, bằng câu chữ:
"`Budget(usd=None)` is permitted but requires passing `None` explicitly, and emits a
`budget.unlimited` warning event on every run. Unlimited is possible; it is not silent."
Đây là chủ đích, không phải sơ sót: `usd=None` là escape hatch tường minh (đúng ca dùng
`FakeModel`/model local ở trên), Poka-Yoke kiểu "làm cho thấy được, không làm cho không
thể" — nhất quán với phần còn lại của thiết kế (vd. S-11's `Approval` ghi lại thay vì cấm).

**Sửa tối thiểu đúng theo hợp đồng đã công bố:** không đổi kiểu `usd: Decimal | None`,
không chặn construction. Thêm cơ chế phát sự kiện cảnh báo `budget.unlimited` — một
`EventKind` mới, phát đúng một lần mỗi run/thread khi `budget.usd is None`, ở cả hai
backend (classic loop và LangGraph). Cơ chế này **được tài liệu hứa nhưng chưa từng được
xây** — `grep "unlimited\|UNLIMITED"` trên toàn bộ `src/harness/` chỉ ra đúng MỘT chỗ
(`run.py:141`), và đó chỉ là một f-string hiển thị, không phải event. Đây chính là lỗi
S-20 thật sự: không phải "có thể unlimited" (được phép, có chủ đích) mà là "unlimited
nhưng im lặng" — đúng câu tài liệu tự phủ định: "Unlimited is possible; it is not silent."

**Đây là lý do nó được làm TRƯỚC bất kỳ mục nào khác trong roadmap** — nó phá đúng lời
hứa trung tâm nhất của cả thiết kế ("budget bắt buộc, không mặc định, không unlimited") và
là phát hiện "chặn phát hành" duy nhất còn sống — nay đã sửa, xem callout trạng thái ở
đầu mục này.

---

## 2. Ba mục cần ghi vào `07-risks-and-open-issues.md`, chưa từng được đối chiếu

Khi soát lại S-1…S-29 đầy đủ cho tệp này, ba mã sau **chưa từng xuất hiện** trong
`07-risks-and-open-issues.md` — không phải vì đã đóng, mà vì chưa ai đối chiếu chúng với
code kể từ khi các cơ chế liên quan được xây (hoặc không được xây):

- **S-1 — đã lỗi thời, cả hai nhánh.** Kịch bản (a) dựa vào `Quarantine`, thứ K-1 đã cắt
  (`0 caller`, đã ghi ở `07 §1.1`). Kịch bản (b) dựa vào chiến lược nén `SummarizeOldPrefix`
  (gọi model để tóm tắt) — nhưng chiến lược nén THẬT SỰ được xây là `ClearToolResults`
  (`context/window.py`, hằng số `CLEARED`) — xoá nội dung, không gọi model nào cả. Không
  có lời gọi model nào ngoài node `model` để mà thiếu `reserve()`.
- **S-4 — chưa lỗi thời, nhưng chưa áp dụng được, vì cơ chế nó bàn chưa tồn tại.** Toàn bộ
  giao thức idempotency ba pha (`IdempotencyMode`, `in_flight`, `call_with_effect_log`)
  **không có trong `src/harness/`** — đúng khoảng trống mà `docs/17-research-alignment.md`
  M6/T-6.1 đã ghi nhận và xếp lịch xây (`S-01` trong `§17.2.2`). S-4 phải được RE-VERIFY
  khi M6 build idempotency — không đóng bây giờ, cũng không phải việc làm ngay.
- **S-5 — đã lỗi thời, và may mắn theo hướng an toàn.** Cơ chế `Provenance`/nhãn-theo-bản-ghi
  mà S-5 phê phán (dữ liệu trong store tự khai `label`, có thể bị giả mạo) **không được
  xây**. Cơ chế THẬT (`memory/viking.py::tools()`) đơn giản hơn nhiều và tình cờ đúng
  chính "sửa tối thiểu (a)" mà S-5 đề xuất: `recall` khai `effect="external"` — nhãn
  UNTRUSTED áp dụng qua ĐÚNG con đường chung mọi tool `external` đi qua
  (`emits_of`/`check_flow`), không có ngoại lệ "tin provenance" nào để mà giả mạo.

Việc cần làm: thêm ba callout ngắn vào `07 §1.1` (mẫu giống S-1/S-12/S-23/S-26 đã có),
không cần code hay test mới — cả ba đã "đóng" theo đúng nghĩa "đã kiểm, lỗi thời."

---

## 3. Danh sách việc CHƯA hoàn thành, gộp hai backlog

### 3.1 Bảo mật/KISS còn mở (từ `07-risks-and-open-issues.md`)

| Mã | Việc | Vì sao chưa làm | Phụ thuộc |
|---|---|---|---|
| ~~**S-20**~~ | ~~`Budget(usd=None)` bypass~~ | **ĐÃ SỬA** — xem `## 1` | — |
| **S-4** | Idempotency key thật CHO MỘT LỜI GỌI TOOL (bảo vệ `write`/`danger` khỏi chạy lại khi model/client retry) | `execute_once` (T-6.1) tồn tại và nay có CALLER thật (T-9.2, ở mức RUN qua `Idempotency-Key` header) — nhưng chưa gắn vào `Dispatcher._invoke`, nơi S-4 thật sự cần nó. Xem N-8, `07 §1.5` | Wiring vào dispatch path — chưa lên lịch |
| ~~**S-7, S-8, S-10**~~ | ~~`ServerIdentity`(v1: `ServerLabel`)/hint-hạ-effect/`proposed_scope`(≈`accepts_tainted` policy-only) cho MCP~~ | **ĐÃ SỬA** — `harness.mcp` (T-9.1, ADR-054) | — |
| **S-9 (một phần)** | `Scope.server` chặn va chạm HAI nhãn khác nhau (ĐÃ SỬA); re-pointing MỘT nhãn sang endpoint khác thì chưa (cần `ServerIdentity`+`fingerprint`) | K-12: định dạng `fingerprint` chưa chốt cho MCP stdio | Chờ bằng chứng re-pointing thật |
| ~~**S-17**~~ | ~~Injection qua `description` tool MCP trước lời gọi đầu~~ | **ĐÃ SỬA** — bằng tài liệu (docstring `classify_mcp_tool`) + `default_effect=DANGER` fail-closed, cùng lý do S-18 | — |
| ~~**S-23**~~ | ~~`call_key` domain separator cho idempotency~~ | **ĐÃ KIỂM, LỖI THỜI** — `call_key = blake2b(...)` review mô tả không tồn tại trong code thật, xem `07 §1` | — |
| **S-11 (phần còn lại)** | `AuthEvidence` — xác thực người duyệt thật, không chỉ tự khai | Cần mô hình xác thực riêng, chưa thiết kế | Độc lập, ưu tiên theo nhu cầu deployment thật |
| ~~**K-13 (phần còn lại)**~~ | ~~Va chạm namespace `P-`/`I-` giữa `01`/`02`/`docs/09` và `04`/`05`~~ | **ĐÃ SỬA** — `01`'s `P-3`→`PLUG-1`, `02`'s `P-1`/`P-3`/`P-4`→`POL-1`/`POL-3`/`POL-4` (`P-2` giữ nguyên, không va chạm thật), `03`'s `I-1` gốc (idempotency)→`IDEM-1`, `04`'s "bất biến thay thế I-1/I-2" bỏ chữ "thay thế", chú thích chéo tới `docs/02 §2.3`; sửa kéo theo `src/harness/policy/builtin.py` + `design/06`. Xem `07 §1.3` | — |

### 3.2 Tính năng/trưởng thành còn thiếu (từ `docs/17-research-alignment.md`, M6…M10)

Đối chiếu lại bằng `grep` hôm nay (không phải đọc lại `§17`, theo đúng luật "kiểm bằng
chạy code" mà chính tệp đó đặt ra) — **không mục nào dưới đây có bất kỳ dòng code nào
trong `src/harness/`:**

| Milestone | Trọng số × khoảng trống | Việc chính | Trạng thái hôm nay |
|---|---|---|---|
| **M6 — Reliability** | 12% × gap 2/5 | Idempotency key, cancellation đúng chuẩn (không nuốt `CancelledError`), retry theo effect class, failure injection | **XONG (4/4 sub-task, theo đúng nghĩa "Done" mỗi task tự đặt ra).** T-6.2 (cancellation) — `tests/test_m6_t62_cancellation.py`. T-6.3 (retry theo effect class, cả hai backend) — `tests/test_m6_t63_retry.py`, 8 test. T-6.1 (idempotency) — contract `execute_once` xong và khoá bằng `tests/test_m6_t61_idempotency.py` (8 test), CHỦ ĐÍCH chưa gắn vào `Agent`/`Dispatcher`/`Runtime` (ADR-043 — không có caller thật cho tới M9). T-6.4 (chaos) — `harness.testing.chaos` (5 kịch bản: provider timeout, tool raise, store chết, policy raise, model trả rác) + `tests/test_m6_t64_chaos.py` (9 test); viết kịch bản chaos lộ ra **hai lỗi thật, cả hai đã sửa ngay trong lượt này** — N-2 (`try_run()` raise `ToolContractError` không bắt khi `returns=` sai) và **N-4 (nghiêm trọng hơn): lỗi provider — timeout, rate limit — crash thẳng ra ngoài `try_run()`/`graph.invoke()`, KHÔNG có `except` nào bắt ở CẢ HAI backend, trước bản vá này** (ADR-044, `docs/12`). Hai phát hiện phụ ghi lại, CHƯA sửa (ngoài phạm vi M6): **N-1** — LangGraph không có timeout per-tool; **N-3** — LangGraph không hỗ trợ `returns=` (`Result.value` luôn `None`). |
| **M7 — Isolation** | 15% × gap — **nặng ký nhất theo trọng số nghiên cứu** | Workspace root, egress mặc định CHẶN (đảo `allowed_hosts=None` từ "cho tất cả" sang "chặn tất cả" — breaking change), seam `Sandbox`, secret không vào sandbox | **XONG (4/4 sub-task).** T-7.1 (workspace root) — `workspace.py::confine`, `tests/test_m7_t71_workspace.py` (14 test, ADR-045). T-7.2 (egress mặc định chặn, breaking change có chủ đích) — `allowed_hosts` mặc định `None`→`()`, `None` tường minh vẫn escape hatch, `tests/test_m7_t72_egress_default.py` (6 test), ADR-046 giải thích vì sao không làm chu kỳ deprecation nhiều phiên bản. T-7.3+T-7.4 (seam `Sandbox`, secret không vào sandbox) — `sandbox.py`: `Protocol Sandbox`, hai cài đặt `InProcess`/`Subprocess` (env sạch, không kế thừa `os.environ`, `Secret` object bị từ chối tường minh), seam THỨ SÁU đạt phép thử 3 phần `§02.4` (bảng ở `docs/02-architecture.md` đã cập nhật), ADR-047. KHÔNG gắn vào `Agent`/`dispatch.py` — chủ đích, cùng lý do T-6.1 (ADR-043): chưa có tool nào trong codebase cần chạy shell command, gắn dây bây giờ là surface không ai dùng. Khoá bằng `tests/test_m7_t73_t74_sandbox.py` (14 test, gồm bản cài đặt bên thứ ba đúng khuôn `proof.py §I.1`, cộng red-team test secret-không-lộ, mutation-tested). |
| **M8 — Observability** | 10% × gap | Envelope v1 (`schema_version`/`trace_id`/`tenant_id`), `Approval` là bản ghi đầy đủ, OTel exporter thật, cost-per-successful-task, event stream tiêu thụ được, `Session` resource | **XONG (6/6 sub-task).** T-8.1 (envelope v1) ĐÃ XONG — ADR-048, `tests/test_m8_t81_envelope.py` (12 test). T-8.2 (Approval là bản ghi) ĐÃ XONG — chỉ thiếu `policy_version`, hai tiêu chí "Failure"/"Test" T-8.2 tự đặt hoá ra đã đúng sẵn, ADR-049, `tests/test_m8_t82_approval_record.py` (7 test). Sẵn tiện sửa lỗi tài liệu: `docs/04` gọi nhầm `Ruling` là `Decision`. T-8.3 (OTel exporter) ĐÃ XONG — `observe/otel.py::OtelExporter`, đọc `docs/10-observability-ops.md §2` (mapping đã công bố sẵn TRƯỚC bản nháp đầu, phải sửa lại theo đúng bảng đó) thay vì tự bịa tên span/attribute; xử lý đúng thứ tự `policy.decided` phát TRƯỚC `tool.started` bằng buffer-rồi-flush; hai lỗi thật tự bắt (parse `$`-prefixed `cost_usd`, một xung đột tên biến làm mypy từ chối sai); metrics thật (`harness.run.cost`, `.steps`, `harness.tool.duration`, `.cache.hit_ratio`, `harness.policy.denials`) qua OTel Meter thật. ADR-050. `tests/test_m8_t83_otel.py` (16 test, SDK OTel thật không mock, cả hai backend, mutation-tested). Hai phát hiện phụ ghi lại khi đọc docs/10, CHƯA sửa: **N-5** — retry cấp provider (rate limit/timeout) đã công bố ở docs/10 §3 nhưng chưa cài; **N-6** — event `model.response` thiếu `usage`/`latency_ms` so với `docs/05` tự hứa, khiến một phần attribute/metric của chính OTel exporter không có dữ liệu để đọc. T-8.4 (cost-per-success) ĐÃ XONG — `harness.eval.cost_per_success(runs)` (gói mới `harness/eval/`, chuẩn bị chỗ cho M10's `harness.eval.*`), `total_cost / P(thành công)`, khoảng tin cậy Wilson-scored (không phải xấp xỉ chuẩn — sai ở n nhỏ/tỉ lệ cực đoan, đúng miền một golden set nhỏ hay gặp), `cost_per_success_usd=None` (không phải 0 hay inf) khi 0 thành công. ADR-051, `tests/test_m8_t84_cost_per_success.py` (12 test, mutation-tested). T-8.5 (event stream) ĐÃ XONG — `Agent.stream(message, on_delta=None)`, async generator yield `Event` thật (16 kind, envelope v1 đầy đủ) qua một exporter riêng dùng `with_()` gắn thêm (không mutate, ADR-004); `on_delta=` giữ nguyên là cơ chế delta text riêng, không gộp vào stream; mọi phân biệt T-8.5 đòi (tool-call/tool result/approval/retry/cancellation/final) đã có sẵn trên taxonomy hiện tại, không cần kind mới. ADR-052. **Phát hiện phụ khi viết test transcript cho `stream()`, ĐÃ SỬA NGAY: N-7** — `Agent.with_()` âm thầm làm mất `transcript`/`exporters`/`accepts_tainted`/`sensitive` ở MỌI lời gọi (không phải lỗi riêng của `stream()` — bất kỳ ai gọi `with_()` cũng gặp). `tests/test_m8_t85_stream.py` (8 test) + `tests/test_n7_with_preserves_fields.py` (7 test), cả hai mutation-tested. T-8.6 (`Session` resource) ĐÃ XONG — `harness/session.py::Session` bọc `Chat` (không xây lại state isolation Round 37 đã có), id/owner/TTL/`fork()`/`resume_from()` (bọc `Agent.resume()` có sẵn, không phải resume phong phú hơn) + `threading.Lock` cho ranh giới đồng thời (không phải `asyncio.Lock` — khớp `Chat.say()` vốn đồng bộ). Chủ đích CHỈ cho backend cổ điển — LangGraph's `thread_id` đã là session primitive của nó (T-8.1). Test đua tất định (không dựa timing may rủi) chứng minh khoá thật sự cần thiết. ADR-053, `tests/test_m8_t86_session.py` (13 test, mutation-tested). **M8 hoàn thành 6/6 sub-task.** |
| **M9 — Integration** | 8% × gap 3/5 — **khoảng trống lớn nhất theo tự chấm** | MCP client làm tool boundary, Service API (`POST /v1/runs`...), canonical event adapter | **Đang làm (2/3 sub-task).** T-9.1 (MCP client) ĐÃ XONG — `harness/mcp/` (extra, `mcp>=1.9`): `McpServerPolicy`, `classify_mcp_tool()` (M-1..M-3, `design/03 §5.3`, bug-for-bug fail-closed hint mapping chép từ Microsoft), `connect()` (gọi `tools/list` đúng MỘT lần — §5.4's rug-pull-chốt-lúc-bind). `ToolSpec.server` (mới) và `Scope.server` (đã có, chưa ai gọi) giờ tham gia `DecisionLog.lookup()` — grant không rò giữa hai server. Đóng CÙNG LÚC S-7, S-8, S-10, S-17 và một phần S-9 (xem `## 3.1`). ADR-054, `tests/test_m9_t91_mcp.py` (32 test, mutation-tested). T-9.2 (Service API) ĐÃ XONG — `harness/server/` (extra, `starlette`, ASGI — operator mang ASGI server riêng): `POST /v1/runs` (idempotency-key header → `execute_once`, T-6.1's caller thật đầu tiên, ở MỨC RUN), `GET /v1/runs/{id}` (status/result/pending approvals), `GET /v1/runs/{id}/events` (SSE, backlog + tail, `redact()` chạy đúng task của run — RT-13), `POST .../cancel`, `POST .../approvals/{call_id}` (cầu nối `approve=` qua một `asyncio.Future` một request HTTP resolve). Backend cổ điển only (cùng phạm vi `Session`/ADR-053); không auth (operator tự thêm); không `resume` (cần Store-backed registry chưa xây, giống lý do T-6.1/`Sandbox` chưa gắn dây). ADR-055, `tests/test_m9_t92_service_api.py` (21 test, mutation-tested). **S-4 CHƯA đóng dù có caller thật** — xem đoạn "M9" ở `## 4` và N-8 (`07 §1.5`): idempotency ở mức run ≠ idempotency ở mức tool call, `Dispatcher._invoke` chưa gắn `execute_once`. T-9.3 (canonical event/multi-transport adapter) CHƯA làm. |
| **M10 — Evaluation** | 12% (phần Testability còn thiếu) | Trajectory contract khai báo được, golden set + pass rate có khoảng tin cậy, benchmark p50/p95/throughput | Không có. |

**Điểm tự chấm hiện tại (docs/17 §1): 67.8/100**, thấp nhất ở Integration (2/5),
Performance (2/5), Ecosystem (1/5). Không có gì trong nhóm 1 (S-11…S-29) hay lượt vừa rồi
làm dịch chuyển con số này — chúng đóng LỖ HỔNG trong cơ chế đã có, không THÊM cơ chế mới.
M6…M10 là trục hoàn toàn khác, đo bằng tính năng chưa tồn tại.

### 3.3 Ý tưởng có kiến trúc, cắt có chủ ý — không phải backlog

`07-risks-and-open-issues.md §2` (Quarantine model / dual-LLM, CaMeL) là ý tưởng bị cắt
khỏi đường đi bắt buộc theo luật §8.4, **không phải việc đang chờ**. Chỉ lấy lại nếu có
ngày đo được nhu cầu thật — không đưa vào roadmap dưới đây.

---

## 4. Roadmap — thứ tự đề xuất, và vì sao

```
S-20 ──► M6 ──► M7 ──► M8 ──► K-13 ──► M9 ──► M10
(ĐÃ XONG)  Reliability  Isolation    Observ.   (ĐÃ XONG)  Integration   Eval
           + S-4/S-23   + M7 đảo     6/6                  + S-7…S-10    + trajectory
           (idempotency) egress-deny sub-task             /S-17/S-23-  contract
                                                            MCP-part
```

**S-20 trước tất cả — ĐÃ XONG.** Không phụ thuộc gì, sửa nhanh (một sự kiện cảnh báo, đúng
hợp đồng đã công bố ở `docs/04`/`docs/07`, không phải một guard chặn construction), và là
phát hiện "chặn phát hành" DUY NHẤT còn sống trong toàn bộ 58 phát hiện — để một lỗ hổng
như vậy tồn tại trong lúc lên kế hoạch cho M6…M10 sẽ là sai thứ tự ưu tiên; nay không còn
là vấn đề, roadmap tiếp tục từ M6.

**M6 trước M7.** Đúng lý do `docs/17 §5` đã ghi: idempotency là điều kiện tiên quyết cho
retry, cho Service API (idempotency key trong header — M9), và cho M10's contract "retry
không nhân đôi side effect." **Sửa lại một overclaim ở đây** (bắt lúc soát T-9.2): M6 xây
`execute_once` XONG nhưng CHỦ ĐÍCH chưa gắn vào `Dispatcher`/`Runtime` (ADR-043 — "không có
caller thật cho tới M9"), nên câu gốc "Landing M6 cũng đóng được S-4 và S-23 LUÔN" là sai —
S-23 đã tự lỗi thời từ trước, không liên quan gì tới M6 (xem `## 3.1`); S-4 (bảo vệ MỘT
lời gọi tool `write`/`danger` khỏi chạy lại) vẫn CHƯA đóng ngay cả sau T-9.2 — xem đoạn M9
bên dưới và N-8 (`07 §1.5`).

**M7 trước M9.** Mở MCP ra (một hệ sinh thái tool bên thứ ba không đáng tin) trước khi có
Isolation là mở rộng bề mặt tấn công trước khi dựng tường — cảnh báo này đã có sẵn trong
`docs/17`. M7 cũng có trọng số × khoảng trống CAO NHẤT (15% × gap) trong toàn bộ M6…M10.

**K-13's phần còn lại chen vào trước M9 — ĐÃ XONG.** Không phụ thuộc M6/M7, làm trước khi
MCP tới đúng như dự tính — MCP sẽ cần đặt tên invariant mới của riêng nó (rug-pull, re-list)
và dọn namespace trước tránh chồng thêm một namespace thứ ba lên hai cái đã va chạm. Xem
`07 §1.3` cho chi tiết từng số đổi.

**M9 đóng được S-7…S-10, S-17 CÙNG LÚC — ĐÃ XONG (T-9.1).** Đây là lý do năm phát hiện đó
bị hoãn thay vì bị vá non — landing `harness.mcp` (MCP client làm tool boundary, `ServerLabel`
v1, phân loại `effect` bắt buộc cho tool MCP, `Scope.server` tham gia grant matching) chính
là bản sửa của cả năm, không phải năm bản vá rời rạc sau đó.

**T-9.2 (Service API) — ĐÃ XONG, và là caller THẬT ĐẦU TIÊN của `execute_once`** —
đúng như `idempotency.py`'s docstring tự đặt điều kiện ("M9's Service API is the first
real source of one"). Nhưng ở mức RUN (`POST /v1/runs`'s `Idempotency-Key` header dedupe
một request khởi động run), không phải mức TOOL CALL bên trong `Dispatcher._invoke` —
đó mới là nơi S-4 thật sự cần. Landing T-9.2 CHỨNG MINH `execute_once` dùng được với một
caller thật (không còn là "hạ tầng không ai gọi"), nhưng KHÔNG tự động đóng S-4 — ghi lại
là N-8 (`07 §1.5`), việc còn lại (gắn `execute_once` vào đường dispatch tool call) chưa
lên lịch trong roadmap này.

**M10 sau cùng**, đúng thứ tự `docs/17` đã lập luận: trajectory contract cần một bề mặt đã
ổn định (MCP, idempotency, sandbox) để viết `must_call`/`must_not_call` có ý nghĩa.

**Ràng buộc xuyên suốt, nhắc lại từ `docs/17 §5`:** core vẫn 3 dependency, import dưới 100
ms. Mọi thứ ở M6 trở đi là `extra`, và phép thử ranh giới plugin (`02-architecture.md §2.4`)
áp cho từng seam mới — một mục không qua được phép thử đó thì không được vào.

---

## 5. Release plan

### v0.9 — "budget thật, an toàn thật" — ĐẠT (điều kiện: hết `## 1`)

S-20 đã sửa (`EventKind.BUDGET_UNLIMITED`, ADR-041). Đây là điều kiện TỐI THIỂU để bất kỳ
ai gọi đây là "sẵn sàng cho môi trường thật" — một thư viện tuyên bố "budget bắt buộc" mà
một keyword argument vòng qua được, im lặng, thì chưa xứng đáng bất kỳ nhãn phiên bản nào
cao hơn 0.x. Nay `usd=None` vẫn được phép (chủ đích, đúng hợp đồng công bố) nhưng không
còn im lặng.

### v1.0 — "an toàn cho một quy trình, không phải một hạm đội"

Điều kiện, theo đúng `docs/17 §6`'s tiêu chí hoàn thành đã đặt ra (không viết lại):

- Tự chấm lại theo ma trận `§17 §1` đạt **≥ 85**, với Reliability, Observability,
  Integration đều **≥ 4/5** — nghĩa là **M6, M8 (đủ để đạt 4/5), M9 phải xong**, M7 gần
  như chắc chắn cũng phải xong vì Integration mà không có Isolation là câu chuyện cảnh báo
  chính `docs/17` tự kể (W-03, "approval bị nhầm là isolation").
- 14 mục `S-01…S-14` ở `docs/17 §2.2` (điểm mạnh cần học, chưa có) đều có một test đang
  chạy chứng minh chúng tồn tại — không phải chỉ nằm trong bảng.
- `07-risks-and-open-issues.md`'s S-1…S-29 không còn mục nào ở trạng thái "hoãn chờ hạ
  tầng" — vì tới v1.0, hạ tầng đó (MCP, idempotency) đã tồn tại, nên S-7…S-10/S-17/S-4/
  S-23 hoặc đã tự đóng (đúng cách, qua M6/M9) hoặc lộ ra là VẪN sống trên hạ tầng mới và
  cần một bản vá riêng — cả hai đều phải được xác nhận, không được để mặc định là "chắc
  ổn."
- K-13's va chạm namespace đã dọn (không phụ thuộc gì khác, chỉ là kỷ luật) — **ĐÃ XONG**.
- `AuthEvidence` cho S-11: **không phải điều kiện bắt buộc cho v1.0** trừ khi deployment
  mục tiêu cần audit trail chịu được kiểm toán bên ngoài — ghi rõ trong changelog là giới
  hạn đã biết, theo đúng luật §45 ("thà nói 'chưa đủ evidence' còn hơn đoán") thay vì âm
  thầm để đó.

**Pilot bắt buộc trước khi gắn nhãn v1.0**, đúng câu `docs/17` đóng lại: *"Quyết định
cuối cùng cần một pilot 2–4 tuần có cùng model, cùng task set, cùng tool set và cùng
security policy."* Không con số tự chấm nào ở trên thay thế được việc đó — M10's golden
set là công cụ ĐO pilot đó, không phải thứ thay thế nó.

### v1.x — mở rộng theo nhu cầu đo được, không theo lịch

M10 (evaluation) là ranh giới v1.0; mọi thứ SAU M10 — thêm connector, thêm exporter, thêm
định dạng Store — nên chờ bằng chứng sử dụng thật (đúng luật §8.4 "cắt khỏi đường đi bắt
buộc, không vứt đi" đã áp dụng nhất quán suốt tệp này), không phải một v1.1 định sẵn ngày.
`AuthEvidence` (S-11) là ứng viên tự nhiên nhất cho v1.x đầu tiên nếu pilot cho thấy audit
trail cần chịu được kiểm toán bên ngoài.

---

## 6. Việc cần làm ngay, theo thứ tự (tóm tắt điều hành)

1. ~~**S-20**~~ — **ĐÃ SỬA** (`## 1`). Nhỏ, không phụ thuộc, chặn phát hành — không còn
   chặn.
2. ~~**Ghi S-1/S-4/S-5 vào `07-risks-and-open-issues.md`**~~ — **ĐÃ GHI** (`## 2`).
3. **M6 (Reliability) trở đi — được lệnh tiến hành, không còn là điểm chờ quyết định.**
   Người vận hành dự án đã quyết định: thực hiện toàn bộ roadmap này (`M6 → M7 → M8 →
   K-13 → M9 → M10`) cho đến khi hoàn thành. Đây là XÂY TÍNH NĂNG MỚI (idempotency,
   sandbox, MCP, Service API, eval harness), khác về bản chất công việc so với "sửa lỗi
   trong code có sẵn" của giai đoạn S-1…S-29 — nhưng không còn là một nhánh rẽ cần hỏi lại
   giữa chừng cho từng milestone.
