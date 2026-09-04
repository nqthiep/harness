"""M9/T-9.1 (docs/17-research-alignment.md): MCP client là biên giới tool — không phải
bốn bản vá rời, mà một cơ chế landing S-7, S-8, S-9, S-10, S-17 CÙNG LÚC (07-risks §1.1,
"hoãn có chủ ý" tới khi MCP thật được xây).

`design/03-tools-and-mcp.md §5` là bản đặc tả — bốn luật M-1..M-4, cộng §5.4 (rug-pull
chốt tại thời điểm bind). Test dưới đây khoá từng luật bằng một kịch bản cụ thể, và mỗi
cơ chế load-bearing có một mutation đi kèm (khôi phục hành vi TRƯỚC bản vá, xác nhận test
đỏ ngay — cùng kỷ luật `test_attack_*.py`).
"""
import unittest
from datetime import datetime, timezone

import mcp.types as mt

from harness import tool
from harness.lg import build_agent
from harness.mcp import McpServerPolicy, McpToolError, ServerLabel, classify_mcp_tool, connect
from harness.policy.base import Verdict
from harness.policy.decision import Actor, Decision, DecisionLog, Scope
from harness.tools import Effect


def _now():
    return datetime.now(timezone.utc)


def _tool(name="search", description="tìm", annotations=None):
    return mt.Tool(name=name, description=description,
                   inputSchema={"type": "object", "properties": {}}, annotations=annotations)


def _ann(**kw):
    return mt.ToolAnnotations(**kw)


class HintMapping(unittest.TestCase):
    """M-2: `_effect_from_hints` — fail-closed y hệt Microsoft, kể cả chỗ dễ sai."""

    def test_khong_co_annotation_la_danger(self):
        from harness.mcp import _effect_from_hints
        self.assertEqual(_effect_from_hints(None), Effect.DANGER)

    def test_readonly_true_openworld_false_la_read(self):
        from harness.mcp import _effect_from_hints
        ann = _ann(readOnlyHint=True, openWorldHint=False)
        self.assertEqual(_effect_from_hints(ann), Effect.READ)

    def test_readonly_true_openworld_true_la_external(self):
        from harness.mcp import _effect_from_hints
        ann = _ann(readOnlyHint=True, openWorldHint=True)
        self.assertEqual(_effect_from_hints(ann), Effect.EXTERNAL)

    def test_readonly_true_openworld_khong_dat_la_external(self):
        """`openWorldHint` thiếu (None) không phải `False` — polarity "khác True/False cụ
        thể thì đi về nhánh kém tin cậy hơn" áp cho CẢ hai trục, không chỉat readOnlyHint."""
        from harness.mcp import _effect_from_hints
        ann = _ann(readOnlyHint=True)
        self.assertEqual(_effect_from_hints(ann), Effect.EXTERNAL)

    def test_readonly_false_destructive_false_la_write(self):
        from harness.mcp import _effect_from_hints
        ann = _ann(readOnlyHint=False, destructiveHint=False)
        self.assertEqual(_effect_from_hints(ann), Effect.WRITE)

    def test_readonly_thieu_destructive_false_la_write(self):
        """Bài học đắt của Microsoft: GitHub's MCP set `readOnlyHint=True` cho tool đọc
        nhưng ĐỂ TRỐNG (không phải `False`) trên tool ghi — `readOnlyHint` thiếu vẫn phải
        rơi xuống nhánh `destructiveHint` chứ không bị coi là readonly."""
        from harness.mcp import _effect_from_hints
        ann = _ann(destructiveHint=False)
        self.assertEqual(_effect_from_hints(ann), Effect.WRITE)

    def test_destructive_true_la_danger(self):
        from harness.mcp import _effect_from_hints
        ann = _ann(readOnlyHint=False, destructiveHint=True)
        self.assertEqual(_effect_from_hints(ann), Effect.DANGER)

    def test_destructive_thieu_la_danger(self):
        """`destructiveHint` thiếu (None), không phải `False` — mặc định về DANGER."""
        from harness.mcp import _effect_from_hints
        ann = _ann(readOnlyHint=False)
        self.assertEqual(_effect_from_hints(ann), Effect.DANGER)

    def test_mutation_readonly_khac_false_thay_vi_la_true_bo_sot_tool_ghi(self):
        """Mutation: thay `is True` bằng `!= False` (lỗi mà chính Microsoft từng mắc,
        design/03 §5.3 M-2) — một tool GHI không khai `readOnlyHint` (None) sẽ bị đọc
        nhầm thành `read`. Xác nhận version ĐÚNG không mắc lỗi đó."""
        def buggy(ann):
            if ann is None:
                return Effect.DANGER
            if getattr(ann, "readOnlyHint", None) != False:      # noqa: E712 — mô phỏng lỗi
                return Effect.READ if getattr(ann, "openWorldHint", None) is False else Effect.EXTERNAL
            if getattr(ann, "destructiveHint", None) is False:
                return Effect.WRITE
            return Effect.DANGER

        write_tool_ann = _ann(destructiveHint=False)     # readOnlyHint KHÔNG khai
        from harness.mcp import _effect_from_hints
        self.assertEqual(_effect_from_hints(write_tool_ann), Effect.WRITE)
        self.assertNotEqual(buggy(write_tool_ann), Effect.WRITE,
                            "mutation phải cho kết quả SAI (read) — nếu nó cũng ra WRITE, "
                            "test này không còn phân biệt được bản đúng và bản có lỗi")


