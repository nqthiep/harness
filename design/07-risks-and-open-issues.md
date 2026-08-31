# Rủi ro, đánh đổi, và vấn đề còn mở

Tệp này tồn tại vì luật §45 của nghiên cứu: **thà nói "Chưa đủ evidence" còn hơn đoán.**
Áp lên chính bản thiết kế, nó có nghĩa: mọi thứ chưa chắc phải nằm ở đây, không nằm rải rác
trong sáu tệp kia dưới dạng câu văn tự tin.

---

## 1. Phát hiện review CHƯA được sửa

Hai vòng review đối kháng cho **58 phát hiện**. Đã sửa trước khi tệp này bắt đầu theo dõi:
5 lỗi chặn phát hành và 16 lỗi nhất quán. **Một lỗi chặn phát hành thứ sáu (S-20) đã lọt
qua đợt đó** — kiểm lại toàn bộ S-1…S-29 với code hôm nay (không phải chỉ nhóm đang được
sửa từng đợt) tìm thấy nó vẫn sống, xem `## 1.1` và `design/08-roadmap-and-release-plan.md`.
**Còn lại dưới đây chưa sửa** — liệt kê đầy đủ, vì một danh sách rủi ro chỉ có giá trị khi
nó thành thật.

### 1.1 Bảo mật — nghiêm trọng

> **S-20 CÒN SỐNG — CHƯA SỬA.** `Budget.usd: Decimal | None` (`budget/ledger.py`) vẫn
> cho `None`. Việc bắt buộc "phải có trục tiền" chỉ nằm trong `Budget.parse()` — con
> đường qua CHUỖI (`budget="$0.05"`). Dựng `Budget(usd=None, steps=100, wall_clock_s=3600)`
> trực tiếp rồi `Agent(budget=...)` không bị chặn ở đâu cả — kiểm trực tiếp: không
> `ConfigError`, không cảnh báo. `Ledger.size_call()`/`reserve()` cả hai đều có nhánh
> `if self._b.usd is None: ...` bỏ qua trần hoàn toàn. Với một provider THẬT (không phải
> `FakeModel`, thứ `usd=None` được thiết kế RIÊNG cho — xem comment Round 24 trong code),
> đây là "loop limit không kèm spend ceiling" đạt được bằng một keyword argument, đúng
> mô tả gốc của review. Đây là phát hiện "chặn phát hành" DUY NHẤT trong toàn bộ 58 phát
> hiện còn sống chưa sửa — xem `design/08-roadmap-and-release-plan.md §1` cho bản sửa đề
> xuất và vì sao nó đứng đầu danh sách việc cần làm.

> **S-1 ĐÃ KIỂM — LỖI THỜI, KHÔNG CẦN SỬA.** Cả hai nhánh của kịch bản gốc dựa vào cơ chế
> không tồn tại: (a) `Quarantine` — K-1 đã cắt, `0 caller`; (b) chiến lược nén
> `SummarizeOldPrefix` (gọi model để tóm tắt, ngoài node `model` nên thiếu `reserve()`) —
> chiến lược nén THẬT SỰ được xây là `ClearToolResults` (`context/window.py`, hằng số
> `CLEARED`), chỉ xoá nội dung, không gọi model nào. Không có lời gọi model nào ngoài
> node `model` để mà thiếu reservation.

> **S-4 — CHƯA LỖI THỜI, nhưng chưa áp dụng được vì cơ chế nó bàn chưa tồn tại.** Giao
> thức idempotency ba pha (`IdempotencyMode`, `in_flight`, `call_with_effect_log`) không
> có trong `src/harness/` — đúng khoảng trống `docs/17-research-alignment.md` M6/T-6.1 đã
> ghi nhận và xếp lịch xây. Không đóng bây giờ: cần RE-VERIFY khi M6 build idempotency,
> xem `design/08-roadmap-and-release-plan.md §2`.

