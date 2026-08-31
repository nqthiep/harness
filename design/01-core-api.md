# 01 — Core API

**Tệp này tuân theo [`00-foundation.md`](00-foundation.md).** Từ vựng (`Effect`, `Verdict`,
`Decision`, `Actor`, `Ledger`, `Label`, `Run`, `ToolSpec`), hai lattice, và bốn quy tắc
kiến trúc R-1…R-4 được lấy nguyên từ đó, không định nghĩa lại ở đây.

Phạm vi: **bề mặt công khai** — cái mà 90% người dùng chạm vào. Policy engine ở
[`02`](02-safety-engine.md), tool/MCP ở [`03`](03-tools-and-mcp.md), graph runtime ở
[`04`](04-runtime-durability.md), `Ledger` ở [`05`](05-cost-and-memory.md).

---

## 1. API công khai tối thiểu

Bề mặt **tối thiểu** (đủ cho Mức 0–2, xem §2) là **14 tên**, một `import`. Đây KHÔNG phải
toàn bộ bề mặt — Mức 3 (§2, production) cần thêm — một bản nháp trước của mục này tuyên bố
"toàn bộ" rồi chính ví dụ Mức 3 trong CÙNG tệp `import` 20 tên từ 4 module, tự mâu thuẫn
([review-kiss.md](review-kiss.md) K-22). Con số trung thực hơn: **14 tên tối thiểu + tới
20 tên khi dùng hết production** (checkpoint, plugin, policy MCP), qua **1 import gốc +
tối đa 3 submodule** khi thật sự cần chúng.

```python
from harness import (
    Agent, tool, Effect, ToolSpec,          # định nghĩa
    Budget, Workspace,                       # trần chi tiêu, cách ly
    Approver, Answer, Verdict, Decision,     # phê duyệt
    Plugin, Result, StopReason, Label,       # mở rộng, kết quả
)
```

### 1.1 `Agent`

```python
from typing import Generic, Literal, Sequence, TypeVar

OutT  = TypeVar("OutT")

EndStrategy = Literal["early", "graceful", "complete"]

class Agent(Generic[OutT]):
    def __init__(
        self,
        *,                                                  # keyword-only, không ngoại lệ
        name: str,
        job: str,
        model: ModelProvider | ModelName,
        budget: Budget | str,
        tools: Sequence[ToolSpec] = (),
        sandbox: Sandbox | None = None,
        output_type: type[OutT] = str,
        policies: Sequence[Policy] = (),
        approve: Approver | None = None,
        plugins: Sequence[Plugin] = (),
        checkpointer: Checkpointer | None = None,
        end_strategy: EndStrategy = "graceful",
    ) -> None: ...
```

Mười ba tham số, **và mỗi cái tồn tại vì một phát hiện đo được**:

| tham số | vì sao có mặt | nguồn |
|---|---|---|
| `model` là tham số bắt buộc, không có mặc định | Google ADK để `DEFAULT_MODEL: ClassVar[str] = 'gemini-3.5-flash'` — affinity nhà cung cấp giấu trong class variable. MS Agent Framework làm đúng: `client` bắt buộc, dependency inversion cưỡng chế | ([§02](../research/02-api-comparison.md) §6) |
| `budget` **bắt buộc**, phải có trục tiền | Cả ngành có loop limit mà gần như không có spend ceiling: pydantic-ai 3,6 budget/kLOC là cao nhất, LangGraph 0,0; `max_turns` chặn số vòng, còn một vòng 200k token đắt gấp trăm lần | ([§03](../research/03-safety-reliability.md) §20) |
| `sandbox` không có mặc định "local" | smolagents đưa `executor_type` vào constructor — thiết kế cách ly tốt nhất trong nghiên cứu — rồi để mặc định `"local"`, làm giảm giá trị của chính cơ chế đó | ([§05](../research/05-ideal-harness.md) §31 bài học 2 và 10) |
| `output_type` | PydanticAI typed end-to-end là Agent API duy nhất đạt "Type safety cao nhất bảng" | ([§02](../research/02-api-comparison.md) §6) |
| `end_strategy` | Model trả **vừa** kết quả cuối **vừa** tool call là ngữ nghĩa khó. PydanticAI đặt tên cho nó, cho ba lựa chọn, và đổi mặc định từ `early` sang `graceful` vì mặc định cũ sai. Giá trị thứ ba đổi tên từ `"exhaustive"` gốc của PydanticAI thành `"complete"` — `"exhaustive"` đã là tên một trong ba **parallel mode** khác hẳn của chính pydantic-ai ([03 §3.2](03-tools-and-mcp.md)), và dùng lại nó ở đây cho một trục không liên quan là tự tạo va chạm từ vựng trong cùng một bản thiết kế ([review-kiss.md](review-kiss.md) K-11) | ([§02](../research/02-api-comparison.md) §6) |
| `approve` nhận `Approver`, trả `Answer` — không bao giờ `bool` | Approval ở đâu cũng là trạng thái quyền chứ không phải sự kiện audit được; Java `ToolConfirmation` đúng một `boolean` | ([§09](../research/09-memory-context-multiagent-hitl.md) §14.1, [§10](../research/10-governance-health-languages.md) §28) |
| `checkpointer` | Durability là kiến trúc hoặc không tồn tại: LangGraph 51,6 recover/kLOC so với phần còn lại < 2. Và nó chỉ cài được vì có topology | ([§11](../research/11-workflow-and-dx.md) §12) |
| `plugins` | around-hook — xem §4 | ([§08](../research/08-tool-mcp-plugin.md) §24) |
| `policies` | verdict lattice hợp bằng `max()`, chỉ thắt chặt | [`00`](00-foundation.md) §3.1 |
| `*` keyword-only | `extra='forbid'` của Google ADK biến typo thành lỗi lúc dựng, rẻ và hiệu quả. Keyword-only là phiên bản Python thuần của cùng ý tưởng | ([§02](../research/02-api-comparison.md) §6, [§05](../research/05-ideal-harness.md) §31 bài học 6) |

