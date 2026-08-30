# 7. Tám gói Python còn lại — và một kết luận bị lật

**Data collected on: 2026-08-29.** Source tải từ PyPI, đọc trực tiếp. Bộ đo công khai
tại [`harvest.py`](harvest.py) — mọi con số dưới đây tái lập được bằng một lệnh.

| Gói | Latest | kLOC |
|---|---|---:|
| agno | 3.0.1 | **421** |
| semantic-kernel | 1.44.1 | 82 |
| llama-index-core | 0.14.24 | 75 |
| langchain-core | 1.6.1 | 70 |
| browser-use | 0.13.8 | 64 |
| haystack-ai | 3.1.0 | 55 |
| dspy | 3.3.1 | 34 |
| autogen-agentchat | 0.7.5 | **11** |

## Mật độ cơ chế (/kLOC) — ghép được với bảng 8 gói đầu

| | agno | autogen | browser-use | dspy | haystack | langchain-core | llama-index | sem-kernel |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| sandbox | 1,0 | 1,3 | 1,5 | 1,8 | 0,6 | 0,1 | 0,1 | 0,6 |
| approval | 0,8 | 1,5 | **0,0** | **0,0** | 0,3 | **0,0** | **0,0** | 0,1 |
| permission | 1,2 | 0,0 | 1,1 | 0,3 | 1,8 | 0,4 | 0,1 | 0,3 |
| budget | 0,5 | **6,5** | 2,1 | 0,7 | 0,3 | 0,1 | 0,7 | **0,0** |
| retry | 1,1 | 1,5 | 1,1 | 0,7 | 1,0 | 1,0 | 0,6 | 0,1 |
| **idempot** | **0,3** | 0,0 | 0,1 | 0,1 | 0,2 | 0,0 | **0,0** | **0,0** |
| checkpoint | 0,8 | 1,4 | 0,3 | 0,6 | 0,1 | **0,0** | **0,0** | **0,0** |
| cancel | 3,9 | **26,9** | 1,7 | 1,2 | 0,6 | 0,5 | **0,0** | 2,5 |
| otel | 1,9 | 0,1 | 0,6 | 0,3 | **5,1** | 0,8 | 0,9 | 1,5 |
| cost | 0,3 | 0,0 | **1,9** | 0,6 | 0,1 | 0,1 | **0,0** | 0,0 |
| mcp | 1,1 | 0,5 | 2,7 | 0,7 | 0,2 | **0,0** | **0,0** | 1,9 |

---

## ĐÍNH CHÍNH — kết luận trung tâm bị lật một phần

Báo cáo ban đầu viết: *"Không dự án nào cung cấp idempotency cho side effect."*
**Câu đó quá rộng.** Agno 3.0.1 là phản ví dụ thật, và nó được làm cẩn thận.

`agno/db/postgres/postgres.py:7393` — không phải comment, mà là logic có index hỗ trợ:

```python
# Idempotency FIRST: resubmitting an already-accepted job
# must return the existing run even when the queue is full
if job.get("idempotency_key"):
    row = sess.execute(select(table).where(
        table.c.idempotency_key == job["idempotency_key"],
        table.c.user_id.is_not_distinct_from(job.get("user_id")),
    )).fetchone()
    if row is not None:
        return {"accepted": False, "reason": "duplicate", "job": dict(row._mapping)}
```

Ba chi tiết cho thấy đây là kỹ thuật thật, không phải nhãn dán:

1. **Partial-unique index trong DB**, không phải kiểm tra trong bộ nhớ — nên nó đúng cả
   khi có nhiều tiến trình.
2. **Xử lý race**: khi hai submit cùng key chạm index đồng thời, code bắt `IntegrityError`
   rồi *đọc lại để trả về người thắng*, thay vì để lỗi 500.
3. **Phân biệt lỗi thật với dedup**, và comment nói rõ vì sao — *"Swallowing it as
   'duplicate' would 202 a run that was never enqueued."* Đây là loại phân biệt mà chỉ
   người từng bị lỗi đó mới viết ra.

### Kết luận được thu hẹp cho đúng

> **Không gói nào trong 16 gói cung cấp idempotency ở mức TOOL CALL** — tức exactly-once
> cho một tool đã gửi email hay đã ghi database.
>
> **Agno cung cấp idempotency ở mức RUN SUBMISSION**, đúng cách, ở tầng platform
> (`AgentOS`) chứ không ở tầng thư viện.

Khác biệt này quan trọng chứ không phải chẻ chữ: idempotency của Agno ngăn *chạy lại cả
một job*; nó **không** ngăn một tool bên trong job đó thực hiện side effect hai lần khi
agent tự retry. Hai vấn đề khác nhau, và vấn đề thứ hai vẫn chưa ai giải.

