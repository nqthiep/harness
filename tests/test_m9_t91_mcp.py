"""T-9.1, docs/17-research-alignment.md M9 — MCP client làm tool boundary.

Đóng S-7 (không có ServerIdentity -> đưa `ServerLabel` vào `Scope`/`Decision`, xem
`ScopeServerBoundary`), S-8 (annotation MCP được tin trực tiếp -> `classify_mcp_tool`
không bao giờ đọc hint của server không trusted, `ClassifyMcpTool`), S-9 (rug-pull ->
`RugPull`), S-10 (không phân loại theo effect -> cả `ClassifyMcpTool`), S-17 (injection
qua description -> `DescriptionBound`, cộng ghi nhận thẳng phần KHÔNG đóng được ở
design/07-risks-and-open-issues.md, cùng kỷ luật S-18).
"""
import asyncio
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "src")

from harness.mcp import (
    MAX_MCP_DESCRIPTION_CHARS,
    McpRugPullError,
    McpServerPolicy,
    McpTool,
    McpToolNotAllowed,
    ToolAnnotations,
    bind_mcp_server,
    classify_mcp_tool,
)
from harness.policy.base import Verdict
from harness.policy.decision import Actor, Decision, DecisionLog, Scope
from harness.tools import Effect

_SERVER = os.path.join(os.path.dirname(__file__), "fake_mcp_server.py")


def _tool(name="t", ann: ToolAnnotations | None = None, description="d") -> McpTool:
    return McpTool(name=name, description=description,
                   input_schema={"type": "object"}, annotations=ann)


class ClassifyMcpTool(unittest.TestCase):
    """M-1…M-3, design/03-tools-and-mcp.md §5.3."""

    def test_m1_untrusted_server_ignores_hints_uses_default(self):
        policy = McpServerPolicy(identity="evil", trusted=False)
        ann = ToolAnnotations(read_only_hint=True, open_world_hint=False)  # "looks safe"
        spec = classify_mcp_tool(_tool(ann=ann), policy)
        self.assertEqual(spec.effect, Effect.DANGER,
                         "server không trusted: hint KHÔNG được tham gia phân loại")

    def test_m1_operator_override_wins_even_untrusted(self):
        policy = McpServerPolicy(identity="evil", trusted=False,
                                 effects={"t": Effect.READ})
        spec = classify_mcp_tool(_tool(), policy)
        self.assertEqual(spec.effect, Effect.READ)

    def test_m2_trusted_readonly_closed_world_is_read(self):
        policy = McpServerPolicy(identity="github", trusted=True)
        ann = ToolAnnotations(read_only_hint=True, open_world_hint=False)
        spec = classify_mcp_tool(_tool(ann=ann), policy)
        self.assertEqual(spec.effect, Effect.READ)

    def test_m2_trusted_readonly_open_world_is_external_not_read(self):
        """Đọc + mở internet -> nguồn injection, không được coi an toàn như read thuần."""
        policy = McpServerPolicy(identity="github", trusted=True)
        ann = ToolAnnotations(read_only_hint=True, open_world_hint=True)
        spec = classify_mcp_tool(_tool(ann=ann), policy)
        self.assertEqual(spec.effect, Effect.EXTERNAL)

    def test_m2_readonly_false_is_not_read_microsofts_lesson(self):
        """`is True`, không phải "khác False": readOnlyHint=False (khai rõ) vẫn KHÔNG phải
        read — vì Microsoft's bug là coi thiếu/false readOnlyHint là an toàn."""
        policy = McpServerPolicy(identity="github", trusted=True)
        ann = ToolAnnotations(read_only_hint=False)
        spec = classify_mcp_tool(_tool(ann=ann), policy)
        self.assertNotEqual(spec.effect, Effect.READ)

    def test_m2_destructive_false_is_write(self):
        policy = McpServerPolicy(identity="github", trusted=True)
        ann = ToolAnnotations(destructive_hint=False)
        spec = classify_mcp_tool(_tool(ann=ann), policy)
        self.assertEqual(spec.effect, Effect.WRITE)

    def test_m2_no_annotations_fails_closed_even_when_trusted(self):
        policy = McpServerPolicy(identity="github", trusted=True)
        spec = classify_mcp_tool(_tool(ann=None), policy)
        self.assertEqual(spec.effect, Effect.DANGER)

    def test_m3_accepts_tainted_never_derivable_from_hints(self):
        """ToolSpec không có trường `accepts_tainted` cho từng tool — nó CHỈ tồn tại ở
        `Agent(accepts_tainted={...})`, do operator đặt (S-16). classify_mcp_tool không
        có cách nào để "tự khai" nó từ hint dù cố ý, vì không có chỗ để ghi."""
        import dataclasses
        from harness.tools import ToolSpec
        names = {f.name for f in dataclasses.fields(ToolSpec)}
        self.assertNotIn("accepts_tainted", names)

    def test_server_label_stamped_onto_toolspec(self):
        policy = McpServerPolicy(identity="my-server", trusted=False)
        spec = classify_mcp_tool(_tool(), policy)
        self.assertEqual(spec.server, "my-server")

    def test_allow_list_rejects_unlisted_tool(self):
        policy = McpServerPolicy(identity="s", allow=frozenset({"other"}))
        with self.assertRaises(McpToolNotAllowed):
            classify_mcp_tool(_tool(name="t"), policy)

    def test_native_tools_have_no_server(self):
        from harness.tools import tool as tool_decorator

        @tool_decorator(effect="read")
        def native(x: int) -> int:
            """d"""
            return x

        self.assertIsNone(native.server)


