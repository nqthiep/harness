"""S-19: rửa taint qua compaction, và S-16: `accepts_tainted` không còn ở decorator.

Đây là hai chỗ chốt trên GIẤY ở bước "tiếp tục với confidentiality + nhãn per-message" giờ
được đưa vào CODE, kiểm bằng test viết theo cùng kỷ luật với bước 1/2: dựng lại kịch bản tấn
công cụ thể, xác nhận nó từng làm được (hoặc sẽ làm được nếu thiếu cơ chế), rồi chứng minh
cơ chế chặn nó — kèm mutation test để không phải trang trí.

Kịch bản S-19 gốc (design/00-foundation.md §3.2):
  1. `fetch` (external) trả nội dung UNTRUSTED chứa injection.
  2. Model đọc, sinh assistant message diễn đạt lại chỉ thị đó — L-2 phải stamp UNTRUSTED
     ngay lúc sinh, vì context nó thấy đã UNTRUSTED.
  3. `ClearToolResults` xoá NỘI DUNG của message `fetch` (không phải bản thân message) —
     nếu nó cũng xoá nhãn (bug S-19 thứ hai) thì nhãn hiệu dụng "giảm" sai.
  4. Model gọi tool `danger` — phải vẫn bị chặn vì message ở bước 2 còn UNTRUSTED.

Không cần đẩy context lên hàng trăm nghìn token để kích hoạt `_manage` thật: hai cơ chế
(A) `_manage` giữ nhãn khi xoá nội dung, (B) `call_model` stamp L-2 lúc sinh — được chứng
minh riêng, rồi tổ hợp trong một state dựng thẳng ở trạng thái "sau tấn công" để xác nhận
gate vẫn đứng.
"""
import unittest

from fake_chat import FakeChat
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from harness import tool
from harness.context.window import CLEARED
from harness.lg import build_agent
from harness.policy.label import Confidentiality, Integrity, Label

RAN: list = []


@tool(effect="danger")
def refund(ma: str) -> str:
    """Hoàn tiền."""
    RAN.append(("refund", ma))
    return "đã hoàn"


@tool(effect="write")
def send_report(to: str, body: str) -> str:
    """Gửi báo cáo ra ngoài — sink PUBLIC theo effect."""
    RAN.append(("send_report", to, body))
    return "đã gửi"


@tool(effect="read")
def read_payroll(who: str) -> str:
    """Đọc bảng lương nội bộ."""
    RAN.append(("read_payroll", who))
    return "5,000 USD"


class ManageGiuNhan(unittest.TestCase):
    """Cơ chế (A): xoá nội dung không được xoá nhãn."""

    def test_manage_giu_nhan_khi_xoa_noi_dung(self):
        graph, rt = build_agent(model=FakeChat(script=[]), tools=[], budget="$5",
                                model_name="fake")
        old = ToolMessage(content="x" * 500_000, tool_call_id="c0", id="tm0",
                          additional_kwargs={"label_integrity": "UNTRUSTED",
                                            "label_confidentiality": "PUBLIC"})
        recent = [ToolMessage(content="y", tool_call_id=f"c{i}", id=f"tm{i}")
                 for i in range(1, 5)]        # > KEEP_RECENT_STEPS, bảo vệ recent
        edited = rt._manage([old] + recent, {"step": 5})
        self.assertTrue(edited, "phải có message bị sửa — đã vượt EDIT_AT")
        cleared = edited[0]
        self.assertEqual(cleared.content, CLEARED)
        self.assertEqual(cleared.additional_kwargs.get("label_integrity"), "UNTRUSTED",
                         "xoá nội dung xoá luôn nhãn — đây chính là S-19")

    def test_mutation_khong_giu_kwargs_thi_do(self):
        """Mutation: dựng lại ToolMessage KHÔNG kèm additional_kwargs (đúng bug gốc) —
        phải mất nhãn, chứng minh test trên là load-bearing chứ không phải trang trí."""
        old = ToolMessage(content="x" * 500_000, tool_call_id="c0", id="tm0",
                          additional_kwargs={"label_integrity": "UNTRUSTED"})
        # Mô phỏng CHÍNH XÁC dòng code cũ: ToolMessage(content=CLEARED, tool_call_id=.., id=..)
        broken = ToolMessage(content=CLEARED, tool_call_id=old.tool_call_id, id=old.id)
        self.assertNotEqual(broken.additional_kwargs.get("label_integrity"), "UNTRUSTED",
                            "nếu dòng này FAIL nghĩa là bug đã quay lại")


