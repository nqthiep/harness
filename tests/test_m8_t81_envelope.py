"""M8/T-8.1 (docs/17-research-alignment.md): envelope v1 — `schema_version`, `trace_id`,
`tenant_id`, `session_id` vào `Event`.

"Có version thì mới đổi được mà không phá exporter" — test đúng nghĩa: mọi field mới có
mặt, có giá trị hợp lý mặc định, và construction `Event(...)` KIỂU CŨ (positional, không
biết 4 field mới) vẫn chạy được (tương thích ngược).
"""
import sys
import unittest

sys.path.insert(0, "src")

from harness import Agent
from harness.models.fake import FakeModel
from harness.observe.events import EVENT_SCHEMA_VERSION, Event, EventBus, EventKind


class Sink:
    def __init__(self) -> None:
        self.events: list = []

    def emit(self, event) -> None:
        self.events.append(event)

    def close(self) -> None: ...


class EventCoDuTruong(unittest.TestCase):
    def test_construction_kieu_cu_van_chay_duoc(self):
        """Tương thích ngược — `Event(seq, ts, run_id, kind, step, data)` không biết 4
        trường mới vẫn dựng được, nhận default hợp lý."""
        ev = Event(0, 0.0, "r1", EventKind.RUN_STARTED, None, {})
        self.assertEqual(ev.schema_version, EVENT_SCHEMA_VERSION)
        self.assertIsNone(ev.trace_id)
        self.assertIsNone(ev.tenant_id)
        self.assertIsNone(ev.session_id)

    def test_eventbus_tu_dien_schema_version(self):
        bus = EventBus("r1")
        ev = bus.emit(EventKind.RUN_STARTED)
        self.assertEqual(ev.schema_version, EVENT_SCHEMA_VERSION)

    def test_trace_id_mac_dinh_la_run_id(self):
        bus = EventBus("r_abc123")
        ev = bus.emit(EventKind.RUN_STARTED)
        self.assertEqual(ev.trace_id, "r_abc123")

    def test_trace_id_tuong_minh_ghi_de_duoc(self):
        bus = EventBus("r_abc123", trace_id="t_rieng")
        ev = bus.emit(EventKind.RUN_STARTED)
        self.assertEqual(ev.trace_id, "t_rieng")

    def test_tenant_va_session_id_mac_dinh_none(self):
        bus = EventBus("r1")
        ev = bus.emit(EventKind.RUN_STARTED)
        self.assertIsNone(ev.tenant_id)
        self.assertIsNone(ev.session_id)

    def test_tenant_va_session_id_duoc_truyen_qua(self):
        bus = EventBus("r1", tenant_id="acme-corp", session_id="sess-42")
        ev = bus.emit(EventKind.RUN_STARTED)
        self.assertEqual(ev.tenant_id, "acme-corp")
        self.assertEqual(ev.session_id, "sess-42")

    def test_error_raised_tu_exporter_chet_cung_co_du_truong(self):
        """Event `error.raised` phát khi một exporter tự chết (dòng code cũ thứ hai xây
        `Event` trực tiếp) phải cũng có đủ envelope — không phải một class Event khác."""
        class ExporterChet:
            def emit(self, event): raise RuntimeError("boom")
            def close(self): ...

        bus = EventBus("r1", [ExporterChet()], tenant_id="acme")
        bus.emit(EventKind.RUN_STARTED)
        error_events = [e for e in bus.events if e.kind is EventKind.ERROR_RAISED]
        self.assertEqual(len(error_events), 1)
        self.assertEqual(error_events[0].tenant_id, "acme")
        self.assertEqual(error_events[0].schema_version, EVENT_SCHEMA_VERSION)


class AgentThatXuyenQuaAgent(unittest.TestCase):
    def test_agent_tenant_session_id_toi_event_that(self):
        sink = Sink()
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.text("hi")]), budget="$5",
                      exporters=[sink], tenant_id="acme-corp", session_id="sess-1")
        agent.try_run("thử")
        run_started = [e for e in sink.events if e.kind is EventKind.RUN_STARTED]
        self.assertEqual(len(run_started), 1)
        self.assertEqual(run_started[0].tenant_id, "acme-corp")
        self.assertEqual(run_started[0].session_id, "sess-1")

    def test_khong_truyen_thi_tenant_session_none(self):
        sink = Sink()
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.text("hi")]), budget="$5",
                      exporters=[sink])
        agent.try_run("thử")
        run_started = [e for e in sink.events if e.kind is EventKind.RUN_STARTED][0]
        self.assertIsNone(run_started.tenant_id)
        self.assertIsNone(run_started.session_id)

    def test_with_giu_lai_tenant_session_id(self):
        agent = Agent(name="A", job="j", tenant_id="acme", session_id="s1")
        agent2 = agent.with_(name="B")
        self.assertEqual(agent2.tenant_id, "acme")
        self.assertEqual(agent2.session_id, "s1")


class LangGraphThatXuyenQua(unittest.TestCase):
    def test_lg_tenant_id_toi_event_that(self):
        from fake_chat import FakeChat
        from langgraph.checkpoint.memory import MemorySaver
        from harness.lg import build_agent

        sink = Sink()
        graph, _rt = build_agent(
            model=FakeChat(script=[FakeChat.text("hi")]), budget="$5",
            checkpointer=MemorySaver(), exporters=[sink], tenant_id="acme-lg")
        from langchain_core.messages import HumanMessage
        graph.invoke({"messages": [HumanMessage("thử")]},
                     config={"configurable": {"thread_id": "t1"}})
        run_started = [e for e in sink.events if e.kind is EventKind.RUN_STARTED][0]
        self.assertEqual(run_started.tenant_id, "acme-lg")
        self.assertEqual(run_started.session_id, "t1",
                         "session_id trên LangGraph backend phải là thread_id")


class MutationEnvelopeCoTacDung(unittest.TestCase):
    def test_bo_truyen_tenant_id_thi_test_do(self):
        """Mutation: `EventBus` giả bỏ qua `tenant_id`/`session_id` hoàn toàn — xác nhận
        test chính phụ thuộc đúng vào việc chúng được lưu và trả lại."""
        class FakeBus(EventBus):
            def __init__(self, run_id, exporters=(), **kw):
                super().__init__(run_id, exporters)  # bỏ hết kw — mô phỏng thiếu T-8.1

        bus = FakeBus("r1", tenant_id="acme", session_id="s1")
        ev = bus.emit(EventKind.RUN_STARTED)
        self.assertIsNone(ev.tenant_id, "với mutation này, tenant_id bị mất — khác "
                                        "hành vi thật, chứng minh test phụ thuộc đúng")


if __name__ == "__main__":
    unittest.main()