class DescriptionBound(unittest.TestCase):
    """S-17 — bound kích thước, không lọc nội dung (giống S-18: nói thẳng cái không đóng
    được). Xem docstring `harness.mcp.MAX_MCP_DESCRIPTION_CHARS`."""

    def test_untrusted_description_truncated(self):
        long_desc = "A" * (MAX_MCP_DESCRIPTION_CHARS + 500)
        policy = McpServerPolicy(identity="evil", trusted=False)
        spec = classify_mcp_tool(_tool(description=long_desc), policy)
        self.assertLess(len(spec.description), len(long_desc))
        self.assertLessEqual(len(spec.description),
                             MAX_MCP_DESCRIPTION_CHARS + len("\n[bị cắt — mô tả từ MCP "
                                                              "server không được operator "
                                                              "đánh dấu trusted]"))

    def test_trusted_description_kept_verbatim(self):
        long_desc = "A" * (MAX_MCP_DESCRIPTION_CHARS + 500)
        policy = McpServerPolicy(identity="github", trusted=True)
        spec = classify_mcp_tool(_tool(description=long_desc), policy)
        self.assertEqual(spec.description, long_desc)

    def test_short_description_untouched(self):
        policy = McpServerPolicy(identity="evil", trusted=False)
        spec = classify_mcp_tool(_tool(description="short"), policy)
        self.assertEqual(spec.description, "short")


class ScopeServerBoundary(unittest.TestCase):
    """M-4, design/03 §5.3 — `Scope.server` giờ THẬT SỰ được so khớp (trước bản vá này,
    trường tồn tại nhưng `matches()` không đọc nó — confused-deputy mở toang)."""

    def _grant(self, *, tool: str, server: str | None) -> DecisionLog:
        log = DecisionLog()
        log.record(Decision(
            id="d1", verdict=Verdict.ALLOW,
            scope=Scope(tool=tool, args=None, server=server),
            actor=Actor.operator("op"), decided_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            run_id="r1"))
        return log

    def test_grant_on_server_a_does_not_authorize_server_b(self):
        log = self._grant(tool="search", server="server-a")
        v = log.lookup("search", {}, run_id="r1", now=datetime.now(timezone.utc),
                       server="server-b")
        self.assertEqual(v, Verdict.ASK, "grant server A khớp bừa với tool cùng tên server B")

    def test_grant_on_server_a_authorizes_server_a(self):
        log = self._grant(tool="search", server="server-a")
        v = log.lookup("search", {}, run_id="r1", now=datetime.now(timezone.utc),
                       server="server-a")
        self.assertEqual(v, Verdict.ALLOW)

    def test_native_tool_grant_unaffected_server_none_both_sides(self):
        """Tool native (server=None) trước và sau bản vá hành xử giống nhau."""
        log = self._grant(tool="delete_file", server=None)
        v = log.lookup("delete_file", {}, run_id="r1", now=datetime.now(timezone.utc),
                       server=None)
        self.assertEqual(v, Verdict.ALLOW)


class StdioTransportAndRugPull(unittest.TestCase):
    """Chạy `fake_mcp_server.py` như một tiến trình con THẬT — không mock transport."""

    def test_bind_classifies_and_calls_a_real_subprocess_server(self):
        async def go():
            policy = McpServerPolicy(identity="fake", trusted=True)
            binding = await bind_mcp_server(sys.executable, [_SERVER], policy=policy)
            try:
                by_name = {t.name: t for t in binding.tools}
                self.assertEqual(by_name["echo"].effect, Effect.READ)
                self.assertEqual(by_name["delete_everything"].effect, Effect.DANGER)
                result = await by_name["echo"].fn(text="hello mcp")
                self.assertEqual(result, "hello mcp")
            finally:
                await binding.close()
        asyncio.run(go())

    def test_rug_pull_detected_and_fails_closed(self):
        async def go():
            env = {**os.environ, "FAKE_MCP_RUGPULL": "1"}
            policy = McpServerPolicy(identity="fake", trusted=True)
            binding = await bind_mcp_server(sys.executable, [_SERVER], policy=policy, env=env)
            try:
                with self.assertRaises(McpRugPullError):
                    await binding.check_for_rug_pull()
            finally:
                await binding.close()
        asyncio.run(go())

    def test_no_rug_pull_when_server_stable(self):
        async def go():
            policy = McpServerPolicy(identity="fake", trusted=True)
            binding = await bind_mcp_server(sys.executable, [_SERVER], policy=policy)
            try:
                await binding.check_for_rug_pull()          # không raise
            finally:
                await binding.close()
        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
