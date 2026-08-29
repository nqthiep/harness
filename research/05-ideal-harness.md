# 29. Best-of-Breed

Đề bài §1072 nói đúng: framework tốt nhất không nhất thiết là một framework.

| Câu hỏi | Câu trả lời | Bằng chứng |
|---|---|---|
| **Agent API tốt nhất?** | **PydanticAI** | `deps_type`/`output_type` typed end-to-end; `EndStrategy` đặt tên cho ngữ nghĩa khó |
| **Workflow/Runtime tốt nhất?** | **LangGraph** | checkpoint 21,1/kLOC, `durability` ba chế độ, `interrupt()` resumable |
| **Tool API tốt nhất?** | **PydanticAI** (`toolsets`) và **OpenAI Agents SDK** (MCP hạng nhất) | `mcp_servers`/`mcp_config` là tham số constructor |
| **Memory tốt nhất?** | **Letta** | Stateful memory là kiến trúc, không phải tính năng |
| **Context engineering tốt nhất?** | **MS Agent Framework** | `context_providers` + `compaction_strategy` là API, không phải mẹo |
| **Security/Isolation tốt nhất?** | **smolagents** | `executor_type` trong constructor; permission 16,3/kLOC |
| **Observability tốt nhất?** | **MS Agent Framework** | OTel 7,7/kLOC |
| **Evaluation tốt nhất?** | **Google ADK** | `AgentEvaluator` trong source |
| **DX tốt nhất?** | **OpenAI Agents SDK** | Ít primitive nhất trong Tier A |
| **Plugin architecture tốt nhất?** | **MS Agent Framework** | middleware chain + context provider + MCP dày đặc |
| **Enterprise nhất?** | **MS Agent Framework** / **Google ADK** | Đa ngôn ngữ, middleware, deployment path |
| **Cost control tốt nhất?** | **PydanticAI** | Dự án duy nhất có `cost` đáng kể — và vẫn chưa đủ |

**Không ai thắng ở:** idempotency, trajectory contract, spend ceiling thật, và
authorization envelope cho tool call. Bốn chỗ trống này là nơi một harness mới có lý do
tồn tại.

---

# 30. Overall Ranking (0–10, theo trọng số đã hiệu chỉnh)

| Dự án | Arch | Agent API | Tool | Runtime | Reliab | Security | Ctx/Mem | Obs | Eval | DX | Cost | **Tổng** |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| **LangGraph** | 9 | 6 | 5 | **10** | **9** | 5 | 7 | 2 | 5 | 6 | 2 | **6,6** |
| **PydanticAI** | 8 | **10** | 8 | 6 | 8 | 6 | 6 | 7 | 8 | 8 | **7** | **7,5** |
| **OpenAI Agents SDK** | 7 | 9 | **9** | 6 | 6 | 7 | 5 | 7 | 6 | **9** | 3 | **7,0** |
| **MS Agent Framework** | 8 | 8 | 8 | 7 | 7 | 5 | **9** | **9** | 6 | 6 | 3 | **7,2** |
| **Google ADK** | 7 | 7 | 7 | 6 | 6 | 7 | 7 | 8 | **8** | 6 | 3 | **6,8** |
| **smolagents** | 7 | 6 | 6 | 4 | 4 | **9** | 3 | 3 | 4 | 8 | 2 | **5,6** |
| **CrewAI** | 4 | 5 | 5 | 4 | 4 | **2** | 5 | 5 | 4 | 7 | 2 | **4,3** |
| **Letta** | 7 | 5 | 5 | 6 | 6 | 5 | **9** | 5 | 4 | 6 | 3 | **5,9** |

*Justification ngắn:* LangGraph Runtime 10 vì checkpoint 21,1/kLOC hơn một bậc độ lớn;
Cost 2 vì budget 0,0. PydanticAI Agent API 10 vì typed end-to-end + `EndStrategy`; Cost
7 vì là dự án duy nhất có khái niệm. CrewAI Security 2 vì approval 0,0/kLOC. smolagents
Security 9 vì cách ly ở constructor, nhưng Runtime 4 vì cancel 0,0.

**Tổng số này không dùng để chọn một dự án.** Nó dùng để cho thấy phổ điểm hẹp (4,3–7,5)
trong khi phổ *chuyên môn hoá* rất rộng — bằng chứng cho kiến trúc ghép, không phải chọn.

---

