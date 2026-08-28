# HARNESS.md — Yêu cầu của chủ dự án

> Đây là **bản ghi yêu cầu**, không phải bản thiết kế. Nó trả lời câu hỏi *"chủ dự án
> đã yêu cầu những gì?"*, tách khỏi câu hỏi *"hội đồng đã thiết kế thế nào?"* — cái sau
> nằm ở [`docs/`](docs/).
>
> Mọi mục dưới đây trích từ chính lời chủ dự án trong quá trình làm việc. Chỗ nào là
> diễn giải của hội đồng đều được đánh dấu rõ. Cột **Ở đâu** trỏ tới nơi yêu cầu đó
> được đáp ứng, để một yêu cầu không thể "được đồng ý" mà không có địa chỉ.

**Nguồn:** toàn bộ hội thoại thiết kế, hội đồng chạy từ Round 0 đến Round 37.
**Phạm vi:** thư viện Python `harness`, nhánh `claude/ai-agent-harness-design-ti5vk3`.

---

## 0. Nhiệm vụ gốc

Dùng phương pháp **Personal Stack** lập một **Implementation Design Council** gồm các
chuyên gia phù hợp nhất, để biến ý tưởng và kiến trúc **AI Agent Harness** thành một
**Implementation Plan cực kỳ chi tiết, thực tế và Ready for Implement**.

> Đây **không phải** một buổi brainstorming và cũng không phải nhiệm vụ tạo ra một
> implementation plan trong một lần.

Hội đồng làm việc lặp nhiều vòng theo chu trình:

```
Understand → Design → Challenge → Find Gaps → Debate → Resolve → Refine → Validate → Repeat
```

và **chỉ được kết thúc** khi Implementation Plan đủ rõ để một engineering team bắt đầu
coding ngay mà không phải tự đưa ra quyết định kiến trúc hoặc kỹ thuật quan trọng nào.

---

## I. Năm nguyên tắc bất biến

> **Tuyệt đối không được đánh đổi các nguyên tắc dưới đây chỉ để làm implementation dễ hơn.**
> Nếu một thiết kế vi phạm một trong các nguyên tắc này, hội đồng phải phát hiện, phản
> biện và thiết kế lại.

### 1. Extensible / Pluginable

Có thể bổ sung, thay thế, mở rộng capability mà không phải sửa core một cách không cần thiết.

> **Pluginable không đồng nghĩa với "Everything is a Plugin".**

Hội đồng phải **tự xác định** đâu là plugin boundary hợp lý, đâu là core primitive, đâu
không nên biến thành plugin. Không được biến mọi thứ thành abstraction/plugin chỉ vì muốn
extensibility.

**Ở đâu:** [`docs/02-architecture.md §4`](docs/02-architecture.md) — phép thử plugin boundary
ba phần, rút 9 abstraction đề xuất xuống **5 seam**: Tool, ModelProvider, Store, Policy,
Exporter. Ba thứ được giữ trong core **chính vì** một bản thay thế có thể vô hiệu hoá một
nguyên tắc bất biến.

### 2. Cost Efficient

Câu hỏi phải hỏi liên tục:

> "Có cách nào đạt được cùng kết quả với ít token, ít model call, ít infrastructure và ít
> computation hơn không?"

Phải xem xét: model selection, model routing, small vs large model, caching, context
management, memory, RAG, tool usage, retry, parallel execution, batch processing, token
usage, cost monitoring, fallback strategy.

> **Cost phải là một architectural concern, không phải vấn đề tối ưu sau khi build xong.**

**Ở đâu:** [`docs/07-cost.md`](docs/07-cost.md) — trần ngân sách pre-flight (ADR-017),
cache-safety bằng cấu trúc (ADR-029, đo được 95.3%), ADR-026 sau khi SC-2 bị bác bỏ bằng
đo đạc.

### 3. Safe by Design

An toàn ngay từ thiết kế, không phải bổ sung security ở cuối. Tư duy bắt buộc:

> **Secure by Design + Fail Safe + Least Privilege + Defense in Depth**

Đặc biệt xem xét: agent safety, tool safety, plugin safety, prompt injection, data leakage,
unauthorized tool execution, malicious plugin, secret leakage, excessive permissions,
uncontrolled agent loops, resource exhaustion, supply-chain risks.