**`sandbox` bắt buộc khi nào.** Không phải lúc nào cũng bắt buộc — bắt buộc *khi liên quan*.
Nếu `tools` chứa bất kỳ tool nào có `Effect.WRITE`, `Effect.EXTERNAL` hoặc `Effect.DANGER`
mà `sandbox=None`, đó là **lỗi lúc construction**. Một agent không tool, hoặc chỉ có tool
`read`, không chạm vào thế giới nên không cần chọn. Điều này giữ hello-world ở 5 dòng mà
không nới lỏng Poka-Yoke: cái gì nguy hiểm khi thiếu thì không được có mặc định
([§05](../research/05-ideal-harness.md) §35–36).

### 1.2 Bốn cách chạy

```python
class Agent(Generic[OutT]):
    async def run(self, prompt: str, *, run_id: RunId | None = None) -> Result[OutT]: ...
    async def try_run(self, prompt: str, *, run_id: RunId | None = None) -> Result[OutT]: ...
    def stream(self, prompt: str, *, run_id: RunId | None = None) -> AsyncIterator[Event]: ...
    async def resume(self, run_id: RunId, *, answer: Answer | None = None) -> Result[OutT]: ...
    async def cancel(self, run_id: RunId) -> None: ...

    def run_sync(self, prompt: str, *, run_id: RunId | None = None) -> Result[OutT]: ...
```

`run()` **raise** khi run không hoàn tất; `try_run()` **luôn** trả `Result`. Cả hai trả
cùng một kiểu, nên đổi giữa chúng không phải viết lại code xử lý kết quả.

Interface mẫu `interface Agent { AgentResult run(AgentRequest) }` thiếu **năm** thứ mà
bằng chứng nói là bắt buộc: streaming, cancellation, session, approval round-trip, và
idempotency ([§05](../research/05-ideal-harness.md) §35–36). **Ba cái đầu ở trên** —
streaming, cancellation, approval round-trip. `session` KHÔNG có ở đây: không có kiểu
`Session` nào trong tệp này hay `02`/`04`/`05`, chỉ có `run_id`; tenant/owner/TTL — ranh
giới cách ly mà `session` phải cung cấp — nằm ở *Chưa đủ evidence* của ba tệp
(`02`, `04`, `05`). Trung thực hơn là nói rõ: session là phạm vi của **tầng service** bọc
quanh harness, không phải của chính harness ([review-kiss.md](review-kiss.md) K-28, xem
`07-risks`). idempotency không nằm trên bề mặt vì gateway **sinh** key chứ không nhận
— người dùng không thể quên cái mà họ không được phép cung cấp.

`cancel()` là **tín hiệu**, không phải một stop reason mà model tự chọn — smolagents có
Security 9 nhưng Runtime 4 vì `cancel` 0,0/kLOC ([§05](../research/05-ideal-harness.md) §30).

### 1.3 `@tool`

```python
@overload
def tool(fn: Callable[..., Any], /) -> NoReturn: ...          # thiếu effect -> lỗi ngay
@overload
def tool(*, effect: Effect, name: str | None = None,
         ) -> Callable[[Callable[..., OutT]], ToolSpec]: ...
```

Người viết tool khai **đúng một thứ**: `effect`. Năm hành vi — song song, retry, taint,
verdict mặc định, mức audit — là *dẫn xuất* ([`00`](00-foundation.md) §2). Không có cờ
`sequential=`, không có `handle_tool_error=`, không có `retries=` trên từng tool — và
**không có `accepts_tainted=` lẫn `max_confidentiality=`**, xem [03 §1.1](03-tools-and-mcp.md).

`@tool` không có dạng gọi trần (`@tool` không tham số): overload đầu tiên trả `NoReturn`
nên **type checker báo lỗi trước cả khi chạy**, và runtime raise `MissingEffectError` lúc
import. Đây là hàng đầu tiên trong bảng Poka-Yoke: tool không khai `effect` bị chặn ở
*import time* ([§05](../research/05-ideal-harness.md) §35–36).

`ToolSpec` giữ lại `__call__` uỷ nhiệm cho hàm gốc, nên tool vẫn unit-test được trực tiếp
mà không cần dựng `Agent`.

### 1.4 `Approver` và `Answer` — sửa phát hiện số một

