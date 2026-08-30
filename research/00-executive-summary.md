# Nghiên cứu Agent Framework & Agent Harness

**Data collected on: 2026-08-29** (GitHub metadata và PyPI source đọc trực tiếp trong ngày).

> **Phương pháp — đọc trước khi tin.** Nghiên cứu này không dựa vào feature list hay
> marketing. GitHub metadata lấy qua API; **source code thật tải từ PyPI và đọc trực
> tiếp**; các con số cơ chế được chuẩn hoá theo kLOC. Chỗ nào không kiểm chứng được,
> tài liệu ghi **"Chưa đủ evidence"** thay vì suy đoán — theo đúng §45 của đề bài.
>
> Giới hạn phải nói trước: (1) egress proxy của môi trường **chặn nhiều trang docs
> chính thức**, nên bằng chứng nghiêng về *source code* — vốn được §45 xếp hạng cao hơn
> documentation, nhưng khiến phần TypeScript/Rust (Cline, opencode, Codex, Goose) mỏng
> hơn phần Python; (2) không tự tạo benchmark, theo §21; (3) điểm số là **expert
> synthesis kiểm tra được**, không phải benchmark có kiểm soát.

---

# Executive Summary

Ba kết luận, mỗi kết luận có bằng chứng đo được.

**1. "Framework tốt nhất" là câu hỏi sai. Không dự án nào mạnh đều.** Mật độ cơ chế
trong source thật (lần xuất hiện trên mỗi 1.000 dòng code) cho thấy mỗi dự án dồn kỹ
thuật vào đúng một chỗ:

| | checkpoint | sandbox | permission | retry | cancel | cost |
|---|---:|---:|---:|---:|---:|---:|
| **langgraph** 1.2.11 | **21.1** | 0.1 | 1.0 | 1.9 | 4.7 | 0.0 |
| **smolagents** 1.26.0 | 0.4 | **7.0** | **16.3** | 1.2 | 0.0 | 0.0 |
| **openai-agents** 0.22.0 | 1.0 | **8.5** | 0.9 | 2.1 | 3.6 | 0.0 |
| **pydantic-ai** 2.36.0 | 0.7 | 0.7 | 0.5 | **6.5** | **9.1** | **1.1** |
| **agent-framework** 1.16.0 | 6.2 | 0.2 | 0.6 | 0.4 | 1.9 | 0.0 |
| **google-adk** 2.8.0 | 0.5 | 1.5 | 5.3 | 1.1 | 1.4 | 0.1 |
| **crewai** 1.15.18 | 1.9 | 0.2 | 0.6 | 0.6 | 1.0 | 0.0 |

LangGraph hơn phần còn lại **một bậc độ lớn** về checkpoint — durability không phải
tính năng của nó, đó là kiến trúc của nó. smolagents có mật độ permission cao nhất
trong một gói chỉ 13 kLOC.

Bảng đầy đủ 16 gói ở [§7](07-remaining-python.md), và nó thêm hai điều: **autogen-agentchat
có kỷ luật cancellation cao nhất toàn bộ nghiên cứu (26,9/kLOC — gấp ba lần á quân)**,
truyền `CancellationToken` tường minh qua từng biên API; và **browser-use có mật độ `cost`
1,9/kLOC, cao hơn PydanticAI**, với kế toán `cost_usd`/`cost_per_token` thật. Câu đúng là:
PydanticAI là *framework đa dụng* duy nhất coi cost là khái niệm hạng nhất.

**2. Một lỗ hổng của cả ngành: không dự án nào có idempotency ở mức TOOL CALL.**
*(Đã thu hẹp sau khi đọc thêm 8 gói — xem [§7](07-remaining-python.md). Câu ban đầu
"không dự án nào có idempotency" quá rộng: **agno 3.0.1 là phản ví dụ thật** ở mức
run-submission, có partial-unique index trong Postgres và xử lý race đúng cách.)*

Với 15/16 gói còn lại, grep `idempoten*` thì có, nhưng đọc vào thì:

- `pydantic-ai` — nói về **thao tác nội bộ no-op** ("Idempotent: re-applying to an
  already-trimmed list is a no-op"), không phải bảo vệ side effect
- `letta-client` — `idempotency_header`, **idempotency tầng HTTP** của một generated client
- `openai-agents` — comment ghi rằng *lời gọi server* là idempotent
- `langgraph` — `CachePolicy(key_func=default_cache_key, ttl=...)`, tức **cache node**,
  và mặc định băm input **bằng pickle**

> **Không gói nào trong 16 gói cung cấp *exactly-once* ở mức tool call** — cho một tool
> đã gửi email hay đã ghi database. Agno bảo vệ ở mức *submit một job*, không bảo vệ một
> tool bên trong job đó khi agent tự retry. Đây là khoảng trống chung, và là lý do §33
> đặt idempotency vào **core**, không phải plugin.
>
> Cách phát hiện cũng đáng ghi: agno có mật độ `idempoten*` **0,3/kLOC — thấp hơn** gói
> mà khi đọc vào chỉ có HTTP header. Dừng ở bảng số thì kết luận đã sai.

**3. Approval không phải isolation, và ngành đang lẫn hai thứ đó.** Goose có bốn chế độ
quyền (Chat / Auto / Approve / SmartApprove) — nhưng sandbox seatbelt trên macOS **đã
được gỡ sau khi thử nghiệm**, và tiến trình chạy tool **có đúng quyền của user**
[G1][G2]. Ngược lại, smolagents đặt `executor_type: Literal["local","blaxel","e2b",
"modal","docker"]` **thẳng vào constructor** — cách ly là một quyết định người dùng buộc
phải nhìn thấy, không phải một trang tài liệu.

**Câu trả lời cho câu hỏi cuối cùng của đề bài** (§50 — nếu xây một harness mới từ zero
thì học gì, giữ gì, bỏ gì): giữ **checkpoint-as-architecture** của LangGraph, **typed
end-to-end + ngữ nghĩa được đặt tên** của PydanticAI, **sandbox-in-constructor** của
smolagents, **middleware + context provider** của Microsoft Agent Framework; bỏ
**role-play multi-agent** (CrewAI), **god-object Agent** (nhiều dự án), và **"safety by
prompt"**; và thêm thứ chưa ai làm — **idempotency và authorization envelope cho mỗi
tool call**.

[G1]: https://goose-docs.ai/blog/2026/02/23/goose-v1-25-0/
[G2]: https://deepwiki.com/block/goose/6.2-permission-modes-and-tool-approval
