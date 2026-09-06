"""M8/T-8.3: OTel exporter — kiểm ĐÚNG bảng mapping đã công bố sẵn ở
`docs/10-observability-ops.md §2` (đọc trước khi viết code, không phải bịa một mapping
riêng) bằng SDK OTel THẬT (`InMemorySpanExporter`/`InMemoryMetricReader`), không mock.
"""
import sys
import unittest

import _paths

from harness import Agent, tool
from harness.models.fake import FakeModel
from harness.observe.otel import OtelExporter

from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def _tracer():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


def _meter():
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    return provider.get_meter("test"), reader


def _metric_points(reader, name):
    data = reader.get_metrics_data()
    if data is None:
        return []
    points = []
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == name:
                    points.extend(m.data.data_points)
    return points


class SpanNamesDungBangDaCongBo(unittest.TestCase):
    def test_run_step_model_span_dung_ten(self):
        tracer, exporter = _tracer()
        otel = OtelExporter(tracer)
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.text("hi")]), budget="$5",
                      exporters=[otel])
        agent.try_run("thử")
        names = [s.name for s in exporter.get_finished_spans()]
        self.assertIn("harness.run", names)
        self.assertIn("harness.step", names)
        self.assertIn("gen_ai.chat", names)

    def test_tool_span_dung_ten(self):
        @tool(effect="read")
        def look(x: int) -> str:
            """Nhìn."""
            return "ok"

        tracer, exporter = _tracer()
        otel = OtelExporter(tracer)
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.tool_call("look", {"x": 1}),
                                          FakeModel.text("xong")]),
                      tools=[look], budget="$5", exporters=[otel])
        agent.try_run("thử")
        spans = {s.name: s for s in exporter.get_finished_spans()}
        self.assertIn("harness.tool", spans)
        self.assertEqual(spans["harness.tool"].attributes["harness.tool.name"], "look")

    def test_moi_span_deu_dong(self):
        tracer, exporter = _tracer()
        otel = OtelExporter(tracer)
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.text("hi")]), budget="$5",
                      exporters=[otel])
        agent.try_run("thử")
        for s in exporter.get_finished_spans():
            self.assertIsNotNone(s.end_time, f"span {s.name!r} không được đóng")

    def test_tool_span_la_con_cua_step_span(self):
        @tool(effect="read")
        def look(x: int) -> str:
            """Nhìn."""
            return "ok"

        tracer, exporter = _tracer()
        otel = OtelExporter(tracer)
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.tool_call("look", {"x": 1}),
                                          FakeModel.text("xong")]),
                      tools=[look], budget="$5", exporters=[otel])
        agent.try_run("thử")
        spans = {s.name: [] for s in exporter.get_finished_spans()}
        for s in exporter.get_finished_spans():
            spans.setdefault(s.name, []).append(s)
        step_span = spans["harness.step"][0]
        tool_span = spans["harness.tool"][0]
        self.assertEqual(tool_span.parent.span_id, step_span.context.span_id)


class AttributeDungTenGenAI(unittest.TestCase):
    def test_run_span_co_gen_ai_agent_name_va_model(self):
        tracer, exporter = _tracer()
        otel = OtelExporter(tracer)
        agent = Agent(name="TroLy", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.text("hi")]), budget="$5",
                      exporters=[otel])
        agent.try_run("thử")
        run_span = next(s for s in exporter.get_finished_spans() if s.name == "harness.run")
        self.assertEqual(run_span.attributes["gen_ai.agent.name"], "TroLy")
        self.assertEqual(run_span.attributes["gen_ai.request.model"], "claude-opus-5")

    def test_message_khong_bao_gio_xuat_hien_khi_include_content_false(self):
        """docs/10 §2: nội dung không bao giờ export mặc định — kể cả khi `data` có
        khoá `message` (RUN_STARTED mang nó ở backend cổ điển)."""
        tracer, exporter = _tracer()
        otel = OtelExporter(tracer)
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.text("hi")]), budget="$5",
                      exporters=[otel])
        agent.try_run("một câu hỏi tuyệt mật")
        run_span = next(s for s in exporter.get_finished_spans() if s.name == "harness.run")
        self.assertNotIn("harness.message", run_span.attributes)
        for s in exporter.get_finished_spans():
            for v in s.attributes.values():
                if isinstance(v, str):
                    self.assertNotIn("một câu hỏi tuyệt mật", v)

    def test_include_content_true_cho_qua_neu_that_su_co_du_lieu(self):
        """Đối chứng: `include_content=True` KHÔNG lọc — nhưng vẫn không có gì để lọ vì
        event hôm nay không mang nội dung thật (chính N-6 tìm thấy khi viết module
        này). Test khẳng định tham số hoạt động đúng theo nghĩa "không tự chặn" khi bật,
        không khẳng định nó tạo ra dữ liệu không tồn tại."""
        tracer, exporter = _tracer()
        otel = OtelExporter(tracer, include_content=True)
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.text("hi")]), budget="$5",
                      exporters=[otel])
        agent.try_run("một câu hỏi")
        run_span = next(s for s in exporter.get_finished_spans() if s.name == "harness.run")
        self.assertEqual(run_span.attributes.get("harness.message"), "một câu hỏi")