> **Ba tên, ba vai, đừng lẫn.** `Ruling` là điều **policy** phán
> ([02 §1](02-safety-engine.md)); `Answer` là điều **con người** trả lời; `Decision` là
> bản ghi bất biến mà **runtime** niêm phong từ `Answer` + `Actor` + `Scope`
> ([00 §4](00-foundation.md)). Chỉ `Decision` đi vào audit log.

```python
@value
class Answer:
    verdict: Literal[Verdict.ALLOW, Verdict.DENY]   # ASK không bao giờ là kết quả cuối
    reason: str                                      # bắt buộc, cả khi ALLOW
    expires_at: datetime | None = None

@value
class ApprovalRequest:
    scope: Scope                 # tool + giá trị tham số + server — 00 §4.1
    reason: str                  # policy nào yêu cầu hỏi
    label: Label                 # context đang ở nhãn nào
    estimated_cost: Money

class Approver:
    def __init__(self, fn: Callable[[ApprovalRequest], Awaitable[Answer]],
                 *, actor: Actor) -> None: ...
```

**`Approver` trả `Answer`, không trả `Decision`.** Đây là cách bất biến D-1 được thực thi
*bằng kiểu*, không bằng review: người duyệt chỉ điền `verdict`, `reason`, `expires_at`;
runtime niêm phong thành `Decision` với `id`, `decided_at`, `run_id`, và `actor` — mà
`actor` được gắn **lúc dựng `Approver`**, không phải mỗi lần gọi. Không có đường nào để
một `Decision` mang actor do bên được duyệt tự khai. Đây chính là chỗ agno sai: nhật ký
audit dày nhất trong 23 gói Python là `decision_log` — một tool mà chính model gọi để tự
ghi về mình ([§09](../research/09-memory-context-multiagent-hitl.md) §14.1).

`reason` bắt buộc cả khi `ALLOW`: một grant không có lý do không audit được sáu tháng sau.

---

## 2. Zero-to-Agent — progressive disclosure

Nghiên cứu trả lời rất thẳng câu "bao lâu để có agent production": **dưới một giờ tới một
demo ở bất kỳ framework nào — đó là bài toán đã giải và không phải điểm phân biệt; phần
production thì framework không đưa bạn tới, và khoảng cách đó không phải tài liệu**
([§11](../research/11-workflow-and-dx.md) §22). Bốn mức dưới đây được thiết kế để **mỗi
mức mới lộ ra đúng một khái niệm**, và mức 4 là chỗ nghiên cứu nói cả ngành bỏ trống.

### Mức 0 — chạy được: 5 dòng

```python
from harness import Agent

agent = Agent(name="Helper", job="Trả lời ngắn gọn bằng tiếng Việt.",
              model="claude-opus-5", budget="$0.05")
print(agent.run_sync("Thủ đô của Việt Nam là gì?").text)
```

Bốn tham số. `budget` là tham số thứ tư vì nó bắt buộc — và đó là **chủ ý**: thứ duy nhất
buộc người mới gõ thêm một dòng chính là thứ cả ngành không có
([§03](../research/03-safety-reliability.md) §20). `"$0.05"` parse thành `Budget`; sai cú
pháp là `InvalidBudgetError` lúc construction.

### Mức 1 — thêm tool: +5 dòng

```python
from harness import Agent, Effect, tool

@tool(effect=Effect.READ)
def word_count(text: str) -> int:
    """Đếm số từ trong một đoạn văn bản."""
    return len(text.split())

agent = Agent(name="Helper", job="Đếm từ khi được hỏi.",
              model="claude-opus-5", budget="$0.05",
              tools=[word_count])
print(agent.run_sync("Câu 'xin chào các bạn' có mấy từ?").text)
```

Khái niệm mới: **đúng một** — `effect`. Không `sandbox` vì bộ tool toàn `read`. Schema
sinh từ type hints; docstring thành mô tả tool.

### Mức 2 — thêm tác dụng phụ: sandbox và approval xuất hiện *vì bộ tool đổi*

```python
from harness import (Agent, Approver, Channel, Effect, Human, Answer,
                     Verdict, Workspace, tool)

@tool(effect=Effect.WRITE)
def save_note(path: str, body: str) -> str:
    """Ghi một ghi chú vào workspace và trả về đường dẫn đã ghi."""
    ...

async def ask_terminal(req):
    print(f"  {req.scope.tool}({dict(req.scope.args or {})})")
    print(f"  vì: {req.reason} · ước tính {req.estimated_cost}")
    ok = input("  duyệt? [y/N] ") == "y"
    return Answer(verdict=Verdict.ALLOW if ok else Verdict.DENY,
                  reason="người dùng trả lời ở terminal")

agent = Agent(
    name="Notetaker", job="Ghi chú theo yêu cầu của người dùng.",
    model="claude-opus-5",
    budget="$0.20, 15 steps, 60s",
    tools=[save_note],
    sandbox=Workspace("./ws", egress="deny"),
    approve=Approver(ask_terminal, actor=Human(id="nqthiep", via=Channel.CLI)),
)
```