# 31. Lessons Learned — mười bài học, mỗi bài có nguồn

1. **Đặt tên cho ngữ nghĩa khó thay vì xử lý ngầm.** `EndStrategy` của PydanticAI.
2. **Cách ly phải ở constructor, không ở tài liệu.** `executor_type` của smolagents.
3. **Durability là kiến trúc hoặc không tồn tại.** LangGraph 21,1 vs phần còn lại <2.
4. **Cross-cutting concern thuộc middleware, không thuộc Agent.** MS Agent Framework.
5. **Dependency injection là điều kiện của testability.** `deps_type` của PydanticAI.
6. **`extra='forbid'` rẻ và hiệu quả.** Google ADK biến typo thành lỗi lúc dựng.
7. **Loop limit không phải cost control.** Cả ngành có `max_turns`, gần như không có
   spend ceiling.
8. **Approval không phải isolation.** Goose: 4 mode, sandbox đã gỡ, tool chạy quyền user.
9. **Idempotency là lỗ hổng của cả ngành, không của một dự án.**
10. **Mặc định quan trọng hơn khả năng.** `executor_type="local"` mặc định làm giảm giá
    trị của chính cơ chế cách ly tốt nhất trong nghiên cứu.

---

# 32. Ideal Agent Harness Architecture

Thiết kế lại từ bằng chứng, **không** dùng sơ đồ mẫu của đề bài (§1173 cho phép và yêu
cầu phản biện). Khác biệt chính so với sơ đồ mẫu: sơ đồ mẫu đặt Policy **ngang hàng**
với Context và Memory. Bằng chứng nói Policy không ngang hàng — nó là **cổng bắt buộc
trên đường đi**, và đó phải là hình dạng chứ không phải quy ước.

```
                      ┌───────────────────────────┐
   caller ───────────►│  Session  (id, tenant,    │
   (lib · HTTP · CLI) │   owner, TTL, resume)     │
                      └─────────────┬─────────────┘
                                    │
                      ┌─────────────▼─────────────┐
                      │      Run  (run_id)        │◄── Checkpoint Store
                      └─────────────┬─────────────┘        (durable)
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        │  MỌI đường tới model đi qua đây, không có ngoại lệ     │
        ▼                                                       │
  ┌───────────┐      ┌──────────┐      ┌──────────┐      ┌──────┴─────┐
  │  BUDGET   │─────►│  MODEL   │─────►│  POLICY  │─────►│  APPROVAL  │
  │ reserve   │      │ adapter  │      │ verdict  │      │  record    │
  │ trước khi │      │          │      │ lattice  │      │ (không     │
  │ gọi       │      │          │      │          │      │  phải bool)│
  └───────────┘      └──────────┘      └────┬─────┘      └──────┬─────┘
        ▲                                   │ DENY              │ ALLOW
        │                                   ▼                   ▼
        │                            ┌──────────────────────────────┐
        │                            │   TOOL GATEWAY               │
        │                            │   envelope: idempotency_key, │
        │                            │   principal, scopes, timeout │
        │                            └──────────────┬───────────────┘
        │                                           │
        │                            ┌──────────────▼───────────────┐
        └────────────────────────────┤  SANDBOX (seam)              │
                                     │  workspace root · egress deny│
                                     └──────────────┬───────────────┘
                                                    │
   ┌────────────────────────────────────────────────▼──────────────┐
   │  EVENT BUS — một envelope có version, mọi transport dùng chung │
   │  schema_version · run_id · session_id · tenant_id · trace_id   │
   │  · sequence · type · payload                                   │
   └────────────────────────────────────────────────────────────────┘
```

**Bốn quyết định khác với mọi dự án đã khảo sát:**

**1. Budget là cổng, không phải bộ đếm.** Ngành có `max_turns` sau khi gọi; ở đây
`reserve()` xảy ra **trước** mỗi lần gọi model, và `max_tokens` được suy ra từ ngân sách
còn lại. Lý do: một vòng lặp 200k token đắt gấp trăm lần một vòng ngắn, nên đếm vòng
không kiểm soát tiền.

**2. Idempotency key là bắt buộc trên mỗi tool call**, không phải tuỳ chọn. Đây là chỗ
trống của cả ngành. `run_id:call_id` đi vào một `IdempotencyStore`; `write`/`danger`
**fail closed** khi store chết, `read` fail open.