class PolicyDecidedTrenToolSpan(unittest.TestCase):
    def test_policy_decided_la_span_event_tren_tool_khong_phai_step(self):
        @tool(effect="read")
        def look(x: int) -> str:
            """Nhìn."""
            return "ok"

        tracer, exporter = _tracer()
        otel = OtelExporter(tracer)
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.tool_call("look", {"x": 1}),
                                          FakeModel.text("xong")]),
                      tools=[look], budget="$5", exporters=[otel])
        agent.try_run("thử")
        spans_by_name: dict = {}
        for s in exporter.get_finished_spans():
            spans_by_name.setdefault(s.name, []).append(s)
        tool_span = spans_by_name["harness.tool"][0]
        step_span = spans_by_name["harness.step"][0]
        self.assertIn("policy.decided", [e.name for e in tool_span.events],
                      "docs/10 §2: policy.decided phải là span event TRÊN TOOL SPAN")
        self.assertNotIn("policy.decided", [e.name for e in step_span.events])


class MetricThat(unittest.TestCase):
    def test_harness_run_cost_histogram(self):
        tracer, _ = _tracer()
        meter, reader = _meter()
        otel = OtelExporter(tracer, meter)
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.text("hi")]), budget="$5",
                      exporters=[otel])
        agent.try_run("thử")
        points = _metric_points(reader, "harness.run.cost")
        self.assertTrue(len(points) >= 1, "phải có ít nhất một điểm dữ liệu cost")

    def test_harness_run_steps_histogram(self):
        tracer, _ = _tracer()
        meter, reader = _meter()
        otel = OtelExporter(tracer, meter)
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.text("hi")]), budget="$5",
                      exporters=[otel])
        agent.try_run("thử")
        points = _metric_points(reader, "harness.run.steps")
        self.assertTrue(len(points) >= 1)

    def test_harness_tool_duration_histogram(self):
        @tool(effect="read")
        def look(x: int) -> str:
            """Nhìn."""
            return "ok"

        tracer, _ = _tracer()
        meter, reader = _meter()
        otel = OtelExporter(tracer, meter)
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.tool_call("look", {"x": 1}),
                                          FakeModel.text("xong")]),
                      tools=[look], budget="$5", exporters=[otel])
        agent.try_run("thử")
        points = _metric_points(reader, "harness.tool.duration")
        self.assertTrue(len(points) >= 1)

    def test_harness_policy_denials_counter(self):
        @tool(effect="danger")
        def xoa(x: int) -> str:
            """Xoá, không hoàn tác."""
            return "đã xoá"

        tracer, _ = _tracer()
        meter, reader = _meter()
        otel = OtelExporter(tracer, meter)
        # Không approve= và effect=danger -> tự động DENY (fail closed, docs/06).
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.tool_call("xoa", {"x": 1}),
                                          FakeModel.text("xong")]),
                      tools=[xoa], budget="$5", exporters=[otel])
        agent.try_run("thử")
        points = _metric_points(reader, "harness.policy.denials")
        self.assertTrue(len(points) >= 1, "danger không approve= phải tự DENY, tăng counter")


class KhongCoTracerMeterTuXay(unittest.TestCase):
    def test_khong_truyen_gi_van_dung_duoc(self):
        otel = OtelExporter()
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.text("hi")]), budget="$5",
                      exporters=[otel])
        r = agent.try_run("thử")
        self.assertTrue(r.ok)


class ImportKhongEagerOtel(unittest.TestCase):
    def test_import_harness_khong_keo_theo_opentelemetry(self):
        import subprocess
        code = (f"import sys; sys.path.insert(0, {str(_paths.SRC)!r}); import harness; "
                "print('opentelemetry' in sys.modules)")
        rc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(rc.stdout.strip(), "False", rc.stderr)


class LangGraphOTel(unittest.TestCase):
    def test_lg_run_tao_span_dung_ten(self):
        from fake_chat import FakeChat
        from langgraph.checkpoint.memory import MemorySaver
        from harness.lg import build_agent

        tracer, exporter = _tracer()
        otel = OtelExporter(tracer)
        graph, _rt = build_agent(
            model=FakeChat(script=[FakeChat.text("hi")]), budget="$5",
            checkpointer=MemorySaver(), exporters=[otel])
        from langchain_core.messages import HumanMessage
        graph.invoke({"messages": [HumanMessage("thử")]},
                     config={"configurable": {"thread_id": "t1"}})
        names = [s.name for s in exporter.get_finished_spans()]
        self.assertIn("harness.run", names)


class MutationOTelCoTacDung(unittest.TestCase):
    def test_bo_rename_thi_test_attribute_do(self):
        """Mutation: `_start` cho span run KHÔNG rename `agent`/`model` — xác nhận test
        `gen_ai.agent.name` thật sự phụ thuộc vào nhánh rename đó."""
        import harness.observe.otel as otel_mod
        original = otel_mod.OtelExporter.emit

        def broken_emit(self, ev):
            if ev.kind is otel_mod.EventKind.RUN_STARTED:
                self._root[ev.run_id] = self._start("harness.run", ev, None)  # KHÔNG rename
            else:
                original(self, ev)

        otel_mod.OtelExporter.emit = broken_emit
        try:
            tracer, exporter = _tracer()
            otel = OtelExporter(tracer)
            agent = Agent(name="TroLy", job="j", model="claude-opus-5",
                          provider=FakeModel([FakeModel.text("hi")]), budget="$5",
                          exporters=[otel])
            agent.try_run("thử")
            run_span = next(s for s in exporter.get_finished_spans() if s.name == "harness.run")
            self.assertNotIn("gen_ai.agent.name", run_span.attributes,
                            "với mutation này, gen_ai.agent.name KHÔNG có mặt — khác "
                            "hành vi thật, chứng minh test phụ thuộc đúng vào rename")
        finally:
            otel_mod.OtelExporter.emit = original


if __name__ == "__main__":
    unittest.main()
