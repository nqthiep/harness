# 04 — Runtime & Durability

**Sở hữu:** graph, checkpoint, resume, `unguarded_paths()`, cancel, sub-agent boundary,
OTel span.
**Không sở hữu:** `Decision` lifecycle và audit sink (→ [`02-safety-engine.md`]), hình
dạng `Ledger` (→ [`05-cost-and-memory.md`]), `ToolSpec` (→ [`03-tools-and-mcp.md`]).
Từ vựng dùng đúng [`00-foundation.md`](00-foundation.md) §6.

---

## 1. Vì sao graph chứ không phải loop

Đây là lập luận kiến trúc mạnh nhất trong toàn nghiên cứu, và nó là lập luận **từ số
liệu**, không từ khẩu vị ([§11](../research/11-workflow-and-dx.md) §12).

| gói | kLOC | graph | declarative | visualize | **recover** |
|---|---:|---:|---:|---:|---:|
| langgraph 1.2.11 | 27 | **29,2** | 0,6 | 0,2 ᵃ | **51,6** |
| autogen-agentchat 0.7.5 | 11 | 19,0 | 6,5 | 0,0 | 2,3 |
| haystack-ai 3.1.0 | 54 | 2,9 | **22,7** | **1,3** | **0,3** |
| openai-agents 0.22.0 | 125 | **0,2** | 3,4 | 0,1 | 5,3 |
| agno 3.0.1 | 419 | 0,4 | 5,3 | 0,0 | 2,4 |

Mười gói xếp vào **ba** triết lý, không phải mười một
([§11](../research/11-workflow-and-dx.md) §12):

1. **Explicit graph** (langgraph, autogen) — topology là cấu trúc dữ liệu dựng trước khi
   chạy.
2. **Declarative pipeline** (haystack) — topology *serialise được*, round-trip ra YAML.
3. **Imperative loop** (openai-agents, agno, google-adk, pydantic-ai) — không có
   topology; chỉ có `while`, một model, một bảng tool và `max_turns`. openai-agents đạt
   0,2 ở cột `graph` vì nó **không có** graph, và đó là thiết kế, không phải khiếm khuyết.

### 1.1 Ba hệ quả của một quyết định

Trục thật không phải "power vs simplicity" mà là **biết được bao nhiêu trước khi model
chạy**. Một `while` loop không thể nói trước nó sẽ làm gì, nên nó **không thể**:

- checkpoint ở một biên có nghĩa,
- kiểm tra tĩnh,
- vẽ ra.

Một graph làm được cả ba. Đó là lý do mật độ checkpoint của langgraph cao hơn cả ngành
một bậc độ lớn: **không phải vì họ quan tâm durability hơn, mà vì có topology mới cài
được durability** ([§11](../research/11-workflow-and-dx.md) §12).

> Với một harness, **graph không phải tính năng — nó là tiền đề.** Durability, static
> guard check, và topology vẽ được là **ba hệ quả của MỘT quyết định**.

Đây chính là bất biến thứ năm của [`00-foundation.md`](00-foundation.md) §1: *"Có topology
mới cài được durability"* → runtime là graph, không phải `while` loop.

### 1.2 Phản chứng bắt buộc phải đọc: haystack

haystack là hàng khai báo nhất trong nghiên cứu (`declarative` 22,7 — `to_dict`/`from_dict`
trên mọi component, và là first-class visualisation duy nhất) và **gần như không khôi phục
được** (`recover` **0,3**) ([§11](../research/11-workflow-and-dx.md) §12).

> **Serialise ĐỊNH NGHĨA là một bài toán khác với checkpoint THỰC THI, và giải được cái
> thứ nhất không mua được gì cho cái thứ hai.**

Hệ quả trực tiếp cho tệp này: một harness "config-driven, YAML round-trip được" **không**
tự động resume được. Ta cần cả hai, và phải xây riêng. Đây là lý do §4 dưới đây tồn tại và
không được rút gọn thành "thì ta dump graph ra JSON".

### 1.3 Chọn LangGraph là nhận thêm hai món việc

LangGraph vô địch Recovery nhưng **budget 0,0** và **otel 0,1**
([§03](../research/03-safety-reliability.md) §17, §18). Đây không phải chê: nó là runtime,
cố ý để hai việc đó cho tầng trên. Nhưng nó xác định phạm vi của harness:

| tầng | ai làm | ở đâu |
|---|---|---|
| topology, checkpoint, resume, interrupt | LangGraph | tệp này dùng lại |
| budget ceiling trước mỗi model call | **harness** | §2 node `budget`, chi tiết ở `05` |
| observability | **harness** | §8 |
| chứng minh gate không bị vòng qua | **harness** | §3 |

Bằng chứng rằng checkpoint của LangGraph là **kiến trúc cố ý chứ không phải đặc tính của
một codebase**: `@langchain/langgraph` tái lập đúng mật độ đó ở TypeScript — **23,4 so với
21,1/kLOC** ([§10](../research/10-governance-health-languages.md) §28). Hai cài đặt độc
lập, cùng một con số ⇒ tái dùng được, không phải may mắn.

---

## 2. Cấu trúc graph

Sáu node. Không hơn. Mỗi node là một biên checkpoint.