class PhanLoaiTheoPolicy(unittest.TestCase):
    """M-1: server không trusted -> hint không tham gia. M-2: server trusted -> hint làm
    mặc định. Ghi đè `policy.effects` luôn thắng cả hai."""

    def test_khong_trusted_bo_qua_hint_dung_default(self):
        t = _tool(annotations=_ann(readOnlyHint=True, openWorldHint=False))   # trông như READ
        policy = McpServerPolicy(identity=ServerLabel("evil"), trusted=False,
                                 default_effect=Effect.DANGER)
        spec = classify_mcp_tool(t, policy, call=lambda n, a: None)
        self.assertEqual(spec.effect, Effect.DANGER,
                         "server không trusted: hint KHÔNG được quyết định phân loại")

    def test_trusted_dung_hint_lam_mac_dinh(self):
        t = _tool(annotations=_ann(readOnlyHint=True, openWorldHint=False))
        policy = McpServerPolicy(identity=ServerLabel("github"), trusted=True)
        spec = classify_mcp_tool(t, policy, call=lambda n, a: None)
        self.assertEqual(spec.effect, Effect.READ)

    def test_override_thang_ca_khi_trusted(self):
        t = _tool(name="wipe", annotations=_ann(readOnlyHint=True, openWorldHint=False))
        policy = McpServerPolicy(identity=ServerLabel("github"), trusted=True,
                                 effects={"wipe": Effect.DANGER})
        spec = classify_mcp_tool(t, policy, call=lambda n, a: None)
        self.assertEqual(spec.effect, Effect.DANGER)

    def test_override_thang_ca_khi_khong_trusted(self):
        """M-1: "lối thoát là policy.effects — operator hạ từng tool xuống bằng tay"."""
        t = _tool(name="read_file")
        policy = McpServerPolicy(identity=ServerLabel("evil"), trusted=False,
                                 default_effect=Effect.DANGER,
                                 effects={"read_file": Effect.READ})
        spec = classify_mcp_tool(t, policy, call=lambda n, a: None)
        self.assertEqual(spec.effect, Effect.READ)


class DungToolSpec(unittest.TestCase):
    """`classify_mcp_tool` phải dựng đúng `ToolSpec`: tên có tiền tố server (không va
    chạm giữa hai server), `server=` đúng nhãn, `fn` gọi ĐÚNG tên giao thức (không phải
    tên đã gắn tiền tố cho model)."""

    def test_ten_co_tien_to_server(self):
        t = _tool(name="search")
        policy = McpServerPolicy(identity=ServerLabel("github"))
        spec = classify_mcp_tool(t, policy, call=lambda n, a: None)
        self.assertEqual(spec.name, "github_search")
        self.assertEqual(spec.server, "github")

    def test_hai_server_cung_ten_tool_ra_hai_ten_khac_nhau(self):
        t = _tool(name="search")
        s1 = classify_mcp_tool(t, McpServerPolicy(identity=ServerLabel("github")),
                               call=lambda n, a: None)
        s2 = classify_mcp_tool(t, McpServerPolicy(identity=ServerLabel("gitlab")),
                               call=lambda n, a: None)
        self.assertNotEqual(s1.name, s2.name)
        self.assertNotEqual(s1.server, s2.server)

    def test_khong_co_description_dung_ten_lam_fallback(self):
        t = _tool(name="ping", description=None)
        spec = classify_mcp_tool(t, McpServerPolicy(identity=ServerLabel("x")),
                                 call=lambda n, a: None)
        self.assertEqual(spec.description, "ping")

    def test_fn_goi_dung_ten_giao_thuc_khong_phai_ten_gan_tien_to(self):
        seen = {}

        async def call(name, args):
            seen["name"] = name
            seen["args"] = dict(args)
            return "ok"

        t = _tool(name="search")
        spec = classify_mcp_tool(t, McpServerPolicy(identity=ServerLabel("github")), call=call)
        import asyncio
        result = asyncio.run(spec.fn(query="x"))
        self.assertEqual(seen["name"], "search")          # KHÔNG phải "github_search"
        self.assertEqual(seen["args"], {"query": "x"})
        self.assertEqual(result, "ok")

    def test_mutation_fn_goi_nham_ten_da_gan_tien_to(self):
        """Mutation: `_fn` gọi `call(name, ...)` (tên harness-facing) thay vì
        `call(tool.name, ...)` (tên giao thức) — server MCP thật sẽ không nhận ra tool
        này, vì nó chưa từng công bố cái tên có tiền tố `github_`."""
        seen = {}

        async def call(name, args):
            seen["name"] = name
            return "ok"

        async def buggy_fn(**kwargs):
            return await call("github_search", kwargs)     # SAI: tên đã gắn tiền tố

        import asyncio
        asyncio.run(buggy_fn(query="x"))
        self.assertNotEqual(seen["name"], "search",
                            "mutation phải gọi sai tên giao thức — nếu nó cũng đúng, test "
                            "này không còn phân biệt được bản đúng và bản có lỗi")


