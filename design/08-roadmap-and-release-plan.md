# 08 — Roadmap và release plan

Tệp này trả lời ba câu: **còn gì chưa xong**, **làm theo thứ tự nào**, và **cái gì phải
xong trước khi gắn nhãn "v1"**. Nó gộp hai backlog đang sống song song trong repo này —
`07-risks-and-open-issues.md` (phát hiện từ hai vòng review đối kháng, S-1…S-29/K-1…K-29)
và `docs/17-research-alignment.md` (khoảng trống so với nghiên cứu hợp nhất, M6…M10) —
thành một danh sách, vì tới hôm nay không tệp nào đọc chung cả hai.

---

## 0. Tình trạng hiện tại, tóm tắt trung thực

- **359 test xanh**, ruff/mypy sạch trên mọi tệp đã đụng, mọi `examples/*.py` chạy được.
- **Toàn bộ 58 phát hiện của hai vòng review (S-1…S-29, K-1…K-29) đã được xét qua** —
  không có nghĩa "đã sửa hết". Phân loại thật:
  - **Đã sửa bằng code, có test + mutation test:** phần lớn S-2…S-29 còn lại
    (S-3/S-6/S-11/S-13/S-14/S-15/S-16/S-19/S-21/S-22/S-24/S-25/S-27/S-29), K-9.
  - **Đã kiểm, xác nhận lỗi thời hoặc đã đúng sẵn — không cần sửa:** S-1, S-5, S-6, S-7…
    S-10 (hoãn, không phải lỗi thời), S-12, S-15 (backend cổ điển), S-17 (hoãn), S-23,
    S-26, S-28, K-7, K-23. Tất cả đều được xác nhận **bằng cách đọc code hôm nay**, không
    phải đoán — nhiều mô tả gốc của review khớp với một bản nháp thiết kế đã bị thay bằng
    một cơ chế khác, đơn giản hơn, khi thật sự cài đặt.
  - **Đã sửa bằng tài liệu, không phải code (không có cách sửa ở tầng đang xét):** S-18,
    S-28's phần tài liệu.
  - **Sửa được một phần, phần còn lại cần thiết kế mới:** S-11 (kênh `Approval` xong,
    `AuthEvidence` thật thì chưa).
  - **CÒN SỐNG, chưa sửa — xem `## 1`:** S-20, S-4 (xem `## 2`).
  - **Hoãn có chủ ý, chờ hạ tầng chưa tồn tại:** S-7…S-10, S-17 (chờ M9/MCP); K-13's va
    chạm `P-`/`I-` (chờ một lượt riêng, đụng cả code lẫn `docs/*.md` sống).
- **Toàn bộ M6…M10 của `docs/17-research-alignment.md` (idempotency, isolation/sandbox,
  observability envelope v1, MCP, Service API, evaluation) chưa có DÒNG CODE NÀO** — kiểm
  lại bằng `grep` hôm nay, không phải bằng đọc lại tài liệu cũ (xem `## 3.2`).

---

## 1. PHÁT HIỆN MỚI, SỐNG — `Budget(usd=None)` vòng qua "budget bắt buộc có trục tiền"

Kiểm S-20 khi soát lại toàn bộ danh sách cho tệp này (S-1…S-29 chưa từng được đối chiếu
đầy đủ với code cho tới hôm nay) và thấy nó **vẫn còn sống, y hệt mô tả gốc**:

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

**Sửa tối thiểu (theo đúng đề xuất gốc, áp cho code hôm nay):** không xoá `usd: Decimal |
None` khỏi kiểu (phá `FakeModel`), mà bắt `Agent.__init__`/`build_agent()` từ chối construction
khi `budget.usd is None` **và** `provider` không tự khai "miễn phí" (`price(...).input_per_mtok
== 0`, đã có sẵn — `pricing.py`'s bảng `"fake": Price(0,0,0,0)` chính là cách phân biệt
này). Thông báo lỗi trỏ đúng chỗ: nếu thật sự muốn chạy không trần tiền với một provider
trả phí, dùng `budget="0 steps"`-kiểu tường minh không tồn tại hôm nay — cần quyết định có
xây nó không, hay bắt buộc luôn phải có `usd` cho mọi provider trả phí (đơn giản hơn, đúng
tinh thần "budget bắt buộc" như tài liệu công khai vẫn tuyên bố).

