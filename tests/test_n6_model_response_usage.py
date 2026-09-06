"""N-6 (design/07-risks-and-open-issues.md): `OtelExporter` thật sự đọc được usage.

`model.response`/`run.finished` mang usage/latency_ms/duration_s đã được khoá ở
`tests/test_n5_n6_retry_and_usage.py` (cả hai backend). File này còn lại đúng phần chưa
ai kiểm: trước bản vá N-6, `OtelExporter` (đọc mapping `docs/10 §2`) đã sẵn sàng đổi tên
`input_tokens`/`output_tokens`/`cache_read_tokens` từ payload này, nhưng payload chưa
từng mang chúng — exporter im lặng nhận `None` mỗi lần, không phải một lỗi ai nhìn thấy.
"""
import unittest

from harness import Agent
from harness.models.fake import FakeModel
from harness.observe.otel import OtelExporter

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def _tracer():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


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
        self.assertEqual(attrs["harness.cache_creation_tokens"], 0)
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
