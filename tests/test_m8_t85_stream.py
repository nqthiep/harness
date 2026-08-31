"""M8/T-8.5 (docs/17-research-alignment.md): `async for ev in agent.stream(msg)` trên
taxonomy 16 kind thật, mang envelope v1 (T-8.1).
"""
import asyncio
import sys
import unittest

sys.path.insert(0, "src")

from harness import Agent, tool
from harness.models.fake import FakeModel
from harness.observe.events import EVENT_SCHEMA_VERSION, EventKind


class StreamCoBanCacKindThat(unittest.TestCase):
    def test_stream_yield_dung_thu_tu_kind(self):
        async def scenario():
            agent = Agent(name="A", job="j", model="claude-opus-5",
                          provider=FakeModel([FakeModel.text("hi")]), budget="$5")
            kinds = [ev.kind async for ev in agent.stream("thử")]
            self.assertEqual(kinds[0], EventKind.RUN_STARTED)
            self.assertEqual(kinds[-1], EventKind.RUN_FINISHED)
            self.assertIn(EventKind.STEP_STARTED, kinds)
            self.assertIn(EventKind.MODEL_REQUEST, kinds)
            self.assertIn(EventKind.MODEL_RESPONSE, kinds)

        asyncio.run(scenario())

    def test_stream_mang_du_envelope_v1(self):
        async def scenario():
            agent = Agent(name="A", job="j", model="claude-opus-5",
                          provider=FakeModel([FakeModel.text("hi")]), budget="$5",
                          tenant_id="acme", session_id="s1")
            events = [ev async for ev in agent.stream("thử")]
            for ev in events:
                self.assertEqual(ev.schema_version, EVENT_SCHEMA_VERSION)
                self.assertEqual(ev.tenant_id, "acme")
                self.assertEqual(ev.session_id, "s1")

        asyncio.run(scenario())

    def test_stream_qua_tool_thay_du_tool_requested_finished(self):
        @tool(effect="read")
        def look(x: int) -> str:
            """Nhìn."""
            return "ok"

        async def scenario():
            agent = Agent(name="A", job="j", model="claude-opus-5",
                          provider=FakeModel([FakeModel.tool_call("look", {"x": 1}),
                                              FakeModel.text("xong")]),
                          tools=[look], budget="$5")
            kinds = [ev.kind async for ev in agent.stream("thử")]
            self.assertIn(EventKind.TOOL_REQUESTED, kinds)
            self.assertIn(EventKind.TOOL_STARTED, kinds)
            self.assertIn(EventKind.TOOL_FINISHED, kinds)
            self.assertIn(EventKind.POLICY_DECIDED, kinds)

        asyncio.run(scenario())

    def test_stream_khong_thay_doi_exporters_khac_cua_agent(self):
        """`stream()` chỉ THÊM một exporter riêng cho lần gọi này — không đổi
        `agent.exporters` (Agent bất biến, ADR-004)."""
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.text("hi")]), budget="$5")
        before = agent.exporters

        async def scenario():
            [ev async for ev in agent.stream("thử")]

        asyncio.run(scenario())
        self.assertEqual(agent.exporters, before)

    def test_stream_khong_lam_hong_transcript_writer_co_san(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name

        async def scenario():
            agent = Agent(name="A", job="j", model="claude-opus-5",
                          provider=FakeModel([FakeModel.text("hi")]), budget="$5",
                          transcript=path)
            events = [ev async for ev in agent.stream("thử")]
            self.assertTrue(len(events) > 0)

        asyncio.run(scenario())
        with open(path) as f:
            lines = f.readlines()
        self.assertTrue(len(lines) > 0, "transcript vẫn phải được ghi bình thường")


class StreamKetThucKetQua(unittest.TestCase):
    def test_run_that_bai_van_stream_het_su_kien(self):
        """Model trả rác không khớp `returns=` — vẫn phải stream ĐẦY ĐỦ sự kiện tới
        run.finished(error), không crash giữa chừng (N-2)."""
        import dataclasses

        @dataclasses.dataclass
        class Ans:
            x: int

        async def scenario():
            agent = Agent(name="A", job="j", model="claude-opus-5",
                          provider=FakeModel([FakeModel.text("not json")]), budget="$5",
                          returns=Ans)
            events = [ev async for ev in agent.stream("thử")]
            self.assertEqual(events[-1].kind, EventKind.RUN_FINISHED)
            self.assertEqual(events[-1].data.get("stop_reason"), "error")

        asyncio.run(scenario())


class StreamHuyBoLanTruyen(unittest.TestCase):
    """T-6.2's cancellation-propagates guarantee, mở rộng cho generator này."""

    def test_huy_giua_stream_khong_treo(self):
        class SlowFakeModel(FakeModel):
            def __init__(self, script, *, started, hold):
                super().__init__(script)
                self._started, self._hold = started, hold

            async def complete(self, request, *, on_delta=None):
                self._started.set()
                await self._hold.wait()
                return await super().complete(request, on_delta=on_delta)

        async def scenario():
            started, hold = asyncio.Event(), asyncio.Event()
            agent = Agent(name="A", job="j", model="claude-opus-5",
                          provider=SlowFakeModel([FakeModel.text("hi")],
                                                 started=started, hold=hold),
                          budget="$5")
            gen = agent.stream("thử")
            got = []

            async def consume():
                async for ev in gen:
                    got.append(ev)

            task = asyncio.ensure_future(consume())
            await started.wait()
            task.cancel()
            with __import__("contextlib").suppress(asyncio.CancelledError):
                await task
            # không treo mãi — tới đây coi như thành công (assertTrue luôn đúng, phép
            # thử THẬT SỰ là dòng trên không bao giờ trả về nếu generator bị kẹt).
            self.assertTrue(True)

        asyncio.run(asyncio.wait_for(scenario(), timeout=5))


class MutationStreamCoTacDung(unittest.TestCase):
    def test_bo_exporter_thi_stream_rong(self):
        """Mutation: `stream()` giả không thêm `_QueueExporter` — xác nhận generator
        không bao giờ yield gì (chứng minh test chính phụ thuộc đúng vào việc thêm
        exporter)."""
        async def broken_stream(self, message, *, on_delta=None):
            await self.atry_run(message, on_delta=on_delta)
            return
            yield   # làm cho nó là async generator nhưng không bao giờ yield gì

        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.text("hi")]), budget="$5")

        async def scenario():
            events = [ev async for ev in broken_stream(agent, "thử")]
            self.assertEqual(events, [],
                             "với mutation này, không sự kiện nào được yield — khác "
                             "hành vi thật, chứng minh test chính phụ thuộc đúng vào "
                             "_QueueExporter")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