**3. Approval là bản ghi, không phải boolean.** `ApprovalRecord(decision_id, actor,
policy_version, decided_at, expires_at, verdict, reason)`. Một `bool` không audit được,
không hết hạn được, không truy ra ai quyết định.

**4. Sandbox là seam bắt buộc phải chọn, mặc định là chế độ hẹp nhất.** Học smolagents
nhưng **sửa mặc định**: `sandbox=` không có giá trị mặc định "local" — người dùng phải
chọn, và lựa chọn rẻ nhất vẫn là workspace-rooted + egress deny.

---

# 33. Minimal Core

Nguyên tắc: **core càng nhỏ càng tốt, nhưng không được thiếu abstraction mà một bản
thay thế có thể dùng để vô hiệu hoá một bảo đảm an toàn.**

### TRONG CORE (8 thứ)

| | Vì sao **phải** ở core |
|---|---|
| **Run loop** | Là nơi duy nhất budget check đứng trước model call và policy check đứng trước tool call |
| **Budget ledger** | Một bản thay thế có thể tắt trần chi tiêu |
| **Policy engine** (verdict lattice) | Một bản thay thế có thể **nới lỏng** — nên phép hợp phải là `max()`, và phải nằm trong core |
| **Tool contract + effect class** | Phân loại hệ quả không thể để plugin định nghĩa lại |
| **Idempotency store interface** | Exactly-once không thể là tuỳ chọn |
| **Event envelope có version** | Không có version thì không đổi được taxonomy mà không phá exporter |
| **Session/Run identity** | tenant/owner/TTL là ranh giới cách ly |
| **Checkpoint interface** | Recovery phải là kiến trúc (bài học LangGraph) |

### LÀ PLUGIN (seam)

`ModelProvider` · `Sandbox` · `Store` (memory/RAG/vector) · `Exporter` (OTel/vendor) ·
`ToolSource` (MCP) · `ApprovalProvider` · `Evaluator`

### KHÔNG THUỘC VỀ ĐÂU CẢ — dịch vụ ngoài

Vector DB · Knowledge graph · Browser · Container runtime · Secret manager · Identity
provider. Chúng là **external service**, không phải plugin: harness gọi chúng qua seam,
không nhúng chúng.

**Phản biện đề bài:** §1205 gợi ý "MCP implementation" nên là plugin — **đồng ý**, và
thêm một điều kiện: tool đến từ MCP mà **không khai `effect`** phải mặc định là lớp
untrusted nhất, không phải lớp an toàn nhất. Một server MCP là bên thứ ba; protocol
không phải security boundary.

---

# 35–36. Ideal API Design & Poka-Yoke API

Phản biện interface mẫu của đề bài (§1247). `interface Agent { AgentResult run(AgentRequest) }`
thiếu **năm** thứ mà bằng chứng cho thấy là bắt buộc: streaming, cancellation, session,
approval round-trip, và idempotency.

```python
# CORE — nhỏ, và mỗi tham số tồn tại vì một bằng chứng
agent = Agent(
    name="...", job="...",                  # tối thiểu để chạy
    model=provider,                         # DI cưỡng chế (MS AF)
    tools=[...],                            # mỗi tool khai effect
    budget="$0.20, 15 steps, 60s",          # ba trục — spend ceiling, không phải loop limit
    policies=[...],                         # verdict lattice, chỉ thắt chặt
    sandbox=Workspace("./ws"),              # BẮT BUỘC chọn (sửa mặc định của smolagents)
    approve=my_approver,                    # trả ApprovalRecord, không phải bool
    checkpointer=store,                     # durability là kiến trúc (LangGraph)
    end_strategy="graceful",                # đặt tên cho ngữ nghĩa khó (PydanticAI)
)

result  = agent.run(msg)                    # raise khi thất bại
result  = agent.try_run(msg)                # trả Result
async for ev in agent.stream(msg): ...      # event envelope có version
await     agent.cancel(run_id)              # huỷ là tín hiệu, không phải stop reason
await     agent.resume(run_id)              # từ checkpoint
```

**Poka-Yoke — cân bằng Safety ↔ Simplicity.** Đề bài (§1300) gợi ý builder
`.withTimeout().withBudget().withPolicy().run()`. **Phản biện: builder sai ở đây**, vì
nó cho phép gọi `.run()` mà không đặt gì cả. Cách đúng là **tham số bắt buộc trong
constructor**: thứ nguy hiểm khi thiếu thì không được có mặc định.