**Ở đâu:** [`docs/06-safety.md`](docs/06-safety.md) — threat model, taint lattice (ADR-011),
`Secret`, ranh giới tin cậy plugin, 21 kịch bản red-team chạy trong CI.

### 4. Intelligent

Agent thông minh **không** đồng nghĩa với luôn dùng model lớn hoặc reasoning phức tạp.

> **Maximum intelligence per unit of cost and latency.**

Harness cần chọn được cách xử lý phù hợp với từng nhiệm vụ thay vì luôn dùng cùng một
model hoặc cùng một workflow.

**Ở đâu:** [`docs/07-cost.md §6`](docs/07-cost.md).

### 5. Efficient

Hiệu quả về: latency, token usage, compute, memory, network, infrastructure, **developer
effort**, operational effort.

> Không chỉ tối ưu runtime. **Developer Experience cũng là một dạng efficiency.**

---

## II. Poka-Yoke — chống lỗi ngay từ thiết kế

> "Một trong những nguyên tắc quan trọng nhất."

Thay vì *"Developer phải nhớ làm đúng"*, ưu tiên:

> **"Thiết kế hệ thống để developer gần như không thể làm sai."**

Mỗi khi phát hiện một loại lỗi có thể xảy ra, phải đặt sáu câu hỏi:

1. Có thể loại bỏ khả năng xảy ra lỗi này bằng thiết kế không?
2. Có thể phát hiện lỗi ngay lập tức không?
3. Có thể tự động ngăn chặn lỗi không?
4. Có thể cung cấp safe default không?
5. Có thể biến lỗi runtime thành compile/configuration-time error không?
6. Có thể thiết kế API khiến cách sử dụng sai trở nên khó hoặc không thể thực hiện không?

Thứ tự ưu tiên:

```
Prevent → Detect Early → Fail Safe → Recover        (KHÔNG phải: Allow → Detect Later → Debug)
```

Áp dụng cho: API, configuration, plugin, agent definition, tool calling, memory, model
selection, workflow, security, deployment, testing, developer experience.

**Ở đâu:** [`docs/08-poka-yoke.md`](docs/08-poka-yoke.md) — 83 failure mode, mỗi mode có
một biện pháp ở mức thiết kế, xếp hạng theo thang Impossible > Import-time >
Construction-time > First-run > Loud warning > Documented.

---

## III. Engineering Principles

| Nguyên tắc | Yêu cầu |
|---|---|
| **SOLID** | Áp dụng **thực chất, không máy móc** |
| **CLEAN CODE** | Readable, understandable, maintainable, explicit, cohesive, low coupling |
| **KISS** | Nếu một giải pháp đơn giản giải quyết được vấn đề, **không được** chọn giải pháp phức tạp hơn |
| **NOT OVER-ENGINEER** | **Bắt buộc.** Không xây capability chỉ vì "có thể cần trong tương lai" |

Với NOT OVER-ENGINEER, phải phân biệt rõ bốn mức: **Required now** / **Required for
production** / **Useful later** / **Speculative**.

> Không biến future possibility thành current complexity.

---

## IV. Extreme Developer Experience

> "Đây là một yêu cầu **đặc biệt quan trọng**."

Mục tiêu:

> **Một học sinh 10 tuổi cũng có thể hiểu cách sử dụng Harness để xây dựng một Agent cơ bản.**

Điều này **không** có nghĩa architecture bên trong phải đơn giản như ứng dụng trẻ em:

> **Complexity inside, Simplicity outside.**

Tối ưu **Time to First Agent** và **Cognitive Load** xuống mức thấp nhất có thể.

Triết lý minh hoạ (nhưng **không được mặc định đây là thiết kế cuối cùng** — hội đồng phải
tự tìm ra DX/UX tốt nhất):

```
Create Agent → Give it a name → Tell it what to do → Give it capabilities → Run
```

### IV.a — Đính chính quan trọng của chủ dự án

Ở vòng 0 hội đồng lập luận rằng yêu cầu "10 tuổi" là bất khả thi. Chủ dự án bác bỏ:

> **"Tôi nói thêm là trẻ em 10 tuổi đã biết `pip install`, đã được học lập trình python
> cơ bản rồi."**

