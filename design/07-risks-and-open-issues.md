# Rủi ro, đánh đổi, và vấn đề còn mở

Tệp này tồn tại vì luật §45 của nghiên cứu: **thà nói "Chưa đủ evidence" còn hơn đoán.**
Áp lên chính bản thiết kế, nó có nghĩa: mọi thứ chưa chắc phải nằm ở đây, không nằm rải rác
trong sáu tệp kia dưới dạng câu văn tự tin.

---

## 1. Phát hiện review CHƯA được sửa

Hai vòng review đối kháng cho **58 phát hiện**. Đã sửa trước khi tệp này bắt đầu theo dõi:
5 lỗi chặn phát hành và 16 lỗi nhất quán. **Một lỗi chặn phát hành thứ sáu (S-20) đã lọt
qua đợt đó** — kiểm lại toàn bộ S-1…S-29 với code hôm nay (không phải chỉ nhóm đang được
sửa từng đợt) tìm thấy nó vẫn sống. **S-20 nay đã sửa** (`EventKind.BUDGET_UNLIMITED`,
`docs/12-decision-logs.md` ADR-041) — xem callout ở `## 1.1` và
`design/08-roadmap-and-release-plan.md §1`. **Khi thực hiện M6 (roadmap), chaos testing
(T-6.4) lộ ra một lỗi nghiêm trọng KHÔNG thuộc S-1…S-29 gốc — N-4 ở `## 1.5`: lỗi
provider (timeout, rate limit) crash thẳng ra ngoài `try_run()`/`graph.invoke()`, cả hai
backend, không có `except` nào bắt — nay đã sửa.**
**Còn lại dưới đây chưa sửa** — liệt kê đầy đủ, vì một danh sách rủi ro chỉ có giá trị khi
nó thành thật.

### 1.1 Bảo mật — nghiêm trọng

> **S-20 ĐÃ SỬA.** `Budget.usd: Decimal | None` (`budget/ledger.py`) vẫn cho `None` —
> việc bắt buộc "phải có trục tiền" vẫn chỉ nằm trong `Budget.parse()` (con đường qua
> CHUỖI, `budget="$0.05"`), và `Budget(usd=None, ...)` dựng trực tiếp vẫn không bị
> `ConfigError` nào chặn — **đúng như thiết kế**: `docs/04-interfaces.md`/`docs/07-cost.md`
> đã công bố `usd=None` là escape hatch có chủ đích (provider miễn phí/local, IDL-36),
> không phải lỗi cần cấm. Cái ĐÃ sửa là phần hai của lời hứa tài liệu — "emits a
> `budget.unlimited` warning event on every run... Unlimited is possible; it is not
> silent" — chưa từng được cài đặt trước bản vá này. `EventKind.BUDGET_UNLIMITED`
> (`docs/12-decision-logs.md` ADR-041) nay phát đúng một lần mỗi run/thread ngay sau
> `RUN_STARTED`, ở cả hai backend, khi `budget.usd is None` — khoá bằng
> `tests/test_attack_s20.py` (6 test, gồm mutation test). Đây từng là phát hiện "chặn
> phát hành" DUY NHẤT trong toàn bộ 58 phát hiện còn sống chưa sửa — xem
> `design/08-roadmap-and-release-plan.md §1` cho chi tiết bản sửa, gồm cả việc bản đề
> xuất đầu tiên (chặn construction) bị bác bỏ vì sai với hợp đồng đã công bố.

> **S-1 ĐÃ KIỂM — LỖI THỜI, KHÔNG CẦN SỬA.** Cả hai nhánh của kịch bản gốc dựa vào cơ chế
> không tồn tại: (a) `Quarantine` — K-1 đã cắt, `0 caller`; (b) chiến lược nén
> `SummarizeOldPrefix` (gọi model để tóm tắt, ngoài node `model` nên thiếu `reserve()`) —
> chiến lược nén THẬT SỰ được xây là `ClearToolResults` (`context/window.py`, hằng số
> `CLEARED`), chỉ xoá nội dung, không gọi model nào. Không có lời gọi model nào ngoài
> node `model` để mà thiếu reservation.

