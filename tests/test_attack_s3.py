"""S-3: trục confidentiality không có đường nào nâng lên `SECRET` — cả `Grants.sensitive`
(nguồn thứ hai, operator gắn nhãn cho một tool cả gói) VÀ `Secret[T]` người dùng đưa vào
(nguồn thứ nhất) đều được liệt kê ở `00-foundation.md §3.2`, nhưng chỉ nguồn thứ hai từng
vào `src/harness/` — xem `tests/test_attack_s19.py::TrucConfidentiality` cho nguồn đó.

Nguồn thứ nhất: không có cơ chế `deps` riêng trong harness này, nên đường thật nó đi qua là
một tool tự `.reveal()` một `Secret` (vd. để ký request) rồi chính GIÁ TRỊ đó xuất hiện
nguyên văn trong payload trả về — đúng khoảnh khắc `redact()` đã canh sẵn để chặn trước khi
bytes tới model (`secrets.py`, RT-13/Round 35). `contains_live_secret()` dùng lại đúng phép
so khớp đó để phát hiện, và `emits_of(spec, grants, payload)` nâng nhãn message lên SECRET
khi phát hiện — dù `redact()` đã xoá đúng chuỗi token đó khỏi bytes model thấy, phần CÒN LẠI
của payload (nội dung khác cùng message) không bị xoá, nên nhãn SECRET vẫn cần để `check_flow`
chặn nó rời qua sink PUBLIC ở bước sau — cùng độ chi tiết "theo message" mà toàn bộ Label
dùng, không theo từng ký tự.

Kịch bản khai thác gốc (review-security.md S-3, biến thể dùng `Secret` thay vì chuỗi trần):
  1. Agent có `sign_request` (effect=read) và `send_report` (effect=write, sink PUBLIC).
  2. `sign_request` dựng `Secret("sk-live-...", name="api_key")`, `.reveal()` nó để ký, rồi
     LỖI (hoặc bị điều khiển) trả nguyên payload còn chứa token đã lộ.
  3. Trước bản vá: `emits_of` không thấy payload ⇒ nhãn không đổi ⇒ vẫn PUBLIC.
  4. `send_report` gọi tiếp — `check_flow` không có gì để chặn, dữ liệu (đã lộ secret) rời
     máy qua sink PUBLIC. Không `Decision` DENY nào.
"""
import unittest

from harness import Agent, tool
from harness.models.fake import FakeModel
from harness.secrets import Secret, contains_live_secret, redaction_scope

RAN: list = []
TOKEN = "sk-live-abc123-do-not-leak"


@tool(effect="read")
def sign_request(payload: str) -> str:
    """Ký request bằng một credential nội bộ — VÀ (lỗi) trả luôn token đã lộ."""
    tok = Secret(TOKEN, name="api_key")
    with tok.reveal() as v:
        return f"signed({payload}) with token={v}"


@tool(effect="write")
def send_report(body: str) -> str:
    """Gửi báo cáo ra ngoài — sink PUBLIC theo effect."""
    RAN.append(body)
    return "đã gửi"


class ContainsLiveSecret(unittest.TestCase):
    """Đơn vị nhỏ nhất: phát hiện đúng, không đăng ký thêm giá trị khi chỉ dò."""

    def test_phat_hien_gia_tri_con_song(self):
        with redaction_scope():
            s = Secret("hunter2", name="pw")
            with s.reveal() as v:
                self.assertTrue(contains_live_secret(f"mật khẩu là {v}"))
            self.assertFalse(contains_live_secret("không có gì nhạy cảm ở đây"))

    def test_khong_tu_dang_ky_khi_chi_do(self):
        """`contains_live_secret` không gọi `.reveal()` — dò không được tự làm giá trị
        trở thành 'đã lộ' cho `redact()` tương lai coi là cần xoá."""
        with redaction_scope():
            s = Secret("hunter2", name="pw")
            contains_live_secret("mật khẩu là hunter2")  # không qua .reveal()
            from harness.secrets import redact
            # Vẫn redact được vì Secret còn sống trong registry yếu — đây không phải điều
            # đang kiểm; điều đang kiểm là contains_live_secret không throw, không side effect.
            self.assertEqual(redact("hunter2"), "<pw hidden>")
            self.assertIsNotNone(s)             # giữ tham chiếu sống tới đây (tránh GC sớm, RT-13)


class EmitsOfNangNhanTuPayload(unittest.TestCase):
    """`emits_of(spec, grants, payload)` — đơn vị, không cần dựng agent."""

    def test_khong_co_secret_trong_payload_thi_khong_nang(self):
        from harness.policy.builtin import emits_of
        from harness.policy.label import Confidentiality, Grants

        raised = emits_of(sign_request, Grants(), payload="không có gì nhạy cảm")
        self.assertEqual(raised.confidentiality, Confidentiality.PUBLIC)

    def test_secret_con_song_trong_payload_nang_len_secret(self):
        from harness.policy.builtin import emits_of
        from harness.policy.label import Confidentiality, Grants

        with redaction_scope():
            s = Secret("tok-xyz", name="api_key")
            with s.reveal() as v:
                payload = f"kết quả: token={v}"
            raised = emits_of(sign_request, Grants(), payload=payload)
        self.assertEqual(raised.confidentiality, Confidentiality.SECRET,
                         "Secret còn sống xuất hiện trong payload nhưng nhãn không nâng")

    def test_khong_truyen_payload_thi_giu_hanh_vi_cu(self):
        """Tương thích ngược: gọi không kèm `payload` (chữ ký cũ) vẫn hoạt động — chỉ
        `grants.sensitive` mới nâng, giống trước khi có S-3 nguồn thứ nhất."""
        from harness.policy.builtin import emits_of
        from harness.policy.label import Confidentiality, Grants

        raised = emits_of(sign_request, Grants())
        self.assertEqual(raised.confidentiality, Confidentiality.PUBLIC)