Bỏ `sandbox=` ra khỏi đoạn này thì **không chạy được** — `UnsafeToolSetError` lúc
construction, kèm tên tool và effect của nó. Bỏ `approve=` cũng vậy, vì `write` có verdict
mặc định `ASK` ([`00`](00-foundation.md) §2) và một `ASK` không có người trả lời là bế tắc
biết trước. Cả hai lỗi xảy ra **trước khi tiêu một xu**.

Đây là chỗ khác builder pattern `.withTimeout().withBudget().run()`: builder cho phép gọi
`.run()` mà không đặt gì cả ([§05](../research/05-ideal-harness.md) §35–36).

### Mức 3 — production: 4 khái niệm còn lại

```python
import asyncio
from harness import Agent, Approver, Human, Channel, Workspace, StopReason
from harness.checkpoint import SqliteCheckpointer
from harness.plugins import Retry, CostReport
from harness.policy import DenyHosts

agent = Agent(
    name="Notetaker", job="Ghi chú theo yêu cầu của người dùng.",
    model="claude-opus-5",
    budget="$0.20, 15 steps, 60s",
    tools=[save_note, fetch_page],
    sandbox=Workspace("./ws", egress="allowlist:docs.python.org"),
    approve=Approver(ask_terminal, actor=Human(id="nqthiep", via=Channel.CLI)),
    policies=[DenyHosts("*.internal")],
    plugins=[Backoff(on=("rate_limited", "unavailable"), attempts=3), CostReport()],
    checkpointer=SqliteCheckpointer("./runs.db"),
    # `end_strategy` KHÔNG lên ở đây có chủ ý (review-kiss.md K-11): mặc định đã là
    # "graceful", và nó không phải một trong bốn khái niệm còn thiếu của cả ngành mà
    # mục này minh hoạ (retry, cost report, durable checkpoint, deny-hosts) — chỉ khai
    # nó khi thật sự cần đổi khỏi mặc định.
)

async def main() -> None:
    async for ev in agent.stream("Tóm tắt trang X rồi ghi vào note.md", run_id="r-42"):
        print(ev.type, ev.sequence)

    r = await agent.try_run("Tiếp tục", run_id="r-42")
    if r.stop_reason is StopReason.AWAITING_DECISION:
        r = await agent.resume("r-42", answer=await ask_terminal_for(r.pending))
    print(r.output, r.cost, r.label, len(r.decisions))

asyncio.run(main())
```

Bốn khái niệm mới, và chúng là **bốn chỗ trống của cả ngành**, không phải bốn tính năng
thêm cho vui: durability (`checkpointer` — có được vì runtime là graph,
[§11](../research/11-workflow-and-dx.md) §12), policy chỉ-thắt-chặt, plugin cho retry/cost,
và approval round-trip qua `AWAITING_DECISION` → `resume(answer=…)`.

**Tổng cộng: 5 → 10 → 22 → 30 dòng.** Không mức nào phải viết lại mức trước; mỗi mức chỉ
thêm tham số vào cùng một constructor.

---

## 3. Extreme DX — đứng ở đâu, và vì sao

Nghiên cứu đo DX chứ không nhận xét DX ([§11](../research/11-workflow-and-dx.md) §22).
Ba con số, ba lập trường:

### 3.1 Type hints — `py.typed` không còn là điểm khác biệt

**11/12 gói ship `py.typed`** ([§11](../research/11-workflow-and-dx.md) §22); hai năm
trước bảng đó chủ yếu là "no". Ship `py.typed` bây giờ là **điều kiện tối thiểu**, không
phải điểm mạnh — nên nói "chúng tôi có type hints" là nói không có gì.

Lập trường cụ thể hơn, đo được:

- ship `py.typed`;
- **không có `Any` trong signature công khai** — 14 tên ở §1 phải kiểm được bằng
  `mypy --strict` từ phía *người dùng*, không chỉ từ phía source;
- `Agent` generic trên `OutT` để `result.output` có kiểu thật, học PydanticAI
  ([§02](../research/02-api-comparison.md) §6);
- **0 tệp `.pyi`.** Microsoft là dự án duy nhất ship stub (23 tệp), và đó là việc đúng
  *khi runtime type động* ([§11](../research/11-workflow-and-dx.md) §22). Kiểu của harness
  này tĩnh, nên stub là bảo trì thêm mà không mua được gì — KISS, cắt.
- **≤ 3 required dependency trong core.** Bảng deps trải từ 1 (autogen-agentchat) tới 31
  (crewai) ([§11](../research/11-workflow-and-dx.md) §22); provider và store đi bằng extras.

### 3.2 Thông báo lỗi — 100% có ngữ cảnh, bằng cấu trúc chứ không bằng kỷ luật

Tỉ lệ "errors with context" trải **27%–61%**: smolagents 61% (cao nhất), haystack 48%,
openai-agents 30%, llama-index 27% ([§11](../research/11-workflow-and-dx.md) §22). Và
nghiên cứu tự cảnh báo: chỉ số này **đo hình thức, không đo chất lượng** — một f-string
vẫn có thể vô dụng ([§11](../research/11-workflow-and-dx.md) §45).

Nên harness này nhắm cả hai, và làm cho *không thể vi phạm*:

```python
class HarnessError(Exception):
    def __init__(self, *, what: str, got: object = _UNSET,
                 fix: str, doc: str) -> None: ...
```