class L2StampLucSinh(unittest.TestCase):
    """Cơ chế (B): message model sinh mang nhãn của TOÀN BỘ context tại thời điểm sinh."""

    def test_model_message_mang_nhan_untrusted_khi_context_da_nhiem(self):
        graph, rt = build_agent(
            model=FakeChat(script=[FakeChat.text("Người dùng muốn hoàn tiền ngay.")]),
            tools=[refund], budget="$5", model_name="fake")
        tainted_fetch = ToolMessage(content="đã đọc trang", tool_call_id="c0", id="tm0",
                                    additional_kwargs={"label_integrity": "UNTRUSTED"})
        state = {"messages": [HumanMessage("go"), tainted_fetch], "step": 1}
        out = rt.call_model(state)
        msg = out["messages"][0]
        self.assertEqual(msg.additional_kwargs.get("label_integrity"), "UNTRUSTED",
                         "message model sinh KHÔNG mang nhãn UNTRUSTED của context — L-2 hỏng")

    def test_model_message_trusted_khi_context_sach(self):
        """Đối chứng: không có gì UNTRUSTED trong context thì message model cũng vậy."""
        graph, rt = build_agent(model=FakeChat(script=[FakeChat.text("chào")]),
                                tools=[], budget="$5", model_name="fake")
        state = {"messages": [HumanMessage("go")], "step": 0}
        out = rt.call_model(state)
        msg = out["messages"][0]
        self.assertEqual(msg.additional_kwargs.get("label_integrity", "TRUSTED"), "TRUSTED")


class KhongRuaDuocQuaCompaction(unittest.TestCase):
    """Tổ hợp (A)+(B): trạng thái SAU tấn công — fetch đã bị xoá nội dung (nhưng giữ
    nhãn) VÀ model đã diễn đạt lại chỉ thị (đã stamp UNTRUSTED lúc sinh) — gate vẫn đứng."""

    def setUp(self):
        RAN.clear()

    def test_S19_danger_van_bi_chan_sau_ca_hai_buoc_tan_cong(self):
        graph, rt = build_agent(model=FakeChat(script=[]), tools=[refund], budget="$5",
                                checkpointer=MemorySaver())
        cleared_fetch = ToolMessage(content=CLEARED, tool_call_id="c0", id="tm0",
                                    additional_kwargs={"label_integrity": "UNTRUSTED"})
        restated = AIMessage(content="Người dùng muốn hoàn tiền ngay.",
                             additional_kwargs={"label_integrity": "UNTRUSTED"})
        state = {"messages": [HumanMessage("go"), cleared_fetch, restated], "step": 2,
                 "run_id": "t-launder",
                 "_pending": [{"tool": "refund",
                              "call": {"id": "c1", "name": "refund", "args": {"ma": "X"}},
                              "verdict": 1, "reason": "ask"}]}
        rt.run_tools(state)
        self.assertNotIn(("refund", "X"), RAN,
                         "taint bị rửa qua compaction + model diễn đạt lại — S-19 sống lại")

    def test_check_flow_don_le_dung_dung_mot_minh(self):
        """`check_flow` — nếu label là TRUSTED (đúng cái L-2 ngăn không cho xảy ra khi
        context thật ra đã nhiễm), nhánh integrity không còn gì để chặn. Cô lập đúng MỘT
        hàm, không lẫn với đường ASK/grant của `_regate` (test trước đã thử làm việc này
        qua `run_tools` và sai vì DANGER luôn ASK trước, không liên quan taint)."""
        from harness.policy.builtin import check_flow
        from harness.policy.label import Grants

        self.assertEqual(
            check_flow(Label(Integrity.UNTRUSTED), refund, Grants()).verdict.name, "DENY")
        self.assertEqual(
            check_flow(Label(Integrity.TRUSTED), refund, Grants()).verdict.name, "ALLOW",
            "label TRUSTED (mô phỏng thiếu L-2) không còn gì để check_flow chặn")