| Cách làm sai | Bị chặn ở đâu |
|---|---|
| Tool không khai `effect` | **Import time** |
| `sandbox=` không chọn | **Construction** |
| Tool `external` + tool không hoàn tác trong cùng bộ | **Construction** |
| Ngân sách thiếu trục tiền | **Construction** |
| Approve trả `bool` | **Type error** |
| Tool call thiếu idempotency key | **Không thể** — gateway sinh ra nó |
| Đổi event taxonomy | **Version bump bắt buộc** |

---

# 37. Recommendation by Use Case

| Nếu bạn muốn | Chọn | Cảnh giác |
|---|---|---|
| Prototype nhanh | OpenAI Agents SDK | Đường sung sướng là OpenAI |
| Production Python typed | **PydanticAI** | Không có isolation; pin version |
| Workflow bền vững, pause/resume | **LangGraph** | Tự dựng budget và observability |
| Coding agent dùng ngay | claude-code · opencode · Codex · Cline | Approval ≠ sandbox |
| Enterprise .NET/polyglot | MS Agent Framework | Còn mới, API dịch chuyển |
| GCP/deployment | Google ADK | Thiên hướng Gemini trong class var |
| RAG/document | LlamaIndex · Haystack | Surface rộng |
| Chạy code do model sinh | **smolagents** | **Đổi `executor_type` khỏi `"local"`** |
| Memory dài hạn | Letta | Phụ thuộc dịch vụ |
| Java/Spring Boot | **Chưa đủ evidence** cho một lựa chọn Java trưởng thành trong bộ này | MS AF có .NET, không phải Java |
| Multi-agent | Cân nhắc **không** dùng | CrewAI: approval 0,0/kLOC; chi phí ẩn sau abstraction |

---

# 39. Open Questions / Research Gaps

1. **Không có benchmark có kiểm soát** giữa các framework trên cùng model/task/policy.
   Theo §650, tôi không tự tạo. Đây vẫn là khoảng trống lớn nhất của lĩnh vực.
2. **Phần TypeScript/Rust mỏng hơn** phần Python vì egress proxy chặn docs và các dự án
   này không phát hành lên PyPI. Cline, opencode, Codex, Goose được đánh giá từ metadata
   và nguồn thứ cấp — **Chưa đủ evidence** ở mức source.
3. **`anthropics/claude-code`**: harness có ảnh hưởng lớn nhất theo forks, nhưng nội bộ
   không đọc được từ PyPI. **Chưa đủ evidence.**
4. **Mật độ cơ chế/kLOC là proxy, không phải chất lượng.** Nó đo *sự hiện diện*, không
   đo *tính đúng*. Một dự án có thể có `retry` khắp nơi mà retry vẫn sai.
5. **Tỉ lệ issue mở** có thể phản ánh chính sách đóng issue hơn là chất lượng.
6. **Idempotency**: cần xác nhận thêm ở tầng server của Letta và OpenHands, nơi có thể
   đã có mà client không lộ ra.

---

# 40. Sources

**Nguồn cấp 1 — source code đọc trực tiếp** (tải từ PyPI, 2026-08-29):
`pydantic-ai-slim 2.36.0` · `openai-agents 0.22.0` · `google-adk 2.8.0` ·
`agent-framework-core 1.16.0` · `langgraph 1.2.11` · `smolagents 1.26.0` ·
`crewai 1.15.18` · `letta-client 1.12.1`

**Nguồn cấp 1 — GitHub API** (2026-08-29): 35 repository, stars/forks/issues/license/
ngày tạo/ngôn ngữ.

**Nguồn cấp 2 — phân tích bên thứ ba (dùng có ghi rõ):**
- [G1] goose v1.25.0 — sandbox seatbelt và việc gỡ bỏ: https://goose-docs.ai/blog/2026/02/23/goose-v1-25-0/
- [G2] Permission modes and tool approval (block/goose): https://deepwiki.com/block/goose/6.2-permission-modes-and-tool-approval
- Repository chính: https://github.com/langchain-ai/langgraph · https://github.com/pydantic/pydantic-ai · https://github.com/openai/openai-agents-python · https://github.com/microsoft/agent-framework · https://github.com/google/adk-python · https://github.com/huggingface/smolagents · https://github.com/OpenHands/OpenHands · https://github.com/anomalyco/opencode