```
        START
          │
          ▼
      ┌────────┐  stop_reason?  ┌────────┐
      │ budget │───────────────▶│ finish │──▶ END
      └────────┘                └────────┘
          │ ok                      ▲ ▲ ▲
          ▼                         │ │ │
      ┌────────┐  stop/không tool ──┘ │ │
      │ model  │                      │ │
      └────────┘                      │ │
          │ có tool_calls             │ │
          ▼                           │ │
      ┌────────┐   có ASK    ┌─────────┐│
      │ policy │────────────▶│ approve ││
      └────────┘             └─────────┘│
          │ toàn ALLOW            │      │
          ▼                       ▼      │
      ┌────────┐◀─────────────────┘      │
      │ tools  │                         │
      └────────┘─────────────────────────┘
          └──────────▶ budget  (vòng kế tiếp)
```

| node | trách nhiệm | vì sao là node riêng |
|---|---|---|
| `budget` | reserve tiền + đếm step + wall clock, suy ra `max_tokens` | invariant "không gì tới model mà không có reservation" phải là **cạnh**, không phải quy ước ([§03](../research/03-safety-reliability.md) §20: không ai reserve trước khi gọi model) |
| `model` | đúng một lời gọi model, settle reservation, phân loại stop reason | biên checkpoint tự nhiên: chỗ đắt tiền nhất |
| `policy` | mỗi `ToolCall` → một `Verdict` | invariant "không gì tới tool mà không có verdict" |
| `approve` | `ASK` → `ALLOW`/`DENY` qua `Decision` | tách khỏi `policy` vì **chỉ node này được phép dừng lâu** (§4) |
| `tools` | thực thi, redaction scope, nâng taint, barrier cho `write`/`danger` | nơi duy nhất tool output thành bytes ⇒ nơi duy nhất redaction phải giữ |
| `finish` | lối ra duy nhất | xem §2.2 |

### 2.1 Ba node bắt buộc phải đi qua

```python
GUARDED: Mapping[NodeName, NodeName] = {
    MODEL:  BUDGET,     # không đường nào tới model mà không qua budget
    TOOLS:  POLICY,     # không đường nào tới tools mà không qua policy
    END:    FINISH,     # không đường nào tới END mà không qua finish
}
```

Đây là dữ liệu, không phải văn xuôi — §3 đọc chính bảng này.

### 2.2 Vì sao `END` cũng nằm trong bảng

Round 35 của repo này tìm ra graph chỉ phát được **9 trong 15** event kind đã khai báo,
trong đó có `run.finished`, vì mỗi nhánh thoát đi thẳng ra `END`. Cùng lớp lỗi mà Round 27
bắt được trong loop viết tay: một event được khai báo mà không có chỗ phát.

Định tuyến mọi lối ra qua **một** node biến event đóng run thành **cấu trúc**, thay vì
thành thứ mỗi nhánh phải nhớ. Cùng một luật với hai gate kia — đó là lý do nó nằm chung
bảng chứ không phải một quy ước riêng.

### 2.3 Điều graph này cố ý KHÔNG làm

Nghiên cứu ghi: **state machine hạng nhất gần như bằng không ở mọi nơi (tối đa 0,3)** —
"agent không được refund trước khi verify" ở khắp ngành đều bị ép bằng prompt hoặc
conditional viết tay ([§11](../research/11-workflow-and-dx.md) §12 *What nobody has*).

Ta **không** xây state machine cho business logic. Sáu node trên là *enforcement*
topology, không phải *business* topology. Business state đi trong `state["workflow"]` và
được checkpoint cùng phần còn lại. Lý do: KISS — một cơ chế không sửa khuyết điểm đo được
thì cắt ([`00-foundation.md`](00-foundation.md) §8.4), và ở đây khuyết điểm đo được là
*enforcement bị vòng qua*, không phải *thiếu DSL trạng thái*.

---

## 3. `unguarded_paths()` — R-2, chứng minh được chứ không phải review được

### 3.1 Vì sao phải là chương trình, không phải người đọc

Số liệu quyết định, ghi trong [`00-foundation.md`](00-foundation.md) §5 R-2:

> **23 vòng review tìm ra 20 lỗi và 0 lỗi bảo mật; 16 vòng CHẠY tìm ra 38+ lỗi và 4 lỗi
> bảo mật.** Đọc không tìm ra cái mà chạy tìm ra.

