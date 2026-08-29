# 4. Evaluation Methodology — và phản biện trọng số của đề bài

Đề bài đưa ra một bộ trọng số mẫu và **yêu cầu phản biện** (§907). Tôi điều chỉnh, và
nói rõ lý do.

| Chiều | Đề bài | Điều chỉnh | Lý do |
|---|---:|---:|---|
| Architecture | 10% | 8% | Kiến trúc chỉ có giá trị khi nó *tạo ra* các thuộc tính bên dưới. Chấm riêng dễ thành chấm thẩm mỹ |
| Agent API | 10% | 8% | |
| Tool API | 10% | **12%** | Tool là nơi agent chạm vào thế giới thật. Mọi side effect không hoàn tác được đều đi qua đây |
| Extensibility | 10% | 10% | Giữ nguyên |
| Runtime/Harness | 10% | 10% | Giữ nguyên |
| Reliability | 10% | **12%** | Bằng chứng: idempotency gần như vắng mặt ở cả ngành. Chiều nào cả ngành yếu thì chiều đó phân loại tốt |
| Security | 8% | **12%** | Đề bài để 8%, nhưng chính đề bài (§507, §542) dành hai mục riêng cho Poka-Yoke và Security. Blast radius của một tool sai lớn hơn của một API xấu |
| Context/Memory | 8% | 6% | Quan trọng, nhưng phần lớn đã tách được thành plugin — bằng chứng: Letta tồn tại như một dịch vụ riêng |
| Observability | 7% | 7% | Giữ nguyên |
| Evaluation | 7% | 6% | |
| Developer Experience | 5% | **6%** | DX quyết định adoption; adoption quyết định số người tìm ra lỗi |
| Performance/Cost | 5% | 3% + *(cost gộp vào Reliability)* | **Không tự tạo benchmark** (§650), nên chấm performance từ tài liệu là chấm lời hứa |

**Thay đổi lớn nhất: Security 8% → 12%.** Lý do có bằng chứng: sandbox của Goose đã bị
gỡ sau thử nghiệm, và CrewAI có approval 0.0/kLOC. Nếu chỉ chấm 8%, hai sự thật đó gần
như không ảnh hưởng thứ hạng — và đó là một hệ thống chấm điểm sai với thực tế production.

---

# 6. Agent API Comparison — API thật, đọc từ source

## PydanticAI 2.36.0 — `pydantic_ai/agent/__init__.py`

```python
def __init__(
    self,
    model: models.Model | models.KnownModelName | str | None = None,
    *,
    output_type: OutputSpec[OutputDataT] = str,
    instructions: AgentInstructions[AgentDepsT] = None,
    deps_type: type[AgentDepsT] = object,
    retries: int | AgentRetries | None = None,
    tools: Sequence[Tool[AgentDepsT] | ToolFuncEither[AgentDepsT, ...]] = (),
    toolsets: Sequence[AgentToolset[AgentDepsT]] | None = None,
    end_strategy: EndStrategy = 'graceful',
    tool_timeout: float | None = None,
    max_concurrency: _concurrency.AnyConcurrencyLimit = None,
    capabilities: Sequence[AgentCapability[AgentDepsT]] | None = None,
) -> None: ...
```

**Điểm đáng học nhất trong toàn bộ nghiên cứu nằm ở dòng `end_strategy`.** Đọc tiếp
`_agent_graph.py:101`:

```python
EndStrategy = Literal['early', 'graceful', 'exhaustive']
"""How to handle function tool calls a model requests alongside a result that ends the run.
- 'early': Output tools run in the order the model emitted them and the run ends at the first
  one that succeeds; function tools are not executed. ..."""
# và: "The default changed from 'early' to 'graceful' in v2."
```

Đây là tình huống model trả về **vừa kết quả cuối vừa tool call**. Hầu hết framework xử
lý ngầm; PydanticAI **đặt tên cho nó, cho ba lựa chọn, và đổi mặc định vì mặc định cũ
sai**. Ba việc đó là định nghĩa của một API trưởng thành.

- `deps_type` — dependency injection tách state khỏi agent: **một** agent phục vụ nhiều
  ngữ cảnh, không cần nhân bản
- `tool_timeout`, `max_concurrency`, `retries` — ba núm điều khiển reliability ngay trên
  constructor, không phải trong tài liệu
- `Complexity` trung bình-cao · `Type safety` **cao nhất bảng** · `Testability` cao ·
  `Production readiness` cao

