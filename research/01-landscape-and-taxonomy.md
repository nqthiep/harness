# 1. Agent Framework vs Agent Harness

Đề bài coi đây là yêu cầu rất quan trọng, và nó đúng — nhưng ranh giới **không nằm ở
danh sách tính năng**, nó nằm ở **ai sở hữu vòng lặp và ai sở hữu môi trường**.

| | Framework | Harness |
|---|---|---|
| Sở hữu vòng lặp | Ứng dụng gọi framework | **Harness gọi ứng dụng** |
| Sở hữu môi trường | Không | **Có** — filesystem, shell, browser, process |
| Đơn vị công việc | Một `run()` | Một **session** có tuổi thọ |
| Thất bại nghĩa là | Exception ném cho caller | **Trạng thái cần khôi phục** |
| Người dùng cuối | Developer nhúng vào app | **Con người ngồi trước nó** |

Một phép thử phân biệt gọn hơn mọi định nghĩa:

> **Nếu bỏ nó đi, ứng dụng của bạn mất một thư viện hay mất một môi trường?**

### Phổ vị trí, đặt theo bằng chứng chứ không theo tên gọi

```
LLM SDK ──► Agent Framework ──► Agent Runtime ──► Agent Harness ──► Agent Platform

vercel/ai      pydantic-ai        langgraph        claude-code        OpenHands
               openai-agents      MS AF            opencode           dify
               smolagents                          Codex              langflow
               crewai                              Cline              agno
               haystack                            aider              Letta
               google-adk                          Goose
```

Ba lưu ý mà bằng chứng bắt phải ghi:

**smolagents nằm ở đâu?** Nó tự mô tả là "barebones library for agents that think in
code" — nghe như framework. Nhưng constructor của nó có
`executor_type: Literal["local","blaxel","e2b","modal","docker"]` và
`additional_authorized_imports`. **Nó sở hữu môi trường thực thi.** Về hành vi, đây là
harness nhỏ nhất trong nghiên cứu, không phải framework.

**LangGraph nằm ở đâu?** Không có filesystem, shell hay browser — không phải harness.
Nhưng mật độ checkpoint 21.1/kLOC và `durability: "sync"|"async"|"exit"` cho thấy nó
không chỉ là framework. Nó là **runtime**: thứ mà harness được xây *trên*.

**Topic của chính các dự án đang xoá nhoà ranh giới.** GitHub topics đọc hôm nay:
`openai/openai-agents-python` gắn topic **`harness`**; `pydantic/pydantic-ai` gắn
**`harness`** và **`harness-engineering`**. Hai framework tự nhận là harness. Đây là tín
hiệu thị trường đang hội tụ, và cũng là lý do đề bài yêu cầu không đánh đồng.

---

# 2. Market / GitHub Landscape

**Data collected on: 2026-08-29.** Nguồn: GitHub API. Không dùng stars làm tiêu chí duy
nhất (§103), nhưng ghi lại vì nó đo được.