> **S-4 — RE-VERIFIED trên `idempotency.py` thật (ADR-057), đóng — nhưng bằng một phát
> hiện KHÁC câu chữ gốc, không phải xác nhận câu chữ gốc.** `IdempotencyMode`/
> `in_flight`/`call_with_effect_log` (giao thức ba pha `03 §4.4`) đúng là không tồn tại —
> T-6.1 xây một cơ chế đơn giản hơn (`execute_once`: get → miss → chạy → put, không có
> claim `in_flight`, không `UNIQUE INDEX`). Kiểm trực tiếp thay vì chỉ đọc lại tài liệu thì
> thấy cơ chế đơn giản hơn đó có một race THẬT mà bản ba pha vốn được thiết kế để chặn: hai
> `execute_once` đua nhau cùng key có thể cùng miss `get()`, cùng chạy `fn()` — nhân đôi
> side effect cho `write`/`danger`, đúng lỗi T-6.1 tồn tại để ngăn. **Đã sửa**: khoá
> `asyncio.Lock` theo từng key, trong một tiến trình — yếu hơn giao thức `03 §4.4` (không
> chặn được hai tiến trình/replica đua nhau cùng key, cần `Store` có CAS thật, chưa xây),
> nhưng đóng đúng cửa sổ TOCTOU trong phạm vi nó tuyên bố. Đồng thời: `idempotency.py`'s
> docstring tự nói "M9's Service API is the first real source of [a key]" — M9 nay đã
> tồn tại và KHÔNG gọi module này. Đã gắn: `POST /v1/runs` nhận `idempotency_key`, một
> client retry (do timeout của chính họ) nhận lại ĐÚNG run cũ thay vì khởi run thứ hai —
> xem `harness/server.py::RunStore._by_key`, cố ý KHÔNG dùng `execute_once` (nó nhắm một
> `Store` bền vững, `RunStore` không có). `tests/test_m6_t61_idempotency.py::
> RaceClosedByLock`, `tests/test_m9_t92_t93_service.py`'s bốn case idempotency-key.

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
> **Va chạm namespace phát hiện khi làm ADR-059, chưa dọn (thêm vào danh sách K-13 kiểu
> cho lần sau, không sửa ngay ở đây để tránh phình phạm vi lượt này):** mã `S-14` này
> (Ledger, từ hai vòng review gốc) trùng số với `docs/17-research-alignment.md §2.2`'s
> `S-14` (backpressure của Service API's SSE, ADR-059) — hai phát hiện HOÀN TOÀN khác
> nhau, chỉ trùng số vì hai review khác thời điểm đều đánh số từ S-1. Đúng loại va chạm
> K-13 đã dọn cho `P-`/`I-`; `S-` chưa được rà theo cách đó.
>
> **S-14 (Ledger, mã này) ĐÃ SỬA MỘT PHẦN.** `Ledger._committed()` mới (`budget/ledger.py`) cộng mọi
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

> **S-11 — ĐÃ SỬA thêm một phần nữa (ADR-060, `AuthEvidence`), phần lõi crypto XONG,
> phần "harness tự verify hộ" CỐ Ý KHÔNG LÀM.** `policy/auth_evidence.py`:
> `AuthEvidence`/`sign_evidence`/`verify_auth_evidence` (HMAC-SHA256,
> `hmac.compare_digest` hằng thời gian, cửa sổ chống replay). `Actor.verified: bool =
> False`, `Approval.evidence: AuthEvidence | None`. Callback tự verify bằng chứng từ kênh
> CỦA NÓ (Slack, OAuth, …) rồi mới tự báo `verified=True` — harness không wire việc verify
> vào `PolicyEngine.resolve()`, vì harness không biết gì về bí mật/hình dạng của một kênh
> mà `src/harness/` không hề tích hợp (0 implementer, đúng loại K-5 đã cắt). Phần CÒN LẠI,
> vẫn đúng như review gốc nói: không gì ngăn một callback tự đặt `verified=True` mà không
> gọi `verify_auth_evidence()` — đó là giới hạn cố hữu của bất kỳ cơ chế approval nào kết
> thúc ở code của operator, không phải lỗ hổng thêm bản vá này tạo ra.
> `tests/test_s11_auth_evidence.py` (14 test): round-trip, sai secret, payload/signature
> bị sửa, evidence của actor A dùng cho actor B (đúng kịch bản S-11 lo) đều thất bại đúng
> cách; cửa sổ replay chặn cả evidence quá cũ lẫn "ký trong tương lai."
>
> **Phần landing trước đó (T-8.2, giữ nguyên).** Chữ ký thật
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
> trước — mutation khôi phục `resolve()` cũ xác nhận 4 test đỏ ngay. Lúc đó vẫn không chặn
> được callback TỰ KHAI GIAN danh tính — `AuthEvidence` (đoạn trên, ADR-060) là phần sửa
> đó, sau này.

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

> **S-7, S-8, S-9, S-10, S-17 — ĐÃ SỬA, cùng lúc với MCP thật (T-9.1, ADR-054).** Đúng như
> dự tính khi hoãn: cả năm đóng bằng một lượt xây `src/harness/mcp.py`, không phải năm bản
> vá rời. `ToolSpec.server`/`Scope.server` (đã có kiểu từ trước, chưa từng được `matches()`
> so khớp — lỗ hổng đó tự nó là một dạng S-9/S-10, sửa luôn) nay thật sự khoá grant theo
> server (M-4). `classify_mcp_tool` áp đúng bốn luật M-1…M-4 của `design/03 §5.3`: server
> không trusted thì hint không tham gia phân loại (S-7/S-8), trusted thì hint làm mặc định
> với đúng bug Microsoft `is True` (không phải `!= False`) đã tránh. Rug-pull (S-9) —
> `McpBinding.check_for_rug_pull()` — CHỌN KHÁC bản thiết kế gốc: fail-closed
> (`McpRugPullError`) thay vì "coi là tool mới, phân loại lại", vì `Scope` khoá theo TÊN
> tool chứ không theo fingerprint, nên phân loại lại âm thầm dưới cùng một tên có grant
> sống tự nó có thể là một confused-deputy mới — xem ADR-054 cho lý do đầy đủ. S-17 (
> injection qua `description`) sửa bằng CÙNG mẫu `max_result_tokens` đã dùng cho kết quả
> tool — bound kích thước (`MAX_MCP_DESCRIPTION_CHARS`), không lọc nội dung (E7.1 đã bác
> bỏ detector) — và nói thẳng phần không đóng được, đúng kỷ luật S-18: một description độc
> vẫn có thể dụ model gọi tool khác, phòng thủ CẤU TRÚC (không phải nội dung) vẫn là
> `DANGER` mặc định cho server không trusted, buộc qua `ASK`. `ServerLabel` (chuỗi), không
> phải `ServerIdentity{label,fingerprint}` — giữ nguyên quyết định K-12: `fingerprint` cho
> MCP stdio chưa có định dạng chốt được. `tests/test_m9_t91_mcp.py` (20 test) khoá cả bốn
> luật, ranh giới confused-deputy hai chiều, và transport/rug-pull chạy qua một tiến trình
> con THẬT (`tests/fake_mcp_server.py`, JSON-RPC qua stdio thật), không mock.

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
thời/đã đúng, không cần sửa). S-11 (bảng `## 1.1`) nay ĐÃ XONG phần lõi `AuthEvidence`
(ADR-060) — phần cố ý không làm (harness tự verify hộ) đã ghi rõ lý do ngay tại đó, không
phải một việc còn treo.