class ChayThatQuaAgent(unittest.TestCase):
    """`_invoke` (dispatch.py, backend cổ điển) thật sự gọi `emits_of(..., payload)` đúng
    chỗ — không chỉ đơn vị `emits_of` cô lập."""

    def setUp(self):
        RAN.clear()

    def test_secret_lo_qua_tool_chan_duoc_sink_public_ke_tiep(self):
        script = [
            FakeModel.tool_call("sign_request", {"payload": "x"}),
            FakeModel.tool_call("send_report", {"body": "y"}),
            FakeModel.text("xong"),
        ]
        agent = Agent(name="A", job="ký rồi gửi", model="claude-opus-5",
                      provider=FakeModel(script), tools=[sign_request, send_report],
                      budget="$5")
        agent.try_run("đi")
        self.assertNotIn("y", RAN,
                         "send_report vẫn chạy dù sign_request vừa làm lộ một Secret vào "
                         "context — nhãn confidentiality không hề nâng, S-3 nguồn thứ nhất")

    def test_khong_co_secret_thi_khong_chan(self):
        """Đối chứng: tool không đụng tới Secret nào thì luồng bình thường, không bị chặn
        oan — nguồn thứ nhất không tự ý siết cái gì khi không có gì để phát hiện."""
        @tool(effect="read")
        def plain_read(payload: str) -> str:
            """Đọc — không đụng Secret nào."""
            return f"đọc xong: {payload}"

        script = [
            FakeModel.tool_call("plain_read", {"payload": "x"}),
            FakeModel.tool_call("send_report", {"body": "z"}),
            FakeModel.text("xong"),
        ]
        agent = Agent(name="B", job="đọc rồi gửi", model="claude-opus-5",
                      provider=FakeModel(script), tools=[plain_read, send_report],
                      budget="$5")
        agent.try_run("đi")
        self.assertIn("z", RAN)


class ChayThatQuaGraph(unittest.TestCase):
    """Backend LangGraph (`lg/runtime.py::_run_tools`) — cùng cơ chế, chỗ gọi khác với
    backend cổ điển; kiểm riêng vì hai backend không dùng chung code path."""

    def setUp(self):
        RAN.clear()

    def test_secret_lo_qua_tool_chan_duoc_sink_public_ke_tiep(self):
        from fake_chat import FakeChat
        from langchain_core.messages import HumanMessage
        from langgraph.checkpoint.memory import MemorySaver

        from harness.lg import build_agent

        graph, rt = build_agent(
            model=FakeChat(script=[
                FakeChat.call("sign_request", {"payload": "x"}, cid="c1"),
                FakeChat.call("send_report", {"body": "y"}, cid="c2"),
                FakeChat.text("xong"),
            ]),
            tools=[sign_request, send_report], budget="$5", checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "t-secret-graph"}}
        graph.invoke({"messages": [HumanMessage("ký rồi gửi")], "step": 0}, cfg)
        self.assertNotIn("y", RAN,
                         "send_report vẫn chạy trên backend LangGraph dù sign_request vừa "
                         "làm lộ một Secret vào context")


class MutationXacNhanLoadBearing(unittest.TestCase):
    """Khôi phục hành vi CŨ — `emits_of` gọi không kèm `payload` tại chỗ dispatch — và xác
    nhận secret lộ qua tool KHÔNG còn bị `check_flow` chặn, chứng minh bản vá không phải
    trang trí."""

    def setUp(self):
        RAN.clear()

    def test_khong_truyen_payload_thi_secret_lo_ma_khong_bi_chan(self):
        from harness.policy.builtin import check_flow
        from harness.policy.label import Confidentiality, Grants, Label

        with redaction_scope():
            s = Secret("tok-mutation", name="api_key")
            with s.reveal():
                pass                            # đăng ký giá trị "đã lộ", như tool thật làm
            from harness.policy.builtin import emits_of
            # Mô phỏng CHÍNH XÁC lời gọi trước bản vá: không truyền payload.
            old_behavior = emits_of(sign_request, Grants())
        self.assertEqual(old_behavior.confidentiality, Confidentiality.PUBLIC,
                         "mutation phải cho PUBLIC — nếu fail nghĩa là phép so sánh sai")
        self.assertEqual(
            check_flow(Label(confidentiality=old_behavior.confidentiality), send_report,
                      Grants()).verdict.name, "ALLOW",
            "dưới hành vi cũ, send_report (sink PUBLIC) không hề bị chặn dù secret vừa lộ")


if __name__ == "__main__":
    unittest.main()