| Repo | Stars | Forks | Open issues | Lang | Tạo | Phân loại |
|---|---:|---:|---:|---|---|---|
| obra/superpowers | 279.204 | 25.004 | 333 | Shell | 2025-10 | *skills collection* |
| affaan-m/ECC | 244.058 | 36.914 | 146 | JS | 2026-01 | *harness tooling* |
| mattpocock/skills | 240.572 | 20.452 | 437 | Shell | 2026-02 | *skills collection* |
| NousResearch/hermes-agent | 237.906 | 48.349 | 37.282 | Python | 2025-07 | agent |
| **anomalyco/opencode** | **202.281** | 26.266 | 5.621 | TS | 2025-04 | harness |
| ultraworkers/claw-code | 195.139 | 108.847 | 41 | Rust | 2026-03 | agent |
| anthropics/skills | 172.374 | 20.487 | 1.187 | Python | 2025-09 | *skills collection* |
| langflow-ai/langflow | 153.839 | 9.958 | 991 | Python | 2023-02 | visual platform |
| langgenius/dify | 153.804 | 24.309 | 980 | TS | 2023-04 | platform |
| **langchain-ai/langchain** | 145.231 | 24.236 | 432 | Python | 2022-10 | framework |
| **anthropics/claude-code** | 143.346 | 22.921 | 15.358 | Python | 2025-02 | harness |
| **openai/codex** | 119.684 | 18.284 | 14.350 | Rust | 2025-04 | harness |
| browser-use/browser-use | 111.614 | 12.250 | 388 | Python | 2024-10 | chuyên biệt |
| **OpenHands/OpenHands** | 85.510 | 11.189 | 605 | TS | 2024-03 | harness platform |
| **cline/cline** | 67.107 | 7.244 | 1.142 | TS | 2024-07 | harness |
| microsoft/autogen | 60.686 | 9.165 | 996 | Python | 2023-08 | framework |
| **crewAIInc/crewAI** | 57.782 | 8.280 | 770 | Python | 2023-10 | framework |
| run-llama/llama_index | 51.909 | 8.048 | 675 | Python | 2022-11 | framework |
| **Aider-AI/aider** | 48.568 | 4.898 | 1.833 | Python | 2023-05 | harness |
| agno-agi/agno | 41.961 | 5.834 | 1.278 | Python | 2022-05 | framework+platform |
| **langchain-ai/langgraph** | 40.650 | 6.852 | 722 | Python | 2023-08 | runtime |
| stanfordnlp/dspy | 37.647 | 3.270 | 642 | Python | 2023-01 | optimiser |
| **openai/openai-agents-python** | 29.053 | 4.627 | **42** | Python | 2025-03 | framework |
| **huggingface/smolagents** | 29.039 | 2.901 | 740 | Python | 2024-12 | harness nhỏ |
| microsoft/semantic-kernel | 28.513 | 4.744 | 264 | C# | 2023-02 | framework |
| mastra-ai/mastra | 27.553 | 2.709 | 545 | TS | 2024-08 | framework |
| vercel/ai | 26.480 | 5.041 | 1.677 | TS | 2023-05 | LLM SDK |
| deepset-ai/haystack | 26.354 | 3.049 | **113** | Python | 2019-11 | framework |
| letta-ai/letta | 24.485 | 2.600 | **39** | — | 2023-10 | memory platform |
| trycua/cua | 22.002 | 1.512 | 738 | HTML | 2025-01 | chuyên biệt |
| **google/adk-python** | 21.323 | 3.909 | 506 | Python | 2025-04 | framework |
| SWE-agent/SWE-agent | 20.170 | 2.210 | 82 | Python | 2024-04 | research harness |
| **pydantic/pydantic-ai** | 19.568 | 2.609 | 750 | Python | 2024-06 | framework |
| **microsoft/agent-framework** | 13.197 | 2.239 | 639 | Python | 2025-04 | framework |

### Ba điều bảng này nói mà bảng xếp hạng thông thường bỏ qua

**Hai dự án đã đổi tổ chức, và tài liệu cũ đang trỏ sai.** `sst/opencode` giờ là
**`anomalyco/opencode`**; `All-Hands-AI/OpenHands` giờ là **`OpenHands/OpenHands`**.
Bất kỳ nghiên cứu nào trích link cũ đều đã lỗi thời. Đây là bằng chứng cụ thể cho §46 —
độ mới của dữ liệu không phải hình thức.

**Bốn repo top-10 không phải framework hay harness.** `superpowers`, `mattpocock/skills`,
`anthropics/skills`, `agency-agents` là **bộ sưu tập prompt/skill**. Chúng đứng đầu
bảng sao nhưng không cung cấp runtime, tool contract hay safety model. Nếu xếp chúng
chung bảng với LangGraph thì bảng đó vô nghĩa — đây chính là lý do §103 cấm dùng stars
làm tiêu chí duy nhất.