`fix` và `doc` là **keyword bắt buộc**. Không có cách nào raise một lỗi của harness mà
không nói phải làm gì. Tỉ lệ error-context là 100% *theo cấu trúc*, và một test CI dùng
lại chính probe của `research/harvest.py` để chứng minh nó không tụt.

Ba yêu cầu về nội dung, mỗi cái sửa một kiểu thông báo vô dụng:

1. **Nội suy giá trị gây lỗi** — `got=` bắt buộc render vào message, không phải "invalid
   budget" mà là giá trị người dùng đã gõ.
2. **Nói phải làm gì** — `fix` là câu mệnh lệnh, không phải mô tả điều kiện.
3. **Một neo tài liệu** — `doc` trỏ tới đúng mục, không trỏ trang chủ.

```
InvalidBudgetError: budget cần một trục tiền.

  Bạn viết:  budget="15 steps, 60s"
  Viết:      budget="$0.20, 15 steps, 60s"

  Giới hạn số bước không phải giới hạn chi tiêu: một vòng 200k token
  đắt gấp trăm lần một vòng ngắn, nên đếm vòng không kiểm soát được tiền.

  -> design/01-core-api.md §1.1
```

### 3.3 Docstring — nhắm bề mặt, không nhắm mật độ

Mật độ docstring trải **21,3–38,0/kLOC**, và biến thiên 2–3× **không tương quan với quy
mô, hậu thuẫn, hay độ phổ biến** của dự án ([§11](../research/11-workflow-and-dx.md) §22).
Mật độ cũng là proxy: docstring của module và của class được đếm ngang docstring hàm
([§11](../research/11-workflow-and-dx.md) §45).

Nên mục tiêu không phải một con số/kLOC mà là: **100% ký hiệu công khai có docstring**, và
mật độ rơi vào đâu thì rơi. Nhắm vào chỉ số proxy là tối ưu hoá cái thước.

### 3.4 Lỗi xảy ra sớm nhất có thể

| sai lầm | bị chặn ở | cơ chế |
|---|---|---|
| tool không khai `effect` | **import time** | `@tool` không có dạng gọi trần; overload trả `NoReturn` |
| `sandbox=` thiếu khi bộ tool có write/external/danger | **construction** | kiểm bộ tool trong `__init__` |
| `budget` thiếu trục tiền | **construction** | parser của `Budget` |
| gõ sai tên tham số | **construction** | keyword-only + `TypeError` của Python |
| `approve` trả `bool` | **type error** | `Approver` yêu cầu `Awaitable[Answer]` |
| tool call thiếu idempotency key | **không thể xảy ra** | gateway sinh key, API không nhận |

Bảng này là bản rút gọn của Poka-Yoke matrix ([§05](../research/05-ideal-harness.md)
§35–36); bản đầy đủ ở [`06-poka-yoke-matrix.md`](06-poka-yoke-matrix.md).

---

## 4. Plugin API — hai around-hook, và ranh giới không được vượt

### 4.1 Học ai

`AgentMiddleware` của LangChain 1.x là **thiết kế mở rộng mạnh nhất tìm được trong toàn
nghiên cứu** ([§08](../research/08-tool-mcp-plugin.md) §24). Lý do không phải mật độ
(24,3 middleware/kLOC) mà là *hình dạng*: trong sáu extension point, cặp `wrap_model_call`
/ `wrap_tool_call` là **around-hook nhận `handler`**, nên nó quyết định gọi hay không gọi,
gọi khác đi, hay gọi hai lần. Hệ quả: retry, cache, approval gate, fallback model và cost
accounting đều biểu diễn được **mà framework không cần ship tính năng riêng nào**. Đó là
Open/Closed đạt được thật.

```python
ModelHandler = Callable[[ModelRequest], Awaitable[ModelResponse]]
ToolHandler  = Callable[[ToolRequest],  Awaitable[ToolOutcome]]

class Plugin:
    name: str

    async def wrap_model_call(self, req: ModelRequest,
                              handler: ModelHandler) -> ModelResponse:
        return await handler(req)

    async def wrap_tool_call(self, req: ToolRequest,
                             handler: ToolHandler) -> ToolOutcome:
        return await handler(req)
```

**Hai hook, không phải sáu.** LangChain có thêm `before_agent` / `before_model` /
`after_model` / `after_agent`, nhưng bốn cái đó là dòng đầu và dòng cuối của một around-hook.
Cặp `wrap_*` là phần không thay thế được ([§08](../research/08-tool-mcp-plugin.md) §24);
bốn cái kia là đường tắt. KISS: cắt bốn, giữ hai.

`plugins=[a, b, c]` lồng theo thứ tự khai báo: `a` ngoài cùng, tool/model call trong cùng.

**Plugin phải stateless.** State theo run đi trong `req.scratch: MutableMapping[str, Any]`,
sống trong state đã checkpoint. Đây là R-4 được thực thi bởi chính hình dạng API: nếu
plugin giữ state trong `self`, hai run đồng thời đọc nhầm của nhau — đúng lỗi Microsoft
mắc với `threading.local()` ([`00`](00-foundation.md) §3.2, [§09](../research/09-memory-context-multiagent-hitl.md) §16bis).