Yêu cầu này phải được hiểu **theo nghĩa đen**. Hội đồng đã mở lại vòng 13–16 và ADR-012.

**Ở đâu:** [`docs/15-first-agent.md`](docs/15-first-agent.md) — tài liệu hướng tới trẻ em,
đo được **lớp 4.2** theo thang Flesch–Kincaid, dùng làm đặc tả chạy được;
[`docs/16-sc1b-field-kit.md`](docs/16-sc1b-field-kit.md) — bộ công cụ khảo sát thật.

---

## V. Zero-to-Agent Experience

Thiết kế trải nghiệm liên tục:

> **Zero knowledge → First Agent → Useful Agent → Advanced Agent**

Phải xác định: mental model tối thiểu, API tối thiểu, configuration tối thiểu, default
behavior, **safe defaults**, convention over configuration, **progressive disclosure**.

Advanced capability chỉ xuất hiện khi người dùng thực sự cần. **Không bắt beginner phải
hiểu** LLM orchestration, agent runtime, context engineering, memory architecture, RAG,
tool protocol, model routing, multi-agent coordination — chỉ để tạo một Agent đơn giản.

**Ở đâu:** [`docs/03-public-api.md`](docs/03-public-api.md) — thang progressive disclosure;
[`examples/langgraph_quickstart.py`](examples/langgraph_quickstart.py) — năm bậc, mỗi bậc
thêm đúng một khái niệm.

---

## VI. Hội đồng phải tự phát hiện mọi vấn đề

Không chỉ giải quyết những gì chủ dự án đã nói. Phải chủ động tìm: architectural,
implementation, API, UX, security, performance, cost, scalability, testing, deployment,
operational, maintainability, migration, versioning, plugin-ecosystem, developer-onboarding
problems.

> **Nếu tôi có assumption chưa hợp lý, hãy phản biện thẳng thắn.**
> **Không cố bảo vệ ý tưởng của tôi. Mục tiêu là xây được hệ thống tốt nhất.**

---

## VII. Quy trình Iterative Council

Không được tạo Implementation Plan một lần rồi kết thúc.

| Vòng | Nội dung |
|---|---|
| **Round 0** | Understand — goals, constraints, requirements, NFR, principles, success criteria. Ambiguity quan trọng phải được giải quyết |
| **Round 1** | Initial Implementation Plan — chưa cần hoàn hảo, tạo baseline để phản biện |
| **Round 2** | Architecture-to-Code — tìm missing component / interface / dependency / contract / ambiguous behavior |
| **Round 3** | Developer Review — *"Tôi nhận task này hôm nay. Tôi có đủ thông tin để code chưa?"* Nếu **No** → xác định blocker và sửa plan |
| **Round 4** | Beginner UX — *"Một học sinh 10 tuổi có thể tạo Agent đầu tiên không?"* |
| **Round 5** | Poka-Yoke — với mỗi lỗi: **Can we prevent it by design?** |
| **Round 6** | Cost & Performance — token/model/infrastructure waste, latency, computation, network thừa |
| **Round 7** | Security & Safety — **cố tình tìm cách break the system** |
| **Round 8** | Production Engineering — reliability, failure handling, observability, deployment, scaling, recovery, upgrade, migration |
| **Round N** | **Recursive Review** — sau mỗi thay đổi lớn, review lại **toàn bộ**, vì một thay đổi có thể tạo regression ở phần khác. **Không giới hạn số vòng** |

**Ở đâu:** [`docs/00-council.md`](docs/00-council.md) — nhật ký đầy đủ Round 0 → Round 37,
**kèm cả những lập luận đã thua**.

---

## VIII. Implementation Plan phải đạt mức nào

Không chỉ Epic → Story → Task. Mỗi task phải trả lời đủ **chín** câu hỏi:

**What** · **Why** · **Where** · **How** · **Dependency** · **Contract** · **Failure** ·
**Test** · **Done**

**Ở đâu:** [`docs/11-implementation-plan.md`](docs/11-implementation-plan.md) — M0–M5, mọi
task theo đúng chín mục này, kèm ma trận truy vết.

---

## IX. Poka-Yoke cho chính Implementation Plan