Với một graph, "chạy" còn mạnh hơn test: reachability trên graph **đã compile** phủ cả
những đường mà không test nào bước qua. Đó chính là món mà loop viết tay không cho được
([§11](../research/11-workflow-and-dx.md) §12: *"An imperative loop … cannot be
checkpointed at a meaningful boundary, statically checked, or drawn"*).

### 3.2 Signature

```python
from collections.abc import Mapping, Sequence
from typing import NewType
from langgraph.graph.state import CompiledStateGraph

NodeName = NewType("NodeName", str)


@value
class UnguardedPath:
    """Một phản ví dụ cụ thể: đường đi từ START tới `node` không đụng `gate`."""
    node: NodeName                 # node được canh (model / tools / END)
    gate: NodeName                 # gate lẽ ra phải đi qua (budget / policy / finish)
    witness: tuple[NodeName, ...]  # đường đi thật, START → … → node


def unguarded_paths(
    compiled: CompiledStateGraph,
    guarded: Mapping[NodeName, NodeName] = GUARDED,
) -> Sequence[UnguardedPath]:
    """Mọi cách vòng qua một gate. Rỗng ⇔ enforcement giữ trên MỌI đường.

    Duyệt DFS từ START trên graph đã compile, **từ chối đi qua** `gate`. Chạm được
    `node` trong điều kiện đó là một phản ví dụ, và `witness` là đường đi để in ra.
    """
```

**Khác cài đặt hiện tại ở `src/harness/lg/graph.py` một điểm:** hàm hiện tại trả
`list[tuple[str, str]]` — biết *có* lỗi nhưng không biết *đường nào*. `witness` là chênh
lệch giữa "test đỏ" và "test đỏ sửa được trong ba mươi giây". Đây là DX đo được:
[§11](../research/11-workflow-and-dx.md) §22 đo *"errors with context"* — lỗi nội suy giá
trị vi phạm thay vì phát biểu điều kiện chung — và cả ngành chỉ đạt 25–61%. Không có lý do
để harness của mình nằm ở nửa dưới.

### 3.3 Chạy KHI NÀO — cả hai, và mỗi lần vì một lý do khác

**(a) Compile time — fail-closed, là đường duy nhất lấy được graph.**

```python
class UnguardedPathError(RuntimeError):
    def __init__(self, paths: Sequence[UnguardedPath]) -> None: ...


def compile_guarded(
    g: StateGraph,
    *,
    checkpointer: BaseCheckpointSaver,
) -> CompiledStateGraph:
    """API DUY NHẤT trả về một graph chạy được. `build()` là private."""
    compiled = g.compile(checkpointer=checkpointer)
    bad = unguarded_paths(compiled)
    if bad:
        raise UnguardedPathError(bad)
    return compiled
```

Poka-Yoke: người dùng **không có** đường nào lấy được compiled graph mà bỏ qua kiểm tra,
vì `build()` không public. Điều này sửa đúng lớp lỗi
[§09](../research/09-memory-context-multiagent-hitl.md) §16bis mô tả — Microsoft có
lattice tốt nhất nghiên cứu nhưng `_harness/` của chính họ **không import** nó: cơ chế
đúng mà không nằm trên đường đi bắt buộc thì không tồn tại. Ở đây gate check *là* đường đi.

**(b) Test — vì một checker luôn trả rỗng cũng "pass".**

Compile-time check chứng minh graph đúng; nó **không** chứng minh checker đúng. Nên bộ test
phải có **mutation test**: dựng graph rồi thêm cạnh `START → model`, và khẳng định
`unguarded_paths` trả về non-empty với `witness == (START, MODEL)`. Ba mutation tối thiểu,
một cho mỗi dòng của `GUARDED`.

Đây chính là R-2 áp lên chính nó: nếu chỉ *đọc* `unguarded_paths` để tin nó đúng, ta đang
làm lại đúng cái vòng review tìm ra 0 lỗi bảo mật.

**(c) CI — chạy trên graph thật của mỗi ví dụ trong `examples/`**, không chỉ graph mặc
định. Một plugin thêm node là chuyện được phép (R-1); thêm node mà mở đường vòng thì không.

### 3.4 Nó chứng minh gì, và KHÔNG chứng minh gì

| chứng minh được | KHÔNG chứng minh được |
|---|---|
| không đường nào tới `model` bỏ qua `budget` | `budget` có thật sự `reserve()` không |
| không đường nào tới `tools` bỏ qua `policy` | `policy` có ra `DENY` đúng chỗ không |
| không đường nào tới `END` bỏ qua `finish` | `finish` có phát `run.finished` không |

Cột phải là việc của property test ở `02`/`05`. Nói rõ ranh giới này để không ai đọc
"unguarded_paths xanh" thành "harness an toàn" — đúng cái ngộ nhận mà
[§03](../research/03-safety-reliability.md) §17 chỉ ra ở Goose: bốn permission mode mà
tool vẫn chạy với toàn quyền tài khoản người dùng.

---

## 4. Checkpoint và resume

### 4.1 Học của ai

LangGraph, và học có bằng chứng: `recover` 51,6/kLOC (502 `checkpoint` + 308
`checkpointer` + 88 `resume`, đã verify không phải nhiễu —
[§11](../research/11-workflow-and-dx.md) §12), cộng `durability:
Literal["sync","async","exit"]` và `interrupt()` được mô tả ngay trong source là *"Interrupt
the graph with a resumable exception from within a node"*
([§03](../research/03-safety-reliability.md) §15). Tái lập ở TypeScript 23,4 vs 21,1
([§10](../research/10-governance-health-languages.md) §28) ⇒ **kiến trúc cố ý, không phải
một codebase**.

Nhóm 3 — smolagents 0,4, letta-client 0,0 — không có gì để học ở đây
([§03](../research/03-safety-reliability.md) §15).

### 4.2 `durability` mặc định suy ra từ `Effect`, không phải từ cấu hình

| run chứa tool có effect | durability |
|---|---|
| chỉ `read` / `external` | `"async"` — mất một checkpoint chỉ tốn thời gian, tool retry được |
| có `write` hoặc `danger` | **`"sync"`** — mất một checkpoint có thể gây side effect lần hai |

Vì sao không cho người dùng chọn tự do: **không gói nào trong 16 gói cung cấp exactly-once
ở mức tool call** ([§03](../research/03-safety-reliability.md) §15 — pydantic-ai nói về
no-op nội bộ, letta là HTTP header, langgraph là `CachePolicy` băm bằng `pickle`). Khi
exactly-once không có, thứ duy nhất còn lại là **không mất dấu vết của lời gọi đã bay**, và
đó là checkpoint đồng bộ. Suy ra từ phân loại một lần, đúng mô hình
[`00-foundation.md`](00-foundation.md) §2 — người viết tool không phải nghĩ về durability.

### 4.3 `interrupt` / `Command(resume=...)`, và chỗ **không được** thiết kế trùng

`langgraph/types.py:851`: `interrupt(value: Any)`; `Interrupt` mang `value: Any` và
`id: str`, với id là `xxh3_128_hexdigest` của node namespace. Resume là `Command(resume=...)`,
lại `Any` ([§09](../research/09-memory-context-multiagent-hitl.md) §14.1).

**Điểm mạnh phải giữ:** id đó định vị *chỗ dừng*, nên một resume không thể áp nhầm sang một
pause khác. Đó là tính chất đúng đắn thật, và là lý do langgraph đứng đầu cột `resume` một
cách trung thực.

**Khuyết điểm phải sửa — và sửa ở đâu:** id định danh *chỗ dừng*, **không** định danh
*người trả lời*. *"Nothing in the type system distinguishes a resume value that came from a
human who clicked Approve from one produced by a script, another agent, or a replay"*
([§09](../research/09-memory-context-multiagent-hitl.md) §14.1).

> Chỗ đó do `Decision` trong [`02-safety-engine.md`](02-safety-engine.md) lo.
> **Tệp này không thiết kế lại actor/expiry/scope.** Việc của tệp này là làm cho kênh
> resume *chỉ chở được* một `DecisionId`, không chở được một `bool`.

```python
@value
class PauseRequest:
    """Payload của `interrupt()` — cái người duyệt nhìn thấy."""
    call_id: CallId
    tool: ToolName
    effect: Effect
    args: Mapping[str, Any]     # đã qua redact()
    reason: str

@value
class ResumeToken:
    """Payload DUY NHẤT `Command(resume=...)` chấp nhận."""
    decision_id: DecisionId
```

Node `approve` khi nhận resume:

```python
def approval_gate(self, state: AgentState) -> dict[str, Any]:
    """Đổi ResumeToken lấy Decision từ audit store, rồi mới định tuyến."""
```

Ba kiểm tra fail-closed, theo đúng thứ tự:

1. resume **không** phải `ResumeToken` (ví dụ `True`, `"yes"`, `1`) → `DENY`, không phải
   `ALLOW`. Đây là chỗ chữa `approve_tool(item, always_approve=True)` của openai-agents:
   một `bool` không mang được actor nên nó không được là input hợp lệ
   ([§09](../research/09-memory-context-multiagent-hitl.md) §14.1).
2. `Decision.scope` không khớp `ToolCall` đang chờ (tool, giá trị args, `server`) → `DENY`.
   Học `ToolApprovalRule` của Microsoft: duyệt `delete_file(path="/tmp/x")` không duyệt
   `delete_file(path="/etc/passwd")`, và grant cho một MCP server không chuyển sang server
   khác cùng tên tool ([§09](../research/09-memory-context-multiagent-hitl.md) §14.1).
3. `Decision.expires_at` đã qua → `DENY`. Sửa đúng chỗ *"that grant never lapses for the
   life of the context"*.

Cả ba đều là *tra cứu*, không phải *tin lời*. Kênh resume không bao giờ là nguồn của quyền.

### 4.4 Grant phải sống qua resume

Microsoft làm đúng một việc mà không ai khác làm: `ToolApprovalState` có
`to_dict`/`from_dict` và gắn với session, *"so a grant survives a resume rather than being
silently re-asked or silently re-granted"*
([§09](../research/09-memory-context-multiagent-hitl.md) §14.1).

Ta giữ đúng tính chất đó, nhưng cái được checkpoint là **danh sách `DecisionId` còn hiệu
lực trong run này**, không phải bản sao của grant:

```python
class AgentState(TypedDict, total=False):
    decisions: list[str]        # DecisionId — tra sổ, không phải bản sao quyền
```

Vì `Decision` là append-only ([`00-foundation.md`](00-foundation.md) §4, D-2), một bản sao
trong state sẽ không thấy được `Decision` thu hồi (`verdict=DENY`) ghi sau đó. Lưu id buộc
mọi lần dùng phải đọc lại sổ.

### 4.5 Resume vào một graph đã đổi

Checkpoint tham chiếu **tên node**. Nếu topology đổi giữa lúc pause và lúc resume,
checkpoint vẫn load nhưng ý nghĩa của nó đã khác. LangGraph không kiểm việc này; runtime
hiện tại của repo chỉ chịu được phần dễ (`tool no longer available`).

KISS, một trường:

```python
class AgentState(TypedDict, total=False):
    graph_version: str      # sha256 của (tên node đã sắp xếp, cạnh đã sắp xếp, GUARDED)
```

Resume vào graph khác `graph_version` → `stop_reason="graph_changed"`, đi qua `finish`,
không chạy tiếp. Fail-closed. Đây là chỗ bài học haystack quay lại: định nghĩa và thực thi
là hai thứ, nên checkpoint phải **nói được** nó thuộc định nghĩa nào
([§11](../research/11-workflow-and-dx.md) §12).

---

## 5. Trạng thái sống ở đâu (R-4)

> **State đã checkpoint của thread là bộ nhớ DUY NHẤT.**

### 5.1 Ba primitive, và tại sao cả ba đều không đủ

| primitive | vì sao hỏng | bằng chứng |
|---|---|---|
| attribute trên object runtime | `Runtime` dựng một lần cho mỗi compiled graph, phục vụ **mọi** cuộc hội thoại | Round 37: ledger chung tính tiền khách A cho khách B; taint tracker chung rò taint A sang B |
| `threading.local()` | per-OS-thread; dưới asyncio nhiều coroutine chung một thread ⇒ hai tool call đồng thời đọc nhầm slot của nhau, **im lặng và fail-open** | [§09](../research/09-memory-context-multiagent-hitl.md) §16bis, `security.py:685` |
| `ContextVar` | per-task, đúng hơn `threading.local()` — **nhưng vẫn không đi xuyên node LangGraph**: mỗi node chạy trong context được **copy** | lỗi tự mắc Round 34, sửa **sai** Round 37, sửa **đúng** Round 41 |

Ghi rõ để không ai "sửa" bằng nửa bước: `threading.local()` **yếu hơn** cái vốn đã không
đủ. Nghiên cứu nói đúng câu đó: *"`threading.local()` is strictly weaker than the primitive
that was already not enough"* ([§09](../research/09-memory-context-multiagent-hitl.md)
§16bis).