### 4.2 Sửa khuyết điểm nào — R-1 là ranh giới

Extension point cũng là **attack surface và đường bypass**. Bằng chứng cụ thể: lattice
information-flow của Microsoft — cài đặt an toàn tinh vi nhất trong nghiên cứu — là
middleware, và vì là middleware nên *không cài* là mặc định: chính `_harness/` của họ
không import nó ([§08](../research/08-tool-mcp-plugin.md) §24,
[§09](../research/09-memory-context-multiagent-hitl.md) §16bis). Một approval gate viết
dưới dạng plugin chỉ bảo vệ những agent mà ai đó nhớ bọc.

Cách giải không phải chọn một bên, mà là **đặt hai loại kiểm tra ở hai vị trí khác nhau so
với chuỗi plugin**:

| kiểm tra | vị trí so với chuỗi plugin | plugin bỏ qua được? |
|---|---|---|
| policy verdict (`max()` lattice) | **trước** chuỗi | không — không qua thì chuỗi không được vào |
| taint check (`Label`) | **trước** chuỗi | không |
| yêu cầu `Decision` khi verdict là `ASK` | **trước** chuỗi | không |
| budget `reserve` / `settle` | **trong** handler | không — mỗi lần gọi handler đều bị tính tiền |
| idempotency key | **trong** handler | không — gateway sinh |
| audit event | **trong** handler | không |
| retry · cache · fallback · cost report · log | **là** chuỗi | đó chính là việc của plugin |

Hai quy tắc, phát biểu gọn:

- **Gate-before** — cái mà bỏ qua là *lỗi bảo mật* (permission, taint, `Decision`) nằm
  **trước** chuỗi. Plugin không bao giờ nhận được `handler` nếu gate chưa cho qua.
- **Gate-inside** — cái mà một plugin có thể *lặp lại* (model call, tool call) có gate nằm
  **bên trong** handler. Một `Retry` gọi handler ba lần thì reserve ba lần, ghi audit ba
  lần. Retry không dodge được ngân sách.

**Tính chất PLUG-1 (plugin chỉ làm yếu đi)** — đổi tên từ `P-3` gốc (K-13,
`07-risks-and-open-issues.md` §1.5): số `P-3` va chạm với `design/02 §1.2`'s P-3 riêng
("fail closed khi policy ném lỗi", nay `POL-3`) và `docs/09-testing.md`'s P-3 riêng
(property-test ID, "mỗi `tool_use` đúng một `tool_result`") — ba nghĩa khác nhau, cùng
một số. `PLUG-1`: với mọi danh sách plugin `P`, tập tác dụng phụ
mà một run thực hiện được khi cài `P` là **tập con** của tập khi không cài gì. Plugin có
thể bỏ qua, không thể nới rộng. Chứng minh được bằng property-based test, cùng cách P-2
được chứng minh ([`00`](00-foundation.md) §3.1) — vì lý do ở R-2: 23 vòng review tìm ra
0 lỗi bảo mật, 16 vòng *chạy* tìm ra 4 ([`00`](00-foundation.md) §5).

Ba thứ plugin **không có API để chạm tới**, và đó là danh sách đóng: `Ledger` (chỉ đọc
qua `req.remaining`), `Decision` (không có constructor công khai — chỉ runtime niêm phong
từ `Answer`), và `Verdict` (không nằm trong `ToolRequest`). Cùng lý do R-3: model không
cầm công tắc an toàn nào, và plugin do model gián tiếp điều khiển thì cũng không.

---

## 5. Lỗi trả về gì

### 5.1 `Result` — một kiểu cho mọi kết cục

```python
@value
class Result(Generic[OutT]):
    output: OutT                       # kiểu do output_type quyết định
    text: str
    stop_reason: StopReason
    run_id: RunId
    steps: int
    cost: Money                        # Decimal-backed, không bao giờ float
    usage: Usage
    label: Label                       # nhãn hai chiều lúc kết thúc
    decisions: tuple[Decision, ...]    # dấu vết audit, append-only (D-2)
    pending: Scope | None              # có giá trị khi AWAITING_DECISION
    tools_run: tuple[CallId, ...]      # tool đã CHẠY, không phải tool model đã xin
    detail: str

    @property
    def ok(self) -> bool: ...
    def raise_for_status(self) -> None: ...
```

`decisions` nằm ngay trên `Result` là chỗ bất biến D-1/D-2 trở nên *dùng được*: người gọi
trả lời được "ai duyệt cái này, lúc nào, grant còn hiệu lực không" mà không phải đọc log
riêng. Không framework nào khảo sát trả lời được câu đó
([§09](../research/09-memory-context-multiagent-hitl.md) §14.1 kết luận).

`tools_run` ghi tool đã **thực thi**, không phải tool model đã **yêu cầu**. Trong một thư
viện mà câu chuyện an toàn là "tool nguy hiểm bị chặn", một helper không phân biệt được
tool bị chặn với tool đã chạy thì vô dụng cho chính việc kiểm tra đó.

### 5.2 Trạng thái kết thúc

