# Rủi ro, đánh đổi, và vấn đề còn mở

Tệp này tồn tại vì luật §45 của nghiên cứu: **thà nói "Chưa đủ evidence" còn hơn đoán.**
Áp lên chính bản thiết kế, nó có nghĩa: mọi thứ chưa chắc phải nằm ở đây, không nằm rải rác
trong sáu tệp kia dưới dạng câu văn tự tin.

---

## 1. Phát hiện review CHƯA được sửa

Hai vòng review đối kháng cho **58 phát hiện**. Đã sửa: 5 lỗi chặn phát hành và 16 lỗi nhất
quán. **Còn lại dưới đây chưa sửa** — liệt kê đầy đủ, vì một danh sách rủi ro chỉ có giá trị
khi nó thành thật.

### 1.1 Bảo mật — nghiêm trọng

> **S-6 ĐÃ KHOÁ.** Đọc lại `_regate()` (bước 1, `lg/runtime.py`) thì công thức hợp thành
> **đã đúng từ trước** — `if r.verdict is not Verdict.ASK: return r` trả DENY ngay, không
> bao giờ chạm `lookup()`; một grant cũ không thể thắng một DENY tươi từ taint. Không phải
> sửa code, chỉ thiếu bằng chứng. `tests/test_attack_s6.py` khoá lại bằng 9 tổ hợp verdict
> × trạng thái sổ đủ hết cỡ + một luật gộp, cộng mutation test (bỏ short-circuit → 4 test
> đỏ ngay).
>
> **S-14 ĐÃ SỬA MỘT PHẦN.** `Ledger._committed()` mới (`budget/ledger.py`) cộng mọi
> reservation đang mở vào `remaining_usd()` và vào cả hai nhánh kiểm ngân sách của
> `reserve()` — trước bản vá, `self._open` được ghi và pop nhưng KHÔNG được cộng vào đâu
> cả, nên hai `reserve()` chồng nhau (một `Retry` plugin tương lai gọi handler nhiều lần
> trước khi cái đầu `settle()`) sẽ mỗi lần đọc cùng ngân sách còn trống và đều "vừa đủ" độc
> lập. `tests/test_attack_s14.py` chứng minh bằng cách gọi `reserve()` hai lần liên tiếp
> chưa `settle()`, cộng mutation test bỏ `_committed()` xác nhận nó load-bearing.
> **`void()` — ĐÃ KIỂM, KHÔNG THÊM.** Truy hết cả hai backend (`run.py:69→86`,
> `lg/runtime.py:111`+`138`): không có `try/except` nào giữa `reserve()` và `settle()` mà
> cho phép CÙNG một `Ledger` sống tiếp để `reserve()` lần nữa sau một lần thất bại —
> `run.py` chỉ bắt `asyncio.CancelledError` ở tầng ngoài, một lỗi provider khác propagate
> thẳng ra khỏi `run()` và mang theo cả `Ledger`; backend graph còn triệt để hơn, mỗi node
> dựng một `Ledger` MỚI từ checkpoint nên `budget_gate`'s reservation không bao giờ tới
> được `call_model` để mà cần huỷ. `void()` có **0 caller** trong `src/harness/` hôm nay —
> đúng bằng chứng đã dùng để hoãn S-7..S-10, nên áp cùng luật: không xây trước khi có
> caller thật. Sẽ cần lại khi `Retry` plugin tồn tại — xem `## 5`.

