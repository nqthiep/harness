# 6. Phía TypeScript — và câu trả lời dứt khoát cho MCP

**Data collected on: 2026-08-29.** Nguồn: `registry.npmjs.org`, `npm pack` rồi đọc trực
tiếp. Bộ đo: [`harvest.py`](harvest.py), cùng bộ probe với phía Python.

## Một khác biệt về chất lượng bằng chứng, phải nói trước

Không phải gói npm nào cũng phát hành source. Đếm thật:

| Gói | Latest | `src/*.ts` | `.d.ts` | `.js` | Bằng chứng cho phép nói gì |
|---|---|---:|---:|---:|---|
| **`ai`** (Vercel) | 7.0.85 | **343** | 5 | 3 | **Source thật** — nói được cả về implementation |
| `@mastra/core` | 1.63.2 | 2 | 1.101 | 174 | dist + khai báo — chỉ nói về **API contract** |
| `@openai/agents-core` | 0.17.0 | 0 | 210 | 210 | như trên |
| `@langchain/langgraph` | 1.4.13 | 0 | 79 | 89 | như trên |
| `@modelcontextprotocol/sdk` | 1.30.0 | 0 | 172 | 172 | như trên |

Chỉ Vercel AI SDK cho đọc source. Bốn gói còn lại chỉ có JS đã biên dịch và file khai
báo kiểu. Điều đó **không** làm bằng chứng vô giá trị: `.d.ts` là **contract công khai**,
tức đúng thứ mà §23 của đề bài hỏi. Nhưng với câu hỏi *"nó thực sự làm gì bên trong"*,
với bốn gói đó câu trả lời là **Chưa đủ evidence**.

## Mật độ cơ chế (/kLOC)

| | `ai` | langgraph-ts | mastra-core | mcp-sdk | openai-agents-core |
|---|---:|---:|---:|---:|---:|
| kLOC | 76 | 21 | 435 | 70 | 134 |
| sandbox | 1,3 | 0,0 | 2,2 | 0,0 | **5,9** |
| **approval** | **7,5** | 0,0 | 1,9 | 0,0 | 2,5 |
| permission | 0,1 | 0,0 | 1,9 | **12,2** | 1,2 |
| **budget** | **0,0** | **0,0** | 0,3 | **0,0** | 0,1 |
| retry | 2,9 | 2,6 | 2,0 | 0,9 | 1,0 |
| idempot | 0,1 | 0,0 | 0,1 | 0,3 | 0,0 |
| **checkpoint** | 0,3 | **23,4** | 1,3 | 0,4 | 1,0 |
| cancel | 1,9 | 0,9 | 1,3 | 8,3 | 0,9 |
| otel | 0,1 | 0,0 | 5,1 | 0,0 | **7,3** |
| cost | 0,0 | 0,0 | 0,7 | 0,1 | 0,0 |

---

## Phát hiện 1 — MCP **không** phải security boundary, và SDK tự chứng minh điều đó

`@modelcontextprotocol/sdk` có mật độ permission **12,2/kLOC**, cao nhất trong nhóm
TypeScript. Nhưng đọc vào thì toàn bộ con số đó là **một thứ duy nhất**:

```
425  authorization              130  authorizationserverurl
106  authorizationcode           60  authorizederror
 50  authorizationservermetadata 44  authorized
 44  authorizationurl            42  authorize
```

`shared/auth.d.ts`: `authorization_servers`, `authorization_endpoint`,
`authorization_details_types_supported` — đây là **OAuth ở tầng transport**.

> MCP chuẩn hoá câu hỏi **"client này có được phép nói chuyện với server này không"**.
> Nó **không** chuẩn hoá câu hỏi **"lời gọi tool này có được phép chạy bây giờ, với
> principal này, trên tài nguyên này không"**.

Đó là **authentication của kết nối**, không phải **authorization của hành động**. Anti-
pattern "coi MCP là security boundary" vì thế được chứng minh **từ chính SDK**, không
phải từ suy luận: trong 70 kLOC của MCP SDK không có khái niệm nào về việc một *hành
động cụ thể* có được phép hay không.

Hệ quả thiết kế: một harness nhận tool từ MCP **vẫn phải tự có policy gate**. Và tool
đến từ MCP mà không khai hệ quả phải mặc định là lớp **untrusted nhất**, không phải lớp
an toàn nhất.

---

## Phát hiện 2 — Vercel AI SDK có mô hình approval tốt nhất toàn bộ nghiên cứu

**7,5/kLOC — cao hơn mọi gói Python và TypeScript đã đo**, gấp đôi Microsoft Agent
Framework (3,6). Và nó không phải nhãn dán. `src/generate-text/tool-approval-configuration.ts`:

```typescript
export type ToolApprovalStatus =
  | undefined
  | 'not-applicable' | 'approved' | 'denied' | 'user-approval'
  | { type: 'not-applicable'; reason?: never }
  | { type: 'approved';       reason?: string }
  | { type: 'denied';         reason?: string }
  | { type: 'user-approval';  reason?: string };
```

Bốn điều đáng học, mỗi điều là một quyết định thiết kế:

**1. Approval không phải boolean.** Bốn trạng thái, và `not-applicable` được **tách khỏi**
`approved` — phân biệt "không cần hỏi" với "đã hỏi và được đồng ý". Một `bool` gộp hai
thứ đó lại và mất thông tin audit.

**2. Có `reason`, và reason đi theo hai chiều.** Docstring nói rõ: với `approved`/`denied`
thì reason được phát ra trên **response**; với `user-approval` thì reason được phát ra
trên **request** để hiển thị cho người duyệt. Đây là chi tiết chỉ có khi ai đó thật sự
đã dựng UI phê duyệt.

**3. Approval có ID.** `approvalId`, `ApprovalResponses`, và `approval: { id, approved,
reason }` (`ui/chat.ts:515`). Có ID nghĩa là truy vết được, khớp được request với
response, và không nhầm hai lần duyệt khác nhau.

**4. Nó nằm trên đường thực thi, không phải ở tầng UI.** `execute-tools-from-stream.ts:108`
gọi `resolveToolApproval({ tools, toolCall, ... })` **trong** vòng thực thi tool.

`SingleToolApprovalFunction` nhận `input`, `toolContext`, `runtimeContext` và trả về
`MaybePromiseLike<ToolApprovalStatus>` — tức quyết định phê duyệt **theo từng lời gọi,
có ngữ cảnh, bất đồng bộ được**.

**Điều này lật một định kiến của chính nghiên cứu.** Vercel `ai` được xếp vào tầng
"LLM SDK" — thấp nhất trên phổ. Nhưng về approval, nó đang đi trước mọi framework và
harness trong bộ. Vị trí trên phổ không dự đoán được chất lượng của từng cơ chế.

---

## Phát hiện 3 — LangGraph: cùng một kiến trúc ở hai ngôn ngữ

| | checkpoint /kLOC |
|---|---:|
| `langgraph` (Python 1.2.11) | 21,1 |
| `@langchain/langgraph` (TS 1.4.13) | **23,4** |

2.020 lần `checkpoint`, 656 lần `checkpointer` trong bản TypeScript. Hai bản cài đặt độc
lập, hai ngôn ngữ, cùng một mật độ.

Đây là kiểm chứng chéo quan trọng: con số 21,1 của bản Python **không phải hiện tượng
của một codebase**, mà là **kiến trúc được cố ý giữ nguyên qua các bản cài đặt**. Không
cơ chế nào khác trong nghiên cứu có sự nhất quán xuyên ngôn ngữ như vậy.

Và cả hai bản đều **0,0 về budget**. Sự thiếu vắng cũng nhất quán như sự hiện diện.

---

## Phát hiện 4 — `budget` bằng 0 ở cả phía TypeScript

`ai` 0,0 · langgraph-ts 0,0 · mcp-sdk 0,0 · openai-agents-core 0,1 · mastra 0,3.

Cộng với phía Python (langgraph 0,0 · semantic-kernel 0,0 · llama-index 0,7 ·
langchain-core 0,1), kết luận về chi phí giờ đứng trên **21 gói, hai ngôn ngữ**:

> Ngành có **loop limit**, gần như không có **spend ceiling**. Và không ngôn ngữ nào khá
> hơn ngôn ngữ nào.

---

## Cập nhật bảng best-of-breed

| Hạng mục | Trước | **Sau khi đọc TypeScript** |
|---|---|---|
| **Approval / HITL** | MS Agent Framework (3,6/kLOC) | **Vercel AI SDK** — 7,5/kLOC, `ToolApprovalStatus` bốn trạng thái có `reason` và ID, giải quyết trong vòng thực thi tool |
| Durability | LangGraph (Python) | **LangGraph, cả hai ngôn ngữ** — kiểm chứng chéo 21,1 vs 23,4 |
| MCP làm plugin boundary | "có vẻ hợp lý" | **Hợp lý cho *kết nối*, không hợp lý cho *quyền hành động*** — SDK chỉ có OAuth |

## Còn thiếu — Chưa đủ evidence

**Cline, opencode, Codex, Goose, OpenHands, claude-code**: không phát hành lên npm dưới
dạng gói đọc được (Rust, hoặc phân phối qua kênh khác). Với các harness này, nghiên cứu
chỉ có metadata GitHub và nguồn thứ cấp — **không đọc được source**. Mọi nhận định về
nội bộ của chúng trong tài liệu này đều ghi rõ giới hạn đó.
