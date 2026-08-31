"""N-6 (design/07-risks-and-open-issues.md): `model.response` mang usage/latency_ms.

`docs/05 §1` đã hứa `model.response` mang `stop_reason`, usage (bốn trục), `cost_usd`,
`latency_ms` từ trước — event thật chỉ mang `stop_reason`/`cost_usd`. Không ai bắt được
vì `OtelExporter` (đọc mapping `docs/10 §2`) đã sẵn sàng đọc `input_tokens`/
`output_tokens`/`cache_read_tokens` từ payload này, nhưng payload chưa từng có chúng —
exporter im lặng nhận `None` mỗi lần, không phải một lỗi ai nhìn thấy.

Tên trường PHẲNG (`input_tokens`, `output_tokens`, `cache_read_tokens`,
`cache_write_tokens`), không phải `usage{...}` lồng — khớp đúng với những gì
`OtelExporter` đã đổi tên sẵn (`gen_ai.usage.input_tokens` v.v.), để không phải sửa cả
exporter lẫn schema cùng lúc cho một lời hứa tài liệu.
"""
import sys
import unittest

sys.path.insert(0, "src")
sys.path.insert(0, "tests")

from fake_chat import FakeChat
from langchain_core.messages import HumanMessage

from harness import Agent
from harness.lg import build_agent
from harness.models.fake import FakeModel
from harness.observe.events import EventKind
from harness.observe.otel import OtelExporter

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def _tracer():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


class Collector:
    def __init__(self):
        self.rows = []

    def emit(self, e):
        if e.kind is EventKind.MODEL_RESPONSE:
            self.rows.append(dict(e.data))

    def close(self):
        pass


class VongLapClassic(unittest.TestCase):
    def test_du_ca_bon_truc_usage_va_latency(self):
        col = Collector()
        Agent(name="A", job="j", provider=FakeModel([FakeModel.text("hi")]), budget="$5",
              exporters=[col]).try_run("thử")
        self.assertEqual(len(col.rows), 1)
        row = col.rows[0]
        self.assertEqual(row["input_tokens"], 100)
        self.assertEqual(row["output_tokens"], 20)
        self.assertEqual(row["cache_read_tokens"], 0)
        self.assertEqual(row["cache_write_tokens"], 0)
        self.assertIsInstance(row["latency_ms"], float)
        self.assertGreaterEqual(row["latency_ms"], 0.0)
        self.assertIn("stop_reason", row)
        self.assertIn("cost_usd", row)


class BackendGraph(unittest.TestCase):
    def test_du_ca_bon_truc_usage_va_latency(self):
        col = Collector()
        chat = FakeChat(script=[FakeChat.text("hi")], input_tokens=100, output_tokens=50)
        graph, _ = build_agent(model=chat, budget="$5", exporters=[col])
        graph.invoke({"messages": [HumanMessage("thử")], "step": 0})
        self.assertEqual(len(col.rows), 1)
        row = col.rows[0]
        self.assertEqual(row["input_tokens"], 100)
        self.assertEqual(row["output_tokens"], 50)
        self.assertEqual(row["cache_read_tokens"], 0)
        self.assertEqual(row["cache_write_tokens"], 0)
        self.assertIsInstance(row["latency_ms"], float)
        self.assertGreaterEqual(row["latency_ms"], 0.0)


class OtelDocDuocDuLieuThat(unittest.TestCase):
    """Trước bản vá, `OtelExporter` đã sẵn sàng đổi tên các trường này — chỉ là payload
    chưa từng mang chúng. Test này khoá đúng lời hứa `docs/10 §2`."""

    def test_span_gen_ai_chat_mang_usage_that(self):
        tracer, exp = _tracer()
        otel = OtelExporter(tracer)
        Agent(name="A", job="j", provider=FakeModel([FakeModel.text("hi")]), budget="$5",
              exporters=[otel]).try_run("thử")
        spans = [s for s in exp.get_finished_spans() if s.name == "gen_ai.chat"]
        self.assertEqual(len(spans), 1)
        attrs = spans[0].attributes
        self.assertEqual(attrs["gen_ai.usage.output_tokens"], 20)
        self.assertEqual(attrs["harness.cache_read_tokens"], 0)
        # Không nằm trong bảng đã đổi tên ở docs/10 §2, nhưng vẫn tới được span qua
        # nhánh fallback `harness.<key>` — không phải một trường bị âm thầm bỏ rơi.
        self.assertEqual(attrs["harness.cache_write_tokens"], 0)
        self.assertIn("harness.latency_ms", attrs)

    def test_ty_le_cache_hit_doc_duoc_input_tokens_that(self):
        """`OtelExporter.emit` tự tính `cache/input` cho `harness.cache.hit_ratio` — nó
        đọc `ev.data.get("input_tokens")`, thứ trước bản vá này luôn là `None`."""
        tracer, exp = _tracer()
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader
        reader = InMemoryMetricReader()
        meter = MeterProvider(metric_readers=[reader]).get_meter("test")
        otel = OtelExporter(tracer, meter)
        Agent(name="A", job="j", provider=FakeModel([FakeModel.text("hi")]), budget="$5",
              exporters=[otel]).try_run("thử")
        data = reader.get_metrics_data()
        names = {m.name for rm in data.resource_metrics for sm in rm.scope_metrics
                for m in sm.metrics}
        self.assertIn("harness.cache.hit_ratio", names)


if __name__ == "__main__":
    unittest.main()