> **S-15 ĐÃ SỬA — trên backend LangGraph.** `build_agent()` chạy đúng một lần và
> `PolicyEngine` nó dựng phục vụ mọi thread sau đó, nên một policy có state (đếm,
> cache) mà người dùng lỡ truyền INSTANCE thay vì factory sẽ bị mọi thread dùng
> chung — không có ranh giới "hết một run()" sạch để dò như backend cổ điển có
> (`agent.py:_check_shared_policy_state`, Round 34, vẫn nguyên vẹn và vẫn đúng cho
> backend đó). Sửa bằng chiến lược khác cho backend này: `build_agent()` giờ **từ
> chối construction** bất kỳ policy nào không phải factory (`ConfigError`, chỉ đúng
> cách sửa); `Runtime._engine_for(run_id)` dựng một `PolicyEngine` riêng cho mỗi
> thread, gọi factory đúng một lần, cache theo `run_id` — cùng thread qua nhiều lượt
> thấy lại đúng instance của mình (rate-limit trong một cuộc hội thoại vẫn đúng),
> thread khác không bao giờ thấy được. `tests/test_attack_s15.py`: từ chối instance
> lúc dựng, cùng thread giữ state qua nhiều lượt, hai thread không dùng chung
> instance, chạy graph thật hai thread không lây đếm — cộng mutation test (khôi
> phục một `PolicyEngine` chia sẻ) xác nhận 3 test đỏ ngay.

> **S-15 trên backend cổ điển — ĐÃ KIỂM, KHÔNG THÊM.** Câu hỏi: có nên áp CÙNG luật
> "từ chối instance, chỉ nhận factory" lên `Agent`/backend cổ điển, để hai backend
> nhất quán? Không — hai cơ chế khác nhau có chủ ý, không phải một cái đã sửa và một
> cái quên. `agent.py:_check_shared_policy_state` (Round 34, `tests/test_m4.py::
> StatefulPolicy`, vẫn nguyên vẹn) đã kiểm và đã có test cho đúng bốn trường hợp: một
> instance có state đổi trong lúc chạy bị từ chối SAU KHI chạy xong (`ConfigError`,
> "changed while it ran"), một factory/class được dùng đúng, mỗi lần chạy một instance
> mới, VÀ — khác biệt chính — **một instance cấu hình thuần (`EgressPolicy(["..."])`,
> không state đổi theo run) vẫn được dùng chung bình thường, không bị từ chối**.
> `build_agent()` không làm được việc phân biệt đó: nó từ chối MỌI instance ngay lúc
> dựng, kể cả instance cấu hình thuần, vì graph phục vụ nhiều thread ĐỒNG THỜI — không
> có ranh giới "hết một run()" sạch để chờ rồi so sánh trước/sau như backend cổ điển
> có (mỗi `atry_run()` là một lời gọi async hoàn chỉnh, tuần tự trên CÙNG một `Agent`,
> nên "trước lúc chạy" và "sau khi chạy xong" là hai mốc rõ ràng để snapshot). Áp luật
> từ chối construction của backend LangGraph sang backend cổ điển sẽ phá đúng ca dùng
> hợp lệ mà Round 34 cố tình giữ lại (`test_a_stateless_policy_is_still_shareable`) mà
> không sửa được lỗ hổng nào thật — cơ chế hiện tại của backend này đã đúng cho đúng
> mô hình thực thi của nó. Khoảng trống còn lại — hai lời gọi `atry_run()` ĐỒNG THỜI
> (không tuần tự) cùng dùng một instance có state — là rủi ro mutable-state-dùng-chung
> chung của Python với BẤT KỲ object nào, không riêng gì cơ chế Policy của harness này,
> và không có cách sửa tương tự per-thread-cache của S-15 vì backend cổ điển không có
> khái niệm định danh kiểu `run_id`/`thread_id` để cache theo — chấp nhận, không xây.

