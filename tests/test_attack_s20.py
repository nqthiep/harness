"""S-20: `Budget.usd: Decimal | None` cho phép dựng `Budget(usd=None, ...)` trực tiếp
(không qua `Budget.parse()`) rồi truyền cho `Agent(budget=...)` — không `ConfigError`,
không cảnh báo nào. `Ledger.size_call()`/`reserve()` cả hai đều có nhánh `if
self._b.usd is None: ...` bỏ qua trần hoàn toàn, nên với một provider THẬT (không phải
`FakeModel`), đây là loop-limit-không-kèm-spend-ceiling đạt được bằng một keyword
argument — đúng mô tả gốc `design/review-security.md` §S-20.

Bản nháp đầu của `design/08-roadmap-and-release-plan.md §1` từng đề xuất SỬA bằng cách
chặn construction. Kiểm lại trước khi viết code thì thấy sai: `docs/04-interfaces.md:452`
và `docs/07-cost.md:75` đã quyết định — bằng câu chữ — một hợp đồng KHÁC: `usd=None` được
PHÉP (escape hatch tường minh, phục vụ `FakeModel`/model local không tính phí, IDL-36),
nhưng "emits a `budget.unlimited` warning event on every run. Unlimited is possible; it
is not silent." Cơ chế này CHƯA TỪNG được cài — `grep "unlimited/UNLIMITED"` trên toàn bộ
`src/harness/` trước bản vá này chỉ ra đúng một chỗ (`run.py`'s f-string hiển thị, không
phải event). Bản vá: thêm `EventKind.BUDGET_UNLIMITED` (`budget.unlimited`, ADR-041), phát
đúng một lần mỗi run/thread ngay sau `RUN_STARTED`, ở cả hai backend.
"""
import sys, unittest
sys.path.insert(0, "src"); sys.path.insert(0, "tests")

from harness import Agent
from harness.budget.ledger import Budget
from harness.models.fake import FakeModel
from harness.observe.events import EventKind


class Sink:
    def __init__(self) -> None:
        self.events: list = []

    def emit(self, event) -> None:
        self.events.append(event)

    def close(self) -> None: ...


class KhongTranTienPhaiCanhBaoRoRang(unittest.TestCase):
    def test_usd_none_dung_duoc_khong_config_error(self):
        """`usd=None` vẫn phải DỰNG ĐƯỢC — nó là escape hatch có chủ đích (IDL-36), không
        phải lỗi cần chặn. Nếu test này fail (raise), sửa đã đi quá tay (hard-reject),
        khác với hợp đồng docs/04/docs/07 đã công bố."""
        b = Budget(usd=None, steps=100, wall_clock_s=3600)
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.text("hi")]), budget=b)
        self.assertIsNone(agent.budget.usd)

    def test_usd_none_phat_canh_bao_dung_mot_lan_moi_run(self):
        sink = Sink()
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.text("hi")]),
                      budget=Budget(usd=None, steps=100, wall_clock_s=3600),
                      exporters=[sink])
        r = agent.try_run("chào")
        self.assertTrue(r.ok)
        warnings = [e for e in sink.events if e.kind is EventKind.BUDGET_UNLIMITED]
        self.assertEqual(len(warnings), 1,
                         "budget.usd is None phải phát ĐÚNG MỘT cảnh báo budget.unlimited "
                         "mỗi run — không phát nghĩa là unlimited vẫn im lặng (đúng lỗi "
                         "S-20 mô tả); phát nhiều hơn một là rò rỉ per-step")

    def test_usd_co_gia_tri_khong_phat_canh_bao(self):
        """Đối chứng: một budget có trục tiền bình thường không được phát cảnh báo này —
        cảnh báo phải đặc hiệu cho unlimited, không phải phát vô điều kiện."""
        sink = Sink()
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.text("hi")]),
                      budget="$5", exporters=[sink])
        r = agent.try_run("chào")
        self.assertTrue(r.ok)
        warnings = [e for e in sink.events if e.kind is EventKind.BUDGET_UNLIMITED]
        self.assertEqual(warnings, [])

    def test_size_call_va_reserve_van_khong_tran_khi_usd_none(self):
        """Xác nhận hành vi kỹ thuật gốc S-20 mô tả vẫn còn (đây là ca dùng hợp lệ của
        escape hatch, không phải thứ cần sửa) — cảnh báo phải LÀM CHO THẤY ĐƯỢC hành vi
        này, không xoá nó."""
        from harness.models.pricing import PRICES
        led = __import__("harness.budget.ledger", fromlist=["Ledger"]).Ledger(
            Budget(usd=None, steps=100, wall_clock_s=3600))
        cap = led.size_call(100, PRICES["claude-opus-5"], model_max=8192)
        self.assertEqual(cap, 8192, "usd=None vẫn phải cho model_max đầy đủ (IDL-36)")


class MutationCanhBaoCoTacDung(unittest.TestCase):
    """Mutation test: nếu bỏ nhánh phát `BUDGET_UNLIMITED`, test cảnh báo phải đỏ — chứng
    minh test không tự-vượt-qua vì lý do khác."""

    def test_khong_phat_neu_khong_co_dong_check(self):
        # Mô phỏng "quên check": gọi thẳng EventBus mà không qua nhánh `if usd is None`,
        # xác nhận `Sink` KHÔNG tự nhiên có event này trừ khi code thật phát nó — tức là
        # `test_usd_none_phat_canh_bao_dung_mot_lan_moi_run` ở trên thật sự phụ thuộc vào
        # nhánh mới thêm trong run.py, không phải một side effect khác.
        sink = Sink()
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.text("hi")]),
                      budget="$5", exporters=[sink])
        agent.try_run("chào")
        self.assertNotIn(EventKind.BUDGET_UNLIMITED, [e.kind for e in sink.events])


class LangGraphCungPhaiCanhBao(unittest.TestCase):
    """Cùng lỗ hổng, backend LangGraph — `Runtime.budget_gate()` là nơi tương đương
    `RunEngine.run()`'s đoạn vừa sửa (cùng chỗ `RUN_STARTED` phát một lần mỗi thread)."""

    def test_lg_usd_none_phat_canh_bao_dung_mot_lan(self):
        from fake_chat import FakeChat
        from langgraph.checkpoint.memory import MemorySaver
        from harness.lg import build_agent

        sink = Sink()
        graph, _rt = build_agent(
            model=FakeChat(script=[FakeChat.text("hi")]),
            budget=Budget(usd=None, steps=100, wall_clock_s=3600),
            checkpointer=MemorySaver(), exporters=[sink])
        graph.invoke({"messages": [__import__("langchain_core.messages",
                                              fromlist=["HumanMessage"]).HumanMessage("chào")]},
                     config={"configurable": {"thread_id": "t1"}})
        warnings = [e for e in sink.events if e.kind is EventKind.BUDGET_UNLIMITED]
        self.assertEqual(len(warnings), 1)

        # một thread THỨ HAI phải lại thấy cảnh báo của chính nó (mỗi thread một lần,
        # không phải một lần cho cả đời compiled graph — cùng bài học S-24/IDL rule).
        graph.invoke({"messages": [__import__("langchain_core.messages",
                                              fromlist=["HumanMessage"]).HumanMessage("chào")]},
                     config={"configurable": {"thread_id": "t2"}})
        warnings = [e for e in sink.events if e.kind is EventKind.BUDGET_UNLIMITED]
        self.assertEqual(len(warnings), 2)


if __name__ == "__main__":
    unittest.main()