```python
class StopReason(str, Enum):
    COMPLETED         = "completed"
    AWAITING_DECISION = "awaiting_decision"
    BUDGET_EXHAUSTED  = "budget_exhausted"
    STEP_LIMIT        = "step_limit"
    TIMEOUT           = "timeout"
    DENIED            = "denied"
    CANCELLED         = "cancelled"
    TRUNCATED         = "truncated"        # context không nén thêm được — 05 §B.3
    GRAPH_CHANGED     = "graph_changed"    # graph đổi giữa hai lần resume — 04 §4.5
    ERROR             = "error"
```

| stop reason | `ok` | resume được? | tiền đã tính | `output` |
|---|---|---|---|---|
| `COMPLETED` | ✅ | — | có | đầy đủ |
| `AWAITING_DECISION` | ❌ | ✅ `resume(answer=…)` | tới thời điểm dừng | một phần |
| `BUDGET_EXHAUSTED` | ❌ | ✅ sau khi nâng trần | có, tới trần | một phần |
| `STEP_LIMIT` | ❌ | ✅ | có | một phần |
| `TIMEOUT` | ❌ | ✅ | có | một phần |
| `DENIED` | ❌ | ❌ — cần `Decision` mới | có | một phần |
| `CANCELLED` | ❌ | ✅ | tới lúc nhận tín hiệu | một phần |
| `ERROR` | ❌ | ✅ nếu có checkpointer | có | một phần |

**`AWAITING_DECISION` không phải thất bại — nó là một điểm dừng.** Đây là approval
round-trip mà interface mẫu bỏ sót ([§05](../research/05-ideal-harness.md) §35–36).
Cột "resume được" chỉ có nghĩa khi có `checkpointer`: durability là kiến trúc, không phải
tuỳ chọn thêm sau ([§11](../research/11-workflow-and-dx.md) §12).

### 5.3 Ngoại lệ — ba nhóm, ba thời điểm

```python
class HarnessError(Exception): ...                 # gốc, yêu cầu what/got/fix/doc

class ConfigError(HarnessError): ...               # LUÔN ở import/construction time
class MissingEffectError(ConfigError): ...
class UnsafeToolSetError(ConfigError): ...
class InvalidBudgetError(ConfigError): ...
class DuplicateToolError(ConfigError): ...
class UnknownModelError(ConfigError): ...

class RunFailed(HarnessError):                     # từ run(), không từ try_run()
    result: Result[Any]
class RunPaused(RunFailed):                        # AWAITING_DECISION
    pending: Scope
```

Ba thời điểm, và ranh giới là bất biến kiểm được bằng test: **không có `ConfigError` nào
được raise từ bên trong một run.** Cái gì sai về cấu hình phải nổ trước khi tiêu tiền.

`ProviderError` và các con của nó (`ProviderRateLimited`, `ProviderUnavailable`,
`ProviderTimeout`, `ProviderAuthError`) **không rò ra bề mặt công khai**: chúng là đầu vào
của plugin `Retry` (`req` mang mã lỗi đã chuẩn hoá) và biến thành `StopReason.ERROR` ở
biên. Người dùng không phải học taxonomy lỗi của từng nhà cung cấp.

### 5.4 Lỗi tool — ba kết cục, suy ra từ `Effect`

pydantic-ai có **taxonomy ba nhánh duy nhất trong nghiên cứu**
([§08](../research/08-tool-mcp-plugin.md) §8.2):

| raise | model thấy | có prompt sửa lỗi | tiêu retry budget | run tiếp |
|---|---|---|---|---|
| `Retry` | ✅ | ✅ | ✅ | ✅ |
| `ToolFailed` | ✅ | ❌ | ❌ | ✅ |
| ngoại lệ khác | ❌ | — | — | ❌ |

Phân biệt "tiêu retry budget hay không" là phần tinh tế và nó đúng: một tham số sai định
dạng *phải* tiêu quota — model đang đoán và cần bị chặn lại; một 404 dứt khoát thì *không*
— retry không giúp được, model cần **thích nghi**.

Harness này giữ ba kết cục đó nhưng **không bắt người viết tool chọn**. LangChain để mặc
định `handle_tool_error=False`, nghĩa là một tool raise sẽ **kết thúc cả run** — nên một
lời gọi HTTP chập chờn giết một run 50 bước vốn đang chạy tốt, và muốn khác thì tác giả
từng tool phải opt-in ([§08](../research/08-tool-mcp-plugin.md) §8.2). Bài học nghiên cứu
rút ra rất rõ: **không mặc định nào đúng cho mọi tool, nên lựa chọn thuộc về *phân loại*
của tool, không thuộc về một cờ trên từng tool.**

Quy tắc mặc định, suy ra từ `Effect` ([`00`](00-foundation.md) §2):

| effect | ngoại lệ lạ được diễn giải thành | vì sao |
|---|---|---|
| `read` | `Retry` | retryable, và thất bại của nó là thông tin |
| `external` | `Retry` | retryable theo bảng effect |
| `write` | `ToolFailed` | không retry được nếu thiếu idempotency key |
| `danger` | `ToolFailed` | không retry được, và mức audit là `audit` |