> **S-13 ĐÃ SỬA — cả hai backend.** `_run_subagent` chỉ `hold()` trục `usd`;
> `steps`/`wall_clock_s` của con được kế thừa nguyên vẹn từ `Budget` con tự khai,
> không liên quan gì tới số còn lại của cha — bốn sub-agent spawn trong một lượt,
> mỗi đứa tự khai `steps=20`, có thể tiêu 80 step trong khi trần cha chỉ có 20/8.
> `Ledger.hold_steps()`/`release_steps()` áp đúng lý luận TOCTOU của `hold()`
> (Round 28) sang trục step; `child_wall_clock()` cắt trần thời gian con xuống
> đúng số cha còn lại tại thời điểm spawn — không cần hold/release vì wall-clock
> không phải hồ tài nguyên bị chia (hai con chạy song song không cộng dồn thời
> gian của nhau). `tests/test_attack_s13.py`: con bị cap đúng xuống step cha còn
> lại (đo bằng SỐ LẦN TOOL THẬT SỰ CHẠY, không phải số học `Ledger` — lần thử
> mutation đầu tiên vô tình triệt tiêu vì `release_steps` tính theo hiệu số nên
> "bỏ hold + bỏ cap" khớp nhau về 0, khiến `remaining_steps()` trông vẫn an toàn
> dù cha không hề bị trừ), bốn con cộng dồn không vượt trần, step dư được trả
> lại — mutation khôi phục hành vi cũ xác nhận 2 test đỏ ngay.

| mã | vấn đề | vì sao chưa sửa |
|---|---|---|
| S-11 | `Actor` là lời tự khai, không có evidence | cần mô hình xác thực người duyệt |
| S-12 | ba kênh resume (`answer=`, `ResumeToken`) vẫn chưa thống nhất giữa 01 và 04 | đã sửa một nửa (`ruling=` → `answer=`) |
| S-17 | `description` của tool MCP vào prompt khi nhãn còn `TRUSTED` | injection qua metadata; cần gắn nhãn cho description |
| S-18 | P-4 (`Policy.check` thuần) làm `DenyHosts` chỉ còn advisory ⇒ SSRF đi qua | cần tách policy thuần khỏi enforcement I/O |

> **S-7, S-8, S-9, S-10 — HOÃN CÓ CHỦ Ý, không phải bỏ quên.** Cả bốn xoay quanh
> `ServerIdentity`/`fingerprint`/rug-pull qua re-list của tool MCP. Kiểm tra `src/harness/`:
> **không có tích hợp MCP nào tồn tại** — `ToolSpec` không có trường `server`, không có
> `ServerIdentity`. Xây cơ chế so khớp server ngay bây giờ là hạ tầng không ai gọi, đúng
> loại lỗi mà chính vòng KISS đã cắt (`Quarantine`, `deps_type`, `Snapshottable` — xem
> `review-kiss.md` K-1/K-2/K-5). Bốn phát hiện này phải được đưa vào code **cùng lúc** với
> khi MCP thật được xây, không phải trước — landing chúng trước sẽ tạo ra đúng kiểu trừu
> tượng "0 implementer khớp" mà K-5 đã cảnh báo.

> **S-16, S-19, và S-3 ĐÃ SỬA — trên giấy VÀ trong `src/harness/`.** Bước 0 chốt mô hình
> (S-16: `accepts_tainted` rời `@tool`, chỉ đến từ operator; S-19: nhãn per-message +
> L-1/L-2/L-3). Bước sau đó đưa vào code: `policy/label.py` (canonical `Integrity` ×
> `Confidentiality` × `Label`, `Grants`), `policy/builtin.py` (`check_flow` hai nhánh,
> `emits_of`), `lg/runtime.py` (`_effective_label` thay `_tainter`, L-2 stamp trong
> `call_model`, L-1 stamp trong `_run_tools`, `_manage` giữ `additional_kwargs` khi xoá
> nội dung). Backend cổ điển (`run.py`/`dispatch.py`) nâng `TaintTracker` lên `Label` hai
> trục nhưng GIỮ sticky-per-run — nó miễn nhiễm với chính kiểu rửa taint mà per-message
> phải phòng, vì nó không tính lại theo message; đây là khác biệt có chủ ý giữa hai
> backend, không phải việc chưa xong. 11 test tấn công mới ở `tests/test_attack_s19.py`,
> mỗi cơ chế chính có một mutation test đi kèm (xoá đúng dòng code thì test phải đỏ) —
> cùng kỷ luật với bước 1/2.

