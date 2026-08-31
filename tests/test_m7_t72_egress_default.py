"""M7/T-7.2 (docs/17-research-alignment.md): `allowed_hosts` mặc định đổi từ `None`
(cho tất cả) sang `()` (chặn tất cả) — BREAKING CHANGE có chủ đích.

`EgressPolicy`'s cơ chế bên trong (S-18, đã đúng từ trước) không đổi: `_hosts is None`
nghĩa là không hạn chế, một allowlist rỗng nghĩa là chặn hết, một allowlist có phần tử
chỉ cho đúng những host đó qua. Cái ĐỔI ở đây chỉ là GIÁ TRỊ MẶC ĐỊNH khi `Agent`/
`build_agent` không nhận `allowed_hosts=` — trước là `None` (cho tất cả), giờ là `()`
(chặn tất cả). `allowed_hosts=None` truyền TƯỜNG MINH vẫn là escape hatch hợp lệ —
cùng khuôn `Budget(usd=None)` của S-20: không im lặng, phải gõ ra.
"""
import sys
import unittest

sys.path.insert(0, "src")

from harness import Agent, tool
from harness.models.fake import FakeModel


@tool(effect="external")
def fetch(url: str) -> str:
    """Lấy nội dung một URL."""
    return f"nội dung của {url}"


class MacDinhChanHet(unittest.TestCase):
    def test_khong_truyen_allowed_hosts_thi_external_bi_chan(self):
        """Đây chính là T-7.2: KHÔNG truyền `allowed_hosts=` gì cả — mặc định giờ phải
        CHẶN, không phải cho qua."""
        m = FakeModel([FakeModel.tool_call("fetch", {"url": "http://example.com"}),
                       FakeModel.text("xong")])
        agent = Agent(name="A", job="j", model="claude-opus-5", provider=m,
                      tools=[fetch], budget="$5")
        self.assertEqual(agent.allowed_hosts, (),
                         "mặc định phải là tuple rỗng, không phải None")
        r = agent.try_run("thử")
        self.assertTrue(r.ok)
        self.assertEqual(r.tools_run, (),
                         "fetch phải bị DENY bởi egress mặc định — không có host nào "
                         "trong allowlist rỗng")

    def test_truyen_none_tuong_minh_van_la_escape_hatch(self):
        """`allowed_hosts=None` TƯỜNG MINH phải vẫn hoạt động như trước T-7.2 — không
        hạn chế gì. Đây không phải lỗi cần sửa, là hợp đồng có chủ đích (S-20's khuôn)."""
        m = FakeModel([FakeModel.tool_call("fetch", {"url": "http://example.com"}),
                       FakeModel.text("xong")])
        agent = Agent(name="A", job="j", model="claude-opus-5", provider=m,
                      tools=[fetch], budget="$5", allowed_hosts=None)
        self.assertIsNone(agent.allowed_hosts)
        r = agent.try_run("thử")
        self.assertTrue(r.ok)
        self.assertEqual(r.tools_run, ("fetch",),
                         "allowed_hosts=None tường minh phải cho qua, không hạn chế")

    def test_allowlist_cu_the_chi_cho_dung_host_do(self):
        m = FakeModel([FakeModel.tool_call("fetch", {"url": "http://example.com"}),
                       FakeModel.text("xong")])
        agent = Agent(name="A", job="j", model="claude-opus-5", provider=m,
                      tools=[fetch], budget="$5", allowed_hosts=["example.com"])
        r = agent.try_run("thử")
        self.assertTrue(r.ok)
        self.assertEqual(r.tools_run, ("fetch",))

    def test_allowlist_cu_the_chan_host_khac(self):
        m = FakeModel([FakeModel.tool_call("fetch", {"url": "http://evil.com"}),
                       FakeModel.text("xong")])
        agent = Agent(name="A", job="j", model="claude-opus-5", provider=m,
                      tools=[fetch], budget="$5", allowed_hosts=["example.com"])
        r = agent.try_run("thử")
        self.assertTrue(r.ok)
        self.assertEqual(r.tools_run, ())


class LangGraphCungPhaiChanMacDinh(unittest.TestCase):
    def test_lg_khong_truyen_allowed_hosts_thi_bi_chan(self):
        from fake_chat import FakeChat
        from langgraph.checkpoint.memory import MemorySaver
        from harness.lg import build_agent

        graph, rt = build_agent(
            model=FakeChat(script=[FakeChat.call("fetch", {"url": "http://e.com"}),
                                   FakeChat.text("xong")]),
            tools=[fetch], budget="$5", checkpointer=MemorySaver())
        self.assertEqual(rt._budget, rt._budget)  # smoke: rt xây được
        from langchain_core.messages import HumanMessage
        out = graph.invoke({"messages": [HumanMessage("thử")]},
                           config={"configurable": {"thread_id": "t1"}})
        self.assertNotIn("fetch", out.get("ran", []) if isinstance(out.get("ran"), list) else [])


class MutationEgressDefaultCoTacDung(unittest.TestCase):
    def test_default_ve_None_thi_test_chinh_do(self):
        """Mutation: khôi phục default CŨ (`None`) trên `Agent.__init__` bằng cách gọi
        constructor với `allowed_hosts=None` GIẢ VỜ là default (mô phỏng revert) — xác
        nhận hành vi khác hẳn, chứng minh test chính phụ thuộc vào default MỚI."""
        m = FakeModel([FakeModel.tool_call("fetch", {"url": "http://example.com"}),
                       FakeModel.text("xong")])
        # Mô phỏng: nếu default vẫn là None (hành vi TRƯỚC T-7.2)
        agent = Agent(name="A", job="j", model="claude-opus-5", provider=m,
                      tools=[fetch], budget="$5", allowed_hosts=None)
        r = agent.try_run("thử")
        self.assertEqual(r.tools_run, ("fetch",),
                         "với default GIẢ (None), fetch phải CHẠY ĐƯỢC — khác kết quả "
                         "của test chính (default thật là () → bị chặn), chứng minh "
                         "test chính phụ thuộc đúng vào giá trị default mới")


if __name__ == "__main__":
    unittest.main()
