"""M6/T-6.2 — Y-01 (`docs/17-research-alignment.md`): `CancelledError` bị nuốt.

Đo gốc: `t.cancel(); await t` từng trả về `Result(stop_reason="cancelled")` thay vì
raise `CancelledError` — side effect không rò (đúng), nhưng phá giao thức huỷ của
asyncio: một `TaskGroup`/`asyncio.wait_for` bao ngoài không bao giờ thấy việc huỷ đã
xảy ra, vì `await` trên task đó trả về bình thường thay vì raise.

Sửa: `RunEngine.run()`'s `except asyncio.CancelledError:` giờ dọn dẹp (phát
`RUN_FINISHED(cancelled)` — vẫn còn bản ghi quan sát) rồi `raise` thay vì nuốt và trả
`Result`. `try_run()`/`run()` (đồng bộ, `agent.py`) không bắt gì thêm nên
`CancelledError` truyền thẳng lên `asyncio.run()`, ra ngoài lời gọi.
"""
import asyncio
import sys
import unittest

sys.path.insert(0, "src")

from harness import Agent, tool
from harness.models.base import ModelResponse
from harness.models.fake import FakeModel
from harness.observe.events import EventKind
from harness.result import StopReason


class SlowFakeModel(FakeModel):
    """Như `FakeModel`, nhưng `complete()` chờ một event trước khi trả lời — đủ thời
    gian để test `cancel()` một task đang thật sự `await` bên trong `RunEngine.run()`,
    đúng chỗ Y-01 mô tả."""

    def __init__(self, script, *, started: asyncio.Event, hold: asyncio.Event) -> None:
        super().__init__(script)
        self._started = started
        self._hold = hold

    async def complete(self, request, *, on_delta=None) -> ModelResponse:
        self._started.set()
        await self._hold.wait()
        return await super().complete(request, on_delta=on_delta)


class Sink:
    def __init__(self) -> None:
        self.events: list = []

    def emit(self, event) -> None:
        self.events.append(event)

    def close(self) -> None: ...


RAN: list = []


@tool(effect="write")
def save(x: int) -> str:
    """Lưu một giá trị."""
    RAN.append(x)
    return "saved"


class HuyKhongBiNuot(unittest.TestCase):
    def setUp(self):
        RAN.clear()

    def test_cancel_giua_run_raise_ra_ngoai_khong_tra_result(self):
        async def scenario():
            started = asyncio.Event()
            hold = asyncio.Event()
            sink = Sink()
            agent = Agent(name="A", job="j", model="claude-opus-5",
                          provider=SlowFakeModel([FakeModel.text("xong")],
                                                 started=started, hold=hold),
                          budget="$5", exporters=[sink])
            task = asyncio.ensure_future(agent.atry_run("chào"))
            await started.wait()          # chắc chắn đang ở giữa await complete()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError,
                                   msg="huỷ giữa run phải raise CancelledError ra ngoài, "
                                       "không được trả về Result(stop_reason='cancelled') "
                                       "một cách im lặng (đúng lỗi Y-01)"):
                await task
            cancelled_events = [e for e in sink.events if e.kind is EventKind.RUN_FINISHED
                               and e.data.get("stop_reason") == StopReason.CANCELLED.value]
            self.assertEqual(len(cancelled_events), 1,
                             "vẫn phải có một bản ghi quan sát run.finished(cancelled) — "
                             "raise không có nghĩa là bỏ luôn observability")

        asyncio.run(scenario())

    def test_huy_khong_de_tool_chay_tiep(self):
        """Đối chứng: side effect KHÔNG được rò khi huỷ — hành vi này đã đúng trước khi
        sửa (docs/17 tự ghi "hiện đã đúng"); sửa Y-01 không được làm hỏng nó."""
        async def scenario():
            started = asyncio.Event()
            hold = asyncio.Event()
            agent = Agent(name="A", job="j", model="claude-opus-5",
                          provider=SlowFakeModel(
                              [FakeModel.tool_call("save", {"x": 1}), FakeModel.text("xong")],
                              started=started, hold=hold),
                          tools=[save], budget="$5")
            task = asyncio.ensure_future(agent.atry_run("chào"))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(RAN, [], "tool write chạy dù run đã bị huỷ trước khi tới nó")

        asyncio.run(scenario())


class MutationRaiseCoTacDung(unittest.TestCase):
    def test_khong_raise_thi_test_chinh_do(self):
        """Mutation nhẹ: mô phỏng hành vi CŨ (nuốt CancelledError) bằng một provider tự
        raise rồi bắt nội bộ — xác nhận `assertRaises` ở test chính thật sự phụ thuộc
        vào việc `run.py` raise, không phải một side effect khác của asyncio."""
        async def scenario():
            async def just_returns():
                return "khong raise gi ca — mo phong hanh vi cu bi nuot"
            # Không cancel gì — task hoàn tất bình thường, không raise — chứng minh
            # assertRaises ở trên KHÔNG tự nhiên pass với một coroutine không bị huỷ.
            task = asyncio.ensure_future(just_returns())
            result = await task
            self.assertEqual(result, "khong raise gi ca — mo phong hanh vi cu bi nuot")

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