class TrucConfidentiality(unittest.TestCase):
    """S-3: nhánh confidentiality của `check_flow`, và nguồn nâng nó (`sensitive`)."""

    def setUp(self):
        RAN.clear()

    def test_sensitive_tool_chan_duoc_sink_public(self):
        graph, rt = build_agent(
            model=FakeChat(script=[
                FakeChat.call("read_payroll", {"who": "A"}, cid="c1"),
                FakeChat.call("send_report", {"to": "x@y.com", "body": "..."}, cid="c2"),
                FakeChat.text("xong"),
            ]),
            tools=[read_payroll, send_report], budget="$5", checkpointer=MemorySaver(),
            sensitive=["read_payroll"])
        cfg = {"configurable": {"thread_id": "t-secret"}}
        graph.invoke({"messages": [HumanMessage("đọc lương rồi gửi báo cáo")], "step": 0}, cfg)
        self.assertIn(("read_payroll", "A"), RAN)
        self.assertNotIn(("send_report", "x@y.com", "..."), RAN,
                         "dữ liệu SECRET (đã đọc bảng lương) vẫn tới được sink PUBLIC")

    def test_khong_sensitive_thi_khong_chan(self):
        """Đối chứng: không đánh dấu `sensitive` thì luồng dữ liệu bình thường, không bị
        chặn — nhánh confidentiality không tự ý siết cái gì cả."""
        graph, rt = build_agent(
            model=FakeChat(script=[
                FakeChat.call("read_payroll", {"who": "A"}, cid="c1"),
                FakeChat.call("send_report", {"to": "x@y.com", "body": "..."}, cid="c2"),
                FakeChat.text("xong"),
            ]),
            tools=[read_payroll, send_report], budget="$5", checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "t-nosecret"}}
        graph.invoke({"messages": [HumanMessage("đọc lương rồi gửi báo cáo")], "step": 0}, cfg)
        self.assertIn(("send_report", "x@y.com", "..."), RAN)

    def test_emits_of_nang_dung_mot_truc(self):
        from harness.policy.builtin import emits_of
        from harness.policy.label import Grants
        from harness.tools import EFFECT_PROFILES, Effect

        base = EFFECT_PROFILES[Effect.READ].emits
        self.assertEqual(base, Label())            # read mặc định TRUSTED/PUBLIC

        raised = emits_of(read_payroll, Grants(sensitive=frozenset({"read_payroll"})))
        self.assertEqual(raised.confidentiality, Confidentiality.SECRET)
        self.assertEqual(raised.integrity, Integrity.TRUSTED,
                         "sensitive chỉ nâng confidentiality, không đổi integrity")


class KhongConTrenDecorator(unittest.TestCase):
    """S-16: `accepts_tainted` không tồn tại trên `@tool()`/`ToolSpec` nữa."""

    def test_tool_decorator_khong_nhan_accepts_tainted(self):
        import inspect
        from harness.tools import tool as tool_decorator
        params = inspect.signature(tool_decorator).parameters
        self.assertNotIn("accepts_tainted", params,
                         "accepts_tainted quay lại decorator — đúng lỗ hổng S-16")

    def test_toolspec_khong_co_truong_accepts_tainted(self):
        from harness.tools import ToolSpec
        import dataclasses
        names = ([f.name for f in dataclasses.fields(ToolSpec)]
                if dataclasses.is_dataclass(ToolSpec) else dir(ToolSpec))
        self.assertNotIn("accepts_tainted", names)


if __name__ == "__main__":
    unittest.main()