> **S-21 ĐÃ SỬA.** `Ledger.snapshot()` thiếu `blocked` — một ledger `_blocked=True` (spend
> vượt trần cứng) phục hồi từ checkpoint về `_blocked=False`, tự "quên" nó đã bị chặn.
> Thêm `blocked` vào cả `snapshot()`/`restore()`. `tests/test_attack_s21_s22.py`.

> **S-22 ĐÃ SỬA.** `size_call()`/`reserve()` chỉ định giá theo `input_per_mtok`, trong khi
> `settle()` có thể tính theo `cache_write_per_mtok` (`_p()` trong `models/pricing.py`:
> luôn đắt hơn đúng 25%, cố định — không phải số đo). Mọi cuộc gọi THẬT SỰ ghi cache bị
> ước lượng thấp hơn thực tế có hệ thống. Định giá lại theo mức TỆ NHẤT
> (`cache_write_per_mtok`) ở cả `reserve()` lẫn nhánh `hard_max_input`.
> `tests/test_attack_s21_s22.py`.

> **S-23 RE-VERIFIED sau khi MCP thật tồn tại (ADR-057) — điều kiện đóng cũ ("name không
> phải input MCP điều khiển được") đã hết hiệu lực từ khi T-9.1 landing, nên phải kiểm
> lại, không được để mặc định "chắc vẫn ổn."** Kiểm bằng CÁCH DỰNG (không phải đọc), đúng
> luật IDL-34: `dispatch.py`'s dedup key `f"{name}:{canonical(args)}"` — thử ép một tên
> tool MCP chứa `:` để tạo va chạm với key của một tool khác, không dựng được, vì
> `canonical(args)` luôn là một giá trị JSON hoàn chỉnh, tự đóng gói; tách chuỗi ghép tại
> bất kỳ `:` nào NẰM TRONG khối JSON đó luôn để lại một dấu đóng ngoặc dư từ tầng bao
> quanh, nên phần còn lại không bao giờ tự nó là một giá trị JSON hợp lệ thứ hai — không
> có hai cặp `(name, args)` khác nhau nào tạo cùng key qua đường ký tự `:` trong tên. **Vẫn
> sửa, vì lý do robust khác, không phải vì tìm ra va chạm:** `classify_mcp_tool` giờ chạy
> mọi tên tool MCP qua `slug()` (cùng hàm `as_tool()` đã dùng) trước khi vào
> `ToolSpec`/prompt của model — một tên chứa khoảng trắng, dấu câu, hay không phải ASCII
> không còn lọt nguyên văn tới model, và không tên nào có thể chứa `:` sau khi qua `slug()`
> — đóng luôn câu hỏi ở tầng HÌNH DẠNG, không chỉ dựa vào chứng minh JSON. Tên gọi
> `tools/call` thật (upstream) không đổi — `bind_mcp_server`'s closure vẫn dùng tên gốc,
> chỉ tên model THẤY bị chuẩn hoá. `tests/test_m9_t91_mcp.py::
> test_malformed_server_name_is_normalized_not_rejected`.

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

> **K-13 ĐÃ SỬA — cả phần P- lẫn phần I-.** Bước trước (đợt trước bản vá này) đổi an toàn
> bốn luật cancel `03 §6.3` `C-1…4` → `CAN-1…4`, bốn bất biến cost `05 §A.1` `C-1…4` →
> `COST-1…4`, "Luật đọc" memory `05` `R-1…3` → `MEM-R1…3` (giữ nguyên `00-foundation.md §5
> R-1…4`, toàn cục, không đổi) — không đụng code. Phần còn lại (`P-`/`I-`), hoá ra rộng hơn
> ước lượng ban đầu, nay đã dọn:
>
> **`P-`.** Đối chiếu lại toàn bộ trước khi đổi: `docs/09-testing.md`'s `P-1…P-10`
> (property test ID, tham chiếu vài chục lần từ `docs/00`, `docs/08`, `docs/11`, `docs/12`,
> `docs/13`, `docs/14`) là namespace THẬT, đang sống — **không đổi**. `00-foundation.md
> §3.1`'s `P-2` ("thêm policy không bao giờ nới lỏng") mang ĐÚNG nghĩa với `docs/09`'s
> `P-2` và được `01`/`02` dùng lại nhất quán — **không đổi**. Va chạm THẬT nằm ở bốn chỗ có
> nghĩa KHÁC nhau trùng số: `02 §1.2`'s `P-1`/`P-3`/`P-4` (policy engine: sàn theo
> `Effect`, fail-closed, `Policy.check` thuần) đổi thành **`POL-1`/`POL-3`/`POL-4`**; `01
> §2`'s `P-3` (plugin chỉ làm yếu đi tập tác dụng phụ — nghĩa khác hẳn `02`'s `P-3`/fail-
> closed) đổi thành **`PLG-1`**, kéo theo `06-poka-yoke-matrix.md` dòng 27 (cùng tham
> chiếu). `02`'s `P-4` (Policy.check thuần) đã lọt ra ngoài `design/*.md` từ trước — trích
> trong comment `src/harness/policy/builtin.py` (`TaintPolicy`, `EgressPolicy`) VÀ trong
> `docs/06-safety.md` (link nhầm sang `docs/02-architecture.md`, tệp không có `P-` nào) VÀ
> trong module docstring của `tests/test_attack_s18.py` — nghĩa là bản test THẬT đang chạy
> mang một ID collide với chính `docs/09`'s `P-4` (schema validation, nghĩa hoàn toàn khác).
> Cả bốn chỗ đã đổi sang `POL-4`; hành vi/test không đổi, chỉ đổi tên trong docstring và
> comment.
>
> **`I-`.** `02-architecture.md §3`'s `I-1`/`I-2`/`I-3` (reservation trước model call, gate
> trước tool exec, pairing `tool_use`/`tool_result`) là namespace THẬT, sống trong
> `dispatch.py`, `lg/runtime.py`, `context/window.py`, `run.py` — **không đổi**. `04`'s "bất
> biến THAY THẾ I-1"/"I-2" chỉ diễn giải lại đúng hai bất biến đó cho backend graph, **không
> đổi nghĩa nên không cần đổi tên** — nhận định trước đây rằng đây là một va chạm cần sửa là
> SAI, đã kiểm lại. `05 §B.1`'s `I-3` cũng đúng nghĩa với `02`'s `I-3` — không đổi. Va chạm
> THẬT DUY NHẤT: `03 §4.4`'s "bất biến I-1 (thứ tự)" — effect log ghi trước khi tool chạy,
> checkpoint ghi sau — là một bất biến IDEMPOTENCY, khác hẳn `02`'s `I-1` (budget
> reservation), trùng số. Không có caller nào trong `src/harness/idempotency.py` trích số
> này (kiểm bằng grep trước khi đổi) nên đổi an toàn, không đụng code: **`IDEM-1`**.
>
> Bốn tệp `design/*.md` (`01`, `02`, `03`, `06`), một tệp `docs/*.md` (`06-safety.md`), một
> tệp code (`src/harness/policy/builtin.py`, hai docstring) và một tệp test
> (`tests/test_attack_s18.py`, hai docstring) đổi tên — không tệp nào đổi HÀNH VI, chỉ đổi
> nhãn. `pytest` (khi có `pytest` để chạy) không cần chạy lại vì không assertion nào so
> khớp chuỗi `"P-4"`/`"I-1"` trong code — kiểm bằng `grep -rn "P-4\|I-1"
> src/harness/tests` sau khi đổi xác nhận không còn chuỗi cũ nào sót.
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