> **S-5 ĐÃ KIỂM — LỖI THỜI, và may mắn theo hướng an toàn.** Cơ chế `Provenance`/nhãn-theo-
> bản-ghi mà S-5 phê phán (dữ liệu trong store tự khai `label`, giả mạo được) không được
> xây. Cơ chế THẬT (`memory/viking.py::tools()`) đơn giản hơn và tình cờ đúng "sửa tối
> thiểu (a)" mà chính S-5 đề xuất: `recall` khai `effect="external"` — nhãn UNTRUSTED áp
> qua đúng con đường chung mọi tool `external` đi (`emits_of`/`check_flow`), không có
> ngoại lệ "tin provenance" nào để mà giả mạo.

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

> **S-11 SỬA MỘT PHẦN.** `Actor` vẫn là lời tự khai — không có mô hình xác thực người
> duyệt, và xây một cái (chữ ký kênh, `channel_message_id` — `AuthEvidence` review đề
> xuất) là việc lớn, cần thiết kế riêng, ghi lại bên dưới. Phần landing được: chữ ký thật
> của `approve=` (`ApprovalFn = Callable[[ToolCall, RunContext], bool]`) không có kênh
> nào để callback báo DANH TÍNH — trước bản vá, backend LangGraph (backend DUY NHẤT có
> `DecisionLog`; backend cổ điển không dùng nó) ghi cứng
> `Actor.human("approver", via="callback")` cho MỌI lần duyệt, bất kể ai bấm. `Approval(ok,
> actor=...)` (`policy/decision.py`) là trả về TÙY CHỌN thay cho `bool` trần — callback
> nào thật sự biết danh tính (phiên Slack đã xác thực, OAuth) giờ báo được, callback trả
> `bool` không đổi gì. `PolicyEngine.resolve()` trả `(Ruling, Actor | None)` thay vì chỉ
> `Ruling`. `tests/test_attack_s11.py`: `resolve()` với `bool` trần không báo actor nào;
> với `Approval(...)` trả đúng actor đó cho cả ALLOW/DENY; chạy graph thật xác nhận
> `DecisionLog` ghi đúng actor callback báo, và callback `bool` cũ vẫn ra placeholder y hệt
> trước — mutation khôi phục `resolve()` cũ xác nhận 4 test đỏ ngay. Vẫn không chặn được
> callback TỰ KHAI GIAN danh tính — đó là phần cần `AuthEvidence` thật.

