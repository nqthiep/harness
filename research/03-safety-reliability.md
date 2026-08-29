# 15. Reliability — và lỗ hổng chung của cả ngành

Câu hỏi của đề bài (§503): *"Agent có thể tiếp tục từ nơi thất bại hay phải chạy lại từ
đầu?"* Bằng chứng chia các dự án thành ba nhóm rõ rệt.

**Nhóm 1 — khôi phục là kiến trúc.** LangGraph: checkpoint **21,1 lần/kLOC**, cộng
`durability: Literal["sync","async","exit"]` (`_constants.py:69`) và `interrupt()` được
mô tả trong source là *"Interrupt the graph with a resumable exception from within a
node"*. Microsoft Agent Framework theo sau ở 6,2/kLOC.

**Nhóm 2 — khôi phục là tính năng.** crewai 1,9 · openai-agents 1,0 · pydantic-ai 0,7 ·
google-adk 0,5. Có cơ chế, nhưng không phải xương sống.

**Nhóm 3 — không có.** smolagents 0,4 · letta-client 0,0 (state nằm ở server).

## Lỗ hổng chung: idempotency

Đề bài liệt kê `Idempotency` trong §496 như một tiêu chí production. Kết quả đọc source:

| Gói | `idempoten*` xuất hiện | **Thực chất là gì** |
|---|---|---|
| pydantic-ai | 18 file | *"Idempotent: re-applying to an already-trimmed list is a no-op"* — **thao tác nội bộ**, không phải side effect |
| letta-client | 21 file | `idempotency_header` — **HTTP header** của generated client |
| openai-agents | 5 file | comment: *"idempotent on the server side"* — **lời hứa của server** |
| google-adk | 6 file | rải rác |
| langgraph | 1 file | `CachePolicy(key_func=default_cache_key, ttl=...)` — **cache node**, băm input **bằng pickle** |
| smolagents | 0 file | không có |

> **Không gói nào trong tám gói cung cấp exactly-once cho một tool đã gửi email hoặc đã
> ghi database.** Đây là khoảng trống của cả ngành, không phải điểm yếu của một dự án.

Hệ quả thực tế: client timeout → gọi lại → **side effect lần hai**. Cơ chế gần nhất là
`CachePolicy` của LangGraph, nhưng caching *không phải* exactly-once, và mặc định băm
bằng `pickle` có hàm ý bảo mật riêng khi input đến từ nguồn không tin cậy.

---

# 17. Poka-Yoke / Safety by Design

Đề bài yêu cầu phân biệt **safety by design** với **safety by prompt** (§534). Bảng dưới
chỉ tính cơ chế **đọc được trong source**, không tính hướng dẫn trong prompt.

| Lớp | Cơ chế | Ai làm tốt (bằng chứng) |
|---|---|---|
| **Schema** | Typed I/O, validation, `extra='forbid'` | PydanticAI (`output_type`, `deps_type`), Google ADK (`ConfigDict(extra='forbid')`) |
| **Execution** | Graph, transition tường minh, bounded loop | LangGraph (checkpoint 21,1/kLOC) |
| **Permission** | Allowlist, least privilege | **smolagents 16,3/kLOC** (`additional_authorized_imports`), Google ADK 5,3 |
| **Isolation** | Container/VM/workspace | **smolagents** (`executor_type` trong constructor), **openai-agents 8,5/kLOC** |
| **Human gate** | Interrupt, approval | MS AF 3,6 · openai-agents 3,1 · **crewai 0,0** |
| **Budget** | Token/call/time limit | **pydantic-ai 3,6/kLOC** · MS AF 1,9 · **langgraph 0,0** |
| **Recovery** | Checkpoint, resume | **LangGraph 21,1** |
| **Audit** | Trace prompt/tool/error/cost | MS AF 7,7 · ADK 4,8 · pydantic-ai 4,2 · **langgraph 0,1** |

### Ba nhận định mà bảng này cho phép nói, và tài liệu marketing thì không

**1. Không dự án nào phủ đủ tám lớp.** LangGraph vô địch Recovery nhưng budget **0,0**
và OTel **0,1** — nó là runtime, cố tình để hai việc đó cho tầng trên. smolagents vô
địch Permission và Isolation nhưng Recovery 0,4. **Đây là lập luận mạnh nhất cho kiến
trúc plugin**: không ai xây được cả tám lớp tốt trong một gói.