## OpenAI Agents SDK 0.22.0 — `agents/agent.py`

```python
def __init__(
    self, name: str,
    handoff_description: str | None = None,
    tools: list[Tool] = ...,
    mcp_servers: list[MCPServer] = ...,
    mcp_config: MCPConfig = ...,
    instructions: str | Callable[[RunContextWrapper[TContext], Agent[TContext]],
                                 MaybeAwaitable[str]] | None = None,
    handoffs: list[Agent[Any] | Handoff[TContext, Any]] = ...,
    model: str | Model | None = None, ...
)
```

`mcp_servers` và `mcp_config` là **tham số hạng nhất của constructor** — MCP không phải
tiện ích bên lề. `instructions` nhận callable với `RunContextWrapper`, tức system prompt
động theo ngữ cảnh mà vẫn giữ kiểu.

- `Complexity` **thấp nhất trong nhóm Tier A** · `Flexibility` trung bình (đường sung
  sướng là OpenAI) · `Production readiness` cao — 42 issue mở là bằng chứng kỷ luật

## smolagents 1.26.0 — `smolagents/agents.py`

```python
def __init__(
    self, tools: list[Tool], model: Model,
    additional_authorized_imports: list[str] | None = None,
    executor_type: Literal["local","blaxel","e2b","modal","docker"] = "local",
    executor_kwargs: dict[str, Any] | None = None,
    max_print_outputs_length: int | None = None, ...
)
```

**Cách ly là tham số constructor.** Người viết agent buộc phải nhìn thấy nó khi gõ dòng
đầu tiên. So với một harness ghi sandbox trong trang tài liệu thứ ba, đây là khác biệt
Poka-Yoke ở mức thiết kế: `additional_authorized_imports` là **allowlist**, tức mặc
định là từ chối.

- `Complexity` thấp · `Type Safety` trung bình · **`Safety` cao nhất theo mật độ**

## Microsoft Agent Framework 1.16.0 — `agent_framework/_agents.py`

```python
def __init__(
    self, client: SupportsChatGetResponse[OptionsCoT],
    instructions: str | None = None, *,
    tools: ToolTypes | Callable[..., Any] | Sequence[...] | None = None,
    context_providers: Sequence[ContextProvider] | None = None,
    middleware: MiddlewareTypes | Sequence[MiddlewareTypes] | None = None,
    require_per_service_call_history_persistence: bool = False,
    compaction_strategy: CompactionStrategy ...
)
```

`middleware` + `context_providers` + `compaction_strategy` là mô hình enterprise cổ điển
áp đúng chỗ: cross-cutting concern không nhét vào Agent mà thành chuỗi. `client` là
tham số đầu tiên và **bắt buộc** — dependency inversion cưỡng chế, không có model mặc
định ẩn.

## Google ADK 2.8.0 — `google/adk/agents/base_agent.py`

```python
class BaseAgent(BaseNode, abc.ABC):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra='forbid')
class LlmAgent(BaseAgent, abc.ABC):
    DEFAULT_MODEL: ClassVar[str] = 'gemini-3.5-flash'
```

`extra='forbid'` là Poka-Yoke thật: gõ sai tên trường thì **lỗi lúc dựng**, không phải
im lặng bỏ qua. Nhưng `DEFAULT_MODEL = 'gemini-3.5-flash'` là **affinity nhà cung cấp
nằm trong class variable** — không phải lock-in, nhưng là một mặc định có thiên hướng.

## Bảng đối chiếu ngắn

| | Agent API | Type safety | Tool là hạng nhất | Cách ly | Ổn định |
|---|---|---|---|---|---|
| PydanticAI | **Tốt nhất Python** | **Cao nhất** | Có (`toolsets`) | Không | Có breaking v1→v2, **đã ghi rõ** |
| OpenAI Agents | Đơn giản nhất | Cao | Có (+MCP) | Sandbox session | Rất tốt (42 issue) |
| MS Agent Framework | Enterprise | Cao | Có | Không | Mới, cần pin |
| Google ADK | Pydantic, `forbid` | Cao | Có | Có (Agent Engine) | Mới |
| smolagents | Nhỏ nhất | Trung bình | Có | **Constructor** | Nhỏ nên ổn định |
| LangGraph | Không có "Agent" | Trung bình | Không — là node | Không | Có deprecation rõ |
| CrewAI | Role/Task | Trung bình | Có | Rất yếu | Nhiều knob |