Và không có singleton mức module. Microsoft có `_global_variable_store` và
`_quarantine_chat_client` ở mức module — trong server đa tenant, một tenant đổi là mọi
tenant đổi theo ([§09](../research/09-memory-context-multiagent-hitl.md) §16bis).

### 5.2 `snapshot()` / `restore()`

Đây là ý tưởng tốt của runtime hiện tại (`Ledger.snapshot()/restore()`), nâng lên thành
protocol chung:

```python
from typing import Any, Protocol, Self
from collections.abc import Mapping


class Snapshottable(Protocol):
    """Mọi thứ mang trạng thái sống trong một run phải cài protocol này."""

    def snapshot(self) -> Mapping[str, Any]:
        """JSON-serialisable. Không float cho tiền, không callable, không object."""

    @classmethod
    def restore(cls, snap: Mapping[str, Any] | None, /) -> Self:
        """`None` ⇒ trạng thái khởi đầu. Phải toàn phần: không raise trên snapshot cũ."""
```

Ba thứ cài nó: `Ledger`, `Label` (taint hai chiều —
[`00-foundation.md`](00-foundation.md) §3.2), và tập `DecisionId` đang hiệu lực.

Mỗi node bắt đầu bằng `restore(state[...])` và kết thúc bằng `{...: x.snapshot()}`. Không
node nào đọc `self` cho thứ gì thuộc về một run.