> **S-3 nguồn thứ nhất (`Secret[T]`) ĐÃ SỬA — cả hai backend.** Bước trên chỉ landing
> nguồn thứ hai (`Grants.sensitive`, operator đánh dấu tool). Không có cơ chế `deps`
> riêng ở harness này (K-1/K-2 đã cắt), nên đường thật của `Secret[T]` là: tool tự
> `.reveal()` một `Secret` (`secrets.py`) rồi giá trị đó xuất hiện nguyên văn trong
> payload trả về — đúng khoảnh khắc `redact()` đã canh sẵn để chặn trước khi bytes tới
> model (RT-13, Round 35). `secrets.contains_live_secret()` dùng lại đúng phép so khớp
> đó để phát hiện (không `.reveal()` khi chỉ dò — không tự đăng ký thêm); `emits_of(spec,
> grants, payload)` (`policy/builtin.py`) nâng nhãn MESSAGE đó lên `SECRET` khi phát hiện,
> dù `redact()` đã xoá đúng token khỏi bytes model thấy — phần còn lại của cùng message
> không bị xoá, nên `check_flow` vẫn cần nhãn SECRET để chặn nó rời qua sink `PUBLIC` ở
> bước sau, cùng độ chi tiết "theo message" mà `Label` dùng ở khắp nơi khác. Cả hai chỗ
> gọi (`dispatch.py::_invoke`, `lg/runtime.py::_run_tools`) truyền payload thô (trước
> `redact()`) vào `emits_of`; tham số mới có mặc định `None` nên không phá chữ ký cũ.
> `tests/test_attack_s3.py`: `contains_live_secret` đơn vị, `emits_of` đơn vị với/không
> payload, chạy thật qua cả hai backend (`Agent.try_run` và `build_agent().invoke`) xác
> nhận secret lộ qua một tool `read` chặn được sink `PUBLIC` kế tiếp — mutation gọi
> `emits_of` không kèm payload (đúng chữ ký cũ) ở cả hai chỗ xác nhận 2 test đỏ ngay,
> mỗi backend một cái.

### 1.2 Bảo mật — nên sửa (S-21…S-29)

`_blocked` không snapshot · `reserve()` bỏ qua cache-write · `call_key` thiếu domain
separator · `seq` không có nguồn cấp · UI injection và approval fatigue qua `args` ·
`canonical_args` mất kiểu · cửa sổ nhãn trong node `tools` · sub-agent cần `ASK` không có
đường · tái dùng grant không để lại bản ghi durable.

### 1.3 KISS — chưa cắt

`Confidentiality` giờ đã có nguồn (S-3 đã sửa) nên **K-6 không còn hiệu lực**. Còn lại:
`Reservation.exact` (K-7) · `run`/`try_run`/`raise_for_status`/`.ok` bốn cách nói một điều
(K-9) · taxonomy OTel 9 span nên rút còn 4 (K-10) · `end_strategy` (K-11) ·
`ServerIdentity.fingerprint` (K-12) · 38 mã bất biến với 3 va chạm namespace (K-13) ·
tuyên bố "14 tên, một import" sai — ví dụ import 20 tên từ 4 module (K-22) · 9 tunable
không có đường từ API công khai (K-23) · thiếu session/tenant identity mà minimal core đòi
(K-28).

### 1.4 Đếm khái niệm — chưa đạt mục tiêu

| phép đo | hiện tại | sau khi áp phần còn lại |
|---|---:|---:|
| `class` định nghĩa | ~55 | ~48 |
| tên để viết agent đầu tiên | **8** ✅ | 8 |
| tên cộng dồn tới production | ~38 | ~33 |

Mức 0 là 5 dòng và 8 tên — đạt yêu cầu "học sinh 10 tuổi". Đường tới production thì **chưa**
gọn như tuyên bố.

---

## 2. Ý tưởng có kiến trúc, chờ eval

Cắt khỏi đường đi bắt buộc theo luật §8.4, **không vứt đi**. Nếu có ngày đo được, đây là chỗ
lấy lại.


