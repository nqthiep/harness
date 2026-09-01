"""`TaskLedger` — sổ công việc bền cho phiên chạy dài (`src/harness/tasks.py`).

Bốn nhóm ở đây tương ứng bốn điều module đó tự hứa trong docstring của nó, nên nếu một
lời hứa hỏng thì test nói ra hứa nào hỏng:

1. Sổ đếm được — trạng thái là từ vựng đóng, id bịa bị từ chối.
2. Sổ BỀN qua restart tiến trình (`SqliteStore`) — thứ mà checkpoint của graph không cho.
3. Sổ chạy được từ phía model, trên `Agent` thật, qua đúng đường tool.
4. Giả định an toàn mà module dựa vào (`write` không parallel-safe) được canh bằng test,
   không phải bằng một câu trong docstring.
"""
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, "src")
sys.path.insert(0, "tests")

from harness import Agent, Effect
from harness.errors import HarnessError
from harness.memory import InMemoryStore, SqliteStore
from harness.models.fake import FakeModel
from harness.tasks import STATUSES, TaskLedger, UnknownStatusError, UnknownTaskError
from harness.tools import EFFECT_PROFILES


class SoDemDuoc(unittest.TestCase):
    def setUp(self):
        self.ledger = TaskLedger(InMemoryStore())

    def test_so_trong_noi_ro_la_trong(self):
        self.assertEqual(asyncio.run(self.ledger.summary()), "(sổ công việc trống)")

    def test_them_roi_hoan_thanh_thi_dem_dung(self):
        async def go():
            await self.ledger.add("viết test")
            await self.ledger.add("chạy test")
            await self.ledger.set_status("t1", "done", "xanh")
            return await self.ledger.summary()

        s = asyncio.run(go())
        self.assertTrue(s.startswith("1/2 xong"), s)
        self.assertIn("[x] t1: viết test  — xanh", s)
        self.assertIn("[ ] t2: chạy test", s)

    def test_moi_trang_thai_co_dau_rieng(self):
        async def go():
            for i, st in enumerate(STATUSES):
                await self.ledger.add(f"việc {i}")
                await self.ledger.set_status(f"t{i + 1}", st, "vì sao")
            return await self.ledger.all()

        marks = {t.status: t.line()[:3] for t in asyncio.run(go())}
        self.assertEqual(len(set(marks.values())), len(STATUSES), marks)

    def test_trang_thai_ngoai_tu_vung_bi_tu_choi(self):
        async def go():
            await self.ledger.add("việc")
            await self.ledger.set_status("t1", "xong")

        with self.assertRaises(UnknownStatusError) as e:
            asyncio.run(go())
        # Thông điệp phải liệt kê lựa chọn hợp lệ — model đọc lỗi này để tự sửa.
        for st in STATUSES:
            self.assertIn(st, str(e.exception))

    def test_id_bia_bi_tu_choi_chu_khong_am_tham_tao_moi(self):
        async def go():
            await self.ledger.add("việc thật")
            await self.ledger.set_status("t42", "done")

        with self.assertRaises(UnknownTaskError):
            asyncio.run(go())
        self.assertEqual(len(asyncio.run(self.ledger.all())), 1)

    def test_ghi_chu_cu_khong_bi_xoa_khi_doi_trang_thai_khong_kem_ghi_chu(self):
        async def go():
            await self.ledger.add("việc")
            await self.ledger.set_status("t1", "blocked", "thiếu quyền")
            await self.ledger.set_status("t1", "doing")
            return (await self.ledger.all())[0]

        self.assertEqual(asyncio.run(go()).note, "thiếu quyền")

    def test_so_hong_bao_loi_chu_khong_bat_dau_lai_so_trang(self):
        store = InMemoryStore()

        async def go():
            await store.put("harness:tasks", "{ đây không phải JSON")
            await TaskLedger(store).all()

        with self.assertRaises(HarnessError) as e:
            asyncio.run(go())
        self.assertIn("không đọc được", str(e.exception))


class BenQuaRestart(unittest.TestCase):
    def test_so_song_sot_qua_mot_tien_trinh_moi(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "tasks.db")

            async def phien_mot():
                store = SqliteStore(path)
                await TaskLedger(store).add("việc bắt đầu ở phiên 1")
                await TaskLedger(store).set_status("t1", "doing")
                await store.close()

            async def phien_hai():
                store = SqliteStore(path)
                s = await TaskLedger(store).summary()
                await store.close()
                return s

            asyncio.run(phien_mot())
            self.assertIn("[~] t1: việc bắt đầu ở phiên 1", asyncio.run(phien_hai()))

    def test_hai_so_khac_key_khong_dam_vao_nhau(self):
        store = InMemoryStore()

        async def go():
            await TaskLedger(store, key="a").add("việc của A")
            await TaskLedger(store, key="b").add("việc của B")
            return (await TaskLedger(store, key="a").summary(),
                    await TaskLedger(store, key="b").summary())

        a, b = asyncio.run(go())
        self.assertIn("việc của A", a)
        self.assertNotIn("việc của B", a)
        self.assertIn("việc của B", b)


class ModelDungDuocQuaToolThat(unittest.TestCase):
    """Đường đi thật: `Agent` → tool → `Store`. Không có API key, không có mạng."""

    def test_agent_ghi_duoc_vao_so_va_so_con_lai_sau_khi_run_xong(self):
        store = InMemoryStore()
        ledger = TaskLedger(store)
        agent = Agent(
            name="Coder", job="Làm việc theo danh sách.", budget="$1, 30 steps",
            tools=ledger.tools(),
            provider=FakeModel([
                FakeModel.tool_call("add_task", {"title": "sửa bug"}),
                FakeModel.tool_call("start_task", {"task_id": "t1"}),
                FakeModel.tool_call("finish_task", {"task_id": "t1", "note": "test xanh"}),
                FakeModel.tool_call("list_tasks", {}),
                FakeModel.text("xong"),
            ]),
        )
        result = agent.try_run("làm đi")
        self.assertTrue(result.ok, result.text)
        self.assertEqual(result.tools_run,
                         ("add_task", "start_task", "finish_task", "list_tasks"))
        # Điểm mấu chốt: tiến độ nằm trong `Store`, không nằm trong lịch sử hội thoại —
        # nên nén context không làm mất nó.
        self.assertIn("1/1 xong", asyncio.run(ledger.summary()))
        self.assertIn("test xanh", asyncio.run(ledger.summary()))

    def test_effect_da_phan_loai_san_dung_cho_tung_tool(self):
        tools = TaskLedger(InMemoryStore()).tools()
        by_name = {t.name: t.effect for t in tools}
        self.assertEqual(by_name["list_tasks"], Effect.READ)
        for name in ("add_task", "start_task", "finish_task", "block_task"):
            self.assertEqual(by_name[name], Effect.WRITE, name)
        # Không tool nào là `danger`: ghi một dòng vào sổ của chính mình hoàn tác được.
        self.assertNotIn(Effect.DANGER, set(by_name.values()))


class GiaDinhAnToanDuocCanh(unittest.TestCase):
    def test_write_khong_parallel_safe_nen_read_modify_write_an_toan(self):
        """Module `tasks.py` dựa vào đúng điều này để không cần khoá riêng. Nếu ai đó đổi
        `write` thành parallel-safe, test này đỏ trước khi sổ công việc âm thầm mất ghi."""
        self.assertFalse(EFFECT_PROFILES[Effect.WRITE].parallel_safe)


if __name__ == "__main__":
    unittest.main()
