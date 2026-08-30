# Nghiên cứu Agent Framework & Agent Harness

**Data collected on: 2026-08-29**

Nghiên cứu độc lập theo prompt `agent_framework_harness_research_prompt.md`. Không liên
quan tới bất kỳ mã nguồn nào khác trong repository này.

| Tệp | Nội dung (theo §47 của đề bài) |
|---|---|
| [00 — Executive Summary](00-executive-summary.md) | Ba kết luận có bằng chứng; bảng mật độ cơ chế |
| [01 — Landscape & Taxonomy](01-landscape-and-taxonomy.md) | §1 Framework vs Harness · §2 GitHub landscape (35 repo) · §3 Tier A–D |
| [02 — Methodology & API](02-api-comparison.md) | §4 Phản biện trọng số · §6 So sánh API thật từ source |
| [03 — Safety & Reliability](03-safety-reliability.md) | §15 Reliability · §16 Security · §17 Poka-Yoke · §18 Observability · §19 Evaluation · §20 Cost |
| [04 — Weaknesses & Anti-patterns](04-weaknesses-antipatterns.md) | §26 S/W · §27 Hidden weaknesses · §28 Anti-patterns |
| [05 — Ideal Harness](05-ideal-harness.md) | §29 Best-of-breed · §30 Ranking · §31 Lessons · §32 Kiến trúc · §33 Minimal core · §35–36 API · §37 Khuyến nghị · §39 Gaps · §40 Sources |
| [07 — Tám gói Python còn lại](07-remaining-python.md) | Bảng 16 gói · **hai đính chính cho kết luận trong §00** |
| [harvest.py](harvest.py) | **Bộ đo công khai** — mọi con số tái lập bằng một lệnh |

## Tái lập

```
python3 research/harvest.py <thư-mục-các-gói-đã-giải-nén>
```

Bộ probe regex nằm trong [`harvest.py`](harvest.py), không nằm trong văn xuôi. Một bảng
số không tái lập được thì không phải bằng chứng, nó là lời khẳng định — và chính một
reviewer chỉ ra rằng bản nháp đầu tiên đã mắc lỗi đó.

## Phương pháp

Bằng chứng xếp theo đúng thứ tự §1380 của đề bài: **official source > source code >
technical documentation > credible third-party > community**.

Trọng tâm là **source code thật**: tám gói Python tải từ PyPI và đọc trực tiếp, không
qua tài liệu. Các con số cơ chế được chuẩn hoá theo kLOC để so sánh công bằng giữa gói
13 kLOC (smolagents) và gói 172 kLOC (google-adk).

## Ba giới hạn, nói trước

1. **Egress proxy chặn nhiều trang docs chính thức.** Bằng chứng nghiêng về source code
   — §45 xếp hạng cao hơn documentation, nhưng khiến phần TypeScript/Rust (Cline,
   opencode, Codex, Goose, claude-code) mỏng hơn phần Python.
2. **Không tự tạo benchmark** (§650). Mọi nhận định về performance đều ghi
   *"Chưa đủ evidence"*.
3. **Mật độ cơ chế/kLOC đo sự hiện diện, không đo tính đúng.** Một dự án có thể có
   `retry` khắp nơi mà retry vẫn sai.

## Ba kết luận

1. **Không dự án nào mạnh đều** — LangGraph hơn phần còn lại một bậc độ lớn về
   checkpoint (21,1/kLOC) nhưng budget 0,0 và OTel 0,1. Chuyên môn hoá, không phải thứ hạng.
2. **Idempotency ở mức tool call vắng mặt ở cả 16 gói** — grep có, đọc vào thì là no-op
   nội bộ, header HTTP, hoặc cache. Một ngoại lệ được tìm ra khi mở rộng phạm vi:
   **agno** có idempotency thật ở mức *run submission*. Kết luận đã được thu hẹp cho đúng
   thay vì giữ nguyên câu rộng ban đầu.
3. **Approval không phải isolation** — Goose có bốn permission mode nhưng sandbox
   seatbelt đã bị gỡ và tool chạy với quyền của user.