Ghi lại cả cách phát hiện: grep cho `idempoten*` ở agno chỉ ra **0,3/kLOC** — thấp hơn
`letta-client` (0,8), gói mà khi đọc vào chỉ có HTTP header. Nếu dừng ở bảng số thì kết
luận đã sai. **Đây là bằng chứng cho chính giới hạn số 4 ghi trong `harvest.py`: mọi con
số nổi bật phải được đọc code xác minh, và ở đây con số KHÔNG nổi bật mới là con số đúng.**

---

## ĐÍNH CHÍNH THỨ HAI — "PydanticAI là dự án duy nhất có cost"

Cũng sai. **browser-use 0.13.8 có mật độ `cost` 1,9/kLOC, cao hơn PydanticAI (1,1)**, và
đọc vào thì là kế toán thật: `cost_per_token`, `cost_usd`, `cost_service`, `cost_logger`,
cùng `_usage_from_events_with_costs()` dựng lại usage từ terminal event và tính giá theo
từng lời gọi (`beta/service.py:3197`).

Câu đúng phải là: **PydanticAI là framework đa dụng duy nhất coi cost là khái niệm hạng
nhất; browser-use — một harness chuyên biệt — còn làm kỹ hơn trong phạm vi hẹp của nó.**

---

## Phát hiện mới

### autogen-agentchat: kỷ luật cancellation cao nhất toàn bộ nghiên cứu

**26,9/kLOC** — gấp ba lần dự án đứng thứ hai (pydantic-ai 9,1). Không phải nhiễu: 183
lần `cancellation_token` là **tham số tường minh** trên API, cộng 84 lần `CancellationToken`.

```python
# teams/_group_chat/_base_group_chat.py:251
cancellation_token: CancellationToken | None = None,
# """The cancellation token to kill the task immediately."""
```

Đây là mẫu actor/.NET: huỷ được **truyền tay qua từng biên**, không dựa vào cơ chế ngầm
của runtime. Đắt hơn về mặt API surface, nhưng là cách duy nhất khiến việc huỷ trở nên
kiểm tra được. **Một framework mang tiếng "sắp bị thay thế" lại đang giữ bài học tốt
nhất về cancellation trong cả bộ.**

### langchain-core và llama-index-core: gần như trống về mặt runtime

| | approval | checkpoint | cancel | mcp | cost |
|---|---:|---:|---:|---:|---:|
| langchain-core 1.6.1 | 0,0 | 0,0 | 0,5 | 0,0 | 0,1 |
| llama-index-core 0.14.24 | 0,0 | **0,0** | **0,0** | **0,0** | **0,0** |

`llama-index-core` là gói **duy nhất trong 16 gói** có **năm** chỉ số bằng 0. Điều này
**không** có nghĩa nó tệ — nó có nghĩa hai gói này là **thư viện thành phần**, không phải
runtime. LangChain đẩy runtime sang LangGraph; LlamaIndex tập trung vào document/RAG.
Đọc bảng này như "kém an toàn" là đọc sai vai trò.

Nhưng nó dẫn tới một hệ quả thực tế: **chọn langchain-core hay llama-index-core làm nền
cho agent production nghĩa là tự xây toàn bộ tầng approval, checkpoint và cancellation.**

### semantic-kernel: budget 0,0 và checkpoint 0,0

Trên 82 kLOC. Cộng với việc Microsoft Agent Framework là successor đã công bố, đây là
bằng chứng định lượng cho khuyến nghị không chọn SK cho dự án mới.

### agno: lớn nhất, và sự lớn đó nói lên điều gì

**421 kLOC** — gấp 2,4 lần gói lớn thứ hai (google-adk 172). Vì nó không chỉ là thư viện
mà là **platform** (`agno/os/routers/...`, job queue, Postgres layer). Sự lớn ấy là lý do
nó có idempotency thật — và cũng là lý do so sánh agno với pydantic-ai theo mật độ là so
sánh hai loại vật khác nhau.

---

## Cập nhật anti-pattern

| Anti-pattern | Bằng chứng mới |
|---|---|
| **Thư viện thành phần bị dùng như runtime** | langchain-core và llama-index-core có approval/checkpoint 0,0 — không phải khuyết điểm của chúng, mà là khuyết điểm của việc chọn sai lớp |
| **Successor debt đo được** | semantic-kernel: budget 0,0, checkpoint 0,0 trên 82 kLOC |
| **Bảng số không thay được việc đọc code** | Agno có idempotency **thấp nhất nhì** theo grep nhưng là gói **duy nhất** làm đúng |