### Quarantine model (dual-LLM / CaMeL)

### 5.1 Cái Microsoft làm đúng, và chỗ đặt sai

`set_quarantine_client` cung cấp một model riêng, rẻ hơn, để suy luận trên nội dung không
tin cậy — mẫu dual-LLM / CaMeL. Đây là thứ **không gói nào khác trong 28 gói Python +
TypeScript có** ([§09](../research/09-memory-context-multiagent-hitl.md) §16bis). Ý tưởng
đúng; chỗ đặt sai: `_quarantine_chat_client` là biến **mức module**, gán qua
`global`.

### 5.2 Nó thuộc về đâu trong đời một `Run`

Quarantine không phải cấu hình của tiến trình. Nó là **một seam của `Run`**:

```python
@value
class RunConfig:
    ...
    quarantine: ModelProvider | None = None      # None = fail closed, xem dưới
    max_grant_ttl: timedelta = timedelta(hours=1)


@value
class Quarantined[T]:
    """Kết quả rút trích từ nội dung UNTRUSTED. Chỉ dữ liệu có schema, không văn xuôi."""
    value: T                    # T phải là kiểu đóng: primitive, enum, hoặc @value có schema
    label: Label                # luôn Integrity.UNTRUSTED — không hạ được
    source_call_id: CallId
```

Ba luật, mỗi luật sửa một chỗ:

1. **Vòng đời = vòng đời `Run`.** Provider được chốt (resolve) một lần lúc `Run` khởi
   tạo và nằm trong `RunConfig` bất biến. Không setter, không `global`. Hai tenant chạy
   song song trong một tiến trình có hai `RunConfig` — sửa khác biệt #3 ở §4.3.
2. **Chỉ dữ liệu có schema quay lại context chính.** Model quarantine đọc nội dung
   `UNTRUSTED` và trả về `Quarantined[T]` với `T` là kiểu đóng. Văn xuôi tự do **không**
   được quay lại — đó chính là tính chất làm nên CaMeL: nội dung không tin cậy được rút
   thành *giá trị*, không được rút thành *chỉ thị*. Transcript của quarantine không bao
   giờ merge vào `messages` của run chính.
3. **`Quarantined.label` không hạ được.** Không có API nào biến `UNTRUSTED` thành
   `TRUSTED`. Quarantine giảm *bề mặt tấn công* (model rẻ, output có schema), nó **không**
   tẩy nhãn. Đây là điểm dễ cài sai nhất và là lý do trường `label` nằm ngay trong kiểu.
4. **Không cấu hình ⇒ `DENY`, không phải bỏ qua.** `quarantine=None` mà gặp tình huống
   cần nó thì kết quả là `DENY` kèm reason nêu rõ. Đối lập với Microsoft: ở đó module
   không được wire vào nên tình huống ấy **im lặng đi tiếp**.

Chi phí của model quarantine tính vào **cùng một `Ledger`** của run
([`05-cost-and-memory.md`](./05-cost-and-memory.md)). Một cơ chế an toàn có ngân sách
riêng là một cơ chế an toàn không đếm được.

**KISS:** quarantine chỉ được gọi ở đúng một tình huống — context `UNTRUSTED`, tool cần
gọi là `danger` hoặc `accepts_tainted=False`, và người vận hành đã cấu hình provider. Nếu
không, đường đi bình thường là `ASK` (có con người) hoặc `DENY`. Không thêm chế độ nào
khác cho tới khi có số đo cho thấy cần.

---

**Vì sao cắt.** Mẫu này có đúng **một** cài đặt trong toàn nghiên cứu, và cài đặt đó
`@experimental`, không được wire vào harness của chính nó, và không concurrency-safe. **Không
có eval nào** so sánh tỉ lệ prompt-injection thành công có và không có nó
([review-kiss.md](review-kiss.md) K-1).