Mỗi task quan trọng phải có: preconditions, inputs, expected behavior, constraints,
acceptance criteria, Definition of Done, tests, dependencies.

Hai điều kiện loại bỏ:

> - Nếu developer có thể hiểu task theo nhiều cách khác nhau → **task chưa đủ rõ**.
> - Nếu developer có thể implement sai nhưng vẫn pass review → **thiết kế chưa đủ Poka-Yoke**.

---

## X. Implementation Readiness Gate

Sau **mỗi vòng**, hội đồng phải tự chấm 16 chiều:

Architecture · Component Design · Interfaces · Data & State · Security · Cost ·
Performance · Testing · Observability · Deployment · Plugin Architecture · Poka-Yoke ·
Developer Experience · Beginner Experience · Documentation · Implementation Tasks

> **Chỉ cần một critical item = No → không được kết thúc.** Mở vòng tiếp theo.

---

## XI. Final Implementation Simulation

Trước khi tuyên bố Ready for Implement, phải mô phỏng:

> *"Ngày mai một engineering team bắt đầu implementation dựa hoàn toàn vào Implementation
> Plan này."*

Walkthrough: **Day 1 → Day 2 → First Component → First Integration → First Agent →
First Test → First Deployment**. Tìm tất cả blocker, rồi **Fix → Update Plan → Review Again**.

---

## XII. Điều kiện kết thúc tuyệt đối

> - Không kết thúc dựa trên số vòng.
> - Không kết thúc vì "đã đủ chi tiết".
> - Không kết thúc vì "hội đồng đã đồng ý".

Chỉ kết thúc khi **đồng thời**:

1. Một engineering team có thể bắt đầu implementation ngay từ Implementation Plan mà không
   cần đưa ra thêm architectural decision quan trọng.
2. Một người mới có thể sử dụng Harness với cognitive load tối thiểu để tạo Agent.
3. Kiến trúc cân bằng được **Extensibility + Cost Efficiency + Intelligence + Safety +
   Performance + Simplicity + Developer Experience** mà không vi phạm **Poka-Yoke + SOLID +
   Clean Code + KISS + Not Over-Engineering**.

---

## XIII. Deliverable bắt buộc

| Deliverable | Ở đâu |
|---|---|
| Final Implementation Plan | [`docs/11-implementation-plan.md`](docs/11-implementation-plan.md) |
| **Design Decision Log** | [`docs/12-decision-logs.md`](docs/12-decision-logs.md) — ADR-001…037 |
| **Implementation Decision Log** | [`docs/12-decision-logs.md`](docs/12-decision-logs.md) — IDL-01…48 |
| **Risk Register** | [`docs/13-risk-register.md`](docs/13-risk-register.md) — R-01…R-21 |
| **Open Issues** (chỉ giữ thứ thực sự không blocking) | [`docs/13-risk-register.md §3`](docs/13-risk-register.md) — OI-1…OI-10 |
| **Definition of Done** | [`docs/11-implementation-plan.md`](docs/11-implementation-plan.md) — mỗi task |
| **Implementation Sequence** | [`docs/11-implementation-plan.md`](docs/11-implementation-plan.md) — M0→M5 |
| **Dependency Graph** | [`docs/11-implementation-plan.md`](docs/11-implementation-plan.md) |
| **Validation Plan** | [`docs/14-validation-plan.md`](docs/14-validation-plan.md) — AC-01…55 |

---

## XIV. Nguyên tắc tối thượng

> - **Do not optimize for producing a detailed plan. Optimize for producing a plan that can
>   actually be implemented.**
> - **Do not optimize for architectural sophistication. Optimize for simplicity,
>   extensibility, safety, intelligence, efficiency and usability.**
> - **Do not ask developers to remember how to use the system correctly. Use Poka-Yoke to
>   make the correct way the easiest way.**
> - **Do not expose internal complexity to users unnecessarily.**
> - **Complexity inside. Simplicity outside.**
> - **If the council finds a critical problem, do not document it and move on. Resolve it,
>   update the plan, and review again.**
> - **Keep iterating until the council can confidently say: "This Implementation Plan is
>   ready to implement."**

---

## XV. Nền tảng bắt buộc

