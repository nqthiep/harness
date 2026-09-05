"""H-7 (design/review-architect-round3.md, continuing test_attack_s27.py's own S-27
scenario one bucket further): `test_attack_s27.py` fixed the same-batch taint-staleness
gap for `read` (parallel-safe) followed by `write` (serial — S-27's own re-check). But
`EFFECT_PROFILES[EXTERNAL]` is BOTH `parallel_safe=True` AND a confidentiality sink
(`max_confidentiality=PUBLIC`, same as `write`) — so two `external`-effect tools in one
batch land in `dispatch.py`'s `parallel` bucket TOGETHER, and before this fix, nothing
re-checked `check_flow` there at all: the first call raising confidentiality to SECRET
did not stop an already-scheduled second `external` call, in the same `asyncio.gather`,
from running and returning its result — the secret literally crosses from one tool's
output into another's, no policy override or misconfiguration needed on the model's
part.

Kept in the same bilingual style `test_attack_s27.py` already established.
"""
import sys, unittest
sys.path.insert(0, "src")

from harness import Agent, tool
from harness.models.base import ModelResponse
from harness.models.fake import FakeModel
from harness.result import Usage

SHARED: dict = {}
RAN: list = []


@tool(effect="external")
async def peek_secret() -> str:
    """Đọc một giá trị bí mật (mô phỏng: tool nào đó đọc dữ liệu nhạy cảm).

    `async def`, không phải `def` thường: một tool `def` thường bị `@tool` bọc qua
    `asyncio.to_thread` (`tools/__init__.py`), một điểm suspend THẬT — hai tool như vậy
    chạy trong CÙNG MỘT `asyncio.gather` xen kẽ nhau theo lịch trình thật của thread
    pool, không còn là kịch bản xác định được nữa. `async def` không có `await` bên
    trong không bao giờ nhường lại control cho event loop khi được `await` — nó chạy
    trọn vẹn một lượt, đúng cách H-7's kịch bản gốc dựng lại được xác định (không cần
    adversarial timing)."""
    RAN.append("peek_secret")
    SHARED["v"] = "TOP-SECRET-PAYROLL-DATA"
    return SHARED["v"]


@tool(effect="external")
async def leak() -> str:
    """Đọc bất cứ gì đang được chia sẻ — tool khác, không liên quan tới peek_secret."""
    RAN.append("leak")
    return SHARED.get("v", "(nothing yet)")


def _multi_call() -> ModelResponse:
    return ModelResponse(
        ({"type": "tool_use", "id": "c1", "name": "peek_secret", "input": {}},
         {"type": "tool_use", "id": "c2", "name": "leak", "input": {}}),
        "tool_use", Usage(100, 15), "fake")


class ChayThatQuaAgent(unittest.TestCase):
    """Backend cổ điển (`dispatch.py::_run_tools`/`_bounded`)."""

    def setUp(self):
        SHARED.clear(); RAN.clear()

    def test_leak_bi_chan_du_ca_hai_cung_o_bucket_parallel(self):
        script = [_multi_call(), FakeModel.text("xong")]
        agent = Agent(name="A", job="đi", model="claude-opus-5",
                      provider=FakeModel(script), tools=[peek_secret, leak],
                      budget="$5", sensitive=["peek_secret"])
        r = agent.try_run("đi")
        self.assertTrue(r.ok)
        self.assertIn("peek_secret", r.tools_run)
        self.assertNotIn("leak", r.tools_run,
                         "leak chạy dù peek_secret (cùng lượt, cùng bucket parallel) vừa "
                         "nâng confidentiality lên SECRET — H-7: parallel không có re-check "
                         "giống serial's S-27")
        # Nội dung thật sự trả về cho leak (tool_use_id c2) không được chứa bí mật —
        # tìm đúng message user chứa cả hai tool_result (c1 và c2).
        contents = next(m["content"] for m in r.messages
                       if isinstance(m, dict) and m.get("role") == "user"
                       and isinstance(m.get("content"), list)
                       and any(c.get("tool_use_id") == "c2" for c in m["content"]))
        c2 = next(c for c in contents if c.get("tool_use_id") == "c2")
        self.assertNotIn("TOP-SECRET-PAYROLL-DATA", str(c2.get("content", "")))

    def test_tools_run_giu_dung_thu_tu_ngay_ca_khi_mot_cai_bi_chan(self):
        """`Result.tools_run` phải giữ đúng thứ tự yêu cầu (IDL-49) — `peek_secret`
        trước, dù `leak` (bị chặn) không còn nằm trong đó."""
        script = [_multi_call(), FakeModel.text("xong")]
        agent = Agent(name="A", job="đi", model="claude-opus-5",
                      provider=FakeModel(script), tools=[peek_secret, leak],
                      budget="$5", sensitive=["peek_secret"])
        r = agent.try_run("đi")
        self.assertEqual(r.tools_run, ("peek_secret",))