### 1.5 Phát hiện mới, phát sinh khi thực hiện roadmap M6…M10

Không thuộc hai vòng review gốc (S-1…S-29/K-1…K-29) — tìm thấy khi làm M6, đánh số riêng
`N-` để không va chạm với `S-`/`K-` đã có (đúng bài học K-13 đang chờ dọn: một namespace
mới không nên tự tạo thêm va chạm).

> **N-1 ĐÃ SỬA — LangGraph backend không thực thi timeout per-tool nào.** Phát hiện khi
> cổng T-6.3 (retry theo effect class) sang `lg/runtime.py::_run_tools`: hàm này gọi
> `asyncio.run(spec.fn(**args))` trực tiếp, không có `async with asyncio.timeout(...)`
> nào bọc quanh — khác hẳn `dispatch.py::_invoke` (classic loop), nơi
> `self._e._l.tool_timeout(spec.timeout_s)` luôn được áp (Round 23). Một tool `read`
> chạy mãi mãi (vd. một HTTP call treo, không có timeout riêng của thư viện HTTP đó) sẽ
> treo cả node graph vô thời hạn trên backend LangGraph — chỉ có wall-clock CẤP RUN mới
> chặn được (`budget_gate`'s `remaining_wall_clock() <= 0`), và cấp đó chỉ kiểm ĐẦU mỗi
> bước, không kiểm GIỮA một lời gọi tool đang chạy. **Sửa: `led.tool_timeout(spec.
> timeout_s)` bọc đúng chỗ đường idempotency (ADR-064) đã đi qua, cùng thông điệp hai
> nhánh `dispatch.py` đã có** (ADR-068, `docs/12`). Xác nhận đây là hồi quy thật, không
> phải lý thuyết: revert bản vá rồi chạy test mới — cả ba test đỏ trong 16 giây (không
> treo vô hạn — `asyncio.sleep(5.0)` luôn trả về sau cùng, đó chính là lý do timeout cần
> tồn tại), phục hồi bản vá thì cả ba xanh dưới một giây.
> `tests/test_n1_per_tool_timeout.py`.

> **N-2 ĐÃ SỬA — classic loop's `try_run()` từng raise `ToolContractError` KHÔNG BỊ BẮT
> khi model trả rác khớp sai `returns=`.** Phát hiện khi làm T-6.4 (chaos test "model trả
> rác"): `_parse_returns()` (`run.py`) được gọi SAU khi `RUN_FINISHED` đã phát với
> `stop=COMPLETED`, và raise của nó (`ToolContractError`) truyền thẳng ra ngoài
> `atry_run()`/`try_run()` — vi phạm đúng hợp đồng đã công bố (`docs/03-public-api.md`:
> "Never raises for run outcomes"; IDL-11: "run() raises, try_run() returns"). Kiểm trực
> tiếp xác nhận: `Agent(returns=Ans).try_run(...)` với model trả `"not json"` raise thẳng
> `ToolContractError`, không trả `Result`. Sửa: gọi `_parse_returns` TRƯỚC khi phát
> `RUN_FINISHED`, bắt `ToolContractError` và hạ xuống `Result(stop_reason=ERROR, ...)`
> bình thường — model trả rác giờ là một OUTCOME của run, không phải một crash. Khoá
> bằng `tests/test_m6_t64_chaos.py`.

> **N-3 — LangGraph backend không hỗ trợ `returns=` (không parse, không validate,
> `Result.value` luôn `None`).** Phát hiện khi cổng bản sửa N-2 sang LangGraph để kiểm
> parity: `build_agent()` (`lg/__init__.py`) **không có tham số `returns=` nào cả**, và
> `lg/runtime.py` không có lời gọi `_parse_returns`/tương đương ở đâu —
> `docs/03-public-api.md` không ghi `returns=` là "classic-loop only" ở đâu cả, nên đây
> là một khoảng trống parity thật, không phải giới hạn có tài liệu. Chưa sửa — ngoài
> phạm vi T-6.4 (đó là chaos testing, không phải xây tính năng mới cho backend kia); cần
> một `finish` node mới đọc `state["messages"][-1]` và validate, cộng quyết định graph
> shape (một cạnh lỗi riêng, hay gộp vào `FINISH` hiện có). Ghi lại để không mất, không
> sửa vội.

> **N-4 ĐÃ SỬA — lỗi provider (`ProviderTimeout`/`ProviderRateLimited`/bất kỳ raise nào
> từ `complete()`/`invoke()`) crash thẳng ra ngoài `try_run()`/`graph.invoke()`, CẢ HAI
> backend.** Phát hiện khi viết kịch bản chaos "provider timeout" cho T-6.4: kiểm trực
> tiếp, `Agent(provider=<một provider tự raise TimeoutError>).try_run(...)` raise thẳng
> `TimeoutError` ra ngoài, không trả `Result` nào — vi phạm đúng hợp đồng `try_run()`
> "never raises for run outcomes" (docs/03), giống hệt lớp lỗi N-2 vừa sửa nhưng ở một
> chỗ khác. `src/harness/models/anthropic.py` MAP lỗi SDK thật thành
> `ProviderError`/`ProviderTimeout`/`ProviderRateLimited`/... (`errors.py`) — nhưng
> **không có `except` nào cho các lớp đó ở bất kỳ đâu trong toàn bộ `src/harness/`**
> (kiểm bằng grep, xác nhận trước khi sửa). Nghĩa là MỌI lần gọi provider thật gặp rate
> limit hay timeout tạm thời sẽ crash chương trình gọi nó thay vì trả về một `Result`
> lỗi — nghiêm trọng hơn N-2 vì đây là đường đi PHỔ BIẾN nhất khi chạy với provider thật
> (khác `returns=`, một tính năng opt-in). Sửa cả hai backend: `run.py`'s vòng lặp bọc
> `self._p.complete(...)` trong try/except, hạ xuống `Result(stop_reason=ERROR, ...)`;
> `lg/runtime.py::call_model` bọc `self._model.invoke(...)` tương tự, trả
> `{"stop_reason": "error", ...}` cho `_after_model` route sang `FINISH`. `retryable=`
> trên sự kiện `error.raised` được gắn đúng theo loại lỗi (`ProviderTimeout`/
> `ProviderRateLimited`/`ProviderUnavailable`/`TimeoutError` → `True`) dù CHƯA có gì tự
> động retry ở tầng model-call (chỉ ghi lại cho audit — đúng khuôn `EFFECT_PROFILES.
> retryable` tồn tại từ Round 5 trước khi T-6.3 mới có ai đọc nó). Khoá bằng
> `tests/test_m6_t64_chaos.py` (cả hai backend, cộng mutation test từng cái).

> **N-5 — retry cấp PROVIDER đã có tài liệu công bố nhưng chưa cài đặt.**
> `docs/10-observability-ops.md §3`'s bảng "Vendor → Harness → Retry" viết rõ:
> `ProviderRateLimited` retry "Yes, honoring Retry-After", `ProviderUnavailable` retry
> "Yes, exponential backoff", `ProviderTimeout` retry "Yes, exponential backoff". Phát
> hiện khi đọc docs/10 §2 để làm T-8.3 (OTel) — bảng này SÁT NGAY BÊN bảng OTel, đọc
> lướt qua ban đầu. N-4 (đã sửa) chỉ biến provider error thành `Result(ERROR)`, KHÔNG tự
> động retry gì cả — khác hẳn cam kết "Yes, honoring Retry-After" ở đây. Đây là một lớp
> retry RIÊNG với T-6.3 (retry cấp TOOL theo effect class, đã xây) — retry cấp MODEL CALL,
> chưa có gì. Chưa sửa — ngoài phạm vi T-8.3 (exporter, không phải retry policy); cần
> quyết định thiết kế riêng (đọc header `Retry-After` từ đâu khi `ProviderError` không
> mang nó hôm nay — `errors.py`'s `ProviderRateLimited` không có trường đó) trước khi cài.

> **N-6 ĐÃ SỬA — `model.response` event thiếu `usage`/`latency_ms` so với `docs/05` tự
> hứa.** `docs/05-data-and-state.md §1`'s bảng taxonomy ghi `model.response` mang
> `stop_reason, usage{in,out,cache_read,cache_write}, cost_usd, latency_ms` — code thật
> (`run.py`, `lg/runtime.py::call_model`) chỉ emit `stop_reason`/`cost_usd`. Phát hiện khi
> viết `OtelExporter` (T-8.3): `gen_ai.usage.output_tokens`/`harness.cache_read_tokens`
> (docs/10 §2's mapping) không bao giờ có dữ liệu để đọc — không phải lỗi của exporter,
> exporter chỉ đọc đúng những gì event mang. **Sửa: `input_tokens`/`output_tokens`/
> `cache_read_tokens`/`cache_write_tokens`/`latency_ms` (tên PHẲNG, khớp đúng rename table
> `OtelExporter` đã viết sẵn, không phải `usage{...}` lồng — sửa lại đúng `docs/05`'s
> bảng theo hình dạng đó thay vì đổi cả exporter) nay gắn vào `emit(MODEL_RESPONSE, ...)`
> ở cả hai backend** (ADR-067, `docs/12`). `tests/test_n6_model_response_usage.py` khoá
> cả bốn trục usage + latency trên cả hai backend, và khoá luôn hệ quả: span `gen_ai.chat`
> nay mang dữ liệu thật, `harness.cache.hit_ratio` nay thật sự ghi được điểm dữ liệu đầu
> tiên của nó.

> **N-7 ĐÃ SỬA — `Agent.with_()` âm thầm làm mất `transcript`/`exporters`/
> `accepts_tainted`/`sensitive`, MỌI lần gọi.** Phát hiện khi viết T-8.5
> (`agent.stream()`, dùng `with_()` để thêm một exporter riêng cho lần gọi đó): test
> transcript của `stream()` fail vì agent phái sinh có `transcript=None` dù agent gốc có
> đặt. Kiểm trực tiếp xác nhận: `Agent(transcript=..., accepts_tainted=[...]).with_(name=
> "X")` trả về agent với `transcript=None` VÀ `accepts_tainted` rỗng — không phải lỗi
> riêng của `stream()`, `with_()`'s base dict đơn giản là THIẾU bốn trường này từ trước,
> ảnh hưởng MỌI lời gọi `with_()`, không chỉ khi caller đụng tới một trong bốn trường đó.
> `accepts_tainted`/`sensitive` thiếu vì một lý do sâu hơn: chúng không hề là attribute
> đọc lại được (`self.accepts_tainted` không tồn tại) — `__init__` gộp chúng vào
> `self._grants` (frozenset) rồi không giữ bản gốc, nên `with_()` phải đọc từ
> `self._grants.accepts_tainted`/`.sensitive`, không phải từ tên trường trùng khớp như
> chín trường còn lại. Sửa: thêm `transcript`/`exporters` vào base dict, đọc
> `accepts_tainted`/`sensitive` từ `self._grants`. Khoá bằng
> `tests/test_n7_with_preserves_fields.py` (7 test, mutation-tested) độc lập với T-8.5 —
> đây là lỗi bất kỳ ai gọi `with_()` cũng gặp, không phải lỗi riêng của tính năng mới.

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
   S-18 sửa bằng tài liệu (không có cách sửa ở tầng `Policy` thuần); S-11 nay XONG cả phần
   landing (`Approval` — channel tuỳ chọn cho `approve=` báo actor thật) LẪN
   `AuthEvidence` (ADR-060, mục 3 dưới, phần lõi crypto — phần "harness tự verify hộ" cố ý
   không làm, lý do ghi tại `## 1.1`). Chi tiết từng mã ở `## 1.1`/`## 1.2`.
2. **S-7, S-8, S-9, S-10, S-17 — XONG (T-9.1, ADR-054).**
3. **`AuthEvidence` cho S-11 — XONG (ADR-060).** `policy/auth_evidence.py`:
   `sign_evidence`/`verify_auth_evidence` (HMAC-SHA256, chữ ký kênh, `channel_message_id`)
   để một callback tự xác minh rồi mới báo `Actor(verified=True)` — chặn được kịch bản cụ
   thể S-11 nêu (evidence của actor A dùng lại cho actor B). Không wire verify vào
   `PolicyEngine.resolve()` — quyết định có chủ đích, xem ADR-060.
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