class KetQuaGoiTool(unittest.TestCase):
    """`_unwrap`: `isError` phải raise, không phải trả về như một kết quả bình thường."""

    def test_isError_raise_mctooltoolerror(self):
        from harness.mcp import _unwrap
        result = mt.CallToolResult(content=[mt.TextContent(type="text", text="quota exceeded")],
                                   isError=True)
        with self.assertRaises(McpToolError):
            _unwrap(result)

    def test_khong_loi_tra_ve_text_noi_lai(self):
        from harness.mcp import _unwrap
        result = mt.CallToolResult(
            content=[mt.TextContent(type="text", text="a"), mt.TextContent(type="text", text="b")],
            isError=False)
        self.assertEqual(_unwrap(result), "a\nb")

    def test_mutation_bo_qua_isError_tra_ve_nhu_thanh_cong(self):
        """Mutation: đọc `.content` mà không kiểm `isError` — một lỗi thật (quota, auth)
        sẽ chảy vào model như một kết quả THÀNH CÔNG."""
        from harness.mcp import _text
        result = mt.CallToolResult(content=[mt.TextContent(type="text", text="quota exceeded")],
                                   isError=True)
        buggy = _text(result)          # bỏ qua isError hoàn toàn
        self.assertEqual(buggy, "quota exceeded",
                         "mutation (bỏ kiểm isError) phải trả về y hệt văn bản lỗi như một "
                         "kết quả bình thường — đúng cái _unwrap() phải NGĂN")


class FakeSession:
    """Đủ để `connect()` chạy — không phải mock của `mcp.ClientSession` đầy đủ."""

    def __init__(self, tools, results=None):
        self._tools = tools
        self._results = results or {}

    async def list_tools(self):
        return mt.ListToolsResult(tools=self._tools)

    async def call_tool(self, name, arguments=None):
        text = self._results.get(name, f"called {name}")
        return mt.CallToolResult(content=[mt.TextContent(type="text", text=text)], isError=False)


class ConnectClientThat(unittest.IsolatedAsyncioTestCase):
    async def test_connect_tra_ve_toolspec_da_phan_loai(self):
        session = FakeSession([_tool(name="search"), _tool(name="wipe")])
        policy = McpServerPolicy(identity=ServerLabel("s1"))
        specs = await connect(session, policy)
        self.assertEqual({s.name for s in specs}, {"s1_search", "s1_wipe"})
        self.assertTrue(all(s.server == "s1" for s in specs))

    async def test_allowlist_loc_bot_tool(self):
        session = FakeSession([_tool(name="search"), _tool(name="wipe")])
        policy = McpServerPolicy(identity=ServerLabel("s1"), allow=frozenset({"search"}))
        specs = await connect(session, policy)
        self.assertEqual({s.name for s in specs}, {"s1_search"})

    async def test_fn_round_trip_qua_session_that(self):
        session = FakeSession([_tool(name="search")], results={"search": "3 kết quả"})
        specs = await connect(session, McpServerPolicy(identity=ServerLabel("s1")))
        out = await specs[0].fn(q="x")
        self.assertEqual(out, "3 kết quả")


