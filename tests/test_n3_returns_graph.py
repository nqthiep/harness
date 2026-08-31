"""N-3 (design/07-risks-and-open-issues.md): backend LangGraph hỗ trợ `returns=`.

Trước bản vá, `build_agent()` không có tham số `returns=` nào cả, và `lg/runtime.py`
không gọi `_parse_returns`/tương đương ở đâu — câu trả lời cuối cùng của model không bao
giờ được kiểm, `state["value"]` không tồn tại. Đây là một khoảng trống PARITY thật, vì
`docs/03-public-api.md` không ghi `returns=` là "chỉ vòng lặp classic" ở đâu cả.

`parse_returns` (module-level, `run.py`) được TÁI DÙNG chứ không viết lại — một model trả
JSON hỏng nhận đúng một thông điệp trên cả hai backend (R-17).

**Giới hạn nói thẳng, không giấu:** backend này không dựng system prompt từ `job=`
(`build_agent()` không có tham số đó) và không ràng buộc provider phải sinh đúng schema
(khác vòng lặp classic, nơi `output_format` đi thẳng vào `output_config.format` của
Anthropic SDK) — bản vá này chỉ PARSE và VALIDATE cái model đã trả về, không ép model trả
JSON. Test dưới đây dùng `FakeChat` (đã kịch bản sẵn câu trả lời) nên không cần quan tâm
model có tự nguyện trả JSON hay không — đúng điều bản vá THẬT SỰ đảm bảo.
"""
import dataclasses
import sys
import unittest

sys.path.insert(0, "src")
sys.path.insert(0, "tests")

from fake_chat import FakeChat
from langchain_core.messages import HumanMessage

from harness.lg import build_agent
from harness.lg.state import AgentState


@dataclasses.dataclass
class Order:
    id: str
    eta_days: int


def run(script, **kw):
    graph, rt = build_agent(model=FakeChat(script=script), budget="$5, 20 steps", **kw)
    return graph.invoke({"messages": [HumanMessage("go")], "step": 0}), rt


class KhaiBaoTrangThai(unittest.TestCase):
    def test_value_duoc_khai_bao_trong_AgentState(self):
        """IDL-41: LangGraph âm thầm bỏ một key không khai báo — thiếu dòng này thì
        `finish` trả `value` mà chẳng ai thấy nó trong state cuối cùng."""
        self.assertIn("value", AgentState.__annotations__)


class DataclassHopLe(unittest.TestCase):
    def test_json_dung_schema_thi_parse_thanh_dict(self):
        out, _ = run([FakeChat.text('{"id": "o1", "eta_days": 3}')], returns=Order)
        self.assertEqual(out.get("stop_reason"), "completed")
        self.assertEqual(out.get("value"), {"id": "o1", "eta_days": 3})

    def test_value_la_dict_JSON_hoa_duoc_khong_phai_instance_dataclass(self):
        """State đã checkpoint — một instance dataclass không bảo đảm round-trip qua
        checkpointer, một dict thì có (IDL-42's lý do, một tầng cao hơn)."""
        out, _ = run([FakeChat.text('{"id": "o1", "eta_days": 3}')], returns=Order)
        value = out.get("value")
        self.assertIsInstance(value, dict)
        self.assertNotIsInstance(value, Order)
        import json
        json.dumps(value)   # không raise — chính là điều kiện IDL-42 đòi

    def test_truong_du_thi_bi_loai_khong_lam_hong_dataclass(self):
        out, _ = run([FakeChat.text('{"id": "o1", "eta_days": 3, "rac": "x"}')],
                     returns=Order)
        self.assertEqual(out.get("value"), {"id": "o1", "eta_days": 3})


class KhongPhaiJsonHopLe(unittest.TestCase):
    def test_khong_phai_json_thi_dung_kem_ly_do_doc_duoc(self):
        out, _ = run([FakeChat.text("xin chào, đây không phải JSON")], returns=Order)
        self.assertEqual(out.get("stop_reason"), "error")
        self.assertIn("Order", out.get("detail", ""))
        self.assertIn("not Order", out.get("detail", ""))

    def test_thieu_truong_bat_buoc_thi_neu_ro_truong_nao(self):
        out, _ = run([FakeChat.text('{"id": "o1"}')], returns=Order)
        self.assertEqual(out.get("stop_reason"), "error")
        self.assertIn("eta_days", out.get("detail", ""))

    def test_loi_returns_khong_lam_run_bi_stall_hay_treo(self):
        """Một lỗi `returns=` là một OUTCOME của run — giống N-2 đã sửa cho vòng lặp
        classic — không phải một crash ra khỏi `graph.invoke()`."""
        out, _ = run([FakeChat.text("rác")], returns=Order)
        self.assertIn(out.get("stop_reason"), ("error",))
        self.assertNotEqual(out.get("stop_reason"), "stalled")


class KhongDatReturns(unittest.TestCase):
    def test_khong_truyen_returns_thi_value_la_None_nhu_truoc(self):
        out, _ = run([FakeChat.text("chào bạn")])
        self.assertEqual(out.get("stop_reason"), "completed")
        self.assertIsNone(out.get("value"))


class KieuKhongPhaiDataclass(unittest.TestCase):
    def test_returns_mot_kieu_khong_phai_dataclass_thi_tra_ve_du_lieu_tho(self):
        """Cùng nhánh `parse_returns` của vòng lặp classic: không phải dataclass thì trả
        thẳng JSON đã parse, không có bước validate trường."""
        out, _ = run([FakeChat.text('{"a": 1, "b": [1, 2]}')], returns=dict)
        self.assertEqual(out.get("value"), {"a": 1, "b": [1, 2]})


class ChiKichHoatKhiHoanThanhSach(unittest.TestCase):
    def test_run_dung_o_step_limit_thi_khong_dam_parse_returns(self):
        """`stop == "completed"` là điều kiện, không phải "còn text nào đó" — một run bị
        cắt ở trần bước không nên bị parse như thể model đã trả lời xong."""
        from harness import tool

        @tool(effect="read")
        def look(x: int) -> str:
            """Nhìn."""
            return "ok"

        script = [FakeChat.call("look", {"x": i}, f"c{i}") for i in range(10)]
        graph, _ = build_agent(model=FakeChat(script=script), tools=[look],
                               budget="$5, 2 steps", returns=Order)
        out = graph.invoke({"messages": [HumanMessage("go")], "step": 0})
        self.assertEqual(out.get("stop_reason"), "step_limit")
        self.assertIsNone(out.get("value"))


class ChiaSeMotDinhNghiaVoiVongLapClassic(unittest.TestCase):
    def test_hai_backend_dung_chung_ham_parse_returns(self):
        """R-17: hai bản sao của một luật luôn trôi khỏi nhau. Đây khoá bằng cách đọc
        đúng identity của hàm, không phải so sánh hai đoạn code."""
        import harness.lg.runtime as lg_runtime
        import harness.run as run_mod
        self.assertIs(lg_runtime.parse_returns, run_mod.parse_returns)

    def test_cung_mot_cau_loi_tren_ca_hai_backend(self):
        import asyncio

        from harness import Agent
        from harness.models.fake import FakeModel

        classic = Agent(name="T", job="j", model="claude-opus-5", budget="$5",
                        returns=Order,
                        provider=FakeModel([FakeModel.text("không phải JSON")]))
        r = asyncio.run(classic.atry_run("go"))
        out, _ = run([FakeChat.text("không phải JSON")], returns=Order)
        self.assertEqual(r.detail, out.get("detail"))


if __name__ == "__main__":
    unittest.main()
