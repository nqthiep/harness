"""M8/T-8.6 (docs/17-research-alignment.md): `Session` — id, ownership, TTL, fork,
resume, ranh giới đồng thời. Chỉ bao lấy `Chat` (backend cổ điển) — không xây lại state
isolation Round 37 đã sửa, chỉ ĐẶT TÊN cho thứ đã tồn tại ngầm.
"""
import threading
import time
import unittest

from harness import Agent
from harness.models.fake import FakeModel
from harness.session import Session, SessionExpiredError


def _agent(script):
    return Agent(name="A", job="j", model="claude-opus-5",
                provider=FakeModel(script), budget="$50")


class SessionCoBanIdOwnerTTL(unittest.TestCase):
    def test_moi_session_co_id_rieng(self):
        a1 = _agent([FakeModel.text("hi")])
        a2 = _agent([FakeModel.text("hi")])
        s1, s2 = Session(a1), Session(a2)
        self.assertNotEqual(s1.id, s2.id)
        self.assertTrue(s1.id.startswith("sess_"))

    def test_id_truyen_vao_duoc_giu_nguyen(self):
        a = _agent([FakeModel.text("hi")])
        s = Session(a, id="sess_co_dinh")
        self.assertEqual(s.id, "sess_co_dinh")

    def test_owner_luu_lai(self):
        a = _agent([FakeModel.text("hi")])
        s = Session(a, owner="user-42")
        self.assertEqual(s.owner, "user-42")

    def test_khong_ttl_thi_khong_bao_gio_het_han(self):
        a = _agent([FakeModel.text("hi")])
        s = Session(a)
        self.assertIsNone(s.expires_at)
        self.assertFalse(s.expired())

    def test_ttl_het_han_dung_luc(self):
        a = _agent([FakeModel.text("hi")])
        s = Session(a, ttl_s=1.0)
        self.assertFalse(s.expired(now=s.created_at + 0.5))
        self.assertTrue(s.expired(now=s.created_at + 1.5))

    def test_say_sau_khi_het_han_raise(self):
        a = _agent([FakeModel.text("hi")])
        s = Session(a, ttl_s=0.01)
        time.sleep(0.05)
        with self.assertRaises(SessionExpiredError):
            s.say("thử")


class SessionSay(unittest.TestCase):
    def test_say_chuyen_tiep_toi_chat_va_giu_lich_su(self):
        a = _agent([FakeModel.text("chào bạn"), FakeModel.text("tôi khoẻ")])
        s = Session(a)
        r1 = s.say("chào")
        self.assertTrue(r1.ok)
        self.assertEqual(len(s.messages), 2)   # user + assistant
        r2 = s.say("bạn khoẻ không")
        self.assertTrue(r2.ok)
        self.assertEqual(len(s.messages), 4)


class SessionFork(unittest.TestCase):
    def test_fork_doc_lap_lich_su(self):
        a = _agent([FakeModel.text("chào"), FakeModel.text("gốc"), FakeModel.text("nhánh")])
        s = Session(a)
        s.say("hi")
        f = s.fork()
        self.assertNotEqual(f.id, s.id)
        self.assertEqual(f.messages, s.messages)   # bắt đầu giống hệt
        f.say("tiếp trên nhánh")
        s.say("tiếp trên gốc")
        self.assertNotEqual(f.messages, s.messages,
                            "sau khi say() riêng, hai session phải khác lịch sử")

    def test_fork_giu_owner_neu_khong_ghi_de(self):
        a = _agent([FakeModel.text("hi")])
        s = Session(a, owner="user-1")
        f = s.fork()
        self.assertEqual(f.owner, "user-1")

    def test_fork_ghi_de_owner_duoc(self):
        a = _agent([FakeModel.text("hi")])
        s = Session(a, owner="user-1")
        f = s.fork(owner="user-2")
        self.assertEqual(f.owner, "user-2")


class SessionResumeFrom(unittest.TestCase):
    def test_resume_from_tra_ve_session_dung_tiep_duoc(self):
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name

        a = Agent(name="A", job="j", model="claude-opus-5",
                 provider=FakeModel([FakeModel.text("xong")]), budget="$5",
                 transcript=path)
        a.try_run("việc cần resume")
        session = Session.resume_from(a, path, owner="user-9")
        self.assertEqual(session.owner, "user-9")
        self.assertTrue(len(session.messages) > 0)