**Điều kiện lấy lại:** một eval trên tập prompt-injection thật, đo tỉ lệ thành công có và
không có quarantine, trên cùng bộ tool. Nếu chênh lệch không có ý nghĩa thống kê, giữ nguyên
trạng thái cắt.

---

## 3. Đánh đổi đã chọn, và cái giá của từng cái

| chọn | được | mất |
|---|---|---|
| Graph thay vì loop | durability, chứng minh được, vẽ được — ba hệ quả của một quyết định | phụ thuộc LangGraph; người dùng phải hiểu khái niệm node |
| Effect class suy ra 5 hành vi | tác giả tool khai **một** thứ; không có guard viết tay per-tool | 4 lớp là thô — một tool vừa đọc vùng nhạy cảm vừa ghi không xếp gọn |
| Invariant trên đường bắt buộc, plugin cho policy | "không cài" không còn là mặc định không an toàn | plugin không làm được vài thứ (không chặn được permission check) |
| `Decision` append-only | audit thật, thu hồi bằng `max()` | sổ chỉ lớn lên; cần chính sách lưu trữ chưa viết |
| effect log luôn bật cho `write` | khuyết điểm #5 sửa **theo mặc định** | một round-trip DB thêm mỗi `write` call — **chưa benchmark** |
| At-most-once thay vì exactly-once | trung thực về cái harness một mình làm được | người dùng muốn exactly-once phải có upstream nhận key |

---

## 4. Chưa đủ evidence — hợp nhất

Từ sáu tệp thiết kế, không lặp lại lý lẽ:

- **Chi phí effect log** trên mỗi `write`. Không đo được từ source người khác.
- **`fingerprint` cho MCP stdio.** TLS SPKI pin đúng cho HTTP; không có tương đương hiển
  nhiên cho tiến trình con.
- **Biên checkpoint chính xác của LangGraph** giữa chừng một node — chưa đọc trong source.
- **Isolation đa tenant ở tầng store.** R-4 là điều kiện cần, không phải đủ.
- **Số bậc của trục confidentiality.** Hai bậc là suy luận, không phải kết quả đo.
- **TTL mặc định cho grant `danger`.** Không có bằng chứng nào về con số đúng.
- **Chính sách hết hạn memory.** Một memo `UNTRUSTED` sống mãi là rủi ro thật, nhưng nghiên
  cứu không có bằng chứng về chính sách nào đúng, nên không phát minh một chính sách.
- **Learning curve.** [§11](../research/11-workflow-and-dx.md) §45 ghi rõ nó **không được
  đo** trong nghiên cứu. Mọi tuyên bố DX ở đây dựa trên thứ đếm được (`py.typed`, số tên,
  số dòng Mức 0), không dựa trên người dùng thật.

---

## 5. Việc tiếp theo, theo thứ tự

1. **S-7, S-8, S-9, S-10** — landing cùng lúc với khi tích hợp MCP thật được xây, không
   trước (xem `## 1.1`).
2. **Cắt K-7, K-9, K-10, K-23** — giảm đường tới production từ ~38 xuống ~33 tên.
3. **`Ledger.void()`, S-16/S-19/S-3 trên backend cổ điển, S-15 trên backend cổ điển —
   ĐÃ KIỂM, KHÔNG THÊM.** Cả ba được xét kỹ; xem `## 1.1` cho từng cái.
4. **Tiếp tục viết code.** S-16/S-19/S-3 (cả hai nguồn)/S-6/S-14/S-15/S-13 đã vào
   `src/harness/` — 312 test xanh, mỗi cơ chế chính có mutation test đi kèm. Vẫn còn
   nhiều phát hiện review chưa chạm tới code, và nghiên cứu của chính dự án đo được
   **23 vòng review tìm 20 lỗi và 0
   lỗi bảo mật; 16 vòng chạy tìm 38+ lỗi và 4 lỗi bảo mật** — bản thiết kế là sản phẩm của
   review, nó sẽ sai ở những chỗ chỉ có chạy mới tìm ra.
