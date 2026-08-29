# 26–27. Strengths, Weaknesses, Hidden Weaknesses

Đề bài §919 cấm viết kiểu marketing và **bắt buộc** tìm điểm yếu. Mỗi điểm yếu dưới đây
gắn với một quan sát đo được hoặc một dòng source.

## LangGraph 1.2.11

**S1** Checkpoint 21,1/kLOC — durability là kiến trúc. **S2** `durability:
sync|async|exit` cho phép chọn đánh đổi bền vững/độ trễ. **S3** `interrupt()` là
resumable exception, HITL đúng nghĩa. **S4** `CachePolicy` có `ttl` và `key_func`.
**S5** Gói nhỏ (28 kLOC) cho một runtime.

**W1** budget **0,0/kLOC** — không có khái niệm chi tiêu. **W2** OTel 0,1 — phải tự
dựng quan sát. **W3** MCP **0** — không có tool ecosystem. **W4** Không có abstraction
"Agent"; mọi thứ là node và state, cognitive load cao. **W5** Không có tool contract —
tool là việc của developer.

**Hidden** — `CachePolicy` mặc định băm input **bằng pickle** (`default_cache_key`).
Với input từ nguồn không tin cậy, đây là bề mặt cần cân nhắc. **Hidden 2** — sức mạnh
checkpoint chuyển thành nghĩa vụ: bạn phải hiểu thread/checkpoint/durability semantics
trước khi dùng đúng, và hiểu sai thì hỏng im lặng chứ không báo lỗi.

## PydanticAI 2.36.0

**S1** Typed end-to-end (`deps_type`, `output_type`) — testability tốt nhất Python.
**S2** `EndStrategy` đặt tên cho ngữ nghĩa dangling tool call và **đổi mặc định vì mặc
định cũ sai**. **S3** retry 6,5 và cancel 9,1/kLOC cao nhất. **S4** Duy nhất có `cost`
đáng kể. **S5** `tool_timeout`, `max_concurrency` ngay trên constructor.

**W1** Không có isolation (sandbox 0,7). **W2** Checkpoint 0,7 — durable workflow phải
ghép thêm. **W3** Constructor 20+ tham số — surface lớn. **W4** Đã có breaking change
v1→v2 (`end_strategy` đổi mặc định); cần pin.

**Hidden** — chính `EndStrategy` là bằng chứng rằng **ngữ nghĩa này khó, và ai không
đặt tên cho nó thì đang xử lý sai một cách im lặng**. Một framework không có khái niệm
tương đương gần như chắc chắn đang bỏ rơi hoặc chạy nhầm dangling tool call.

## OpenAI Agents SDK 0.22.0

**S1** API đơn giản nhất Tier A. **S2** sandbox 8,5/kLOC. **S3** `mcp_servers`/
`mcp_config` là tham số constructor. **S4** **42 issue mở** trên 29k sao — kỷ luật bảo
trì tốt nhất bảng. **S5** `instructions` nhận callable có context.

**W1** Đường sung sướng là OpenAI. **W2** checkpoint 1,0 — durability yếu. **W3** cost
0,0. **W4** `handoffs` khuyến khích multi-agent, dễ sinh chi phí ẩn.

**Hidden** — con số 42 issue mở có thể phản ánh **chính sách đóng issue** chứ không chỉ
chất lượng. Cần đọc tỉ lệ đóng theo thời gian mới kết luận chắc; ở đây ghi là *tín hiệu*,
không phải *bằng chứng*.

## Microsoft Agent Framework 1.16.0

**S1** `middleware` + `context_providers` + `compaction_strategy` trong constructor.
**S2** OTel 7,7 và MCP 7,6/kLOC — cao nhất. **S3** `client` là tham số đầu bắt buộc:
dependency inversion cưỡng chế. **S4** Python + .NET.

**W1** Mới (repo tạo 2025-04), 639 issue mở. **W2** sandbox 0,2. **W3** cost 0,0.
**W4** Kế thừa vai trò successor của AutoGen và Semantic Kernel — hai cộng đồng phải di
trú vào đây, rủi ro API còn dịch chuyển.

## Google ADK 2.8.0