**2. Approval ≠ Isolation, và có bằng chứng cụ thể.** Goose có bốn permission mode
(Chat / Auto / Approve / SmartApprove) [G2]. Nhưng sandbox seatbelt macOS **đã bị gỡ sau
giai đoạn thử nghiệm**, và tiến trình server chạy tool **có đúng quyền của tài khoản
người dùng, không được sandbox ở mức OS** [G1]. Một harness có bốn chế độ phê duyệt vẫn
chạy tool với toàn quyền của bạn.

**3. CrewAI là rủi ro kiến trúc, không chỉ là "kém tinh vi".** approval **0,0**,
sandbox 0,2, cost 0,0 trên 118 kLOC. Một framework quảng bá agent tự trị nhiều vai trò
mà gần như không có human gate trong source thì blast radius không bị chặn ở đâu cả.

---

# 16. Security

| Khía cạnh | Trạng thái toàn ngành |
|---|---|
| Prompt injection | Không dự án nào giải quyết được ở mức kiến trúc. Goose có "prompt injection detection" và "adversary reviewer" [G1] — đây là **phát hiện**, không phải ngăn chặn |
| Tool permission | Có ở smolagents, ADK, Goose, Cline. **Không** có mô hình capability thống nhất |
| Multi-tenancy | **Chưa đủ evidence** cho phần lớn thư viện. Chúng là library in-process; tenancy là việc của tầng service |
| Secret leakage | Không gói Python nào trong nghiên cứu có kiểu `Secret` chuyên dụng chống rò rỉ qua log/prompt |
| Network policy | **Chưa đủ evidence** — không thấy egress allowlist mặc định ở gói nào đã đọc |

**Mô hình đúng, phát biểu ngắn:** model được quyền **đề xuất** một tool call; nó không
được quyền **tự cấp phép thực thi**. Cổng kiểm tra phải nằm giữa hai việc đó, và phải là
đường đi bắt buộc chứ không phải quy ước.

---

# 20. Cost Engineering

Câu hỏi §629: *"Framework có cơ chế chống Agent chạy vô hạn và đốt tiền không?"*

| Gói | budget/kLOC | cost/kLOC | Nhận định |
|---|---:|---:|---|
| **pydantic-ai** | 3,6 | **1,1** | Dự án duy nhất coi cost là khái niệm hạng nhất trong source |
| MS Agent Framework | 1,9 | 0,0 | Có giới hạn, không có khái niệm tiền |
| letta-client | 1,8 | 0,1 | |
| openai-agents | 1,4 | 0,0 | `max_turns` chặn loop |
| crewai | 0,7 | 0,0 | |
| google-adk | 0,6 | 0,1 | |
| **langgraph** | **0,0** | 0,0 | Không có khái niệm ngân sách |

**Kết luận có bằng chứng:** ngành có **loop limit** nhưng gần như không có **spend
ceiling**. `max_turns`/`max_iterations` chặn số vòng — nhưng một vòng với 200k token
input đắt gấp trăm lần một vòng ngắn, nên đếm vòng không phải kiểm soát chi phí.

Công thức đúng để so sánh không phải cost/task mà:

```
Cost_successful = (LLM + Tool + Infra + Sandbox + Observability + HumanReview) / P(success)
```

Một framework ít token nhưng success rate thấp đắt hơn framework nhiều token mà xong ngay.

---

# 18. Observability · 19. Evaluation

**Observability** (otel/kLOC): MS AF **7,7** · google-adk 4,8 · pydantic-ai 4,2 ·
openai-agents 4,1 · crewai 2,9 · **langgraph 0,1**. LangGraph gần như không có
telemetry nội tại — đúng với vị trí runtime của nó, nhưng nghĩa là **chọn LangGraph là
nhận thêm việc dựng tầng quan sát**.

**Evaluation.** Google ADK có `AgentEvaluator` trong source. Ngoài ra hệ sinh thái eval
chủ yếu nằm ở sản phẩm đi kèm (LangSmith, Logfire) chứ không trong thư viện. Điểm quan
trọng mà đề bài §605 hỏi — *"framework có giúp xây agent testable không?"* — trả lời tốt
nhất là PydanticAI: `deps_type` cho phép **tiêm phụ thuộc giả mà không đụng agent**, đó
là điều kiện tiên quyết của test xác định.

**Điều cả ngành còn thiếu:** không dự án nào cung cấp **trajectory contract** khai báo
được — kiểu "tool A phải được gọi, tool B không được gọi, ≤ N model call, ≤ C chi phí,
retry không nhân đôi side effect". Đây là khoảng trống thứ hai sau idempotency.