### 5.3 Ba luật kiểm được bằng test

- **S-1. `restore(snapshot(x)) == x`** — property test, mọi kiểu Snapshottable.
- **S-2. `json.dumps(state)` không raise.** Đây là lý do `_pending` mang **`ToolName`**
  chứ không mang `ToolSpec`: `ToolSpec` giữ một callable mà không serializer nào ghi được.
  Spec là *cấu hình runtime*, tra khi dùng.
- **S-3. Tiền là `str` của `Decimal`, không bao giờ `float`.**

### 5.4 `Runtime` phải bất biến — và hiện tại nó chưa

`src/harness/lg/runtime.py` mở đầu bằng đúng luật này ("*the thread's state is the only
memory*") rồi vi phạm nó ba dòng sau, trong `budget_gate`:

```python
if state.get("step", 0) == 0 and not self._started:
    self._started = True
```

`self._started` là trạng thái **của một run** nằm trên object **dùng chung cho mọi
thread**. Cuộc hội thoại thứ hai trên cùng compiled graph không phát `run.started`. Cùng
lớp lỗi Round 37, sót lại một chỗ.

Thiết kế: `Runtime` đóng băng sau `__init__` (`@value`-style `__setattr__` raise), và cờ
này về state:

```python
class AgentState(TypedDict, total=False):
    started: bool
```

Một object không gán được thì không có lần sót thứ hai. Đây là R-4 ở dạng Poka-Yoke chứ
không phải ở dạng comment.

---

## 6. Cancel và cleanup

### 6.1 Học của ai

**autogen-agentchat, 26,9 `cancel`/kLOC — cao nhất toàn nghiên cứu, gấp ba á quân
(pydantic-ai 9,1)** ([§07](../research/07-remaining-python.md), tái xác nhận ở
[§00](../research/00-executive-summary.md)). Không phải nhiễu: 183 lần `cancellation_token`
là **tham số tường minh trên API**, cộng 84 lần `CancellationToken`.

> Huỷ được **truyền tay qua từng biên**, không dựa vào cơ chế ngầm của runtime. Đắt hơn về
> API surface, nhưng là cách duy nhất khiến việc huỷ **kiểm tra được**.

Ta lấy đúng mẫu đó: `CancelToken` là tham số tường minh xuống tới `ToolCtx`. Không biến
toàn cục, không `ContextVar` (§5.1 đã nói vì sao).

```python
@value
class CancelToken:
    """Định nghĩa chuẩn. Truyền TƯỜNG MINH qua mọi biên có thể block —
    không ContextVar, không biến toàn cục (00 §5 R-4). Học autogen, gói có kỷ luật
    cancellation cao nhất nghiên cứu (26,9/kLOC).
    """
    def cancel(self, reason: str) -> None: ...
    def cancelled(self) -> bool: ...
    def raise_if_cancelled(self) -> None: ...
    @property
    def reason(self) -> str | None: ...
```

### 6.2 Huỷ giữa chừng thì trạng thái ở đâu

Biên checkpoint là **biên node**. Nên câu trả lời chính xác:

| huỷ khi đang ở | state khôi phục về | lý do |
|---|---|---|
| `budget` | trước reservation | reservation chưa ghi vào state |
| `model` | trước lời gọi | phản hồi chưa về ⇒ chưa settle; xem 6.4 |
| `policy` / `approve` | trước khi có verdict | chưa tool nào chạy |
| `tools` | **xem 6.3** | đây là chỗ duy nhất có side effect |

Mọi trường hợp: `stop_reason="cancelled"`, và **vẫn đi qua `finish`** — luật §2.2 không có
ngoại lệ cho cancel. Đây đúng là chỗ mà "mỗi nhánh tự nhớ" sẽ hỏng.

### 6.3 Tool đang chạy thì sao — quy tắc suy ra từ `Effect`

Không gói nào có exactly-once ở mức tool call
([§03](../research/03-safety-reliability.md) §15). Nên **không được** giả vờ rằng huỷ một
`write` đang bay là an toàn.

| effect | khi cancel | vì sao |
|---|---|---|
| `read`, `external` | huỷ ngay | retryable, không side effect ([`00`](00-foundation.md) §2) |
| `write`, `danger` | **không abort.** Chờ tới `cancel_grace` (mặc định 30s), ghi kết quả vào state, rồi mới dừng | side effect có thể đã xảy ra; mất kết quả nguy hiểm hơn chờ |

Hết `cancel_grace` mà tool chưa xong:

```python
class AgentState(TypedDict, total=False):
    unresolved: list[dict[str, Any]]   # {call_id, tool, effect, started_at}
```

- `stop_reason="cancelled"`, `detail` nêu rõ có bao nhiêu lời gọi không xác định kết quả;
- `finish` phát `run.finished` với `unresolved` non-empty;
- Run được đánh dấu **cần đối soát**, không bao giờ báo `completed`.

Nguyên tắc: một `write` không rõ đã xảy ra hay chưa là **thông tin**, và thông tin đó phải
nằm trong state, không nằm trong đầu người vận hành. Đây là lớp lỗi mà
[§03](../research/03-safety-reliability.md) §15 gọi là "client timeout → gọi lại → side
effect lần hai" — ta không giải được exactly-once ở đây, nhưng ta không được **giấu** nó.

### 6.4 Cleanup: reservation phải đóng

`finish` là nơi duy nhất đóng sổ, và nó phải đóng **cả trên đường lỗi**:

- mọi reservation còn mở → `release()` về ledger (chi tiết ở [`05`](05-cost-and-memory.md));
- mọi `hold()` cấp cho sub-agent chưa `release()` → release với chi phí thực tế đã biết,
  hoặc với toàn bộ hold nếu không biết (fail-closed về phía **đã tiêu**, không về phía
  "hoàn lại");
- ledger snapshot cuối ghi vào state **trước** khi phát `run.finished`.

Fail-closed về phía "đã tiêu" là có chủ ý: đoán thấp chi phí là cách một budget ceiling trở
thành trang trí, và ceiling là một trong năm bất biến
([`00`](00-foundation.md) §1).

---

## 7. Sub-agent

### 7.1 Nghiên cứu nói gì (đọc trước khi thiết kế)

Một handoff **là một tool call**: `on_invoke_handoff` trả về một `Agent` khác, loop chạy
tiếp. Không process mới, không context mới, không client mới
([§09](../research/09-memory-context-multiagent-hitl.md) §13).

| | có không |
|---|---|
| giảm tool surface | ✅ |
| chuyên biệt hoá prompt | ✅ |
| **turn budget dùng chung, không reset được** | ✅ |
| context isolation | ❌ mặc định thấy toàn bộ lịch sử |
| fault isolation | ❌ exception của con là của cha |
| **privilege isolation** | ❌ `_resolve_approval_key` **không có agent identity** — tool duyệt cho A vẫn duyệt cho B |
| taint isolation | ❌ không có mô hình taint để mà isolate |

> **Multi-agent như đang được cài là một router, không phải một kiến trúc.** Nó là
> over-engineering khi được với tới như một cơ chế *isolation*, vì không cài đặt nào cung
> cấp cả ba isolation mà từ đó hàm ý.

### 7.2 Điểm mạnh phải giữ: turn budget KHÔNG reset qua handoff

Verify trong `agents/run.py`: `current_turn = 0` xuất hiện **đúng một lần**, ở dòng 762 lúc
bắt đầu run; tại chỗ handoff (dòng 2050) chỉ `current_agent` được gán lại
([§09](../research/09-memory-context-multiagent-hitl.md) §13).

> Cài đặt ngây thơ — mỗi agent có `max_turns` riêng — biến một chuỗi delegation thành vòng
> lặp không chặn: ba agent handoff vòng tròn sẽ chạy mãi. OpenAI làm đúng chỗ này.

**Luật của harness:** `steps` là của **run gốc**. Sub-agent trừ vào cùng sổ step, không
được cấp hạn mức step mới. Cộng thêm:

```python
class AgentState(TypedDict, total=False):
    depth: int          # 0 = agent gốc; cap cứng, mặc định 3
```

`depth` cap là để chặn đúng cái vòng tròn ở trên, ở chiều mà `steps` chung không chặn (một
cây delegation rộng vẫn có thể nổ trước khi chạm step limit).

### 7.3 Sub-agent phải có ledger riêng và policy scope riêng

Nghiên cứu kết luận thẳng: *"if you want a genuinely isolated sub-agent … the framework
will not give it to you. It has to be a first-class construct with its own ledger and its
own policy scope"* ([§09](../research/09-memory-context-multiagent-hitl.md) §13).

**Ledger riêng — qua `hold()`, không qua `remaining_usd()`.**

```python
def spawn_subagent(
    parent: Ledger,
    child: AgentSpec,
    *,
    task: str,
    cancel: CancelToken,
) -> SubagentResult:
    """Con chạy trên `thread_id` riêng, ngân sách là một lát ĐÃ GIỮ của cha."""
```

Lý do `hold()` chứ không `remaining_usd()`: nhiều con chạy song song, mỗi con đọc
`remaining_usd()` đều thấy toàn bộ số dư và đều tưởng mình được tiêu hết (Round 28). Đây là
ADR-030 của repo, và nó tồn tại đúng vì lý do §13 nêu.

**Policy scope riêng — dùng `Decision.run_id`, không thêm field mới.**

`Decision` ([`00`](00-foundation.md) §4) đã có `run_id`. Sub-agent có `run_id` riêng ⇒ một
`Decision` cấp trong run cha **không khớp** khi con tra sổ. Đó là fix chính xác cho
`_resolve_approval_key` không chứa agent identity, và nó **không cần** cơ chế mới — chỉ cần
luật tra sổ: *một `Decision` chỉ áp dụng khi `decision.run_id == state["run_id"]`.*

Ghi rõ để `02` không thiết kế trùng: tệp này chỉ khẳng định **con có `run_id` riêng**; luật
khớp scope thuộc [`02-safety-engine.md`](02-safety-engine.md).

**Fault isolation — cái §13 nói không ai có.**

Exception của con **không** thoát ra graph cha. Nó thành một `ToolMessage(status="error")`
trong graph cha, đúng như một tool lỗi bất kỳ. Con dừng, cha đọc lỗi và quyết định.

**Taint — join hai chiều, không reset.**

`Label` của con khởi tạo bằng `Label` của cha; khi con trả kết quả, `Label` của con join
ngược vào cha. Đơn điệu, không bao giờ giảm trong một run
([`00`](00-foundation.md) §3.2). Không có "sub-agent làm sạch context" — đó chính là cái
lỗ hổng `input_filter` để lại khi mặc định là `None`
([§09](../research/09-memory-context-multiagent-hitl.md) §13).

### 7.4 Khi nào KHÔNG dùng sub-agent

Khi lý do là "isolation". Nó chỉ chính đáng khi mục tiêu là **giảm tool surface** hoặc
**tránh một prompt 4000-token** — cả hai đều thật và đo được
([§09](../research/09-memory-context-multiagent-hitl.md) §13). KISS:
[`00`](00-foundation.md) §8.4.

---

## 8. Observability

### 8.1 Học của ai, và vì sao đây là việc bắt buộc phải làm

**google/adk-java: 11,4 OpenTelemetry hit/kLOC — mạnh nhất của bất kỳ gói nào trong bất kỳ
ngôn ngữ nào trong toàn nghiên cứu** (63 tham chiếu `opentelemetry` trực tiếp, phần còn lại
là span plumbing thật: `spanId`, `spanContext`, `spanRecord`)
([§10](../research/10-governance-health-languages.md) §28). *"Google instruments its agent
runtime the way it instruments its services."*

Ở phía đối diện: **langgraph 0,1 otel/kLOC** ([§03](../research/03-safety-reliability.md)
§18) — *"chọn LangGraph là nhận thêm việc dựng tầng quan sát"*. Tệp này là chỗ nhận việc đó.

Và [§11](../research/11-workflow-and-dx.md) §22 chốt: observability *"genuine only in
google-adk … and thin elsewhere"* là một trong năm thứ một deployment production cần mà
framework không cho.

### 8.2 Span: một node, một span

| span | attribute |
|---|---|
| `harness.run` (root) | `run_id`, `thread_id`, `graph_version`, `model`, `depth`, `stop_reason`, `steps`, `cost_usd`, `integrity`, `confidentiality`, `unresolved_count` |
| `harness.step` | `step` |
| `harness.budget` | `reserved_usd`, `remaining_usd`, `remaining_steps`, `remaining_wall_clock_s`, `max_tokens` |
| `harness.model` | `model`, `input_tokens`, `output_tokens`, `cache_read_tokens`, `provider_stop_reason`, `cost_usd` |
| `harness.policy` | `tool`, `call_id`, `effect`, `verdict`, `policy`, `decision_id` |
| `harness.approve` | `call_id`, `pause_id`, `waited_ms`, `decision_id`, `actor_kind`, `resumed_from_checkpoint` |
| `harness.tool` | `tool`, `call_id`, `effect`, `server`, `is_error`, `error_type`, `retryable`, `taints_output`, `args_sha256` |
| `harness.subagent` | `child_run_id`, `child_thread_id`, `held_usd`, `actual_usd`, `depth` |
| `harness.finish` | `stop_reason`, `unresolved_count` |

Bốn quy tắc, mỗi cái sửa một khuyết điểm đo được:

1. **`effect` có mặt trên mọi span liên quan tới tool.** Đó là trục phân loại duy nhất
   ([`00`](00-foundation.md) §2); nếu nó không lên trace thì không ai trả lời được "run
   nào chạm `danger`" mà không đọc log thô.
2. **`decision_id`, không phải `approved=true`.** Trace nói *có một quyết định*; nội dung
   quyết định nằm ở audit sink. Đây là chỗ tránh lặp lại agno: nhật ký audit không được là
   nơi duy nhất, và nhất là không được do bên bị audit viết
   ([§09](../research/09-memory-context-multiagent-hitl.md) §14.1).
3. **`actor_kind`, không phải `actor_id`.** Trace đi tới vendor thứ ba; danh tính người
   duyệt ở lại audit sink.
4. **Không giá trị tham số tool lên attribute** — chỉ `args_sha256`. Nghiên cứu ghi:
   *không gói Python nào trong nghiên cứu có kiểu `Secret` chuyên dụng chống rò rỉ qua
   log/prompt* ([§03](../research/03-safety-reliability.md) §16). Mọi attribute dạng chuỗi
   đi qua `redact()` — cùng hàm mà node `tools` dùng, không phải bản sao thứ hai.

### 8.3 Span KHÔNG phải audit

Ranh giới, viết để không ai xây trùng:

| | trace | audit (`02`) |
|---|---|---|
| mất được không | **có** (sampling, exporter chết) | **không** |
| ai đọc | người vận hành, dashboard | kiểm toán, điều tra sự cố |
| chứa danh tính | không | **có** (`Actor`) |
| ghi bởi | runtime | runtime, append-only (D-2) |

Nếu một tính chất phải chứng minh được sau sáu tháng, nó thuộc audit sink chứ không thuộc
trace. `unguarded_paths()` chứng minh **cấu trúc**; audit chứng minh **những gì đã xảy ra**;
trace chỉ giúp *thấy* trong lúc nó đang xảy ra.

---

## Chưa đủ evidence

- **Chi phí của `durability="sync"`.** §4.2 chọn checkpoint đồng bộ cho mọi run có tool
  `write`/`danger`, nhưng nghiên cứu chỉ ghi *sự tồn tại* của ba mode
  ([§03](../research/03-safety-reliability.md) §15) chứ không đo latency của từng mode.
  Nếu chi phí lớn, ngưỡng có thể phải hạ xuống mức per-tool. Cần đo, không đoán.
- **`cancel_grace = 30s`** là số đặt ra, không phải số đo được. Nghiên cứu đo *mật độ*
  cancellation (autogen 26,9) chứ không đo hành vi timeout của tool thật.
- **Tính đúng đắn của resume dưới cancel.** Không nghiên cứu nào đọc source của LangGraph
  ở đường "interrupt + cancel + resume cùng lúc". Đây là tổ hợp phải test trên hệ thật
  trước khi tin.
- **`graph_version` chống được đổi topology, không chống được đổi *hành vi node*.** Cùng
  tên node, cùng cạnh, logic bên trong khác ⇒ hash không đổi. Chưa có cơ chế nào trong
  nghiên cứu giải việc này; nêu ra như một lỗ đã biết.
- **Chi phí OTel.** §8 mô tả ~9 loại span cho mỗi step. google-adk đạt 11,4/kLOC nhưng
  nghiên cứu **không** đo overhead runtime của mật độ đó
  ([§10](../research/10-governance-health-languages.md) §28). Sampling rate mặc định chưa
  có cơ sở.
- **Song song trong node `tools` dưới checkpoint.** [`00`](00-foundation.md) §2 cho
  `read`/`external` chạy song song và bắt `write`/`danger` làm barrier — đúng theo
  [§08](../research/08-tool-mcp-plugin.md) §8.3. Nhưng tương tác giữa barrier đó và
  checkpoint giữa chừng của LangGraph chưa được đọc trong source. **Chưa đủ evidence.**
- **Multi-tenancy.** [§03](../research/03-safety-reliability.md) §16 ghi thẳng "Chưa đủ
  evidence cho phần lớn thư viện". Thiết kế ở đây (state là bộ nhớ duy nhất, không
  singleton) là *điều kiện cần* cho đa tenant, không phải bằng chứng đủ.