Tác giả tool vẫn raise `ToolInputInvalid`/`ToolUnavailable` tường minh khi biết rõ hơn. Nhưng **không có
đường nào để một tool giết cả run**: mặc định của chúng ta ngược với LangChain. Đổi lại,
để tránh "degrade âm thầm" mà mặc định của LangChain phòng chống, mọi lỗi tool đều phát
một event ở mức `info` trở lên và ghi vào `Result.detail` — ồn ào mà không gây tử vong.

---

## 6. Học của ai, sửa cho ai

| lấy từ | cái gì | ở đây sửa gì |
|---|---|---|
| PydanticAI ([§02](../research/02-api-comparison.md) §6) | `output_type`, `end_strategy`, taxonomy lỗi 3 nhánh | ba nhánh lỗi **suy ra từ `Effect`** thay vì bắt mỗi tool tự chọn |
| MS Agent Framework ([§02](../research/02-api-comparison.md) §6) | `client` bắt buộc — DI cưỡng chế | không có model mặc định, cũng không có class var thiên hướng |
| smolagents ([§05](../research/05-ideal-harness.md) §31) | cách ly ở constructor | **bỏ mặc định `"local"`** — không chọn thì không chạy |
| Google ADK ([§02](../research/02-api-comparison.md) §6) | `extra='forbid'` | keyword-only + reject positional |
| LangChain 1.x ([§08](../research/08-tool-mcp-plugin.md) §24) | around-hook `wrap_model_call`/`wrap_tool_call` | 6 hook → 2; và **R-1**: invariant ra khỏi chuỗi plugin |
| LangGraph ([§11](../research/11-workflow-and-dx.md) §12) | checkpoint là kiến trúc | `resume(run_id, answer=…)` là approval round-trip, không chỉ resume kỹ thuật |
| Microsoft `ToolApprovalRule` ([§09](../research/09-memory-context-multiagent-hitl.md) §14) | `Scope` khoá theo giá trị tham số + `server_label` | `Answer` → `Decision` niêm phong bởi runtime, actor gắn ở `Approver` |
| — (không ai có) | trần chi tiêu thật ([§03](../research/03-safety-reliability.md) §20) | `budget` bắt buộc, phải có trục tiền |

---

## Chưa đủ evidence

1. **`external` retryable — có mâu thuẫn nội bộ chưa giải.** [`00`](00-foundation.md) §2
   xếp `external` là retryable ✅, còn [§08](../research/08-tool-mcp-plugin.md) §8.2 cảnh
   báo "một tool `external` thất bại có thể đã gây tác dụng rồi và không được retry mù".
   Tệp này theo foundation vì foundation là luật, nhưng mâu thuẫn là thật và có lẽ phải
   giải bằng idempotency key chứ không bằng cờ retry. Thuộc [`03`](03-tools-and-mcp.md).
2. **`run` async + `run_sync` là quy ước, không phải phát hiện.** Nghiên cứu không đo tác
   động của lựa chọn này lên DX; nó chỉ ghi rằng hai Agent API điểm cao nhất đều làm vậy.
3. **`deps_type` đã bị CẮT.** Bản nháp đầu có `Agent` generic trên `DepsT`. Reviewer chỉ ra
   hai lý do độc lập, mỗi lý do đủ: generic đó **không tới được người dùng nào** (`ToolCtx`
   của [03](03-tools-and-mcp.md) §6.2 không có trường `deps`), và trích dẫn duy nhất cho nó
   là "PydanticAI có nó" — theo [00 §8.4](00-foundation.md) đó là ý kiến, không phải phát
   hiện ([review-kiss.md](review-kiss.md) K-2). Ai cần DI thì đóng gói vào closure của tool;
   `functools.partial` đã có sẵn. Nếu sau này có nhu cầu **đo được**, thêm lại bằng một
   trường `deps: Any` trên `ToolCtx` — một dòng, không cần generic trên `Agent`.


4. **Thứ tự lồng plugin.** Nghiên cứu đọc `AgentMiddleware` ở mức signature; ngữ nghĩa
   thứ tự khi nhiều middleware cùng cài **không được đo**. "Khai trước thì ngoài hơn" là
   lựa chọn của tệp này, không phải điều học được.
5. **Vercel AI SDK middleware chưa đo** ([§08](../research/08-tool-mcp-plugin.md) §45) —
   có thể tồn tại một hình dạng hook tốt hơn cặp `wrap_*` mà nghiên cứu chưa chạm tới.
6. **`≤ 3 required dependency` là mục tiêu, chưa phải phép đo.** Nó chỉ kiểm được khi có
   implementation; cho tới lúc đó nó là ràng buộc tự đặt.
7. **`Answer` → `Decision` chưa có tiền lệ.** Không framework nào tách phần người duyệt
   điền khỏi phần runtime niêm phong ([§09](../research/09-memory-context-multiagent-hitl.md)
   §14.1), nên không có bằng chứng nào cho biết nó chịu được một UI phê duyệt thật (nhiều
   người duyệt, uỷ quyền, thu hồi).
8. **Chi phí của `output_type`.** Vòng lặp validate–retry khi model trả sai schema tốn bao
   nhiêu token không được đo ở bất kỳ dự án nào trong nghiên cứu — nên tương tác giữa
   `output_type` và trần chi tiêu là chưa biết.