**Đây là việc nên làm TRƯỚC bất kỳ mục nào khác trong roadmap** — nó phá đúng lời hứa
trung tâm nhất của cả thiết kế ("budget bắt buộc, không mặc định, không unlimited") và là
phát hiện "chặn phát hành" duy nhất còn sống.

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
| **S-20** | `Budget(usd=None)` bypass | Chưa làm — xem `## 1` | Không (làm ngay được) |
| **S-4** | Idempotency key thật | Cơ chế chưa tồn tại | M6 (T-6.1) |
| **S-7, S-8, S-9, S-10** | `ServerIdentity`/rug-pull/hint-hạ-effect/`proposed_scope` cho MCP | Không có tích hợp MCP nào để mà sửa | M9 (T-9.1) |
| **S-17** | Injection qua `description` tool MCP trước lời gọi đầu | Cùng lý do trên | M9 (T-9.1) |
| **S-23** | `call_key` domain separator cho idempotency | Cơ chế chưa tồn tại (giống S-4) | M6 (T-6.1) |
| **S-11 (phần còn lại)** | `AuthEvidence` — xác thực người duyệt thật, không chỉ tự khai | Cần mô hình xác thực riêng, chưa thiết kế | Độc lập, ưu tiên theo nhu cầu deployment thật |
| **K-13 (phần còn lại)** | Va chạm namespace `P-`/`I-` giữa `01`/`02`/`docs/09` và `04`/`05` | Rộng hơn ước lượng ban đầu — đụng cả `src/harness/policy/*.py` (comment trích `P-2`/`P-4`) lẫn 7+ tệp `docs/*.md` sống (`docs/09-testing.md`'s property-test-ID series) | Không phụ thuộc gì, nhưng cần một lượt riêng, cẩn thận |

### 3.2 Tính năng/trưởng thành còn thiếu (từ `docs/17-research-alignment.md`, M6…M10)

Đối chiếu lại bằng `grep` hôm nay (không phải đọc lại `§17`, theo đúng luật "kiểm bằng
chạy code" mà chính tệp đó đặt ra) — **không mục nào dưới đây có bất kỳ dòng code nào
trong `src/harness/`:**

| Milestone | Trọng số × khoảng trống | Việc chính | Trạng thái hôm nay |
|---|---|---|---|
| **M6 — Reliability** | 12% × gap 2/5 | Idempotency key, cancellation đúng chuẩn (không nuốt `CancelledError`), retry theo effect class, failure injection | `grep -rl idempot src/` → chỉ 1 dòng comment. Chưa có gì. |
| **M7 — Isolation** | 15% × gap — **nặng ký nhất theo trọng số nghiên cứu** | Workspace root, egress mặc định CHẶN (đảo `allowed_hosts=None` từ "cho tất cả" sang "chặn tất cả" — breaking change), seam `Sandbox`, secret không vào sandbox | `grep -rl sandbox src/` → không có gì. `EgressPolicy` (S-18) đã đúng NHƯNG mặc định vẫn `None`=cho-tất-cả. |
| **M8 — Observability** | 10% × gap | Envelope v1 (`schema_version`/`trace_id`/`tenant_id`), `Approval` là bản ghi đầy đủ (K-đã landing một phần qua S-11's `Approval`, chưa có TTL/policy_version), OTel exporter thật, cost-per-successful-task | `Event` vẫn `['seq','ts','run_id','kind','step','data']` — thiếu 3 trường nghiên cứu đòi. Không OTel. |
| **M9 — Integration** | 8% × gap 3/5 — **khoảng trống lớn nhất theo tự chấm** | MCP client làm tool boundary, Service API (`POST /v1/runs`...), canonical event adapter | `grep -rl mcp src/` → chỉ comment. Không route HTTP nào. |
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
Bây giờ ──► M6 ──► M7 ──► M8 ──► M9 ──► M10
S-20         Reliability  Isolation    Observ.      Integration   Eval
(giờ)        + S-4/S-23   + M7 đảo     + K-13       + S-7…S-10    + trajectory
             (idempotency) egress-deny  namespace    /S-17/S-23-  contract
                                                      MCP-part
```

**S-20 trước tất cả.** Không phụ thuộc gì, sửa nhanh (một guard lúc construction), và là
phát hiện "chặn phát hành" DUY NHẤT còn sống trong toàn bộ 58 phát hiện — để một lỗ hổng
như vậy tồn tại trong lúc lên kế hoạch cho M6…M10 là sai thứ tự ưu tiên.

**M6 trước M7.** Đúng lý do `docs/17 §5` đã ghi: idempotency là điều kiện tiên quyết cho
retry, cho Service API (idempotency key trong header — M9), và cho M10's contract "retry
không nhân đôi side effect." Landing M6 cũng đóng được S-4 và S-23 LUÔN — hai phát hiện
"chờ cơ chế chưa tồn tại" ở `## 3.1` tự động được giải quyết theo cách ĐÚNG (không phải
patch riêng, mà xây đúng cơ chế rồi kiểm S-4/S-23 lại trên nó).

**M7 trước M9.** Mở MCP ra (một hệ sinh thái tool bên thứ ba không đáng tin) trước khi có
Isolation là mở rộng bề mặt tấn công trước khi dựng tường — cảnh báo này đã có sẵn trong
`docs/17`. M7 cũng có trọng số × khoảng trống CAO NHẤT (15% × gap) trong toàn bộ M6…M10.

**K-13's phần còn lại chen vào trước M9.** Không phụ thuộc M6/M7, nhưng đáng làm trước khi
MCP tới — MCP sẽ cần đặt tên invariant mới của riêng nó (rug-pull, re-list) và dọn namespace
trước sẽ tránh chồng thêm một namespace thứ ba lên hai cái đã va chạm.

**M9 đóng được S-7…S-10, S-17, phần MCP của S-23 CÙNG LÚC.** Đây là lý do bốn phát hiện đó
bị hoãn thay vì bị vá non — landing `T-9.1` (MCP client làm tool boundary, `ServerIdentity`,
phân loại `effect` bắt buộc cho tool MCP) chính là bản sửa của cả bốn, không phải bốn bản
vá rời rạc sau đó.

**M10 sau cùng**, đúng thứ tự `docs/17` đã lập luận: trajectory contract cần một bề mặt đã
ổn định (MCP, idempotency, sandbox) để viết `must_call`/`must_not_call` có ý nghĩa.

**Ràng buộc xuyên suốt, nhắc lại từ `docs/17 §5`:** core vẫn 3 dependency, import dưới 100
ms. Mọi thứ ở M6 trở đi là `extra`, và phép thử ranh giới plugin (`02-architecture.md §2.4`)
áp cho từng seam mới — một mục không qua được phép thử đó thì không được vào.

---

## 5. Release plan

### v0.9 — "budget thật, an toàn thật" (điều kiện: hết `## 1`)

Sửa S-20. Đây là điều kiện TỐI THIỂU để bất kỳ ai gọi đây là "sẵn sàng cho môi trường thật"
— một thư viện tuyên bố "budget bắt buộc" mà một keyword argument vòng qua được thì chưa
xứng đáng bất kỳ nhãn phiên bản nào cao hơn 0.x.

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
- K-13's va chạm namespace đã dọn (không phụ thuộc gì khác, chỉ là kỷ luật).
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

1. **S-20** — sửa (`## 1`). Nhỏ, không phụ thuộc, chặn phát hành.
2. **Ghi S-1/S-4/S-5 vào `07-risks-and-open-issues.md`** (`## 2`). Tài liệu, vài phút.
3. **Quyết định: có bắt đầu M6 (Reliability) không, và khi nào.** Đây là điểm rẽ nhánh
   thật — mọi thứ từ đây là XÂY TÍNH NĂNG MỚI (idempotency, sandbox, MCP, Service API,
   eval harness), không còn là "sửa lỗi trong code có sẵn" như toàn bộ phiên làm việc vừa
   qua. Cần quyết định của người vận hành dự án về phạm vi/thời gian, không phải thứ suy
   ra được từ chính code.