**S1** permission 5,3/kLOC. **S2** `extra='forbid'` — sai tên trường là lỗi lúc dựng.
**S3** OTel 4,8. **S4** `AgentEvaluator` trong source. **S5** Đa ngôn ngữ.

**W1** `DEFAULT_MODEL: ClassVar[str] = 'gemini-3.5-flash'` — thiên hướng nhà cung cấp
nằm trong class variable. **W2** 686 file Python, 172 kLOC — surface rất rộng. **W3**
`config_type` đã bị đánh dấu **DEPRECATED ngay trong docstring** của `BaseAgent` — API
còn đang dịch chuyển.

## smolagents 1.26.0

**S1** 13 kLOC, 20 file — core nhỏ nhất. **S2** `executor_type` trong constructor.
**S3** permission 16,3/kLOC — cao nhất tuyệt đối. **S4** `additional_authorized_imports`
là allowlist, mặc định từ chối.

**W1** cancel **0,0** — không có ngữ nghĩa huỷ. **W2** checkpoint 0,4. **W3** OTel 0,4.
**W4** Code-as-action mở rộng bề mặt tấn công theo bản chất: sandbox không phải tuỳ chọn
mà là điều kiện.

**Hidden** — `executor_type="local"` là **mặc định**. Một mặc định an toàn hơn sẽ là
từ chối chạy cho tới khi người dùng chọn rõ. Đây là ví dụ về Poka-Yoke đúng chỗ nhưng
sai mặc định.

## CrewAI 1.15.18

**S1** Thời gian tới prototype ngắn. **S2** Cộng đồng lớn (57.782 sao). **S3** timeout
2,1/kLOC — cao.

**W1** approval **0,0/kLOC**. **W2** sandbox 0,2. **W3** cost 0,0. **W4** 118 kLOC cho
một abstraction role/task. **W5** 770 issue mở.

**Hidden** — abstraction role/task **giấu số lượt gọi model**. Một "crew" bốn vai trò
tranh luận có thể tốn gấp nhiều lần một graph tuần tự làm cùng việc, và API không cho
thấy điều đó ở chỗ người dùng nhìn.

## Letta 1.12.1 (client)

**S1** Stateful memory là kiến trúc. **S2** **Gói duy nhất có `idempotency_header`
thật** ở tầng HTTP. **S3** 39 issue mở.

**W1** `letta-client` là **generated client**, logic nằm ở server — không đánh giá được
kiến trúc runtime từ đây. **W2** Phụ thuộc dịch vụ. **Chưa đủ evidence** về nội bộ server.

---

# 28. Anti-patterns — quan sát được, không phải liệt kê lý thuyết

| Anti-pattern | Bằng chứng cụ thể |
|---|---|
| **Everything-is-agent** | CrewAI: role/task/crew là abstraction trung tâm cho cả những việc chỉ cần một hàm |
| **God-object Agent** | PydanticAI 20+ tham số constructor; ADK `LlmAgent` gánh model, tools, memory, callbacks, config |
| **Safety by prompt** | Toàn ngành: không dự án nào ngăn prompt injection ở mức kiến trúc; Goose *phát hiện*, không *ngăn* |
| **Approval nhầm là isolation** | Goose 4 permission mode + sandbox đã gỡ = tool chạy với quyền user [G1] |
| **Unbounded token usage** | budget 0,0/kLOC ở LangGraph; cost 0,0 ở 6/8 gói. Có loop limit, không có spend ceiling |
| **Retry không idempotent** | Cả tám gói. `CachePolicy` là caching, không phải exactly-once |
| **Hidden state** | LangGraph: state semantics mạnh nhưng ngầm; hiểu sai thread/durability thì hỏng im lặng |
| **Vendor default trong core** | ADK `DEFAULT_MODEL: ClassVar = 'gemini-3.5-flash'` |
| **Framework magic** | `CachePolicy` băm input bằng `pickle` — hành vi quan trọng nằm sau một mặc định |
| **Migration debt bán như tính năng** | AutoGen → MS Agent Framework; Semantic Kernel → cùng đích |
| **Stars ≠ adoption** | 4 repo top-10 là *skills collection*, không có runtime |