**Tỉ lệ issue mở là tín hiệu mạnh hơn stars.** `openai-agents-python` có **42** issue mở
trên 29k sao; `haystack` **113** trên 26k; `letta` **39** trên 24k. Ngược lại
`claude-code` 15.358 và `codex` 14.350 — đặc trưng của sản phẩm dùng-hàng-loạt hơn là
của thư viện bảo trì kém, nhưng nó vẫn nói lên khối lượng vận hành mà maintainer gánh.

---

# 3. Frameworks Selected — phân tier và lý do

Tier dựa trên **bốn** trục, không phải một: chất lượng kiến trúc (đọc source), mức độ
production, sức khoẻ dự án, và mức độ đáng học về mặt thiết kế.

### Tier A — leading / production-grade

| Dự án | Vì sao Tier A |
|---|---|
| **LangGraph** 1.2.11 | Checkpoint 21.1/kLOC — hơn phần còn lại một bậc độ lớn. `durability: sync\|async\|exit`, `interrupt()` resumable, `CachePolicy`. Đây là runtime mà người khác xây lên trên |
| **PydanticAI** 2.36.0 | Typed end-to-end với `deps_type`/`output_type`; retry 6.5/kLOC và cancel 9.1/kLOC cao nhất; **dự án duy nhất** có `cost` đáng kể trong source; `EndStrategy` đặt tên cho một ngữ nghĩa mà các dự án khác để ngầm |
| **OpenAI Agents SDK** 0.22.0 | Sandbox 8.5/kLOC cao nhất nhóm lớn; approval 3.1; **42 issue mở** — kỷ luật bảo trì tốt nhất bảng; primitive ít, dễ học |
| **Microsoft Agent Framework** 1.16.0 | `middleware`, `context_providers`, `compaction_strategy` ngay trong constructor; OTel 7.7 và MCP 7.6/kLOC cao nhất; Python + .NET |
| **Google ADK** 2.8.0 | Permission 5.3/kLOC; Pydantic model với `extra='forbid'`; sandbox executor cho Agent Engine |
| **anthropics/claude-code** | Harness được dùng nhiều nhất theo forks/issues; **Chưa đủ evidence** về nội bộ vì không phát hành source Python đầy đủ qua PyPI |

### Tier B — highly promising

**smolagents** 1.26.0 (sandbox và permission dày đặc nhất theo kLOC, chỉ 13 kLOC — bài
học về core nhỏ), **Goose** (bốn permission mode, ACP + MCP), **Cline** (approval-first,
SDK + IDE + CLI), **OpenHands** (server/sandbox/canvas, ACP), **opencode** (đa giao
diện, 202k sao), **Mastra** (TS-native), **Letta** (stateful memory là kiến trúc chứ
không phải tính năng — và là gói duy nhất có `idempotency_header` thật).

### Tier C — specialized / niche

**Aider** (git-centric, developer-in-the-loop), **SWE-agent** (research/benchmark),
**browser-use** (browser), **trycua/cua** (computer-use, fleet OS), **DSPy** (tối ưu
chương trình LM, không phải runtime), **Haystack** (pipeline/RAG, 113 issue mở — sức
khoẻ rất tốt), **LlamaIndex** (document/OCR).

### Tier D — emerging hoặc không thuộc phạm vi

**hermes-agent**, **claw-code**, **ECC**, và các bộ **skills collection**. Ba bộ skills
đứng đầu bảng sao nhưng **không phải framework/harness** — chúng là nội dung, không phải
runtime. Ghi ở đây để bảng landscape trung thực, không phải để so sánh.

### Hai dự án bị hạ tier so với vị thế truyền thông

**CrewAI** (57.782 sao) xuống Tier C về mặt production-grade: approval **0.0/kLOC**,
sandbox 0.2, cost 0.0. Một framework mà agent có thể được giao quyền hành động nhưng
gần như không có human gate trong source là rủi ro kiến trúc, bất kể độ phổ biến.

**AutoGen** (60.686 sao) xuống Tier C vì **migration risk**: Microsoft Agent Framework
là successor đã công bố. Chọn AutoGen cho dự án mới hôm nay là chọn một đường di trú.