class SessionRanhGioiDongThoi(unittest.TestCase):
    def test_hai_thread_goi_say_dong_thoi_khong_dua_lich_su(self):
        calls = []

        class SlowFakeModel(FakeModel):
            async def complete(self, request, *, on_delta=None):
                calls.append(1)
                import asyncio
                await asyncio.sleep(0.01)
                return await super().complete(request, on_delta=on_delta)

        a = Agent(name="A", job="j", model="claude-opus-5",
                 provider=SlowFakeModel([FakeModel.text("a"), FakeModel.text("b")]),
                 budget="$50")
        s = Session(a)
        results = []

        def worker(msg):
            results.append(s.say(msg))

        t1 = threading.Thread(target=worker, args=("một",))
        t2 = threading.Thread(target=worker, args=("hai",))
        t1.start(); t2.start()
        t1.join(); t2.join()
        # Với khoá đúng, 2 lượt say() phải nối tiếp — 4 message cuối cùng (2 user + 2
        # assistant), không phải lịch sử bị đua/ghi đè mất một lượt.
        self.assertEqual(len(s.messages), 4,
                         "hai say() đồng thời phải nối tiếp nhau, không đua nhau ghi "
                         "đè _messages — đúng \"ranh giới đồng thời\" T-8.6 đòi")


class MutationSessionCoTacDung(unittest.TestCase):
    def test_bo_lock_thi_dua_that_su_xay_ra_tat_dinh(self):
        """Mutation ĐỊNH ĐOẠT (không dựa vào timing may rủi): hai luồng đọc
        `_messages` (rỗng) TRƯỚC KHI cái nào ghi lại — ép bằng hai `threading.Event`
        mà test tự điều khiển thời điểm mở khoá provider. Không có khoá: kết quả cuối
        chỉ còn 2 message (của luồng ghi sau), không phải 4 — bằng chứng race tất định,
        không phải xác suất. CÓ khoá (`Session` thật): không thể xảy ra vì luồng thứ
        hai phải đợi luồng thứ nhất ghi lại xong mới được đọc."""
        import threading as _threading

        release_a = _threading.Event()
        release_b = _threading.Event()
        started_a = _threading.Event()
        started_b = _threading.Event()

        class ControlledModel(FakeModel):
            def __init__(self, script, tag):
                super().__init__(script)
                self._tag = tag

            async def complete(self, request, *, on_delta=None):
                if self._tag == "a":
                    started_a.set()
                    release_a.wait(timeout=5)
                else:
                    started_b.set()
                    release_b.wait(timeout=5)
                return await super().complete(request, on_delta=on_delta)

        agent_a = Agent(name="A", job="j", model="claude-opus-5",
                        provider=ControlledModel([FakeModel.text("a")], "a"), budget="$50")
        # Cùng MỘT Chat instance cho cả hai — mô phỏng chỗ khoá (hoặc thiếu khoá) thật
        # sự bảo vệ: `_chat._messages`/`_spent`.
        chat = agent_a.chat()
        chat._agent = agent_a   # dùng agent_a's provider cho cả hai lượt gọi dưới đây

        results = {}

        def call_a():
            results["a"] = chat.say("một")   # KHÔNG qua Session — gọi Chat.say() trực
                                             # tiếp, mô phỏng đúng hành vi khi thiếu khoá

        def call_b():
            # Đợi luồng A đã VÀO provider (đã đọc _messages rỗng) trước khi B cũng đọc,
            # ép cả hai đọc cùng lịch sử rỗng — đúng kịch bản race.
            started_a.wait(timeout=5)
            agent_a.provider._tag = "b"     # chuyển provider sang kịch bản "b" cho lượt này
            results["b"] = chat.say("hai")

        t_a = _threading.Thread(target=call_a)
        t_b = _threading.Thread(target=call_b)
        t_a.start(); t_b.start()
        started_b.wait(timeout=5)
        # Cả hai đã ở TRONG provider, cả hai đã đọc _messages rỗng — giờ mở khoá theo
        # thứ tự A trước, B sau (B ghi đè lên A vì lịch sử B đọc không có lượt của A).
        release_a.set()
        t_a.join(timeout=5)
        release_b.set()
        t_b.join(timeout=5)

        self.assertEqual(len(chat.messages), 2,
                         "KHÔNG có khoá: luồng B đọc lịch sử rỗng (trước khi A ghi "
                         "lại), rồi ghi ĐÈ lên A — kết quả cuối chỉ còn 2 message "
                         "(của B), không phải 4. Đây là chính XÁC race mà "
                         "`Session._lock` tồn tại để ngăn — chứng minh test chính "
                         "(dùng Session thật, CÓ khoá, ra 4) phụ thuộc đúng vào nó.")


if __name__ == "__main__":
    unittest.main()
