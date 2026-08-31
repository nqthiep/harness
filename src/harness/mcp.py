"""MCP client làm tool boundary — design/03-tools-and-mcp.md §5, T-9.1
(docs/17-research-alignment.md M9). Đóng đồng thời S-7, S-8, S-9, S-10, S-17
(design/07-risks-and-open-issues.md §1.1): năm phát hiện đó đều xoay quanh việc harness
tin annotation của một MCP server thay vì tự phân loại — mệnh lệnh của chính nghiên cứu:

    "A harness must classify third-party tools itself — from an operator-controlled
    policy keyed to the server's identity, with the MCP hints used at most as a default
    for servers already trusted, and never as the decision."

`classify_mcp_tool()` là nơi mệnh lệnh đó thành code: policy > hint > default, không bao
giờ ngược lại. Một khi `ToolSpec` đã sinh ra, nó đi qua ĐÚNG con đường mọi tool khác đi
qua (`dispatch.py`/`lg/runtime.py` gọi `spec.fn` như bất kỳ callable nào) — "tool boundary"
không phải một cơ chế mới, nó chính là cơ chế `effect`/`Policy`/taint đã có, áp cho một
nguồn tool không đáng tin thay vì tool tác giả tự viết.

v1 khoá theo `ServerLabel` (chuỗi), KHÔNG phải `ServerIdentity{label,fingerprint}` — K-12
đã bác bỏ `fingerprint` vì chưa có định dạng chốt được (không tương đương SPKI hiển nhiên
cho MCP stdio). Thêm lại khi quan sát được một lần re-pointing thật (07-risks §5.2).
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import field
from typing import Any, Awaitable, Callable, Final, Mapping, Sequence

from ._value import value
from .errors import ConfigError, HarnessError
from .tools import Effect, ToolSpec

ServerLabel = str
ToolName = str

#: S-17 — injection qua `description` một tool MCP đưa vào prompt TRƯỚC lời gọi đầu tiên.
#: Đây KHÔNG phải một detector (E7.1 đã bác bỏ pattern đó — một heuristic phân loại nội
#: dung cho ảo tưởng an toàn). Đây là cùng luật `max_result_tokens` đã áp cho KẾT QUẢ tool:
#: giới hạn kích thước input không đáng tin, không đọc hiểu nó. Với server KHÔNG `trusted`,
#: mọi payload injection nhét trong `description` bị cắt cùng cỡ mọi mô tả khác — không
#: xoá được nội dung độc, chỉ giới hạn máu chảy. Phòng thủ THẬT của harness với S-17 vẫn là
#: capability rule cũ: server không tin cậy mặc định `DANGER` (M-1 dưới đây), nên dù mô tả
#: dụ được model gọi tool đó, lời gọi vẫn phải qua ASK. Nói thẳng, như S-18: description từ
#: MCP không được lọc nội dung, không thể được — chỉ bị giới hạn kích thước.
MAX_MCP_DESCRIPTION_CHARS: Final[int] = 2_000


class McpError(HarnessError):
    """Gốc cho mọi lỗi runtime của module này (khác `ConfigError` — không phải lỗi setup)."""


class McpTransportError(McpError):
    """Tiến trình server chết, đóng stdout, hoặc trả JSON hỏng."""


class McpProtocolError(McpError):
    """Server trả một JSON-RPC error object cho `initialize`/`tools/list`/`tools/call`."""


class McpToolNotAllowed(ConfigError):
    """`policy.allow` không chứa tên tool này — lỗi cấu hình, phát hiện lúc bind."""


class McpRugPullError(McpError):
    """§5.4 — một tool đã bind đổi hình dạng (`input_schema`/`annotations`/`description`)
    giữa hai lần `tools/list`. Thiết kế gốc đề xuất "coi là tool mới, phân loại lại" — bản
    vá này chọn CHẶT HƠN: fail-closed thay vì âm thầm phân loại lại. Lý do: `Scope` (
    `policy/decision.py`) khoá theo TÊN tool, không theo fingerprint — âm thầm đổi effect
    của một tool đang có grant sống dưới CÙNG một tên, giữa một run, là chỗ một
    confused-deputy mới có thể nảy ra, không kém gì việc không phát hiện rug-pull. Raise
    và để operator quyết định, đúng luật fail-closed dùng khắp nơi khác trong package này.
    """


@value
class ToolAnnotations:
    """`mcp_types.ToolAnnotations` — CHỈ ba trường harness này đọc. Nguyên văn nghiên cứu
    (§08 §9): "all properties in ToolAnnotations are hints... Clients should never make
    tool use decisions based on ToolAnnotations received from untrusted servers." Đây là
    input chưa kiểm chứng — `classify_mcp_tool` không bao giờ đọc trực tiếp trường này trừ
    khi `policy.trusted` đã tự khai từ phía operator.
    """
    read_only_hint: bool | None = None
    destructive_hint: bool | None = None
    open_world_hint: bool | None = None


@value
class McpTool:
    """Tool THÔ, như `tools/list` trả về — chưa qua phân loại."""
    name: str
    description: str
    input_schema: Mapping[str, Any]
    annotations: ToolAnnotations | None = None
    __hash__ = None                     # holds a Mapping


@value
class McpServerPolicy:
    """design/03-tools-and-mcp.md §5.2 — operator giữ, khoá theo `ServerLabel`."""
    identity: ServerLabel
    trusted: bool = False                                   # hint có được dùng làm mặc định không
    default_effect: Effect = Effect.DANGER                  # dùng khi không tin, hoặc không có hint
    #: `field(default_factory=dict)` — cùng khuôn `observe/events.py`'s `Event.data`.
    #: `MappingProxyType({})` (design/03 §5.2's pseudocode gốc) fail ở dataclass's mutable-
    #: default detector: nó soi `__hash__` của giá trị default, và `mappingproxy` không
    #: hashable dù bất biến.
    effects: Mapping[ToolName, Effect] = field(default_factory=dict)   # operator ghi đè từng tool
    accepts_tainted: frozenset[ToolName] = frozenset()       # CHỈ operator — không bao giờ từ hint
    allow: frozenset[ToolName] | None = None                 # None = mọi tool server công bố
    __hash__ = None


def _effect_from_hints(ann: ToolAnnotations | None) -> Effect:
    """M-2, design/03 §5.3 — fail-closed như Microsoft `_map_mcp_annotations_to_labels`.

    `is True`, không phải "khác False": server thật (GitHub's MCP) khai `readOnlyHint=True`
    cho tool đọc nhưng BỎ TRỐNG trường này cho tool ghi — một so sánh `== False` chặt sẽ bỏ
    lọt chúng. Mọi giá trị khác `True` — false HOẶC thiếu — là write sink.
    """
    if ann is None:
        return Effect.DANGER
    if ann.read_only_hint is True:
        return Effect.READ if ann.open_world_hint is False else Effect.EXTERNAL
    if ann.destructive_hint is False:
        return Effect.WRITE
    return Effect.DANGER


def _bound_description(description: str, *, trusted: bool) -> str:
    if trusted or len(description) <= MAX_MCP_DESCRIPTION_CHARS:
        return description
    return (description[:MAX_MCP_DESCRIPTION_CHARS]
            + "\n[bị cắt — mô tả từ MCP server không được operator đánh dấu trusted]")


async def _unbound(**_kwargs: Any) -> Any:
    raise McpTransportError(
        "tool MCP này được phân loại (classify_mcp_tool) nhưng chưa gắn với client sống — "
        "dùng bind_mcp_server() thay vì gọi classify_mcp_tool() một mình nếu cần tool thực "
        "thi được, không chỉ để kiểm phân loại."
    )


def classify_mcp_tool(
    tool: McpTool,
    policy: McpServerPolicy,
    *,
    caller: Callable[..., Awaitable[Any]] | None = None,
) -> ToolSpec:
    """§5.2-§5.3 — sinh `ToolSpec` cho một tool bên thứ ba. Thứ tự ưu tiên: policy > hint >
    default. `caller=None` cho kiểm phân loại thuần (test, dry-run); `bind_mcp_server()`
    truyền một closure gọi lại `tools/call` thật.
    """
    if policy.allow is not None and tool.name not in policy.allow:
        raise McpToolNotAllowed(
            f"server {policy.identity!r} công bố tool {tool.name!r}, nhưng "
            f"policy.allow không chứa tên đó.\n\n"
            f"  Sửa: thêm {tool.name!r} vào McpServerPolicy(allow={{...}}), "
            f"hoặc để allow=None nếu muốn nhận mọi tool server công bố."
        )

    # M-1: server không trusted -> hint không tham gia, trừ khi operator ghi đè per-tool.
    # M-2: server trusted -> hint làm MẶC ĐỊNH, operator vẫn thắng.
    if tool.name in policy.effects:
        effect = policy.effects[tool.name]
    elif policy.trusted:
        effect = _effect_from_hints(tool.annotations)
    else:
        effect = policy.default_effect

    description = _bound_description(tool.description, trusted=policy.trusted)
    fn = caller if caller is not None else _unbound

    async def _fn(**kwargs: Any) -> Any:
        return await fn(**kwargs)

    return ToolSpec(
        name=tool.name,
        description=description,
        input_schema=dict(tool.input_schema),
        effect=effect,
        fn=_fn,
        source=f"mcp:{policy.identity}",
        server=policy.identity,
    )


def _fingerprint(tool: McpTool) -> str:
    """§5.4 — chốt hình dạng một tool lúc bind, để phát hiện `tools/list` đổi giữa hai lần
    gọi (rug-pull). Không dùng schema/annotation gốc trực tiếp — canonical JSON để hai lần
    gọi cùng nội dung luôn cho cùng fingerprint bất kể thứ tự key (IDL-04).
    """
    ann = tool.annotations
    payload = {
        "name": tool.name,
        "description": tool.description,
        "input_schema": tool.input_schema,
        "annotations": (
            None if ann is None
            else [ann.read_only_hint, ann.destructive_hint, ann.open_world_hint]
        ),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class StdioMcpClient:
    """MCP transport tối thiểu, thật — JSON-RPC 2.0 qua stdio của một tiến trình con.

    Chỉ ba method của giao thức: `initialize`, `tools/list`, `tools/call` — đủ cho
    `classify_mcp_tool`/`bind_mcp_server`. Không phụ thuộc SDK `mcp` chính thức: giao thức
    trên stdio là JSON-RPC newline-delimited, tự viết bằng `asyncio.subprocess` giữ đúng
    kỷ luật "core 3 dependency" (`docs/17 §5`) — MCP không thêm gói nào, giống cách
    `viking`/`graph` chỉ vendor phần CLIENT (ADR-035), không phải cả hệ sinh thái.
    """

    def __init__(
        self,
        command: str,
        args: Sequence[str] = (),
        *,
        env: Mapping[str, str] | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self._command = command
        self._args = tuple(args)
        self._env = dict(env) if env is not None else None
        self._timeout_s = timeout_s
        self._proc: asyncio.subprocess.Process | None = None
        self._next_id = 1

    async def start(self) -> None:
        full_env = {**os.environ, **self._env} if self._env is not None else None
        self._proc = await asyncio.create_subprocess_exec(
            self._command, *self._args,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=full_env,
        )
        await self._request("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "harness", "version": "0.1.0.dev0"},
        })
        await self._notify("notifications/initialized", {})

    async def _send(self, obj: Mapping[str, Any]) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
        await self._proc.stdin.drain()

    async def _notify(self, method: str, params: Mapping[str, Any]) -> None:
        await self._send({"jsonrpc": "2.0", "method": method, "params": dict(params)})

    async def _request(self, method: str, params: Mapping[str, Any]) -> Any:
        assert self._proc is not None and self._proc.stdout is not None
        req_id = self._next_id
        self._next_id += 1
        await self._send({"jsonrpc": "2.0", "id": req_id, "method": method,
                          "params": dict(params)})
        while True:
            try:
                line = await asyncio.wait_for(self._proc.stdout.readline(),
                                              timeout=self._timeout_s)
            except asyncio.TimeoutError:
                raise McpTransportError(
                    f"server không trả lời '{method}' trong {self._timeout_s}s") from None
            if not line:
                stderr = b""
                if self._proc.stderr is not None:
                    stderr = await self._proc.stderr.read(4_096)
                raise McpTransportError(
                    f"tiến trình MCP đóng stdout khi chờ '{method}'. "
                    f"stderr: {stderr.decode('utf-8', 'replace')[:500]}")
            try:
                msg = json.loads(line)
            except json.JSONDecodeError as exc:
                raise McpTransportError(f"server trả JSON hỏng cho '{method}': {exc}") from exc
            if msg.get("id") != req_id:
                continue                    # notification hoặc trả lời cho id khác — bỏ qua
            if "error" in msg:
                err = msg["error"]
                raise McpProtocolError(
                    f"'{method}' -> {err.get('code')}: {err.get('message')}")
            return msg.get("result")

    async def list_tools(self) -> tuple[McpTool, ...]:
        result = await self._request("tools/list", {})
        out = []
        for t in result.get("tools", []):
            ann_raw = t.get("annotations")
            ann = None
            if ann_raw is not None:
                ann = ToolAnnotations(
                    read_only_hint=ann_raw.get("readOnlyHint"),
                    destructive_hint=ann_raw.get("destructiveHint"),
                    open_world_hint=ann_raw.get("openWorldHint"),
                )
            out.append(McpTool(name=t["name"], description=t.get("description", ""),
                               input_schema=t.get("inputSchema", {"type": "object"}),
                               annotations=ann))
        return tuple(out)

    async def call_tool(self, name: str, arguments: Mapping[str, Any]) -> str:
        result = await self._request("tools/call", {"name": name, "arguments": dict(arguments)})
        content = (result or {}).get("content", [])
        text = "\n".join(b.get("text", "") for b in content if b.get("type") == "text")
        if text:
            return text
        return json.dumps(result, ensure_ascii=False)

    async def close(self) -> None:
        if self._proc is None:
            return
        if self._proc.stdin is not None:
            self._proc.stdin.close()
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            self._proc.kill()
            await self._proc.wait()


class McpBinding:
    """Kết quả của `bind_mcp_server()` — client sống + tool đã phân loại + fingerprint
    lúc bind, để `check_for_rug_pull()` so sánh sau này (§5.4)."""

    def __init__(self, client: StdioMcpClient, tools: tuple[ToolSpec, ...],
                fingerprints: Mapping[str, str], policy: McpServerPolicy,
                raw: Mapping[str, McpTool]) -> None:
        self.client = client
        self.tools = tools
        self.policy = policy
        self._fingerprints = dict(fingerprints)
        self._raw = dict(raw)

    async def check_for_rug_pull(self) -> None:
        """Gọi lại `tools/list`. Bất kỳ tool đã bind nào đổi hình dạng -> `McpRugPullError`.
        Một tool MỚI xuất hiện (không có lúc bind) không phải rug-pull — không tự động bind
        nó (§5.4 chỉ nói về tool ĐANG DÙNG đổi hình dạng); operator gọi `bind_mcp_server`
        lại nếu muốn nhận tool mới.
        """
        relisted = {t.name: t for t in await self.client.list_tools()}
        for name, old_fp in self._fingerprints.items():
            new = relisted.get(name)
            if new is None:
                raise McpRugPullError(
                    f"server {self.policy.identity!r} không còn công bố tool {name!r} "
                    f"nữa (từng bind lúc trước) — rug-pull hoặc server đổi phiên bản.")
            if _fingerprint(new) != old_fp:
                raise McpRugPullError(
                    f"tool {name!r} trên server {self.policy.identity!r} đã đổi "
                    f"description/input_schema/annotations kể từ lúc bind. Phân loại "
                    f"effect cũ không còn đáng tin — bind lại (bind_mcp_server) để phân "
                    f"loại lại từ đầu, đừng tiếp tục dùng ToolSpec cũ.")

    async def close(self) -> None:
        await self.client.close()


async def bind_mcp_server(
    command: str,
    args: Sequence[str] = (),
    *,
    policy: McpServerPolicy,
    env: Mapping[str, str] | None = None,
    timeout_s: float = 30.0,
) -> McpBinding:
    """Khởi động một MCP server qua stdio, liệt kê tool, phân loại từng cái theo
    `policy` (M-1…M-3), chốt fingerprint (§5.4).  `McpBinding.tools` là `tuple[ToolSpec,
    ...]` — dùng thẳng trong `Agent(tools=[...])`/`build_agent(tools=[...])`, cùng con
    đường mọi tool khác đi qua.  Caller chịu trách nhiệm `await binding.close()`.
    """
    client = StdioMcpClient(command, args, env=env, timeout_s=timeout_s)
    await client.start()
    raw_tools = await client.list_tools()

    specs = []
    fingerprints = {}
    raw_by_name = {}
    for t in raw_tools:

        async def _call(_arguments: Mapping[str, Any], _name: str = t.name) -> Any:
            return await client.call_tool(_name, _arguments)

        async def _caller(_call=_call, **kwargs: Any) -> Any:
            return await _call(kwargs)

        specs.append(classify_mcp_tool(t, policy, caller=_caller))
        fingerprints[t.name] = _fingerprint(t)
        raw_by_name[t.name] = t

    return McpBinding(client, tuple(specs), fingerprints, policy, raw_by_name)