Nguyên văn, đưa ra sau khi hội đồng đã **khuyến nghị ngược lại** ở vòng 34:

> **"Bắt buộc: xây dựng trên nền tảng langchain/langgraph, openvikking"**

Hội đồng ghi nhận việc đảo chiều bằng một câu và **không tranh luận lại**.

| Thành phần | Trạng thái | Ghi chú |
|---|---|---|
| **LangChain / LangGraph** | ✅ Đã xây | [`src/harness/lg/`](src/harness/lg/) — LangGraph giữ vòng lặp; luật an toàn trở thành **hình dạng đồ thị** (ADR-032) |
| **`openvikking`** | ✅ Đã xây | Tên đúng là **`openviking`** (một chữ `k`) — [volcengine/OpenViking](https://github.com/volcengine/OpenViking). Ban đầu bị ghi nhận nhầm là "không tồn tại trên PyPI" do lỗi chính tả. Tích hợp qua seam `Store` (ADR-035): [`src/harness/memory/viking.py`](src/harness/memory/viking.py) |

**Điều yêu cầu này làm vỡ, và hội đồng nói thẳng:** NFR-05 giới hạn ≤ 3 runtime
dependency. `langgraph` kéo theo **36 gói**; `openviking` (server) kéo theo **185 gói**.
Giải pháp trung thực là extra: `harness[graph]`, `harness[viking]` — core vẫn 3
dependency và import 90 ms, có test chặn ranh giới đó (AC-46).

---

## XVI. Quyết định phạm vi

Chốt qua hỏi–đáp trực tiếp ở vòng 0:

| Câu hỏi | Quyết định |
|---|---|
| Ngôn ngữ | **Python** |
| Hình thái | **Library-first**, service tính sau |
| Đối tượng & threat model | **Open-source / lập trình viên phổ thông**, có **plugin bên thứ ba không đáng tin** trong threat model |

---

## XVII. Yêu cầu phát sinh trong quá trình làm việc

| # | Nguyên văn | Kết quả |
|---|---|---|
| 1 | *"báo cáo tóm tắt kết quả đi"* | Báo cáo tổng hợp |
| 2 | *"tóm lại đã thiết kế xong, hết lỗi, ready for implement chưa?"* | Trả lời thẳng, kèm danh sách còn mở |
| 3 | *"cho tôi xem code ví dụ tạo một agent đa chức năng đi"* | [`examples/support_agent.py`](examples/support_agent.py) — 6 tool đủ 4 lớp effect, subagent, `returns=`, ngân sách, phê duyệt, transcript, `Secret` |
| 4 | *"agent Trợ lý CSKH có chạy được multi turn, multi workflow, cross workflow không?"* | Trả lời kèm chứng minh chạy được |
| 5 | *"có business thì nên quản lý bằng state machine sẽ tốt hơn chứ nhỉ"* | **Đồng ý.** [`examples/refund_workflow.py`](examples/refund_workflow.py) + [`docs/06-safety.md §4.1`](docs/06-safety.md) — state machine là một `Policy`, nên nó chỉ **thắt chặt**, không bao giờ nới lỏng |
| 6 | *"có nên xây dựng harness này dựa trên một framework có sẵn như langchain langgraph không"* | Hội đồng khuyến nghị **không** → chủ dự án **bác bỏ** ở mục XV |
| 7 | *"cho tôi example tạo agent với harness langgraph đi"* | [`examples/langgraph_quickstart.py`](examples/langgraph_quickstart.py). **Chính việc viết example này phát hiện 3 lỗi** — multi-turn chưa bao giờ chạy được (vòng 37) |
| 8 | *"hãy viết toàn bộ yêu cầu của tôi ... ra thành một file"* | File này |

---

## XVIII. Yêu cầu về quy trình Git

- Phát triển trên nhánh **`claude/ai-agent-harness-design-ti5vk3`** (repo `nqthiep/harness`).
- Commit với thông điệp rõ ràng, mô tả được.
- **Không bao giờ** push sang nhánh khác nếu chưa được cho phép rõ ràng.

---

## XIX. Trạng thái hiện tại đối chiếu với yêu cầu

| Yêu cầu | Trạng thái | Bằng chứng |
|---|:--:|---|
| 5 nguyên tắc bất biến có mục riêng, quyết định riêng, test riêng | ✅ | Vòng 21 kiểm đếm; một nguyên tắc từng **không có mục nào** cho tới lúc đó |
| Plugin boundary được xác định bằng phép thử, không bằng cảm tính | ✅ | 5 seam, [`docs/02-architecture.md §4`](docs/02-architecture.md) |
| Cost là architectural concern | ✅ | ADR-017/026/029; SC-4 = 95.3% đo thật |
| Safe by Design | ✅ | 21 red-team test chạy trong CI; 4 lỗi bảo mật đã tìm ra và sửa |
| Poka-Yoke | ✅ | 83 failure mode, xếp hạng theo thang phòng ngừa |
| Extreme DX / 10 tuổi | ⚠️ **Một phần** | Đo được: tài liệu lớp 4.2, thông báo lỗi xấu nhất lớp 4.9. **Chưa đo với trẻ em thật** — xem SC-1b |
| Zero-to-Agent | ✅ | Thang progressive disclosure + quickstart 5 bậc |
| Rounds 0–8 + Round N đệ quy | ✅ | **Round 0 → Round 37**, nhật ký đầy đủ kèm lập luận đã thua |
| Readiness Gate 16 chiều | ✅ | [`docs/00-council.md §3`](docs/00-council.md) |
| Final Implementation Simulation | ✅ | [`docs/14-validation-plan.md §5`](docs/14-validation-plan.md) |
| 9 deliverable mục XIII | ✅ | Bảng ở mục XIII |
| Nền tảng bắt buộc (LangChain/LangGraph + OpenViking) | ✅ | Mục XV |

### Còn mở — nói thẳng, không giấu

| # | Vấn đề | Vì sao chưa đóng được |
|---|---|---|
| **SC-1b** | Chưa đo với **trẻ em thật 10–12 tuổi** | Cần người thật. [`docs/16-sc1b-field-kit.md`](docs/16-sc1b-field-kit.md) là bộ công cụ chạy được, nhưng hội đồng **không coi yêu cầu mục IV là đã đạt** cho tới khi đo xong |
| **OI-10** | Binding OpenViking **chưa từng chạy với server thật** | Server cần embedding model và wizard đòi TTY. Test chạy qua **code thật của SDK** trên stub transport; nội dung response thật vẫn chưa được kiểm chứng |
| — | `AnthropicProvider` chưa chạy với API thật | Môi trường không có `ANTHROPIC_API_KEY` |
| — | mypy / ruff chưa từng chạy | Chưa cài trong môi trường này |

---

## XX. Bài học mà chính yêu cầu của chủ dự án tạo ra

Ghi lại vì chúng là kết quả trực tiếp của việc mục VI bắt hội đồng phải tự phản biện.

**23 vòng đọc–review** tìm ra 20 lỗi và **0 lỗi bảo mật**.
**14 vòng xây thật** tìm ra hơn 30 lỗi, **4 lỗi bảo mật**, và **12+ tính năng đã đặc tả
nhưng chưa bao giờ được viết** — kể cả model provider.

> **Đọc không tìm ra được thứ chỉ có chạy mới tìm ra.**

Hội đồng đã **tuyên bố hội tụ sai hai lần** (vòng 12 với gate 16/16, vòng 23 với "phát
hiện đang giảm dần"). Mục XII của chủ dự án — *"không kết thúc vì hội đồng đã đồng ý"* —
là thứ duy nhất ngăn cả hai lần đó trở thành điểm dừng.

Ba lỗi tái phát đúng lớp đã từng sửa (vòng 25 → 35, vòng 27 → 35, vòng 34 → 35 → 37):

> **Một lớp lỗi đã được đặt tên và sửa ở implementation này không có nghĩa là đã sửa ở
> implementation kế tiếp.**

Và bài học vòng 37, do chính việc viết tài liệu tìm ra:

> **Một bộ test phủ hết mọi luật vẫn có thể bỏ sót hình dạng sử dụng.**
> Mọi kịch bản graph đều chỉ `invoke()` đúng một lần, nên multi-turn chưa bao giờ chạy được
> mà không ai biết.

---

*Bản ghi này được cập nhật khi chủ dự án bổ sung hoặc thay đổi yêu cầu.*