class ScopeServerKhoaTheoNhan(unittest.TestCase):
    """M-4: `Decision` duyệt tool trên server A không duyệt tool CÙNG TÊN trên server B —
    `Scope.server` phải THAM GIA so khớp, không chỉ tồn tại như một trường chết."""

    def test_matches_true_khi_cung_server(self):
        s = Scope(tool="search", server="a")
        self.assertTrue(s.matches("search", {}, call_id=None, server="a"))

    def test_matches_false_khi_khac_server(self):
        s = Scope(tool="search", server="a")
        self.assertFalse(s.matches("search", {}, call_id=None, server="b"))

    def test_matches_false_khi_mot_ben_khong_co_server(self):
        s = Scope(tool="search", server=None)
        self.assertFalse(s.matches("search", {}, call_id=None, server="a"))

    def test_tool_local_khong_server_van_khop_nhu_truoc(self):
        """Hồi quy: mọi grant cho tool LOCAL (server=None ở cả hai phía) vẫn khớp y hệt
        trước khi trường `server` tồn tại."""
        s = Scope(tool="wipe", args={"x": 1}, call_id="c1")
        self.assertTrue(s.matches("wipe", {"x": 1}, call_id="c1", server=None))

    def test_decisionlog_lookup_khong_ro_ri_qua_server(self):
        log = DecisionLog()
        log.record(Decision(id="d1", verdict=Verdict.ALLOW,
                            scope=Scope(tool="search", server="a", call_id="c1"),
                            actor=Actor.policy("test"), decided_at=_now(),
                            expires_at=None, run_id="r1"))
        self.assertEqual(log.lookup("search", {}, run_id="r1", now=_now(),
                                    call_id="c1", server="a"), Verdict.ALLOW)
        self.assertEqual(log.lookup("search", {}, run_id="r1", now=_now(),
                                    call_id="c1", server="b"), Verdict.ASK,
                         "grant của server a không được rò sang server b")

    def test_mutation_scope_matches_khong_kiem_server(self):
        """Mutation: khôi phục `matches()` bản TRƯỚC T-9.1 (không kiểm `server` chút
        nào) — xác nhận grant rò từ server A sang server B ngay lập tức."""
        def old_matches(self, tool, args, *, call_id, server=None):
            if self.tool != tool:
                return False
            if self.call_id is not None and self.call_id != call_id:
                return False
            if self.args is None:
                return True
            return dict(self.args) == dict(args)

        s = Scope(tool="search", server="a", call_id="c1")
        self.assertFalse(s.matches("search", {}, call_id="c1", server="b"))
        self.assertTrue(old_matches(s, "search", {}, call_id="c1", server="b"),
                        "mutation (bỏ kiểm server) phải cho khớp SAI giữa hai server — "
                        "nếu nó cũng từ chối, test này không còn phân biệt được bản đúng "
                        "và bản có lỗi")


@tool(effect="danger")
def local_wipe(x: int) -> str:
    """Xoá cục bộ."""
    return "gone"


class RegateTuChoiKhiKhacServer(unittest.TestCase):
    """Tích hợp trên `lg/runtime.py::_regate` — nơi grant thật được tra lại lúc thực thi.
    ToolSpec MCP có `server="github"`; một `Decision` ghi (nhầm, hoặc do rug-pull đổi
    server) dưới nhãn khác không được coi là còn sống cho lời gọi này."""

    def _rt_with_mcp_tool(self, server: str):
        from fake_chat import FakeChat
        t = _tool(name="search")
        spec = classify_mcp_tool(t, McpServerPolicy(identity=ServerLabel(server)),
                                 call=lambda n, a: "x")
        graph, rt = build_agent(model=FakeChat(script=[]), tools=[spec], budget="$5")
        return rt, spec

    def test_deny_khi_grant_ghi_cho_server_khac(self):
        rt, spec = self._rt_with_mcp_tool("github")
        call_id, run_id = "c1", "r1"
        rt._decisions.record(Decision(
            id=f"dec-{call_id}", verdict=Verdict.ALLOW,
            scope=Scope(tool=spec.name, args={}, server="gitlab", call_id=call_id),
            actor=Actor.human("nqthiep", via="cli"), decided_at=_now(),
            expires_at=None, run_id=run_id, reason="approved"))
        state = {"messages": [], "step": 1, "run_id": run_id}
        p = {"tool": spec.name, "call": {"id": call_id, "name": spec.name, "args": {}}}
        gate = rt._regate(p, state)
        self.assertEqual(gate.verdict, Verdict.DENY)

    def test_allow_khi_grant_dung_server(self):
        rt, spec = self._rt_with_mcp_tool("github")
        call_id, run_id = "c1", "r2"
        rt._decisions.record(Decision(
            id=f"dec-{call_id}", verdict=Verdict.ALLOW,
            scope=Scope(tool=spec.name, args={}, server="github", call_id=call_id),
            actor=Actor.human("nqthiep", via="cli"), decided_at=_now(),
            expires_at=None, run_id=run_id, reason="approved"))
        state = {"messages": [], "step": 1, "run_id": run_id}
        p = {"tool": spec.name, "call": {"id": call_id, "name": spec.name, "args": {}}}
        gate = rt._regate(p, state)
        self.assertEqual(gate.verdict, Verdict.ALLOW)


if __name__ == "__main__":
    unittest.main()