> **S-12 ĐÃ KIỂM — LỖI THỜI, KHÔNG CẦN SỬA.** Ba chữ ký `answer=`/`ruling=`/`ResumeToken`
> review mô tả không có cái nào tồn tại trong `src/harness/` — `grep` toàn bộ `src/` và
> `tests/` không thấy `ResumeToken`, `class Answer`, hay `ruling=` ở đâu cả. Cơ chế resume
> THẬT hoàn toàn khác và không có lỗ hổng S-12 mô tả: backend cổ điển
> (`Agent.resume(transcript)`) chạy lại các lời gọi `read`/`external` bị ngắt giữa chừng
> từ transcript, không đụng gì tới `Decision`/grant. Backend LangGraph dùng
> `interrupt()`/`Command(resume=<bool>)` gốc của LangGraph — `ok = bool(interrupt(...))`
> chỉ quyết định ALLOW/DENY; `Scope`/`actor`/`expires_at` của `Decision` ghi ra đều do
> RUNTIME tự dựng từ `_pending`, người resume không tự đặt được scope rộng hơn hay
> `expires_at` xa hơn — đúng phần mà bản thiết kế gốc lo bị bỏ qua ("Answer.expires_at đi
> vào từ bên ngoài") không hề tồn tại trên code hôm nay.

> **S-17 — GẤP CHUNG VỚI S-7…S-10, HOÃN CÓ CHỦ Ý.** Injection qua `description` tool MCP
> đưa vào prompt trước lời gọi tool đầu tiên là một phát hiện thật, nhưng — như S-7…S-10 —
> hoàn toàn thuộc về phân loại tool MCP, thứ **không tồn tại trong `src/harness/` hôm
> nay**. Không có `tools/list`, không có bước bind server, không có gì để nâng nhãn "lúc
> bind" cả. Landing cùng lúc với khi MCP thật được xây, không trước.

> **S-18 ĐÃ SỬA — bằng tài liệu, không phải code.** `EgressPolicy` (chỗ thật thay cho
> `DenyHosts` mà review trích) đã đúng như S-18 mô tả: P-4 (`Policy.check` thuần, không
> I/O) khiến nó chỉ so khớp CHUỖI hostname, không resolve DNS — một
> `fetch_page(url="http://look-alike.attacker.example/")` qua được đúng phép kiểm nếu
> chuỗi host tự nó nằm trong allowlist, DNS rebinding trỏ nó về IP nội bộ chỉ lộ ra lúc
> THẬT SỰ gọi. Không có cách sửa ở tầng `Policy` thuần cho việc này — cần một tầng mạng
> thật (egress proxy, network policy container), ngoài phạm vi một policy đồng bộ. Sửa
> bằng cách nói thẳng: docstring `EgressPolicy` (`policy/builtin.py`) và
> `docs/06-safety.md` giờ ghi rõ nó chỉ chặn trường hợp RÕ RÀNG, không hơn. Kiểm thêm phát
> hiện phụ của S-18 (nhầm lẫn URL qua `userinfo@host`) và thấy nó đã LỖI THỜI: `urlparse`
> của Python và client HTTP chuẩn RFC 3986 đều đồng ý `evil.example` là host thật trong cả
> hai biến thể review nêu — `tests/test_attack_s18.py` khoá lại bằng test trực tiếp.

> **S-7, S-8, S-9, S-10, S-17 — HOÃN CÓ CHỦ Ý, không phải bỏ quên.** Cả năm xoay quanh
> `ServerIdentity`/`fingerprint`/rug-pull/injection qua metadata của tool MCP. Kiểm tra
> `src/harness/`: **không có tích hợp MCP nào tồn tại** — `ToolSpec` không có trường
> `server`, không có `ServerIdentity`, không có `tools/list`. Xây cơ chế so khớp server
> ngay bây giờ là hạ tầng không ai gọi, đúng loại lỗi mà chính vòng KISS đã cắt
> (`Quarantine`, `deps_type`, `Snapshottable` — xem `review-kiss.md` K-1/K-2/K-5). Năm
> phát hiện này phải được đưa vào code **cùng lúc** với khi MCP thật được xây, không phải
> trước — landing chúng trước sẽ tạo ra đúng kiểu trừu tượng "0 implementer khớp" mà K-5
> đã cảnh báo.

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

**Cả chín ĐÃ XONG** (S-21/22/24/25/27/29 sửa code; S-23/26/28 kiểm rồi xác nhận lỗi
thời/đã đúng, không cần sửa). S-11 (bảng `## 1.1`) cũng sửa được phần landing được — còn
lại duy nhất là `AuthEvidence` thật, ghi ở `## 5`.

> **S-21 ĐÃ SỬA.** `Ledger.snapshot()` thiếu `blocked` — một ledger `_blocked=True` (spend
> vượt trần cứng) phục hồi từ checkpoint về `_blocked=False`, tự "quên" nó đã bị chặn.
> Thêm `blocked` vào cả `snapshot()`/`restore()`. `tests/test_attack_s21_s22.py`.

> **S-22 ĐÃ SỬA.** `size_call()`/`reserve()` chỉ định giá theo `input_per_mtok`, trong khi
> `settle()` có thể tính theo `cache_write_per_mtok` (`_p()` trong `models/pricing.py`:
> luôn đắt hơn đúng 25%, cố định — không phải số đo). Mọi cuộc gọi THẬT SỰ ghi cache bị
> ước lượng thấp hơn thực tế có hệ thống. Định giá lại theo mức TỆ NHẤT
> (`cache_write_per_mtok`) ở cả `reserve()` lẫn nhánh `hard_max_input`.
> `tests/test_attack_s21_s22.py`.

> **S-23 ĐÃ KIỂM — LỖI THỜI, KHÔNG CẦN SỬA.** `call_key = blake2b(...)` mà review mô tả
> thuộc giao thức idempotency ba pha (`03 §4.4`) — `IdempotencyMode`, `in_flight`,
> `call_with_effect_log` — **không tồn tại** trong `src/harness/` (cùng tình trạng "0
> caller" như S-7…S-10, K-25). Cơ chế dedup THẬT (T-2.5, `dispatch.py`) dùng
> `f"{name}:{canonical_json(args)}"` chứ không phải `blake2b` nối chuỗi, và `name` là tên
> tool do TÁC GIẢ đặt lúc bind (không phải input model/MCP điều khiển được hôm nay) — va
> chạm domain-separator review lo chỉ thật khi `tool` là chuỗi tự do do bên ngoài đặt, đúng
> viễn cảnh MCP namespaced mà S-7…S-10 hoãn. Không có gì để sửa cho tới khi MCP thật tồn
> tại.

> **S-24 ĐÃ SỬA — hoá ra rộng hơn "seq không có nguồn cấp" review mô tả.** Đúng lỗi Round
> 37 đã sửa cho `Ledger`/`TaintTracker`, và S-15 sửa cho `PolicyEngine`, lần THỨ TƯ:
> `build_agent()` từng dựng đúng MỘT `EventBus("run", exporters)`, giữ trên `Runtime`, dùng
> chung cho MỌI thread — `event.run_id` là chuỗi cố định `"run"` cho mọi hội thoại,
> `event.seq` là một bộ đếm chung xuyên suốt đời compiled graph. Sửa cùng khuôn
> `_policy_cache`/`_engine_for`: `Runtime._bus_cache` giữ một `EventBus` riêng mỗi thread,
> dựng lười, đóng dấu đúng `run_id` thật. Tìm thêm trong cùng lượt: cờ `self._started`
> (instance trên `Runtime`) làm `RUN_STARTED` chỉ phát MỘT LẦN DUY NHẤT cho cả đời compiled
> graph thay vì mỗi thread — bỏ cờ, chỉ dựa vào `step==0` (đã đủ, vì `step` sống trong
> state theo từng thread). `tests/test_attack_s24.py`, mutation test xác nhận 4/5 đỏ.

> **S-25 ĐÃ SỬA.** (a) `call.arguments` (thô từ model) vào thẳng `approve(call, ctx)`,
> không escape/truncate — `secrets.safe_for_display()` mới (xuất ở top-level) escape mọi
> ký tự không in được thành dạng chữ (`\x1b` không thực thi) và thay giá trị dài hơn 200
> ký tự bằng độ dài + digest. (b) không có trần số lần `ASK` — `max_asks_per_run` (mặc
> định 20) trên cả `Agent`/`build_agent`, đếm per-run (classic loop) hoặc trong
> `AgentState.asks` reset mỗi lượt mới (graph, vì `Runtime` dùng chung giữa các thread).
> `tests/test_attack_s25.py`, mutation test mỗi backend.

> **S-26 ĐÃ KIỂM — LỖI THỜI, KHÔNG CẦN SỬA.** `Scope.args` review mô tả là
> `Mapping[str, str]` (ép kiểu về chuỗi, `transfer(amount=10)` khớp nhầm
> `transfer(amount="10")`) — code hôm nay là `Mapping[str, Any]`, và `Scope.matches()` so
> `dict(self.args) == dict(args)` trực tiếp, giữ nguyên kiểu Python (`10 == "10"` là
> `False`). `tests/test_attack_s26.py` khoá lại bằng test trực tiếp.

> **S-27 ĐÃ SỬA — kịch bản gốc không dựng lại được, nhánh confidentiality thì có thật.**
> Kịch bản gốc (`fetch_url` external + `run_shell` danger cùng lượt) bị `_check_tool_set`
> (F9.1) chặn NGAY LÚC DỰNG agent trừ khi operator tự khai `accepts_tainted` cho tool
> danger đó — và một khi đã khai, `check_flow` không còn gì để chặn. Nhánh CÒN SỐNG: nhánh
> confidentiality (SECRET vào sink PUBLIC) không bị check đó chạm tới — một tool `read`
> nhạy cảm + một tool `write` cùng lượt tái tạo đúng lỗ hổng. Backend cổ điển
> (`dispatch.py`): mọi quyết định trong batch tính MỘT LẦN trước khi tool nào chạy; sửa
> bằng recheck `check_flow` ngay trước mỗi lời gọi serial, dùng nhãn SỐNG. Backend LangGraph
> (`lg/runtime.py::_regate`): tưởng đã đóng bằng I-1 (S-2) — nhưng `_regate` tính nhãn từ
> `self._effective_label(state)`, và `state` chưa thấy kết quả của call ĐÃ chạy TRƯỚC
> trong CÙNG `_run_tools` (chỉ gộp vào state ở cuối hàm); sửa bằng truyền `label` đang cập
> nhật sống trong vòng lặp vào `_regate` thay vì để nó tính lại từ `state` cũ.
> `tests/test_attack_s27.py`, mutation test mỗi backend.

> **S-28 ĐÃ KIỂM — ĐÃ ĐÚNG SẴN, CHỈ THIẾU TÀI LIỆU.** Sub-agent cần `ASK` không có đường
> tới node `approve` của cha nghe như treo — nhưng `PolicyEngine.resolve()`'s luật "không
> có `approve=`" (đã có sẵn, áp dụng y hệt cho con lẫn cha) đã đóng đúng lỗ này mà review
> không xét tới: con không có `approve=` riêng thì `danger` tự động DENY, mọi thứ khác tự
> động ALLOW — không bao giờ dừng chờ. Ghi rõ vào `docs/06-safety.md`.
> `tests/test_attack_s28.py` khoá hành vi.

> **S-29 ĐÃ SỬA — chỉ tồn tại trên backend LangGraph.** Backend cổ điển không dùng
> `DecisionLog` (mỗi `atry_run()` gọi `approve()` mới, không có khái niệm grant sống qua
> nhiều lần thực thi), nên S-29 chỉ áp cho `lg/runtime.py::_regate`. Phạm vi thật hẹp hơn
> văn bản gốc review mô tả ("TTL 1 giờ phủ N lần chạy khác nhau") — MỌI `Decision` từng
> dựng (kể cả bản vá này) khoá `scope.call_id` vào đúng MỘT call cụ thể, không có
> `scope.call_id=None`/TTL dài thật sự tồn tại trong code hôm nay. Cơ chế thật là recheck
> tại điểm tiêu thụ của I-1 (S-2): cùng một `call_id` được duyệt ở `approval_gate` rồi
> `_regate` tra lại lúc thực thi — khoảng cách đó có thể là mili-giây (thường) hay hàng
> giờ (resume). Trước bản vá, lần tra lại đó KHÔNG ghi gì thêm dù tìm thấy grant sống. Sửa:
> `_regate` ghi một `Decision` thứ hai (id `-reuse`) mỗi lần grant được tái dùng.
> `tests/test_attack_s29.py`, mutation test xác nhận 2/3 đỏ.

### 1.3 KISS — chưa cắt

`Confidentiality` giờ đã có nguồn (S-3 đã sửa) nên **K-6 không còn hiệu lực**.

`K-7`, `K-9`, `K-10`, `K-23` — bốn mục "nên cắt" ưu tiên nhất đã được kiểm lại trên
`src/harness/` HÔM NAY, không phải bản nháp `K-7`/`K-23` viện dẫn — hai trong bốn đã lỗi
thời trước khi tới lượt sửa:

> **K-9 ĐÃ CẮT.** `Result.raise_for_status()` đúng như review nói: `run()` viết lại,
> không chỗ nào khác đọc. Cắt khỏi `Result` (`result.py`); `run()`/`arun()`
> (`agent.py`) giờ tự dựng `RunFailed` qua một hàm riêng `_raise_if_failed`, không lộ
> ra ngoài — giữ nguyên thông điệp lỗi cũ, `try_run()`/`.ok` không đổi.
> `tests/test_kiss_cuts.py::K9RaiseForStatusCut` khoá bề mặt đã cắt + xác nhận `run()`
> vẫn raise đúng nội dung qua test `RunFailed` sẵn có (`test_walkthrough.py`).

> **K-7 ĐÃ LỖI THỜI — KHÔNG CẮT.** Lý do review đưa ra ("không mục nào đọc
> `Reservation.exact`") không còn đúng: code đã tiến hoá thành
> `Ledger.last_call_was_exactly_bounded` (property, không phải trường trên
> `Reservation`), và `run.py` ĐỌC nó thật, đưa vào sự kiện `BUDGET_RESERVED` làm
> attribute `exact=` — một consumer thật (observability), không phải trang trí.
> `tests/test_kiss_cuts.py::K7ExactVanConDuocDoc` khoá cả hai: `reserve()` đặt cờ đúng
> lúc hard bound vừa ngân sách, VÀ `run.py` thật sự đưa nó ra ngoài qua exporter.

> **K-23 hầu như đã KHÔNG CÒN ĐÚNG — KHÔNG CẮT.** Bảng chín tunable của review: một
> (`max_concurrency`) giờ CÓ đường từ `Agent(...)` — `max_parallel_tools`, review viết
> khi nó chưa có. Sáu cái khác (`RunConfig`, `cancel_grace`, `max_grant_ttl`,
> `quarantine`, trần `depth`, ngân sách retry) **chưa từng được xây** trong
> `src/harness/` — không có gì để cắt, cùng tình trạng "0 caller" như S-7..S-10.
> Hai cái còn lại (`EDIT_AT`/`COMPACT_AT`/`KEEP_RECENT_STEPS`,
> `INPUT_MARGIN`/`MIN_USEFUL_OUTPUT_TOKENS`) đã ĐÚNG như chính K-23 đề nghị: hằng số
> module `Final`, không cấu hình được, không phải cấu hình rải ba nơi.

`K-10` (taxonomy OTel 9 span → 4) không có code để cắt — chưa có tích hợp OpenTelemetry
nào trong `src/harness/` (`observe/events.py`'s `EventBus`/`Exporter` là cơ chế quan sát
hiện có, độc lập với OTel). Rút gọn thẳng ở kế hoạch: `design/04-runtime-durability.md`
§8.2 giờ chỉ còn bốn span, năm cái kia gộp attribute vào span còn sống gần nhất — để
LÚC OTel thật được xây, xây đúng bốn ngay từ đầu.

> **K-11 ĐÃ SỬA.** Mặc định `end_strategy` đã là `"graceful"` từ trước (không cần đổi).
> Giá trị thứ ba đổi tên từ `"exhaustive"` gốc PydanticAI thành `"complete"` — `"exhaustive"`
> đã là tên một **parallel mode** khác hẳn của chính pydantic-ai trong `03 §3.2`, dùng lại
> cho một trục không liên quan là tự tạo va chạm từ vựng trong cùng bản thiết kế. Xoá khỏi
> ví dụ Mức 3 (`01 §2`) — nó không phải một trong bốn khái niệm còn thiếu của cả ngành mà
> mục đó minh hoạ.

> **K-12 ĐÃ SỬA.** `03 §5.2` hạ `McpServerPolicy.identity` từ `ServerIdentity{label,
> fingerprint}` xuống chỉ `ServerLabel` (chuỗi) cho v1 — `fingerprint` chưa chốt được định
> dạng (không tương đương SPKI cho MCP stdio, đã ghi ở *Chưa đủ evidence*), một trường
> không chốt được định dạng chưa nên vào kiểu công khai. Cái giá nói thẳng: M-4 (`03 §5.3`)
> v1 KHÔNG chặn được label bị trỏ lại sang endpoint khác — đúng gap S-7 đã nêu, cùng lý do
> hoãn (`## 1.1`). Thêm lại `ServerIdentity`/`fingerprint` khi quan sát được một lần
> re-pointing thật.

> **K-13 SỬA MỘT PHẦN.** Ba trong bốn va chạm mã có phạm vi gọn, chỉ nằm trong đúng hai
> tệp `design/*.md`, không đụng `src/harness/` hay `docs/*.md` — đổi tên an toàn: `03 §6.3`
> bốn luật cancel `C-1…4` → `CAN-1…4`; `05 §A.1` bốn bất biến cost `C-1…4` → `COST-1…4`;
> `05` "Luật đọc" (memory) `R-1…3` → `MEM-R1…3`, giữ nguyên `00-foundation.md §5 R-1…4` (toàn
> cục, không đổi). Còn lại KHÔNG sửa, và hoá ra RỘNG hơn K-13 mô tả: va chạm `P-` không chỉ
> hai namespace (`01` plugin, `02` policy) mà BA — `docs/09-testing.md` có hẳn một series
> `P-1…P-10` (property test ID) được `docs/00-council.md`, `docs/08-poka-yoke.md`,
> `docs/09-testing.md`, `docs/11-implementation-plan.md`, `docs/12-decision-logs.md`,
> `docs/13-risk-register.md`, `docs/14-validation-plan.md` tham chiếu vài chục lần — đó rõ
> ràng là namespace THẬT, đang sống, không phải bản nháp. Và `02`'s `P-1…4` (policy) được
> chính `src/harness/policy/{base,engine,builtin}.py` trích trong comment. Đổi bất kỳ series
> nào trong ba cũng kéo theo sửa code hoặc sửa 7+ tệp `docs/` — vượt xa phạm vi một lần dọn
> `design/*.md`. Tương tự, va chạm `I-1`/`I-2` (`04`, "gate là tiền điều kiện tại chỗ tiêu
> thụ" — invariant chính bản thân `04` gọi là "bất biến THAY THẾ I-1", tự thú đang dùng lại
> số của `03 §4.4`'s I-1 khác hẳn) và `I-3` (`05`, pairing tool_call/tool_result) đều đã là
> từ vựng sống trong `dispatch.py`, `lg/runtime.py`, `context/window.py`, và nhiều
> `tests/test_attack_*.py` của CHÍNH bản vá phiên này — đổi sẽ là một PR tách riêng, không
> phải phần của lượt dọn KISS này. Để nguyên, ghi lại đây cho lần sau.
>
> **K-22 ĐÃ SỬA.** `01 §1` tuyên bố "toàn bộ bề mặt là 14 tên, một import" rồi chính ví dụ
> Mức 3 trong CÙNG tệp `import` 20 tên từ 4 module — tự mâu thuẫn. Sửa bằng cách nói đúng
> phạm vi: 14 tên là bề mặt TỐI THIỂU (Mức 0–2), không phải toàn bộ; production (Mức 3) cần
> tới 20 tên qua tối đa 3 submodule. Kiểm thêm hai phát hiện phụ của K-22 trên `design/*.md`
> hôm nay: `Workspace` (định nghĩa ở `03 §5`) và `Event` (định nghĩa ở `00`) — CẢ HAI đã
> ĐƯỢC ĐỊNH NGHĨA, khác lúc K-22 viết; phần đó của K-22 đã lỗi thời, không cần sửa thêm.
>
> **K-28 ĐÃ SỬA.** `01 §1.1` tuyên bố interface mẫu thiếu "năm thứ", liệt kê bốn cái đã có
> "ở trên" — nhưng không có kiểu `Session` nào trong `01`/`02`/`04`/`05`, chỉ có `run_id`.
> Sửa câu thành "ba cái đầu ở trên" (streaming, cancellation, approval round-trip) và nói rõ
> `session` là phạm vi của **tầng service** bọc quanh harness, không phải của chính harness —
> trung thực thay vì ngầm nhận có cái không có.

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

1. **Nhóm 1 (S-11, S-12, S-17, S-18, S-21…S-29) — XONG, cả 13.** Cùng kỷ luật verify-trước
   với mọi mục trước: sáu sửa code thật (S-21/22/24/25/27/29 — kiểm khi làm S-24/S-27 lộ
   ra hai lỗi RỘNG hơn văn bản gốc: `EventBus` dùng chung xuyên thread ở S-24, nhánh
   confidentiality của S-27 sống dù kịch bản gốc đã bị chặn từ chỗ khác); năm kiểm rồi xác
   nhận lỗi thời/đã đúng, không cần sửa (S-12, S-17 gộp vào hoãn MCP, S-23, S-26, S-28);
   S-18 sửa bằng tài liệu (không có cách sửa ở tầng `Policy` thuần); S-11 sửa được phần
   landing được (`Approval` — channel tuỳ chọn cho `approve=` báo actor thật), phần còn lại
   cần `AuthEvidence` — xem mục 3. Chi tiết từng mã ở `## 1.1`/`## 1.2`.
2. **S-7, S-8, S-9, S-10, S-17** — landing cùng lúc với khi tích hợp MCP thật được xây,
   không trước (xem `## 1.1`).
3. **`AuthEvidence` cho S-11** — mô hình xác thực người duyệt thật (chữ ký kênh,
   `channel_message_id`) để chặn một callback TỰ KHAI GIAN danh tính, không chỉ mở kênh
   báo tự nguyện như bản vá vừa landing. Cần thiết kế riêng, chưa bắt đầu.
4. **K-7, K-9, K-10, K-23 — XONG.** Kiểm lại trên code hiện tại trước khi cắt (cùng kỷ
   luật với các mục security): K-9 cắt thật (`Result.raise_for_status()`); K-7 và K-23
   hoá ra đã lỗi thời — cắt sẽ phá một consumer thật (K-7) hoặc không có gì để cắt (K-23,
   phần lớn tunable của nó chưa từng được xây); K-10 không có code, chỉ rút gọn kế hoạch
   trong design doc. Xem `## 1.3`.
5. **`Ledger.void()`, S-16/S-19/S-3 trên backend cổ điển, S-15 trên backend cổ điển —
   ĐÃ KIỂM, KHÔNG THÊM.** Cả ba được xét kỹ; xem `## 1.1` cho từng cái.
6. **K-11, K-12, K-22, K-28 — XONG; K-13 — MỘT PHẦN.** Toàn bộ văn xuôi trong `design/*.md`,
   không đụng `src/harness/`. K-13 chỉ đổi được ba trong bốn va chạm mã (phạm vi gọn, hai
   tệp); va chạm `P-`/`I-` để nguyên vì hoá ra RỘNG hơn ước lượng ban đầu — đụng cả code lẫn
   `docs/*.md` sống, xứng một lượt riêng chứ không phải phụ lục của lượt này. Xem `## 1.3`.
7. **Tiếp tục viết code.** S-16/S-19/S-3 (cả hai nguồn)/S-6/S-11/S-14/S-15/S-13/S-21/S-22/
   S-24/S-25/S-27/S-29 đã vào `src/harness/`, K-9 cắt khỏi bề mặt công khai — 359 test
   xanh, mỗi cơ chế chính có mutation test đi kèm. Vẫn còn phát hiện review chưa chạm tới
   code (S-7…S-10/S-17 hoãn có chủ ý, `AuthEvidence` của S-11, phần P-/I- của K-13), và
   nghiên cứu của chính dự án đo được **23 vòng review tìm 20 lỗi và 0
   lỗi bảo mật; 16 vòng chạy tìm 38+ lỗi và 4 lỗi bảo mật** — bản thiết kế là sản phẩm của
   review, nó sẽ sai ở những chỗ chỉ có chạy mới tìm ra.