class ChayThatQuaGraph(unittest.TestCase):
    """Backend LangGraph — không có `asyncio.gather` nào trong `lg/runtime.py::_run_tools`
    (mọi call, kể cả `parallel_safe`, chạy tuần tự qua MỘT vòng `for`), nên `_regate`
    (đã đúng từ S-27) đã bảo vệ luôn kịch bản này — không cần bản vá riêng ở đây, chỉ xác
    nhận backend này chưa từng có lỗ hổng H-7 mô tả."""

    def setUp(self):
        SHARED.clear(); RAN.clear()

    def test_leak_bi_chan_tren_backend_langgraph(self):
        from fake_chat import FakeChat
        from langchain_core.messages import AIMessage, HumanMessage
        from langgraph.checkpoint.memory import MemorySaver

        from harness.lg import build_agent

        model = FakeChat(script=[
            AIMessage(content="", tool_calls=[
                {"name": "peek_secret", "args": {}, "id": "c1"},
                {"name": "leak", "args": {}, "id": "c2"}]),
            FakeChat.text("xong"),
        ])
        graph, rt = build_agent(model=model, tools=[peek_secret, leak],
                                budget="$5", checkpointer=MemorySaver(),
                                sensitive=["peek_secret"])
        graph.invoke({"messages": [HumanMessage("đi")], "step": 0},
                    {"configurable": {"thread_id": "t-h7"}})
        self.assertIn("peek_secret", RAN)
        self.assertNotIn("leak", RAN)


class DoiChungKhongNhamOan(unittest.TestCase):
    """Không có gì nhạy cảm thì cả hai vẫn chạy bình thường — không bị chặn oan."""

    def setUp(self):
        SHARED.clear(); RAN.clear()

    def test_khong_sensitive_thi_ca_hai_deu_chay(self):
        script = [_multi_call(), FakeModel.text("xong")]
        agent = Agent(name="A", job="đi", model="claude-opus-5",
                      provider=FakeModel(script), tools=[peek_secret, leak], budget="$5")
        r = agent.try_run("đi")
        self.assertTrue(r.ok)
        self.assertEqual(set(r.tools_run), {"peek_secret", "leak"})


class MutationXacNhanLoadBearing(unittest.TestCase):
    def test_khong_co_ban_va_thi_bucket_parallel_khong_bao_gio_bi_chan(self):
        """Mô phỏng CHÍNH XÁC hành vi trước bản vá: `check_flow` không bao giờ được gọi
        lại cho bucket `parallel` — chỉ có quyết định TRƯỚC batch (luôn ALLOW vì nhãn
        rỗng lúc đó), y hệt cách `_bounded` cũ chỉ gọi `_invoke` thẳng không qua gate."""
        from harness.policy.base import Verdict
        from harness.policy.builtin import check_flow
        from harness.policy.label import Confidentiality, Grants, Integrity, Label

        d_truoc = check_flow(Label(), leak, Grants())
        self.assertEqual(d_truoc.verdict, Verdict.ALLOW,
                         "mutation phải cho ALLOW lúc đầu batch")
        d_sau = check_flow(Label(Integrity.TRUSTED, Confidentiality.SECRET), leak,
                           Grants())
        self.assertEqual(d_sau.verdict, Verdict.DENY,
                         "mutation phải cho DENY nếu check_flow được gọi lại đúng lúc — "
                         "nếu fail, phép so sánh không còn phản ánh lỗi H-7")


if __name__ == "__main__":
    unittest.main()
